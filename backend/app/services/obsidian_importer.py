"""Helpers for importing Markdown notes from an Obsidian vault."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_OBSIDIAN_IGNORE_DIRS = frozenset(
    {
        ".obsidian",
        ".git",
        ".hg",
        ".svn",
        ".trash",
        ".claude",
        ".claudian",
        "__pycache__",
        "asset",
        "assets",
        "attachments",
    }
)


@dataclass
class PreparedObsidianDocument:
    """Normalized note content ready for StudyLoop ingestion."""

    absolute_path: Path
    relative_path: str
    title: str
    topic_hint: str | None
    folder_hints: list[str]
    tags: list[str]
    source: str
    content: str


def collect_markdown_files(
    vault_root: Path,
    *,
    include_subdirs: list[str] | None = None,
    ignore_dirs: list[str] | None = None,
    max_files: int | None = None,
) -> list[Path]:
    """Collect Markdown files from a vault with conservative filtering."""

    normalized_include = {
        str(item).strip()
        for item in (include_subdirs or [])
        if str(item).strip()
    }
    normalized_ignore = {
        str(item).strip().lower()
        for item in (ignore_dirs or [])
        if str(item).strip()
    } | set(DEFAULT_OBSIDIAN_IGNORE_DIRS)

    files: list[Path] = []
    for file_path in sorted(vault_root.rglob("*.md")):
        relative_path = file_path.relative_to(vault_root)
        if _should_ignore_path(
            relative_path,
            include_subdirs=normalized_include,
            ignore_dirs=normalized_ignore,
        ):
            continue
        files.append(file_path)
        if max_files is not None and len(files) >= max_files:
            break
    return files


def prepare_obsidian_document(
    vault_root: Path,
    file_path: Path,
    *,
    min_chars: int = 80,
) -> tuple[PreparedObsidianDocument | None, str | None]:
    """Read and normalize a single Obsidian Markdown note."""

    raw_text = _read_markdown_text(file_path)
    frontmatter, body = _split_frontmatter(raw_text)
    cleaned_body = _clean_obsidian_markdown(body).strip()
    if len(_visible_text(cleaned_body)) < max(0, int(min_chars)):
        return None, "content_too_short"

    relative_path = file_path.relative_to(vault_root).as_posix()
    folder_hints = _derive_folder_hints(Path(relative_path))
    tags = _normalize_tags(frontmatter.get("tags"))
    title = _extract_title(frontmatter, cleaned_body, file_path)
    topic_hint = folder_hints[0] if folder_hints else None

    composed_content = _compose_import_content(
        title=title,
        relative_path=relative_path,
        folder_hints=folder_hints,
        tags=tags,
        body=cleaned_body,
    )
    return (
        PreparedObsidianDocument(
            absolute_path=file_path,
            relative_path=relative_path,
            title=title,
            topic_hint=topic_hint,
            folder_hints=folder_hints,
            tags=tags,
            source=f"obsidian:{relative_path}",
            content=composed_content,
        ),
        None,
    )


def _should_ignore_path(
    relative_path: Path,
    *,
    include_subdirs: set[str],
    ignore_dirs: set[str],
) -> bool:
    parts = list(relative_path.parts)
    directory_parts = parts[:-1]

    for part in directory_parts:
        lowered = part.strip().lower()
        if lowered.startswith(".") or lowered in ignore_dirs:
            return True

    if include_subdirs:
        if len(parts) <= 1:
            return True
        if parts[0] not in include_subdirs:
            return True

    return False


def _read_markdown_text(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, flags=re.DOTALL)
    if not match:
        return {}, text

    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, text[match.end() :]


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，\s]+", value)
    elif isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]

    normalized: list[str] = []
    for item in raw_items:
        cleaned = str(item).strip().lstrip("#")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _extract_title(
    frontmatter: dict[str, Any],
    body: str,
    file_path: Path,
) -> str:
    title = str(frontmatter.get("title", "")).strip()
    if title:
        return title

    heading_match = re.search(
        r"^\s{0,3}#{1,6}\s+(.+?)\s*$",
        body,
        flags=re.MULTILINE,
    )
    if heading_match:
        return heading_match.group(1).strip()

    return file_path.stem.strip()


def _derive_folder_hints(relative_path: Path) -> list[str]:
    hints: list[str] = []
    for part in relative_path.parts[:-1]:
        cleaned = re.sub(r"^\d+[-_.、\s]*", "", part).strip()
        cleaned = cleaned.replace("_", " ").strip()
        if cleaned and not cleaned.startswith("."):
            hints.append(cleaned)
    return hints


def _clean_obsidian_markdown(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"<!--.*?-->", "", normalized, flags=re.DOTALL)
    normalized = re.sub(r"!\[\[(.+?)\]\]", "", normalized)
    normalized = re.sub(
        r"\[\[([^|\]]+)\|([^\]]+)\]\]",
        lambda match: match.group(2).strip(),
        normalized,
    )
    normalized = re.sub(r"\[\[([^\]]+)\]\]", _replace_plain_wikilink, normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _replace_plain_wikilink(match: re.Match[str]) -> str:
    target = match.group(1).split("#", 1)[0].strip()
    if "/" in target:
        target = target.rsplit("/", 1)[-1]
    return target


def _compose_import_content(
    *,
    title: str,
    relative_path: str,
    folder_hints: list[str],
    tags: list[str],
    body: str,
) -> str:
    compact_body = _drop_duplicate_heading(body, title)
    header_lines = [f"# {title}", "", f"来源路径：{relative_path}"]
    if folder_hints:
        header_lines.append(f"目录主题：{' / '.join(folder_hints)}")
    if tags:
        header_lines.append(f"Obsidian 标签：{', '.join(tags)}")
    header_lines.append("")
    return "\n".join(header_lines).strip() + "\n\n" + compact_body.strip()


def _drop_duplicate_heading(body: str, title: str) -> str:
    first_heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*(?:\n+|$)", body)
    if not first_heading:
        return body
    if _normalize_heading(first_heading.group(1)) != _normalize_heading(title):
        return body
    return body[first_heading.end() :].lstrip()


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def _visible_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))
