"""全局配置：从 .env 读取 LLM 与服务参数。"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    PORT: int = int(os.getenv("PORT", "8000"))

    @property
    def llm_ready(self) -> bool:
        return bool(self.LLM_API_KEY) and "YOUR_API_KEY" not in self.LLM_API_KEY


settings = Settings()
