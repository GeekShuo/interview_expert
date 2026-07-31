# AI 模拟面试系统 — 规划路线（算法岗实习/校招方向）

> 定位：用户上传简历 + 目标岗位 JD，系统分配领域专家面试官，进行不限轮次的全真模拟面试，
> 覆盖「自我介绍 → 项目深挖 → LeetCode 手撕 → 八股」四大环节，结束后输出面试评估报告。

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────┐
│  前端 Next.js/React                                  │
│  ├─ 简历/JD 上传页                                    │
│  ├─ 面试对话界面（聊天流 + 环节进度条）                  │
│  ├─ Monaco Editor 代码面板（算法环节）                  │
│  └─ 面试报告页                                        │
└──────────────────────┬──────────────────────────────┘
                       │ REST / SSE(流式)
┌──────────────────────┴──────────────────────────────┐
│  后端 FastAPI (Python, uv 管理)                       │
│  ├─ 简历/JD 解析服务（PDF → 结构化 JSON）              │
│  ├─ 面试编排引擎（LangGraph 状态机）                    │
│  │   ├─ Stage 0: 打招呼 + 自我介绍（3min）             │
│  │   ├─ Stage 1: 项目/经历深挖（追问引擎）              │
│  │   ├─ Stage 2: LeetCode 算法题                      │
│  │   ├─ Stage 3: 八股文考察                           │
│  │   └─ Stage 4: 收尾 + 报告生成                      │
│  ├─ 追问引擎（可追问点清单 + 追问链 + "答不上来"判定）    │
│  ├─ 题库服务（算法题 + 八股题，按方向标签检索）           │
│  └─ 会话/上下文管理（面试官 memo + 滚动摘要）            │
└──────────────────────┬──────────────────────────────┘
                       │
        LLM API（DeepSeek / GPT-4o / Claude）
        存储：SQLite(MVP) → PostgreSQL；简历文件 → 本地/OSS
