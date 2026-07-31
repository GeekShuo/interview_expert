"""面试官人设库：根据 JD 方向随机分配一位领域专家。"""
import random

# 各方向的面试官人设池
PERSONAS = {
    "机器学习": [
        {"name": "李衡", "title": "字节跳动 机器学习资深工程师", "domain": "机器学习/推荐系统",
         "style": "节奏快、直接、重 impact，喜欢追问指标和线上收益"},
        {"name": "陈默", "title": "阿里巴巴 算法专家", "domain": "机器学习/风控",
         "style": "刨根问底，喜欢从业务动机切入，考察技术判断力"},
    ],
    "深度学习": [
        {"name": "王沐", "title": "旷视 深度学习研究员", "domain": "深度学习/CV",
         "style": "重原理推导，会追问 loss 设计、训练细节和论文理解"},
        {"name": "赵可", "title": "商汤 高级算法工程师", "domain": "深度学习/CV",
         "style": "关注工程落地与模型部署，喜欢追问数据和 badcase"},
    ],
    "计算机视觉": [
        {"name": "孙屹", "title": "腾讯优图 CV 算法专家", "domain": "计算机视觉",
         "style": "从数据到部署全链路追问，重视 badcase 分析能力"},
    ],
    "自然语言处理": [
        {"name": "周瑜", "title": "百度 NLP 资深工程师", "domain": "自然语言处理/大模型",
         "style": "紧跟大模型前沿，追问 attention、tokenizer、微调细节"},
        {"name": "吴桐", "title": "智谱AI 算法工程师", "domain": "大模型/NLP",
         "style": "考察对 LLM 训练与推理的理解，喜欢开放式追问"},
    ],
    "推荐系统": [
        {"name": "郑楠", "title": "美团 推荐算法专家", "domain": "推荐系统",
         "style": "务实，必问线上 AB 实验、指标提升和特征工程"},
    ],
    "大模型": [
        {"name": "冯睿", "title": "月之暗面 大模型算法工程师", "domain": "大模型",
         "style": "追问 RLHF、长上下文、推理优化，前沿且犀利"},
    ],
    "通用算法": [
        {"name": "何川", "title": "华为 算法工程师", "domain": "算法/数据结构",
         "style": "基础扎实优先，重视代码质量与复杂度分析"},
        {"name": "林深", "title": "拼多多 算法工程师", "domain": "机器学习/算法",
         "style": "高压快节奏，算法题要求高，追问穷追不舍"},
    ],
}

# JD 关键词 -> 方向
DIRECTION_KEYWORDS = {
    "机器学习": ["机器学习", "machine learning", "ml", "特征", "风控", "建模"],
    "深度学习": ["深度学习", "deep learning", "神经网络", "pytorch", "tensorflow"],
    "计算机视觉": ["视觉", "cv", "图像", "检测", "分割", "opencv", "目标检测"],
    "自然语言处理": ["nlp", "自然语言", "文本", "语言模型", "bert", "transformer"],
    "推荐系统": ["推荐", "recommend", "召回", "排序", "ctr", "cvr"],
    "大模型": ["大模型", "llm", "gpt", "rlhf", "sft", "aigc", "生成式"],
}


def detect_direction(jd_text: str) -> str:
    text = (jd_text or "").lower()
    scores = {d: 0 for d in DIRECTION_KEYWORDS}
    for direction, kws in DIRECTION_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                scores[direction] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "通用算法"


def assign_persona(jd_text: str, direction: str = None) -> dict:
    """根据 JD 方向随机分配一位面试官；direction 显式指定时优先使用（覆盖自动推断）。"""
    if direction and direction in PERSONAS:
        pass  # 使用用户指定的方向
    else:
        direction = detect_direction(jd_text)
    pool = PERSONAS.get(direction, PERSONAS["通用算法"])
    persona = dict(random.choice(pool))
    persona["direction"] = direction
    return persona
