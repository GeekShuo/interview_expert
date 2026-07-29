"""代码沙箱执行与自动判题。

设计：
- 仅支持 Python（MVP）；其他语言返回 supported=False，由面试官凭讲解评判。
- 子进程隔离执行：`python -I`（isolated 模式，不加载用户 site/env），超时强杀。
- 判题 harness 与用户代码拼成临时脚本，结果以 JSON 打印在特殊标记行之后，
  避免用户 print 污染判题输出。
- 每题结构化测试用例见 problems.py：entry / kind(func|linked_list|tree|ops) / tests。

安全边界（本地单机 MVP 可接受）：
- 超时 8s 强杀、输出截断 64KB、-I 隔离模式、临时目录运行。
- 不做网络/文件系统硬隔离（本地自用）；上线多租户前需换 Docker/Judge0。
"""
import json
import os
import subprocess
import sys
import tempfile

JUDGE_MARKER = "###JUDGE_RESULT###"
TIMEOUT_SEC = 8
MAX_OUTPUT = 64 * 1024

# 判题 harness 模板：__SPEC_LITERAL__ 会被替换为 repr(json字符串)，__USER_CODE__ 为用户代码
_HARNESS = r'''
import json as _json, copy as _copy, traceback as _tb, os as _os
_SELF_DIR = _os.path.dirname(_os.path.abspath(__file__))

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def _build_list(arr):
    head = None
    for v in reversed(arr or []):
        head = ListNode(v, head)
    return head

def _list_to_arr(node):
    out = []
    seen = 0
    while node is not None and seen < 100000:
        out.append(node.val)
        node = node.next
        seen += 1
    return out

def _build_tree(arr):
    if not arr:
        return None
    vals = list(arr)
    root = TreeNode(vals[0])
    queue = [root]
    i = 1
    while queue and i < len(vals):
        node = queue.pop(0)
        if node is None:
            continue
        if i < len(vals):
            v = vals[i]; i += 1
            if v is not None:
                node.left = TreeNode(v); queue.append(node.left)
        if i < len(vals):
            v = vals[i]; i += 1
            if v is not None:
                node.right = TreeNode(v); queue.append(node.right)
    return root

# ==================== 用户代码 ====================
__USER_CODE__
# ==================== 用户代码结束 ====================

_SPEC = _json.loads(__SPEC_LITERAL__)

def _norm(v):
    if isinstance(v, tuple):
        v = list(v)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v

def _run():
    kind = _SPEC.get("kind", "func")
    entry = _SPEC.get("entry")
    results = []
    passed = 0
    for t in _SPEC.get("tests", []):
        item = {"input": t.get("args") if kind != "ops" else t.get("ops"),
                "expected": t.get("expected"), "ok": False, "got": None, "error": None}
        try:
            if kind == "ops":
                cls = globals().get(entry)
                if cls is None:
                    raise NameError("未找到类 %s，请按题目要求命名" % entry)
                obj = None
                outs = []
                for name, args in t["ops"]:
                    if name == entry:
                        obj = cls(*args)
                        outs.append(None)
                    else:
                        outs.append(getattr(obj, name)(*args))
                got = _norm(outs)
                item["got"] = got
                item["ok"] = got == _norm(t["expected"])
            else:
                fn = globals().get(entry)
                if fn is None:
                    raise NameError("未找到函数 %s，请按题目给出的函数签名命名" % entry)
                args = _copy.deepcopy(t["args"])
                if kind == "linked_list" and args:
                    args[0] = _build_list(args[0])
                if kind == "tree" and args:
                    args[0] = _build_tree(args[0])
                got = fn(*args)
                if kind == "linked_list":
                    got = _list_to_arr(got)
                got = _norm(got)
                item["got"] = got
                exp = _norm(t["expected"])
                if t.get("compare") == "sorted" and isinstance(got, list) and isinstance(exp, list):
                    try:
                        item["ok"] = sorted(got) == sorted(exp)
                    except TypeError:
                        item["ok"] = got == exp
                else:
                    item["ok"] = got == exp
        except Exception as e:
            tb = _tb.format_exc().strip().splitlines()
            item["error"] = "%s: %s" % (type(e).__name__, e)
            item["trace"] = [ln.replace(_SELF_DIR, "").replace("\\", "/") for ln in tb[-3:]]
        if item["ok"]:
            passed += 1
        results.append(item)
    print("__JUDGE_MARKER__")
    print(_json.dumps({"passed": passed, "total": len(results), "results": results},
                      ensure_ascii=False, default=str))

_run()
'''


