"""面试会话状态机与内存存储。

核心职责：
- 维护每个会话的阶段(stage)、对话历史、面试官记忆(memo)、当前算法题等。
- 根据阶段构造对应的 system prompt。
- 解析 LLM 回复中的「可见内容 / 面试笔记 / 控制信号」三段。
- 驱动阶段转移（含 next_stage 时的转场与出题）。
"""
import uuid
import json
import time
import re
import threading
from typing import Iterator, Optional

from . import llm, prompts, personas, parser, runner, mistakes
from . import db as _db  # 进行中会话的快照存于 SQLite live_sessions 表：重启后可恢复
from . import history as history_store
from .config import settings
from .problems import pick_problem
from .questions import pick_questions
from .schemas import Stage, STAGE_LABELS, STAGE_ORDER

# 面试模式 → 阶段流程（定向练习只走对应环节 + 报告）
MODE_FLOWS = {
    "full": STAGE_ORDER,
    # 非技术岗完整面试：自我介绍 → 经历深挖 → 专业问答 → 报告（无代码环节）
    "no_code": [Stage.GREETING, Stage.PROJECT, Stage.QUIZ, Stage.REPORT],
    "coding": [Stage.CODING, Stage.REPORT],
    "quiz": [Stage.QUIZ, Stage.REPORT],
    "project": [Stage.PROJECT, Stage.REPORT],
}
MODE_LABELS = {"full": "完整面试", "no_code": "非技术岗面试", "coding": "只练手撕代码",
               "quiz": "只练专业八股", "project": "只练项目深挖"}

# 每个环节允许的最大「候选人发言轮次」，超过则强制推进，避免面试卡死
STAGE_TURN_LIMITS = {
    Stage.GREETING: 8,
    Stage.PROJECT: 14,
    Stage.CODING: 10,
    Stage.QUIZ: 12,
}


