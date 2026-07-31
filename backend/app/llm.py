"""LLM 客户端封装：OpenAI 兼容接口，支持同步与流式调用。"""
from typing import Iterator
from openai import OpenAI

from .config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "sk-placeholder",
            timeout=60.0,      # 避免LLM卡住时SSE长期挂起
            max_retries=2,     # 偶发网络错误自动重试
        )
    return _client


def chat(messages: list[dict], temperature: float | None = None, model: str | None = None) -> str:
    """一次性返回完整回复。model 缺省用普通模型。"""
    resp = get_client().chat.completions.create(
        model=model or settings.LLM_MODEL,
        messages=messages,
        temperature=settings.LLM_TEMPERATURE if temperature is None else temperature,
        stream=False,
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict], temperature: float | None = None, model: str | None = None) -> Iterator[str]:
    """流式返回增量文本。model 缺省用普通模型。"""
    stream = get_client().chat.completions.create(
        model=model or settings.LLM_MODEL,
        messages=messages,
        temperature=settings.LLM_TEMPERATURE if temperature is None else temperature,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
