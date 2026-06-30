from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import BackendSettings
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
        study_history_db_path=notes_dir / "study_history.db",
    )
    return StudyAgentService(settings)


def test_import_obsidian_vault_ingests_selected_subdirs(tmp_path):
    service = build_service(tmp_path)
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".obsidian" / "graph.md").write_text("# should be ignored", encoding="utf-8")
    (vault / "asset").mkdir(parents=True)
    (vault / "asset" / "image.md").write_text("# should be ignored too", encoding="utf-8")

    redis_note = vault / "02-Redis" / "01-基础" / "01-Redis 基础.md"
    redis_note.parent.mkdir(parents=True)
    redis_note.write_text(
        "---\n"
        "tags:\n"
        "  - redis\n"
        "  - cache\n"
        "---\n"
        "# Redis 基础\n\n"
        "Redis 是一个内存键值数据库，常用于缓存、计数器和排行榜。\n"
        "它支持 String、Hash、List、Set 和 ZSet，并且经常与 [[Memcached]] 对比。\n",
        encoding="utf-8",
    )

    agent_note = vault / "03-Agent" / "Context Engineering.md"
    agent_note.parent.mkdir(parents=True)
    agent_note.write_text(
        "# Context Engineering\n\n"
        "上下文工程强调围绕学习目标、证据、历史错误和当前任务来动态组织信息。\n",
        encoding="utf-8",
    )

    (vault / "draft.md").write_text("# root note\n\n这篇不应被这次选择性导入。", encoding="utf-8")

    result = service.import_obsidian_vault(
        vault_path=vault,
        include_subdirs=["02-Redis", "03-Agent"],
        min_chars=20,
        skip_existing=False,
    )

    assert result["imported_count"] == 2
    assert result["failed_count"] == 0
    assert result["skipped_count"] == 0
    assert any(item["classification"]["primary_topic"] == "Redis" for item in result["imported_items"])
    assert any(
        "Context" in item["classification"]["primary_topic"] or "Agent" in item["classification"]["primary_topic"]
        for item in result["imported_items"]
    )

    redis_hits = service.retriever.search("Memcached", top_k=3)
    assert redis_hits
    assert redis_hits[0]["source"].startswith("obsidian:02-Redis/")


def test_import_obsidian_vault_can_skip_existing_sources(tmp_path):
    service = build_service(tmp_path)
    vault = tmp_path / "vault"
    note_path = vault / "02-Redis" / "redis.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "# Redis\n\nRedis 适合做缓存，因为它是内存数据库，并支持多种数据结构。\n",
        encoding="utf-8",
    )

    first = service.import_obsidian_vault(
        vault_path=vault,
        include_subdirs=["02-Redis"],
        min_chars=10,
        skip_existing=False,
    )
    second = service.import_obsidian_vault(
        vault_path=vault,
        include_subdirs=["02-Redis"],
        min_chars=10,
        skip_existing=True,
    )

    assert first["imported_count"] == 1
    assert second["imported_count"] == 0
    assert second["skipped_count"] == 1
    assert second["skipped_items"][0]["reason"] == "already_imported"
