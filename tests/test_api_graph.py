from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.agents.graph import StudyLoopGraph
from backend.app.config import BackendSettings
from backend.app.services.study_agent_service import StudyAgentService


def build_test_client(tmp_path, monkeypatch):
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
    graph = StudyLoopGraph(service)
    monkeypatch.setattr(main_module, "get_study_service", lambda: service)
    monkeypatch.setattr(main_module, "get_study_graph", lambda: graph)
    return TestClient(main_module.app)


def test_api_endpoints_use_graph(tmp_path, monkeypatch):
    client = build_test_client(tmp_path, monkeypatch)

    ingest_response = client.post(
        "/knowledge/ingest",
        json={
            "content": "Context engineering uses evidence, goals, and learner history.",
            "source": "manual",
        },
    )
    assert ingest_response.status_code == 200
    ingest_payload = ingest_response.json()
    assert ingest_payload["documents_indexed"] >= 1
    assert ingest_payload["classification"]["primary_topic"]
    assert ingest_payload["classification"]["category_path"]

    explain_response = client.post(
        "/study/explain",
        json={
            "question": "Why is evidence useful?",
        },
    )
    assert explain_response.status_code == 200
    explain_payload = explain_response.json()
    assert explain_payload["answer"]
    assert explain_payload["auto_context"]["current_topic"]

    quiz_response = client.post(
        "/study/quiz",
        json={
            "prompt": "Create one short-answer question about context engineering.",
        },
    )
    assert quiz_response.status_code == 200
    quiz_payload = quiz_response.json()
    assert quiz_payload["quiz"]["question"]
    assert quiz_payload["quiz"]["reference_answer"]

    quiz_set_response = client.post(
        "/study/quiz",
        json={
            "current_topic": "Context Engineering",
            "question_count": 4,
            "question_types": ["multiple_choice", "open_ended"],
            "focus_mode": "manual",
        },
    )
    assert quiz_set_response.status_code == 200
    quiz_set_payload = quiz_set_response.json()
    assert quiz_set_payload["quiz_set"]["topic"] == "Context Engineering"
    assert len(quiz_set_payload["quiz_set"]["questions"]) == 4

    exam_submission_response = client.post(
        "/study/exam/submit",
        json={
            "topic": "Context Engineering",
            "questions": [
                {
                    "question_id": item["question_id"],
                    "question": item["question"],
                    "question_type": item["question_type"],
                    "options": item["options"],
                    "correct_option": "A" if item["question_type"] == "multiple_choice" else "",
                    "reference_answer": item["reference_answer"],
                    "student_answer": "A"
                    if item["question_type"] == "multiple_choice"
                    else "It organizes goals, tasks, and retrieved evidence so the answer stays grounded.",
                }
                for item in quiz_set_payload["quiz_set"]["questions"]
            ],
        },
    )
    assert exam_submission_response.status_code == 200
    exam_payload = exam_submission_response.json()
    assert len(exam_payload["results"]) == 4
    assert exam_payload["total_max"] == 30
    assert exam_payload["total_score"] >= 20
    assert exam_payload["summary"]

    chat_response = client.post(
        "/chat",
        json={
            "message": "Summarize why retrieved evidence improves answers.",
            "save_memory": True,
        },
    )
    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    assert chat_payload["answer"]
    assert chat_payload["memory_summary"]["summary"]
    assert chat_payload["saved_note"]["id"]

    grade_response = client.post(
        "/study/grade",
        json={
            "question": "Why should evidence be included?",
            "student_answer": "It keeps the answer grounded.",
        },
    )
    assert grade_response.status_code == 200
    payload = grade_response.json()
    assert payload["result"]["score"] >= 0
    assert payload["reference_answer"]
    assert payload["mistake_record_note"]["id"]
    assert payload["next_plan"]["summary"]

    topics_response = client.get("/study/topics")
    assert topics_response.status_code == 200
    topics_payload = topics_response.json()
    assert topics_payload["topics"]
    assert topics_payload["topic_tree"]
    assert topics_payload["recommended_topic"]
    assert topics_payload["recommended_topic_label"]


def test_api_can_import_obsidian_vault(tmp_path, monkeypatch):
    client = build_test_client(tmp_path, monkeypatch)
    vault = tmp_path / "vault"
    note_path = vault / "02-Redis" / "redis.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "# Redis\n\nRedis 常用于缓存、排行榜和计数器，并支持多种数据结构。\n",
        encoding="utf-8",
    )

    response = client.post(
        "/knowledge/import-obsidian",
        json={
            "vault_path": str(vault),
            "include_subdirs": ["02-Redis"],
            "min_chars": 10,
            "skip_existing": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["imported_items"][0]["relative_path"] == "02-Redis/redis.md"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
