import io
import os
import re
import uuid
import qrcode
from typing import Any
from fastapi import UploadFile
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError
from database import get_db_connection

ALLOWED_CATEGORIES = {
    "cleanser": "Cleanser",
    "toner": "Toner",
    "serum": "Serum",
    "moisturizer": "Moisturizer",
    "sunscreen": "Sunscreen",
    "mask": "Mask",
}

SKU_PATTERN = re.compile(r"^LS-\d{4}$")
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
MIN_IMAGE_DIM = 800                # 800 x 800 px
ALLOWED_PIL_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def process_excel_import(file_bytes: bytes) -> dict:
    try:
        wb = load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        return {
            "fatal_error": f"ไม่สามารถอ่านไฟล์ Excel ได้ กรุณาตรวจสอบว่าเป็นไฟล์ .xlsx ที่ถูกต้อง ({str(e)})",
            "inserted": 0,
            "updated": 0,
            "errors": [],
        }

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        return {"fatal_error": "ไฟล์ Excel ว่างเปล่า ไม่มีข้อมูล", "inserted": 0, "updated": 0, "errors": []}

    # แมปชื่อคอลัมน์ (รองรับตัวพิมพ์เล็ก/ใหญ่ และช่องว่าง)
    header_map = {}
    for idx, h in enumerate(raw_headers):
        if h is not None:
            key = str(h).strip().lower().replace(" ", "_")
            header_map[key] = idx

    required_cols = {"sku", "name", "price"}
    missing_cols = required_cols - set(header_map.keys())
    if missing_cols:
        return {
            "fatal_error": f"ไม่พบคอลัมน์บังคับในไฟล์ Excel: {', '.join(sorted(missing_cols))}",
            "inserted": 0,
            "updated": 0,
            "errors": [],
        }

    seen_skus_in_file = set()
    valid_records = []
    row_errors = []

    for row_idx, row in enumerate(rows_iter, start=2):
        # ข้ามแถวที่ว่างทั้งแถวโดยไม่นับเป็น Error
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue

        def get_val(col_name: str) -> Any:
            idx = header_map.get(col_name)
            return row[idx] if idx is not None and idx < len(row) else None

        raw_sku = _clean_str(get_val("sku")).upper()
        raw_name = _clean_str(get_val("name"))
        raw_category = _clean_str(get_val("category"))
        raw_price = get_val("price")
        raw_size = _clean_str(get_val("size")) or None
        raw_desc = _clean_str(get_val("description")) or None
        raw_how_to_use = _clean_str(get_val("how_to_use")) or None
        raw_status = _clean_str(get_val("status")).lower()

        reasons = []

        # 1. ตรวจสอบ SKU
        if not raw_sku:
            reasons.append("ไม่ได้ระบุ SKU")
        elif not SKU_PATTERN.match(raw_sku):
            reasons.append(f"รูปแบบ SKU ไม่ถูกต้อง ('{raw_sku}' ต้องเป็น LS-0000)")
        elif raw_sku in seen_skus_in_file:
            reasons.append(f"SKU '{raw_sku}' ซ้ำกันภายในไฟล์ Excel")

        # 2. ตรวจสอบ Name
        if not raw_name:
            reasons.append("ไม่ได้ระบุชื่อสินค้า (name)")

        # 3. ตรวจสอบ Category
        clean_category = None
        if raw_category:
            mapped_cat = ALLOWED_CATEGORIES.get(raw_category.lower())
            if not mapped_cat:
                reasons.append(
                    f"หมวดหมู่ '{raw_category}' ไม่ถูกต้อง (ต้องเป็น Cleanser, Toner, Serum, Moisturizer, Sunscreen, Mask)"
                )
            else:
                clean_category = mapped_cat

        # 4. ตรวจสอบ Price (รองรับลูกน้ำคั่นหลักพัน เช่น 1,290)
        clean_price = None
        if raw_price is None or str(raw_price).strip() == "":
            reasons.append("ไม่ได้ระบุราคา (price)")
        else:
            try:
                price_str = str(raw_price).replace(",", "").strip()
                clean_price = float(price_str)
                if clean_price <= 0:
                    reasons.append("ราคาต้องมากกว่า 0")
            except ValueError:
                reasons.append(f"ราคา '{raw_price}' ไม่ใช่ตัวเลขที่ถูกต้อง")

        # 5. ตรวจสอบ Status (ว่าง -> active)
        if not raw_status:
            clean_status = "active"
        elif raw_status in ("active", "inactive"):
            clean_status = raw_status
        else:
            clean_status = None
            reasons.append(f"สถานะ '{raw_status}' ไม่ถูกต้อง (ต้องเป็น active หรือ inactive)")

        if reasons:
            row_errors.append({
                "row": row_idx,
                "sku": raw_sku or "-",
                "name": raw_name or "-",
                "reasons": reasons,
            })
            continue

        seen_skus_in_file.add(raw_sku)
        valid_records.append({
            "sku": raw_sku,
            "name": raw_name,
            "category": clean_category,
            "price": clean_price,
            "size": raw_size,
            "description": raw_desc,
            "how_to_use": raw_how_to_use,
            "status": clean_status,
        })

    # บันทึกลง SQLite แบบ Upsert (อัปเดตโดยไม่ลบ image_path เดิม)
    inserted_count = 0
    updated_count = 0

    with get_db_connection() as conn:
        for item in valid_records:
            existing = conn.execute(
                "SELECT id FROM products WHERE sku = ?",
                (item["sku"],),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE products
                    SET name = ?,
                        category = ?,
                        price = ?,
                        size = ?,
                        description = ?,
                        how_to_use = ?,
                        status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sku = ?
                    """,
                    (
                        item["name"],
                        item["category"],
                        item["price"],
                        item["size"],
                        item["description"],
                        item["how_to_use"],
                        item["status"],
                        item["sku"],
                    ),
                )
                updated_count += 1
            else:
                conn.execute(
                    """
                    INSERT INTO products (sku, name, category, price, size, description, how_to_use, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["sku"],
                        item["name"],
                        item["category"],
                        item["price"],
                        item["size"],
                        item["description"],
                        item["how_to_use"],
                        item["status"],
                    ),
                )
                inserted_count += 1
        conn.commit()

    return {
        "fatal_error": None,
        "inserted": inserted_count,
        "updated": updated_count,
        "errors": row_errors,
    }