```

---

## 二、阶段拆解

### Phase 0：调研与骨架（~3 天）
- [ ] 体验竞品：面多多、牛面、HackerRank Mock、InterviewMode，记录体验差距点
- [ ] 阅读参考源码：`references/Interviewer`（架构）、`references/AI-Interview`（代码评估思路）
- [ ] 初始化 monorepo：`backend/`（FastAPI + uv）、`frontend/`（Next.js）
- [ ] 打通 LLM API 流式对话链路

**设计要点**
- 从第一天就用 SSE 流式输出，面试对话延迟感直接决定体验。
- LLM 供应商做成可配置（env 切换），追问质量对模型敏感，需 A/B 对比。

### Phase 1：简历/JD 解析 + 面试官分配（~4 天）
- [ ] PDF/Word 简历解析（pdfplumber 兜底 + LLM 结构化抽取）
- [ ] 抽取 schema：教育背景 / 项目经历 / 实习经历 / 论文竞赛 / 技能栈
- [ ] JD 解析：方向标签（CV/NLP/推荐/大模型/风控…）、技能要求、级别（实习/校招）
- [ ] 面试官 persona 工厂：按方向标签生成专家人设（背景、口吻、考察侧重）

**设计要点**
- 简历抽取时同步生成「**可追问点清单**」（probe points）：每段经历标注可深挖的技术决策、
  指标、数据规模、疑似夸大点。这是后续追问引擎的弹药库，比事后临时想问题质量高得多。
- 解析失败要有降级：允许用户粘贴纯文本简历。
- persona 不只是名字头衔，要包含「该方向面试官的典型追问习惯」（如推荐方向必问 AUC/线上收益）。

### Phase 2：面试流程状态机（~5 天）
- [ ] LangGraph 实现 5 阶段 FSM，每阶段有进入条件、退出条件、超时/跳转规则
- [ ] Stage 0 打招呼：欢迎语 → 请自我介绍（提示 3 分钟）→ 记录要点入 memo
- [ ] 阶段转场话术（"好的，接下来我们做一道算法题"）
- [ ] 会话持久化：随时断线可恢复；「不限长度交互」→ 滚动摘要 + 面试官 memo 机制

**设计要点**
- **不要用单一大 prompt 撑全场**。每个 Stage 独立 system prompt + 共享面试官 memo，
  memo 记录：候选人表现要点、已问问题、已暴露的薄弱点。
- 状态转移条件显式化：如「项目深挖 ≥ N 轮 或 覆盖 M 个 probe point 后进入算法环节」。
- 上下文策略：完整保留当前 Stage 对话，历史 Stage 只保留摘要 + memo。

### Phase 3：项目深挖追问引擎（~7 天，核心壁垒）
- [ ] 追问链模板：挑战是什么 → 为什么选方案 X 不选 Y → 指标怎么定义/怎么算 →
      数据规模/训练细节 → 遇到的坑 → 你的个人贡献占比
- [ ] 「答不上来」判定器：识别含糊其辞 / 绕开问题 / 直接认不会 → 记录后跳下一 probe point
- [ ] 真实性一致性检测：前后矛盾、细节深度不足时标记（仅用于报告，不当场揭穿）
- [ ] 表达逻辑评估：每轮回答按 STAR 完整度、逻辑清晰度打分入 memo

**设计要点**
- 追问深度控制：单个点最多追 3 层，避免变成压力面劝退用户。
- 判定「答不上来」要宽容：给一次引导提示（"没关系，换个角度…"）再跳转。
- 每轮追问由「策略节点」决策（继续追 / 换点 / 换环节），而不是让对话模型自由发挥。

### Phase 4：LeetCode 算法环节（~6 天）
- [ ] 前端 Monaco Editor + 语言选择（Python/C++/Java）
- [ ] 自建题库 50~100 题（easy/medium/hard，带标签、参考题解、复杂度分析）
- [ ] 出题策略：按 JD 级别 + 简历强弱选题（实习 medium 为主）
- [ ] 代码评估 MVP：LLM review（正确性 / 边界 / 复杂度）；二期接 Judge0 沙箱真实判题
- [ ] 讲解环节：让用户口述思路 → 追问优化时间/空间复杂度 → 追问变体题

**设计要点**
- 面试官行为要真实：用户卡住 ≥ X 分钟主动给 hint；写完先让讲思路再评价。
- 题目原文、参考解不进对话上下文的用户可见部分，只给评估节点用，防止泄答案。
- 复杂度追问是必选动作：「能否优化到 O(n)？」「空间能否 O(1)？」

### Phase 5：八股环节 + 报告（~5 天）
- [ ] 八股题库 200+ 题：ML 基础 / DL / 方向专题（CV/NLP/RecSys/LLM）/ 数据结构 / 概率统计
- [ ] 抽题策略：JD 方向为主 + 简历技能栈随机抽查，答错自动追一道同知识点
- [ ] 面试报告：各环节评分（专业能力/表达逻辑/代码能力/知识广度）+ 逐项证据引用 +
      薄弱点清单 + 改进建议 + 逐题复盘
- [ ] 报告页可导出 PDF

**设计要点**
- 报告的每个结论必须引用对话原文作为证据，否则用户不信服。
- 评分维度对齐真实大厂面评表（如：编码 / 算法 / 项目深度 / 沟通 / 基础知识，1-4 档）。

### Phase 6：打磨与增强（持续）
- [ ] 语音交互（ASR + TTS + 打断处理）
- [ ] 多轮面试模拟（一面/二面/HR 面）
- [ ] 压力面 / 宽松面风格开关
- [ ] Judge0 沙箱判题 + 用例集
- [ ] 用户历史面试对比、成长曲线

---

## 三、关键技术选型

| 项 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.12 + FastAPI + uv | 生态、异步流式、uv 管理依赖 |
| 编排 | LangGraph（或自写 FSM） | 显式状态机，阶段可控可测 |
| 前端 | Next.js + Monaco Editor | 代码面板成熟方案 |
| LLM | DeepSeek-V3 / GPT-4o 可切换 | 追问质量需强模型，成本敏感场景用 DeepSeek |
| 存储 | SQLite → PostgreSQL | MVP 从简 |
| 判题 | LLM review → Judge0 | 先低成本验证体验 |

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| 追问质量差、像客服 | probe points 前置生成 + 策略节点决策 + 强模型 |
| 长对话上下文爆炸 | Stage 摘要 + 面试官 memo，历史环节不进全文 |
| "真实性检测"过度承诺 | 措辞改为"深度与一致性检验"，只入报告不当场质疑 |
| 题目答案泄漏 | 参考解仅评估节点可见 |
| 同质化竞争 | 深耕算法岗垂直题库与追问链，报告做出证据感 |

## 五、参考项目（本地克隆于 references/）

| 项目 | 技术栈 | 参考点 |
|---|---|---|
| IliaLarchenko/Interviewer | Python + Gradio | 整体架构、LLM/STT/TTS 三模型解耦、coding 面试实现 |
| xgwangdl/AI-Interview | Java Spring Alibaba AI | AST + LLM 双引擎代码评估 |
| jennifer88huang/interview-skills | — | JD+简历生成个性化面试题的 prompt 设计 |

## 六、本地运行与在线体验

### 本地已配置（uv 管理）
- `references/Interviewer`：已用 uv 建好 `.venv`，双击 `start.bat` 或运行 `run_bg.bat` 启动，
  访问 http://127.0.0.1:7860 。
  - 注意：`.env` 中的 `OPENAI_API_KEY` 为占位符，需替换为真实 key（或改为 DeepSeek 等
    OpenAI 兼容接口的 URL/模型名）后功能才可用。
  - Windows 适配记录：`webrtcvad` → `webrtcvad-wheels`（免编译）；`httpx==0.27.2`、
    `pydantic==2.9.2`、`gradio==4.44.1`、`jinja2==3.1.4`、`fastapi==0.112.4`（版本兼容修复）。
- `references/interview-skills`：纯文档/Prompt 仓库，无需启动，直接阅读。
- `references/AI-Interview`：Java Spring 项目（不适用 uv），且远端默认分支损坏，克隆内容为空，
  如需研究可手动 `git clone -b master https://github.com/xgwangdl/AI-Interview.git`。

