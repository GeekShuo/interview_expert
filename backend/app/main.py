"""FastAPI 入口：会话创建、SSE 流式对话、代码提交、静态前端托管。

鉴权：除健康检查/登录/注册/静态资源外，所有端点要求 Bearer token（JWT），
user_id 一律取自 token，不再信任客户端传入的 user_id（防越权访问他人数据）。
"""
import json
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, Depends
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db
from . import session as sess
from . import parser
from . import history as history_store
from . import mistakes as mistakes_store
from . import accounts as accounts_store
from . import voice
from .auth import create_token, decode_token, get_current_user
from .config import settings
from .schemas import ChatRequest, CodeSubmitRequest, STAGE_LABELS
from .schemas import Stage

import logging as _logging

# 语音模块日志：uvicorn 默认不给第三方 logger 挂 handler，这里单独配置
# （输出到 stderr，随服务日志一起采集；一轮一行，量级可控）
_vlog = _logging.getLogger("interview_expert")
if not _vlog.handlers:
    _h = _logging.StreamHandler()
    _h.setFormatter(_logging.Formatter("VOICE %(levelname)s: %(message)s"))
    _vlog.addHandler(_h)
    _vlog.setLevel(_logging.INFO)
    _vlog.propagate = False

app = FastAPI(title="AI 模拟面试系统")

STATIC_DIR = Path(__file__).parent / "static"

# 启动时初始化存储：建表 → 旧 JSON 存储迁移 → 账户库（全部幂等，多 worker 安全）
db.init_db()
db.migrate_legacy_json()
accounts_store.init_accounts()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _owned_session(session_id: str, user_id: str):
    """取会话并校验归属：不存在或不属于当前用户一律 404（避免探测他人会话是否存在）。

    s.user_id 为空的旧会话（升级前创建）兼容放行。
    """
    s = sess.get_session(session_id)
    if not s or (s.user_id and s.user_id != user_id):
        raise HTTPException(404, "会话不存在")
    return s


@app.get("/api/health")
def health():
    return {"ok": True, "llm_ready": settings.llm_ready, "model": settings.LLM_MODEL}


