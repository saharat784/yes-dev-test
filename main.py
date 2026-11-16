import io
import logging
import re
import secrets

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime
from database import get_db_connection, hash_password, init_db

from auth import get_current_user, require_super_admin, verify_password
from database import get_db_connection, init_db
from services import ALLOWED_CATEGORIES, process_excel_import, validate_and_save_product_image

from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from services import (
    ALLOWED_CATEGORIES,
    generate_qr_code_bytes,
    process_excel_import,
    validate_and_save_product_image,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Luma Skin Admin System", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key="lumaskin-super-secret-key-change-in-prod",
    max_age=60 * 60 * 8,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    clean_email = email.strip().lower()
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (clean_email,)).fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "อีเมลหรือรหัสผ่านไม่ถูกต้อง"},
            status_code=401,
        )

    if user["status"] != "active":
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "บัญชีนี้ยังไม่เปิดใช้งาน"},
            status_code=403,
        )

    request.session["user_id"] = user["id"]
    request.session["role"] = user["role"]
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        total_products = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
        active_products = conn.execute("SELECT COUNT(*) AS c FROM products WHERE status = 'active'").fetchone()["c"]
        missing_images = conn.execute("SELECT COUNT(*) AS c FROM products WHERE image_path IS NULL").fetchone()["c"]
        total_scans = conn.execute("SELECT COUNT(*) AS c FROM scan_logs").fetchone()["c"]

        # สรุปอันดับสินค้าที่ถูกสแกนมากที่สุด (Top 5)
        top_scanned = conn.execute(
            """
            SELECT p.id, p.sku, p.name, p.category, p.status, COUNT(s.id) AS scan_count
            FROM products p
            LEFT JOIN scan_logs s ON p.id = s.product_id
            GROUP BY p.id
            HAVING scan_count > 0
            ORDER BY scan_count DESC, p.sku ASC
            LIMIT 5
            """
        ).fetchall()

        # ประวัติการสแกนล่าสุด 10 รายการ
        recent_scans = conn.execute(
            """
            SELECT s.scanned_at, s.user_agent, p.sku, p.name
            FROM scan_logs s
            JOIN products p ON s.product_id = p.id
            ORDER BY s.id DESC
            LIMIT 10
            """
        ).fetchall()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_products": total_products,
            "active_products": active_products,
            "missing_images": missing_images,
            "total_scans": total_scans,
            "top_scanned": top_scanned,
            "recent_scans": recent_scans,
        },
    )

# ==================== PHASE 3: PRODUCT MANAGEMENT & EXCEL IMPORT ====================

def _query_products(q: str = "", category: str = "", status_filter: str = ""):
    sql = "SELECT * FROM products WHERE 1=1"
    params = []

    if q.strip():
        sql += " AND (sku LIKE ? OR name LIKE ?)"
        like_q = f"%{q.strip()}%"
        params.extend([like_q, like_q])

    if category.strip():
        sql += " AND category = ?"
        params.append(category.strip())

    if status_filter.strip() in ("active", "inactive"):
        sql += " AND status = ?"
        params.append(status_filter.strip())

    sql += " ORDER BY sku ASC"
    with get_db_connection() as conn:
        return conn.execute(sql, params).fetchall()


@app.get("/products", response_class=HTMLResponse)
def list_products(
    request: Request,
    q: str = "",
    category: str = "",
    status: str = "",
    msg: str = "",
    user: dict = Depends(get_current_user),
):
    products = _query_products(q=q, category=category, status_filter=status)
    return templates.TemplateResponse(
        "products.html",
        {
            "request": request,
            "user": user,
            "products": products,
            "categories": list(ALLOWED_CATEGORIES.values()),
            "filters": {"q": q, "category": category, "status": status},
            "import_result": None,
            "msg": msg,
        },
    )


