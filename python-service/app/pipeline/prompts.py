"""提示词模板：素材解析（understand）阶段。

约束目标：
- 产出结构化 JSON：core_message / tone / audience / facts[]
- facts 必须能逐条在素材中找到原文依据（防幻觉的第一道闸）
- 语言保持与素材一致（默认中文）
"""

import json


def _profile_dict(profile: dict) -> dict:
    """规范化创作者画像字段（FR-32）：去除空白键/None，统一字段名。"""
    if not profile:
        return {}
    return {
        k: (v if isinstance(v, str) else "")
        for k, v in profile.items()
        if k in {"brand_voice", "domain", "audience", "avoid"}
    }

UNDERSTAND_SYSTEM = (
    "你是一名资深自媒体内容分析师。你的任务是从用户提供的素材中提炼结构化信息，"
    "只允许输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown。\n"
    "JSON 结构：\n"
    "{\n"
    '  "core_message": "核心观点/卖点，一句话概括（不超过80字）",\n'
    '  "tone": "语气基调，例如：理性干货/真诚种草/犀利观点/温暖故事",\n'
    '  "audience": "目标受众画像，例如：职场新人/宝妈/数码爱好者/自由职业者",\n'
    '  "facts": [{"text": "事实原文摘录", "type": "data|story|opinion"}]\n'
    "}\n"
    "facts 规则：\n"
    "1. type 三选一：data=客观数据/事实；story=经历/故事；opinion=观点/感受。\n"
    "2. facts[].text 必须逐字或近乎逐字来自素材原文，禁止改写、禁止添加素材中不存在的信息。\n"
    "3. 抽取 3~10 条最关键的事实，覆盖数据、经历与观点三类。\n"
    "4. 若素材没有数据类内容，则不要编造 data 类型。\n"
)


def build_understand_prompt(raw_text: str, source_kind: str, title: str | None) -> str:
    parts = []
    if title:
        parts.append(f"标题：{title}")
    if source_kind and source_kind != "general":
        parts.append(f"素材类型：{source_kind}")
    parts.append("素材全文：")
    parts.append(raw_text)
    return "\n".join(parts)


# ============================================================ 平台策略(brief)
BRIEF_SYSTEM = (
    "你是资深跨平台内容运营。根据给定【平台DNA】与【素材理解】，"
    "为把素材改写成该平台内容制定一份策略。只输出一个 JSON 对象：\n"
    "{\n"
    '  "angle": "内容切入角度(一句话)",\n'
    '  "hooks": ["钩子1(标题/开头可用)", "钩子2"],\n'
    '  "structure_plan": "正文结构规划(步骤化)",\n'
    '  "tag_direction": ["话题标签方向1", "方向2"],\n'
    '  "rationale": "为什么这样改写，引用平台DNA的关键点"\n'
    "}\n"
    "要求：策略必须贴合平台调性与爆款机制；钩子要有冲突/悬念/数字/共鸣力。"
)


