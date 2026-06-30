"""LangGraph orchestration for the StudyLoop learning workflow."""

from __future__ import annotations

from functools import partial
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.app.agents.nodes import (
    build_study_context,
    explain_concept,
    generate_quiz,
    grade_answer,
    parse_user_intent,
    replan_learning_path,
    retrieve_learning_notes,
    retrieve_materials,
    update_memory,
)
from backend.app.schemas import StudyLoopState
from backend.app.services.study_agent_service import StudyAgentService


class StudyLoopGraph:
    """统一编排 explain、quiz、grade 三条学习流程，并提供 HITL 交互式会话。"""

    def __init__(self, service: StudyAgentService):
        self.service = service
        self.checkpointer = MemorySaver()
        self.graph = self._build_graph()
        self.hitl_graph = self._build_hitl_graph()

    def _build_graph(self):
        """构建一次并复用的 LangGraph。"""
        workflow = StateGraph(StudyLoopState)

        workflow.add_node("parse_user_intent", parse_user_intent)
        workflow.add_node(
            "retrieve_materials",
            partial(retrieve_materials, service=self.service),
        )
        workflow.add_node(
            "retrieve_learning_notes",
            partial(retrieve_learning_notes, service=self.service),
        )
        workflow.add_node(
            "build_study_context",
            partial(build_study_context, service=self.service),
        )
        workflow.add_node(
            "explain_concept",
            partial(explain_concept, service=self.service),
        )
        workflow.add_node(
            "generate_quiz",
            partial(generate_quiz, service=self.service),
        )
        workflow.add_node(
            "grade_answer",
            partial(grade_answer, service=self.service),
        )
        workflow.add_node(
            "update_memory",
            partial(update_memory, service=self.service),
        )
        workflow.add_node(
            "replan_learning_path",
            partial(replan_learning_path, service=self.service),
        )

        workflow.add_edge(START, "parse_user_intent")
        workflow.add_edge("parse_user_intent", "retrieve_materials")
        workflow.add_edge("retrieve_materials", "retrieve_learning_notes")
        workflow.add_edge("retrieve_learning_notes", "build_study_context")

        workflow.add_conditional_edges(
            "build_study_context",
            _route_after_context,
            {
                "explain": "explain_concept",
                "quiz": "generate_quiz",
                "grade": "grade_answer",
            },
        )

        workflow.add_edge("explain_concept", END)
        workflow.add_edge("generate_quiz", END)
        workflow.add_edge("grade_answer", "update_memory")
        workflow.add_edge("update_memory", "replan_learning_path")

        workflow.add_conditional_edges(
            "replan_learning_path",
            _route_after_replan,
            {
                "remediate": "generate_quiz",
                "done": END,
            },
        )

        return workflow.compile(name="studyloop_langgraph")

    def invoke(
        self, payload: dict[str, Any] | StudyLoopState
    ) -> StudyLoopState:
        """执行编排后的 graph，并统一返回 StudyLoopState。"""
        state_payload = (
            payload.model_dump() if isinstance(payload, StudyLoopState) else payload
        )
        result = self.graph.invoke(state_payload)
        return StudyLoopState.model_validate(result)

    def explain(
        self,
        *,
        question: str,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        learner_state: dict[str, Any] | None = None,
        conversation_context: list[Any] | None = None,
    ) -> dict[str, Any]:
        """概念讲解入口。"""
        brief = self.service.prepare_study_brief(
            seed_text=question,
            intent="explain",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
            conversation_context=conversation_context,
        )
        self.service.save_last_auto_context(brief)
        state = self.invoke(
            {
                "intent": "explain",
                "question": question,
                "learning_goal": learning_goal or brief["learning_goal"],
                "current_topic": current_topic or brief["current_topic"],
                "current_task": current_task or brief["current_task"],
                "learner_state": learner_state or {},
                "conversation_context": conversation_context or [],
            }
        )
        return {
            "answer": state.explanation,
            "context": state.study_context,
            "evidence": state.retrieved_evidence,
            "auto_context": brief,
            "error": state.error_message,
        }

    def quiz(
        self,
        *,
        prompt: str | None = None,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        difficulty: str = "medium",
        question_count: int = 1,
        question_types: list[str] | None = None,
        focus_mode: str = "weakest",
        learner_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成练习题入口。"""
        seed_text = (
            prompt
            or current_topic
            or learning_goal
            or "请围绕最近的学习主题生成一道练习题。"
        )
        brief = self.service.prepare_study_brief(
            seed_text=seed_text,
            intent="quiz",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
        )
        self.service.save_last_auto_context(brief)
        state = self.invoke(
            {
                "intent": "quiz",
                "question": prompt or "",
                "learning_goal": learning_goal or brief["learning_goal"],
                "current_topic": current_topic or brief["current_topic"],
                "current_task": current_task or brief["current_task"],
                "learner_state": learner_state or {},
                "difficulty": difficulty,
                "question_count": question_count,
                "question_types": question_types or [],
                "focus_mode": focus_mode,
                "quiz_mode": "practice",
            }
        )
        return {
            "quiz": state.quiz,
            "quiz_set": state.quiz_set,
            "context": state.study_context,
            "evidence": state.retrieved_evidence,
            "auto_context": brief,
            "error": state.error_message,
        }

    def grade(
        self,
        *,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        question: str,
        student_answer: str,
        reference_answer: str | None = None,
    ) -> dict[str, Any]:
        """答案批改入口。"""
        brief = self.service.prepare_study_brief(
            seed_text=question,
            intent="grade",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task="Assess the learner answer for completeness, accuracy, and application.",
        )
        self.service.save_last_auto_context(brief)
        evidence = self.service.retriever.search(
            question or brief["current_topic"],
            top_k=3,
        )
        reference = self.service.generate_reference_answer(
            question=question,
            current_topic=current_topic or brief["current_topic"],
            evidence=evidence,
            reference_answer=reference_answer,
        )
        state = self.invoke(
            {
                "intent": "grade",
                "learning_goal": learning_goal or brief["learning_goal"],
                "current_topic": current_topic or brief["current_topic"],
                "current_task": brief["current_task"],
                "question": question,
                "user_answer": student_answer,
                "reference_answer": reference["reference_answer"],
                "learner_state": {
                    "mastery": self.service.get_mastery(
                        current_topic or brief["current_topic"],
                        0.5,
                    )
                },
            }
        )
        return {
            "result": (
                state.grading_result.model_dump()
                if state.grading_result
                else None
            ),
            "mastery_before": state.mastery_before,
            "mastery_after": state.mastery_after,
            "mistake_record_note": state.note_result,
            "next_plan": (
                state.next_plan.model_dump() if state.next_plan else None
            ),
            "reference_answer": reference["reference_answer"],
            "reference_rubric": reference["rubric"],
            "auto_context": brief,
            "remediation_quiz": state.remediation_quiz,
            "retry_count": state.retry_count,
            "mastery_threshold": state.mastery_threshold,
            "error": state.error_message,
        }

    # ═══════════════════════════════════════════════════════════════════
    # HITL 交互式学习会话
    # ═══════════════════════════════════════════════════════════════════

    def _build_hitl_graph(self):
        """构建 HITL（Human-in-the-Loop）学习会话图。

        与主图（_build_graph）的核心区别：
        ────────────────────────────────
        1. 图结构是**线性**的（没有 explain/quiz/grade 三岔口）：
           START → generate_quiz → [PAUSE] → grade_answer → replan → (loop or END)
           所有输入走的都是同一条路径。

        2. compile 时设置了 checkpointer + interrupt_after=["generate_quiz"]：
           - generate_quiz 节点产题后图自动暂停，等待学生作答
           - 调用 session_resume 时注入 user_answer 并恢复执行
           - 补练时 generate_quiz 会再次暂停（interrupt_after 对每轮都生效）

        3. 条件边 _route_hitl_replan 替代 _route_after_replan：
           - retry_count 守卫仍有效（防 generate_quiz 内部补练死循环）
           - session_rounds 控制总轮次（默认最多 3 轮）
           - 掌握度达标或轮次耗尽 → 结束会话

        效果：一次会话可以经历"出题→答题→批改→补练→再答题→再批改→结束"
        的多个循环，每次出题后都会暂停等待人类输入。
        """
        workflow = StateGraph(StudyLoopState)

        # 注入 service（与主图共享 partial 模式）
        nodes_cfg = [
            ("parse_user_intent", parse_user_intent),
            ("retrieve_materials", partial(retrieve_materials, service=self.service)),
            ("retrieve_learning_notes", partial(retrieve_learning_notes, service=self.service)),
            ("build_study_context", partial(build_study_context, service=self.service)),
            ("generate_quiz", partial(generate_quiz, service=self.service)),
            ("grade_answer", partial(grade_answer, service=self.service)),
            ("update_memory", partial(update_memory, service=self.service)),
            ("replan_learning_path", partial(replan_learning_path, service=self.service)),
        ]
        for name, fn in nodes_cfg:
            workflow.add_node(name, fn)

        # 线性链（不分支）——出题后暂停等人，resume 后批改+重规划
        workflow.add_edge(START, "parse_user_intent")
        workflow.add_edge("parse_user_intent", "retrieve_materials")
        workflow.add_edge("retrieve_materials", "retrieve_learning_notes")
        workflow.add_edge("retrieve_learning_notes", "build_study_context")
        workflow.add_edge("build_study_context", "generate_quiz")
        workflow.add_edge("generate_quiz", "grade_answer")
        workflow.add_edge("grade_answer", "update_memory")
        workflow.add_edge("update_memory", "replan_learning_path")

        # 循环条件：掌握度不足且未达轮次上限 → 继续出补练题等人
        workflow.add_conditional_edges(
            "replan_learning_path",
            _route_hitl_replan,
            {
                "continue_quiz": "generate_quiz",
                "complete": END,
            },
        )

        return workflow.compile(
            name="studyloop_hitl",
            checkpointer=self.checkpointer,
            interrupt_after=["generate_quiz"],
        )

    def session_start(
        self,
        *,
        prompt: str | None = None,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        difficulty: str = "medium",
        question_count: int = 1,
        question_types: list[str] | None = None,
        focus_mode: str = "weakest",
    ) -> dict[str, Any]:
        """启动一个 HITL 交互式学习会话。

        1. 生成学习简报（prepare_study_brief）
        2. 以 thread_id 初始化图调用 → 走到 generate_quiz → 暂停
        3. 返回 thread_id + quiz（前端展示题目等学生作答）

        前端拿到返回后要：
          - 展示 quiz
          - 收学生答案
          - 调 session_resume(thread_id, student_answer)
        """
        thread_id = str(uuid4())
        seed_text = prompt or current_topic or learning_goal or "请出一道练习题。"
        brief = self.service.prepare_study_brief(
            seed_text=seed_text,
            intent="quiz",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
        )
        self.service.save_last_auto_context(brief)
        config = {"configurable": {"thread_id": thread_id}}

        initial = {
            "intent": "grade",
            "question": prompt or "",
            "learning_goal": learning_goal or brief["learning_goal"],
            "current_topic": current_topic or brief["current_topic"],
            "current_task": current_task or brief["current_task"],
            "learner_state": {},
            "difficulty": difficulty,
            "question_count": max(1, question_count),
            "question_types": question_types or [],
            "focus_mode": focus_mode,
            "quiz_mode": "practice",
            "session_rounds": 0,
        }

        # 首次调用，走到 interrupt_after=["generate_quiz"] 处暂停
        self.hitl_graph.invoke(initial, config)

        snapshot = self.hitl_graph.get_state(config)
        state = StudyLoopState.model_validate(snapshot.values)

        return {
            "thread_id": thread_id,
            "quiz": state.quiz,
            "quiz_set": state.quiz_set,
            "context": state.study_context,
            "evidence": state.retrieved_evidence,
            "auto_context": brief,
        }

    def session_resume(
        self,
        *,
        thread_id: str,
        student_answer: str,
        reference_answer: str | None = None,
    ) -> dict[str, Any]:
        """恢复一个暂停的 HITL 会话，注入学生作答。

        执行流程：
        1. update_state 注入 user_answer（+ 可选 reference_answer）
        2. invoke(None) 恢复图执行
        3. 图从 generate_quiz 继续 → grade_answer → update_memory → replan
        4. 如果掌握度 < 阈值 + 轮次 < 上限：
           回连 generate_quiz → 再次暂停（前端再次拿到补练题）
           前端展示补练题后调 session_resume 进入下一轮
        5. 如果掌握度达标或轮次耗尽：
           会话结束（session_complete=True）

        返回字段：
          - session_complete: bool（True 表示会话结束，前端应展示结果）
          - result:          批改结果 {score, mistake_type, feedback, ...}
          - mastery_before / mastery_after
          - next_plan:       学习计划 {summary, focus_areas, next_actions}
          - session_rounds:  已完成轮次
          - quiz:            当 session_complete=False 时为补练题（继续答题）
          - next_action:     "answer"（继续答题）| "done"（结束）
        """
        config = {"configurable": {"thread_id": thread_id}}

        # 注入学生答案到 state
        self.hitl_graph.update_state(config, {"user_answer": student_answer})
        if reference_answer:
            self.hitl_graph.update_state(config, {"reference_answer": reference_answer})

        # 恢复图执行
        self.hitl_graph.invoke(None, config)

        # 读取当前 state
        snapshot = self.hitl_graph.get_state(config)
        state = StudyLoopState.model_validate(snapshot.values)
        remaining = snapshot.next  # 下一批要执行的节点

        # 如果 next 为空（无待执行节点）或 session_complete 被标记 → 会话结束
        is_complete = state.session_complete or not remaining
        result = {
            "session_complete": is_complete,
            "result": (
                state.grading_result.model_dump()
                if state.grading_result
                else None
            ),
            "mastery_before": state.mastery_before,
            "mastery_after": state.mastery_after,
            "next_plan": (
                state.next_plan.model_dump() if state.next_plan else None
            ),
            "mistake_record_note": state.note_result,
            "session_rounds": state.session_rounds,
            "error": state.error_message,
        }

        if not is_complete and state.quiz:
            result["quiz"] = state.quiz
            result["next_action"] = "answer"
        else:
            result["next_action"] = "done"

        return result


def _route_after_context(state: StudyLoopState) -> str:
    """根据意图决定进入 explain、quiz 还是 grade 分支。"""
    return state.intent or "explain"


def _route_after_replan(state: StudyLoopState) -> str:
    """掌握度不足时回连到补练出题，否则正常结束。"""
    if (
        state.intent == "grade"
        and state.mastery_after is not None
        and state.mastery_after < state.mastery_threshold
        and state.retry_count < state.max_retries
    ):
        return "remediate"
    return "done"


def _route_hitl_replan(state: StudyLoopState) -> str:
    """HITL 模式重规划路由——掌握度不足且未达轮次上限则继续出补练题。"""
    mastery_ok = (
        state.mastery_after is not None
        and state.mastery_after >= state.mastery_threshold
    )
    rounds_exhausted = state.session_rounds >= state.max_session_rounds
    if mastery_ok or rounds_exhausted:
        return "complete"
    return "continue_quiz"
