#Luma Skin Plan

## 1. System Flow

### 1.1 Admin Flow

1. **Authentication:** Admin / Super Admin เข้าสู่ระบบผ่านหน้า Login(ไม่มีระบบสมัครสมาชิกสาธารณะ)
2. **Import Excel:** อัปโหลดไฟล์ '.xlsx', 
3. **Manage Product ang image:** ดูรายการสินค้าทั้งหมด, แก้ไขรายละเอียด,ระบบตรวจสอบข้อมูบทีละแถว, อัปโหลดรูปสินค้า (JPG/PNG ไม่เกิน 2MB ขั้นต่ำ 800 x 800 px)
4. **QR Code Management:** สร้าง QR Code
5. **User management:** 

### 1.2 Public Customer Flow 

1. **Scan QR Code;**
2. **Verify Status:**

---

## 2. Database Schema

ใช้ฐานข้อมูล **SQLite** โดยแยกโครงสร้างตารางไว้ในไฟล์ `schema.sql` ดังนี้:

- **Table: `users`** (เก็บข้อมูลผู้ดูแลระบบ)
  - `id`: INTEGER (PK, Auto-increment)
  - `email`: TEXT (NOT NULL, UNIQUE)
  - `password_hash`: TEXT (NOT NULL)
  - `role`: TEXT (NOT NULL, DEFAULT `'admin'`) — `'super_admin'` | `'admin'`
  - `status`: TEXT (NOT NULL, DEFAULT `'active'`) — `'active'` | `'invited'`
  - `invite_token`: TEXT (NULL)
  - `created_at`: DATETIME (DEFAULT CURRENT_TIMESTAMP)

- **Table: `products`** (เก็บข้อมูลสินค้าและสถานะการแสดงผล)
  - `id`: INTEGER (PK, Auto-increment)
  - `sku`: TEXT (NOT NULL, UNIQUE) — รูปแบบ `LS-0000`
  - `name`: TEXT (NOT NULL)
  - `category`: TEXT (NULL) — `Cleanser`, `Toner`, `Serum`, `Moisturizer`, `Sunscreen`, `Mask`
  - `price`: REAL (NOT NULL) — มากกว่า 0
  - `size`: TEXT (NULL)
  - `description`: TEXT (NULL)
  - `how_to_use`: TEXT (NULL)
  - `image_path`: TEXT (NULL)
  - `status`: TEXT (NOT NULL, DEFAULT `'active'`) — `'active'` | `'inactive'`
  - `updated_at`: DATETIME (DEFAULT CURRENT_TIMESTAMP)

- **Table: `scan_logs`** (เก็บประวัติการสแกน QR Code)
  - `id`: INTEGER (PK, Auto-increment)
  - `product_id`: INTEGER (NOT NULL, FK -> `products.id`)
  - `scanned_at`: DATETIME (DEFAULT CURRENT_TIMESTAMP)
  - `user_agent`: TEXT (NULL)

---

## 3. Task Sequence & Time Estimation (รวม 4 ชั่วโมง 30 นาที)

- **Phase 1: วางแผนและออกแบบระบบ (`PLAN.md`) **
  - วิเคราะห์ข้อมูลจากไฟล์ Excel ตัวอย่าง วางโครงสร้างฐานข้อมูล จัดลำดับความสำคัญ และ Push `PLAN.md` ขึ้น GitHub
- **Phase 2: Setup, Database & Authentication **
  - สร้าง Environment, ไฟล์ `schema.sql`, ระบบเชื่อมต่อ SQLite, Seed บัญชี Super Admin และระบบ Login + แยกสิทธิ์ Role
- **Phase 3: Core Product Management & Excel Import **
  - สร้างระบบนำเข้าไฟล์ `.xlsx` (Validation + Upsert + รายงาน Error รายแถว), หน้าจัดการรายการสินค้า, แก้ไขข้อมูล และอัปโหลดรูปภาพพร้อมตรวจเงื่อนไข
- **Phase 4: QR Code Generation & Public Product Page **
  - สร้าง QR Code ผูกกับ `/p/{sku}` และพัฒนาหน้าแสดงข้อมูลสินค้าสาธารณะที่รองรับมือถือและสถานะ `inactive`
- **Phase 5: Scan Analytics, QR Customization & Invite Admin **
  - เพิ่มระบบเก็บ `scan_logs` พร้อมสรุปสินค้าที่ถูกดูมากที่สุด, ปรับสี/ขนาด QR Code และระบบเชิญ Admin ใหม่ (บันทึก Log)
- **Phase 6: End-to-End Testing & Documentation (`README.md`) **
  - ทดสอบนำเข้าไฟล์ทั้ง 2 รอบ, ทดสอบเงื่อนไขรูปภาพ, เขียน `README.md` ตามหัวข้อที่กำหนด และ Push ส่งงานก่อนหมดเวลา 5 นาที

