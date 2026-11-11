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
    '  "facts": [{"text": "事实原文摘录", "type": "data|story|opinion"}],\n'
    '  "keywords": ["索引关键词1", "索引关键词2"]\n'
    "}\n"
    "keywords 规则：\n"
    "1. 3~8 个，供**以后检索这篇素材**用 —— 不是概括全文，而是别人会用什么词来找它。\n"
    "2. 优先名词/短词（≤8 字），例如：AI写作、内容复用、周报、番茄钟、副业。\n"
    "3. 不要带 # 号，不要标点，不要泛词（内容、方法、技巧这种搜出来等于没搜 ✗）。\n"
    "4. 至少覆盖两个维度：**主题**（讲什么）+ **场景/对象**（谁、什么场合）。\n"
    "5. 宁可少而准，不要凑数。\n"
    "facts 规则：\n"
    "1. type 三选一：data=客观数据/事实；story=经历/故事；opinion=观点/感受。\n"
    "2. facts[].text 必须逐字或近乎逐字来自素材原文，禁止改写、禁止添加素材中不存在的信息。\n"
    "3. 抽取关键事实（短素材 3~10 条，长篇干货 10~25 条），完整覆盖数据、步骤、经历与观点，为多平台差异化展开提供充足依据。\n"
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
            # ⚠️ 这段原先**从没传给任何 prompt**（审计发现）：它讲的是"平台靠什么给流量"
            # —— 比如小红书「搜索分发权重高，标题需埋真实搜索词（这也是标题 20 字内
            # 关键词前置的原因）」。不给模型，"为什么这么写"的根据就丢了。
            "distribution": dna.get("distribution", []),
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


# ============================================================ 成稿(draft · 骨架式, ADR-025)
DRAFT_SECTION_SYSTEM = (
    "你是某平台的资深博主，正在**按已定好的提纲写其中一段**。\n"
    "要求：\n"
    "1. 只写这一段的正文，不要标题、不要解释、不要写别的段。\n"
    "2. **槽位写法**：`{{n:序号|表层}}` 是**格式占位**，不是要你原样输出的字。\n"
    "   例：第 3 条事实是「月入286块」，段里要写 286 时写成 `{{n:3|286块}}` ——\n"
    "   **`序号` 换成该事实的编号、`表层` 换成原文里的那个数值**。\n"
    "   **绝对不要**输出 `{{n:序号|表层}}` 这种带中文标签的写法（那说明你在照抄示例）。\n"
    "   没有事实依据的普通叙述直接写文字，**不要**加槽位。\n"
    "3. 只能使用 material_facts（或 section.fact_texts）里的事实与数字；素材里没有的不许写。\n"
    "4. 遵守 platform_dna 的风格与格式必备项；正文总字数见 section.char_budget，"
    "**这一段只写自己的预算，不要替别的段写**。\n"
    '5. 只输出 {"text": "..."} 这一个 JSON 对象。\n'
)

