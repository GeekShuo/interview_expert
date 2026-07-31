"""预设登录账户（本地多用户隔离）。

- 账户存于 backend/data/accounts.json（已在 .gitignore 的 data/ 下，不会进版本库）
- 首次启动自动播种几个示例账户；密码为明文（仅本地演示用，切勿用于生产）
- 登录成功后以 username 作为 user_id，历史 / 错题按账户隔离，多端、多次打开都能看到自己的数据
"""
import json
import os
import threading

from .history import DATA_DIR

ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

# 预置账户：用户名 -> (显示名, 密码)。演示用统一密码，便于「先开放几个账户」。
SEED_ACCOUNTS = [
    ("alice", "Alice（产品算法）", "pass123"),
    ("bob", "Bob（推荐算法）", "pass123"),
    ("carol", "Carol（CV 算法）", "pass123"),
    ("dave", "Dave（后端开发）", "pass123"),
]

_lock = threading.Lock()


def _seed():
    """首次启动写入预置账户（若文件已存在则跳过）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(ACCOUNTS_FILE):
        return
    data = [
        {"username": u, "name": n, "password": p}
        for (u, n, p) in SEED_ACCOUNTS
    ]
    tmp = ACCOUNTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCOUNTS_FILE)


def _read() -> list:
    _seed()
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def verify(username: str, password: str) -> dict | None:
    """校验账户，成功返回 {username, name}，失败返回 None。"""
    username = (username or "").strip()
    if not username:
        return None
    for a in _read():
        if a.get("username") == username and a.get("password") == password:
            return {"username": a["username"], "name": a.get("name", a["username"])}
    return None


def list_public() -> list:
    """返回可登录的账户列表（仅用户名与显示名，不含密码，供登录页一键登录）。"""
    return [
        {"username": a.get("username"), "name": a.get("name", a.get("username"))}
        for a in _read()
    ]