def build_brief_prompt(dna: dict, structured, tone_override: str | None,
                       template: dict | None = None,
                       creator_profile: dict | None = None,
                       retrospect_hints: list[str] | None = None) -> str:
    payload = {
        "platform": {"code": dna["code"], "name": dna["name"]},
        "platform_dna": {
            "style": dna.get("style", []),
            "structure_template": dna.get("structure_template", []),
            "title_rules": dna.get("title_rules", []),
            "tags": dna.get("tags", {}),
            "limits": dna.get("limits", {}),
            "viral_logic": dna.get("viral_logic", []),
            "hooks": dna.get("hooks", []),
        },
        "material": {
            "core_message": structured.core_message,
            "tone": tone_override or structured.tone,
            "audience": structured.audience,
            "facts": [f.text for f in structured.facts],
        },
    }
    if template:
        payload["user_template"] = template
    if creator_profile:
        payload["creator_profile"] = _profile_dict(creator_profile)
    if retrospect_hints:
        payload["retrospect_hints"] = list(retrospect_hints)
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ============================================================ 成稿(draft)
DRAFT_SYSTEM = (
    "你是某平台的资深博主，正在把一份素材改写为该平台的原创内容。\n"
    "硬性要求：\n"
    "1. 严格遵守【平台DNA】：风格、结构模板、标题规则、标签规则、字数上限。\n"
    "2. 事实红线：只能使用【素材事实清单】中出现的信息；禁止编造数字、数据、头衔、经历细节。\n"
    "3. 不是翻译/搬运，而是按平台调性重写：改语气、改结构、改标题、补互动钩子。\n"
    "只输出一个 JSON 对象：\n"
    "{\n"
    '  "titles": ["主标题", "备选2", "备选3"],\n'
    '  "body": "正文(含平台化排版:分段/换行/emoji适度)",\n'
    '  "tags": ["标签1", "标签2"],\n'
    '  "cover_suggestion": "封面文案或首屏钩子",\n'
    '  "interaction_line": "文末互动引导语",\n'
    '  "facts_used": [1, 3],\n'
    '  "rationale": "为什么这样写(对应哪些爆款机制)"\n'
    "}\n"
    "\n"
    "关于 facts_used（**必填**）：正文里的每一个数字、年份、专有名词、以及每一条"
    "事实性断言，都必须来自【素材事实清单】里某一条。请把你**实际用到**的条目序号列出来。"
    "用不到任何一条就写 []。\n"
    "**写不出序号的内容，就是你不该写的内容** —— 删掉它，不要写进正文。\n"
)


def build_draft_prompt(dna: dict, structured, brief, feedback: str | None = None,
                       template: dict | None = None,
                       creator_profile: dict | None = None,
                       retrospect_hints: list[str] | None = None) -> str:
    payload = {
        "platform": {"code": dna["code"], "name": dna["name"]},
        "platform_dna": {
            "content_forms": dna.get("content_forms", []),
            "style": dna.get("style", []),
            "structure_template": dna.get("structure_template", []),
            "title_rules": dna.get("title_rules", []),
            "tags": dna.get("tags", {}),
            "limits": dna.get("limits", {}),
            "viral_logic": dna.get("viral_logic", []),
        },
        "brief": {
            "angle": brief.angle,
            "hooks": brief.hooks,
            "structure_plan": brief.structure_plan,
            "tag_direction": brief.tag_direction,
        },
        # 带**序号**：模型要靠它填 facts_used（1-based，与 facts_used 的约定一致）
        "material_facts": [{"n": i, "text": f.text}
                           for i, f in enumerate(structured.facts, 1)],
    }
    if template:
        payload["user_template"] = template
    if creator_profile:
        payload["creator_profile"] = _profile_dict(creator_profile)
    if retrospect_hints:
        payload["retrospect_hints"] = list(retrospect_hints)
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        prompt += f"\n\n【上一轮 QA 未通过，请针对性修改后重写】\n{feedback}"
    return prompt


# ============================================================ 质量门(qa)
QA_SYSTEM = (
    "你是平台内容质检编辑。审查给定成稿，只输出一个 JSON 对象：\n"
    "{\n"
    '  "passed": true/false,\n'
    '  "issues": ["必须修复的问题(未通过时必填)", "..."],\n'
    '  "warnings": ["非阻断提醒", "..."]\n'
    "}\n"
    "\n"
    "【第一优先，也是最容易判错的一条】\n"
    "成稿里有【facts_used】—— 它声明了本次依据了哪几条事实。请逐句判断：\n"
    "**有没有哪一句超出了它声明依据的那些事实？**\n"
    "判定要点：\n"
    "  · 把「素材原文」当唯一事实来源；它比事实清单更全，以它为准。\n"
    "  · 典型越界：把两件事连成一个新断言（素材说\"要交286块\"，成稿写\"漏交了286块\"）；\n"
    "    给素材里的行为加上素材没有的动机/程度/时间跨度（凭空写\"半年数据\"）；\n"
    "    凭空加检查清单、适用人群结论这类素材未提及的内容。\n"
    "  · 命中则 passed=false，且 issues 里要**指出是哪一句**、**属于哪一类**"
    "（数字 / 年份 / 专名 / 新增断言）。\n"
    "  · 正常的改写、换语气、换结构、补互动钩子**不算越界** —— 不要因为这些判失败。\n"
    "\n"
    "【其余检查】2) 是否贴合平台风格与结构模板；3) 标题是否有吸引力、是否超长；"
    "4) 字数与标签是否在上限内。"
)


