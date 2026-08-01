"""SQLite 数据库基础设施 + 旧 JSON 存储自动迁移。

- Phase 1：users 表（账户认证）
- Phase 2：interviews / mistakes / live_sessions 三张表替代 JSON 文件存储

设计：
- 单文件库：backend/data/app.db（data/ 已在 .gitignore）
- WAL 模式 + busy_timeout：多 uvicorn worker 并发读写安全（写操作串行化）
- 每 worker 进程持有自己的连接（check_same_thread=False + 进程内写锁）
"""
import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

_CONN: sqlite3.Connection | None = None
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")  # 多 worker 写竞争时等待而非立刻报错
    return c


def conn() -> sqlite3.Connection:
    """当前进程的共享连接（懒加载）。"""
    global _CONN
    if _CONN is None:
        _CONN = _connect()
    return _CONN


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """写操作（自动提交，进程内串行）。"""
    with _LOCK:
        cur = conn().execute(sql, params)
        conn().commit()
        return cur


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    with _LOCK:
        cur = conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def init_db():
    """建表（幂等，多 worker 启动时并发执行也安全）。"""
    execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            nickname TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free',
            openid TEXT UNIQUE,
            created_at REAL NOT NULL
        )
    """)
    # ---------- 面试历史（替代 history.json） ----------
    execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT '',
            started_at REAL,
            finished_at REAL,
            created_at REAL,
            persona_name TEXT,
            persona_title TEXT,
            jd_title TEXT,
            score INTEGER,
            verdict TEXT,
            abandoned INTEGER NOT NULL DEFAULT 0,
            dimensions TEXT,
            judge_passed INTEGER,
            judge_total INTEGER,
            weak_tags TEXT,
            report TEXT,
            transcript TEXT,
            tier TEXT
        )
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_interviews_user ON interviews(user_id, finished_at DESC)")
    # ---------- 错题本（替代 mistakes.json） ----------
    execute("""
        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT 'code',
            problem_id TEXT,
            title TEXT,
            difficulty TEXT,
            tags TEXT,
            question TEXT,
            direction TEXT,
            wrong_times INTEGER NOT NULL DEFAULT 1,
            last_passed INTEGER,
            last_total INTEGER,
            updated_at REAL
        )
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_mistakes_user ON mistakes(user_id, type)")
    # 部分唯一索引：同用户同题（code）/ 同问题文本（quiz）去重，并发写入与重复迁移防重
    execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_mistakes_code ON mistakes(user_id, problem_id) WHERE type = 'code' AND problem_id IS NOT NULL")
    execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_mistakes_quiz ON mistakes(user_id, question) WHERE type = 'quiz'")
    # ---------- 进行中会话快照（替代 live_sessions/*.json） ----------
    execute("""
        CREATE TABLE IF NOT EXISTS live_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT '',
            snapshot TEXT NOT NULL,
            updated_at REAL
        )
    """)


# ---------- 旧 JSON 存储自动迁移（幂等） ----------

def migrate_legacy_json():
    """history.json / mistakes.json / live_sessions/*.json → SQLite。

    各表独立判断：目标表为空且对应 JSON 存在才执行；INSERT OR REPLACE / IGNORE
    保证多 worker 并发或重复执行不产生脏数据。迁移后原 JSON 文件保留作兜底。
    """
    if not query_one("SELECT id FROM interviews LIMIT 1"):
        _migrate_history()
    if not query_one("SELECT id FROM mistakes LIMIT 1"):
        _migrate_mistakes()
    if not query_one("SELECT session_id FROM live_sessions LIMIT 1"):
        _migrate_live_sessions()


def _migrate_history():
    path = os.path.join(DATA_DIR, "history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for r in records:
        rid = r.get("id")
        if not rid:
            continue
        execute(
            """INSERT OR REPLACE INTO interviews
               (id, user_id, started_at, finished_at, created_at, persona_name, persona_title,
                jd_title, score, verdict, abandoned, dimensions, judge_passed, judge_total,
                weak_tags, report, transcript, tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, r.get("user_id") or "", r.get("started_at"), r.get("finished_at"),
             r.get("created_at") or time.time(), r.get("persona_name"), r.get("persona_title"),
             r.get("jd_title"), r.get("score"), r.get("verdict"),
             1 if r.get("abandoned") else 0,
             json.dumps(r.get("dimensions") or {}, ensure_ascii=False),
             r.get("judge_passed"), r.get("judge_total"),
             json.dumps(r.get("weak_tags") or [], ensure_ascii=False),
             r.get("report"), r.get("transcript"), r.get("tier")),
        )


def _migrate_mistakes():
    path = os.path.join(DATA_DIR, "mistakes.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for it in items:
        execute(
            """INSERT OR IGNORE INTO mistakes
               (user_id, type, problem_id, title, difficulty, tags, question, direction,
                wrong_times, last_passed, last_total, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (it.get("user_id") or "", it.get("type") or "code", it.get("problem_id"),
             it.get("title"), it.get("difficulty"),
             json.dumps(it.get("tags") or [], ensure_ascii=False),
             it.get("question"), it.get("direction"),
             it.get("wrong_times") or 1, it.get("last_passed"), it.get("last_total"),
             it.get("updated_at") or time.time()),
        )


def _migrate_live_sessions():
    live_dir = os.path.join(DATA_DIR, "live_sessions")
    try:
        names = [n for n in os.listdir(live_dir) if n.endswith(".json")]
    except OSError:
        return
    for name in names:
        try:
            with open(os.path.join(live_dir, name), "r", encoding="utf-8") as f:
                raw = f.read()
            data = json.loads(raw)
            execute(
                "INSERT OR REPLACE INTO live_sessions (session_id, user_id, snapshot, updated_at) VALUES (?,?,?,?)",
                (data.get("id") or name[:-5], data.get("user_id") or "", raw, time.time()),
            )
        except (OSError, json.JSONDecodeError):
            continue
