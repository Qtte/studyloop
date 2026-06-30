from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import get_settings
from backend.app.services.study_agent_service import StudyAgentService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import an Obsidian vault into StudyLoop.")
    parser.add_argument("vault_path", help="Obsidian 知识库目录")
    parser.add_argument(
        "--include-subdir",
        action="append",
        dest="include_subdirs",
        default=None,
        help="只导入指定顶层目录，可重复传入，如 02-Redis",
    )
    parser.add_argument(
        "--ignore-dir",
        action="append",
        dest="ignore_dirs",
        default=None,
        help="额外忽略的目录名，可重复传入",
    )
    parser.add_argument("--max-files", type=int, default=None, help="最多导入多少篇 Markdown")
    parser.add_argument("--min-chars", type=int, default=80, help="正文最少字符数，过短文件会跳过")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不真正入库")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="跳过 metadata.source 已存在的 Obsidian 笔记",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = StudyAgentService(get_settings())
    result = service.import_obsidian_vault(
        vault_path=args.vault_path,
        include_subdirs=args.include_subdirs,
        ignore_dirs=args.ignore_dirs,
        max_files=args.max_files,
        min_chars=args.min_chars,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Vault: {result['vault_path']}")
    print(
        "Imported {imported_count}, skipped {skipped_count}, failed {failed_count}, indexed {documents_indexed}".format(
            **result
        )
    )
    if result["imported_items"]:
        print("\nImported Preview:")
        for item in result["imported_items"][:10]:
            topic_label = (
                item["classification"]["primary_topic"]
                if "classification" in item
                else item.get("topic_hint")
            )
            print(f"- {item['relative_path']} -> {topic_label}")
    if result["failed_items"]:
        print("\nFailed Preview:")
        for item in result["failed_items"][:10]:
            print(f"- {item['relative_path']}: {item['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