DRAFT_SYSTEM = (
    "你是某平台的资深博主，正在把一份素材改写为该平台的原创内容。\n"
    "你输出的不是正文，而是**段落级骨架** —— 系统会逐槽校验事实依据、回填、装配正文。\n"
    "硬性要求：\n"
    "1. 严格遵守【平台DNA】：风格、结构模板、标题规则、标签规则、字数上限。\n"
    "1b. **标题字数上限是硬约束**：见 platform_dna.title_chars_max。"
    "每个标题（主标题与备选）都必须 ≤ 该值，**包括标点与数字**。"
    "超了会在平台被截断/折叠，等于白写。宁可少塞一个数字，也不要超。\n"
    "2. **槽位写法**：`{{n:序号|表层}}` 是**格式占位**，不是要你原样输出的字 ✗。\n"
    "   例：第 3 条事实是「月入286块」，段里要写 286 时写成 `{{n:3|286块}}` ——\n"
    "   **`序号` 换成该事实的编号、`表层` 换成原文里的那个数值**。\n"
    "   **绝对不要**输出 `{{n:序号|表层}}` 这种带中文标签的写法（那说明你在照抄示例）。\n"
    "   没有事实依据的普通叙述直接写文字，**不要**加槽位。\n"
    "3. 只输出一个 JSON 对象（骨架）：\n"
    "{\n"
    '  "titles": ["主标题", "备选2", "备选3"],\n'
    '  "paras": [\n'
    '    {"facts": [1, 3], "role": "hook", "text": "段内文本，事实值写槽位 {{n:3|286块}}"}\n'
    "  ],\n"
    '  "tags": ["标签1", "标签2"],\n'
    '  "cover_suggestion": "封面文案或首屏钩子",\n'
    '  "interaction_line": "文末互动引导语",\n'
    '  "rationale": "为什么这样写(对应哪些爆款机制)"\n'
    "}\n"
    "3. 段落与事实声明：paras 至少 1 段，数组顺序即正文顺序；每段 facts = 该段全部"
    "事实依据的序号（1-based，对应 material_facts 的 n）。**段内事实性内容不得超出"
    "声明集合** —— 写不进声明的内容，就是你不该写的内容。\n"
    "3b. **素材没写的内容，一个字都不要补**。这是本项目**最常见**的失败原因 ✗："
    "补出来的内容会被质检逐条判红，改两轮仍不过 → **整篇作废**。具体禁止：\n"
    "    · 不要补**方法论/步骤清单/操作建议**（「第一步先算生存线，再谈理想」"
    "「建议先小成本验证需求」）；\n"
    "    · 不要补**因果推断或总结**（「这件事让我意识到：需求拆解比文笔更影响收入」"
    "「这条路能走通」）；\n"
    "    · 不要补**适用人群/可复制性判断**（「这条路并非人人可复制」"
    "「适合有稳定主业、能接受前期低回报的人」）；\n"
    "    · 不要给素材的行为**补动机、程度、时间跨度**（素材写「有单就接」，"
    "不要写成「不敢挑活，有单就接」；素材写「差点就放弃了」，不要写成「差点把号注销」）；\n"
    "    · 素材是**个人经历**时，就写个人经历 —— **不要把它升格成通用教程**；\n"
    "    · 素材没提的**数字/年份/专名**，不要写（这条机械门会直接拒绝）。\n"
    "    注意：「更值钱」「更有干货」**不是**可以补内容的理由 —— 加了照样判红。\n"
    "4. 槽位：段内与标题里的每个数字、年份、专有名词，必须写成 {{n:序号|表层}}。"
    "表层 = 你想让它出现在成稿里的写法，系统校验「表层在该条事实里有依据」后**原样回填**"
    "（数值等价也算依据：素材「两百八十六」表层可写「286」；素材「第1步」表层可写「第一步」）。\n"
    "5. 除槽位之外，段内与标题里**禁止出现任何数字/年份/专名** —— 裸写会被系统机械门"
    "直接拒绝，整篇作废重写。\n"
    "6. **表述自由，内容不自由**：改语气、改结构、埋钩子、换人称 —— 随便；"
    "但**不许出现素材里没有的内容**（见 3b）。被钉死的是两件事：事实值，和「哪段依据哪条事实」。"
    "这不是翻译/搬运，而是按平台调性重写。\n"
    # ---- 以下两条来自真实作者评审（docs/74 §八）----
    "7b. **完整性优先于精简**：素材里已经给出的**具体例子、工具名、数字、步骤**必须保留。"
    "小标题写了「示例」就必须真的给一个具体例子（只列公式不举例 = 名不副实 ✗）；"
    "平台若是「收藏型」（如小红书），**关键工具名不得省略** —— 省掉收藏价值就没了。\n"
    "7c. **同一篇内禁止同义反复**：同一个意思（例如「不是一稿多投」「母体最好是3000字长文」"
    "「10条不是10篇新文章」）只在一处说透；换段换个说法再讲一遍，读者会当成凑字数 ✗。\n"
    "7d. **不许绝对化**：用「往往 / 多数 / 通常 / 不少人」，不要用「从来 / 永远 / 一定 / 全部」——"
    "除非素材原话如此；绝对化表述既失真，也会被质检判成新增断言 ✗。\n"
    "7e. **AI 标注**：平台 DNA 里若写了「AI 辅助创作需主动标明」（小红书就是这样），"
    "成稿**文末最后一段**必须有一句明确说明，例如「本文由 AI 辅助创作，观点与修改由本人完成」。"
    "这句用「AI 辅助 / 由 AI 协助」这类字样，不要只写「用 AI 写」—— 那不算标注 ✗。\n"
    "7. 若输入带 user_template：其 opening / closing **原样**放入首段/末段的 text，"
    "不用槽位（系统对模板文本豁免数字校验）；structure 与 voice 按模板执行。\n"
    "role 取 hook|body|evidence|close（提示段落用途，不影响顺序）。\n"
)


