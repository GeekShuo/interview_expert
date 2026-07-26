"""简历 / JD 解析：文件文本提取 + LLM 结构化抽取（含降级）。"""
import io
import json
from typing import Optional

from . import llm
from .config import settings


def extract_text_from_file(filename: str, content: bytes) -> str:
    """从上传的 PDF / DOCX / TXT 中提取纯文本。"""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts).strip()
        except Exception as e:
            return f"[PDF 解析失败: {e}]"
    if name.endswith(".docx"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs).strip()
        except Exception as e:
            return f"[DOCX 解析失败: {e}]"
    # 纯文本兜底
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(enc).strip()
        except Exception:
            continue
    return ""


def parse_resume(resume_text: str) -> dict:
    """把简历文本结构化，并生成可追问点清单。LLM 不可用时降级为原文。"""
    resume_text = (resume_text or "").strip()
    if not resume_text:
        return {
            "summary": "候选人未提供简历。",
            "full_text": "",
            "probe_points": "简历为空，请围绕候选人自我介绍中提到的经历进行追问。",
        }

    if not settings.llm_ready:
        return {
            "summary": resume_text[:500],
            "full_text": resume_text,
            "probe_points": "（未配置 LLM，无法自动生成追问点，将由面试官临场追问。）",
        }

    prompt = f"""请分析下面这份算法岗候选人的简历，输出严格的 JSON（不要多余文字），字段：
- "summary": 一段话总结候选人背景（150字内）
- "probe_points": 字符串，列出 3-6 个最值得在面试中深挖追问的点（每个点换行，
  聚焦技术决策、可量化指标、数据规模、以及可能夸大或存疑之处）

简历原文：
{resume_text[:4000]}
"""
    try:
        raw = llm.chat([{"role": "user", "content": prompt}], temperature=0.3)
        data = _extract_json(raw)
        return {
            "summary": data.get("summary", resume_text[:300]),
            "full_text": resume_text,
            "probe_points": data.get("probe_points", ""),
        }
    except Exception:
        return {"summary": resume_text[:300], "full_text": resume_text, "probe_points": ""}


def parse_jd(jd_text: str) -> dict:
    """结构化 JD。"""
    jd_text = (jd_text or "").strip()
    if not jd_text:
        return {"title": "算法工程师（实习/校招）", "requirements": "通用算法岗要求", "full_text": ""}

    if not settings.llm_ready:
        return {"title": "算法工程师", "requirements": jd_text[:300], "full_text": jd_text}

    prompt = f"""分析下面的岗位 JD，输出严格 JSON（不要多余文字），字段：
- "title": 岗位名称
- "requirements": 一段话总结核心技能要求（100字内）

JD 原文：
{jd_text[:2000]}
"""
    try:
        raw = llm.chat([{"role": "user", "content": prompt}], temperature=0.3)
        data = _extract_json(raw)
        return {
            "title": data.get("title", "算法工程师"),
            "requirements": data.get("requirements", jd_text[:200]),
            "full_text": jd_text,
        }
    except Exception:
        return {"title": "算法工程师", "requirements": jd_text[:200], "full_text": jd_text}


def _extract_json(raw: str) -> dict:
    """从 LLM 输出里鲁棒地抽取 JSON。"""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    return json.loads(raw)
