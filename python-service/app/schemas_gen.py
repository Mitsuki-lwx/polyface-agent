"""生成阶段数据模型：brief（平台策略）/ draft（成稿）/ qa（质检）。"""
from pydantic import BaseModel, Field


class Brief(BaseModel):
    """某平台的内容策略。"""
    platform_code: str = Field(..., description="平台代码")
    angle: str = Field(..., description="内容切入角度")
    hooks: list[str] = Field(default_factory=list, description="钩子候选(标题/开头方向)")
    structure_plan: str = Field(..., description="结构规划")
    tag_direction: list[str] = Field(default_factory=list, description="话题标签方向")
    rationale: str = Field(default="", description="为什么这么改写(参考平台DNA)")


class DraftPayload(BaseModel):
    """单平台可发布成稿。"""
    titles: list[str] = Field(..., min_length=1, description="主标题+备选(含不同标题策略)")
    body: str = Field(..., description="正文/脚本/帖子正文")
    tags: list[str] = Field(default_factory=list, description="话题标签(不带#)")
    cover_suggestion: str = Field(default="", description="封面文案/首屏钩子建议")
    interaction_line: str = Field(default="", description="互动引导语")
    rationale: str = Field(default="", description="改写说明:踩了哪些平台机制")


class QaReport(BaseModel):
    passed: bool = Field(..., description="是否通过质量门")
    issues: list[str] = Field(default_factory=list, description="未通过项")
    warnings: list[str] = Field(default_factory=list, description="提醒(不阻断)")


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


class GenerateResponse(BaseModel):
    """素材 → N 平台成稿（brief+draft+qa 全链路）。"""
    structured: dict = Field(..., description="素材理解结果(含事实清单)")
    drafts: list[PlatformDraft]
    used_mock: bool