def judge(problem: dict, code: str, language: str = "python") -> dict:
    """执行判题，返回结构化结果。

    返回: {supported, passed, total, results, error?}
    """
    if not language.lower().startswith("py"):
        return {"supported": False,
                "message": "自动判题目前仅支持 Python，其他语言由面试官人工评判"}
    tests = problem.get("tests")
    if not tests:
        return {"supported": False, "message": "本题暂无自动测试用例"}

    spec = {
        "entry": problem.get("entry"),
        "kind": problem.get("kind", "func"),
        "tests": tests,
    }
    script = (
        _HARNESS
        .replace("__USER_CODE__", code)
        .replace("__SPEC_LITERAL__", repr(json.dumps(spec, ensure_ascii=False)))
        .replace("__JUDGE_MARKER__", JUDGE_MARKER)
    )

    tmpdir = tempfile.mkdtemp(prefix="judge_")
    path = os.path.join(tmpdir, "solution.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", path],
                capture_output=True, timeout=TIMEOUT_SEC, cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            return {"supported": True, "passed": 0, "total": len(tests), "results": [],
                    "error": f"执行超时（>{TIMEOUT_SEC}s），可能存在死循环或复杂度过高"}
        stdout = proc.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
        stderr = proc.stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]
        if JUDGE_MARKER in stdout:
            payload = stdout.split(JUDGE_MARKER, 1)[1].strip()
            try:
                data = json.loads(payload.splitlines()[0])
                data["supported"] = True
                return data
            except Exception:
                pass
        # 没拿到判题结果：多半是语法错误 / 全局异常
        err = _brief_error(stderr) or "代码执行失败（无输出）"
        return {"supported": True, "passed": 0, "total": len(tests),
                "results": [], "error": err}
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def _brief_error(stderr: str) -> str:
    """从 stderr 提取简短错误（最后几行 traceback），并隐去临时路径。"""
    if not stderr:
        return ""
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    tail = lines[-3:]
    cleaned = []
    for ln in tail:
        if "judge_" in ln and "solution.py" in ln:
            # 隐去临时文件绝对路径，仅保留行号信息
            idx = ln.find("solution.py")
            ln = '  File "solution.py' + ln[idx + len("solution.py"):]
        cleaned.append(ln)
    return "\n".join(cleaned)[:800]


def summarize_for_llm(result: dict) -> str:
    """把判题结果压缩成给面试官(LLM)看的客观描述。"""
    if not result.get("supported"):
        return f"（{result.get('message', '本次未自动判题')}）"
    if result.get("error"):
        return f"代码未能通过自动判题：{result['error']}（0/{result.get('total', '?')} 用例通过）"
    passed, total = result.get("passed", 0), result.get("total", 0)
    lines = [f"自动判题：通过 {passed}/{total} 组测试用例。"]
    for i, r in enumerate(result.get("results", []), 1):
        if r.get("ok"):
            continue
        detail = f"用例{i}失败：输入={json.dumps(r.get('input'), ensure_ascii=False)}，" \
                 f"期望={json.dumps(r.get('expected'), ensure_ascii=False)}，" \
                 f"实际={json.dumps(r.get('got'), ensure_ascii=False)}"
        if r.get("error"):
            detail += f"，错误={r['error']}"
        lines.append(detail)
        if len(lines) >= 4:  # 最多给 3 个失败用例，控制上下文
            break
    return "\n".join(lines)
