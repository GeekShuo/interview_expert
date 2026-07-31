"""错题本：自动判题未全通过的算法题，落盘供定向重练。

- 文件：backend/data/mistakes.json（gitignore 的 data/ 下）
- 按题目 id 去重：重复做错则更新最近成绩并累加错误次数；
  之后全部通过则从错题本移除（自动消灭错题）。
"""
import json
import os
import time
import threading

from .history import DATA_DIR, _file_lock

MISTAKES_FILE = os.path.join(DATA_DIR, "mistakes.json")
_LOCK = threading.Lock()


def _read_all() -> list:
    try:
        with open(MISTAKES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _write_all(items: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MISTAKES_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    with _file_lock():
        items = _read_all()
        by_id = {it["problem_id"]: it for it in items}
        for pid, j in latest.items():
            failed = (j.get("passed") or 0) < j["total"]
            if failed:
                it = by_id.get(pid)
                if it:
                    it["wrong_times"] = it.get("wrong_times", 0) + 1
                    it["last_passed"] = j.get("passed") or 0
                    it["last_total"] = j["total"]
                    it["updated_at"] = time.time()
                else:
                    by_id[pid] = {
                        "problem_id": pid,
                        "title": j.get("problem", ""),
                        "difficulty": j.get("difficulty", ""),
                        "tags": j.get("tags", []),
                        "wrong_times": 1,
                        "last_passed": j.get("passed") or 0,
                        "last_total": j["total"],
                        "user_id": user_id or "",
                        "updated_at": time.time(),
                    }
            else:
                by_id.pop(pid, None)  # 已全通过：消灭错题
        _write_all(sorted(by_id.values(), key=lambda x: x.get("updated_at", 0), reverse=True))


def record_quiz_mistake(direction: str, question: str, user_id: str | None = None):
    """八股错题：八股无客观判题，由用户自评入本（按题目文本去重）。"""
    q = (question or "").strip()
    if not q:
        return
    with _file_lock():
        items = _read_all()
        for it in items:
            if it.get("type") == "quiz" and it.get("question") == q and it.get("user_id") == (user_id or ""):
                it["wrong_times"] = it.get("wrong_times", 0) + 1
                it["updated_at"] = time.time()
                break
        else:
            items.append({
                "type": "quiz",
                "question": q,
                "direction": direction or "",
                "user_id": user_id or "",
                "wrong_times": 1,
                "updated_at": time.time(),
            })
        _write_all(sorted(items, key=lambda x: x.get("updated_at", 0), reverse=True))


def list_mistakes(user_id: str | None = None) -> list:
    """返回错题列表。user_id 传入时仅返回该用户的错题（多用户隔离）。"""
    items = _read_all()
    if user_id:
        items = [it for it in items if it.get("user_id") == user_id]
    return items


def delete_mistake(problem_id: str) -> bool:
    with _file_lock():
        items = _read_all()
        new = [it for it in items if it.get("problem_id") != problem_id and it.get("question") != problem_id]
        if len(new) == len(items):
            return False
        _write_all(new)
    return True
