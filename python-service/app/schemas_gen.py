"""生成阶段数据模型：brief（平台策略）/ draft（成稿）/ clip_sheet（剪辑单）/ qa（质检）。"""
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import StructuredMaterial


class Brief(BaseModel):
    """某平台的内容策略。"""
    platform_code: str = Field(..., description="平台代码")
    angle: str = Field(..., description="内容切入角度")
    hooks: list[str] = Field(default_factory=list, description="钩子候选(标题/开头方向)")
    structure_plan: str = Field(..., description="结构规划")
    tag_direction: list[str] = Field(default_factory=list, description="话题标签方向")
    rationale: str = Field(default="", description="为什么这么改写(参考平台DNA)")


class UserTemplate(BaseModel):
    """用户提供的内容模板（用户级覆盖层，ADR-014）。

    优先级高于平台 DNA 的结构/风格部分；平台 limits/红线 仍由 QA 强制执行。
    """
    name: str = Field(default="我的模板", description="模板名称")
    voice: str = Field(default="", description="语气/人设偏好")
    opening: str = Field(default="", description="固定开头句式(可选)")
    structure: list[str] = Field(default_factory=list, description="正文结构要点")
    closing: str = Field(default="", description="固定结尾/互动句式(可选)")
    tag_style: str = Field(default="", description="标签风格偏好(可选)")
    taboo: list[str] = Field(default_factory=list, description="我不想要的内容(可选)")


class ClipScene(BaseModel):
    """剪辑单中的单个分镜。"""
    seq: int = Field(..., description="分镜序号(从1开始)")
    duration_hint: str = Field(default="", description="建议时长，如 '8s'/'前3秒'")
    script: str = Field(..., description="该分镜口播/字幕内容")
    visual: str = Field(default="", description="画面建议(实拍/图文/数据展示等)")
    subtitle: str = Field(default="", description="字幕断句建议")
    sound: str = Field(default="", description="声音/BGM/音效备注")


class ClipSheet(BaseModel):
    """视频平台成稿附带的剪辑执行单（AI 剪辑 A 阶段，FR-50）。"""
    intro_note: str = Field(default="", description="剪辑要点总说明")
    scenes: list[ClipScene] = Field(default_factory=list, description="分镜列表")
    bgm_hint: str = Field(default="", description="BGM 风格建议")


class DraftPayload(BaseModel):
    """单平台可发布成稿。"""
    titles: list[str] = Field(..., min_length=1, description="主标题+备选(含不同标题策略)")
    body: str = Field(..., description="正文/脚本/帖子正文")
    tags: list[str] = Field(default_factory=list, description="话题标签(不带#)")
    cover_suggestion: str = Field(default="", description="封面文案/首屏钩子建议")
    interaction_line: str = Field(default="", description="互动引导语")
    rationale: str = Field(default="", description="改写说明:踩了哪些平台机制")
    # 事实依据声明（`docs/spec_factguard.md` 关键决定三）：
    # 把"你有没有幻觉"换成"你写的这句依据哪条事实" —— 给机械校验一个明确的对照集合。
    # 用**序号**（对应 prompt 里事实清单的 1-based 编号）而不是原文，省 token 且精确。
    #
    # ⚠️ 三态语义必须分清（checklist §4）：
    #   None = **没声明**（违规，校验要报）
    #   []   = 声明了"本次没用到任何事实"（合法）
    #   [1,3]= 依据第 1、3 条
    # 所以这里**不能**用 default_factory=list —— 那样"没声明"会被伪装成"声明了空"。
    facts_used: list[int] | None = Field(
        default=None, description="本次正文依据的事实序号(1-based)；空列表=没用到事实")
    clip_sheet: ClipSheet | None = Field(default=None, description="视频平台剪辑单(仅 video_native 平台)")


# 失败分类（**封闭四类**，docs/spec_factguard.md 关键决定二）：
# 不做开放式建议，否则没法验收，也没法按类给定向指令。
QaIssueCategory = Literal["数字", "年份", "专名", "新增断言", "其它"]


class QaIssue(BaseModel):
    """一条质检问题：**哪一类** + **哪一句** + **怎么改**。

    结构化是为了让重写能**按类给不同指令**（T6）——
    原先只有一句笼统的 feedback，模型只能自己猜该改什么。
    """
    category: QaIssueCategory = Field(..., description="失败分类")
    quote: str = Field("", description="成稿里出问题的那一句(便于定位)")
    detail: str = Field("", description="问题说明")


