"""
LangGraph nodes for the StudyLoop learning workflow.

═══════════════════════════════════════════════════════════════════════════════
节点设计原则
═══════════════════════════════════════════════════════════════════════════════

1. 每个节点是一个纯函数：`fn(state, *, service) -> dict[str, Any]`
   - 接收完整 state（只读），返回 state 子集 dict（将被 LangGraph 合并回 state）
   - service 通过 partial 注入（在 graph._build_graph 里预绑定）

2. 节点内部不直接修改 state（pydantic 对象），而是返回 dict。
   这是 LangGraph 的不可变更新模式：
     state 是 pydantic BaseModel → 不可原地修改 → 返回 dict → 框架合并

3. 节点之间通过 state 字段通信，不是返回值。
   例如：grade_answer 把 grading_result 字段写入 state 返回，
   update_memory 从 state 读 grading_result 字段，不通过函数参数传递。

4. 每个节点只关心自己需要读写的字段，返回的 dict 越小越好。
   框架会 shallow merge（浅合并），只覆盖返回的 key。

═══════════════════════════════════════════════════════════════════════════════
JSON 解析的自愈策略（_parse_structured_output）
═══════════════════════════════════════════════════════════════════════════════

LLM 输出的 JSON 非常不可靠 —— 可能被 markdown fences 包裹、可能夹杂文本、
可能字段名拼错、可能用单引号。本项目采用多层自愈策略：

1. 去除 markdown code fences（```json ... ```）
2. 在文本中寻找第一个 { 到最后一个 } 的跨度（从嵌入文本中抠 JSON）
3. 尝试 json.loads 直接解析
4. 如果失败，再尝试 Pydantic model_validate_json（类型转换 + 校验）
5. 如果 Pydantic 也失败，再用 json.loads 包一层
6. 全失败 → fallback_payload（启发式结果）
7. 记录 error_message 到 state，但不中断流程

这个策略的核心思想是 "fail gracefully"：
搞不定 JSON 就降级到预设答案，不抛异常中止整个图执行。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.app.agents.prompts import (
    ANSWER_GRADER_PROMPT,
    MAIN_SYSTEM_PROMPT,
    REPLANNER_PROMPT,
)
from backend.app.schemas import GradingResult, LearningPlan, StudyLoopState
from backend.app.services.study_agent_service import StudyAgentService


ModelT = TypeVar("ModelT", bound=BaseModel)


# ═══════════════════════════════════════════════════════════════════════════
# 阶段一：前置节点（所有 intent 共享）
# ═══════════════════════════════════════════════════════════════════════════


def parse_user_intent(state: StudyLoopState) -> dict[str, Any]:
    """
    入口节点 —— 解析意图并初始化基础状态。

    这是图的第一个节点（START → parse_user_intent），负责：
    1. 判断意图（explain / quiz / grade）—— 如果调用方已明确传入则直接使用
    2. 设置当前任务（current_task）
    3. 生成概念 slug（concept_id）—— 用于笔记索引、文件名
    4. 确保 learner_state 有默认 mastery（0.5）

    意图推断逻辑（_infer_intent）：
      - user_answer 非空 → grade（用户提交了答案，想要批改）
      - 文本含 quiz/question/题/练习 等关键词 → quiz
      - 其余 → explain（默认：把输入当问题，生成讲解）
    """
    intent = state.intent or _infer_intent(state)
    current_task = state.current_task or _default_task_for_intent(intent)
    concept_id = state.concept_id or _slugify(
        state.current_topic or state.question or "general-topic"
    )
    learner_state = dict(state.learner_state or {})
    learner_state.setdefault("mastery", learner_state.get("mastery", 0.5))
    return {
        "intent": intent,
        "current_task": current_task,
        "concept_id": concept_id,
        "learner_state": learner_state,
    }


def retrieve_materials(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    检索相关学习资料。

    从全局 retriever（内存/关键词，或 Qdrant 向量）中搜索与主题相关的材料切片。
    explain 模式多取一些（top_k=5），quiz/grade 少取（3），因为出题/批改对
    证据的依赖比讲解弱一点。

    返回的 retrieved_evidence 格式：[{doc_id, content, source, metadata, score}, ...]
    """
    query = (
        state.question
        or state.current_topic
        or state.learning_goal
        or state.current_task
    )
    top_k = 5 if state.intent == "explain" else 3
    return {"retrieved_evidence": service.retriever.search(query, top_k=top_k)}


