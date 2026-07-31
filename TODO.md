# 待做清单（TODO）— AI 模拟面试系统

> 整合自 `ROADMAP.md` 与 `0730复盘.md`，并吸收 2026-07-31 PM 审查结论，作为统一待办入口。
> 最后更新：2026-07-31
>
> **北极星指标**：4 周内「同用户 ≥3 场且平均分提升 ≥10 分」的用户占比。
> **验收节奏**：每完成一个 Phase → git commit → 子 agent（QA + PM）审核 → 记录意见 → 修正 → 下一 Phase。

---

## P0 — 决定"有没有用"（最高优先，必须最先做）✅ 已全部完成

### P0-1 代码沙箱真实判题（客观可信基石）✅
- [x] 后端 `subprocess` 隔离运行用户 Python 代码（超时 / 内存上限 / 禁网）— `runner.py` 已实现并实测：正确 4/4、错误 0/4
- [x] 题库每题补结构化 `tests: [{input, expected}]` — `problems.py` 10 题全覆盖
- [x] 判题结果（通过 x/y、错误输出）注入面试官评价，替代纯 LLM review — `session.stream_code_submission` + `summarize_for_llm`
- [x] 前端展示用例通过情况、错误输出，面试官基于客观结果追问 — `app.js renderJudgeResult`
- [x] **验收**：写错代码 → 判题失败 → 面试官指出 → 修正 → 通过 ✅ 实测通过

### P0-2 报告加「可执行提升计划」✅
- [x] 薄弱点 → 具体练习建议（对应题目 / 知识点清单 / 练习路径）— `prompts.py` 报告 prompt 已含「改进建议 + 可执行提升计划」
- [x] 报告维度对齐大厂面评表（编码 / 算法 / 项目深度 / 沟通 / 基础，1–4 档），每条结论引用对话原文证据 ✅

### P0-3 简历解析结果预览 / 可编辑 ✅
- [x] 上传后展示抽取要点（summary + 可追问点清单）— `upload_resume` 返回 parsed，前端 `#resumePreview` 卡片
- [x] 用户可修正后再开始面试，提升后续所有环节质量 — 编辑后 `resume_probe_points` 回传并覆盖解析结果，影响深挖方向 ✅ 实测通过

---

## P1 — 决定"留不留"（留存与闭环）

- [x] **会话持久化与恢复**（M6 遗留）：刷新 / 关页后可继续进行中面试 — 后端 `/api/state` + `_persist_live` 落盘 + `get_session` 磁盘恢复；前端 `checkResumable()`（页面加载即调用）+ `resumeInterview()` 重建对话与代码面板 ✅ 2026-07-31 实测：/api/state 正确返回历史 3 条、阶段、人设、流程
- [x] **成长曲线**：历史页加分数趋势图、维度雷达图、薄弱点画像（跨场聚合）— 趋势图 SVG sparkline + 平均分/最高分/变化、薄弱点标签为既有；**本次新增纯 SVG 雷达图**（零 CDN 依赖，`renderRadar`），node 验证 SVG 合法，`/api/history` 数据契约核对匹配 ✅
- [x] **定向练习模式**：只练代码 / 只练八股 / 只练项目深挖（`mode` 后端已支持）— 前端 `modePills` 四选一 + `selectMode` 切换 + `startInterview` 传 `mode`；后端 `MODE_FLOWS` 支持 full/coding/quiz/project ✅
- [x] **难度与方向选择**：easy / medium / hard + 方向标签（CV / NLP / 推荐 / LLM）— 难度选择（中文 简单/中等/困难 + coding 模式限定抽题）后端 `pick_problem(difficulty)` 本已支持；**本次新增方向选择**：`assign_persona` 支持显式 `direction` 覆盖自动推断，`/api/start` 透传，前端 `dirPills`（自动/CV/NLP/推荐/LLM/机器学习/深度学习/通用）✅ 实测：指定 NLP→命中 NLP 池、自动→通用算法、非法方向→回退 detect
- [x] **错题本**：答错的八股 / 代码题自动入本、可重练 — 代码侧（`mistakes.record_session_results` 判题未过入本、全过消灭）早已完整；**本次补八股侧**：因八股无客观判题，采用「用户自评入本 + 八股定向重练」。后端 `record_quiz_mistake` + `/api/mistakes/quiz`；前端八股环节气泡加「📕 加入错题本」按钮，错题本区分代码/八股两类并各自重练 ✅ 实测：八股入库/列表/按题删除全通过

