from app.session import Session
print("parse1", Session._parse_score("总分：82/100 推荐结论：通过"))
print("parse2", Session._parse_score("没有分数"))
from app import prompts
print("guardrail_ok", "GUARDRAIL" in prompts.GUARDRAIL)
print("score_in_report", "总分" in prompts.report_prompt({}, {}, {}, "", ""))
print("STAGE_LIMITS", Session.STAGE_TURN_LIMITS if hasattr(Session, "STAGE_TURN_LIMITS") else "MISSING")