def _draft_payload(dna: dict, structured, brief, template: dict | None = None,
                   creator_profile: dict | None = None,
                   retrospect_hints: list[str] | None = None,
                   avoid_sentences: list[str] | None = None,
                   library_context: list[dict] | None = None,
                   image_notes: str | None = None) -> dict:
    """三种草稿提示词共用的**材料与规则**（骨架 / 提纲 / 单段）。

    只此一份：分段拆步时如果各写一套，两边的平台规则会慢慢不一致。
    """
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
            # 同 brief：分发机制原先也没传（审计发现）
            "distribution": dna.get("distribution", []),
            # 调研出来的数值规格（各平台字段名统一在 specs 下）
            "specs": dna.get("specs", {}),
            # ⚠️ 标题字数上限**必须显式传给模型**：它只在 DNA 的 `specs.title_chars_max` 里，
            # 而 `title_rules` 是散文（"多用数字+结果"），不含长度 —— 不传的话模型压根不知道
            # 小红书标题要 ≤20 字（实测：xhs 标题系统性超限，加固后更严重）。
            "title_chars_max": dna.get("specs", {}).get("title_chars_max"),
            # 正文篇幅窗口（openspec: platform-differentiation）
            "body_chars": dna.get("body_chars", {}),
        },
        "brief": {
            "angle": brief.angle,
            "hooks": brief.hooks,
            "structure_plan": brief.structure_plan,
            "tag_direction": brief.tag_direction,
        },
        # 带**序号**：模型靠它写段落的 facts 声明与槽位（1-based）
        "material_facts": [{"n": i, "text": f.text}
                           for i, f in enumerate(structured.facts, 1)],
    }
    if template:
        payload["user_template"] = template
    if creator_profile:
        payload["creator_profile"] = _profile_dict(creator_profile)
    if retrospect_hints:
        payload["retrospect_hints"] = list(retrospect_hints)
    # 写作规则必须放进 **JSON 内部**（`writing_rules`）：prompt 在无 feedback 时
    # 是纯 JSON，既有测试与调试工具都直接 `json.loads(prompt)` —— 在尾部追加散文
    # 会破坏这个契约 ✗（实测：test_title_limit 立刻红）。
    rules = ["平台篇幅按 `platform_dna.body_chars` 的窗口来写（低于下限或超出上限都算不合格）。"]
    code = (dna.get("code") or dna.get("platform") or "").lower()
    if code in ("xhs", "xiaohongshu"):
        rules.append("【平台格式必备】小红书正文或 tags 必须包含至少 5 个话题标签（如 `#话题`）。表达短小精炼，多用 emoji 分点清单（如「📌 步骤名：说明」），避免书面长句铺陈。")
    elif code in ("gzh", "gongzhonghao"):
        rules.append("【平台格式必备】公众号正文必须使用至少 3 个分段小标题（使用 markdown `## 标题` 或独占一行的加粗小标题、或【小标题】），层级清晰。素材中的方法、步骤、公式或案例清单应完整展开，承载深度干货（确保篇幅达到 800~3000 字窗口，避免过于简略）。")
    elif code in ("zhihu",):
        rules.append("【平台格式必备】知乎首句必须结论先行：开头 60 字内直接亮出核心观点/结论，严禁以「首先/其实/说起来/话说/最近/这几年/随着」等铺垫词开头。论证侧重底层逻辑反思与利弊分析。素材中的方法推导、对比与清单要充分展开（确保篇幅达到 500~2000 字窗口，避免过于简短）。")
    if avoid_sentences:
        # 多平台同发时，把**别的平台已经用过的句子**交给模型，要求换说法。
        # 为什么必须显式给：模型看不到别的平台的产出，只靠"要差异化"这种空话
        # 会把同一句金句原样搬过去 ✗（2026-11-01 实测：三篇逐字重合 30%~33%）。
        payload["already_used_sentences"] = list(avoid_sentences)
        rules.append(
            "【跨平台去重】`already_used_sentences` 是同一批里其它平台已经用过的句子；"
            "表达同样的意思必须换词、换句式、换切入角度，不得逐字复用；"
            "即使是同一事实或金句，各平台切入视角也须契合本平台定位（小红书侧重避雷清单、知乎侧重底层逻辑、公众号侧重实操全景）；"
            "严禁直接照搬 `already_used_sentences` 中的原话（必须改写为符合本平台调性的表达）；"
            "**事实与数据要保留**（数字、步骤、结论不能因为去重而丢），变的是表达与组织结构。"
        )
    if library_context:
        # 参考资料：只能学"怎么写"，不能拿它的**事实** ✗
        # 为什么必须写死：参考素材和本次素材主题相近，模型极易把参考里的数字/案例搬过来，
        # 而质检的事实核对只认**本次素材** —— 搬过来就会被判"新增断言"。
        payload["reference_materials"] = [
            {"id": r.get("id"), "title": r.get("title", ""),
             "snippet": r.get("snippet", ""), "keywords": r.get("keywords", [])}
            for r in library_context[:3]
        ]
        rules.append(
            "【参考素材】`reference_materials` 是作者过去写过的相关内容，**只能借鉴**它们的"
            "切入角度、句式与结构；**事实、数字、案例、人名一律不得取自参考素材** —— "
            "成稿里的每一个事实都必须能在 `material_facts` 里找到依据。"
            "可以完全不用它们（不相关时忽略 ✓）。"
        )
    if image_notes:
        # 图片/视频素材的**额外观察**：模型看过原图或关键帧后记下的细节
        payload["image_notes"] = image_notes
        rules.append(
            "【画面观察】`image_notes` 是对素材原图的描述，可作为**细节与氛围**参考；"
            "但**事实与数字仍以 material_facts 为准** —— 画面里没有量化信息时不补 ✗。"
        )
    payload["writing_rules"] = rules
    return payload


