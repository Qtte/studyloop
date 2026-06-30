from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.services.study_context_builder import StudyContextBuilder


def test_context_builder_includes_required_sections():
    builder = StudyContextBuilder()
    context = builder.build(
        learning_goal="Understand retrieval-augmented study support",
        current_task="Explain how evidence should be used",
        current_topic="Context Engineering",
        learner_state={"mastery": 0.55},
        evidence=[{"source": "chunk-1", "content": "Evidence should support the explanation."}],
        mistake_history=["Often forgets to cite evidence."],
        learning_notes=["Saved note about prompt structure."],
        conversation_context=["Learner asked for a simple explanation."],
        output_spec="Answer in plain language and mention evidence.",
    )

    required_sections = [
        "[Role & Policies]",
        "[Learning Goal]",
        "[Current Task]",
        "[Current Topic]",
        "[Learner State]",
        "[Evidence]",
        "[Mistake History]",
        "[Learning Notes]",
        "[Conversation Context]",
        "[Output Spec]",
    ]
    for section in required_sections:
        assert section in context


def test_context_builder_compress_preserves_headers():
    builder = StudyContextBuilder()
    long_text = "detail " * 5000
    context = builder.build(
        learning_goal=long_text,
        current_task=long_text,
        current_topic="Compression",
    )
    assert "[Learning Goal]" in context
    assert "[Current Topic]" in context
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
