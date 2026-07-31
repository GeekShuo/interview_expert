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
    # ---- 非技术岗 ----
    "销售": [
        {"name": "周岩", "title": "阿里本地生活 销售总监", "domain": "销售/商务拓展",
         "style": "目标导向，喜欢追问数字、转化率和大客户成交细节"},
        {"name": "林岚", "title": "美团 区域销售经理", "domain": "销售/渠道管理",
         "style": "实战派，重抗压与谈判，爱问被拒绝后如何翻盘"},
    ],
    "采销": [
        {"name": "赵衡", "title": "京东 采销经理", "domain": "采销/供应链",
         "style": "精明务实，追问成本、毛利、议价和库存周转"},
        {"name": "陈启", "title": "拼多多 品类采销负责人", "domain": "采销/选品",
         "style": "数据驱动，必问选品逻辑、供应商谈判和动销"},
    ],
    "产品经理": [
        {"name": "苏黎", "title": "腾讯 高级产品经理", "domain": "产品经理/C端",
         "style": "重用户洞察与数据，追问需求来源、取舍逻辑和上线效果"},
        {"name": "高远", "title": "字节跳动 产品经理", "domain": "产品经理/增长",
         "style": "犀利，喜欢追问北极星指标、AB 实验和优先级判断"},
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
    # 非技术岗：刻意只用强信号词，避免误伤算法岗 JD（打平时优先判为前面的技术方向）
    "销售": ["销售", "商务拓展", "签单", "地推", "电销", "客户经理", "陌拜"],
    "采销": ["采销", "采购", "选品", "供应商", "议价", "毛利", "进销存", "动销"],
    "产品经理": ["产品经理", "prd", "需求文档", "产品策划", "用户研究", "产品运营"],
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
