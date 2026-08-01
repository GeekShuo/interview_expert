"""代码沙箱执行与自动判题。

设计：
- 仅支持 Python（MVP）；其他语言返回 supported=False，由面试官凭讲解评判。
- 判题 harness 与用户代码拼成临时脚本，结果以 JSON 打印在特殊标记行之后，
  避免用户 print 污染判题输出。
- 每题结构化测试用例见 problems.py：entry / kind(func|linked_list|tree|ops) / tests。

执行模式（settings.SANDBOX_MODE）：
- docker：一次性 Docker 容器，断网 + 只读挂载 + 128MB 内存 + 0.5 核 + 64 进程 +
  cap-drop ALL + no-new-privileges + 非 root + 内容器 timeout 8s（生产必须）。
- local：本地 `python -I` 子进程，超时 8s 强杀、输出截断 64KB。
  无网络/文件系统硬隔离，仅限开发调试，生产严禁使用。
- auto（默认）：有 Docker 用 Docker，否则 local 并打印一次警告。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

from .config import settings

JUDGE_MARKER = "###JUDGE_RESULT###"
TIMEOUT_SEC = 8                                # 判题脚本自身执行时限
DOCKER_WALL_TIMEOUT = TIMEOUT_SEC + 6          # docker 模式外层兜底（含容器启动开销）
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

# Docker 可用性探测结果（进程内缓存，避免每次判题都跑 docker version）
_docker_state: dict = {"checked": False, "available": False, "image": False}
_warned_local = False

SANDBOX_UNAVAILABLE_MSG = "判题服务暂不可用（沙箱未就绪），请稍后再试；本次由面试官人工评判"


def _docker_available() -> bool:
    if not _docker_state["checked"]:
        _docker_state["checked"] = True
        if shutil.which("docker"):
            try:
                proc = subprocess.run(
                    ["docker", "version", "--format", "{{.Server.Version}}"],
                    capture_output=True, timeout=5)
                _docker_state["available"] = proc.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
    return _docker_state["available"]


def _judge_image_ready() -> bool:
    """判题镜像是否存在；不存在则尝试用 backend/docker/Dockerfile.judge 自动构建一次。"""
    if _docker_state["image"]:
        return True
    try:
        proc = subprocess.run(["docker", "image", "inspect", settings.JUDGE_IMAGE],
                              capture_output=True, timeout=5)
        if proc.returncode == 0:
            _docker_state["image"] = True
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    build_ctx = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docker"))
    try:
        proc = subprocess.run(
            ["docker", "build", "-t", settings.JUDGE_IMAGE, "-f",
             os.path.join(build_ctx, "Dockerfile.judge"), build_ctx],
            capture_output=True, timeout=600)
        _docker_state["image"] = proc.returncode == 0
        if _docker_state["image"]:
            print(f"[runner] 判题镜像 {settings.JUDGE_IMAGE} 自动构建完成", flush=True)
    except (OSError, subprocess.TimeoutExpired):
        _docker_state["image"] = False
    return _docker_state["image"]


def sandbox_mode() -> str:
    """实际生效的判题模式：显式配置优先，auto 时按 Docker 可用性自动选择。"""
    m = (settings.SANDBOX_MODE or "auto").strip().lower()
    if m in ("docker", "local"):
        return m
    return "docker" if (_docker_available() and _judge_image_ready()) else "local"


def _sandbox_unavailable() -> bool:
    """docker 强制模式但 daemon/镜像未就绪：判题明确不可用，绝不静默退回本地执行。"""
    return (settings.SANDBOX_MODE or "").strip().lower() == "docker" and not (
        _docker_available() and _judge_image_ready())


def _warn_local_once():
    global _warned_local
    if not _warned_local:
        _warned_local = True
        print("[runner] 警告：Docker 不可用，判题回退为本地子进程模式（无网络/文件系统硬隔离），"
              "仅限开发环境！生产部署请安装 Docker 并设 SANDBOX_MODE=docker", flush=True)


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
    if _sandbox_unavailable():
        return {"supported": False, "message": SANDBOX_UNAVAILABLE_MSG}

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

    mode = sandbox_mode()
    if mode == "local":
        _warn_local_once()

    tmpdir = tempfile.mkdtemp(prefix="judge_")
    path = os.path.join(tmpdir, "solution.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        if mode == "docker":
            return _run_docker(tmpdir, len(tests))
        return _run_local(path, len(tests))
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def _timeout_result(total: int) -> dict:
    return {"supported": True, "passed": 0, "total": total, "results": [],
            "error": f"执行超时（>{TIMEOUT_SEC}s），可能存在死循环或复杂度过高"}


def _run_local(path: str, total: int) -> dict:
    """本地子进程执行（仅开发环境：无网络/文件系统硬隔离）。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", path],
            capture_output=True, timeout=TIMEOUT_SEC, cwd=os.path.dirname(path),
        )
    except subprocess.TimeoutExpired:
        return _timeout_result(total)
    stdout = proc.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    stderr = proc.stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    return _parse_output(stdout, stderr, total)


def _run_docker(tmpdir: str, total: int) -> dict:
    """一次性 Docker 容器中执行判题：断网、限资源、只读挂载、非 root。

    双超时：容器内 `timeout 8s`（命令级）+ 外层 CLI 14s 兜底（含容器启动开销）；
    外层超时后按容器名 rm -f，避免 --rm 失效导致的残留。
    """
    name = "judge-" + uuid.uuid4().hex[:12]
    mount = os.path.abspath(tmpdir)
    if os.name == "nt":
        # Docker Desktop (Windows) 需要 /c/... 形式路径
        mount = "/" + mount[0].lower() + mount[2:].replace("\\", "/")
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network=none",                                  # 断网：无法外联/下载/反弹 shell
        f"--memory={settings.JUDGE_MEM}",
        f"--memory-swap={settings.JUDGE_MEM}",             # 内存硬上限，禁 swap
        f"--cpus={settings.JUDGE_CPUS}",                   # CPU 配额
        "--pids-limit=64",                                 # 防 fork 炸弹
        "--read-only",                                     # 根文件系统只读
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",       # 仅供运行时临时文件
        "--cap-drop=ALL",                                  # 去除全部内核能力
        "--security-opt=no-new-privileges",                # 禁止提权
        "-v", f"{mount}:/work:ro",                         # 仅挂载判题目录，只读
        "-w", "/work",
        settings.JUDGE_IMAGE,
        "timeout", str(TIMEOUT_SEC), "python", "-I", "-X", "utf8", "/work/solution.py",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=DOCKER_WALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return _timeout_result(total)
    except OSError:
        return {"supported": False, "message": SANDBOX_UNAVAILABLE_MSG}
    if proc.returncode == 124:  # 容器内 timeout 命令判定超时
        return _timeout_result(total)
    stdout = proc.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    stderr = proc.stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    return _parse_output(stdout, stderr, total)


def _parse_output(stdout: str, stderr: str, total: int) -> dict:
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
    return {"supported": True, "passed": 0, "total": total,
            "results": [], "error": err}


def _brief_error(stderr: str) -> str:
    """从 stderr 提取简短错误（最后几行 traceback），并隐去临时/容器内路径。"""
    if not stderr:
        return ""
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    tail = lines[-3:]
    cleaned = []
    for ln in tail:
        if "solution.py" in ln:
            # 隐去绝对路径（宿主临时目录或容器 /work），仅保留行号信息
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