def build_qa_prompt(dna: dict, draft, structured, source_text: str = "") -> str:
    """质检的输入。

    ⚠️ 两处是 2026-10-09 加的，都是为了让"判定证据不少于被检对象"：
    · `source_text`：**素材原文**。早先只给 3~10 条事实清单 —— 证据比素材还窄，
      判定自然不可靠（`docs/spec_factguard.md` §1）。
    · `facts_used`：成稿自己声明的依据（在 `draft` 里），质检据此判断"有没有超出声明"。
    """
    payload = {
        "platform": {"code": dna["code"], "name": dna["name"]},
        "platform_dna": {
            "style": dna.get("style", []),
            "title_rules": dna.get("title_rules", []),
            "limits": dna.get("limits", {}),
            "viral_logic": dna.get("viral_logic", []),
        },
        "source_text": source_text or "",
        "facts": [f.text for f in structured.facts],
        "draft": draft.model_dump(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ============================================================ 剪辑单(clip_sheet, FR-50)
CLIP_SYSTEM = (
    "你是短视频剪辑指导。根据成稿与平台DNA，把成稿拆成分镜级的剪辑执行单，"
    "供博主照单剪辑。只输出一个 JSON 对象：\n"
    "{\n"
    '  "intro_note": "剪辑要点一句话",\n'
    '  "scenes": [\n'
    '    {"seq":1, "duration_hint":"8s", "script":"本段口播", "visual":"画面建议",'
    ' "subtitle":"字幕断句", "sound":"BGM/音效备注"}\n'
    "  ],\n"
    '  "bgm_hint": "BGM风格建议"\n'
    "}\n"
    "要求：scenes 覆盖全文且顺序连贯；script 只使用成稿内容；"
    "visual 仅给拍摄/素材建议，不要建议生成素材中没有的内容。"
)


def build_clip_prompt(dna: dict, draft, structured, template: dict | None = None) -> str:
    payload = {
        "platform": {"code": dna["code"], "name": dna["name"]},
        "platform_dna": {
            "content_forms": dna.get("content_forms", []),
            "style": dna.get("style", []),
            "viral_logic": dna.get("viral_logic", []),
        },
        "facts": [f.text for f in structured.facts],
        "draft_body": draft.body,
        "draft_rationale": draft.rationale,
    }
    if template:
        payload["user_template"] = template
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ============================================================ 示例学习(learn, FR-63)
LEARN_SYSTEM = (
    "你是内容结构分析师。分析用户提供的示例文本，拆解出可复用的写作模板参数，"
    "供该用户日后复用同一套写作套路。只输出一个 JSON 对象：\n"
    "{\n"
    '  "voice": "语气/人设（如：真诚分享 / 理性干货 / 犀利观点）",\n'
    '  "opening": "开头句式（归纳其手法，<=40字）",\n'
    '  "structure": ["正文结构要点1", "要点2"],\n'
    '  "closing": "结尾/互动句式（<=40字）",\n'
    '  "tag_style": "标签风格描述（无则空字符串）",\n'
    '  "taboo": [],\n'
    '  "rationale": "拆解依据（一句话说明为何这样归纳）"\n'
    "}\n"
    "硬性要求：\n"
    "1. 只归纳『可复用的结构套路』，禁止复述示例中的具体内容、数字、人名、事件；\n"
    "2. structure 用短语概括（每条 <=15 字，3~6 条，按出现顺序），不要抄整句；\n"
    "3. 若某项无法判断，给空字符串或空数组，不要编造。"
)


def build_learn_prompt(sample_text: str, source_note: str | None = None) -> str:
    payload = {
        "task": "拆解示例文本的写作结构，产出可复用的模板参数",
        "sample_text": sample_text,
    }
    if source_note:
        payload["source_note"] = source_note
    return json.dumps(payload, ensure_ascii=False, indent=1)