### P1 补充 — Pro 版切换 & 多用户隔离（2026-07-31）
- [x] **普通 / Pro 随意切换**：头部「普通 / ⭐Pro」开关，localStorage 持久化，影响**新建会话**。Pro 版 = 更强模型（`config.LLM_MODEL_PRO`，未配置则回退普通模型保证开关始终可用）+ 更深点评 system prompt。后端 `Session.tier` + `Session.model` 透传到所有 LLM 调用；前端 `tierToggle` + `currentTier`，`/api/start` 与 `/api/state` 均回传 `tier` ✅ 实测：start/state 正确返回 `tier=pro`、Pro 提示注入生效
- [x] **账户登录（稳定 user_id）**：新增 `accounts.py` 首次启动自动播种 4 个演示账户（alice / bob / carol / dave，密码统一 pass123）至 `data/accounts.json`；`GET /api/accounts` 列出、`POST /api/login` 校验，成功返回稳定 `user_id=账户名`。登录后历史（`/api/history`）/错题（`/api/mistakes`）**按账户隔离，跨浏览器、跨设备、多次打开都能看到自己的数据**；游客模式保留（仅本机本浏览器隔离，回退匿名 `ie_uid`）✅ 实测：登录 / 跨账户隔离正常
- [x] **多用户并行**：会话层本就按 `session_id` 隔离、可并行（已确认）；数据层按 `user_id` 分区（登录账户名或匿名 `ie_uid`），多人开面试互不串数据 ✅
- [x] **多 worker 部署**：`run_server.sh` 启动命令改为 `--workers "${WORKERS:-2}"`（默认 2 进程，`WORKERS=4` 可调大）。配套修复「多 worker 会写坏文件」隐患——`history.json`/`mistakes.json` 原仅进程内锁，已加**跨进程文件锁**（`fcntl.flock`，见 `history._file_lock`，`mistakes.py` 复用）防并发覆盖；并修复**只有开多 worker（或重启）才暴露的 bug**：会话快照 `_SNAP_FIELDS` 漏 `style`，磁盘恢复出的会话缺 `style` 致 `/api/state` 500，已补回并加兜底默认值 ✅ 实测：2 worker 下 12 次跨进程 `/api/state` 调用全部 200

---

## P2 — 打磨（体验与商业化）

- [ ] **前端大改版**（Phase 4）：对标 Final Round AI / HackerRank / 面多多
  - 着陆页（价值主张 + 引导）、面试页（沉浸感 + 时间感）、报告页（可视化）
  - 统一设计系统、暗色模式、移动端适配、微交互（打字指示、阶段转场动效）
- [ ] **报告导出**：PDF / 图片分享
- [ ] **面试官风格开关**（压力面 / 宽松面）：后端 `style=strict|warm|pressure` 已支持，需前端补 UI
- [ ] **付费点设计**：免费 N 场 → 深度报告 / 无限场次 / 定向练习收费（先做本地开关占位）
- [ ] 时间感、引导空状态、（可选）语音 ASR+TTS

---

## 工程 / 部署收尾

- [ ] **固化 uv 到 PATH**：本机 uv 在 `~/.hermes/bin`，建议写入 `~/.zshrc`（`export PATH="$HOME/.hermes/bin:$PATH"`），否则新终端 `uv` 不可用（已建 `run_server.sh` 自动查找，可缓解）
- [ ] **统一启动方式**：`README.md` 里的 `uv run uvicorn` 在本机网络 TLS 阻断下会联网卡死；改用 `.venv/bin/python -m uvicorn app.main:app --port 8000` 或 `./run_server.sh`
- [ ] **M7 遗留**：流式异常时末句偶发缺失（已被锁 + 前端 finally 兜底，影响极小）
- [ ] 补一份"本地运行 / 常见问题"说明（尤其 Mac 跨平台部署注意事项）

---

## 参考：已具备能力（勿重复造轮子）

- 后端四阶段状态机 + memo + 报告、面试官 persona 随机分配、题库（算法 / 八股）
- 会话创建 `/api/start`、流式 `/api/chat`、`/api/opening`、代码提交 `/api/submit_code`、状态恢复 `/api/state`
- 历史记录 `/api/history`、错题本 `/api/mistakes`、静态前端（`app/static/`）
- 安全加固已落地：上传校验（5MB / 扩展名 / PDF 魔数）、并发锁、原子写、XSS（DOMPurify）、LLM 超时重试