class Session:
    def __init__(self, resume_text: str, jd_text: str, mode: str = "full",
                 difficulty: Optional[str] = None, problem_id: Optional[str] = None,
                 style: str = "strict", direction: Optional[str] = None,
                 tier: str = "normal", user_id: Optional[str] = None):
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode if mode in MODE_FLOWS else "full"
        self.difficulty = difficulty if difficulty in {"简单", "中等", "困难"} else None
        self.requested_problem_id = problem_id or None  # 错题重练：指定第一题
        self.style = style if style in prompts.STYLE_INSTR else "strict"
        self.tier = "pro" if tier == "pro" else "normal"   # 普通 / Pro
        self.user_id = user_id or None                      # 多用户隔离：历史与错题按此分区
        self.jd = parser.parse_jd(jd_text)
        self.resume = parser.parse_resume(resume_text)
        self.direction = direction or None  # 用户显式指定的方向，覆盖自动推断
        self.persona = personas.assign_persona(jd_text + " " + self.jd.get("full_text", ""), direction)
        self.stage: Stage = self.stage_flow[0]
        self.history: list[dict] = []          # [{role, content}] 仅可见对话
        self.memo: list[str] = []              # 面试官隐藏笔记
        self.used_problem_ids: list[str] = []
        self.current_problem: Optional[dict] = None
        self.quiz_pool: list[str] = pick_questions(self.persona["direction"], n=8)
        self.coding_phase = "present"          # present -> interact
        self.progress: list[dict] = []         # 各阶段完成记录
        # 进度兜底与历史/评分相关
        self.stage_turns = 0                     # 当前环节候选人发言轮次计数
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.score: Optional[int] = None        # 面试总分 0-100
        self.verdict: Optional[str] = None      # 通过 / 待定 / 不通过
        self.report_text = ""                    # 完整报告 markdown
        self.abandoned = False
        self.judge_results: list[dict] = []      # 每次代码提交的自动判题结果（供报告引用）
        self.dimensions: dict = {}               # 报告分项评分（成长画像用）
        self._saved = False                      # 历史是否已落盘
        self._lock = threading.Lock()            # 串行化同一会话的并发请求，防止状态竞争
        self._report_done = False                # 报告是否已生成（防重入）

    @property
    def stage_flow(self) -> list:
        return MODE_FLOWS.get(self.mode, STAGE_ORDER)

    @property
    def model(self) -> str:
        """本会话使用的 LLM 模型：Pro 用更强模型，未配置则回退普通模型。"""
        return settings.pro_model if self.tier == "pro" else settings.LLM_MODEL

    def _pick(self) -> dict:
        """按难度/指定题抽题（指定题只生效一次）。"""
        p = pick_problem(self.used_problem_ids, self.difficulty, self.requested_problem_id)
        self.requested_problem_id = None
        return p

    # ---------- system prompt 构造 ----------
    def _system_prompt(self) -> str:
        if self.stage == Stage.GREETING:
            return prompts.greeting_prompt(self.persona, self.resume, self.jd)
        if self.stage == Stage.PROJECT:
            return prompts.project_prompt(
                self.persona, self.resume, self.jd, self.resume.get("probe_points", "")
            )
        if self.stage == Stage.CODING:
            if self.current_problem is None:
                self.current_problem = self._pick()
                self.used_problem_ids.append(self.current_problem["id"])
            return prompts.coding_prompt(self.persona, self.current_problem, self.coding_phase)
        if self.stage == Stage.QUIZ:
            qs = "\n".join(f"- {q}" for q in self.quiz_pool)
            return prompts.quiz_prompt(self.persona, self.jd, qs)
        return ""

    def _build_messages(self, user_message: Optional[str]) -> list[dict]:
        msgs = [{"role": "system", "content": self._system_prompt()}]
        # 不可覆盖的安全护栏，常驻于每轮对话
        msgs.append({"role": "system", "content": prompts.GUARDRAIL})
        # 面试官风格：常驻于每轮对话，影响语气与压迫感
        msgs.append({"role": "system", "content": prompts.style_line(self.style)})
        if self.memo:
            msgs.append({
                "role": "system",
                "content": "【你之前的面试笔记，供参考】\n" + "\n".join(self.memo[-12:]),
            })
        # Pro 模式：要求更深入、更挑剔的技术点评与更具体的改进建议
        if self.tier == "pro":
            msgs.append({
                "role": "system",
                "content": "【Pro 模式】请进行更深入、更严格的专业点评：指出候选人回答中"
                           "更隐蔽的逻辑漏洞与边界问题，给出更具体的改进路径与进阶学习方向，"
                           "并在结论中给出更细颗粒度的能力评估。",
            })
        msgs.extend(self.history[-16:])
        if user_message is not None:
            msgs.append({"role": "user", "content": user_message})
        return msgs

    # ---------- 回复解析 ----------
    @staticmethod
    def _parse_reply(full: str) -> tuple[str, str, dict]:
        visible, memo, control = full, "", {}
        if prompts.SEP_MEMO in full:
            visible, rest = full.split(prompts.SEP_MEMO, 1)
            if prompts.SEP_CONTROL in rest:
                memo, ctrl_raw = rest.split(prompts.SEP_CONTROL, 1)
            else:
                memo, ctrl_raw = rest, ""
            memo = memo.strip()
            control = Session._safe_json(ctrl_raw)
        elif prompts.SEP_CONTROL in full:
            visible, ctrl_raw = full.split(prompts.SEP_CONTROL, 1)
            control = Session._safe_json(ctrl_raw)
        return visible.strip(), memo, control

    @staticmethod
    def _safe_json(raw: str) -> dict:
        raw = raw.strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1:
            try:
                return json.loads(raw[s:e + 1])
            except Exception:
                return {}
        return {}

    # ---------- 阶段转移 ----------
    def _advance_stage(self):
        flow = self.stage_flow
        idx = flow.index(self.stage) if self.stage in flow else len(flow) - 1
        self.progress.append({"stage": self.stage.value, "label": STAGE_LABELS[self.stage]})
        self.stage_turns = 0
        if idx + 1 < len(flow):
            self.stage = flow[idx + 1]
        else:
            self.stage = Stage.FINISHED
        if self.stage == Stage.CODING:
            self.coding_phase = "present"

    # ---------- 公共流式：产出可见 token，返回(可见全文, memo, control) ----------
    def _stream_llm(self, user_message: Optional[str], cancel=None):
        """生成器：yield 可见 token 事件；结束时通过 StopIteration.value 返回解析结果。

        cancel：可选的可调用对象（如 threading.Event.is_set），返回 True 时在
        token 间隙协作式中断生成（语音模式打断用）；已产出的部分内容仍会被解析保留。
        """
        messages = self._build_messages(user_message)
        full = ""
        sent = 0
        found_sep = False
        cancelled = False
        keep = len(prompts.SEP_MEMO) + 2  # 尾部保护，避免分隔符被截断误发
        for delta in llm.chat_stream(messages, model=self.model):
            if cancel is not None and cancel():
                cancelled = True
                break
            full += delta
            if not found_sep:
                cut = full.find(prompts.SEP_MEMO)
                if cut == -1:
                    safe_to = max(sent, len(full) - keep)
                    if safe_to > sent:
                        yield {"type": "token", "text": full[sent:safe_to]}
                        sent = safe_to
                else:
                    if cut > sent:
                        yield {"type": "token", "text": full[sent:cut]}
                    sent = cut
                    found_sep = True
                    # 不 break：继续消费后续 token，确保 ###MEMO### 之后的
                    # memo 与 ###CONTROL### 控制信号被完整读入 full，避免 next_stage 丢失
            # found_sep 后只累加 full，不再向用户发送可见 token
        # 流式正常结束时，补发尾部保护预留的最后一段可见内容；
        # 仅当未遇到分隔符时才补发，避免把隐藏的 memo/control 暴露给用户。
        if not cancelled and not found_sep and sent < len(full):
            yield {"type": "token", "text": full[sent:]}
        visible, memo, control = self._parse_reply(full)
        return visible, memo, control, cancelled

    # ---------- 对外：流式对话 ----------
    def stream_reply(self, user_message: Optional[str], cancel=None) -> Iterator[dict]:
        """产出事件字典：token / stage / problem / report_start / done。

        全程持有 self._lock，串行化同一会话的并发请求，防止 history/stage 竞争。
        cancel：可选可调用对象，返回 True 时在 token 间隙协作式中断（语音打断用）。
        """
        with self._lock:
            yield from self._stream_reply_locked(user_message, cancel=cancel)

    def stream_code_submission(self, code: str, language: str, cancel=None) -> Iterator[dict]:
        """提交代码：先沙箱自动判题（客观），再把代码+判题结果交给面试官评价。"""
        with self._lock:
            judge_result = None
            if self.stage == Stage.CODING and self.current_problem:
                judge_result = runner.judge(self.current_problem, code, language)
                yield {"type": "judge", "result": judge_result}
            msg = f"这是我写的代码（{language}）：\n```{language}\n{code}\n```"
            if judge_result is not None:
                summary = runner.summarize_for_llm(judge_result)
                self.judge_results.append({
                    "problem": self.current_problem["title"],
                    "problem_id": self.current_problem["id"],
                    "tags": self.current_problem.get("tags", []),
                    "difficulty": self.current_problem.get("difficulty", ""),
                    "supported": judge_result.get("supported", False),
                    "passed": judge_result.get("passed"),
                    "total": judge_result.get("total"),
                    "summary": summary,
                })
                self.memo.append(
                    f"[算法手撕·自动判题] 《{self.current_problem['title']}》 {summary}"
                )
                msg += (
                    "\n\n【系统自动判题结果（沙箱真实运行，客观事实，候选人界面上同样可见）】\n"
                    + summary
                )
            yield from self._stream_reply_locked(msg, cancel=cancel)

    def _stream_reply_locked(self, user_message: Optional[str], cancel=None) -> Iterator[dict]:
        if user_message:
            self.history.append({"role": "user", "content": user_message})
            prompt_input = None  # 已写入 history
            # 计数当前环节的候选人发言轮次（用于进度兜底）
            if self.stage in STAGE_TURN_LIMITS:
                self.stage_turns += 1
        else:
            # 定向练习「只练代码」：开场即出题，不走寒暄
            if self.stage == Stage.CODING and self.current_problem is None:
                yield from self._auto_present_problem(cancel=cancel)
                self._persist_live()
                yield {"type": "done"}
                return
            prompt_input = "（面试开始，请你作为面试官开场）"  # 开场触发

        gen = self._stream_llm(prompt_input, cancel=cancel)
        visible, memo, control, cancelled = yield from gen

        self.history.append({"role": "assistant", "content": visible})
        if memo:
            self.memo.append(f"[{STAGE_LABELS[self.stage]}] {memo}")
        if cancelled:
            # 语音打断：只保留已生成的部分回复，不做阶段推进（control 可能不完整），
            # 并告知面试官「上一条被候选人打断了」，下轮回复更自然。
            self.memo.append("[系统]上一条回复被候选人中途打断，未说完；请自然衔接候选人的新发言。")
            self._persist_live()
            yield {"type": "done"}
            return

        # 阶段推进：每轮最多推进一个阶段，避免模型一次回复发多个 next_stage
        # 把中间环节整个跳过（每个阶段都应有一次真实交互）；同时支持轮次兜底强制推进。
        advanced_this_turn = False
        while True:
            want_next = (control or {}).get("action", "continue") == "next_stage"
            if not want_next and self.stage in STAGE_TURN_LIMITS:
                # 轮次兜底：当前阶段候选人发言次数达到上限则强制推进
                if self.stage_turns >= STAGE_TURN_LIMITS[self.stage]:
                    want_next = True
            if not want_next or self.stage == Stage.FINISHED:
                break
            # 每个阶段只允许推进一次，下一次推进留待下一轮对话
            self._advance_stage()
            yield {"type": "stage", "stage": self.stage.value,
                   "label": STAGE_LABELS.get(self.stage, "")}
            advanced_this_turn = True
            if self.stage == Stage.CODING:
                yield from self._auto_present_problem(cancel=cancel)
            elif self.stage == Stage.REPORT:
                yield from self._generate_report(cancel=cancel)
            # 只推进一次：跳出循环，剩余 next_stage 留待后续轮次
            break

        self._persist_live()  # 每轮结束落盘，服务重启后可恢复
        yield {"type": "done"}

    def _auto_present_problem(self, cancel=None) -> Iterator[dict]:
        """进入 coding 环节后自动出题。"""
        self.current_problem = self._pick()
        self.used_problem_ids.append(self.current_problem["id"])
        self.coding_phase = "present"
        gen = self._stream_llm("（请正式把这道算法题出给候选人，题面展示清楚）", cancel=cancel)
        visible, memo, _, _cancelled = yield from gen
        self.history.append({"role": "assistant", "content": visible})
        if memo:
            self.memo.append(f"[算法手撕] {memo}")
        self.coding_phase = "interact"
        p = self.current_problem
        yield {"type": "problem", "problem": {
            "id": p["id"], "title": p["title"], "difficulty": p["difficulty"],
            "tags": p["tags"], "statement": p["statement"],
            "signature": p.get("signature", ""),
            "judgeable": bool(p.get("tests")),
        }}

    def _generate_report(self, cancel=None) -> Iterator[dict]:
        # 防止重复生成报告（兜底强制推进/客户端重连可能再次触发）
        if self._report_done or self.stage == Stage.FINISHED:
            return
        self._report_done = True
        transcript = "\n".join(
            f"{'候选人' if m['role'] == 'user' else '面试官'}：{m['content']}"
            for m in self.history
        )
        if self.mode != "full":
            transcript = (f"（注意：本场为定向练习模式「{MODE_LABELS.get(self.mode, '')}」，"
                          f"只进行了对应环节，未考察的维度请标注「本场未考察」，不要臆测打分）\n"
                          + transcript)
        memo_text = "\n".join(self.memo) or "（无）"
        judge_text = "\n".join(
            f"- 《{j['problem']}》（难度:{j.get('difficulty', '?')}，"
            f"考察:{'、'.join(j.get('tags', []))}）：{j['summary']}"
            for j in self.judge_results
        ) or "（本场无自动判题记录）"
        prompt = prompts.report_prompt(
            self.persona, self.resume, self.jd, transcript, memo_text, judge_text,
            style=self.style, direction=self.persona.get("direction"),
        )
        yield {"type": "report_start"}
        report_text = ""
        for delta in llm.chat_stream([{"role": "user", "content": prompt}], temperature=0.4, model=self.model):
            if cancel is not None and cancel():
                break  # 语音打断：报告已被打断，不保存半成品（下轮可重新触发生成）
            report_text += delta
            yield {"type": "token", "text": delta, "channel": "report"}
        if cancel is not None and cancel():
            self._report_done = False  # 允许后续轮次重新生成完整报告
            return
        self.report_text = report_text
        self.score, self.verdict = self._parse_score(report_text)
        self.dimensions = self._parse_dimensions(report_text)
        self.stage = Stage.FINISHED
        self.finished_at = time.time()
        self._save_history()
        self._delete_live()  # 已完成：不再需要恢复快照
        # 错题本：未全通过的题记入，全通过的自动消灭
        try:
            mistakes.record_session_results(self.judge_results, user_id=self.user_id)
        except Exception:
            pass
        yield {"type": "stage", "stage": self.stage.value, "label": STAGE_LABELS[self.stage]}

    # ---------- 评分解析与历史落盘 ----------
    @staticmethod
    def _parse_score(text: str):
        total = None
        verdict = None
        # 兼容多种写法：总分:85、总分 85/100、得分85分、综合评分 85、85/100
        m = re.search(r"(?:总分|得分|综合评分)[：:]?\s*(\d{1,3})(?:\s*/\s*100)?", text)
        if not m:
            m = re.search(r"(?:评分|成绩|得分)\D{0,4}(\d{1,3})\s*分", text)
        if not m:
            m = re.search(r"\b(\d{1,3})\s*/\s*100\b", text)
        if m:
            try:
                total = int(m.group(1))
                if total > 100:
                    total = None
            except ValueError:
                total = None
        v = re.search(r"推荐结论[：:]?\s*(通过|待定|不通过)", text)
        if not v:
            # 仅在报告开头 250 字内兜底搜索，避免命中正文里「通过该项目…」等误报
            head = text[:250]
            v = re.search(r"(?:是否通过|结论|判定)[^：:]*[：:]?\s*(通过|待定|不通过)", head)
        if v:
            verdict = v.group(1)
        return total, verdict

    @staticmethod
    def _parse_dimensions(text: str) -> dict:
        """从报告「分项评分」中解析各维度分数（x/5），用于跨场成长画像。"""
        dims = {}
        for m in re.finditer(r"\*\*([^*\n]{2,24})\*\*[：:]\s*(\d(?:\.\d)?)\s*/\s*5", text):
            name = m.group(1).strip()
            try:
                dims[name] = float(m.group(2))
            except ValueError:
                continue
        return dims

    # ---------- 对外：会话恢复所需的公开状态 ----------
    def public_history(self) -> list[dict]:
        """对候选人可见的对话历史（隐去注入给 LLM 的判题附言）。"""
        out = []
        for m in self.history:
            c = m["content"]
            if m["role"] == "user":
                c = c.split("\n\n【系统自动判题结果", 1)[0]
            out.append({"role": m["role"], "content": c})
        return out

    def _build_history_record(self) -> dict:
        transcript = "\n".join(
            f"{'候选人' if m['role'] == 'user' else '面试官'}：{m['content']}"
            for m in self.history
        )
        return {
            "id": self.id,
            "started_at": self.started_at,
            "finished_at": self.finished_at or time.time(),
            "persona_name": self.persona.get("name"),
            "persona_title": self.persona.get("title"),
            "jd_title": self.jd.get("title"),
            "score": self.score,
            "verdict": self.verdict,
            "abandoned": self.abandoned,
            "dimensions": self.dimensions,
            "judge_passed": sum(j.get("passed") or 0 for j in self.judge_results),
            "judge_total": sum(j.get("total") or 0 for j in self.judge_results),
            "weak_tags": sorted(
                {t for j in self.judge_results
                 if (j.get("total") or 0) > (j.get("passed") or 0)
                 for t in j.get("tags", [])}
                | {n.split("（")[0].strip() for n, s in self.dimensions.items() if s < 3.0}
            ),
            "report": self.report_text,
            "transcript": transcript,
            "user_id": self.user_id,
            "tier": self.tier,
        }

    def _save_history(self, abandoned: bool = False):
        """落盘到历史记录（已完成只存一次；放弃时无论是否已存都覆盖记录）。"""
        if self._saved and not abandoned:
            return
        rec = self._build_history_record()
        rec["abandoned"] = abandoned
        history_store.save_record(rec)
        self._saved = True

    def abandon(self):
        """面试中途放弃：标记为未完成并保存到历史（仅当尚未生成报告）。"""
        if self.stage == Stage.FINISHED:
            return
        self.abandoned = True
        self._save_history(abandoned=True)
        # 中途放弃也要把做错的算法题写入错题本，否则错题本核心闭环在放弃场景失效
        try:
            mistakes.record_session_results(self.judge_results, user_id=self.user_id)
        except Exception:
            pass
        self._delete_live()

    # ---------- 进行中会话的快照持久化（服务重启后可恢复） ----------
    _SNAP_FIELDS = [
        "id", "jd", "resume", "persona", "history", "memo", "used_problem_ids",
        "current_problem", "quiz_pool", "coding_phase", "progress", "stage_turns",
        "started_at", "finished_at", "score", "verdict", "report_text",
        "abandoned", "judge_results", "dimensions", "mode", "difficulty",
        "requested_problem_id", "direction", "tier", "user_id", "style",
    ]

    def snapshot(self) -> dict:
        data = {k: getattr(self, k) for k in self._SNAP_FIELDS}
        data["stage"] = self.stage.value
        return data

    @classmethod
    def from_snapshot(cls, data: dict) -> "Session":
        s = cls.__new__(cls)
        for k in cls._SNAP_FIELDS:
            setattr(s, k, data.get(k))
        if s.mode not in MODE_FLOWS:  # 兼容旧快照
            s.mode = "full"
        s.stage = Stage(data["stage"])
        s.tier = data.get("tier") or "normal"
        s.user_id = data.get("user_id") or None
        s.style = data.get("style") or "strict"
        s._saved = False
        s._lock = threading.Lock()
        s._report_done = False
        return s

    def _persist_live(self):
        """落盘进行中会话快照（INSERT OR REPLACE）；已结束的不落。失败不影响主流程。"""
        if self.stage == Stage.FINISHED:
            return
        try:
            _db.execute(
                "INSERT OR REPLACE INTO live_sessions (session_id, user_id, snapshot, updated_at) VALUES (?,?,?,?)",
                (self.id, self.user_id or "", json.dumps(self.snapshot(), ensure_ascii=False), time.time()))
        except Exception:
            pass

    def _delete_live(self):
        try:
            _db.execute("DELETE FROM live_sessions WHERE session_id = ?", (self.id,))
        except Exception:
            pass


