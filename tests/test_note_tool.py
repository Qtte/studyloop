from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.services.learning_note_tool import LearningNoteTool


def test_note_tool_create_read_update_search_and_summary(tmp_path):
    tool = LearningNoteTool(tmp_path / "notes")

    created = tool.create_note(
        title="Spaced Repetition",
        content="Use spaced repetition to revisit material at expanding intervals.",
        tags=["memory", "revision"],
        metadata={"topic": "learning science"},
    )
    assert created["title"] == "Spaced Repetition"

    loaded = tool.read_note(created["id"])
    assert "expanding intervals" in loaded["content"]

    updated = tool.update_note(
        note_id=created["id"],
        content="Updated content about spaced repetition and retrieval practice.",
        tags=["memory", "retrieval"],
    )
    assert "retrieval practice" in updated["content"]

    matches = tool.search_notes("retrieval practice")
    assert matches
    assert matches[0]["id"] == created["id"]

    notes = tool.list_notes()
    assert len(notes) == 1

    summary = tool.summary()
    assert summary["count"] == 1
    assert summary["by_type"]["learning_note"] == 1

    reloaded_tool = LearningNoteTool(tmp_path / "notes")
    reloaded = reloaded_tool.read_note(created["id"])
    assert "retrieval practice" in reloaded["content"]
    assert (tmp_path / "notes" / "study_history.db").exists()
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
