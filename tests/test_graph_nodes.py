from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
from backend.app.config import BackendSettings
from backend.app.schemas import GradingResult, LearningPlan, StudyLoopState
from backend.app.services.study_agent_service import StudyAgentService


def build_service(tmp_path):
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
            "Context engineering keeps learning goals, evidence, and past mistakes in a stable structure.\n\n"
            "Retrieved evidence should support the answer instead of adding irrelevant details."
        ),
        source="manual",
        topic="Context Engineering",
    )
    service.note_tool.create_note(
        title="Context note",
        content="Use evidence and learner history together.",
        tags=["Context Engineering"],
        metadata={"topic": "Context Engineering"},
    )
    service.note_tool.create_note(
        title="Past mistake",
        content="Learner forgot to cite evidence.",
        note_type="mistake_record",
        tags=["Context Engineering", "missing_evidence"],
        metadata={"score": 55, "topic": "Context Engineering"},
    )
    return service


def test_parse_user_intent_detects_grade_path():
    state = StudyLoopState(current_topic="Topic", question="Why?", user_answer="My answer")
    updated = parse_user_intent(state)
    assert updated["intent"] == "grade"
    assert updated["concept_id"]


def test_retrieve_and_context_nodes_collect_study_data(tmp_path):
    service = build_service(tmp_path)
    state = StudyLoopState(
        intent="explain",
        learning_goal="Understand context engineering",
        current_topic="Context Engineering",
        current_task="Explain the concept clearly.",
        question="Why does the agent need evidence?",
    )
    state = state.model_copy(update=parse_user_intent(state))
    state = state.model_copy(update=retrieve_materials(state, service=service))
    state = state.model_copy(update=retrieve_learning_notes(state, service=service))
    state = state.model_copy(update=build_study_context(state, service=service))

    assert state.retrieved_evidence
    assert state.learning_notes
    assert "[Evidence]" in state.study_context
    assert "[Learning Notes]" in state.study_context


def test_explain_quiz_grade_and_replan_nodes_return_structured_outputs(tmp_path):
    service = build_service(tmp_path)
    base_state = StudyLoopState(
        learning_goal="Understand context engineering",
        current_topic="Context Engineering",
        current_task="Explain the concept clearly.",
        question="Why should retrieved evidence be included?",
        reference_answer="Because it grounds the explanation in the study material.",
        user_answer="It helps keep the answer grounded in the material.",
    )
    state = base_state.model_copy(update=parse_user_intent(base_state))
    state = state.model_copy(update=retrieve_materials(state, service=service))
    state = state.model_copy(update=retrieve_learning_notes(state, service=service))
    state = state.model_copy(update=build_study_context(state, service=service))

    explanation_update = explain_concept(state.model_copy(update={"intent": "explain"}), service=service)
    assert explanation_update["explanation"]

    quiz_update = generate_quiz(state.model_copy(update={"intent": "quiz", "difficulty": "medium"}), service=service)
    assert quiz_update["quiz"]["question"]
    assert quiz_update["quiz"]["rubric"]

    grade_state = state.model_copy(update={"intent": "grade"})
    grade_state = grade_state.model_copy(update=grade_answer(grade_state, service=service))
    assert GradingResult.model_validate(grade_state.grading_result).score >= 0

    grade_state = grade_state.model_copy(update=update_memory(grade_state, service=service))
    assert grade_state.note_result is not None
    assert grade_state.mastery_after is not None

    grade_state = grade_state.model_copy(update=replan_learning_path(grade_state, service=service))
    assert LearningPlan.model_validate(grade_state.next_plan).next_actions


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
