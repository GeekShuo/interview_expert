"""面试会话状态机与内存存储。

核心职责：
- 维护每个会话的阶段(stage)、对话历史、面试官记忆(memo)、当前算法题等。
- 根据阶段构造对应的 system prompt。
- 解析 LLM 回复中的「可见内容 / 面试笔记 / 控制信号」三段。
- 驱动阶段转移（含 next_stage 时的转场与出题）。
"""
import uuid
import json
from typing import Iterator, Optional

from . import llm, prompts, personas, parser
from .problems import pick_problem
from .questions import pick_questions
from .schemas import Stage, STAGE_LABELS, STAGE_ORDER


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
                break
        # 流式正常结束时，补发尾部保护预留的最后一段可见内容；
        # 仅当未遇到分隔符时才补发，避免把隐藏的 memo/control 暴露给用户。
        if not found_sep and sent < len(full):
            yield {"type": "token", "text": full[sent:]}
        return self._parse_reply(full)

    # ---------- 对外：流式对话 ----------
    def stream_reply(self, user_message: Optional[str]) -> Iterator[dict]:
        """产出事件字典：token / stage / problem / report_start / done。"""
        if user_message:
            self.history.append({"role": "user", "content": user_message})
            prompt_input = None  # 已写入 history
        else:
            prompt_input = "（面试开始，请你作为面试官开场）"  # 开场触发

        gen = self._stream_llm(prompt_input)
        visible, memo, control = yield from gen

        self.history.append({"role": "assistant", "content": visible})
        if memo:
            self.memo.append(f"[{STAGE_LABELS[self.stage]}] {memo}")

        # 阶段推进
        action = (control or {}).get("action", "continue")
        if action == "next_stage" and self.stage != Stage.FINISHED:
            self._advance_stage()
            yield {"type": "stage", "stage": self.stage.value,
                   "label": STAGE_LABELS.get(self.stage, "")}
            if self.stage == Stage.CODING:
                yield from self._auto_present_problem()
            elif self.stage == Stage.REPORT:
                yield from self._generate_report()

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
        transcript = "\n".join(
            f"{'候选人' if m['role'] == 'user' else '面试官'}：{m['content']}"
            for m in self.history
        )
        memo_text = "\n".join(self.memo) or "（无）"
        prompt = prompts.report_prompt(
            self.persona, self.resume, self.jd, transcript, memo_text
        )
        yield {"type": "report_start"}
        for delta in llm.chat_stream([{"role": "user", "content": prompt}], temperature=0.4):
            yield {"type": "token", "text": delta, "channel": "report"}
        self.stage = Stage.FINISHED
        yield {"type": "stage", "stage": self.stage.value, "label": STAGE_LABELS[self.stage]}


# ---------- 内存会话存储 ----------
_SESSIONS: dict[str, Session] = {}


def create_session(resume_text: str, jd_text: str) -> Session:
    s = Session(resume_text, jd_text)
    _SESSIONS[s.id] = s
    return s


def get_session(session_id: str) -> Optional[Session]:
    return _SESSIONS.get(session_id)