---

## 4. Prioritization & Scope Trade-offs (หากเวลาไม่พอจะตัดอะไรก่อน)

เรียงลำดับฟีเจอร์ที่จะ **ตัดออกก่อน** หากเวลาจำกัด เพื่อรักษา Core Flow หลักให้สมบูรณ์ 100%:

1. **ตัดออกอันดับ 1: ระบบเชิญ Admin ทางอีเมล (Invite Admin)**
   - *เหตุผล:* ระบบมีบัญชี Super Admin และ Admin ตั้งต้นที่ Login ใช้งานได้แล้ว และการเพิ่มผู้ใช้ใหม่ไม่มีผลกระทบต่อการทำงานหลักของลูกค้าหน้าร้าน
2. **ตัดออกอันดับ 2: การปรับแต่งสีพื้นหลังและขนาดของ QR Code**
   - *เหตุผล:* หากเวลาไม่พอ สามารถใช้ QR Code มาตรฐาน (ขาว-ดำ ขนาดคงที่) ที่ดาวน์โหลดเป็น PNG ได้แทน ซึ่งสแกนใช้งานได้จริงเหมือนกัน
3. **ตัดออกอันดับ 3: สถิติการสแกน (Scan Logs & Top Viewed)**
   - *เหตุผล:* เป็นส่วนวิเคราะห์ข้อมูลเพิ่มเติม ควรทำหลังจากระบบจัดการสินค้า นำเข้า Excel และหน้าสินค้าสาธารณะทำงานได้ครบถ้วนแล้ว

*(หมายเหตุ: ฟีเจอร์หลักที่ **ห้ามตัดเด็ดขาด** คือ Login, Import Excel แบบ Upsert พร้อมแจ้ง Error, แก้ไขสินค้า + อัปโหลดรูป, สร้าง QR และหน้าสินค้าสาธารณะ)*

---

## 5. Assumptions & Design Decisions (สมมติฐานและการตัดสินใจเพิ่มเติม)

1. **Tech Stack:** เลือกใช้ **Python (FastAPI) + SQLite + HTML/Tailwind CSS** เพื่อให้รันทั้งระบบได้ในคำสั่งเดียว ลดเวลา Setup Build Tools และสามารถใช้ Library `openpyxl` (อ่าน Excel), `Pillow` (ตรวจขนาดรูป 800x800px) และ `qrcode` ได้อย่างแม่นยำ
2. **การทำความสะอาดข้อมูล Excel (Data Sanitization):**
   - ข้ามแถวที่ว่างทั้งแถวโดยไม่นับเป็น Error
   - ตัดช่องว่างหัวท้าย (`.strip()`) ของ `sku` และ `status` รวมถึงแปลง `sku` เป็นตัวพิมพ์ใหญ่ (`.upper()`) อัตโนมัติก่อนตรวจรูปแบบ `^LS-\d{4}$`
   - แปลงตัวเลขราคาที่มีลูกน้ำคั่น (เช่น `1,290`) ให้เป็นตัวเลขก่อนตรวจสอบเงื่อนไข `price > 0`
   - หาก `sku` ซ้ำกันเองภายในไฟล์ Excel เดียวกัน ให้รับแถวแรกและปฏิเสธแถวที่ซ้ำพร้อมแจ้ง Error
   - หาก `status` ว่าง ให้กำหนดเป็น `active` อัตโนมัติ แต่หากเป็นค่าอื่นที่ไม่ใช่ `active`/`inactive` ให้ปฏิเสธแถวนั้น
3. **การอัปเดตข้อมูลซ้ำ (Upsert Behavior):** เมื่อนำเข้าไฟล์อัปเดต หาก `sku` มีอยู่แล้ว จะอัปเดตเฉพาะข้อมูลจาก Excel โดย **ไม่ลบ `image_path` เดิม** ที่ Admin อัปโหลดไว้แล้ว
4. **สิทธิ์การใช้งาน (RBAC):** `Super Admin` เข้าถึงทุกเมนูและจัดการบัญชีผู้ใช้ได้ ส่วน `Admin` จัดการได้เฉพาะข้อมูลสินค้า อัปโหลดรูป และสร้าง/ดาวน์โหลด QR Code
5. **การอัปโหลดรูปภาพ:** ตรวจสอบนามสกุล/MIME Type (`JPG`, `PNG`, `WEBP`), ขนาดไฟล์ไม่เกิน `2 MB` และความละเอียดขั้นต่ำ `800 x 800 px` หากไม่ผ่านเงื่อนไขข้อใดข้อหนึ่งจะปฏิเสธและแจ้งเหตุผลเฉพาะเจาะจงทันที
