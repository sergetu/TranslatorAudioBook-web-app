"""Auth service: PBKDF2-хэши, JWT, FastAPI-зависимости."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import time

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .. import config
from ..repositories.sqlite_repo import repo

_ITERATIONS = 210_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = _secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), digest)
    except Exception:
        return False


def create_token(user: dict) -> str:
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "exp": int(time.time()) + config.JWT_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, config.jwt_secret(), algorithm=config.JWT_ALG)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.jwt_secret(), algorithms=[config.JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Токен истёк")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Недействительный токен")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Требуется авторизация")
    payload = _decode_token(creds.credentials)
    user = repo.get_user(int(payload["sub"]))
    if user is None or not user["is_active"]:
        raise HTTPException(401, "Пользователь не найден или отключён")
    return user


def current_optional(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict | None:
    """Для аудио-роутов: без заголовка возвращает None (дальше смотрим ?token=)."""
    if creds is None:
        return None
    try:
        payload = _decode_token(creds.credentials)
    except HTTPException:
        return None
    user = repo.get_user(int(payload["sub"]))
    if user is None or not user["is_active"]:
        return None
    return user


def current_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Требуются права администратора")
    return user
