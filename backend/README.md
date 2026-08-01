# AI 模拟面试系统（算法岗实习/校招）

上传简历 + 目标岗位 JD，系统随机分配一位领域专家面试官，进行全真四环节模拟面试：
**自我介绍 → 项目深挖 → 算法手撕（Monaco 代码框）→ 专业八股 → 面试评估报告**。

## 特性
- 中文原生，界面现代（深色主题 + 环节进度条 + 流式打字）。
- 面试官随机分配、算法题/八股题随机抽取，贴近真实面试的不确定性。
- 面试官只追问不给答案，隐藏笔记（memo）记录疑点，结束生成有证据的评估报告。
- 后端 FastAPI + SSE 流式；LLM 走 OpenAI 兼容接口（可配 DeepSeek / OpenAI）。

## 快速开始（uv）

```bash
cd backend
copy .env.example .env        # Windows；然后编辑 .env 填入 LLM_API_KEY
uv sync                       # 创建虚拟环境并安装依赖
uv run uvicorn app.main:app --reload --port 8000
```

浏览器打开 http://127.0.0.1:8000

## 配置 LLM
编辑 `.env`：
```
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的key
```
也可换成 OpenAI：`LLM_BASE_URL=https://api.openai.com/v1`、`LLM_MODEL=gpt-4o`。

## 配置云端语音（可选，ASR + TTS 全双工语音面试）
不配置时，语音模式自动用浏览器原生语音识别/朗读兜底。配置后体验升级为：
流式识别 + 拟人面试官音色 + 可随时开口打断（barge-in）。

二选一（详见 `.env.example` 注释）：
- **阿里百炼**：填 `DASHSCOPE_API_KEY`，并安装 SDK `uv pip install dashscope`
  （Fun-ASR-Realtime 流式识别 + CosyVoice 合成）。
- **火山引擎**：填 `VOLC_APP_ID` + `VOLC_ACCESS_TOKEN`（豆包语音，纯 WebSocket 无需 SDK）。

`VOICE_PROVIDER` 留空按密钥自动选择；本地联调可设 `VOICE_PROVIDER=mock`
（正弦波 TTS + 文字指令旁路，无需任何密钥）。

## 目录结构
```
backend/
  app/
    main.py       FastAPI 路由 + SSE + 语音 WS
    session.py    面试状态机（四环节 + memo + 报告，支持语音打断取消）
    voice/        云端语音层（Provider 抽象 + 阿里/火山/mock + 全双工管道）
    prompts.py    中文面试 prompts
    personas.py   面试官人设库（按 JD 方向随机分配）
    problems.py   算法题库
    questions.py  八股题库
    parser.py     简历/JD 解析
    llm.py        LLM 客户端
    static/       前端（index.html / app.js / voice2.js / pcm-worklet.js / style.css）
```
