from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
EVALS_DIR = ROOT_DIR / "evals"
DEFAULT_CASES_DIR = EVALS_DIR / "cases"
DEFAULT_FIXTURES_PATH = EVALS_DIR / "fixtures" / "knowledge_base.jsonl"
DEFAULT_RUNTIME_ROOT = EVALS_DIR / "runtime"
DEFAULT_REPORTS_DIR = EVALS_DIR / "reports"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.agents.graph import StudyLoopGraph
from backend.app.config import BackendSettings
from backend.app.services.retrieval_service import SimpleKeywordRetriever
from backend.app.services.study_agent_service import StudyAgentService


@dataclass
class CaseResult:
    suite: str
    case_id: str
    passed: bool
    details: str
    metrics: dict[str, Any]
    payload_preview: dict[str, Any]


@dataclass
class EvalRuntimeContext:
    """评测运行时上下文。

    这里把 service / graph / SQLite 基线快照绑在一起，目的是：
    1. 整套评测只初始化一次外部依赖
    2. 每个 case 执行前快速回滚到干净状态
    3. 避免真实模式下重复导入知识、重复创建 Qdrant collection
    """

    runtime_dir: Path
    baseline_db_path: Path
    qdrant_collection: str | None
    service: StudyAgentService
    graph: StudyLoopGraph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run StudyLoop eval suites.")
    parser.add_argument(
        "--suite",
        choices=["all", "retrieval", "explain", "quiz", "grade", "workflow"],
        default="all",
        help="选择要运行的评测集",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=DEFAULT_CASES_DIR,
        help="评测用例目录",
    )
    parser.add_argument(
        "--case-profile",
        choices=["smoke", "extended", "all"],
        default="smoke",
        help="选择评测集规模：smoke 为快速回归，extended 为扩展覆盖，all 为全部用例",
    )
    parser.add_argument(
        "--fixtures-path",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help="评测知识种子 JSONL 文件",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="每次评测运行目录的父目录",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help="评测报告输出目录",
    )
    parser.add_argument(
        "--use-mock-llm",
        action="store_true",
        help="使用 mock LLM，优先验证流程稳定性",
    )
    parser.add_argument(
        "--use-qdrant",
        action="store_true",
        help="保留 .env 中的 Qdrant 配置，验证向量检索链路",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 形式输出最终结果",
    )
    parser.add_argument(
        "--fresh-runtime-per-case",
        action="store_true",
        help="每个 case 都重新初始化独立运行时，便于排查问题，但会明显更慢",
    )
    parser.add_argument(
        "--full-llm-path",
        action="store_true",
        help="让简报推导等辅助步骤也走真实 LLM，验证最完整链路，但会更慢",
    )
    parser.add_argument(
        "--parallel-suites",
        type=int,
        default=0,
        help="并行执行的 suite 数量。0 表示自动：真实全量评测默认 2，其余默认 1",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def case_matches_profile(case: dict[str, Any], selected_profile: str) -> bool:
    """按 profile 过滤用例，兼顾快速回归和扩展评测。"""

    if selected_profile == "all":
        return True

    raw_profiles = case.get("profiles")
    if isinstance(raw_profiles, list) and raw_profiles:
        case_profiles = [str(item).strip().lower() for item in raw_profiles if str(item).strip()]
    else:
        single_profile = str(case.get("profile", "smoke")).strip().lower() or "smoke"
        case_profiles = [single_profile]

    order = {"smoke": 0, "extended": 1}
    selected_rank = order.get(selected_profile, 0)
    case_rank = max(order.get(profile, 0) for profile in case_profiles)
    return case_rank <= selected_rank


def normalize_text(value: Any) -> str:
    """统一字符串归一化，避免大小写和首尾空白影响匹配。"""

    return str(value or "").strip().casefold()


def make_runtime_dir(runtime_root: Path, run_label: str) -> Path:
    # 默认按“整次评测运行”建目录；是否每个 case 独立由上层控制。
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    runtime_dir = runtime_root / f"{run_label}_{timestamp}"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def build_service(
    runtime_dir: Path,
    *,
    use_mock_llm: bool,
    use_qdrant: bool,
    qdrant_collection: str | None = None,
) -> StudyAgentService:
    notes_dir = runtime_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    settings_kwargs: dict[str, Any] = {
        "use_mock_llm": use_mock_llm,
        "notes_dir": notes_dir,
        "notes_index_path": notes_dir / "notes_index.json",
        "study_history_db_path": runtime_dir / "study_history.db",
    }
    if not use_qdrant:
        # 默认关闭远程向量库，先让本地评测稳定可重复。
        settings_kwargs.update(
            {
                "qdrant_url": None,
                "qdrant_api_key": None,
                "embed_api_key": None,
            }
        )
    elif qdrant_collection:
        # 评测使用独立 collection，避免和业务知识库混用，也能减少检索范围。
        settings_kwargs["qdrant_collection"] = qdrant_collection

    settings = BackendSettings(**settings_kwargs)
    return StudyAgentService(settings=settings)


def build_eval_collection_name(run_label: str) -> str:
    """为评测生成独立的 Qdrant collection 名称。"""

    normalized_label = normalize_text(run_label).replace("-", "_")
    normalized_label = normalized_label.replace(" ", "_") or "all"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"hello_agents_eval_{normalized_label}_{timestamp}"


def seed_knowledge(service: StudyAgentService, fixtures_path: Path) -> None:
    """快速导入评测知识种子。

    这里故意不走 ingest_material()，因为那条路径会做知识整理，真实模式下会触发
    额外 LLM 调用。评测种子本身已经是结构化数据，因此直接用启发式元数据组装即可。
    """

    all_chunks: list[dict[str, Any]] = []
    for record in load_jsonl(fixtures_path):
        source = record.get("source", "eval-seed")
        title = record.get("title")
        topic = record.get("topic")
        content = record["content"]
        profile = service._heuristic_knowledge_profile(  # type: ignore[attr-defined]
            content=content,
            source=source,
            title=title,
            topic=topic,
        )
        chunks = SimpleKeywordRetriever.chunk_text(
            content,
            source=source,
            topic=profile["primary_topic"],
            extra_metadata={
                "fixture_id": record.get("id"),
                "title": profile["title"],
                "topic": profile["primary_topic"],
                "category_path": profile["category_path"],
                "tags": profile["tags"],
                "summary": profile["summary"],
            },
        )
        for index, chunk in enumerate(chunks, start=1):
            fixture_id = str(record.get("id") or profile["title"] or index)
            # Qdrant 只接受整数或 UUID，这里用 uuid5 生成可重复的稳定 ID。
            chunk["doc_id"] = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{fixture_id}-chunk-{index}")
            )
        all_chunks.extend(chunks)
        service.note_tool.create_note(
            title=profile["title"],
            content=(
                f"## 自动摘要\n{profile['summary']}\n\n"
                f"## 自动分类\n- 一级/二级分类：{' / '.join(profile['category_path'])}\n"
                f"- 主题：{profile['primary_topic']}\n"
                f"- 标签：{', '.join(profile['tags'])}"
            ),
            note_type="knowledge_item",
            tags=[profile["primary_topic"], *profile["category_path"], *profile["tags"]],
            metadata={
                "fixture_id": record.get("id"),
                "source": source,
                "title": profile["title"],
                "topic": profile["primary_topic"],
                "category_path": profile["category_path"],
                "summary": profile["summary"],
                "learning_goal": profile["learning_goal"],
            },
        )

    if all_chunks:
        service.retriever.add_documents(all_chunks)


