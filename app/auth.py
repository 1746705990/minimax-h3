"""注册 / 登录 / 会话校验"""
import hashlib
import hmac
import os

from fastapi import HTTPException, Request

from . import db
from .config import CFG

SESSION_COOKIE = "h3_session"


def hash_password(password: str, salt: str = None) -> str:
    salt = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def current_user(request: Request):
    """FastAPI 依赖：从 cookie 取当前用户，未登录抛 401"""
    user = db.get_session_user(request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def check_invite(code: str) -> bool:
    need = CFG.get("invite_code") or ""
    return (not need) or hmac.compare_digest(code or "", need)