def retrieve_learning_notes(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    检索与该主题相关的学习笔记和错题记录。

    笔记的两条检索路径：
    1. search_notes —— 全文关键词搜索（覆盖标题、内容、标签）
    2. list_notes(note_type="mistake_record") —— 按类型过滤错题记录

    将历史错题注入上下文，帮助 LLM 在批改/出题时关注学习者的薄弱点。
    如果主题级的错题没找到，回退到全局最新错题（不传 tag）。
    """
    topic_key = state.current_topic or state.concept_id
    learning_notes = (
        service.note_tool.search_notes(topic_key, limit=3) if topic_key else []
    )
    mistake_history = (
        service.note_tool.list_notes(
            note_type="mistake_record", tag=topic_key, limit=3
        )
        if topic_key
        else []
    )
    if not mistake_history:
        mistake_history = service.note_tool.list_notes(
            note_type="mistake_record", limit=3
        )
    return {"learning_notes": learning_notes, "mistake_history": mistake_history}


def build_study_context(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    拼装 10 段标准化学习上下文（GSSC 流程）。

    这是整个前置链的核心节点：将分散的检索结果、笔记、状态统一组装成
    一段结构化的上下文文本，直接灌给下游节点的 LLM prompt。

    10 段上下文结构（由 StudyContextBuilder 生成）：
      [Role & Policies]      —— 系统角色与行为策略
      [Learning Goal]        —— 学习目标
      [Current Task]         —— 当前任务
      [Current Topic]        —— 当前主题
      [Learner State]        —— 学习者状态（掌握度、得分历史）
      [Evidence]             —— 检索到的证据材料
      [Mistake History]      —— 历史错题
      [Learning Notes]       —— 学习笔记
      [Conversation Context] —— 对话历史
      [Output Spec]          —— 输出格式要求（JSON schema / Markdown 规范）

    output_spec 根据 intent 不同会变化：
      explain → "Answer in prose with explanation, evidence, next step"
      quiz    → "Return strict JSON with question, reference_answer, rubric, difficulty"
      grade   → "Return strict JSON with score, mistake_type, feedback, evidence_used, suggested_note"
    """
    study_context = service.context_builder.build(
        learning_goal=state.learning_goal,
        current_task=state.current_task,
        current_topic=state.current_topic,
        learner_state=state.learner_state,
        evidence=state.retrieved_evidence,
        mistake_history=state.mistake_history,
        learning_notes=state.learning_notes,
        conversation_context=state.conversation_context,
        output_spec=_output_spec_for_intent(state.intent),
        role_policies=MAIN_SYSTEM_PROMPT,
    )
    return {"study_context": study_context}


# ═══════════════════════════════════════════════════════════════════════════
# 阶段二：三分支节点（由条件路由决定执行哪个）
# ═══════════════════════════════════════════════════════════════════════════


def explain_concept(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    讲解概念 —— 用 LLM 生成一个有证据支撑的 Markdown 解释。

    这是三个分支里最简单的：把 10 段上下文 + 学习者问题 → LLM → 返回解释文本。
    不需要结构化 JSON，直接拿 LLM 返回的字符串作为 explanation。
    """
    learner_question = (
        state.question or f"Please explain {state.current_topic}."
    )
    prompt = (
        f"MODE: explain\n{state.study_context}\n\n"
        f"Learner Question: {learner_question}"
    )
    return {"explanation": _invoke_llm_text(service, MAIN_SYSTEM_PROMPT, prompt)}


def generate_quiz(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    生成练习题 —— 委托 service.generate_practice_set 产出题集。

    为什么委托给 service 方法而不是自己拼 prompt？
    ────────────────────────────────────────
    service.generate_practice_set 封装了大量工程细节：
      - 基于 focus_mode 的主题选择（打薄/手动/自动）
      - 启发式题集回退（当 mock LLM 或真实 LLM 挂了）
      - 选择题选项规范化（_normalize_choice_options）
      - 正确选项解析（_resolve_correct_option 处理 A/B/C 标签等各种格式）
      - 不合格 LLM 输出的修复（_normalize_practice_set 兜底）

    节点只做薄封装：
      1. 从 state 取参数 → 调 service 方法
      2. 取题集中第一道开放题写入 state.quiz（兼容 grade"同步到批改区"）
      3. 如果是补练模式（quiz_mode="remediation"）→ 额外产出 remediation_quiz 并递增 retry_count

    ★ 补练模式的关键设计：retry_count 在 generate_quiz 递增而非 replan 递增
    replan 条件判断 retry_count<max_retries 时设 quiz_mode=remediation，
    本节点执行后才递增 retry_count。这样路由函数在 replan 后看到的 retry_count
    还没被递增，不会误杀第一次补练。

    返回字段：
      - quiz:              单道题 dict（第一道开放题）
      - quiz_set:          完整题集 dict（topic / focus_reason / questions[]）
      - remediation_quiz:  补练题（仅在 remediation 模式，与 quiz 相同内容但语义区分）
      - retry_count:       在 remediation 模式下 +1
    """
    quiz_set_payload = service.generate_practice_set(
        prompt=state.question or None,
        current_topic=state.current_topic or None,
        current_task=state.current_task or None,
        difficulty=state.difficulty,
        question_count=max(1, state.question_count),
        question_types=state.question_types or None,
        focus_mode=state.focus_mode,
    )
    quiz_set = quiz_set_payload.get("quiz_set") or {}
    quiz = quiz_set_payload.get("quiz") or {}
    update = {
        "quiz": quiz,
        "quiz_set": quiz_set,
        "error_message": _merge_error(state.error_message, None),
    }
    # 补练模式：把同题写入 remediation_quiz，并递增循环计数器
    if state.quiz_mode == "remediation":
        update["remediation_quiz"] = quiz
        update["retry_count"] = state.retry_count + 1
    return update


def grade_answer(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    批改学生作答 —— 用 LLM 评估回答质量并归类错误。

    输入（从 state 读取）：
      - question:          题目文本
      - reference_answer:  参考答案
      - user_answer:       学生作答
      - study_context:     10 段上下文（含证据/错题/笔记）

    输出（写入 state）：
      - grading_result:   {score, mistake_type, feedback, evidence_used, suggested_note}

    批改的核心：参考答案的质量。
    答案由 graph.grade 入口方法预生成（generate_reference_answer），
    在学生未提供参考答案时用检索证据拼接启发式答案。
    这样即使没有标准答案，批改也有参照系。
    """
    prompt = (
        f"{ANSWER_GRADER_PROMPT}\n\n{state.study_context}\n\n"
        f"Question: {state.question}\n"
        f"Reference Answer: {state.reference_answer or state.current_topic}\n"
        f"Student Answer: {state.user_answer}"
    )
    raw_text = _invoke_llm_text(service, MAIN_SYSTEM_PROMPT, prompt)
    grading_result, error_message = _parse_structured_output(
        raw_text,
        GradingResult,
        fallback_payload={
            "score": 60,
            "mistake_type": "shallow_answer",
            "feedback": "你的回答覆盖了部分内容，但还需要补上核心定义、关键机制和更具体的应用说明。",
            "evidence_used": [
                item.get("source", "retriever")
                for item in state.retrieved_evidence[:2]
            ],
            "suggested_note": "回到核心概念，结合检索到的证据重写一版更完整的回答。",
        },
    )
    return {
        "grading_result": grading_result.model_dump(),
        "error_message": _merge_error(state.error_message, error_message),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 阶段三：记忆与规划（仅 grade 流）
# ═══════════════════════════════════════════════════════════════════════════


def update_memory(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    记忆回写 —— 更新掌握度并创建错题笔记。

    这是"学习闭环"区别于普通问答的关键节点：
    1. 根据批改分数 + 错误类型更新掌握度（MasteryService.update_mastery）
       - score≥90: +0.12             (优秀)
       - 75≤score<90: +0.07          (良好)
       - 60≤score<75: +0.03          (及格)
       - 40≤score<60: -0.03          (不及格)
       - score<40: -0.08             (差)
       再叠加 mistake_type 修正（correct:+0.03, concept_confusion/missing:-0.05 等）

    2. 创建错题笔记（note_type="mistake_record"）
       - 记录题目、学生作答、反馈、建议
       - 标签：当前主题 + 错误类型
       - 元数据：分数、主题、掌握度变化

    这样下次检索时，同一主题的错题历史会被加载进 study_context，
    让批改和出题能关注到学习者的持续薄弱点。
    """
    if state.grading_result is None:
        return {
            "error_message": _merge_error(
                state.error_message, "Missing grading_result for update_memory"
            )
        }

    grading_result = _coerce_grading_result(state.grading_result)
    old_mastery = service.get_mastery(
        state.current_topic,
        float(state.learner_state.get("mastery", 0.5)),
    )
    new_mastery = service.mastery_service.update_mastery(
        old_mastery,
        grading_result.score,
        grading_result.mistake_type,
    )
    # 写回 service 的内存状态（注意：当前是内存存储，重启归零）
    service.state.mastery_by_topic[state.current_topic] = new_mastery
    service.save_last_grade(grading_result.model_dump())

    note = service.note_tool.create_note(
        title=f"Mistake record: {state.current_topic}",
        content=(
            f"Question: {state.question}\n\n"
            f"Student answer: {state.user_answer}\n\n"
            f"Feedback: {grading_result.feedback}\n\n"
            f"Suggested note: {grading_result.suggested_note}"
        ),
        note_type="mistake_record",
        tags=[state.current_topic, grading_result.mistake_type],
        metadata={
            "score": grading_result.score,
            "topic": state.current_topic,
            "mastery_before": old_mastery,
            "mastery_after": new_mastery,
        },
    )

    learner_state = dict(state.learner_state)
    learner_state["mastery"] = new_mastery
    learner_state["last_score"] = grading_result.score
    learner_state["last_mistake_type"] = grading_result.mistake_type

    return {
        "grading_result": grading_result.model_dump(),
        "note_result": note,
        "mastery_before": old_mastery,
        "mastery_after": new_mastery,
        "learner_state": learner_state,
        # session_rounds 用于 HITL 多轮重置流程；
        # 每次批改完成记为一轮，在 graph 中读 session_rounds 控制最多多少轮。
        "session_rounds": state.session_rounds + 1,
    }


def replan_learning_path(
    state: StudyLoopState, *, service: StudyAgentService
) -> dict[str, Any]:
    """
    学习重规划 —— 生成下一步学习建议，并可能触发补练回路 ★。

    这是整个图的最后一个业务节点，但流程不一定在此结束：
    ┌─ 如果 mastery_after ≥ 0.6（达标）→ 路由到 END，正常结束
    └─ 如果 mastery_after < 0.6（不达标）→ 路由到 generate_quiz 补练一道题 ★

    触发的三个条件（缺一不可）：
      1. intent == "grade"              —— 只在批改流触发（explain/quiz 不走这）
      2. mastery_after < 0.6            —— 掌握度低于阈值
      3. retry_count < max_retries      —— 循环守卫（默认一次 grade 最多补练一次）

    触发时的动作（replan 节点做）：
    - 设置 quiz_mode = "remediation"   → generate_quiz 节点会读这个标志
    - question_count = 1               → 只出一题
    - focus_mode = "manual"            → 强制锁定当前主题（不打薄/不自动跳）

    ★ 注意：这里不递增 retry_count！
    递增由 generate_quiz 在 remediation 模式下完成。
    这是踩过的坑：如果这里先增再路由，路由看到 retry_count=1 而 max_retries=1，
    1<1=False → 直接走 done，第一次补练就被误杀。
    """
    grading_result = (
        _coerce_grading_result(state.grading_result)
        if state.grading_result is not None
        else None
    )
    grading_json = (
        grading_result.model_dump_json(indent=2)
        if grading_result is not None
        else "{}"
    )
    prompt = (
        f"{REPLANNER_PROMPT}\n\n{state.study_context}\n\n"
        f"Latest grading result:\n{grading_json}\n\n"
        f"Current mastery after update: {state.mastery_after}"
    )
    raw_text = _invoke_llm_text(service, MAIN_SYSTEM_PROMPT, prompt)
    next_plan, error_message = _parse_structured_output(
        raw_text,
        LearningPlan,
        fallback_payload={
            "summary": f"继续巩固 {state.current_topic}，优先补齐本次暴露出的薄弱点。",
            "focus_areas": [
                grading_result.mistake_type
                if grading_result
                else "core concept"
            ],
            "next_actions": [
                "重新阅读相关材料中的定义与关键机制。",
                "用自己的话重写一次答案，并与参考答案对照。",
                "完成一道新的迁移应用题。",
            ],
            "recommended_question_types": [
                "definition",
                "application",
                "evidence-based explanation",
            ],
        },
    )
    update = {
        "next_plan": next_plan.model_dump(),
        "error_message": _merge_error(state.error_message, error_message),
    }

    # ★ 自适应补练回路的触发点
    # 当掌握度低于阈值时，设置 quiz_mode 标志，让后续的条件路由
    # （_route_after_replan）选择 "remediate" → 回连 generate_quiz
    if (
        state.intent == "grade"
        and state.mastery_after is not None
        and state.mastery_after < state.mastery_threshold
        and state.retry_count < state.max_retries
    ):
        update.update(
            {
                "quiz_mode": "remediation",
                "question_count": 1,
                "focus_mode": "manual",
            }
        )
    return update


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════


def _infer_intent(state: StudyLoopState) -> str:
    """
    从 state 的信息推断用户意图。

    推断优先级：
      1. user_answer 非空 → grade（用户在提交作答，想批改）
      2. 文本含出题相关关键词（quiz / question / 题 / 练习）→ quiz
      3. 否则 → explain（默认：用户的问题需要讲解）

    这是从"用户只发了一段话"到"图知道走哪条分支"的中间层。
    """
    task_text = " ".join(
        [
            state.current_task or "",
            state.question or "",
            state.learning_goal or "",
        ]
    ).lower()
    if state.user_answer.strip():
        return "grade"
    if any(
        token in task_text
        for token in ["quiz", "question", "题", "练习", "测试"]
    ):
        return "quiz"
    return "explain"


def _default_task_for_intent(intent: str) -> str:
    """为每个 intent 生成默认的任务描述（输给 context builder 的 [Current Task] 段）。"""
    if intent == "quiz":
        return "Generate a short quiz."
    if intent == "grade":
        return "Grade the learner answer and classify the mistake type."
    return "Explain the concept clearly."


def _slugify(text: str) -> str:
    """
    将文本转换为 URL/文件名 安全的 slug。

    例如 "Context Engineering 101" → "context-engineering-101"
    用作 concept_id，索引笔记和错题记录。
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "study-concept"


def _output_spec_for_intent(intent: str | None) -> str:
    """
    为不同 intent 返回不同的 Output Spec 指令。

    Output Spec 是 10 段上下文的最后一段，告诉 LLM 应该按什么格式输出。
    这是 context engineering 的关键：格式约束作为上下文的一部分，比系统 prompt 更有效。
    """
    if intent == "quiz":
        return (
            "Return strict JSON with keys question, reference_answer, rubric, difficulty."
        )
    if intent == "grade":
        return (
            "Return strict JSON with score, mistake_type, feedback, evidence_used, suggested_note."
        )
    return "Answer in prose with a short explanation, cited evidence, and a next step."


def _invoke_llm_text(
    service: StudyAgentService, system_prompt: str, user_prompt: str
) -> str:
    """
    同步调用 LLM 并返回纯文本。

    这是最低层的 LLM 调用封装。不处理 JSON 解析、不处理 fallback。
    返回的文本可能包含 markdown fences，由上层 _parse_structured_output 处理。
    """
    response = service.llm.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    return response.content.strip()


def _parse_structured_output(
    raw_text: str,
    schema: type[ModelT],
    fallback_payload: dict[str, Any] | None = None,
) -> tuple[ModelT, str | None]:
    """
    ★ 结构化输出的自愈解析 —— 多层 JSON 提取 + Pydantic 校验。

    这个函数是"agentic self-healing"思想的体现：
    LLM 输出不可靠 → 尽最大努力提取 → 搞不定就降级到 fallback，但不崩溃。

    解析层次：
      1. 从 raw_text 生成多个 JSON 候选（去除 fences + 抠 { } 跨度）
      2. 对每个候选：先 Pydantic model_validate_json，再 json.loads 兜底
      3. 全失败 → 用 fallback_payload 构造一个合法的 Pydantic 模型
      4. 用 fallback 时返回一条 error_message → 汇入 state.error_message

    返回值：
      (解析后的 Pydantic 模型实例, 错误信息字符串 | None)
    """
    for candidate in _json_candidates(raw_text):
        if not candidate:
            continue
        try:
            return schema.model_validate_json(candidate), None
        except Exception:
            try:
                return schema.model_validate(json.loads(candidate)), None
            except Exception:
                continue
    if fallback_payload is not None:
        return (
            schema.model_validate(fallback_payload),
            f"Structured output repair fallback used for {schema.__name__}.",
        )
    raise ValueError(
        f"Failed to parse {schema.__name__} from model output: {raw_text}"
    )


def _json_candidates(raw_text: str) -> list[str]:
    """
    从 LLM 原始文本中提取 JSON 候选。

    策略：
      1. 去除 markdown code fences（```json ... ```）
      2. 完整文本作为一个候选
      3. 找到第一个 { 到最后一个 } 的跨度作为第二个候选（处理嵌入文本中的 JSON）
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.M
        ).strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    return candidates


def _coerce_grading_result(
    value: GradingResult | dict[str, Any],
) -> GradingResult:
    """
    统一 GradingResult 的类型 —— 接受 Pydantic 模型或 dict，总是返回模型。

    为什么需要这个？
    state 的 grading_result 字段类型是 GradingResult | None，但由于
    state 在节点间混合（有时候节点返回 model_dump 的 dict，langgraph 合并后
    可能还是 dict），下游节点需要安全地拿到 .score / .mistake_type 等属性。
    """
    if isinstance(value, GradingResult):
        return value
    return GradingResult.model_validate(value)


def _merge_error(existing: str | None, new_message: str | None) -> str | None:
    """
    合并错误信息 —— 用 ` | ` 拼接多段错误。

    这样整个图的错误信息会累积（如 "parse fallback | missing evidence"），
    最后在 API 响应中一并暴露，方便调试。
    """
    if existing and new_message:
        return f"{existing} | {new_message}"
    return existing or new_message