@app.post("/products/import", response_class=HTMLResponse)
async def import_products_excel(
    request: Request,
    excel_file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not excel_file.filename.lower().endswith(".xlsx"):
        import_result = {
            "fatal_error": "กรุณาอัปโหลดไฟล์นามสกุล .xlsx เท่านั้น",
            "inserted": 0,
            "updated": 0,
            "errors": [],
        }
    else:
        content = await excel_file.read()
        import_result = process_excel_import(content)

    products = _query_products()
    return templates.TemplateResponse(
        "products.html",
        {
            "request": request,
            "user": user,
            "products": products,
            "categories": list(ALLOWED_CATEGORIES.values()),
            "filters": {"q": "", "category": "", "status": ""},
            "import_result": import_result,
            "msg": "",
        },
    )


@app.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product_page(
    request: Request,
    product_id: int,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

    if not product:
        return RedirectResponse(url="/products?msg=ไม่พบสินค้าที่ต้องการแก้ไข", status_code=303)

    return templates.TemplateResponse(
        "product_edit.html",
        {
            "request": request,
            "user": user,
            "product": product,
            "categories": list(ALLOWED_CATEGORIES.values()),
            "error": None,
            "success": None,
        },
    )


@app.post("/products/{product_id}/edit", response_class=HTMLResponse)
async def edit_product_submit(
    request: Request,
    product_id: int,
    name: str = Form(...),
    category: str = Form(""),
    price: str = Form(...),
    size: str = Form(""),
    description: str = Form(""),
    how_to_use: str = Form(""),
    status: str = Form("active"),
    image: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

    if not product:
        return RedirectResponse(url="/products?msg=ไม่พบสินค้า", status_code=303)

    clean_name = name.strip()
    clean_category = category.strip() if category.strip() in ALLOWED_CATEGORIES.values() else None
    clean_size = size.strip() or None
    clean_desc = description.strip() or None
    clean_how = how_to_use.strip() or None
    clean_status = status if status in ("active", "inactive") else "active"

    # ตรวจสอบข้อมูลข้อความและราคา
    try:
        clean_price = float(price.replace(",", "").strip())
        if clean_price <= 0:
            raise ValueError()
    except ValueError:
        return templates.TemplateResponse(
            "product_edit.html",
            {
                "request": request,
                "user": user,
                "product": product,
                "categories": list(ALLOWED_CATEGORIES.values()),
                "error": "ราคาสินค้าต้องเป็นตัวเลขที่มากกว่า 0",
                "success": None,
            },
            status_code=400,
        )

    if not clean_name:
        return templates.TemplateResponse(
            "product_edit.html",
            {
                "request": request,
                "user": user,
                "product": product,
                "categories": list(ALLOWED_CATEGORIES.values()),
                "error": "กรุณาระบุชื่อสินค้า",
                "success": None,
            },
            status_code=400,
        )

    # ตรวจสอบรูปภาพ (หากมีการเลือกไฟล์อัปโหลดใหม่)
    new_image_path = product["image_path"]
    if image and image.filename:
        saved_path, img_err = await validate_and_save_product_image(image, product["sku"])
        if img_err:
            return templates.TemplateResponse(
                "product_edit.html",
                {
                    "request": request,
                    "user": user,
                    "product": product,
                    "categories": list(ALLOWED_CATEGORIES.values()),
                    "error": f"อัปโหลดรูปภาพไม่สำเร็จ: {img_err}",
                    "success": None,
                },
                status_code=400,
            )
        new_image_path = saved_path

    with get_db_connection() as conn:
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
                image_path = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                clean_name,
                clean_category,
                clean_price,
                clean_size,
                clean_desc,
                clean_how,
                clean_status,
                new_image_path,
                product_id,
            ),
        )
        conn.commit()
        updated_product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

    return templates.TemplateResponse(
        "product_edit.html",
        {
            "request": request,
            "user": user,
            "product": updated_product,
            "categories": list(ALLOWED_CATEGORIES.values()),
            "error": None,
            "success": "บันทึกข้อมูลสินค้าเรียบร้อยแล้ว",
        },
    )


# ==================== PHASE 5: USER MANAGEMENT & INVITE ADMIN ====================

@app.get("/users", response_class=HTMLResponse)
def manage_users(request: Request, user: dict = Depends(require_super_admin)):
    with get_db_connection() as conn:
        users = conn.execute(
            "SELECT id, email, role, status, invite_token, created_at FROM users ORDER BY id ASC"
        ).fetchall()

    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "base_url": base_url,
            "error": None,
            "success": None,
            "invite_link": None,
        },
    )


