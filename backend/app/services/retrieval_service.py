"""Retrieval backends for StudyLoop.

The backend prefers a persistent Qdrant vector retriever when configuration is
available, while preserving the original in-memory keyword retriever as a safe
fallback for tests and degraded local runs.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency guard
    OpenAI = None

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
except Exception:  # pragma: no cover - optional dependency guard
    QdrantClient = None
    Distance = None
    PointStruct = None
    VectorParams = None

if TYPE_CHECKING:
    from backend.app.config import BackendSettings


logger = logging.getLogger(__name__)


def _new_point_id() -> str:
    """生成符合 Qdrant 要求的点 ID。"""

    # Qdrant 只接受无符号整数或 UUID，这里统一使用标准 UUID 字符串。
    return str(uuid.uuid4())


@dataclass
class RetrievalDocument:
    """A chunk stored in a retriever backend."""

    content: str
    source: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str = field(default_factory=_new_point_id)


class SimpleKeywordRetriever:
    """Scores documents by keyword overlap with a small frequency bonus."""

    backend_name = "keyword_memory"

    def __init__(self) -> None:
        self._documents: list[RetrievalDocument] = []

    def add_documents(self, chunks: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
        added = []
        for chunk in chunks:
            document = self._coerce_document(chunk)
            self._documents.append(document)
            added.append(self._serialize(document))
        return added

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        scored = []
        for document in self._documents:
            doc_tokens = self._tokenize(document.content)
            overlap = query_tokens & doc_tokens
            if not overlap:
                continue
            frequency_bonus = sum(document.content.lower().count(token) for token in overlap)
            score = len(overlap) * 3 + frequency_bonus
            scored.append((score, document))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {**self._serialize(document), "score": score}
            for score, document in scored[:top_k]
        ]

    def count(self) -> int:
        return len(self._documents)

    @staticmethod
    def chunk_text(
        content: str,
        *,
        source: str = "manual",
        topic: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
        chunk_size: int = 600,
    ) -> list[dict[str, Any]]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
        if not paragraphs:
            return []

        chunks: list[dict[str, Any]] = []
        current: list[str] = []
        current_size = 0
        for paragraph in paragraphs:
            if current and current_size + len(paragraph) > chunk_size:
                chunks.append(
                    {
                        "content": "\n\n".join(current),
                        "source": source,
                        "metadata": {"topic": topic, **(extra_metadata or {})},
                    }
                )
                current = []
                current_size = 0
            current.append(paragraph)
            current_size += len(paragraph)
        if current:
            chunks.append(
                {
                    "content": "\n\n".join(current),
                    "source": source,
                    "metadata": {"topic": topic, **(extra_metadata or {})},
                }
            )
        return chunks

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    @staticmethod
    def _coerce_document(chunk: str | dict[str, Any]) -> RetrievalDocument:
        if isinstance(chunk, str):
            return RetrievalDocument(content=chunk)
        return RetrievalDocument(
            content=chunk["content"],
            source=chunk.get("source", "manual"),
            metadata=chunk.get("metadata", {}),
            doc_id=chunk.get("doc_id") or _new_point_id(),
        )

    @staticmethod
    def _serialize(document: RetrievalDocument) -> dict[str, Any]:
        return {
            "doc_id": document.doc_id,
            "content": document.content,
            "source": document.source,
            "metadata": document.metadata,
        }


class OpenAICompatibleTextEmbedder:
    """Minimal embedding client for OpenAI-compatible providers."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 30,
    ) -> None:
        if OpenAI is None:  # pragma: no cover - guarded by environment
            raise RuntimeError("openai package is required for vector retrieval")
        self.model = model
        self.base_url = self._normalize_base_url(base_url)
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        normalized = [self._normalize_text(text) for text in texts]
        response = self.client.embeddings.create(model=self.model, input=normalized)
        data = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in data]

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/embeddings"):
            normalized = normalized[: -len("/embeddings")]
        return normalized or "https://api.openai.com/v1"

    @staticmethod
    def _normalize_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return cleaned or " "


