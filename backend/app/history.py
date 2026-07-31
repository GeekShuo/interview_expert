"""面试历史记录的本地持久化（JSON 文件）。

- 文件位置：backend/data/history.json（已在 .gitignore 中排除）
- 数据结构：list[dict]，每条记录包含元信息、总分、结论、完整报告与转写
- 仅用于本地查看历史面试，不做任何外部传输
"""
import json
import os
import time
import uuid
import threading
import contextlib
import fcntl

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# 最大保留条数，避免无限增长
MAX_RECORDS = 200

# 进程内串行化（同进程多线程）；跨进程由 _file_lock 兜底（多 worker 场景）
_WRITE_LOCK = threading.Lock()


@contextlib.contextmanager
def _file_lock():
    """跨进程文件锁：多 uvicorn worker 并发写 JSON 时，避免读到半成品文件或互相覆盖。

    说明：仅在本机（macOS / Linux）生效，依赖 fcntl.flock。Windows 下请单 worker 运行。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    lock_path = os.path.join(DATA_DIR, ".json_write.lock")
    with open(lock_path, "w", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _ensure_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False)


def _read_all():
    _ensure_file()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_all(records):
    _ensure_file()
    # 仅保留最新 MAX_RECORDS 条
    records = records[-MAX_RECORDS:]
    # 原子写：先写临时文件再 os.replace，避免崩溃/并发导致 JSON 损坏丢全部历史
    tmp = HISTORY_FILE + ".tmp"
    with _WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, HISTORY_FILE)


def save_record(record: dict) -> str:
    """保存一条面试记录，返回其 id。"""
    with _file_lock():
        records = _read_all()
        rid = record.get("id") or ("iv_" + uuid.uuid4().hex[:12])
        record["id"] = rid
        record.setdefault("created_at", time.time())
        # 更新或插入
        for i, r in enumerate(records):
            if r.get("id") == rid:
                records[i] = record
                break
        else:
            records.append(record)
        _write_all(records)
    return rid


def list_records(user_id: str | None = None) -> list:
    """返回记录列表（按时间倒序，最新在前）。

    user_id 为空时返回全部（兼容旧数据 / 单用户）；传入时仅返回该用户记录，
    实现多用户「成长曲线」互不串数据。
    """
    records = _read_all()
    if user_id:
        records = [r for r in records if r.get("user_id") == user_id]
    records.sort(key=lambda r: r.get("finished_at") or r.get("created_at") or 0, reverse=True)
    # 列表页只返回摘要字段，避免传输大段报告
    summary = []
    for r in records:
        summary.append({
            "id": r.get("id"),
            "finished_at": r.get("finished_at"),
            "created_at": r.get("created_at"),
            "persona_name": r.get("persona_name"),
            "persona_title": r.get("persona_title"),
            "jd_title": r.get("jd_title"),
            "score": r.get("score"),
            "verdict": r.get("verdict"),
            "abandoned": r.get("abandoned", False),
            "dimensions": r.get("dimensions") or {},
            "judge_passed": r.get("judge_passed"),
            "judge_total": r.get("judge_total"),
            "weak_tags": r.get("weak_tags") or [],
        })
    return summary


def get_record(rid: str) -> dict | None:
    for r in _read_all():
        if r.get("id") == rid:
            return r
    return None


def delete_record(rid: str) -> bool:
    with _file_lock():
        records = _read_all()
        new = [r for r in records if r.get("id") != rid]
        if len(new) == len(records):
            return False
        _write_all(new)
    return True