def apply_eval_accelerators(
    service: StudyAgentService,
    *,
    suite_name: str,
    use_mock_llm: bool,
    full_llm_path: bool,
) -> None:
    """在真实评测里关闭辅助型 LLM 调用，优先验证主能力链路。"""

    if use_mock_llm or full_llm_path:
        return

    def fast_prepare_study_brief(
        *,
        seed_text: str,
        intent: str,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        conversation_context: list[Any] | None = None,
    ) -> dict[str, Any]:
        return service._heuristic_study_brief(  # type: ignore[attr-defined]
            seed_text=seed_text,
            intent=intent,
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
        )

    def fast_generate_reference_answer(
        *,
        question: str,
        current_topic: str,
        evidence: list[dict[str, Any]] | None = None,
        reference_answer: str | None = None,
    ) -> dict[str, Any]:
        return service._heuristic_reference_answer(  # type: ignore[attr-defined]
            question=question,
            current_topic=current_topic,
            evidence=evidence or [],
            reference_answer=reference_answer,
        )

    service.prepare_study_brief = fast_prepare_study_brief  # type: ignore[method-assign]
    service.generate_reference_answer = fast_generate_reference_answer  # type: ignore[method-assign]
    # 评测模式下进一步压缩上下文，减少真实模型在长 prompt 上的等待时间。
    if suite_name in {"grade", "workflow"}:
        service.context_builder.config.max_tokens = min(
            service.context_builder.config.max_tokens,
            1200,
        )
        service.context_builder.config.max_items_per_section = min(
            service.context_builder.config.max_items_per_section,
            1,
        )
    elif suite_name == "quiz":
        service.context_builder.config.max_tokens = min(
            service.context_builder.config.max_tokens,
            1600,
        )
        service.context_builder.config.max_items_per_section = min(
            service.context_builder.config.max_items_per_section,
            1,
        )
    else:
        service.context_builder.config.max_tokens = min(
            service.context_builder.config.max_tokens,
            2200,
        )
        service.context_builder.config.max_items_per_section = min(
            service.context_builder.config.max_items_per_section,
            2,
        )

    # 对评测期的检索与笔记做轻量裁剪，避免把过长的原始内容直接塞给模型。
    original_search = service.retriever.search
    original_search_notes = service.note_tool.search_notes
    original_list_notes = service.note_tool.list_notes
    retrieval_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def compact_retrieval_item(item: dict[str, Any], *, content_limit: int) -> dict[str, Any]:
        metadata = dict(item.get("metadata") or {})
        compact_metadata = {
            "topic": metadata.get("topic"),
            "title": metadata.get("title"),
            "category_path": metadata.get("category_path"),
            "tags": metadata.get("tags"),
            "summary": str(metadata.get("summary", ""))[:160],
        }
        return {
            "doc_id": item.get("doc_id"),
            "content": str(item.get("content", ""))[:content_limit],
            "source": item.get("source"),
            "metadata": compact_metadata,
            "score": item.get("score"),
        }

    def compact_note(note: dict[str, Any], *, content_limit: int) -> dict[str, Any]:
        metadata = dict(note.get("metadata") or {})
        preview = str(note.get("preview") or note.get("content") or "")[:content_limit]
        compact_metadata = {
            "topic": metadata.get("topic"),
            "category_path": metadata.get("category_path"),
            "summary": str(metadata.get("summary", ""))[:160],
            "score": metadata.get("score"),
            "mastery_before": metadata.get("mastery_before"),
            "mastery_after": metadata.get("mastery_after"),
        }
        return {
            "id": note.get("id"),
            "title": note.get("title"),
            "note_type": note.get("note_type"),
            "content": preview,
            "preview": preview,
            "tags": note.get("tags", []),
            "metadata": compact_metadata,
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
        }

    def fast_search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        requested_k = int(top_k)
        if suite_name in {"grade", "workflow"}:
            effective_top_k = min(requested_k, 2)
            content_limit = 180
        elif suite_name == "quiz":
            effective_top_k = min(requested_k, 3)
            content_limit = 220
        else:
            effective_top_k = requested_k
            content_limit = 260

        cache_key = (str(query), effective_top_k)
        if cache_key not in retrieval_cache:
            results = original_search(query, top_k=effective_top_k)
            retrieval_cache[cache_key] = [
                compact_retrieval_item(item, content_limit=content_limit)
                for item in results
            ]
        return retrieval_cache[cache_key]

    def fast_search_notes(query: str, limit: int = 10) -> list[dict[str, Any]]:
        if suite_name in {"grade", "workflow"}:
            effective_limit = min(int(limit), 1)
            content_limit = 160
        elif suite_name == "quiz":
            effective_limit = min(int(limit), 1)
            content_limit = 180
        else:
            effective_limit = min(int(limit), 2)
            content_limit = 220
        notes = original_search_notes(query, limit=effective_limit)
        return [compact_note(note, content_limit=content_limit) for note in notes]

    def fast_list_notes(
        *,
        note_type: str | None = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if suite_name in {"grade", "workflow"}:
            effective_limit = min(int(limit), 1)
            content_limit = 160
        elif suite_name == "quiz":
            effective_limit = min(int(limit), 1)
            content_limit = 180
        else:
            effective_limit = min(int(limit), 2)
            content_limit = 220
        notes = original_list_notes(
            note_type=note_type,
            tag=tag,
            limit=effective_limit,
        )
        return [compact_note(note, content_limit=content_limit) for note in notes]

    service.retriever.search = fast_search  # type: ignore[method-assign]
    service.note_tool.search_notes = fast_search_notes  # type: ignore[method-assign]
    service.note_tool.list_notes = fast_list_notes  # type: ignore[method-assign]


def build_runtime_context(
    args: argparse.Namespace,
    run_label: str,
    suite_name: str,
) -> EvalRuntimeContext:
    """创建可复用的评测运行时，并在启动阶段一次性完成知识种子导入。"""

    runtime_dir = make_runtime_dir(args.runtime_root, run_label)
    qdrant_collection = build_eval_collection_name(run_label) if args.use_qdrant else None
    service = build_service(
        runtime_dir,
        use_mock_llm=args.use_mock_llm,
        use_qdrant=args.use_qdrant,
        qdrant_collection=qdrant_collection,
    )
    apply_eval_accelerators(
        service,
        suite_name=suite_name,
        use_mock_llm=args.use_mock_llm,
        full_llm_path=args.full_llm_path,
    )
    seed_knowledge(service, args.fixtures_path)
    baseline_db_path = runtime_dir / "study_history.baseline.db"
    shutil.copy2(service.settings.resolved_study_history_db_path, baseline_db_path)
    return EvalRuntimeContext(
        runtime_dir=runtime_dir,
        baseline_db_path=baseline_db_path,
        qdrant_collection=qdrant_collection,
        service=service,
        graph=StudyLoopGraph(service),
    )


def restore_runtime_context(runtime: EvalRuntimeContext) -> None:
    """每个 case 执行前恢复到种子导入后的干净状态。"""

    db_path = runtime.service.settings.resolved_study_history_db_path
    with sqlite3.connect(runtime.baseline_db_path) as source_conn:
        with sqlite3.connect(db_path) as target_conn:
            source_conn.backup(target_conn)
    runtime.service.state = runtime.service._load_persisted_state()


def cleanup_runtime_context(runtime: EvalRuntimeContext) -> None:
    """清理评测运行时创建的远程资源。"""

    retriever = runtime.service.retriever
    vector_retriever = getattr(retriever, "vector_retriever", None)
    if not runtime.qdrant_collection or vector_retriever is None:
        return
    try:
        vector_retriever.client.delete_collection(
            collection_name=runtime.qdrant_collection,
            timeout=vector_retriever.timeout,
        )
    except Exception:
        # 清理失败不影响评测主结果；这里选择静默跳过，避免掩盖真正的业务失败。
        pass


def preview_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        preview = dict(payload)
        if "answer" in preview and isinstance(preview["answer"], str):
            preview["answer"] = preview["answer"][:180]
        if "result" in preview and isinstance(preview["result"], dict):
            result = dict(preview["result"])
            if "feedback" in result and isinstance(result["feedback"], str):
                result["feedback"] = result["feedback"][:180]
            preview["result"] = result
        return preview
    return {"value": str(payload)[:180]}


def get_retrieval_labels(item: dict[str, Any]) -> dict[str, str]:
    """提取检索结果中用于评测匹配的关键标签。"""

    metadata = item.get("metadata", {}) or {}
    return {
        "doc_id": normalize_text(item.get("doc_id") or metadata.get("doc_id")),
        "title": normalize_text(metadata.get("title")),
        "topic": normalize_text(metadata.get("topic")),
        "source": normalize_text(item.get("source") or metadata.get("source")),
    }


def get_retrieval_gold_sets(case: dict[str, Any]) -> dict[str, set[str]]:
    """把用例中的金标字段整理成集合，方便后续计算命中率和召回率。"""

    return {
        "doc_ids": {
            normalize_text(value) for value in case.get("gold_doc_ids", []) if normalize_text(value)
        },
        "titles": {
            normalize_text(value) for value in case.get("gold_titles", []) if normalize_text(value)
        },
        "topics": {
            normalize_text(value) for value in case.get("gold_topics", []) if normalize_text(value)
        },
        "sources": {
            normalize_text(value) for value in case.get("gold_sources", []) if normalize_text(value)
        },
    }


def find_relevant_matches(
    item: dict[str, Any],
    gold_sets: dict[str, set[str]],
) -> dict[str, list[str]]:
    """判断单条检索结果命中了哪些金标字段。"""

    labels = get_retrieval_labels(item)
    matches: dict[str, list[str]] = {}
    if gold_sets["doc_ids"] and labels["doc_id"] in gold_sets["doc_ids"]:
        matches["doc_ids"] = [labels["doc_id"]]
    if gold_sets["titles"] and labels["title"] in gold_sets["titles"]:
        matches["titles"] = [labels["title"]]
    if gold_sets["topics"] and labels["topic"] in gold_sets["topics"]:
        matches["topics"] = [labels["topic"]]
    if gold_sets["sources"] and labels["source"] in gold_sets["sources"]:
        matches["sources"] = [labels["source"]]
    return matches


def compute_retrieval_metrics(
    results: list[dict[str, Any]],
    case: dict[str, Any],
) -> dict[str, Any]:
    """计算检索评测常见指标，便于后续写进简历或实验报告。"""

    gold_sets = get_retrieval_gold_sets(case)
    ranked_matches: list[dict[str, Any]] = []
    matched_titles: set[str] = set()
    matched_doc_ids: set[str] = set()
    first_relevant_rank: int | None = None

    for rank, item in enumerate(results, start=1):
        matches = find_relevant_matches(item, gold_sets)
        if not matches:
            continue
        if first_relevant_rank is None:
            first_relevant_rank = rank
        labels = get_retrieval_labels(item)
        matched_titles.update(matches.get("titles", []))
        matched_doc_ids.update(matches.get("doc_ids", []))
        ranked_matches.append(
            {
                "rank": rank,
                "doc_id": item.get("doc_id"),
                "title": item.get("metadata", {}).get("title"),
                "topic": item.get("metadata", {}).get("topic"),
                "matched_by": sorted(matches.keys()),
                "score": item.get("score"),
                "normalized_labels": labels,
            }
        )

    document_gold_total: int | None = None
    matched_document_total: int | None = None
    if gold_sets["doc_ids"]:
        document_gold_total = len(gold_sets["doc_ids"])
        matched_document_total = len(matched_doc_ids)
    elif gold_sets["titles"]:
        document_gold_total = len(gold_sets["titles"])
        matched_document_total = len(matched_titles)

    hit_at_k = 1.0 if first_relevant_rank is not None else 0.0
    mrr_at_k = round(1.0 / first_relevant_rank, 4) if first_relevant_rank else 0.0
    requested_k = max(int(case.get("top_k", len(results) or 1)), 1)
    precision_at_k = round(len(ranked_matches) / requested_k, 4)
    recall_at_k = None
    if document_gold_total and matched_document_total is not None:
        recall_at_k = round(matched_document_total / document_gold_total, 4)

    return {
        "requested_k": requested_k,
        "returned_count": len(results),
        "relevant_count": len(ranked_matches),
        "first_relevant_rank": first_relevant_rank,
        "hit_at_k": hit_at_k,
        "mrr_at_k": mrr_at_k,
        "precision_at_k": precision_at_k,
        "recall_at_k": recall_at_k,
        "gold_document_total": document_gold_total,
        "matched_document_total": matched_document_total,
        "matches": ranked_matches,
    }


def round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def aggregate_metrics(results: list[CaseResult]) -> dict[str, Any]:
    """汇总各个 suite 的通过率和关键指标。"""

    suite_metrics: dict[str, dict[str, Any]] = {}
    for suite_name in sorted({item.suite for item in results}):
        suite_results = [item for item in results if item.suite == suite_name]
        passed_count = sum(1 for item in suite_results if item.passed)
        suite_metrics[suite_name] = {
            "total": len(suite_results),
            "passed": passed_count,
            "failed": len(suite_results) - passed_count,
            "pass_rate": round_metric(passed_count / len(suite_results)) if suite_results else 0.0,
            "avg_duration_ms": round_metric(
                sum(float(item.metrics.get("duration_ms", 0.0)) for item in suite_results)
                / len(suite_results)
            )
            if suite_results
            else 0.0,
        }

    retrieval_results = [item for item in results if item.suite == "retrieval"]
    if retrieval_results:
        hit_values = [float(item.metrics.get("hit_at_k", 0.0)) for item in retrieval_results]
        mrr_values = [float(item.metrics.get("mrr_at_k", 0.0)) for item in retrieval_results]
        precision_values = [float(item.metrics.get("precision_at_k", 0.0)) for item in retrieval_results]
        recall_values = [
            float(item.metrics["recall_at_k"])
            for item in retrieval_results
            if item.metrics.get("recall_at_k") is not None
        ]
        suite_metrics["retrieval"].update(
            {
                "avg_hit_at_k": round_metric(sum(hit_values) / len(hit_values)),
                "avg_mrr_at_k": round_metric(sum(mrr_values) / len(mrr_values)),
                "avg_precision_at_k": round_metric(sum(precision_values) / len(precision_values)),
                "avg_recall_at_k": round_metric(sum(recall_values) / len(recall_values))
                if recall_values
                else None,
            }
        )

    grade_results = [item for item in results if item.suite == "grade"]
    if grade_results:
        scores = [
            float(item.metrics["score"])
            for item in grade_results
            if item.metrics.get("score") is not None
        ]
        if scores:
            suite_metrics["grade"]["avg_score"] = round_metric(sum(scores) / len(scores))

    workflow_results = [item for item in results if item.suite == "workflow"]
    if workflow_results:
        retry_counts = [
            float(item.metrics["retry_count"])
            for item in workflow_results
            if item.metrics.get("retry_count") is not None
        ]
        if retry_counts:
            suite_metrics["workflow"]["avg_retry_count"] = round_metric(
                sum(retry_counts) / len(retry_counts)
            )

    passed_count = sum(1 for item in results if item.passed)
    return {
        "overall_pass_rate": round_metric(passed_count / len(results)) if results else 0.0,
        "total_duration_ms": round_metric(
            sum(float(item.metrics.get("duration_ms", 0.0)) for item in results)
        )
        if results
        else 0.0,
        "suite_metrics": suite_metrics,
    }


def write_report(summary: dict[str, Any], report_dir: Path) -> Path:
    """把评测结果写入 JSON，便于沉淀版本对比。"""

    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"metrics_{timestamp}.json"
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


def evaluate_retrieval_case(
    runtime: EvalRuntimeContext,
    case: dict[str, Any],
) -> CaseResult:
    results = runtime.service.retriever.search(case["query"], top_k=case.get("top_k", 3))

    topic_hit = False
    content_hit = False
    expected_topic = case.get("expected_topic")
    must_contain_any = case.get("must_contain_any", [])

    for item in results:
        metadata = item.get("metadata", {})
        topic = str(metadata.get("topic", ""))
        content = str(item.get("content", ""))
        title = str(metadata.get("title", ""))
        if expected_topic and expected_topic.lower() in f"{topic} {title}".lower():
            topic_hit = True
        if must_contain_any and any(token in content for token in must_contain_any):
            content_hit = True

    if not must_contain_any:
        content_hit = True
    if not expected_topic:
        topic_hit = True

    retrieval_metrics = compute_retrieval_metrics(results, case)
    passed = bool(results) and topic_hit and content_hit
    details = (
        f"hits={len(results)}, topic_hit={topic_hit}, content_hit={content_hit}, "
        f"hit@k={retrieval_metrics['hit_at_k']}, mrr@k={retrieval_metrics['mrr_at_k']}, "
        f"recall@k={retrieval_metrics['recall_at_k']}, "
        f"top_topics={[item.get('metadata', {}).get('topic') for item in results[:3]]}"
    )
    return CaseResult(
        suite="retrieval",
        case_id=case["id"],
        passed=passed,
        details=details,
        metrics={
            **retrieval_metrics,
            "topic_hit": topic_hit,
            "content_hit": content_hit,
        },
        payload_preview=preview_payload({"results": results[:2]}),
    )


def evaluate_explain_case(
    runtime: EvalRuntimeContext,
    case: dict[str, Any],
) -> CaseResult:
    payload = runtime.graph.explain(
        question=case["question"],
        current_topic=case.get("current_topic"),
    )
    answer = str(payload.get("answer", ""))
    evidence_count = len(payload.get("evidence", []) or [])

    must_include_points = case.get("must_include_points", [])
    must_include_any_groups = case.get("must_include_any_groups", [])
    must_mention_any = case.get("must_mention_any", [])
    forbid_points = case.get("forbid_points", [])
    missing = [point for point in must_include_points if point not in answer]
    if must_include_any_groups:
        missing = [
            " / ".join(group)
            for group in must_include_any_groups
            if not any(str(token) in answer for token in group)
        ]
    forbidden = [point for point in forbid_points if point in answer]
    answer_len_ok = len(answer) >= int(case.get("min_answer_length", 0))
    evidence_ok = evidence_count >= int(case.get("min_evidence_count", 0))
    mention_ok = not must_mention_any or any(str(token) in answer for token in must_mention_any)
    passed = not missing and not forbidden and answer_len_ok and evidence_ok and mention_ok
    details = (
        f"missing={missing}, forbidden={forbidden}, answer_len={len(answer)}, "
        f"answer_len_ok={answer_len_ok}, evidence_count={evidence_count}, "
        f"evidence_ok={evidence_ok}, mention_ok={mention_ok}"
    )

    return CaseResult(
        suite="explain",
        case_id=case["id"],
        passed=passed,
        details=details,
        metrics={
            "answer_length": len(answer),
            "evidence_count": evidence_count,
            "missing_count": len(missing),
            "forbidden_count": len(forbidden),
            "answer_len_ok": answer_len_ok,
            "evidence_ok": evidence_ok,
            "mention_ok": mention_ok,
        },
        payload_preview=preview_payload(payload),
    )


def _extract_question_type(question: dict[str, Any]) -> str:
    return str(
        question.get("type")
        or question.get("question_type")
        or question.get("kind")
        or ""
    )


def evaluate_quiz_case(
    runtime: EvalRuntimeContext,
    case: dict[str, Any],
) -> CaseResult:
    payload = runtime.graph.quiz(
        current_topic=case.get("current_topic"),
        prompt=case.get("prompt", ""),
        question_count=case.get("question_count", 3),
        question_types=case.get("question_types"),
        difficulty=case.get("difficulty", "medium"),
    )
    quiz_set = payload.get("quiz_set", {})
    questions = quiz_set.get("questions", [])
    returned_topic = str(
        quiz_set.get("topic")
        or payload.get("current_topic")
        or payload.get("resolved_topic")
        or ""
    )
    returned_types = {_extract_question_type(item) for item in questions}
    expected_types = set(case.get("question_types", []))
    expected_topic = str(case.get("expected_topic", ""))

    count_ok = len(questions) >= case.get("question_count", 1)
    topic_ok = not expected_topic or expected_topic.lower() in returned_topic.lower()
    type_ok = expected_types.issubset(returned_types) if expected_types else True
    passed = count_ok and topic_ok and type_ok
    details = (
        f"question_count={len(questions)}, count_ok={count_ok}, topic_ok={topic_ok}, "
        f"type_ok={type_ok}, returned_types={sorted(returned_types)}"
    )

    return CaseResult(
        suite="quiz",
        case_id=case["id"],
        passed=passed,
        details=details,
        metrics={
            "question_count": len(questions),
            "count_ok": count_ok,
            "topic_ok": topic_ok,
            "type_ok": type_ok,
        },
        payload_preview=preview_payload(payload),
    )


def evaluate_grade_case(
    runtime: EvalRuntimeContext,
    case: dict[str, Any],
) -> CaseResult:
    payload = runtime.graph.grade(
        question=case["question"],
        student_answer=case["student_answer"],
        current_topic=case.get("current_topic"),
        reference_answer=case.get("reference_answer"),
    )
    result = payload.get("result", {})
    score = float(result.get("score", 0))
    mistake_type = str(result.get("mistake_type", ""))
    min_score = float(case.get("expected_score_min", 0))
    max_score = float(case.get("expected_score_max", 100))
    allowed_types = set(case.get("expected_mistake_type_any", []))

    score_ok = min_score <= score <= max_score
    type_ok = not allowed_types or mistake_type in allowed_types
    passed = score_ok and type_ok
    details = (
        f"score={score}, score_ok={score_ok}, mistake_type={mistake_type}, "
        f"type_ok={type_ok}, allowed_types={sorted(allowed_types)}"
    )

    return CaseResult(
        suite="grade",
        case_id=case["id"],
        passed=passed,
        details=details,
        metrics={
            "score": score,
            "score_ok": score_ok,
            "mistake_type": mistake_type,
            "type_ok": type_ok,
        },
        payload_preview=preview_payload(payload),
    )


def evaluate_workflow_case(
    runtime: EvalRuntimeContext,
    case: dict[str, Any],
) -> CaseResult:
    current_topic = case.get("current_topic")
    if current_topic:
        runtime.service.state.mastery_by_topic[current_topic] = float(case.get("initial_mastery", 0.5))

    payload = runtime.graph.grade(
        question=case["question"],
        student_answer=case["student_answer"],
        current_topic=current_topic,
        reference_answer=case.get("reference_answer"),
    )

    retry_count = int(payload.get("retry_count", 0))
    remediation_quiz = payload.get("remediation_quiz")
    expected_retry_count = int(case.get("expected_retry_count", 0))
    expect_remediation_quiz = bool(case.get("expect_remediation_quiz", False))

    retry_ok = retry_count == expected_retry_count
    remediation_ok = bool(remediation_quiz) == expect_remediation_quiz
    passed = retry_ok and remediation_ok
    details = (
        f"retry_count={retry_count}, retry_ok={retry_ok}, "
        f"remediation_ok={remediation_ok}, has_remediation_quiz={bool(remediation_quiz)}"
    )

    return CaseResult(
        suite="workflow",
        case_id=case["id"],
        passed=passed,
        details=details,
        metrics={
            "retry_count": retry_count,
            "retry_ok": retry_ok,
            "remediation_ok": remediation_ok,
            "has_remediation_quiz": bool(remediation_quiz),
        },
        payload_preview=preview_payload(payload),
    )


def run_suite(
    args: argparse.Namespace,
    suite_name: str,
    shared_runtime: EvalRuntimeContext | None = None,
) -> list[CaseResult]:
    evaluator_map = {
        "retrieval": evaluate_retrieval_case,
        "explain": evaluate_explain_case,
        "quiz": evaluate_quiz_case,
        "grade": evaluate_grade_case,
        "workflow": evaluate_workflow_case,
    }
    cases_path = args.cases_dir / f"{suite_name}_cases.jsonl"
    cases = [case for case in load_jsonl(cases_path) if case_matches_profile(case, args.case_profile)]
    suite_results: list[CaseResult] = []
    for case in cases:
        runtime = shared_runtime
        if args.fresh_runtime_per_case:
            runtime = build_runtime_context(
                args,
                f"{suite_name}_{case['id']}",
                suite_name,
            )
        elif runtime is None:
            raise RuntimeError("Shared eval runtime is not initialized")

        try:
            restore_runtime_context(runtime)
            started_at = perf_counter()
            result = evaluator_map[suite_name](runtime, case)
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            result.metrics["duration_ms"] = duration_ms
            suite_results.append(result)
        finally:
            if args.fresh_runtime_per_case:
                cleanup_runtime_context(runtime)
    return suite_results


def resolve_parallel_suites(args: argparse.Namespace, suite_count: int) -> int:
    """决定 suite 级并行度。"""

    if args.parallel_suites and args.parallel_suites > 0:
        return min(int(args.parallel_suites), max(suite_count, 1))
    if args.suite == "all" and not args.use_mock_llm:
        return min(2, max(suite_count, 1))
    return 1


def run_suite_with_runtime(
    args: argparse.Namespace,
    suite_name: str,
) -> list[CaseResult]:
    """为单个 suite 管理共享 runtime 的生命周期。"""

    shared_runtime: EvalRuntimeContext | None = None
    try:
        if not args.fresh_runtime_per_case:
            shared_runtime = build_runtime_context(args, suite_name, suite_name)
        return run_suite(args, suite_name, shared_runtime)
    finally:
        if shared_runtime is not None:
            cleanup_runtime_context(shared_runtime)


def format_console_result(result: CaseResult) -> str:
    status = "PASS" if result.passed else "FAIL"
    duration_ms = result.metrics.get("duration_ms")
    duration_text = f", duration_ms={duration_ms}" if duration_ms is not None else ""
    return f"[{status}] {result.suite}/{result.case_id} -> {result.details}{duration_text}"


def main() -> int:
    started_at = perf_counter()
    args = parse_args()
    suites = (
        ["retrieval", "explain", "quiz", "grade", "workflow"]
        if args.suite == "all"
        else [args.suite]
    )
    suite_results_map: dict[str, list[CaseResult]] = {}
    parallel_suites = resolve_parallel_suites(args, len(suites))

    if parallel_suites > 1 and len(suites) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_suites) as executor:
            future_map = {
                executor.submit(run_suite_with_runtime, args, suite_name): suite_name
                for suite_name in suites
            }
            for future in concurrent.futures.as_completed(future_map):
                suite_name = future_map[future]
                suite_results_map[suite_name] = future.result()
    else:
        for suite_name in suites:
            suite_results_map[suite_name] = run_suite_with_runtime(args, suite_name)

    all_results: list[CaseResult] = []
    for suite_name in suites:
        suite_results = suite_results_map.get(suite_name, [])
        all_results.extend(suite_results)
        for result in suite_results:
            print(format_console_result(result))

    wall_clock_ms = round((perf_counter() - started_at) * 1000, 2)
    passed_count = sum(1 for item in all_results if item.passed)
    aggregated = aggregate_metrics(all_results)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "suite": args.suite,
        "case_profile": args.case_profile,
        "runtime_root": str(args.runtime_root),
        "report_dir": str(args.report_dir),
        "use_mock_llm": args.use_mock_llm,
        "use_qdrant": args.use_qdrant,
        "fresh_runtime_per_case": args.fresh_runtime_per_case,
        "full_llm_path": args.full_llm_path,
        "parallel_suites": parallel_suites,
        "passed": passed_count,
        "failed": len(all_results) - passed_count,
        "total": len(all_results),
        "wall_clock_ms": wall_clock_ms,
        **aggregated,
        "results": [asdict(item) for item in all_results],
    }
    report_path = write_report(summary, args.report_dir)
    summary["report_path"] = str(report_path)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"\nSummary: {passed_count}/{len(all_results)} passed, "
            f"{len(all_results) - passed_count} failed"
        )
        print(f"Overall pass rate: {summary['overall_pass_rate']}")
        print(f"Wall clock ms: {summary['wall_clock_ms']}")
        print(f"Total duration ms: {summary['total_duration_ms']}")
        retrieval_metrics = summary.get("suite_metrics", {}).get("retrieval")
        if retrieval_metrics:
            print(
                "Retrieval metrics: "
                f"Hit@k={retrieval_metrics.get('avg_hit_at_k')}, "
                f"MRR@k={retrieval_metrics.get('avg_mrr_at_k')}, "
                f"Recall@k={retrieval_metrics.get('avg_recall_at_k')}, "
                f"Precision@k={retrieval_metrics.get('avg_precision_at_k')}"
            )
        print(f"Report: {report_path}")

    return 0 if passed_count == len(all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
