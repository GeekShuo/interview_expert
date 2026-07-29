"""FastAPI 入口：会话创建、SSE 流式对话、代码提交、静态前端托管。"""
import json
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import session as sess
from . import parser
from . import history as history_store
from .config import settings
from .schemas import ChatRequest, CodeSubmitRequest, STAGE_LABELS
from .schemas import Stage

app = FastAPI(title="AI 模拟面试系统")

STATIC_DIR = Path(__file__).parent / "static"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/health")
def health():
    return {"ok": True, "llm_ready": settings.llm_ready, "model": settings.LLM_MODEL}


@app.post("/api/upload_resume")
async def upload_resume(file: UploadFile = File(...)):
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
    return {"filename": file.filename, "text": text}


@app.post("/api/start")
async def start_interview(
    resume_text: str = Form(""),
    jd_text: str = Form(""),
    previous_session_id: str = Form(""),
):
    """创建面试会话，返回会话信息（面试官、岗位等）。

    previous_session_id：若传入且对应会话尚未完成，则将其标记为「未完成」并写入历史。
    """
    if previous_session_id:
        prev = sess.get_session(previous_session_id)
        if prev is not None and prev.stage != Stage.FINISHED:
            prev.abandon()
    s = sess.create_session(resume_text, jd_text)
    return {
        "session_id": s.id,
        "persona": s.persona,
        "jd": {"title": s.jd.get("title"), "requirements": s.jd.get("requirements")},
        "resume_summary": s.resume.get("summary"),
        "stage": s.stage.value,
        "stage_label": STAGE_LABELS[s.stage],
        "stages": [
            {"key": st.value, "label": STAGE_LABELS[st]}
            for st in [Stage.GREETING, Stage.PROJECT, Stage.CODING, Stage.QUIZ, Stage.REPORT]
        ],
        "llm_ready": settings.llm_ready,
    }


@app.get("/api/opening")
def opening(session_id: str):
    """面试官开场白（SSE 流式）。"""
    s = sess.get_session(session_id)
    if not s:
        raise HTTPException(404, "会话不存在")

    def gen():
        try:
            for ev in s.stream_reply(None):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/chat")
def chat(req: ChatRequest):
    """用户发消息，SSE 流式返回面试官回复。"""
    s = sess.get_session(req.session_id)
    if not s:
        raise HTTPException(404, "会话不存在")

    def gen():
        try:
            for ev in s.stream_reply(req.message):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/submit_code")
def submit_code(req: CodeSubmitRequest):
    """提交算法题代码：先沙箱自动判题，再进入对话由面试官点评。"""
    s = sess.get_session(req.session_id)
    if not s:
        raise HTTPException(404, "会话不存在")

    def gen():
        try:
            for ev in s.stream_code_submission(req.code, req.language):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/state")
def state(session_id: str):
    """会话完整状态：供前端刷新/重开页面后恢复进行中的面试。"""
    s = sess.get_session(session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    p = s.current_problem
    return {
        "session_id": s.id,
        "stage": s.stage.value,
        "stage_label": STAGE_LABELS[s.stage],
        "progress": s.progress,
        "persona": s.persona,
        "jd": {"title": s.jd.get("title"), "requirements": s.jd.get("requirements")},
        "resume_summary": s.resume.get("summary"),
        "stages": [
            {"key": st.value, "label": STAGE_LABELS[st]}
            for st in [Stage.GREETING, Stage.PROJECT, Stage.CODING, Stage.QUIZ, Stage.REPORT]
        ],
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
def list_history():
    """返回历史面试摘要列表（最新在前）。"""
    return {"records": history_store.list_records()}


@app.get("/api/history/{record_id}")
def get_history(record_id: str):
    """返回某次面试的完整记录（含报告与转写）。"""
    rec = history_store.get_record(record_id)
    if not rec:
        raise HTTPException(404, "记录不存在")
    return rec


@app.delete("/api/history/{record_id}")
def delete_history(record_id: str):
    """删除一条历史记录。"""
    ok = history_store.delete_record(record_id)
    if not ok:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}


# ---------- 静态前端 ----------
@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/favicon.ico")
def favicon():
    # 无图标文件，返回 204 避免浏览器反复请求造成 console 404 噪音
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
