"""用户账户：SQLite 存储 + bcrypt 密码哈希。

- users 表由 db.init_db() 建立（username 唯一，预留 openid 供小程序阶段绑定）
- 兼容迁移：旧版 data/accounts.json（明文密码）首次启动时自动迁入并哈希
- SEED_DEMO_ACCOUNTS=true（默认）时播种演示账户；上线务必设为 false
"""
import json
import os
import time

from . import db
from .auth import hash_password, verify_password
from .config import settings
from .history import DATA_DIR

LEGACY_FILE = os.path.join(DATA_DIR, "accounts.json")

SEED_ACCOUNTS = [
    ("alice", "Alice（产品算法）", "pass123"),
    ("bob", "Bob（推荐算法）", "pass123"),
    ("carol", "Carol（CV 算法）", "pass123"),
    ("dave", "Dave（后端开发）", "pass123"),
]


def _insert_user(username: str, nickname: str, password_hash: str):
    db.execute(
        "INSERT OR IGNORE INTO users (username, nickname, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, nickname, password_hash, time.time()),
    )


def _migrate_from_json():
    """旧 accounts.json（明文密码）→ users 表（bcrypt 哈希）。仅表为空时执行一次。"""
    if db.query_one("SELECT id FROM users LIMIT 1"):
        return
    try:
        with open(LEGACY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for a in data:
        u = (a.get("username") or "").strip()
        p = a.get("password") or ""
        if u and p:
            _insert_user(u, a.get("name") or u, hash_password(p))


def _seed():
    for u, n, p in SEED_ACCOUNTS:
        _insert_user(u, n, hash_password(p))


def init_accounts():
    """启动时调用：建表 → 旧数据迁移 → 播种演示账户（幂等）。"""
    db.init_db()
    _migrate_from_json()
    if settings.SEED_DEMO_ACCOUNTS:
        _seed()


def get_user(username: str) -> dict | None:
    return db.query_one(
        "SELECT username, nickname, tier, created_at FROM users WHERE username = ?",
        ((username or "").strip(),),
    )


def verify(username: str, password: str) -> dict | None:
    """校验账户，成功返回 {username, name, tier}，失败返回 None。"""
    username = (username or "").strip()
    if not username or not password:
        return None
    row = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"username": row["username"], "name": row["nickname"] or row["username"], "tier": row["tier"]}


def create_user(username: str, password: str, name: str = "") -> dict:
    """注册新用户；用户名已存在时抛 ValueError。"""
    username = (username or "").strip()
    if get_user(username):
        raise ValueError("username exists")
    _insert_user(username, (name or username).strip(), hash_password(password))
    return {"username": username, "name": (name or username).strip(), "tier": "free"}


def change_password(username: str, old_password: str, new_password: str) -> bool:
    row = db.query_one("SELECT password_hash FROM users WHERE username = ?", ((username or "").strip(),))
    if not row or not verify_password(old_password, row["password_hash"]):
        return False
    db.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (hash_password(new_password), username.strip()),
    )
    return True


def list_public() -> list:
    """演示账户列表（仅用户名与显示名，供登录页一键登录）；生产环境应关闭。"""
    if not settings.SEED_DEMO_ACCOUNTS:
        return []
    seeded = {u for u, _, _ in SEED_ACCOUNTS}
    rows = db.query_all("SELECT username, nickname FROM users")
    return [
        {"username": r["username"], "name": r["nickname"] or r["username"]}
        for r in rows if r["username"] in seeded
    ]