def build_draft_skeleton_prompt(dna: dict, structured, brief, feedback: str | None = None,
                                template: dict | None = None,
                                creator_profile: dict | None = None,
                                retrospect_hints: list[str] | None = None,
                                avoid_sentences: list[str] | None = None,
                                library_context: list[dict] | None = None,
                                image_notes: str | None = None) -> str:
    payload = _draft_payload(dna, structured, brief, template, creator_profile,
                             retrospect_hints, avoid_sentences, library_context, image_notes)
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        prompt += f"\n\n【上一轮产出未通过校验，请针对性修复后重新输出完整骨架 JSON】\n{feedback}"
    return prompt


# ---------------------------------------------------------------- 骨架拆步（llm_draft_split）
#
# 为什么拆（docs/79）：一次让模型写完整篇多段正文，思考 token 能到 3 万+，
# 单次 p95 210 秒。拆成「提纲 → 逐段写」后每次只思考一小块，段间还能并行。
# **形状保持不变**：产出的骨架 JSON 与不拆时一样，下游闸门/装配一行不用改。

def build_draft_outline_prompt(dna: dict, structured, brief, feedback: str | None = None,
                               template: dict | None = None,
                               creator_profile: dict | None = None,
                               retrospect_hints: list[str] | None = None,
                               avoid_sentences: list[str] | None = None,
                               library_context: list[dict] | None = None,
                               image_notes: str | None = None) -> str:
    """第一步：只出提纲（标题/标签/每段用哪几条事实），**不写正文**。"""
    payload = _draft_payload(dna, structured, brief, template, creator_profile,
                             retrospect_hints, avoid_sentences, library_context, image_notes)
    payload["task"] = "只输出提纲，不写正文"
    payload["output_schema"] = {
        "titles": ["主标题", "备选标题"],
        "tags": ["#标签"],
        "cover_suggestion": "封面建议（一句话）",
        "interaction_line": "结尾互动句",
        "rationale": "为什么这样排（给作者看）",
        "outline": [{"role": "hook|body|cta", "facts": [1, 2], "point": "这一段要说什么（一句话）"}],
    }
    payload["output"] = ("严格按 output_schema 返回 JSON。`outline` 的段数按平台 structure_template 与 "
                         "body_chars 定；`point` 一句话，★不要写正文★，不要占位符。")
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        prompt += f"\n\n【上一轮产出未通过校验，请针对性调整提纲】\n{feedback}"
    return prompt


