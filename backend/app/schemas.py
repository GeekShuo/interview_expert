"""Pydantic 数据模型与面试阶段定义。"""
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel


class Stage(str, Enum):
    """面试阶段状态机。"""
    GREETING = "greeting"            # 打招呼 + 自我介绍（3 分钟）
    PROJECT = "project"              # 项目/经历深挖
    CODING = "coding"                # LeetCode 算法题
    QUIZ = "quiz"                    # 八股文考察
    REPORT = "report"                # 生成面试报告
    FINISHED = "finished"            # 结束


STAGE_LABELS = {
    Stage.GREETING: "自我介绍",
    Stage.PROJECT: "项目深挖",
    Stage.CODING: "算法手撕",
    Stage.QUIZ: "专业八股",
    Stage.REPORT: "面试报告",
    Stage.FINISHED: "已结束",
}

STAGE_ORDER = [Stage.GREETING, Stage.PROJECT, Stage.CODING, Stage.QUIZ, Stage.REPORT]


class StartInterviewRequest(BaseModel):
    resume_text: Optional[str] = None
    jd_text: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class CodeSubmitRequest(BaseModel):
    session_id: str
    code: str
    language: str = "python"


class SessionState(BaseModel):
    session_id: str
    stage: Stage
    stage_label: str
    persona: dict[str, Any]
    resume: dict[str, Any]
    jd: dict[str, Any]
    current_problem: Optional[dict[str, Any]] = None
    progress: list[dict[str, Any]] = []