# ---------- 内存会话存储（磁盘快照兜底，重启后可恢复） ----------
_SESSIONS: dict[str, Session] = {}


def create_session(resume_text: str, jd_text: str, mode: str = "full",
                   difficulty: Optional[str] = None,
                   problem_id: Optional[str] = None,
                   style: str = "strict", direction: Optional[str] = None,
                   tier: str = "normal", user_id: Optional[str] = None) -> Session:
    s = Session(resume_text, jd_text, mode=mode, difficulty=difficulty, problem_id=problem_id, style=style, direction=direction, tier=tier, user_id=user_id)
    _SESSIONS[s.id] = s
    s._persist_live()
    return s


def get_session(session_id: str) -> Optional[Session]:
    s = _SESSIONS.get(session_id)
    if s is not None:
        return s
    # 内存没有（如服务重启过）：尝试从 SQLite 快照恢复
    if not session_id or not re.fullmatch(r"[0-9a-f]{12}", session_id):
        return None
    row = _db.query_one("SELECT snapshot FROM live_sessions WHERE session_id = ?", (session_id,))
    if not row:
        return None
    try:
        data = json.loads(row["snapshot"])
        s = Session.from_snapshot(data)
        _SESSIONS[s.id] = s
        return s
    except (ValueError, KeyError):
        return None