def build_draft_section_prompt(dna: dict, structured, brief, section: dict,
                               index: int, total: int,
                               template: dict | None = None,
                               creator_profile: dict | None = None,
                               retrospect_hints: list[str] | None = None,
                               avoid_sentences: list[str] | None = None,
                               library_context: list[dict] | None = None,
                               image_notes: str | None = None,
                               feedback: str | None = None) -> str:
    """第二步：只写这一段正文（数字仍用占位符）。"""
    payload = _draft_payload(dna, structured, brief, template, creator_profile,
                             retrospect_hints, avoid_sentences, library_context, image_notes)
    payload["task"] = f"只写第 {index}/{total} 段正文"
    payload["section"] = {
        "role": section.get("role") or "body",
        "facts": section.get("facts") or [],
        # 把这一段声明的事实原文直接摆出来，免得它去翻整份清单
        "fact_texts": [{"n": n, "text": structured.facts[n - 1].text}
                       for n in (section.get("facts") or [])
                       if isinstance(n, int) and 1 <= n <= len(structured.facts)],
        "point": section.get("point") or "",
    }
    # 每段预算 = 平台正文窗口上界 / 段数。不给预算，模型会按"写满"来写（实测 2396 字 vs 上限 1000）。
    budget = None
    bmax = (dna.get("body_chars") or {}).get("max")
    if isinstance(bmax, (int, float)) and total > 0:
        per = max(40, int(bmax / total))
        budget = {"min": int(per * 0.6), "max": per}
        payload["section"]["char_budget"] = budget
    payload["output_schema"] = {"text": "这一段正文；数字写成 {{n:序号|表层}}"}
    limit = f"（{budget['min']}~{budget['max']} 字）" if budget else ""
    payload["output"] = (f"严格按 output_schema 返回 JSON，只写这一段{limit}。"
                         "★不要写标题、不要解释、不要输出其它段★。")
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        prompt += f"\n\n【上一轮这一段未通过校验，请针对性修复】\n{feedback}"
    return prompt