def generate_qr_code_bytes(
    target_url: str,
    fill_color: str = "#000000",
    back_color: str = "#FFFFFF",
    box_size: int = 10,
) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=3,
    )
    qr.add_data(target_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color=fill_color, back_color=back_color).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()

async def validate_and_save_product_image(upload_file: UploadFile, sku: str) -> tuple[str | None, str | None]:
    """
    ตรวจสอบรูปภาพตามเงื่อนไข:
    1. ชนิดไฟล์ JPG, PNG, WEBP
    2. ขนาดไฟล์ไม่เกิน 2 MB
    3. ความละเอียดขั้นต่ำ 800 x 800 px
    คืนค่า (image_url_path, error_message)
    """
    content = await upload_file.read()

    if len(content) == 0:
        return None, "ไฟล์รูปภาพว่างเปล่า"

    if len(content) > MAX_IMAGE_BYTES:
        size_mb = len(content) / (1024 * 1024)
        return None, f"ขนาดไฟล์ใหญ่เกินกำหนด ({size_mb:.2f} MB) ต้องไม่เกิน 2 MB"

    try:
        with Image.open(io.BytesIO(content)) as img:
            img_format = (img.format or "").upper()
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, Exception):
        return None, "ไฟล์ที่อัปโหลดไม่ใช่ไฟล์รูปภาพที่ถูกต้อง"

    if img_format not in ALLOWED_PIL_FORMATS:
        return None, f"รองรับเฉพาะไฟล์ JPG, PNG และ WEBP เท่านั้น (ไฟล์นี้เป็น {img_format})"

    if width < MIN_IMAGE_DIM or height < MIN_IMAGE_DIM:
        return None, f"ความละเอียดรูปภาพต่ำเกินไป ({width}x{height} px) ต้องมีขนาดขั้นต่ำ 800 x 800 px"

    ext = ALLOWED_PIL_FORMATS[img_format]
    os.makedirs("static/uploads", exist_ok=True)
    filename = f"{sku}_{uuid.uuid4().hex[:8]}.{ext}"
    file_path = os.path.join("static", "uploads", filename)

    with open(file_path, "wb") as f:
        f.write(content)

    return f"/static/uploads/{filename}", None