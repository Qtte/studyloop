from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.graph import StudyLoopGraph
from backend.app.config import BackendSettings
from backend.app.services.study_agent_service import StudyAgentService


def build_graph(tmp_path):
    notes_dir = tmp_path / "notes"
    settings = BackendSettings(
        use_mock_llm=True,
        qdrant_url=None,
        qdrant_api_key=None,
        embed_api_key=None,
        notes_dir=notes_dir,
        notes_index_path=notes_dir / "notes_index.json",
    )
    service = StudyAgentService(settings)
    service.ingest_material(
        content=(
            "Context engineering organizes the learner goal, the current task, and retrieved evidence.\n\n"
            "Evidence should support explanations, quizzes, and grading."
        ),
        source="manual",
    )
    return service, StudyLoopGraph(service)


def test_graph_explain_path(tmp_path):
    _service, graph = build_graph(tmp_path)
    result = graph.explain(question="Why do we need retrieved evidence?")
    assert result["answer"]
    assert result["context"]
    assert result["evidence"]
    assert result["auto_context"]["current_topic"]


def test_graph_quiz_path_returns_structured_quiz(tmp_path):
    _service, graph = build_graph(tmp_path)
    result = graph.quiz(
        prompt="Create a short-answer quiz about context engineering.",
        difficulty="medium",
    )
    assert result["quiz"]["question"]
    assert result["quiz"]["reference_answer"]
    assert result["quiz"]["difficulty"] == "medium"
    assert result["auto_context"]["current_topic"]


def test_graph_quiz_path_can_return_practice_set(tmp_path):
    service, graph = build_graph(tmp_path)
    service.state.mastery_by_topic["Context Engineering"] = 0.42
    result = graph.quiz(
        current_topic="Context Engineering",
        difficulty="medium",
        question_count=5,
        question_types=["multiple_choice", "open_ended"],
        focus_mode="manual",
    )
    assert result["quiz_set"]["topic"] == "Context Engineering"
    assert len(result["quiz_set"]["questions"]) == 5
    assert any(item["question_type"] == "multiple_choice" for item in result["quiz_set"]["questions"])
    assert any(item["question_type"] == "open_ended" for item in result["quiz_set"]["questions"])


def test_list_study_topics_returns_hierarchical_catalog(tmp_path):
    service, _graph = build_graph(tmp_path)
    service.state.mastery_by_topic["Context Engineering"] = 0.42

    catalog = service.list_study_topics()

    assert catalog["topics"]
    assert catalog["topic_tree"]
    assert catalog["recommended_topic"]
    assert catalog["recommended_topic_label"]
    assert any(item["level"] == 1 for item in catalog["topic_tree"])


def test_practice_set_normalization_recovers_invalid_multiple_choice_answer(tmp_path):
    service, _graph = build_graph(tmp_path)
    fallback = service._heuristic_practice_set(
        topic="Context Engineering",
        difficulty="medium",
        question_count=1,
        question_types=["multiple_choice"],
        prompt=None,
        focus_mode="manual",
        topics=service.list_study_topics()["topics"],
        evidence=service.retriever.search("Context Engineering", top_k=3),
    )
    normalized = service._normalize_practice_set(
        {
            "topic": "Context Engineering",
            "focus_reason": "manual",
            "difficulty": "medium",
            "question_count": 1,
            "question_types": ["multiple_choice"],
            "questions": [
                {
                    "question_id": "q1",
                    "question_type": "multiple_choice",
                    "question": "坏题目",
                    "options": ["选项一", "选项二", "选项三", "选项四"],
                    "correct_option": "",
                    "reference_answer": "这是一段解释，不是标准选项。",
                    "rubric": ["判断正确选项"],
                    "difficulty": "medium",
                }
            ],
        },
        fallback,
        difficulty="medium",
        question_count=1,
        question_types=["multiple_choice"],
    )
    question = normalized["questions"][0]
    assert question["question_type"] == "multiple_choice"
    assert question["options"] == fallback["questions"][0]["options"]
    assert question["correct_option"] == fallback["questions"][0]["correct_option"]


def test_graph_grade_path_updates_memory_and_plan(tmp_path):
    service, graph = build_graph(tmp_path)
    result = graph.grade(
        question="Why should evidence be included?",
        student_answer="It grounds the answer in the study material.",
    )
    assert result["result"]["score"] >= 0
    assert result["reference_answer"]
    assert result["mistake_record_note"]["id"]
    assert result["next_plan"]["next_actions"]
    assert service.note_tool.summary()["by_type"]["mistake_record"] >= 1


