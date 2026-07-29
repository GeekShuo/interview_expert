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

from . import llm, prompts, personas, parser
from . import history as history_store
from .problems import pick_problem
from .questions import pick_questions
from .schemas import Stage, STAGE_LABELS, STAGE_ORDER

# 每个环节允许的最大「候选人发言轮次」，超过则强制推进，避免面试卡死
STAGE_TURN_LIMITS = {
    Stage.GREETING: 8,
    Stage.PROJECT: 14,
    Stage.CODING: 10,
    Stage.QUIZ: 12,
}


class Session:
    def __init__(self, resume_text: str, jd_text: str):
        self.id = uuid.uuid4().hex[:12]
        self.jd = parser.parse_jd(jd_text)
        self.resume = parser.parse_resume(resume_text)
        self.persona = personas.assign_persona(jd_text + " " + self.jd.get("full_text", ""))
        self.stage: Stage = Stage.GREETING
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
        self._saved = False                      # 历史是否已落盘
        self._lock = threading.Lock()            # 串行化同一会话的并发请求，防止状态竞争
        self._report_done = False                # 报告是否已生成（防重入）

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
                self.current_problem = pick_problem(self.used_problem_ids)
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
        if self.memo:
            msgs.append({
                "role": "system",
                "content": "【你之前的面试笔记，供参考】\n" + "\n".join(self.memo[-12:]),
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
        idx = STAGE_ORDER.index(self.stage)
        self.progress.append({"stage": self.stage.value, "label": STAGE_LABELS[self.stage]})
        self.stage_turns = 0
        if idx + 1 < len(STAGE_ORDER):
            self.stage = STAGE_ORDER[idx + 1]
        else:
            self.stage = Stage.FINISHED
        if self.stage == Stage.CODING:
            self.coding_phase = "present"

    # ---------- 公共流式：产出可见 token，返回(可见全文, memo, control) ----------
    def _stream_llm(self, user_message: Optional[str]):
        """生成器：yield 可见 token 事件；结束时通过 StopIteration.value 返回解析结果。"""
        messages = self._build_messages(user_message)
        full = ""
        sent = 0
        found_sep = False
        keep = len(prompts.SEP_MEMO) + 2  # 尾部保护，避免分隔符被截断误发
        for delta in llm.chat_stream(messages):
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
        if not found_sep and sent < len(full):
            yield {"type": "token", "text": full[sent:]}
        return self._parse_reply(full)

    # ---------- 对外：流式对话 ----------
    def stream_reply(self, user_message: Optional[str]) -> Iterator[dict]:
        """产出事件字典：token / stage / problem / report_start / done。

        全程持有 self._lock，串行化同一会话的并发请求，防止 history/stage 竞争。
        """
        with self._lock:
            yield from self._stream_reply_locked(user_message)

    def _stream_reply_locked(self, user_message: Optional[str]) -> Iterator[dict]:
        if user_message:
            self.history.append({"role": "user", "content": user_message})
            prompt_input = None  # 已写入 history
            # 计数当前环节的候选人发言轮次（用于进度兜底）
            if self.stage in STAGE_TURN_LIMITS:
                self.stage_turns += 1
        else:
            prompt_input = "（面试开始，请你作为面试官开场）"  # 开场触发

        gen = self._stream_llm(prompt_input)
        visible, memo, control = yield from gen

        self.history.append({"role": "assistant", "content": visible})
        if memo:
            self.memo.append(f"[{STAGE_LABELS[self.stage]}] {memo}")

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
                yield from self._auto_present_problem()
            elif self.stage == Stage.REPORT:
                yield from self._generate_report()
            # 只推进一次：跳出循环，剩余 next_stage 留待后续轮次
            break

        yield {"type": "done"}

    def _auto_present_problem(self) -> Iterator[dict]:
        """进入 coding 环节后自动出题。"""
        self.current_problem = pick_problem(self.used_problem_ids)
        self.used_problem_ids.append(self.current_problem["id"])
        self.coding_phase = "present"
        gen = self._stream_llm("（请正式把这道算法题出给候选人，题面展示清楚）")
        visible, memo, _ = yield from gen
        self.history.append({"role": "assistant", "content": visible})
        if memo:
            self.memo.append(f"[算法手撕] {memo}")
        self.coding_phase = "interact"
        p = self.current_problem
        yield {"type": "problem", "problem": {
            "id": p["id"], "title": p["title"], "difficulty": p["difficulty"],
            "tags": p["tags"], "statement": p["statement"],
        }}

    def _generate_report(self) -> Iterator[dict]:
        # 防止重复生成报告（兜底强制推进/客户端重连可能再次触发）
        if self._report_done or self.stage == Stage.FINISHED:
            return
        self._report_done = True
        transcript = "\n".join(
            f"{'候选人' if m['role'] == 'user' else '面试官'}：{m['content']}"
            for m in self.history
        )
        memo_text = "\n".join(self.memo) or "（无）"
        prompt = prompts.report_prompt(
            self.persona, self.resume, self.jd, transcript, memo_text
        )
        yield {"type": "report_start"}
        report_text = ""
        for delta in llm.chat_stream([{"role": "user", "content": prompt}], temperature=0.4):
            report_text += delta
            yield {"type": "token", "text": delta, "channel": "report"}
        self.report_text = report_text
        self.score, self.verdict = self._parse_score(report_text)
        self.stage = Stage.FINISHED
        self.finished_at = time.time()
        self._save_history()
        yield {"type": "stage", "stage": self.stage.value, "label": STAGE_LABELS[self.stage]}

    # ---------- 评分解析与历史落盘 ----------
    @staticmethod
    def _parse_score(text: str):
        total = None
        verdict = None
        m = re.search(r"总分[：:]\s*(\d{1,3})\s*/\s*100", text)
        if m:
            try:
                total = int(m.group(1))
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
            "report": self.report_text,
            "transcript": transcript,
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


# ---------- 内存会话存储 ----------
_SESSIONS: dict[str, Session] = {}


def create_session(resume_text: str, jd_text: str) -> Session:
    s = Session(resume_text, jd_text)
    _SESSIONS[s.id] = s
    return s


def get_session(session_id: str) -> Optional[Session]:
    return _SESSIONS.get(session_id)