@app.post("/users/invite", response_class=HTMLResponse)
def invite_admin(
    request: Request,
    email: str = Form(...),
    role: str = Form("admin"),
    user: dict = Depends(require_super_admin),
):
    clean_email = email.strip().lower()
    clean_role = role if role in ("admin", "super_admin") else "admin"
    base_url = str(request.base_url).rstrip("/")

    with get_db_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (clean_email,)).fetchone()
        if existing:
            users = conn.execute(
                "SELECT id, email, role, status, invite_token, created_at FROM users ORDER BY id ASC"
            ).fetchall()
            return templates.TemplateResponse(
                "users.html",
                {
                    "request": request,
                    "user": user,
                    "users": users,
                    "base_url": base_url,
                    "error": f"อีเมล {clean_email} มีอยู่ในระบบแล้ว",
                    "success": None,
                    "invite_link": None,
                },
                status_code=400,
            )

        token = secrets.token_urlsafe(24)
        # บันทึกผู้ใช้สถานะ invited (ยังไม่สามารถ Login จนกว่าจะตั้งรหัสผ่านผ่านลิงก์)
        placeholder_hash = hash_password(secrets.token_hex(16))
        conn.execute(
            """
            INSERT INTO users (email, password_hash, role, status, invite_token)
            VALUES (?, ?, ?, 'invited', ?)
            """,
            (clean_email, placeholder_hash, clean_role, token),
        )
        conn.commit()

        users = conn.execute(
            "SELECT id, email, role, status, invite_token, created_at FROM users ORDER BY id ASC"
        ).fetchall()

    invite_link = f"{base_url}/accept-invite?token={token}"
    # บันทึก Log การส่งคำเชิญลงไฟล์ invite_logs.log
    invite_logger.info(
        f"INVITED_BY={user['email']} | TARGET_EMAIL={clean_email} | ROLE={clean_role} | TOKEN={token}"
    )

    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "base_url": base_url,
            "error": None,
            "success": f"สร้างคำเชิญสำหรับ {clean_email} สำเร็จ และบันทึก Log เรียบร้อยแล้ว",
            "invite_link": invite_link,
        },
    )


@app.get("/accept-invite", response_class=HTMLResponse)
def accept_invite_page(request: Request, token: str = ""):
    if not token.strip():
        return HTMLResponse(content="ลิงก์คำเชิญไม่ถูกต้อง", status_code=400)

    with get_db_connection() as conn:
        invited_user = conn.execute(
            "SELECT id, email, role, status FROM users WHERE invite_token = ? AND status = 'invited'",
            (token.strip(),),
        ).fetchone()

    if not invited_user:
        return HTMLResponse(content="ลิงก์คำเชิญหมดอายุหรือถูกใช้งานไปแล้ว", status_code=400)

    return templates.TemplateResponse(
        "accept_invite.html",
        {"request": request, "invited_user": invited_user, "token": token.strip(), "error": None},
    )