def test_grade_weak_mastery_triggers_remediation_loop(tmp_path):
    """Low mastery after grading should loop back to a remediation quiz."""
    service, graph = build_graph(tmp_path)
    result = graph.grade(
        question="Why should evidence be included?",
        student_answer="zzz qqq unrelated nonsense words here.",
    )
    assert result["mastery_after"] < 0.6
    assert result["retry_count"] == 1
    assert result["remediation_quiz"]
    assert result["remediation_quiz"]["question"]


def test_grade_strong_mastery_skips_remediation_loop(tmp_path):
    """Mastery at or above threshold should not trigger the remediation loop."""
    service, graph = build_graph(tmp_path)
    service.state.mastery_by_topic["Context Engineering"] = 0.85
    result = graph.grade(
        question="Why should evidence be included?",
        student_answer="It grounds the answer in the study material.",
    )
    assert result["mastery_after"] >= 0.6
    assert result["retry_count"] == 0
    assert result["remediation_quiz"] is None


def test_study_history_state_persists_across_service_restart(tmp_path):
    notes_dir = tmp_path / "notes"
    settings = BackendSettings(
        use_mock_llm=True,
        qdrant_url=None,
        qdrant_api_key=None,
        embed_api_key=None,
        notes_dir=notes_dir,
        notes_index_path=notes_dir / "notes_index.json",
        study_history_db_path=notes_dir / "study_history.db",
    )
    service = StudyAgentService(settings)
    service.state.mastery_by_topic["Context Engineering"] = 0.42
    service.save_last_grade({"score": 88, "mistake_type": "correct"})
    service.save_last_auto_context({"current_topic": "Context Engineering"})

    reloaded = StudyAgentService(settings)

    assert reloaded.state.mastery_by_topic["Context Engineering"] == 0.42
    assert reloaded.state.last_grade["score"] == 88
    assert reloaded.state.last_auto_context["current_topic"] == "Context Engineering"


def test_hitl_session_start_pauses_for_answer(tmp_path):
    """session_start 应在 generate_quiz 之后暂停并返回 thread_id + quiz。"""
    notes_dir = tmp_path / "notes"
    settings = BackendSettings(
        use_mock_llm=True,
        qdrant_url=None,
        qdrant_api_key=None,
        embed_api_key=None,
        notes_dir=notes_dir,
        notes_index_path=notes_dir / "notes_index.json",
    )
    service = StudyAgentService(settings)
    service.ingest_material(
        content="Context engineering organizes the learner goal, the current task, and retrieved evidence.",
        source="manual",
    )
    graph = StudyLoopGraph(service)
    result = graph.session_start(
        prompt="Explain context engineering with evidence.",
        difficulty="medium",
    )
    assert result["thread_id"]
    assert result["quiz"]
    assert result["quiz"]["question"]
    assert result["context"]


def test_hitl_session_resume_grades_and_completes(tmp_path):
    """session_start → session_resume 应完成批改并返回结果，补练回路也应正常工作。"""
    notes_dir = tmp_path / "notes"
    settings = BackendSettings(
        use_mock_llm=True,
        qdrant_url=None,
        qdrant_api_key=None,
        embed_api_key=None,
        notes_dir=notes_dir,
        notes_index_path=notes_dir / "notes_index.json",
    )
    service = StudyAgentService(settings)
    service.ingest_material(
        content="Context engineering organizes the learner goal, the current task, and retrieved evidence.",
        source="manual",
    )
    graph = StudyLoopGraph(service)

    start = graph.session_start(
        prompt="Explain context engineering with evidence.",
        difficulty="medium",
    )
    # First answer — mock LLM gives low score → triggers remediation quiz → pause
    r1 = graph.session_resume(
        thread_id=start["thread_id"],
        student_answer="zzz unrelated arbitrary answer words here.",
    )
    assert r1["session_complete"] is False
    assert r1["next_action"] == "answer"
    assert r1["result"]["score"] >= 0       # first grading happened
    assert r1["quiz"]                         # remediation quiz
    assert r1["session_rounds"] == 1

    # Second answer (remediation quiz) — same low answer, let the loop decide
    r2 = graph.session_resume(
        thread_id=start["thread_id"],
        student_answer="more unrelated useless random words answer here.",
    )
    # After second resume, session should end (max_session_rounds=3,
    # but mastery likely stays low — it just hits the rounds guard or mastery guard)
    assert r2["session_rounds"] >= 2
    assert r2["result"]["score"] >= 0
    # The session either completed normally or reached max rounds
    if not r2["session_complete"]:
        # Complete third round if needed
        r3 = graph.session_resume(
            thread_id=start["thread_id"],
            student_answer="nonsense text for third round.",
        )
        assert r3["session_complete"] is True or r3["next_action"] == "done"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
