import os
import sqlite3
import bcrypt

DB_PATH = "lumaskin.db"
SCHEMA_PATH = "schema.sql"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def hash_password(plain_password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def init_db() -> None:
    os.makedirs("static/uploads", exist_ok=True)
    os.makedirs("templates", exist_ok=True)

    with get_db_connection() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # ตรวจสอบว่ามีผู้ใช้งานหรือยัง ถ้ายังให้ Seed บัญชีตั้งต้น
        cursor = conn.execute("SELECT COUNT(*) AS count FROM users")
        if cursor.fetchone()["count"] == 0:
            default_users = [
                ("superadmin@lumaskin.com", hash_password("Admin1234!"), "super_admin", "active"),
                ("admin@lumaskin.com", hash_password("Admin1234!"), "admin", "active"),
            ]
            conn.executemany(
                """
                INSERT INTO users (email, password_hash, role, status)
                VALUES (?, ?, ?, ?)
                """,
                default_users,
            )
            conn.commit()