class QdrantVectorRetriever:
    """Persistent semantic retriever backed by Qdrant."""

    backend_name = "qdrant_vector"

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        collection_name: str,
        configured_vector_size: int,
        distance: str,
        timeout: float,
        embedder: OpenAICompatibleTextEmbedder,
    ) -> None:
        if QdrantClient is None or Distance is None or PointStruct is None or VectorParams is None:
            raise RuntimeError("qdrant-client package is required for vector retrieval")

        self.client = QdrantClient(url=url, api_key=api_key, timeout=timeout)
        self.collection_name = collection_name
        self.configured_vector_size = configured_vector_size
        self.timeout = int(timeout)
        self.embedder = embedder
        self.distance = self._resolve_distance(distance)

    def add_documents(self, chunks: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
        documents = [SimpleKeywordRetriever._coerce_document(chunk) for chunk in chunks]
        if not documents:
            return []

        vectors = self.embedder.embed_texts([document.content for document in documents])
        vector_size = len(vectors[0]) if vectors else self.configured_vector_size
        self._ensure_collection(vector_size)

        points = [
            PointStruct(
                id=document.doc_id,
                vector=vector,
                payload=self._build_payload(document),
            )
            for document, vector in zip(documents, vectors, strict=True)
        ]
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
            timeout=self.timeout,
        )
        return [SimpleKeywordRetriever._serialize(document) for document in documents]

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not str(query or "").strip():
            return []
        if not self._collection_exists():
            return []

        query_vector = self.embedder.embed_texts([query])[0]
        self._ensure_collection(len(query_vector))
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
            timeout=self.timeout,
        )
        points = getattr(response, "points", []) or []
        return [self._serialize_hit(point) for point in points]

    def count(self) -> int:
        if not self._collection_exists():
            return 0
        info = self.client.get_collection(self.collection_name)
        return int(getattr(info, "points_count", 0) or 0)

    @staticmethod
    def _resolve_distance(distance: str):
        normalized = str(distance or "cosine").strip().lower()
        mapping = {
            "cosine": Distance.COSINE,
            "dot": Distance.DOT,
            "euclid": Distance.EUCLID,
            "manhattan": Distance.MANHATTAN,
        }
        if normalized not in mapping:
            raise ValueError(f"Unsupported Qdrant distance: {distance}")
        return mapping[normalized]

    def _ensure_collection(self, vector_size: int) -> None:
        if self._collection_exists():
            info = self.client.get_collection(self.collection_name)
            current_size = self._extract_vector_size(info)
            if current_size is not None and current_size != vector_size:
                raise ValueError(
                    "Qdrant collection vector size mismatch: "
                    f"collection={current_size}, embedder={vector_size}. "
                    "Update EMBED_MODEL_NAME/QDRANT_VECTOR_SIZE or recreate the collection."
                )
            return

        if self.configured_vector_size and self.configured_vector_size != vector_size:
            logger.warning(
                "Configured Qdrant vector size %s differs from embedding size %s; "
                "creating collection %s with the embedding size.",
                self.configured_vector_size,
                vector_size,
                self.collection_name,
            )
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=vector_size, distance=self.distance),
            timeout=self.timeout,
        )

    def _collection_exists(self) -> bool:
        if hasattr(self.client, "collection_exists"):
            return bool(self.client.collection_exists(self.collection_name))
        collections = self.client.get_collections()
        items = getattr(collections, "collections", []) or []
        return any(getattr(item, "name", "") == self.collection_name for item in items)

    @staticmethod
    def _extract_vector_size(info: Any) -> int | None:
        config = getattr(info, "config", None)
        params = getattr(config, "params", None) if config else None
        vectors = getattr(params, "vectors", None) if params else None
        if vectors is None:
            return None
        size = getattr(vectors, "size", None)
        if size is not None:
            return int(size)
        if isinstance(vectors, dict):
            for value in vectors.values():
                if hasattr(value, "size"):
                    return int(value.size)
        return None

    @staticmethod
    def _build_payload(document: RetrievalDocument) -> dict[str, Any]:
        metadata = dict(document.metadata or {})
        return {
            "doc_id": document.doc_id,
            "content": document.content,
            "source": document.source,
            "topic": metadata.get("topic"),
            "title": metadata.get("title"),
            "category_path": metadata.get("category_path"),
            "tags": metadata.get("tags"),
            "metadata": metadata,
        }

    @staticmethod
    def _serialize_hit(hit: Any) -> dict[str, Any]:
        payload = getattr(hit, "payload", {}) or {}
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        for key in ("topic", "title", "category_path", "tags", "summary"):
            if key in payload and key not in metadata and payload.get(key) is not None:
                metadata[key] = payload.get(key)
        return {
            "doc_id": payload.get("doc_id") or str(getattr(hit, "id", "")),
            "content": payload.get("content", ""),
            "source": payload.get("source", "qdrant"),
            "metadata": metadata,
            "score": float(getattr(hit, "score", 0.0) or 0.0),
        }


