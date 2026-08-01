"""错题本：自动判题未全通过的算法题 + 用户自评八股错题（SQLite 存储）。

- 存储：data/app.db 的 mistakes 表（由 db.init_db 建立，旧 mistakes.json 自动迁移）
- code 错题按 (user_id, problem_id) 去重、quiz 错题按 (user_id, question) 去重
  （部分唯一索引兜底），重复做错累加错误次数；全部通过后自动消灭。
- 修复旧版缺陷：不同用户错同一题时按题目 id 全局去重导致互相覆盖，现按用户隔离。
"""
import json
import time

from . import db
from .db import DATA_DIR  # noqa: F401  兼容既有引用


def record_session_results(judge_results: list[dict], user_id: str | None = None):
    """一场面试结束后调用：

    - 未全通过的题 → 记入/更新错题本
    - 全部通过的题 → 若在错题本中则移除（已消灭）
    同一题多次提交时以「最后一次」为准（先通过后又失败按失败算，反之亦然）。
    """
    if not judge_results:
        return
    # 同题多次提交：取最后一次结果
    latest: dict[str, dict] = {}
    for j in judge_results:
        if not j.get("supported") or not j.get("total") or not j.get("problem_id"):
            continue
        latest[j["problem_id"]] = j
    if not latest:
        return
    uid = user_id or ""
    now = time.time()
    for pid, j in latest.items():
        failed = (j.get("passed") or 0) < j["total"]
        if failed:
            row = db.query_one(
                "SELECT id, wrong_times FROM mistakes WHERE user_id = ? AND type = 'code' AND problem_id = ?",
                (uid, pid))
            if row:
                db.execute(
                    "UPDATE mistakes SET wrong_times = ?, last_passed = ?, last_total = ?, updated_at = ? WHERE id = ?",
                    (row["wrong_times"] + 1, j.get("passed") or 0, j["total"], now, row["id"]))
            else:
                db.execute(
                    """INSERT OR IGNORE INTO mistakes
                       (user_id, type, problem_id, title, difficulty, tags, wrong_times,
                        last_passed, last_total, updated_at)
                       VALUES (?, 'code', ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (uid, pid, j.get("problem", ""), j.get("difficulty", ""),
                     json.dumps(j.get("tags") or [], ensure_ascii=False),
                     j.get("passed") or 0, j["total"], now))
        else:
            # 已全通过：消灭错题
            db.execute(
                "DELETE FROM mistakes WHERE user_id = ? AND type = 'code' AND problem_id = ?",
                (uid, pid))


def record_quiz_mistake(direction: str, question: str, user_id: str | None = None):
    """八股错题：八股无客观判题，由用户自评入本（按用户 + 题目文本去重）。"""
    q = (question or "").strip()
    if not q:
        return
    uid = user_id or ""
    now = time.time()
    row = db.query_one(
        "SELECT id, wrong_times FROM mistakes WHERE user_id = ? AND type = 'quiz' AND question = ?",
        (uid, q))
    if row:
        db.execute("UPDATE mistakes SET wrong_times = ?, updated_at = ? WHERE id = ?",
                   (row["wrong_times"] + 1, now, row["id"]))
    else:
        db.execute(
            """INSERT OR IGNORE INTO mistakes
               (user_id, type, question, direction, wrong_times, updated_at)
               VALUES (?, 'quiz', ?, ?, 1, ?)""",
            (uid, q, direction or "", now))


def _row_to_item(row: dict) -> dict:
    it = dict(row)
    it.pop("id", None)
    if it.get("tags"):
        it["tags"] = json.loads(it["tags"])
    elif it.get("type") == "code":
        it["tags"] = []
    else:
        it.pop("tags", None)
    return it


def list_mistakes(user_id: str | None = None) -> list:
    """返回错题列表（最近更新在前）。user_id 传入时仅返回该用户错题（多用户隔离）。"""
    if user_id:
        rows = db.query_all("SELECT * FROM mistakes WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
    else:
        rows = db.query_all("SELECT * FROM mistakes ORDER BY updated_at DESC")
    return [_row_to_item(r) for r in rows]


def delete_mistake(problem_id: str, user_id: str | None = None) -> bool:
    """删除一条错题（code 按 problem_id、quiz 按 question 匹配）。

    user_id 传入时仅允许删除归属该用户或无归属（旧迁移数据）的记录。
    """
    if user_id is None:
        cur = db.execute("DELETE FROM mistakes WHERE problem_id = ? OR question = ?", (problem_id, problem_id))
    else:
        cur = db.execute(
            "DELETE FROM mistakes WHERE (problem_id = ? OR question = ?) AND (user_id = '' OR user_id = ?)",
            (problem_id, problem_id, user_id))
    return cur.rowcount > 0