@app.post("/api/upload_resume")
async def upload_resume(file: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    """上传简历文件，返回提取的纯文本。

    安全：限制大小 5MB；校验扩展名；对 PDF 校验文件头魔数 %PDF，防改名绕过。
    """
    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_EXT = {".pdf", ".docx", ".txt"}
    name = (file.filename or "").lower()
    if not any(name.endswith(ext) for ext in ALLOWED_EXT):
        raise HTTPException(415, "仅支持 PDF / DOCX / TXT")
    # 流式读取并限制大小，避免大文件撑爆内存
    chunks = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SIZE:
            raise HTTPException(413, f"文件过大，上限 {MAX_SIZE // 1024 // 1024}MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    # PDF 魔数校验，防把可执行/任意文件改名 .pdf 绕过
    if name.endswith(".pdf") and not content.startswith(b"%PDF"):
        raise HTTPException(415, "文件不是有效的 PDF")
    text = parser.extract_text_from_file(file.filename, content)
    parsed = parser.parse_resume(text)
    return {
        "filename": file.filename,
        "text": text,
        "parsed": {
            "summary": parsed.get("summary", ""),
            "probe_points": parsed.get("probe_points", ""),
        },
    }


# ---------- 账户认证（注册 / 登录 / 游客 token）----------
@app.get("/api/accounts")
def accounts_list():
    """演示账户列表（仅 SEED_DEMO_ACCOUNTS=true 时非空，供登录页一键登录）。"""
    return {"accounts": accounts_store.list_public()}


@app.post("/api/login")
def login(username: str = Form(""), password: str = Form("")):
    """校验账户密码，成功返回 JWT 与账户信息（历史/错题按账户隔离）。"""
    acc = accounts_store.verify(username, password)
    if not acc:
        raise HTTPException(401, "用户名或密码错误")
    return {
        "ok": True,
        "token": create_token(acc["username"]),
        "user_id": acc["username"],
        "name": acc["name"],
        "tier": acc.get("tier", "free"),
    }


@app.post("/api/register")
def register(username: str = Form(""), password: str = Form(""), name: str = Form("")):
    """注册新账户并直接返回登录态 token。"""
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
        raise HTTPException(400, "用户名需为 3~20 位小写字母 / 数字 / 下划线")
    if len(password or "") < 6:
        raise HTTPException(400, "密码至少 6 位")
    try:
        user = accounts_store.create_user(username, password, name.strip() or username)
    except ValueError:
        raise HTTPException(409, "用户名已被注册")
    return {
        "ok": True,
        "token": create_token(user["username"]),
        "user_id": user["username"],
        "name": user["name"],
        "tier": "free",
    }


@app.post("/api/anon_token")
def anon_token(uid: str = Form("")):
    """游客模式：以浏览器匿名 uid 换取匿名 token。

    游客数据沿用旧版行为（本机浏览器标识隔离，无密码保护）；
    需要真正数据保护的用户请注册账户。
    """
    uid = (uid or "").strip()
    if not re.fullmatch(r"u_[a-z0-9]{6,16}", uid):
        uid = "u_" + secrets.token_hex(5)
    return {"ok": True, "token": create_token(uid, kind="anon"), "user_id": uid}


@app.get("/api/me")
def me(user_id: str = Depends(get_current_user)):
    """当前登录态信息（注册用户返回昵称/档位，游客返回匿名标识）。"""
    user = accounts_store.get_user(user_id)
    if user:
        return {"user_id": user["username"], "name": user["nickname"], "tier": user["tier"], "kind": "user"}
    return {"user_id": user_id, "name": "游客", "tier": "free", "kind": "anon"}


@app.post("/api/change_password")
def change_password(
    old_password: str = Form(""),
    new_password: str = Form(""),
    user_id: str = Depends(get_current_user),
):
    if len(new_password or "") < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if not accounts_store.change_password(user_id, old_password, new_password):
        raise HTTPException(400, "原密码错误或当前为游客账户")
    return {"ok": True}


@app.post("/api/start")
async def start_interview(
    resume_text: str = Form(""),
    jd_text: str = Form(""),
    previous_session_id: str = Form(""),
    mode: str = Form("full"),
    difficulty: str = Form(""),
    problem_id: str = Form(""),
    style: str = Form("strict"),
    resume_probe_points: str = Form(""),
    direction: str = Form(""),
    tier: str = Form(""),
    user_id: str = Depends(get_current_user),
):
    """创建面试会话（完整面试或定向练习）。

    - mode: full / coding / quiz / project
    - difficulty: 简单 / 中等 / 困难（coding 定向练习可选）
    - problem_id: 指定第一道算法题（错题重练）
    - style: strict(专业严谨) / warm(温和鼓励) / pressure(高压实战)
    - previous_session_id: 若传入且对应会话未完成，则标记「未完成」写入历史。
    - user_id 取自 JWT（游客为匿名 uid），客户端不再传入。
    """
    if previous_session_id:
        prev = sess.get_session(previous_session_id)
        # 仅允许终结本人名下的旧会话，防止越权标记他人面试为「未完成」
        if prev is not None and prev.stage != Stage.FINISHED \
                and (not prev.user_id or prev.user_id == user_id):
            prev.abandon()
    if mode not in sess.MODE_FLOWS:
        mode = "full"
    # 定向练习（coding/quiz/project）允许不填简历/JD，用占位文本保证 prompt 完整；
    # full 与 no_code 都是完整面试，使用候选人真实简历/JD
    if mode in ("coding", "quiz", "project"):
        resume_text = resume_text.strip() or "（定向练习模式，候选人未提供简历，请勿追问简历细节）"
        jd_text = jd_text.strip() or "算法工程师（定向练习）"
    s = sess.create_session(resume_text, jd_text, mode=mode,
                            difficulty=difficulty or None, problem_id=problem_id or None,
                            style=style or "strict", direction=direction or None,
                            tier=tier or "normal", user_id=user_id)
    if resume_probe_points and resume_probe_points.strip():
        s.resume["probe_points"] = resume_probe_points.strip()
    return {
        "session_id": s.id,
        "persona": s.persona,
        "jd": {"title": s.jd.get("title"), "requirements": s.jd.get("requirements")},
        "resume_summary": s.resume.get("summary"),
        "resume_probe_points": s.resume.get("probe_points"),
        "stage": s.stage.value,
        "stage_label": STAGE_LABELS[s.stage],
        "mode": s.mode,
        "stages": [{"key": st.value, "label": STAGE_LABELS[st]} for st in s.stage_flow],
        "tier": s.tier,
        "llm_ready": settings.llm_ready,
    }


@app.get("/api/opening")
def opening(session_id: str, user_id: str = Depends(get_current_user)):
    """面试官开场白（SSE 流式）。"""
    s = _owned_session(session_id, user_id)

    def gen():
        try:
            for ev in s.stream_reply(None):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/chat")
def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    """用户发消息，SSE 流式返回面试官回复。"""
    s = _owned_session(req.session_id, user_id)

    def gen():
        try:
            for ev in s.stream_reply(req.message):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/submit_code")
def submit_code(req: CodeSubmitRequest, user_id: str = Depends(get_current_user)):
    """提交算法题代码：先沙箱自动判题，再进入对话由面试官点评。"""
    s = _owned_session(req.session_id, user_id)

    def gen():
        try:
            for ev in s.stream_code_submission(req.code, req.language):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/state")
def state(session_id: str, user_id: str = Depends(get_current_user)):
    """会话完整状态：供前端刷新/重开页面后恢复进行中的面试。"""
    s = _owned_session(session_id, user_id)
    p = s.current_problem
    return {
        "session_id": s.id,
        "stage": s.stage.value,
        "stage_label": STAGE_LABELS[s.stage],
        "progress": s.progress,
        "persona": s.persona,
        "jd": {"title": s.jd.get("title"), "requirements": s.jd.get("requirements")},
        "resume_summary": s.resume.get("summary"),
        "mode": s.mode,
        "style": s.style,
        "tier": s.tier,
        "stages": [{"key": st.value, "label": STAGE_LABELS[st]} for st in s.stage_flow],
        "history": s.public_history(),
        "current_problem": None if not p else {
            "id": p["id"], "title": p["title"], "difficulty": p["difficulty"],
            "tags": p["tags"], "statement": p["statement"],
            "signature": p.get("signature", ""), "judgeable": bool(p.get("tests")),
        },
        "score": s.score,
        "verdict": s.verdict,
        "report": s.report_text,
        "abandoned": s.abandoned,
    }


# ---------- 历史面试 ----------
@app.get("/api/history")
def list_history(user_id: str = Depends(get_current_user)):
    """返回当前用户的历史面试摘要列表（最新在前）。"""
    return {"records": history_store.list_records(user_id)}


@app.get("/api/history/{record_id}")
def get_history(record_id: str, user_id: str = Depends(get_current_user)):
    """返回某次面试的完整记录（含报告与转写）。仅记录所有者可访问。"""
    rec = history_store.get_record(record_id)
    # 无归属的旧记录兼容放行；有归属且非本人一律 404（防探测）
    if not rec or (rec.get("user_id") and rec.get("user_id") != user_id):
        raise HTTPException(404, "记录不存在")
    return rec


@app.delete("/api/history/{record_id}")
def delete_history(record_id: str, user_id: str = Depends(get_current_user)):
    """删除一条历史记录。仅记录所有者可删除。"""
    rec = history_store.get_record(record_id)
    if not rec or (rec.get("user_id") and rec.get("user_id") != user_id):
        raise HTTPException(404, "记录不存在")
    history_store.delete_record(record_id)
    return {"ok": True}


# ---------- 错题本 ----------
@app.get("/api/mistakes")
def list_mistakes(user_id: str = Depends(get_current_user)):
    """当前用户的错题本：自动判题未全通过的题（全通过后自动消灭）。"""
    return {"mistakes": mistakes_store.list_mistakes(user_id)}


@app.post("/api/mistakes/quiz")
def add_quiz_mistake(direction: str = Form(""), question: str = Form(""),
                     user_id: str = Depends(get_current_user)):
    """八股错题：八股无客观判题，由用户自评入本（按题目文本去重，按用户分区）。"""
    mistakes_store.record_quiz_mistake(direction, question, user_id)
    return {"ok": True}


@app.delete("/api/mistakes/{problem_id}")
def delete_mistake(problem_id: str, user_id: str = Depends(get_current_user)):
    ok = mistakes_store.delete_mistake(problem_id, user_id=user_id)
    if not ok:
        raise HTTPException(404, "错题不存在")
    return {"ok": True}


# ---------- 云端语音（ASR + TTS）----------
@app.get("/api/voice/config")
def voice_config(user_id: str = Depends(get_current_user)):
    """告知前端云端语音是否可用及音频参数；不可用时前端回退浏览器原生语音。"""
    return voice.voice_config_payload()


@app.websocket("/ws/voice/{session_id}")
async def voice_ws(ws: WebSocket, session_id: str, token: str = ""):
    """语音面试全双工通道：麦克风 PCM 上行，面试官 TTS PCM 下行 + 对话事件。

    鉴权：WS 无法携带自定义头，token 经 query 参数传入，连接建立后立即校验。
    """
    await ws.accept()
    payload = decode_token(token) if token else None
    uid = (payload or {}).get("sub")
    if not uid:
        await ws.send_text('{"type":"error","message":"未登录或登录已过期"}')
        await ws.close(code=4001)
        return
    s = sess.get_session(session_id)
    provider = voice.get_voice_provider()
    if s is None or (s.user_id and s.user_id != uid):
        await ws.send_text('{"type":"error","message":"会话不存在或已结束"}')
        await ws.close(code=4004)
        return
    if provider is None:
        await ws.send_text('{"type":"error","message":"服务端未配置语音供应商"}')
        await ws.close(code=4003)
        return
    from .voice.pipeline import VoiceSession
    try:
        await VoiceSession(ws, s, provider).run()
    except Exception:
        # 连接异常退出：尽力通知客户端后关闭，避免悬挂
        try:
            await ws.close(code=1011)
        except Exception:
            pass


# ---------- 静态前端 ----------
@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/favicon.ico")
def favicon():
    # 无图标文件，返回 204 避免浏览器反复请求造成 console 404 噪音
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
