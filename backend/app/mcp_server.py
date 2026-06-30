"""
StudyLoop MCP Server —— 将学习闭环暴露为 MCP 工具，供 Claude Desktop 等调用。

═══════════════════════════════════════════════════════════════════════════════
设计说明
═══════════════════════════════════════════════════════════════════════════════

FastMCP 提供 sse_app() 返回一个 Starlette ASGI 应用，可以直接 mount 到
FastAPI 上，共享同一端口和依赖注入。

暴露的工具（5 个学习方法 + 1 个查询工具）：
  1. study_explain     → 讲解概念（带证据检索）
  2. study_quiz        → 生成练习题
  3. study_grade       → 批改答案并更新掌握度
  4. study_retrieve    → 搜索知识库
  5. study_state       → 查询学习进度
  6. study_notes       → 搜索学习笔记

每个工具都复用 StudyAgentService 的现有方法，不重复实现业务逻辑。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.app.services.study_agent_service import StudyAgentService

# 全局 MCP 实例
mcp = FastMCP(
    "StudyLoop",
    instructions="""StudyLoop 是一个学习 Agent 系统，提供以下能力：
- 讲解概念：对指定知识点生成有证据支撑的讲解
- 生成练习：根据主题和难度生成练习题
- 批改答案：对学生作答评分、归类错误、更新掌握度
- 搜索知识库：检索已导入的学习材料
- 查询进度：获取当前学习状态和掌握度
- 搜索笔记：按关键词或类型搜索学习笔记
请使用这些工具来辅助学习。""",
)

# 在 mount 时注入 service 实例
_service: StudyAgentService | None = None


def init_mcp(service: StudyAgentService) -> FastMCP:
    """将 StudyAgentService 注入 MCP 工具层。"""
    global _service
    _service = service
    return mcp


def _ensure_service() -> StudyAgentService:
    if _service is None:
        raise RuntimeError("MCP not initialized. Call init_mcp(service) first.")
    return _service


@mcp.tool(
    name="study_explain",
    description="讲解某个知识点，结合检索证据生成结构化解释。",
)
def study_explain(
    question: str,
    current_topic: str | None = None,
) -> str:
    """讲解指定知识点，返回 Markdown 格式的解释。"""
    service = _ensure_service()
    result = service.explain(
        question=question,
        current_topic=current_topic,
    )
    return result.get("answer", "") or ""


@mcp.tool(
    name="study_quiz",
    description="生成练习题，支持难度和题型控制。",
)
def study_quiz(
    prompt: str = "",
    current_topic: str | None = None,
    difficulty: str = "medium",
    question_count: int = 1,
) -> str:
    """生成练习题，返回 Markdown 格式的题目。"""
    service = _ensure_service()
    seed_text = prompt or current_topic or "请生成一道练习题。"
    result = service.quiz(
        prompt=seed_text,
        current_topic=current_topic,
        difficulty=difficulty,
        question_count=question_count,
        focus_mode="manual" if current_topic else "weakest",
    )
    if result.get("quiz_set") and result["quiz_set"].get("questions"):
        return _format_quiz_set(result["quiz_set"])
    if result.get("quiz"):
        return _format_quiz(result["quiz"])
    return "暂无题目可用。"


@mcp.tool(
    name="study_grade",
    description="批改学生对某道题的回答，返回评分、反馈和掌握度变化。",
)
def study_grade(
    question: str,
    student_answer: str,
    current_topic: str | None = None,
    reference_answer: str | None = None,
) -> str:
    """批改答案。"""
    service = _ensure_service()
    result = service.grade(
        question=question,
        student_answer=student_answer,
        current_topic=current_topic,
        reference_answer=reference_answer,
    )
    return _format_grade_result(result)


@mcp.tool(
    name="study_retrieve",
    description="从知识库中检索与查询最相关的资料片段。",
)
def study_retrieve(
    query: str,
    top_k: int = 5,
) -> str:
    """检索知识库资源。"""
    service = _ensure_service()
    results = service.retriever.search(query, top_k=min(top_k, 20))
    if not results:
        return "未找到相关资料。"
    lines = []
    for i, item in enumerate(results, 1):
        content = item.get("content", "")[:300]
        source = item.get("source", "未知来源")
        score = item.get("score", "N/A")
        lines.append(f"**{i}. [{source}]（相关度: {score}）**\n{content}\n")
    return "\n".join(lines)


@mcp.tool(
    name="study_state",
    description="获取当前学习状态，包括资料数量、笔记数量、各主题掌握度。",
)
def study_state() -> str:
    """查询学习进度。"""
    service = _ensure_service()
    state = service.get_state()
    mastery = state.get("mastery_by_topic", {})

    lines = [
        f"📚 **学习进度**",
        f"- 已索引资料切片: {state.get('documents_indexed', 0)}",
        f"- 学习笔记: {state.get('notes', {}).get('count', 0)}",
        f"- LLM 模式: {state.get('llm_mode', 'unknown')}",
        f"",
    ]

    if mastery:
        lines.append("**主题掌握度（按掌握度升序）**：")
        sorted_topics = sorted(mastery.items(), key=lambda x: x[1])
        for topic, score in sorted_topics:
            bar = _mastery_bar(score)
            lines.append(f"- {topic}: {bar} {int(score * 100)}%")

    return "\n".join(lines) if lines else "暂无学习数据。"


@mcp.tool(
    name="study_notes",
    description="搜索学习笔记，支持按类型过滤。",
)
def study_notes(
    query: str = "",
    note_type: str = "",
    limit: int = 10,
) -> str:
    """搜索学习笔记。"""
    service = _ensure_service()
    if query:
        notes = service.note_tool.search_notes(query, limit=min(limit, 50))
    else:
        notes = service.note_tool.list_notes(
            note_type=note_type or None,
            limit=min(limit, 50),
        )

    if not notes:
        return "未找到相关笔记。"

    lines = []
    for note in notes[:limit]:
        title = note.get("title", "未命名")
        note_type_str = note.get("note_type", "unknown")
        preview = note.get("preview", "")[:120]
        lines.append(f"**[{note_type_str}] {title}**\n{preview}\n")

    return "\n".join(lines)


# ── 辅助格式化函数 ──


def _format_quiz(quiz: dict[str, Any]) -> str:
    question = quiz.get("question", "")
    difficulty = quiz.get("difficulty", "medium")
    reference = quiz.get("reference_answer", "")
    return f"**题目**（难度: {difficulty}）\n\n{question}\n\n**参考答案**\n{reference}"


def _format_quiz_set(quiz_set: dict[str, Any]) -> str:
    topic = quiz_set.get("topic", "")
    questions = quiz_set.get("questions", [])
    lines = [f"# {topic}\n"]
    for i, q in enumerate(questions, 1):
        q_type = q.get("question_type", "open_ended")
        question = q.get("question", "")
        ref = q.get("reference_answer", "")
        diff = q.get("difficulty", "medium")
        lines.append(f"## {i}. [{q_type}]（难度: {diff}）\n{question}")
        if ref:
            lines.append(f"\n**参考答案**\n{ref}")
    return "\n\n".join(lines)


def _format_grade_result(result: dict[str, Any]) -> str:
    grade = result.get("result") or {}
    score = grade.get("score", "N/A")
    mistake_type = grade.get("mistake_type", "unknown")
    feedback = grade.get("feedback", "")
    mastery_before = result.get("mastery_before", "N/A")
    mastery_after = result.get("mastery_after", "N/A")
    next_plan = result.get("next_plan") or {}
    plan_summary = next_plan.get("summary", "")

    lines = [
        f"**批改结果**\n",
        f"- 得分: {score}/100",
        f"- 错误类型: {mistake_type}",
        f"- 掌握度变化: {_mastery_val(mastery_before)} → {_mastery_val(mastery_after)}",
        f"",
    ]
    if feedback:
        lines.append(f"**反馈**\n{feedback}\n")
    if plan_summary:
        lines.append(f"**下一步建议**\n{plan_summary}")

    remediation = result.get("remediation_quiz")
    if remediation:
        lines.append(f"\n**补练题**\n{remediation.get('question', '')}")

    return "\n".join(lines)


def _mastery_val(val: Any) -> str:
    if val is None:
        return "N/A"
    return f"{int(float(val) * 100)}%"


def _mastery_bar(score: float) -> str:
    filled = max(1, min(10, int(score * 10)))
    empty = 10 - filled
    return "█" * filled + "░" * empty
