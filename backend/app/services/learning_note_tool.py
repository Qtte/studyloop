"""Learning note storage implemented as a HelloAgents tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.errors import ToolErrorCode
from hello_agents.tools.response import ToolResponse

from .study_history_store import SQLiteStudyHistoryStore


class LearningNoteTool(Tool):
    """基于 SQLite 的学习笔记工具。"""

    def __init__(
        self,
        notes_dir: str | Path | None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="learning_note_tool",
            description="Create and manage SQLite-backed learning notes.",
        )
        resolved_notes_dir = Path(notes_dir) if notes_dir is not None else None
        if db_path is None:
            if resolved_notes_dir is not None and resolved_notes_dir.suffix.lower() == ".db":
                resolved_db_path = resolved_notes_dir
                legacy_notes_dir = None
            else:
                base_dir = resolved_notes_dir or Path.cwd() / "notes"
                base_dir.mkdir(parents=True, exist_ok=True)
                resolved_db_path = base_dir / "study_history.db"
                legacy_notes_dir = resolved_notes_dir
        else:
            resolved_db_path = Path(db_path)
            legacy_notes_dir = resolved_notes_dir

        self.notes_dir = resolved_notes_dir or resolved_db_path.parent
        self.db_path = resolved_db_path
        self.store = SQLiteStudyHistoryStore(
            resolved_db_path,
            legacy_notes_dir=legacy_notes_dir,
        )

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        action = parameters.get("action")
        try:
            if action == "create_note":
                note = self.create_note(
                    title=parameters["title"],
                    content=parameters["content"],
                    note_type=parameters.get("note_type", "learning_note"),
                    tags=parameters.get("tags", []),
                    metadata=parameters.get("metadata", {}),
                )
                return ToolResponse.success(
                    text=f"Created note {note['id']}",
                    data=note,
                )
            if action == "read_note":
                note = self.read_note(parameters["note_id"])
                return ToolResponse.success(
                    text=f"Loaded note {note['id']}",
                    data=note,
                )
            if action == "update_note":
                note = self.update_note(
                    note_id=parameters["note_id"],
                    title=parameters.get("title"),
                    content=parameters.get("content"),
                    tags=parameters.get("tags"),
                    metadata=parameters.get("metadata"),
                )
                return ToolResponse.success(
                    text=f"Updated note {note['id']}",
                    data=note,
                )
            if action == "search_notes":
                results = self.search_notes(
                    parameters["query"],
                    limit=int(parameters.get("limit", 10)),
                )
                return ToolResponse.success(
                    text=f"Found {len(results)} notes",
                    data={"results": results},
                )
            if action == "list_notes":
                results = self.list_notes(
                    note_type=parameters.get("note_type"),
                    tag=parameters.get("tag"),
                    limit=int(parameters.get("limit", 20)),
                )
                return ToolResponse.success(
                    text=f"Listed {len(results)} notes",
                    data={"results": results},
                )
            if action == "summary":
                return ToolResponse.success(
                    text="Generated note summary",
                    data=self.summary(),
                )
        except KeyError as exc:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"Missing required parameter: {exc.args[0]}",
            )
        except FileNotFoundError as exc:
            return ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=str(exc),
            )
        except Exception as exc:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=str(exc),
            )

        return ToolResponse.error(
            code=ToolErrorCode.INVALID_PARAM,
            message=f"Unsupported action: {action}",
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="action",
                type="string",
                description="Action to execute",
                required=True,
            ),
            ToolParameter(
                name="note_id",
                type="string",
                description="Target note id",
                required=False,
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Note title",
                required=False,
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Markdown body",
                required=False,
            ),
            ToolParameter(
                name="note_type",
                type="string",
                description="Type label",
                required=False,
                default="learning_note",
            ),
            ToolParameter(
                name="query",
                type="string",
                description="Search query",
                required=False,
            ),
            ToolParameter(
                name="tags",
                type="array",
                description="List of tags",
                required=False,
                default=[],
            ),
            ToolParameter(
                name="metadata",
                type="object",
                description="Extra metadata",
                required=False,
                default={},
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Result limit",
                required=False,
                default=10,
            ),
            ToolParameter(
                name="tag",
                type="string",
                description="Filter tag",
                required=False,
            ),
        ]

    def create_note(
        self,
        *,
        title: str,
        content: str,
        note_type: str = "learning_note",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store.create_note(
            title=title,
            content=content,
            note_type=note_type,
            tags=tags,
            metadata=metadata,
        )

    def read_note(self, note_id: str) -> dict[str, Any]:
        return self.store.read_note(note_id)

    def update_note(
        self,
        *,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store.update_note(
            note_id=note_id,
            title=title,
            content=content,
            tags=tags,
            metadata=metadata,
        )

    def search_notes(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.search_notes(query, limit=limit)

    def list_notes(
        self,
        *,
        note_type: str | None = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.store.list_notes(
            note_type=note_type,
            tag=tag,
            limit=limit,
        )

    def summary(self) -> dict[str, Any]:
        return self.store.summary()
