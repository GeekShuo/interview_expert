深度思考
已通读根目录五份文档（docs/ 目录本身是空的，实际文档在项目根：0730复盘.md、TODO.md、ROADMAP.md、网页版上线计划.md、小程序技术方案.md）并全面排查了代码。以下按类别列出，只列问题与原因，不做修改。

一、安全问题（最紧急，线上正在裸奔）
真实 API 密钥明文落盘 — backend/.env 里是在用的 DeepSeek key 和 DashScope key 明文；backend/scripts/server_setup.sh:104 还指导 scp backend/.env 明文传服务器。原因：密钥一旦随截图/误提交/服务器被入侵泄露，LLM 额度会被刷爆，建议轮换。
Pro 档位越权（业务漏洞） — main.py:196 /api/start 直接信任客户端传来的 tier 字段，从不查 users.tier。任何人（含游客）传 tier=pro 就免费用 Pro 模型。原因：这是你未来唯一的付费点，现在形同虚设。
演示账户默认开启 + 密码硬编码 pass123 — config.py:21 SEED_DEMO_ACCOUNTS 默认 true；app.js:480 一键登录写死密码；/api/accounts 无鉴权公开账户列表。服务曾通过 cpolar 暴露公网。原因：线上忘关 = 任何人一键登录任意演示账户。
全系统零限流 — /api/login 可无限爆破密码；/api/chat、/api/start 无调用上限；/api/anon_token 无限签发。原因：公网环境下 LLM 费用可被脚本一夜刷穿，这是 HTTP 明文期最容易被忽视的实际损失。
游客 token 可冒名 — main.py:150 接受客户端自报 uid 就签发 token，uid 又是 Math.random() 生成的短字符串。原因：知道/猜到别人 uid 即可读其历史与错题。
WS token 走 URL query — main.py:375，uvicorn access log 会记录完整 URL。原因：30 天有效的 JWT 落进日志文件，日志即密钥库。
沙箱 local 回退模式 — runner.py 在 SANDBOX_MODE=auto（默认）下无 Docker 就在宿主机裸跑用户代码，无网络/资源限制。原因：哪台机器忘配 Docker，就是 RCE 敞口；应默认熔断而非回退。
JWT 30 天有效且不可吊销 — 改密码后旧 token 仍有效，无黑名单/refresh 机制。
二、可靠性与运维不足
异常静默、几乎无日志 — main.py:251,266,281 SSE 异常捕获后不打日志；session.py 多处 except: pass。原因：线上出问题时你没有任何排查线索，只能靠用户截图。
明文密码遗留文件 — backend/data/accounts.json 和 data_backup_phase1/accounts.json（含明文密码）迁移完成后未删。
CDN 依赖无 SRI、无本地化 — index.html 四个 CDN（tailwind/marked/monaco/dompurify）无 integrity 哈希；且 DOMPurify 加载失败时 safeMd 退化为未消毒输出。原因：jsdelivr 被挟持或抽风，XSS 防护和功能同时失效；网页版上线计划.md Phase 4 已规划本地化但未执行。
输入无长度上限 — resume_text、聊天 message 无大小限制，且 prompts.py:92 把简历全文塞进每一轮 system prompt。原因：单次请求可烧大量 token，费用与延迟双风险。
SQLite 全局锁串行化所有读写 — db.py 读操作也走同一把 _LOCK；快照整体 INSERT OR REPLACE 有写放大。原因：当前用户量无感，但它决定了你"2核4G 能撑多少并发"的真实上限比预期低。
测试基本为零 — 无 tests/ 目录、无 pytest、无 CI；唯一的 backend/_test_p3.py 已腐化必失败（调用了已删除的 _write_all、不带 token 必 401）。原因：Phase 1/2/3 当时的"21/21 通过"都是一次性手测脚本，现在没有回归保障，每次改代码都在盲飞。
无安全响应头（CSP/X-Frame-Options 等）、无审计日志、无监控告警（/api/health 没接任何告警，挂了只能等用户发现）。
三、工程与仓库卫生
根目录散落杂物 — _tmp_up.txt（测试残件）、chanpin（无扩展名的 AI 提示词残件）、app_out.log/server.log、cpolar_start.bat（含个人路径，且一启动就把"演示账户+零限流"系统暴露公网）。
backend/scripts/_*.sh 五个一次性修复脚本未跟踪 — 其中 _fix_sudoers.sh 授予 xqer 全权限 NOPASSWD sudo，绝不能入库；部署已完成应归档或删除。
docs/ 下两个空目录、.codebuddy/ 未被 gitignore 覆盖、backend/*.log 7 个调试日志残留。
遗留数据双轨 — data/live_sessions/*.json 43 个 + history/mistakes.json 旧文件与 SQLite 并存。原因：你 TODO 里自己写了"稳定一周后可清"，现在就是该清的时候，不清永远不知道哪份是真数据。
前端 app.js 1537 行单文件 — 所有逻辑（登录/SSE/判题/报告/错题/语音）揉在一起。原因：这正是 小程序技术方案.md 里说的"借 uni-app 重构清理"的理由，但即使不做小程序，继续在这个文件上加功能（P2 的前端大改版）会越来越痛苦。
四、产品层面（对照 TODO 的 P2，仍未做）
前端大改版（Phase 4）未启动 — 无着陆页、无暗色模式、移动端未适配。原因：文档里定位"决定留不留"，当前界面是工具原型感，与面多多等竞品有肉眼差距。
报告导出 PDF/分享图未做 — exportPDF 用的是过时的 document.write。原因：报告分享是 小程序技术方案.md 定的核心裂变点（"我拿了 82 分你敢来试试吗"），网页版先行做掉可直接复用。
面试官风格开关后端已支持、前端无 UI（style=strict|warm|pressure 白做了）。
付费点零占位 — 免费 N 场→深度报告收费的开关连占位都没有，且上面第 2 条说明 tier 体系本身漏的。
北极星指标无埋点 — TODO 定的是"4 周内 ≥3 场且平均分提升 ≥10 分的用户占比"，但代码里没有任何统计/事件记录支撑这个指标的度量。
五、值得深挖的方向（投入产出比高的点）
追问引擎是核心壁垒，但仍是"单 prompt"实现 — ROADMAP Phase 3 设计的「追问策略节点 + 答不上来判定器 + 真实性一致性检测」并未真正实现，目前追问质量完全押注在 DeepSeek 的自由发挥上。原因：这是你与"套壳聊天"产品的分水岭，文档自己标注"核心壁垒（~7 天）"。
判题结果→面试评价的联动深度 — P0-1 已把判题注入评价，但「卡住给 hint、讲完思路再评价、复杂度变体追问」这些 ROADMAP Phase 4 设计的真实面试官行为还没做，做好了是体验上最容易被用户感知的差异点。
语音链路双供应商未实调校准 — 阿里百炼/火山引擎报文只过了 mock E2E，真实密钥首调未做；且语音依赖 HTTPS（等备案）。原因：语音面试是移动端最能打的场景，也是小程序版的差异化武器，值得提前用 cpolar+HTTPS 隧道先行实测。
游客数据认领 — 网页版上线计划.md 风险 2 提到"游客→注册后历史合并"一期没做。原因：这是转化漏斗的关键一环——游客练出 3 场历史后才愿意注册，注册后数据没了会直接流失。
题库规模 — 算法 10 题、八股约百题，对标自建 50~100 题算法 + 200+ 八股的目标差距明显；题库质量（标签、变体、知识点关联错题本）是"定向练习"和"提升计划"的弹药基础。
优先级建议（如果之后要动手）：第一梯队 = ①②③④（密钥轮换、tier 越权、演示账户开关、限流），都是小改动堵大洞；第二梯队 = ⑨⑭⑰（日志、测试基线、危险脚本处置）；第三梯队 = ㉖㉗（产品壁垒深挖）+ P2 前端改版。