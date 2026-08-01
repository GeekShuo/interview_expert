"""面试历史记录的持久化（SQLite）。

- 存储：data/app.db 的 interviews 表（由 db.init_db 建立，旧 history.json 自动迁移）
- 每用户上限 MAX_RECORDS 条，超出后最旧记录自动清理
- DATA_DIR 常量保留导出（auth / accounts 等模块既有引用兼容）
"""
import json
import time
import uuid

from . import db
from .db import DATA_DIR  # noqa: F401  兼容既有 from .history import DATA_DIR

# 每用户最大保留条数，避免无限增长
MAX_RECORDS = 200

_SUMMARY_COLS = ("id, finished_at, created_at, persona_name, persona_title, jd_title,"
                 " score, verdict, abandoned, dimensions, judge_passed, judge_total, weak_tags")

_ORDER = "ORDER BY COALESCE(finished_at, created_at) DESC"


def _row_to_record(row: dict) -> dict:
    rec = dict(row)
    rec["abandoned"] = bool(rec.get("abandoned"))
    rec["dimensions"] = json.loads(rec.get("dimensions") or "{}")
    rec["weak_tags"] = json.loads(rec.get("weak_tags") or "[]")
    return rec


def save_record(record: dict) -> str:
    """保存一条面试记录（同 id 覆盖更新），返回其 id。"""
    rid = record.get("id") or ("iv_" + uuid.uuid4().hex[:12])
    created = record.get("created_at")
    if created is None:
        # 覆盖写时保留原创建时间（旧版 setdefault 语义）
        old = db.query_one("SELECT created_at FROM interviews WHERE id = ?", (rid,))
        created = (old or {}).get("created_at") or time.time()
    db.execute(
        """INSERT OR REPLACE INTO interviews
           (id, user_id, started_at, finished_at, created_at, persona_name, persona_title,
            jd_title, score, verdict, abandoned, dimensions, judge_passed, judge_total,
            weak_tags, report, transcript, tier)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, record.get("user_id") or "", record.get("started_at"), record.get("finished_at"),
         created, record.get("persona_name"), record.get("persona_title"),
         record.get("jd_title"), record.get("score"), record.get("verdict"),
         1 if record.get("abandoned") else 0,
         json.dumps(record.get("dimensions") or {}, ensure_ascii=False),
         record.get("judge_passed"), record.get("judge_total"),
         json.dumps(record.get("weak_tags") or [], ensure_ascii=False),
         record.get("report"), record.get("transcript"), record.get("tier")),
    )
    _enforce_limit(record.get("user_id") or "")
    return rid


def _enforce_limit(user_id: str):
    """每用户仅保留最新 MAX_RECORDS 条（按完成时间倒序）。"""
    db.execute(
        f"""DELETE FROM interviews WHERE user_id = ? AND id NOT IN (
               SELECT id FROM interviews WHERE user_id = ? {_ORDER} LIMIT ?)""",
        (user_id, user_id, MAX_RECORDS),
    )


def list_records(user_id: str | None = None) -> list:
    """返回记录摘要列表（按时间倒序，最新在前）。

    user_id 为空时返回全部（管理/调试用）；传入时仅返回该用户记录，
    实现多用户「成长曲线」互不串数据。
    """
    if user_id:
        rows = db.query_all(f"SELECT {_SUMMARY_COLS} FROM interviews WHERE user_id = ? {_ORDER}", (user_id,))
    else:
        rows = db.query_all(f"SELECT {_SUMMARY_COLS} FROM interviews {_ORDER}")
    return [_row_to_record(r) for r in rows]


def get_record(rid: str) -> dict | None:
    row = db.query_one("SELECT * FROM interviews WHERE id = ?", (rid,))
    return _row_to_record(row) if row else None


def delete_record(rid: str) -> bool:
    return db.execute("DELETE FROM interviews WHERE id = ?", (rid,)).rowcount > 0
