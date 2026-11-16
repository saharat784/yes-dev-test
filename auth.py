import bcrypt
from fastapi import HTTPException, Request, status
from database import get_db_connection


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def get_current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT id, email, role, status FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if not user or user["status"] != "active":
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    return dict(user)


def require_super_admin(request: Request) -> dict:
    user = get_current_user(request)
    if user["role"] != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="เฉพาะ Super Admin เท่านั้นที่สามารถเข้าถึงส่วนนี้ได้",
        )
    return user