### 在线体验 URL（无需本地启动）
| 产品/项目 | URL | 说明 |
|---|---|---|
| Interviewer 官方 Demo (HF Space) | https://huggingface.co/spaces/IliaLarchenko/interviewer | 本地这套的官方在线版 |
| Final Round AI | https://www.finalroundai.com | 含 LeetCode 面试助手，10M+ 用户 |
| HackerRank AI Mock Interview | https://www.hackerrank.com/interview/preparation-kits | coding round 模拟标杆 |
| InterviewMode | https://interview-mode.com | 自适应 AI coding 面试，免费可试 |
| interviewing.io | https://interviewing.io | 真人 mock，学流程设计 |
| Pramp | https://www.pramp.com | 免费同伴互面 |
| 面多多 | https://www.mianduoduo.com.cn | 国内，简历押题+沉浸式模拟，定位最接近 |
| 牛面（牛客） | https://www.nowcoder.com/interview/ai | 程序员专项 AI 模拟面试 |
| AI面试官 | https://www.viewself.cn | 可追问的连贯模拟面试 |

---

## 当前进度（截至 2026-07-31）

> 部署：已验证可在 macOS（Apple Silicon）通过 uv 部署运行；启动用 `.venv/bin/python -m uvicorn app.main:app`（避开 `uv run` 在本机 TLS 阻断下联网卡死），或 `./run_server.sh`。
> 待做清单见 `TODO.md`（唯一入口）。

