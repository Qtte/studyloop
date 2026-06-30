"""
Schema definitions for the StudyLoop LangGraph workflow.

═══════════════════════════════════════════════════════════════════════════════
架构说明
═══════════════════════════════════════════════════════════════════════════════

这个模块定义了 StudyLoop 的两类数据结构：

1. POD (Plain Old Data) 结构体
   QuizQuestion / GradingResult / LearningPlan —— LLM 返回的结构化输出模型。
   它们使用 Pydantic BaseModel 做校验，如果 LLM 返回的 JSON 字段缺失或类型不符，
   会自动触发 fallback 逻辑（见 nodes._parse_structured_output）。

2. LangGraph State
   StudyLoopState —— 图编排中在节点间流转的"共享状态"。
   每个节点返回一个 dict 子集，LangGraph 自动合并回 State。
   这是 LangGraph 的核心设计：状态驱动、不可变更新、可 checkpoint。

为什么 quiz 字段是 dict 而非 QuizQuestion？
────────────────────────────────────────────────
一开始 quiz 字段类型是 `QuizQuestion | None`，但 generate_practice_set 产出的
是包含 question_type / question_id / options 等额外字段的 dict（不只是 QuizQuestion
的那 4 个字段）。Pydantic 在校验时会静默丢弃额外字段，导致 subscriptable 时报错。
改为 `dict | None` 保持数据完整性，前端用下标访问也不会崩。

═══════════════════════════════════════════════════════════════════════════════
循环边相关字段（用于自适应补练）
═══════════════════════════════════════════════════════════════════════════════

- quiz_mode: "practice"（正常出题）| "remediation"（错题重练）
- remediation_quiz: 补练产出的单道题（取题集第一题）
- retry_count: 已执行的补救次数（generate_quiz 节点在 remediation 模式下自增）
- max_retries: 单次 grade 流最大补救次数（默认 1，防死循环）
- mastery_threshold: 掌握度阈值，低于此值触发补救回路
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QuizQuestion(BaseModel):
    """LLM 产出的单道练习题——这是被解析后的结构化结果。

    注意：实际的题集（quiz_set）包含更丰富的字段（question_type / options /
    correct_option / rubric / difficulty），由 service.generate_practice_set
    统一生成。这个模型用于 define 期望字段，但 state.quiz 现在用 dict 存全量数据。
    """

    question: str
    reference_answer: str
    rubric: list[str] = Field(default_factory=list)
    difficulty: str = "medium"


class GradingResult(BaseModel):
    """批改结果——LLM 对学生作答的结构化评估。

    mistake_type 枚举（前端可据此换 badge 颜色）：
      - correct:              回答完全正确
      - concept_confusion:    概念混淆（把 A 当成 B）
      - concept_missing:      概念缺失（没提到关键点）
      - shallow_answer:       回答过于表面，缺乏深度
      - missing_evidence:     没有引用检索到的证据
      - application_weak:     知道了但不会用（应用能力弱）
    """

    score: int = Field(ge=0, le=100)
    mistake_type: str
    feedback: str
    evidence_used: list[str] = Field(default_factory=list)
    suggested_note: str


class LearningPlan(BaseModel):
    """学习重规划——批改后给学习者的下一步建议。

    这是"学习闭环"的最后一块拼图：
    批改 → 记忆更新 → 重规划 → （弱掌握度时）补练出题 → 结束
    """

    summary: str
    focus_areas: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    recommended_question_types: list[str] = Field(default_factory=list)


class StudyLoopState(BaseModel):
    """
    LangGraph 的共享状态——贯穿所有节点的"数据总线"。

    设计原则：
    ─────────────────────────────────
    1. 所有字段都有默认值（空字符串 / None / 空列表），这样每个节点只需返回它关心的子集，
       LangGraph 自动做浅合并（shallow merge）。
    2. 节点之间通过 state 字段通信，不通过函数返回值。
       例如 grade_answer 把 grading_result 写入 state，
       update_memory 从 state 读出 grading_result 并计算 mastery。
    3. intent 字段决定条件路由（conditional edge），是整个图的核心控制信号。

    流程图中的 state 演变：
    ─────────────────────────────────
    START
     │
     ▼
    parse_user_intent    →  填充 intent / current_task / concept_id
     │
     ▼
    retrieve_materials   →  填充 retrieved_evidence
     │
     ▼
    retrieve_learning_notes → 填充 learning_notes / mistake_history
     │
     ▼
    build_study_context  →  填充 study_context（10 段标准上下文）
     │
     ▼
    ┌─ explain_concept   →  填充 explanation
    ├─ generate_quiz     →  填充 quiz / quiz_set
    └─ grade_answer      →  填充 grading_result
                                 │
                                 ▼
                            update_memory → 填充 mastery_before / mastery_after / note_result
                                 │
                                 ▼
                            replan_learning_path → 填充 next_plan
                                                  如果 mastery_after < 0.6:
                                                  设 quiz_mode="remediation"
                                                      │
                                              ┌─→ generate_quiz（补练）→ END
                                              └─→ END
    """

    # ─── 控制字段 ───
    intent: str | None = None          # "explain" | "quiz" | "grade" —— 决定条件路由

    # ─── 学习目标与上下文 ───
    learning_goal: str = ""            # 当前学习目标（由 prepare_study_brief 推导）
    current_topic: str = ""            # 当前学习主题（用于检索 + 笔记关联）
    concept_id: str = ""               # 概念 slug（用于文件名/查找，如 "context-engineering"）
    learner_state: dict[str, Any] = Field(default_factory=dict)  # {mastery:0.5, last_score:75,...}
    current_task: str = ""             # 当前任务描述（如 "解释概念" / "生成练习题"）
    conversation_context: list[Any] = Field(default_factory=list)  # 对话历史（预留）

    # ─── 检索与证据 ───
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)  # 检索到的资料切片
    mistake_history: list[dict[str, Any] | str] = Field(default_factory=list)  # 错题笔记
    learning_notes: list[dict[str, Any] | str] = Field(default_factory=list)  # 学习笔记

    # ─── 拼装后的上下文（10 段格式）───
    study_context: str = ""            # build_study_context 节点产出

    # ─── 用户输入 ───
    question: str = ""                 # 用户当前问题 / 题目文本
    user_answer: str = ""              # 学生作答（grade 流使用）
    reference_answer: str = ""         # 参考答案（用于批改对比）

    # ─── 节点产出 ───
    explanation: str = ""              # explain_concept 产出的讲解文本
    quiz: dict[str, Any] | None = None # 单道题（从题集中取第一道开放题）
    grading_result: GradingResult | None = None  # 批改结果
    next_plan: LearningPlan | None = None        # 下一步学习计划
    note_result: dict[str, Any] | None = None    # 创建的笔记 ID

    # ─── 掌握度追踪 ───
    mastery_before: float | None = None # 批改前掌握度
    mastery_after: float | None = None  # 批改后掌握度

    # ─── 错误处理 ───
    error_message: str | None = None   # 全局错误聚合（各节点通过 _merge_error 追加）

    # ─── 题集参数（quiz 流）───
    difficulty: str = "medium"         # easy / medium / hard
    quiz_set: dict[str, Any] | None = None  # 完整题集（topic / focus_reason / questions[]）
    question_count: int = 1            # 期望题目数量
    question_types: list[str] = Field(default_factory=list)  # ["multiple_choice","open_ended"]
    focus_mode: str = "weakest"        # "weakest"打薄|"manual"手动指定|"auto"自动

    # ─── 自适应补练循环 ─── <-- ★ 简历亮点：条件循环边
    # quiz_mode 区分正常出题（practice）和错题重练（remediation）。
    # remediation 由 replan_learning_path 触发 → 条件边回连 generate_quiz。
    quiz_mode: str = "practice"
    remediation_quiz: dict[str, Any] | None = None  # 补练产出的题目
    retry_count: int = 0              # 已完成补练次数（守卫，防死循环）
    max_retries: int = 1              # 单次 grade 流最大补练次数
    mastery_threshold: float = 0.6
    # ── HITL session ──
    # session_rounds tracks how many quiz→answer→grade cycles have been completed
    # within one HITL session. Combined with max_session_rounds (default 3)
    # to prevent infinite remediation loops.
    session_rounds: int = 0
    max_session_rounds: int = 3
    session_complete: bool = False  # True 表示 HITL 会话已结束


# ── 考试提交 & 批改（批量模式）──────────────────────────


class SubmittedAnswer(BaseModel):
    """一道题目的提交答案（批量考试模式）。"""

    question_id: str
    question: str
    question_type: str  # "multiple_choice" | "open_ended"
    options: list[str] = Field(default_factory=list)
    correct_option: str = ""
    reference_answer: str = ""
    student_answer: str = ""


class ExamSubmissionRequest(BaseModel):
    """批量考试提交的请求体。"""

    questions: list[SubmittedAnswer]
    topic: str = ""


class ExamResultItem(BaseModel):
    """单题批改结果（批量考试模式）。"""

    question_id: str
    score: int  # 选择题: 0 或 满分; 开放题: 1-10
    max_score: int
    feedback: str
    student_answer: str
    correct_answer: str = ""


class ExamGradingResponse(BaseModel):
    """批量考试批改的完整响应。"""

    results: list[ExamResultItem]
    total_score: int
    total_max: int
    summary: str
