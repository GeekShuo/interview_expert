"""FastAPI 入口：会话创建、SSE 流式对话、代码提交、静态前端托管。"""
import json
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import session as sess
from . import parser
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
    """上传简历文件，返回提取的纯文本。"""
    content = await file.read()
    text = parser.extract_text_from_file(file.filename, content)
    return {"filename": file.filename, "text": text}


@app.post("/api/start")
async def start_interview(
    resume_text: str = Form(""),
    jd_text: str = Form(""),
):
    """创建面试会话，返回会话信息（面试官、岗位等）。"""
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
            yield _sse({"type": "error", "message": str(e)})

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
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/submit_code")
def submit_code(req: CodeSubmitRequest):
    """提交算法题代码，作为一条特殊消息进入对话。"""
    s = sess.get_session(req.session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    msg = f"这是我写的代码（{req.language}）：\n```{req.language}\n{req.code}\n```"

    def gen():
        try:
            for ev in s.stream_reply(msg):
                yield _sse(ev)
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/state")
def state(session_id: str):
    s = sess.get_session(session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    return {
        "session_id": s.id,
        "stage": s.stage.value,
        "stage_label": STAGE_LABELS[s.stage],
        "progress": s.progress,
        "current_problem": s.current_problem,
    }


# ---------- 静态前端 ----------
@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
