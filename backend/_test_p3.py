"""Phase 3 回归：定向练习模式 + 错题本。"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient

from app import main
from app import mistakes as ms

c = TestClient(main.app)


def ok(r):
    return r.status_code == 200


# 1) 完整模式：5 个阶段
r = c.post("/api/start", data={"jd_text": "算法实习生", "resume_text": "本科"})
assert ok(r), r.text
d = r.json()
assert d["mode"] == "full"
assert [s["key"] for s in d["stages"]] == ["greeting", "project", "coding", "quiz", "report"], d["stages"]
print("OK 完整模式 stages =", [s["key"] for s in d["stages"]])

# 2) 定向：算法手撕
r = c.post("/api/start", data={"mode": "coding"})
assert ok(r), r.text
d = r.json()
assert d["mode"] == "coding"
assert [s["key"] for s in d["stages"]] == ["coding", "report"], d["stages"]
print("OK coding 模式 stages =", [s["key"] for s in d["stages"]])

# 3) 定向：指定难度 + 指定题
r = c.post("/api/start", data={"mode": "coding", "difficulty": "简单", "problem_id": "two-sum"})
assert ok(r), r.text
d = r.json()
assert d["mode"] == "coding"
print("OK 指定题 two-sum ->", d.get("current_problem", {}).get("title"))

# 4) 错题本：先清空再写入再删除
ms._write_all([])
assert ms.list_mistakes() == []

ms.record_session_results([
    {"problem": "两数之和", "problem_id": "two-sum", "tags": ["哈希", "数组"], "difficulty": "简单",
     "supported": True, "passed": 1, "total": 3},
])
lst = ms.list_mistakes()
assert len(lst) == 1 and lst[0]["problem_id"] == "two-sum", lst
ms.record_session_results([
    {"problem": "两数之和", "problem_id": "two-sum", "tags": ["哈希"], "difficulty": "简单",
     "supported": True, "passed": 0, "total": 3},
])
lst = ms.list_mistakes()
assert len(lst) == 1 and lst[0]["wrong_times"] == 2, lst
ms.record_session_results([
    {"problem": "两数之和", "problem_id": "two-sum", "tags": ["哈希"], "difficulty": "简单",
     "supported": True, "passed": 3, "total": 3},
])
assert ms.list_mistakes() == [], ms.list_mistakes()
print("OK 错题本 record/update/消灭 流程正确")

# 5) 通过 HTTP 端点验证读写
ms.record_session_results([
    {"problem": "反转链表", "problem_id": "reverse-list", "tags": ["链表"], "difficulty": "中等",
     "supported": True, "passed": 0, "total": 2},
])
r = c.get("/api/mistakes")
assert ok(r)
body = r.json()
assert any(m["problem_id"] == "reverse-list" for m in body["mistakes"]), body
r = c.delete("/api/mistakes/reverse-list")
assert ok(r) and r.json().get("ok"), r.text
r = c.get("/api/mistakes")
assert not any(m["problem_id"] == "reverse-list" for m in r.json()["mistakes"])
print("OK /api/mistakes GET/DELETE 正确")

# 6) 中途放弃也写错题本（关键闭环修复）
ms._write_all([])
from app import session as sess_mod
s = sess_mod.Session("本科", "算法实习生", mode="coding")
s.judge_results = [{"problem": "两数之和", "problem_id": "two-sum", "tags": ["哈希"], "difficulty": "简单",
                    "supported": True, "passed": 0, "total": 3}]
s.abandon()
lst = ms.list_mistakes()
assert any(m["problem_id"] == "two-sum" for m in lst), lst
ms._write_all([])
print("OK 中途放弃写错题本")

ms._write_all([])  # 清理
print("ALL_P3_OK")
