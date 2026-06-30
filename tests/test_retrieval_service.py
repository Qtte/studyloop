from pathlib import Path
import sys
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import BackendSettings
from backend.app.services.retrieval_service import (
    OpenAICompatibleTextEmbedder,
    SimpleKeywordRetriever,
    create_retriever,
)


def test_retriever_adds_documents_and_ranks_overlap():
    retriever = SimpleKeywordRetriever()
    retriever.add_documents(
        [
            {"content": "Python uses indentation to define code blocks.", "source": "doc-a"},
            {"content": "Retrieval practice strengthens long-term memory.", "source": "doc-b"},
            {"content": "Context engineering improves agent prompting.", "source": "doc-c"},
        ]
    )

    results = retriever.search("retrieval memory", top_k=2)
    assert len(results) == 1
    assert results[0]["source"] == "doc-b"


def test_chunk_text_splits_large_content():
    content = "Para 1\n\n" + ("A" * 400) + "\n\n" + ("B" * 400)
    chunks = SimpleKeywordRetriever.chunk_text(
        content,
        source="manual",
        topic="chunking",
        chunk_size=500,
    )
    assert len(chunks) >= 2


def test_embed_base_url_normalization_strips_endpoint_suffix():
    normalized = OpenAICompatibleTextEmbedder._normalize_base_url(
        "https://api.siliconflow.cn/v1/embeddings"
    )
    assert normalized == "https://api.siliconflow.cn/v1"


def test_create_retriever_falls_back_without_vector_config():
    settings = BackendSettings(
        qdrant_url=None,
        qdrant_api_key=None,
        embed_api_key=None,
    )
    retriever = create_retriever(settings)
    assert isinstance(retriever, SimpleKeywordRetriever)


def test_retriever_generates_qdrant_compatible_uuid_ids():
    retriever = SimpleKeywordRetriever()
    added = retriever.add_documents(
        [{"content": "A study chunk for UUID validation.", "source": "doc-a"}]
    )
    uuid.UUID(added[0]["doc_id"])


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