@app.post("/accept-invite", response_class=HTMLResponse)
def accept_invite_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    with get_db_connection() as conn:
        invited_user = conn.execute(
            "SELECT id, email, role, status FROM users WHERE invite_token = ? AND status = 'invited'",
            (token.strip(),),
        ).fetchone()

        if not invited_user:
            return HTMLResponse(content="ลิงก์คำเชิญไม่ถูกต้องหรือถูกใช้งานไปแล้ว", status_code=400)

        if len(password) < 6:
            return templates.TemplateResponse(
                "accept_invite.html",
                {
                    "request": request,
                    "invited_user": invited_user,
                    "token": token.strip(),
                    "error": "รหัสผ่านต้องมีความยาวอย่างน้อย 6 ตัวอักษร",
                },
                status_code=400,
            )

        if password != confirm_password:
            return templates.TemplateResponse(
                "accept_invite.html",
                {
                    "request": request,
                    "invited_user": invited_user,
                    "token": token.strip(),
                    "error": "รหัสผ่านทั้งสองช่องไม่ตรงกัน",
                },
                status_code=400,
            )

        new_hash = hash_password(password)
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, status = 'active', invite_token = NULL
            WHERE id = ?
            """,
            (new_hash, invited_user["id"]),
        )
        conn.commit()

    invite_logger.info(f"ACCEPTED_INVITE | EMAIL={invited_user['email']} | STATUS=active")
    return RedirectResponse(url="/login", status_code=303)

# ==================== PHASE 4: QR CODE & PUBLIC PRODUCT PAGE ====================

@app.get("/products/{sku}/qr")
def get_product_qr(
    request: Request,
    sku: str,
    fg: str = "#000000",
    bg: str = "#FFFFFF",
    size: str = "md",
    download: int = 0,
    user: dict = Depends(get_current_user),
):
    clean_sku = sku.strip().upper()
    with get_db_connection() as conn:
        product = conn.execute("SELECT sku FROM products WHERE sku = ?", (clean_sku,)).fetchone()

    if not product:
        return HTMLResponse(content="ไม่พบสินค้า", status_code=404)

    clean_fg = fg if HEX_COLOR_RE.match(fg) else "#000000"
    clean_bg = bg if HEX_COLOR_RE.match(bg) else "#FFFFFF"
    box_size = QR_SIZE_MAP.get(size, 10)

    base_url = str(request.base_url).rstrip("/")
    public_url = f"{base_url}/p/{clean_sku}"

    png_bytes = generate_qr_code_bytes(
        public_url,
        fill_color=clean_fg,
        back_color=clean_bg,
        box_size=box_size,
    )
    headers = {}
    if download == 1:
        headers["Content-Disposition"] = f'attachment; filename="QR_{clean_sku}_{size}.png"'

    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png", headers=headers)


@app.get("/p/{sku}", response_class=HTMLResponse)
def public_product_page(request: Request, sku: str):
    clean_sku = sku.strip().upper()

    with get_db_connection() as conn:
        product = conn.execute("SELECT * FROM products WHERE sku = ?", (clean_sku,)).fetchone()

        # กรณีไม่พบ SKU ในระบบ
        if not product:
            return templates.TemplateResponse(
                "public_product.html",
                {"request": request, "state": "not_found", "product": None, "sku": clean_sku},
                status_code=404,
            )

        # กรณีสินค้าถูกตั้งค่าเป็น inactive
        if product["status"] != "active":
            return templates.TemplateResponse(
                "public_product.html",
                {"request": request, "state": "inactive", "product": product, "sku": clean_sku},
            )

        # บันทึกประวัติการสแกนลง scan_logs เฉพาะเมื่อสินค้าอยู่ในสถานะ active
        user_agent = request.headers.get("user-agent", "")[:500]
        conn.execute(
            "INSERT INTO scan_logs (product_id, user_agent) VALUES (?, ?)",
            (product["id"], user_agent),
        )
        conn.commit()

    return templates.TemplateResponse(
        "public_product.html",
        {"request": request, "state": "active", "product": product, "sku": clean_sku},
    )

# ตั้งค่า Logger สำหรับบันทึกประวัติการเชิญ Admin ลงไฟล์ invite_logs.log
invite_logger = logging.getLogger("invite_audit")
invite_logger.setLevel(logging.INFO)
if not invite_logger.handlers:
    fh = logging.FileHandler("invite_logs.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    invite_logger.addHandler(fh)

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
QR_SIZE_MAP = {"sm": 6, "md": 10, "lg": 14}