class HybridRetriever:
    """Vector-first retriever with keyword fallback."""

    backend_name = "hybrid_qdrant_keyword"

    def __init__(
        self,
        *,
        vector_retriever: QdrantVectorRetriever | None = None,
        keyword_retriever: SimpleKeywordRetriever | None = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.keyword_retriever = keyword_retriever or SimpleKeywordRetriever()

    def add_documents(self, chunks: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
        keyword_added = self.keyword_retriever.add_documents(chunks)
        if not self.vector_retriever:
            return keyword_added
        try:
            vector_added = self.vector_retriever.add_documents(chunks)
            return vector_added or keyword_added
        except Exception as exc:  # pragma: no cover - depends on external services
            logger.warning("Qdrant indexing failed; falling back to keyword cache: %s", exc)
            return keyword_added

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self.vector_retriever:
            try:
                vector_results = self.vector_retriever.search(query, top_k=top_k)
                if vector_results:
                    return vector_results
            except Exception as exc:  # pragma: no cover - depends on external services
                logger.warning("Qdrant search failed; falling back to keyword cache: %s", exc)
        return self.keyword_retriever.search(query, top_k=top_k)

    def count(self) -> int:
        counts = [self.keyword_retriever.count()]
        if self.vector_retriever:
            try:
                counts.append(self.vector_retriever.count())
            except Exception as exc:  # pragma: no cover - depends on external services
                logger.warning("Qdrant count failed; using keyword cache count: %s", exc)
        return max(counts)


def create_retriever(settings: BackendSettings) -> HybridRetriever | SimpleKeywordRetriever:
    """Create the best available retriever for the current configuration."""

    keyword_retriever = SimpleKeywordRetriever()
    if not settings.has_vector_retrieval_config:
        return keyword_retriever

    try:
        embedder = OpenAICompatibleTextEmbedder(
            api_key=settings.embed_api_key or "",
            model=settings.embed_model_name,
            base_url=settings.embed_base_url,
            timeout=settings.qdrant_timeout,
        )
        vector_retriever = QdrantVectorRetriever(
            url=settings.qdrant_url or "",
            api_key=settings.qdrant_api_key or "",
            collection_name=settings.qdrant_collection,
            configured_vector_size=settings.qdrant_vector_size,
            distance=settings.qdrant_distance,
            timeout=settings.qdrant_timeout,
            embedder=embedder,
        )
        return HybridRetriever(
            vector_retriever=vector_retriever,
            keyword_retriever=keyword_retriever,
        )
    except Exception as exc:
        logger.warning("Vector retriever unavailable; using keyword cache only: %s", exc)
        return keyword_retriever
