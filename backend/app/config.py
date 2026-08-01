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

    # ===== 认证配置 =====
    # JWT 签名密钥：留空则自动生成并持久化到 data/.jwt_secret（生产建议显式配置）
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    # 是否播种/展示演示账户（alice/bob/... 密码 pass123）：仅本地演示用，上线必须设为 false
    SEED_DEMO_ACCOUNTS: bool = os.getenv("SEED_DEMO_ACCOUNTS", "true").strip().lower() in ("1", "true", "yes")

    # ===== 代码沙箱配置 =====
    # 判题执行模式：
    #   docker = 强制 Docker 容器隔离（生产必须）；daemon/镜像未就绪时判题明确不可用，绝不退回本地执行
    #   local  = 本地子进程（仅开发调试用，无网络/文件系统硬隔离）
    #   auto   = 自动检测：有 Docker 用 Docker，否则本地子进程并打印警告（默认，开发友好）
    SANDBOX_MODE: str = os.getenv("SANDBOX_MODE", "auto")
    JUDGE_IMAGE: str = os.getenv("JUDGE_IMAGE", "interview-judge:latest")
    JUDGE_MEM: str = os.getenv("JUDGE_MEM", "128m")    # 容器内存上限（swap 同值，禁用交换）
    JUDGE_CPUS: str = os.getenv("JUDGE_CPUS", "0.5")   # 容器 CPU 配额

    # ===== 语音（ASR + TTS）配置 =====
    # aliyun=阿里百炼(dashscope) / volcengine=火山引擎(豆包) / mock=本地调试(无声卡正弦波)
    # 留空=自动选择（优先阿里，其次火山，都没配则语音不可用、前端回退浏览器原生语音）
    VOICE_PROVIDER: str = os.getenv("VOICE_PROVIDER", "")

    # 阿里百炼（需 pip install dashscope）
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    ALIYUN_ASR_MODEL: str = os.getenv("ALIYUN_ASR_MODEL", "paraformer-realtime-v2")
    ALIYUN_TTS_MODEL: str = os.getenv("ALIYUN_TTS_MODEL", "cosyvoice-v2")
    ALIYUN_TTS_VOICE: str = os.getenv("ALIYUN_TTS_VOICE", "longxiaochun_v2")

    # 火山引擎（纯 WebSocket，无需额外 SDK）
    VOLC_APP_ID: str = os.getenv("VOLC_APP_ID", "")
    VOLC_ACCESS_TOKEN: str = os.getenv("VOLC_ACCESS_TOKEN", "")
    VOLC_TTS_CLUSTER: str = os.getenv("VOLC_TTS_CLUSTER", "volcano_tts")
    # 豆包语音合成大模型音色（面试官男声示例，可换）
    VOLC_TTS_VOICE: str = os.getenv("VOLC_TTS_VOICE", "zh_male_jingqiangkanye_moon_bigtts")
    VOLC_TTS_RATE: int = int(os.getenv("VOLC_TTS_RATE", "24000"))
    VOLC_ASR_RESOURCE: str = os.getenv("VOLC_ASR_RESOURCE", "volc.bigasr.sauc.duration")
    VOLC_ASR_MODEL: str = os.getenv("VOLC_ASR_MODEL", "bigmodel")

    @property
    def pro_model(self) -> str:
        """Pro 版使用的模型，未单独配置时回退普通模型。"""
        return self.LLM_MODEL_PRO or self.LLM_MODEL

    @property
    def llm_ready(self) -> bool:
        return bool(self.LLM_API_KEY) and "YOUR_API_KEY" not in self.LLM_API_KEY

    @property
    def voice_provider(self) -> str:
        """解析实际使用的语音供应商：显式配置优先，否则按已配密钥自动选择。"""
        p = (self.VOICE_PROVIDER or "").strip().lower()
        if p in ("aliyun", "volcengine", "mock"):
            return p
        if self.DASHSCOPE_API_KEY:
            return "aliyun"
        if self.VOLC_APP_ID and self.VOLC_ACCESS_TOKEN:
            return "volcengine"
        return ""

    @property
    def voice_ready(self) -> bool:
        return bool(self.voice_provider)


settings = Settings()
