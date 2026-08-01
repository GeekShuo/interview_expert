"""语音模块：ASR/TTS Provider 工厂 + 语音面试管道。"""
from typing import Optional

from ..config import settings
from .base import VoiceProvider


def get_voice_provider() -> Optional[VoiceProvider]:
    """按配置实例化语音供应商；未配置返回 None（前端回退浏览器原生语音）。"""
    name = settings.voice_provider
    if name == "aliyun":
        from .aliyun import AliyunProvider
        return AliyunProvider()
    if name == "volcengine":
        from .volcengine import VolcProvider
        return VolcProvider()
    if name == "mock":
        from .mock import MockProvider
        return MockProvider()
    return None


def voice_config_payload() -> dict:
    """/api/voice/config 的响应：告知前端云端语音是否可用及音频参数。"""
    p = get_voice_provider()
    if p is None:
        return {
            "available": False,
            "provider": "",
            "tts_sample_rate": 0,
            "asr_sample_rate": 16000,
            "hint": "未配置语音供应商：在 backend/.env 填 DASHSCOPE_API_KEY（阿里）或 "
                    "VOLC_APP_ID + VOLC_ACCESS_TOKEN（火山），语音模式将使用浏览器原生语音兜底。",
        }
    return {
        "available": True,
        "provider": p.name,
        "tts_sample_rate": p.tts_sample_rate,
        "asr_sample_rate": 16000,
        "hint": "",
    }