# ============================================================ 质量门(qa)
QA_SYSTEM = (
    "你是平台内容质检编辑。审查给定成稿，只输出一个 JSON 对象：\n"
    "{\n"
    '  "passed": true/false,\n'
    '  "issues": ["必须修复的问题(未通过时必填)", "..."],\n'
    '  "issue_items": [\n'
    '    {"category": "数字|年份|专名|新增断言|其它", "quote": "成稿里出问题的那一句",\n'
    '     "detail": "问题说明 + 应该改成什么"}\n'
    '  ],\n'
    '  "warnings": ["非阻断提醒", "..."]\n'
    "}\n"
    "`issue_items` 的 category **只能用这五个值之一**（便于按类定向修改）：\n"
    "  · 数字 —— 数字与素材不符、或素材里没有\n"
    "  · 年份 —— 年份与素材不符、或素材里没有\n"
    "  · 专名 —— 品牌/工具/机构名素材里没有\n"
    "  · 新增断言 —— 素材未提及的断言（含把两件事连成的新因果、加动机/程度/时间跨度）\n"
    "  · 其它 —— 上述之外（超长、标签等）\n"
    "\n"
    "【做法：先把成稿拆成原子断言，再逐条核对】\n"
    "把成稿拆成**原子断言** —— 一句话一条，不要把两件事合成一条。"
    "然后对**每一条**问：「素材里能直接支持它吗？」\n"
    "  · `claims_checked` = 一共拆出多少条\n"
    "  · `claims_unsupported` = 其中多少条**素材里找不到依据**\n"
    "  · 这两个数**必须如实数**，它们用来算「支持率」；不要为了好看少报\n"
    "  · 正常的改写、换语气、换结构、补互动钩子**不算**没依据\n"
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



def build_disfluency_prompt(pairs: list[dict]) -> str:
    """去冗余的模型复核（`docs/spec_disfluency.md` 关键决定二/三）。

    **只把规则层拿不准的相邻句对交出去** —— 不是全文。这样模型的输入是受控的小集合：
    成本可预测，且它不会"整体跑偏"去改写别的地方。

    `pairs`：`[{"index":0, "prev":"...", "cur":"..."}]`

    要求模型只回答"这两句是不是在说同一件事"，**不给时间戳** ——
    时间戳一律由程序从原句对取（模型看不到音频，给的时间戳不可信）。
    """
    payload = {
        "task": "判断相邻两句是不是「同一件事说了两遍」",
        "rules": [
            "只看这两句，不看别的",
            "「同一件事说两遍」= 后一句在重复前一句的意思（换个说法也算）",
            "正常的递进、补充、举例**不算**重复（比如「我喜欢读书」→「尤其是历史书」）",
            "排比、强调**不算**重复",
            "拿不准就判 false —— 宁可漏，不可错剪（剪错正话的代价远大于多留半句）",
        ],
        "pairs": pairs,
        "output_schema": {
            "verdicts": [
                {"index": 0, "same": True, "why": "一句话说清理由（给用户看）"}
            ]
        },
        "output": "严格按 output_schema 返回 JSON，verdicts 要覆盖每一个 pair 的 index",
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 停顿复核（pause-review-cuts）

def build_pause_review_prompt(candidates: list[dict]) -> str:
    """停顿复核（`openspec/changes/pause-review-cuts`）。

    给模型的**只有候选停顿 + 前后两句**，不是全文 —— 输入受控、成本可预测、
    也不会整体跑偏去改写别的地方。

    模型**不给时间**：候选自带 id，时间戳一律由程序取（它看不到音频）。
    """
    payload = {
        "task": "判断口播视频里每个停顿点该剪掉还是保留",
        "rules": [
            "只回答 id + action(cut/keep) + why；**不要给任何时间或秒数**",
            "cut = 卡壳/犹豫/想下一句：剪掉更紧凑",
            "keep = 故意留白/强调/情绪/段落之间的呼吸",
            "一句话没说完就停顿 → keep（后半句还接着这句话）",
            "换了话题/新段落前的停顿 → keep（那是呼吸，不是卡壳）",
            "语速慢、语气重、重复关键词 → 多半在强调 → keep",
            "拿不准就 keep —— 宁可多留半秒，不可剪断语气",
            "讲自己的经历、说到动情处 → keep",
            "机械重复的口头填充（嗯、啊、这个那个）后接停顿 → cut",
            "有 prosody 字段时**必须参考**：rms_slope 明显为负（渐弱收尾）多判 keep；"
            "rms_slope 接近 0 或为正、且前句带口头填充，多判 cut；"
            "pitch_delta 明显升高（后面起势）说明要开新段落 → keep",
            "prosody 只是参考：它说明**怎么说的**，不说明**说了什么重要不重要**",
        ],
        "candidates": candidates,
        "field_help": {
            "gap_sec": "停顿长度（秒，程序算的）",
            "rule_would_cut": "规则原本会不会剪（你是在复核它）",
            "prosody": "本地算的声学特征：rms_slope=停顿前能量走势（负=渐弱，正=渐强），"
                       "pitch_delta=停顿后音高减停顿前（Hz，正=后面更高）",
        },
        "output_schema": {
            "verdicts": [
                {"id": 0, "action": "keep", "why": "一句话说清理由（给用户看）"}
            ]
        },
        "output": "严格按 output_schema 返回 JSON，verdicts 要覆盖每一个候选 id",
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 提示词版本化（M9）
#
# 为什么要有这一块（`docs/spec_promptver.md`）：改了 prompt 之后，历史记录里
# **看不出哪条是旧 prompt 产的** —— 评测集能说"这次没退步"，但说不出"跟哪一次比"。
#
# 版本号 = **提示词构造函数的源码哈希**（用户选"内容哈希"）：
#   - 手写版本号**会忘** ✗（忘了就白做，而这正是要防的事）
#   - 整个 prompts.py 的文件哈希**太粗** ✗（改一处把所有提示词都标成新版本）
#   - 最终拼出来的 prompt 的哈希是**数据哈希** ✗（素材变了就变，跟"提示词改没改"无关）
#   构造函数源码正好是那个"模板"：含全部静态文案与拼接逻辑，不含素材内容 ✓

#: scene → 构造函数。**穷举现有 scene**（`grep -rho 'scene="[a-z_]*"'` 得到）——
#: 漏一个就等于那个提示词没有版本。取值是"模块路径.函数名"，惰性导入避免循环依赖。
SCENE_BUILDERS: dict[str, str] = {
    "understand": "app.pipeline.prompts.build_understand_prompt",
    "brief": "app.pipeline.prompts.build_brief_prompt",
    "draft": "app.pipeline.prompts.build_draft_skeleton_prompt",
    "draft_outline": "app.pipeline.prompts.build_draft_outline_prompt",
    "draft_section": "app.pipeline.prompts.build_draft_section_prompt",
    "qa": "app.pipeline.prompts.build_qa_prompt",
    "qa_verify": "app.pipeline.prompts.build_qa_prompt",      # 同一个构造函数
    "clip": "app.pipeline.prompts.build_clip_prompt",
    "learn": "app.pipeline.prompts.build_learn_prompt",
    "split": "app.pipeline.split._prompt",
    "disfluency": "app.pipeline.prompts.build_disfluency_prompt",
    "pause_review": "app.pipeline.prompts.build_pause_review_prompt",
    "eval_corpus": "app.pipeline.prompts.build_understand_prompt",   # 语料走理解提示词
    # 参考视频分析（reference-video-analysis）：提示词在 reference.py 里，
    # 登记进这张表才能被"版本号"这一层管住 ✓（测试会抓漏网的 ✗）
    "reference": "app.pipeline.reference.system_prompt",
}

_FP_CACHE: dict[str, tuple[str, str]] = {}          # name → (fp, source)


def _resolve(dotted: str):
    """按 "模块.函数" 取对象。取不到返回 None（不抛错）。"""
    try:
        import importlib
        mod_path, _, attr = dotted.rpartition(".")
        return getattr(importlib.import_module(mod_path), attr)
    except Exception:                                        # noqa: BLE001
        return None


def fingerprint_source(dotted: str) -> tuple[str, str]:
    """返回 `(哈希, 源码)`。哈希取不到时是空串。

    哈希 = 源码文本的 SHA-256 前 12 位 —— 够短够用，且**跨进程/跨机器一致**
    （只依赖源码文本，不含时间戳、不含路径）。
    """
    if dotted in _FP_CACHE:
        return _FP_CACHE[dotted]
    fn = _resolve(dotted)
    if fn is None:
        _FP_CACHE[dotted] = ("", "")
        return _FP_CACHE[dotted]
    try:
        import hashlib
        import inspect
        src = inspect.getsource(fn)
        fp = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
    except Exception:                                        # noqa: BLE001
        # 打包成 pyc / 动态生成时取不到源码 —— 留空并标注，**不抛错**
        src, fp = "", ""
    _FP_CACHE[dotted] = (fp, src)
    return _FP_CACHE[dotted]


def fingerprint_for_scene(scene: str) -> str:
    """`chat_json(scene=…)` 用的版本号。未知 scene → 空串（不抛错）。"""
    dotted = SCENE_BUILDERS.get((scene or "").strip())
    return fingerprint_source(dotted)[0] if dotted else ""


def template_for_scene(scene: str) -> str:
    """该 scene 的提示词模板全文（供"哈希 → 全文"反查）。取不到返回空串。"""
    dotted = SCENE_BUILDERS.get((scene or "").strip())
    return fingerprint_source(dotted)[1] if dotted else ""


def fingerprints() -> dict[str, str]:
    """所有已注册提示词的 `scene → 哈希`（供 --list 与测试）。"""
    return {sc: fingerprint_source(d)[0] for sc, d in SCENE_BUILDERS.items()}