class QaReport(BaseModel):
    passed: bool = Field(..., description="是否通过质量门")
    issues: list[str] = Field(default_factory=list, description="未通过项")
    warnings: list[str] = Field(default_factory=list, description="提醒(不阻断)")
    # 结构化版本（可选，便于按类定向重写；老调用方不传也能跑）
    issue_items: list[QaIssue] = Field(default_factory=list,
                                       description="结构化的问题清单(分类+定位)")
    # **原子计数**（FActScore 口径）：把成稿拆成原子断言逐条核对，报出总数与不通过数。
    # 为什么需要：二值通过率噪声极大（实测同一代码多轮 38%~78%），
    # 而机械检查（数字/年份/专名）几乎总是干净的 —— 真正在失败的是语义级断言。
    # 只有把这一层也原子化，支持率才既稳又有区分度。
    claims_checked: int = Field(0, description="核对了多少条原子断言")
    claims_unsupported: int = Field(0, description="其中多少条在素材里找不到依据")


class PlatformDraft(BaseModel):
    platform_code: str
    platform_name: str = ""
    brief: Brief
    draft: DraftPayload
    qa: QaReport


class GenerateRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)
    source_kind: str = Field("general", description="general|长文|口播稿|大纲|笔记")
    title: str | None = None
    platforms: list[str] = Field(default_factory=lambda: ["xhs"], description="目标平台代码列表")
    tone_override: str | None = Field(None, description="可选：覆盖素材语气")
    template: UserTemplate | None = Field(None, description="可选：用户内容模板(ADR-014)")
    # FR-32 创作者画像（来自 /api/profile；不传则视为空）
    creator_profile: dict | None = Field(default=None, description="创作者画像{brand_voice,domain,audience,avoid}")
    # FR-33 复盘经验回写（来自 /api/retrospect 的 insights[]；不传则视为空）
    retrospect_hints: list[str] | None = Field(default_factory=list,
                                               description="历史复盘建议文案(逐平台什么有效/无效)")
    # FR-34 事实确认闭环：用户已确认的事实清单。
    # 传入时**跳过理解阶段**（省一次 LLM 调用，且保证「所见即所用」）
    confirmed_facts: StructuredMaterial | None = Field(
        default=None, description="已确认事实(core_message/tone/audience/facts)；有则跳过 understand")
    # M8 第二片：阶段事件出口。给了就写（带 version 的 JSONL），不给就**什么都不写**（向后兼容）。
    # 用途：Java 侧的任务编排靠读这个文件算进度（见 docs/spec_async_generate.md §5）。
    events_path: str | None = Field(
        default=None, description="可选：阶段事件文件路径（绝对路径）；不传则不写任何文件")


class GenerateFailure(BaseModel):
    """单平台生成失败（部分失败可容忍：其余平台照常交付）。"""
    platform: str = Field(..., description="失败的平台代码")
    error: str = Field(..., description="失败原因（含异常类型）")


class GenerateResponse(BaseModel):
    """素材 → N 平台成稿（brief+draft+clip+qa 全链路）。

    注意：多平台生成是**可部分成功**的操作 —— 某平台失败时会记入 failures，
    其余平台照常返回，调用方须同时处理 drafts 与 failures。
    """
    structured: dict = Field(..., description="素材理解结果(含事实清单)")
    drafts: list[PlatformDraft]
    used_mock: bool
    failures: list[GenerateFailure] = Field(default_factory=list,
                                            description="失败平台与原因（部分失败可容忍）")


# ============================================================ 示例学习(FR-63)
class LearnRequest(BaseModel):
    """示例学习请求：喂一段自有示例文本，拆解出模板参数。"""
    sample_text: str = Field(..., min_length=1, description="示例正文（Java 侧保证 ≥50 字）")
    source_note: str | None = Field(None, description="示例来源备注，如 '9月小红书爆款'（不存原文）")


class LearnedTemplate(BaseModel):
    """拆解产物（草稿模板参数）。只归纳结构，不复述示例内容。"""
    name: str = Field(..., description="建议模板名（由服务端按来源备注生成）")
    voice: str = Field(default="", description="语气/人设")
    opening: str = Field(default="", description="开头句式(<=40字)")
    structure: list[str] = Field(default_factory=list, description="正文结构要点(短语, 3~6 条)")
    closing: str = Field(default="", description="结尾/互动句式(<=40字)")
    tag_style: str = Field(default="", description="标签风格描述(可空)")
    taboo: list[str] = Field(default_factory=list, description="避雷项(可空)")
    rationale: str = Field(default="", description="拆解依据说明，供用户核对")


class LearnResponse(BaseModel):
    template: LearnedTemplate
    used_mock: bool
