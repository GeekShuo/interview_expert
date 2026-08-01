"""认证体系：bcrypt 密码哈希 + JWT 签发/校验 + FastAPI 鉴权依赖。

- 注册用户 token：sub=username，kind="user"
- 游客 token：sub=匿名 uid（u_xxx），kind="anon"，由 /api/anon_token 签发
- JWT_SECRET 未在 .env 配置时自动生成并持久化到 data/.jwt_secret（重启不失效）
"""
import os
import secrets
import time

import bcrypt
import jwt
from fastapi import HTTPException, Request

from .config import settings
from .history import DATA_DIR

ALGO = "HS256"
TOKEN_TTL = 30 * 24 * 3600  # 30 天：工具类产品避免频繁重登；JWT 无状态不可吊销，到期自然失效

_SECRET_CACHE: str | None = None


def _secret() -> str:
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE
    if settings.JWT_SECRET:
        _SECRET_CACHE = settings.JWT_SECRET
        return _SECRET_CACHE
    path = os.path.join(DATA_DIR, ".jwt_secret")
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
            if s:
                _SECRET_CACHE = s
                return s
    except OSError:
        pass
    s = secrets.token_hex(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _SECRET_CACHE = s
    return s


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, kind: str = "user") -> str:
    now = int(time.time())
    payload = {"sub": user_id, "kind": kind, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, _secret(), algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGO])
    except jwt.PyJWTError:
        return None


def get_current_user(request: Request) -> str:
    """FastAPI 依赖：从 Authorization: Bearer 解析 user_id，缺失/非法/过期一律 401。"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    payload = decode_token(token) if token else None
    if not payload or not payload.get("sub"):
        raise HTTPException(401, "未登录或登录已过期")
    return payload["sub"]