### P0 全部完成 ✅
- **P0-1 沙箱真实判题**：`runner.py` 沙箱 + `problems.py` 10 题测试用例，实测正确 4/4、错误 0/4，结果注入面试官评价、前端渲染判题卡片。
- **P0-2 报告可执行提升计划**：`prompts.py` 报告 prompt 已含「改进建议 + 可执行提升计划（知识点 / 推荐题目 / 7 天冲刺）」。
- **P0-3 简历解析预览 / 可编辑**：上传即结构化解析，`#resumePreview` 展示 summary + 可追问点，用户可编辑后回传覆盖，影响深挖方向。

### P1 全部完成 ✅
- **P1-1 会话持久化与恢复**：后端 `/api/state` + `_persist_live` 落盘 + `get_session` 磁盘恢复；前端 `checkResumable()` + `resumeInterview()` 重建对话与代码面板。实测 /api/state 正确恢复历史对话。
- **P1-2 成长曲线**：趋势 sparkline + 平均分/最高分/变化 + 薄弱点标签；**本次新增纯 SVG 雷达图**（零 CDN 依赖）。
- **P1-3 定向练习模式**：`modePills` 四选一 + 后端 `MODE_FLOWS` 全支持。
- **P1-4 难度与方向选择**：难度（简单/中等/困难）后端抽题本已支持；**本次新增方向选择** `assign_persona(direction)` 覆盖自动推断 + 前端 `dirPills`（自动/CV/NLP/推荐/LLM/机器学习/深度学习/通用），实测生效。
- **P1-5 错题本**：代码侧早已支持；**本次补八股侧**（`record_quiz_mistake` + `/api/mistakes/quiz` + 八股气泡「加入错题本」按钮 + 错题本区分两类并重练）。
- **P1 补充 · 普通/Pro 切换**：头部「普通 / ⭐Pro」开关（localStorage 持久化）。Pro = 更强模型（`LLM_MODEL_PRO`，未配置回退普通模型）+ 更深点评 prompt；`/api/start` 与 `/api/state` 回传 `tier`。实测 `tier=pro` 往返与 Pro 提示注入均生效。
- **P1 补充 · 多用户隔离**：会话本就按 `session_id` 隔离并行；成长曲线与错题本现已按 `user_id` 分区（`/api/history?user_id=`、`/api/mistakes?user_id=`），多人开面试互不串数据。实测 u_A/u_B 完全隔离。
- **P1 补充 · 账户登录（稳定 user_id）**：新增 `accounts.py` 首次启动播种 4 个演示账户（alice/bob/carol/dave，密码 pass123）至 `data/accounts.json`；`GET /api/accounts` 列表 + `POST /api/login` 校验，登录后以**账户名**作稳定 `user_id`，历史/错题**跨浏览器、跨设备、多次打开都能看到自己的数据**；游客模式保留（回退匿名 `ie_uid`）。前端登录弹层支持一键登录/账号密码/游客。
- **P1 补充 · 多 worker 部署**：`run_server.sh` 默认 `--workers 2`（可 `WORKERS=N` 调大）；`history.json`/`mistakes.json` 加**跨进程文件锁**（`fcntl.flock`，`history._file_lock`）防多进程并发写覆盖；并修复跨 worker/重启恢复会话缺 `style` 的 500（快照 `_SNAP_FIELDS` 补 `style` + 兜底默认）。实测 2 worker 下 12 次跨进程 `/api/state` 全部 200、登录隔离正常。

> 💡 运行：`./run_server.sh`（默认 2 worker）。想让 Pro 用更强模型：在 `backend/.env` 增加 `LLM_MODEL_PRO=你的强模型名`，不配则自动回退普通模型，开关始终可用。Windows 不支持 `fcntl`，请保持默认 `WORKERS=1`。

> ⚠️ 部署注意：会话状态已落盘到 `data/live_sessions/`，任意 worker 都能恢复，开多 worker 不会串会话；但服务进程无法热重启，改完代码需 `pkill -f uvicorn; cd backend && ./run_server.sh` 后刷新。
