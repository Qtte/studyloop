"""SQLite-backed storage for StudyLoop learning history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


class PersistentMasteryMap(dict[str, float]):
    """可持久化的掌握度映射，直接赋值时会自动回写到存储层。"""

    def __init__(
        self,
        initial: dict[str, float] | None = None,
        *,
        on_change: Callable[[str, float | None], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_change = on_change
        for key, value in (initial or {}).items():
            dict.__setitem__(self, str(key), float(value))

    def __setitem__(self, key: str, value: float) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        normalized_value = round(float(value), 3)
        super().__setitem__(normalized_key, normalized_value)
        if self._on_change:
            self._on_change(normalized_key, normalized_value)

    def __delitem__(self, key: str) -> None:
        normalized_key = str(key or "").strip()
        super().__delitem__(normalized_key)
        if self._on_change and normalized_key:
            self._on_change(normalized_key, None)

    def pop(self, key: str, default: Any = ...):  # type: ignore[override]
        normalized_key = str(key or "").strip()
        if default is ...:
            value = super().pop(normalized_key)
        else:
            value = super().pop(normalized_key, default)
        if self._on_change and normalized_key:
            self._on_change(normalized_key, None)
        return value

    def clear(self) -> None:
        keys = list(self.keys())
        super().clear()
        if self._on_change:
            for key in keys:
                self._on_change(str(key), None)

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        payload = dict(*args, **kwargs)
        for key, value in payload.items():
            self[key] = value


class SQLiteStudyHistoryStore:
    """统一管理学习笔记、错题记录和学习状态的 SQLite 存储。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        legacy_notes_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_notes_dir = (
            Path(legacy_notes_dir) if legacy_notes_dir else None
        )
        self._initialize_schema()
        self._migrate_legacy_notes_if_needed()

    def create_note(
        self,
        *,
        title: str,
        content: str,
        note_type: str = "learning_note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        note_id = uuid.uuid4().hex[:12]
        now = self._now()
        self._upsert_note(
            note_id=note_id,
            title=title,
            content=content,
            note_type=note_type,
            tags=tags,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        return self.read_note(note_id)

    def read_note(self, note_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, title, note_type, content, metadata_json, preview, created_at, updated_at
                FROM notes
                WHERE id = ?
                """,
                (note_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"Note '{note_id}' not found")
            return self._deserialize_note(conn, row)

    def update_note(
        self,
        *,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.read_note(note_id)
        self._upsert_note(
            note_id=note_id,
            title=title or current["title"],
            content=content if content is not None else current["content"],
            note_type=current["note_type"],
            tags=tags if tags is not None else current.get("tags", []),
            metadata=metadata if metadata is not None else current.get("metadata", {}),
            created_at=current["created_at"],
            updated_at=self._now(),
        )
        return self.read_note(note_id)

    def search_notes(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = [term.lower() for term in str(query or "").split() if term.strip()]
        if not terms:
            return []

        results: list[tuple[int, dict[str, Any]]] = []
        for note in self.list_notes(limit=10_000):
            haystack = " ".join(
                [
                    note.get("title", ""),
                    note.get("content", ""),
                    " ".join(note.get("tags", [])),
                    json.dumps(note.get("metadata", {}), ensure_ascii=False),
                ]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score > 0:
                results.append((score, note))

        results.sort(key=lambda item: (-item[0], item[1]["updated_at"]))
        return [note for _, note in results[:limit]]

    def list_notes(
        self,
        *,
        note_type: str | None = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT n.id, n.title, n.note_type, n.content, n.metadata_json, n.preview, n.created_at, n.updated_at
                FROM notes AS n
                WHERE (? IS NULL OR n.note_type = ?)
                  AND (
                    ? IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM note_tags AS t
                        WHERE t.note_id = n.id AND t.tag = ?
                    )
                  )
                ORDER BY n.updated_at DESC
                LIMIT ?
                """,
                (note_type, note_type, tag, tag, int(limit)),
            ).fetchall()
            return [self._deserialize_note(conn, row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            count = int(
                conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            )
            by_type_rows = conn.execute(
                """
                SELECT note_type, COUNT(*) AS count
                FROM notes
                GROUP BY note_type
                ORDER BY note_type
                """
            ).fetchall()
            tag_rows = conn.execute(
                """
                SELECT tag, COUNT(*) AS count
                FROM note_tags
                GROUP BY tag
                ORDER BY count DESC, tag ASC
                LIMIT 10
                """
            ).fetchall()
            recent_rows = conn.execute(
                """
                SELECT id
                FROM notes
                ORDER BY updated_at DESC
                LIMIT 5
                """
            ).fetchall()

        return {
            "count": count,
            "by_type": {
                str(row["note_type"]): int(row["count"]) for row in by_type_rows
            },
            "top_tags": [
                [str(row["tag"]), int(row["count"])] for row in tag_rows
            ],
            "recent_note_ids": [str(row["id"]) for row in recent_rows],
        }

    def load_state_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            mastery_rows = conn.execute(
                """
                SELECT topic, mastery
                FROM topic_mastery
                ORDER BY topic ASC
                """
            ).fetchall()

        return {
            "mastery_by_topic": {
                str(row["topic"]): float(row["mastery"]) for row in mastery_rows
            },
            "last_grade": self._load_state_value("last_grade"),
            "last_auto_context": self._load_state_value("last_auto_context"),
        }

    def set_mastery(self, topic: str, mastery: float | None) -> None:
        normalized_topic = str(topic or "").strip()
        if not normalized_topic:
            return

        with self._connect() as conn:
            if mastery is None:
                conn.execute(
                    "DELETE FROM topic_mastery WHERE topic = ?",
                    (normalized_topic,),
                )
                return
            conn.execute(
                """
                INSERT INTO topic_mastery (topic, mastery, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(topic) DO UPDATE SET
                    mastery = excluded.mastery,
                    updated_at = excluded.updated_at
                """,
                (normalized_topic, round(float(mastery), 3), self._now()),
            )

    def save_last_grade(self, payload: dict[str, Any] | None) -> None:
        self._save_state_value("last_grade", payload)

    def save_last_auto_context(self, payload: dict[str, Any] | None) -> None:
        self._save_state_value("last_auto_context", payload)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            # 统一存放笔记、标签和学习状态，避免多份索引之间不一致。
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    note_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    preview TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_notes_type
                ON notes(note_type);

                CREATE INDEX IF NOT EXISTS idx_notes_updated_at
                ON notes(updated_at DESC);

                CREATE TABLE IF NOT EXISTS note_tags (
                    note_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY(note_id, tag),
                    FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_note_tags_tag
                ON note_tags(tag);

                CREATE TABLE IF NOT EXISTS topic_mastery (
                    topic TEXT PRIMARY KEY,
                    mastery REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS study_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _upsert_note(
        self,
        *,
        note_id: str,
        title: str,
        content: str,
        note_type: str,
        tags: list[str] | None,
        metadata: dict[str, Any] | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        normalized_tags = sorted(
            {
                str(tag).strip()
                for tag in (tags or [])
                if str(tag or "").strip()
            }
        )
        normalized_metadata = metadata or {}
        preview = str(content or "")[:160]

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    id, title, note_type, content, metadata_json,
                    preview, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    note_type = excluded.note_type,
                    content = excluded.content,
                    metadata_json = excluded.metadata_json,
                    preview = excluded.preview,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    note_id,
                    str(title).strip(),
                    str(note_type).strip() or "learning_note",
                    str(content or "").strip(),
                    json.dumps(normalized_metadata, ensure_ascii=False),
                    preview,
                    created_at,
                    updated_at,
                ),
            )
            conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
            if normalized_tags:
                conn.executemany(
                    "INSERT INTO note_tags (note_id, tag) VALUES (?, ?)",
                    [(note_id, tag) for tag in normalized_tags],
                )

    def _deserialize_note(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        tag_rows = conn.execute(
            """
            SELECT tag
            FROM note_tags
            WHERE note_id = ?
            ORDER BY tag ASC
            """,
            (row["id"],),
        ).fetchall()
        metadata_raw = row["metadata_json"] or "{}"
        metadata = json.loads(metadata_raw)
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "note_type": str(row["note_type"]),
            "tags": [str(tag_row["tag"]) for tag_row in tag_rows],
            "metadata": metadata if isinstance(metadata, dict) else {},
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "preview": str(row["preview"] or ""),
            "content": str(row["content"] or ""),
            # 兼容旧前端/调试代码里可能读取 path 字段的情况。
            "path": None,
        }

    def _save_state_value(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO study_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(value, ensure_ascii=False),
                    self._now(),
                ),
            )

    def _load_state_value(self, key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value_json
                FROM study_state
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def _migrate_legacy_notes_if_needed(self) -> None:
        if self.legacy_notes_dir is None:
            return
        if self._note_count() > 0:
            return

        index_path = self.legacy_notes_dir / "notes_index.json"
        if not index_path.exists():
            return

        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(index, dict):
            return

        for note_id, record in index.items():
            if not isinstance(record, dict):
                continue
            note_path = self.legacy_notes_dir / str(record.get("path") or "")
            if not note_path.exists():
                continue
            metadata, body = self._read_legacy_note_file(note_path)
            merged = dict(record)
            merged.update(metadata)

            self._upsert_note(
                note_id=str(merged.get("id") or note_id),
                title=str(merged.get("title") or note_path.stem),
                content=body,
                note_type=str(merged.get("note_type") or "learning_note"),
                tags=merged.get("tags") if isinstance(merged.get("tags"), list) else [],
                metadata=(
                    merged.get("metadata")
                    if isinstance(merged.get("metadata"), dict)
                    else {}
                ),
                created_at=str(merged.get("created_at") or self._now()),
                updated_at=str(merged.get("updated_at") or self._now()),
            )

    def _note_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM notes").fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _read_legacy_note_file(path: Path) -> tuple[dict[str, Any], str]:
        raw = path.read_text(encoding="utf-8")
        if raw.startswith("---\n"):
            _, front_matter, body = raw.split("---\n", 2)
            metadata = yaml.safe_load(front_matter) or {}
            body = body.strip()
        else:
            metadata = {}
            body = raw.strip()
        if body.startswith("# "):
            lines = body.splitlines()
            body = "\n".join(lines[2:]).strip() if len(lines) >= 2 else ""
        return metadata, body

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
