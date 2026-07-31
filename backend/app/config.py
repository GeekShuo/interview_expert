"""全局配置：从 .env 读取 LLM 与服务参数。"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    # Pro 版专用模型：不配置则回退普通模型（保证 Pro 开关始终可用）
    LLM_MODEL_PRO: str = os.getenv("LLM_MODEL_PRO", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    PORT: int = int(os.getenv("PORT", "8000"))

    @property
    def pro_model(self) -> str:
        """Pro 版使用的模型，未单独配置时回退普通模型。"""
        return self.LLM_MODEL_PRO or self.LLM_MODEL

    @property
    def llm_ready(self) -> bool:
        return bool(self.LLM_API_KEY) and "YOUR_API_KEY" not in self.LLM_API_KEY


settings = Settings()
