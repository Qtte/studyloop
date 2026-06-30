"""
Application service that wires StudyLoop features onto HelloAgents.

═══════════════════════════════════════════════════════════════════════════════
架构说明
═══════════════════════════════════════════════════════════════════════════════

StudyAgentService 是 StudyLoop 的"业务中心"——它创建并持有所有下层服务，
并给 LangGraph 节点提供统一的调用入口。

服务组合：
  ┌──────────────────────────────────────────────────┐
  │              StudyAgentService                    │
  │                                                   │
  │  ┌─ HelloAgentsLLM (或 MockLLM) ────── LLM 调用  │
  │  ├─ SimpleAgent (HelloAgents 框架) ─  Agent 封装  │
  │  ├─ StudyContextBuilder ──────────── 上下文拼装   │
  │  ├─ LearningNoteTool ─────────────── 笔记 CRUD    │
  │  ├─ MasteryService ──────────────── 掌握度计算    │
  │  ├─ SimpleKeywordRetriever / ────── 检索回退      │
  │  │  HybridRetriever                               │
  │  └─ StudyState ───────────────────── 内存状态      │
  └──────────────────────────────────────────────────┘

两层 API 设计：
────────────────────────
- 低层方法（被 LangGraph 节点调用）：
  - retriever / note_tool / mastery_service / context_builder (直接暴露属性)
  - prepare_study_brief()   学习简报生成（LLM+启发式）
  - generate_practice_set() 题集生成（复杂，含选项处理/题型规范化）
  - generate_reference_answer() 参考答案生成

- 高层方法（被 main.py 端点调用）：
  - explain() / quiz() / grade() ← 旧版不走图编排，现已被 Graph 层的同名方法取代
  - ingest_material() / chat() ← 仍直接使用，不经过图
  - grade_exam() ← 批量考试模式，不走图

MockLLM 的设计：
────────────────────────
MockLLM 是一个确定性的假 LLM：
- 根据 prompt 中的 "MODE:" 标记分支返回不同的 JSON/文本
- 不依赖任何外部 API，不消耗 token
- 评分使用 token 重叠算法（_score_answer），不是 AI 评分
- 目的是让开发/测试环境在无 API key 时能端到端跑通整个闭环

为什么有 mock 时不走图？
────────────────────────
mock 模式的核心价值是"端到端可测试"——不管在哪个环境，clone 下来
`uv run pytest` 能全绿。mock 对所有 MODE 都返回确定性输出，
而真实 LLM 的输出不确定，测试只能做"字段存在"的弱断言。
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hello_agents import Config, HelloAgentsLLM, SimpleAgent, ToolRegistry
from hello_agents.core.llm_response import LLMResponse, LLMToolResponse

from backend.app.config import BackendSettings

from .learning_note_tool import LearningNoteTool
from .mastery_service import MasteryService
from .obsidian_importer import (
    DEFAULT_OBSIDIAN_IGNORE_DIRS,
    collect_markdown_files,
    prepare_obsidian_document,
)
from .retrieval_service import SimpleKeywordRetriever, create_retriever
from .study_context_builder import StudyContextBuilder, StudyContextConfig
from .study_history_store import PersistentMasteryMap


SYSTEM_PROMPT = (
    "你是 StudyLoop，一个耐心、可靠的学习助手。"
    "请优先使用提供的学习上下文与检索证据进行回答，默认使用简体中文。"
    "在被要求输出 JSON 时，必须严格按照指定字段返回。"
)

JSON_ONLY_PROMPT = "Return JSON only. Do not include markdown fences or extra commentary."

CATEGORY_RULES = [
    {
        "keywords": [
            "context engineering",
            "agent",
            "agents",
            "llm",
            "rag",
            "retrieval",
            "prompt",
            "大模型",
            "智能体",
            "上下文工程",
            "检索增强",
        ],
        "path": ["人工智能", "智能体"],
        "topic": "Context Engineering",
    },
    # ── 细粒度技术分类（优先匹配，避免落入宽泛的"软件开发"）──
    {
        "keywords": [
            "redis",
            "缓存",
            "cache",
            "caching",
            "nosql",
            "key-value",
            "memcached",
        ],
        "path": ["编程", "数据库", "缓存"],
        "topic": "Redis",
    },
    {
        "keywords": [
            "sql",
            "mysql",
            "postgresql",
            "postgres",
            "sqlite",
            "oracle",
            "sql server",
            "关系型数据库",
            "数据库设计",
            "索引",
        ],
        "path": ["编程", "数据库", "关系型数据库"],
        "topic": "关系型数据库",
    },
    {
        "keywords": [
            "docker",
            "kubernetes",
            "k8s",
            "容器",
            "pod",
            "deployment",
            "devops",
            "ci/cd",
        ],
        "path": ["编程", "DevOps"],
        "topic": "容器与 DevOps",
    },
    {
        "keywords": [
            "vue",
            "react",
            "angular",
            "frontend",
            "前端",
            "组件",
            "dom",
        ],
        "path": ["编程", "前端开发"],
        "topic": "前端开发",
    },
    {
        "keywords": [
            "economy",
            "economics",
            "finance",
            "stock",
            "stocks",
            "investment",
            "market",
            "经济",
            "金融",
            "股票",
            "投资",
            "市场",
        ],
        "path": ["经济", "股票"],
        "topic": "经济与投资",
    },
    {
        "keywords": [
            "python",
            "javascript",
            "java",
            "backend",
            "api",
            "programming",
            "code",
            "编程",
            "代码",
            "后端",
        ],
        "path": ["编程", "软件开发"],
        "topic": "软件开发",
    },
    {
        "keywords": [
            "machine learning",
            "deep learning",
            "model",
            "training",
            "ml",
            "ai",
            "机器学习",
            "深度学习",
            "模型训练",
        ],
        "path": ["人工智能", "机器学习"],
        "topic": "机器学习",
    },
]


@dataclass
class StudyState:
    """当前服务持有的学习状态快照。

    ⚠️ 当前限制：所有数据存在内存 dict 里，服务重启即清零。
    这是 MVP 阶段的权衡——先打通闭环，再上 SQLite/Redis 持久化。
    langgraph SqliteSaver checkpoint 是自然的下一站。
    """

    mastery_by_topic: PersistentMasteryMap
    last_grade: dict[str, Any] | None = None
    last_auto_context: dict[str, Any] | None = None


class MockLLM:
    """
    确定性 mock 模型 —— 不依赖任何外部 API。

    评分算法（用于 grade 模式）：
    ──────────────────────────
    _score_answer 用 token 重叠率计算分数：
      score = max(0, min(100, 40 + 重叠率 * 60))
    所以完全无关的作答 score≈40，完全吻合的作答 score≈100。
    这是简单的 Jaccard 式评分，不是 AI 语义评分。

    分支逻辑（_respond 方法）：
      - MODE: quiz   → 返回单道题的 JSON
      - MODE: grade  → 返回批改 JSON（调用 _score_answer）
      - MODE: replan → 返回学习计划 JSON
      - 其他          → 返回 Markdown 讲解文本
    """

    def __init__(self) -> None:
        self.model = "mock-studyllm"
        self.last_call_stats = None

    def invoke(self, messages: list[dict[str, str]], **_: Any) -> LLMResponse:
        prompt = messages[-1]["content"]
        content = self._respond(prompt)
        return LLMResponse(
            content=content,
            model=self.model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0,
        )

    def invoke_with_tools(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]], **_: Any
    ) -> LLMToolResponse:
        response = self.invoke(messages)
        return LLMToolResponse(
            content=response.content,
            tool_calls=[],
            model=self.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )

    def stream_invoke(self, messages: list[dict[str, str]], **_: Any):
        content = self._respond(messages[-1]["content"])
        for chunk in [content[i : i + 40] for i in range(0, len(content), 40)]:
            yield chunk

    def _respond(self, prompt: str) -> str:
        if "MODE: quiz" in prompt:
            topic = self._extract_section(prompt, "Current Topic") or "the current topic"
            payload = {
                "question": f"请解释 {topic} 的核心概念，并给出一个应用场景。",
                "reference_answer": f"{topic} 的定义、关键机制和一个应用场景。",
                "rubric": ["定义准确", "关键机制清晰", "应用场景合理"],
                "difficulty": "medium",
            }
            return json.dumps(payload, ensure_ascii=False)

        if "MODE: grade" in prompt:
            reference = self._extract_field(prompt, "Reference Answer")
            student = self._extract_field(prompt, "Student Answer")
            score = self._score_answer(student, reference)
            mistake_type = self._mistake_type(score)
            payload = {
                "score": score,
                "mistake_type": mistake_type,
                "feedback": self._feedback(score, mistake_type),
                "evidence_used": ["mock-evaluator"],
                "suggested_note": "回到核心定义，补上关键机制与应用场景，再重写一次答案。",
            }
            return json.dumps(payload, ensure_ascii=False)

        if "MODE: replan" in prompt:
            topic = self._extract_section(prompt, "Current Topic") or "the current topic"
            payload = {
                "summary": f"继续巩固 {topic}，先补齐核心定义，再练一次迁移应用。",
                "focus_areas": ["核心概念", "证据使用"],
                "next_actions": [
                    "重新阅读该主题的定义与关键机制。",
                    "结合一条检索证据重写答案。",
                    "完成一道新的应用型练习题。",
                ],
                "recommended_question_types": ["定义题", "应用题", "证据解释题"],
            }
            return json.dumps(payload, ensure_ascii=False)

        topic = self._extract_section(prompt, "Current Topic") or "current topic"
        goal = self._extract_section(prompt, "Learning Goal") or "current learning goal"
        evidence = self._extract_section(prompt, "Evidence") or "No retrieved evidence yet."
        return (
            f"围绕 {topic}，当前学习目标是：{goal}。\n"
            "可以先解释定义，再说明关键机制，最后补一个应用场景。\n"
            f"相关证据摘要：{evidence[:220]}。"
        )

    @staticmethod
    def _extract_section(prompt: str, name: str) -> str:
        pattern = rf"\[{re.escape(name)}\]\n(.*?)(?:\n\[|\Z)"
        match = re.search(pattern, prompt, re.S)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_field(prompt: str, name: str) -> str:
        pattern = rf"{re.escape(name)}:\s*(.*)"
        match = re.search(pattern, prompt)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _score_answer(student: str, reference: str) -> int:
        student_tokens = set(re.findall(r"\w+", student.lower()))
        reference_tokens = set(re.findall(r"\w+", reference.lower()))
        if not reference_tokens:
            return 70 if len(student.strip()) > 20 else 45
        overlap = len(student_tokens & reference_tokens)
        ratio = overlap / max(len(reference_tokens), 1)
        return max(0, min(100, int(40 + ratio * 60)))

    @staticmethod
    def _mistake_type(score: int) -> str:
        if score >= 90:
            return "correct"
        if score >= 75:
            return "shallow_answer"
        if score >= 60:
            return "missing_evidence"
        if score >= 40:
            return "application_weak"
        return "concept_missing"

    @staticmethod
    def _feedback(score: int, mistake_type: str) -> str:
        if mistake_type == "correct":
            return f"你的回答已经比较完整，得分 {score}。可以继续练迁移应用题。"
        return (
            f"当前得分 {score}。主要问题是 {mistake_type}。"
            "建议回到定义、证据和应用场景这三个层次，补齐回答结构。"
        )


class StudyAgentService:
    """
    协调检索、笔记、掌握度更新和 LLM 调用的业务中心。

    初始化时做三件事：
      1. 创建所有下层服务（笔记/掌握度/检索/上下文构建器）
      2. 创建 LLM（MockLLM 或 HelloAgentsLLM）
      3. 用 SimpleAgent + ToolRegistry 注册工具代理

    Agent 配置（全部关闭）：
      trace/session/skills/subagent/todowrite/devlog 全关 ——
      StudyLoop 不需要这些企业级功能，保持最小依赖。
    """

    def __init__(self, settings: BackendSettings):
        self.settings = settings
        self.note_tool = LearningNoteTool(
            settings.notes_dir,
            db_path=settings.resolved_study_history_db_path,
        )
        self.history_store = self.note_tool.store
        self.retriever = create_retriever(settings)
        self.mastery_service = MasteryService()
        self.context_builder = StudyContextBuilder(StudyContextConfig())
        self.state = self._load_persisted_state()

        self.tool_registry = ToolRegistry()
        with contextlib.redirect_stdout(io.StringIO()):
            self.tool_registry.register_tool(self.note_tool)

        self.llm = self._build_llm()
        agent_config = Config(
            trace_enabled=False,
            session_enabled=False,
            skills_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
        )
        self.agent = SimpleAgent(
            name="studyloop",
            llm=self.llm,
            system_prompt=SYSTEM_PROMPT,
            config=agent_config,
            tool_registry=self.tool_registry,
            enable_tool_calling=True,
        )

    def _load_persisted_state(self) -> StudyState:
        """启动时从 SQLite 恢复最近的学习状态。"""
        snapshot = self.history_store.load_state_snapshot()
        return StudyState(
            mastery_by_topic=PersistentMasteryMap(
                snapshot.get("mastery_by_topic"),
                on_change=self.history_store.set_mastery,
            ),
            last_grade=snapshot.get("last_grade"),
            last_auto_context=snapshot.get("last_auto_context"),
        )

    def get_mastery(self, topic: str | None, default: float = 0.5) -> float:
        """统一读取掌握度，避免业务层散落默认值。"""
        if not topic:
            return float(default)
        return float(self.state.mastery_by_topic.get(topic, default))

    def save_last_grade(self, payload: dict[str, Any] | None) -> None:
        """同步更新内存态和 SQLite 中的最近一次批改结果。"""
        self.state.last_grade = payload
        self.history_store.save_last_grade(payload)

    def save_last_auto_context(self, payload: dict[str, Any] | None) -> None:
        """同步更新内存态和 SQLite 中的最近一次自动上下文摘要。"""
        self.state.last_auto_context = payload
        self.history_store.save_last_auto_context(payload)

    def ingest_material(
        self,
        *,
        content: str,
        source: str = "manual",
        title: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        profile = self.organize_knowledge(content=content, source=source, title=title, topic=topic)
        chunks = SimpleKeywordRetriever.chunk_text(
            content,
            source=source,
            topic=profile["primary_topic"],
            extra_metadata={
                "title": profile["title"],
                "topic": profile["primary_topic"],
                "category_path": profile["category_path"],
                "tags": profile["tags"],
                "summary": profile["summary"],
            },
        )
        added = self.retriever.add_documents(chunks)
        note = self.note_tool.create_note(
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
                "source": source,
                "title": profile["title"],
                "topic": profile["primary_topic"],
                "category_path": profile["category_path"],
                "summary": profile["summary"],
                "learning_goal": profile["learning_goal"],
            },
        )
        answer = (
            f"## 资料已入库\n\n"
            f"- 标题：**{profile['title']}**\n"
            f"- 主题：**{profile['primary_topic']}**\n"
            f"- 分类：**{' / '.join(profile['category_path'])}**\n"
            f"- 标签：{', '.join(profile['tags'])}\n"
            f"- 新增切片：{len(added)}\n"
            f"- 当前索引总量：{self.retriever.count()}\n\n"
            f"### 自动摘要\n{profile['summary']}\n"
        )
        return {
            "title": f"知识入库：{profile['title']}",
            "answer": answer,
            "chunk_count": len(added),
            "documents_indexed": self.retriever.count(),
            "source": source,
            "classification": profile,
            "saved_note": note,
        }

    def import_obsidian_vault(
        self,
        *,
        vault_path: str | Path,
        include_subdirs: list[str] | None = None,
        ignore_dirs: list[str] | None = None,
        max_files: int | None = None,
        min_chars: int = 80,
        dry_run: bool = False,
        skip_existing: bool | None = None,
    ) -> dict[str, Any]:
        """批量导入 Obsidian 知识库中的 Markdown 笔记。"""
        vault_root = Path(vault_path).expanduser().resolve()
        if not vault_root.exists():
            raise FileNotFoundError(f"Obsidian vault not found: {vault_root}")
        if not vault_root.is_dir():
            raise NotADirectoryError(f"Obsidian vault is not a directory: {vault_root}")

        # 若启用了持久向量检索，默认按 source 去重；纯内存模式则默认允许重复导入来重建检索缓存。
        effective_skip_existing = (
            self.settings.has_vector_retrieval_config
            if skip_existing is None
            else bool(skip_existing)
        )
        existing_sources = (
            self._existing_knowledge_sources()
            if effective_skip_existing and not dry_run
            else set()
        )
        discovered_files = collect_markdown_files(
            vault_root,
            include_subdirs=include_subdirs,
            ignore_dirs=ignore_dirs,
            max_files=max_files,
        )

        imported_items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []

        for file_path in discovered_files:
            relative_path = file_path.relative_to(vault_root).as_posix()
            source = f"obsidian:{relative_path}"
            if effective_skip_existing and source in existing_sources:
                skipped_items.append(
                    {
                        "relative_path": relative_path,
                        "reason": "already_imported",
                    }
                )
                continue

            try:
                prepared, skip_reason = prepare_obsidian_document(
                    vault_root,
                    file_path,
                    min_chars=min_chars,
                )
                if prepared is None:
                    skipped_items.append(
                        {
                            "relative_path": relative_path,
                            "reason": skip_reason or "skipped",
                        }
                    )
                    continue

                if dry_run:
                    imported_items.append(
                        {
                            "relative_path": prepared.relative_path,
                            "title": prepared.title,
                            "topic_hint": prepared.topic_hint,
                            "folder_hints": prepared.folder_hints,
                            "tags": prepared.tags,
                            "char_count": len(prepared.content),
                        }
                    )
                    continue

                result = self.ingest_material(
                    content=prepared.content,
                    source=prepared.source,
                    title=prepared.title,
                    topic=prepared.topic_hint,
                )
                imported_items.append(
                    {
                        "relative_path": prepared.relative_path,
                        "title": prepared.title,
                        "topic_hint": prepared.topic_hint,
                        "chunk_count": result["chunk_count"],
                        "classification": result["classification"],
                        "saved_note_id": result["saved_note"]["id"],
                    }
                )
                existing_sources.add(prepared.source)
            except Exception as exc:
                failed_items.append(
                    {
                        "relative_path": relative_path,
                        "error": str(exc),
                    }
                )

        preview_limit = 20
        return {
            "vault_path": str(vault_root),
            "include_subdirs": include_subdirs or [],
            "ignored_dirs": sorted({*(ignore_dirs or []), *DEFAULT_OBSIDIAN_IGNORE_DIRS}),
            "dry_run": dry_run,
            "skip_existing": effective_skip_existing,
            "discovered_file_count": len(discovered_files),
            "imported_count": len(imported_items),
            "skipped_count": len(skipped_items),
            "failed_count": len(failed_items),
            "documents_indexed": self.retriever.count(),
            "imported_items": imported_items[:preview_limit],
            "skipped_items": skipped_items[:preview_limit],
            "failed_items": failed_items[:preview_limit],
        }

    def explain(
        self,
        *,
        question: str,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        learner_state: Any = None,
        conversation_context: list[Any] | None = None,
    ) -> dict[str, Any]:
        brief = self.prepare_study_brief(
            seed_text=question,
            intent="explain",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
            conversation_context=conversation_context,
        )
        evidence = self.retriever.search(question or brief["current_topic"], top_k=5)
        notes = self.note_tool.search_notes(brief["current_topic"], limit=3) if brief["current_topic"] else []
        mistakes = self.note_tool.list_notes(
            note_type="mistake_record",
            tag=brief["current_topic"],
            limit=3,
        )
        context = self.context_builder.build(
            learning_goal=brief["learning_goal"],
            current_task=brief["current_task"],
            current_topic=brief["current_topic"],
            learner_state=learner_state
            or {"mastery": self.get_mastery(brief["current_topic"], 0.5)},
            evidence=evidence,
            mistake_history=mistakes,
            learning_notes=notes,
            conversation_context=conversation_context or [],
            output_spec="请使用简体中文 Markdown 作答，包含简明解释、引用到的关键证据，以及下一步学习建议。",
        )
        prompt = (
            "MODE: explain\n"
            f"{context}\n\n"
            f"学习者问题：{question}\n"
            "请仅使用简体中文回答，并优先引用检索到的证据。"
        )
        answer = self.agent.run(prompt)
        self.save_last_auto_context(brief)
        return {
            "title": f"概念讲解：{brief['current_topic']}",
            "answer": answer,
            "context": context,
            "evidence": evidence,
            "auto_context": brief,
        }

    def quiz(
        self,
        *,
        prompt: str | None = None,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        difficulty: str = "medium",
        question_count: int = 1,
        question_types: list[str] | None = None,
        focus_mode: str = "weakest",
        learner_state: Any = None,
    ) -> dict[str, Any]:
        return self.generate_practice_set(
            prompt=prompt,
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
            difficulty=difficulty,
            question_count=question_count,
            question_types=question_types,
            focus_mode=focus_mode,
            learner_state=learner_state,
        )

    def grade(
        self,
        *,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        question: str,
        student_answer: str,
        reference_answer: str | None = None,
    ) -> dict[str, Any]:
        brief = self.prepare_study_brief(
            seed_text=question,
            intent="grade",
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task="请评估学习者回答的完整度、准确性和应用能力。",
        )
        evidence = self.retriever.search(question or brief["current_topic"], top_k=3)
        reference = self.generate_reference_answer(
            question=question,
            current_topic=brief["current_topic"],
            evidence=evidence,
            reference_answer=reference_answer,
        )
        context = self.context_builder.build(
            learning_goal=brief["learning_goal"],
            current_task=brief["current_task"],
            current_topic=brief["current_topic"],
            learner_state={"current_mastery": self.get_mastery(brief["current_topic"], 0.5)},
            evidence=evidence,
            mistake_history=self.note_tool.list_notes(
                note_type="mistake_record",
                tag=brief["current_topic"],
                limit=3,
            ),
            learning_notes=self.note_tool.search_notes(brief["current_topic"], limit=2),
            output_spec="Return strict JSON with score, mistake_type, feedback, evidence_used, suggested_note.",
        )
        prompt = (
            "MODE: grade\nReturn JSON only.\n"
            f"{context}\n\nQuestion: {question}\n"
            f"Reference Answer: {reference['reference_answer']}\n"
            f"Student Answer: {student_answer}"
        )
        raw = self.agent.run(prompt)
        result = self._parse_json(
            raw,
            fallback={
                "score": 60,
                "mistake_type": "shallow_answer",
                "feedback": "你的回答覆盖了部分内容，但还需要补上核心定义、关键机制和更具体的应用说明。",
                "evidence_used": [item.get("source", "retriever") for item in evidence[:2]],
                "suggested_note": "回到核心概念，结合检索到的证据重写一版更完整的回答。",
            },
        )
        score = int(result["score"])
        old_mastery = self.get_mastery(brief["current_topic"], 0.5)
        new_mastery = self.mastery_service.update_mastery(old_mastery, score, result["mistake_type"])
        self.state.mastery_by_topic[brief["current_topic"]] = new_mastery
        self.save_last_grade(result)
        self.save_last_auto_context(brief)

        note = self.note_tool.create_note(
            title=f"Mistake record: {brief['current_topic']}",
            content=(
                f"Question: {question}\n\n"
                f"Student answer: {student_answer}\n\n"
                f"Auto reference answer: {reference['reference_answer']}\n\n"
                f"Feedback: {result['feedback']}\n\n"
                f"Suggested note: {result['suggested_note']}"
            ),
            note_type="mistake_record",
            tags=[brief["current_topic"], result["mistake_type"], *brief["category_path"]],
            metadata={
                "score": score,
                "topic": brief["current_topic"],
                "mastery_before": old_mastery,
                "mastery_after": new_mastery,
                "category_path": brief["category_path"],
            },
        )
        return {
            "result": result,
            "mastery_before": old_mastery,
            "mastery_after": new_mastery,
            "mistake_record_note": note,
            "reference_answer": reference["reference_answer"],
            "reference_rubric": reference["rubric"],
            "auto_context": brief,
        }

    def chat(
        self,
        *,
        message: str,
        conversation_context: list[Any] | None = None,
        save_memory: bool = True,
    ) -> dict[str, Any]:
        brief = self.prepare_study_brief(
            seed_text=message,
            intent="chat",
            conversation_context=conversation_context,
        )
        evidence = self.retriever.search(message or brief["current_topic"], top_k=4)
        notes = self.note_tool.search_notes(brief["current_topic"], limit=2) if brief["current_topic"] else []
        context = self.context_builder.build(
            learning_goal=brief["learning_goal"],
            current_task=brief["current_task"],
            current_topic=brief["current_topic"],
            learner_state={"mastery": self.get_mastery(brief["current_topic"], 0.5)},
            evidence=evidence,
            learning_notes=notes,
            conversation_context=conversation_context or [],
            output_spec="Answer conversationally in markdown, and end with one short recap.",
        )
        prompt = f"MODE: chat\n{context}\n\nUser Message: {message}"
        answer = self.agent.run(prompt)
        memory_summary = self.summarize_conversation_memory(
            message=message,
            answer=answer,
            brief=brief,
        )
        note = None
        if save_memory:
            note = self.note_tool.create_note(
                title=memory_summary["title"],
                content=(
                    f"## 对话总结\n{memory_summary['summary']}\n\n"
                    f"## 用户问题\n{message}\n\n"
                    f"## Agent 回答\n{answer}\n"
                ),
                note_type="conversation_summary",
                tags=[brief["current_topic"], *memory_summary["category_path"], *memory_summary["tags"]],
                metadata=memory_summary,
            )
        self.save_last_auto_context(brief)
        return {
            "title": f"自由对话：{brief['current_topic']}",
            "answer": answer,
            "context": context,
            "evidence": evidence,
            "auto_context": brief,
            "memory_summary": memory_summary,
            "saved_note": note,
        }

    def list_study_topics(self) -> dict[str, Any]:
        primary_map: dict[str, dict[str, Any]] = {}
        leaf_map: dict[str, dict[str, Any]] = {}
        alias_map: dict[str, dict[str, Any]] = {}

        for note in self.note_tool.list_notes(limit=1_000):
            scope = self._topic_scope_from_note(note)
            if not scope:
                continue
            self._register_topic_scope(
                primary_map=primary_map,
                leaf_map=leaf_map,
                alias_map=alias_map,
                scope=scope,
                note=note,
            )

        for topic, mastery in self.state.mastery_by_topic.items():
            scope = self._topic_scope_from_text(str(topic), alias_map)
            if not scope:
                continue
            self._register_topic_scope(
                primary_map=primary_map,
                leaf_map=leaf_map,
                alias_map=alias_map,
                scope=scope,
                mastery=float(mastery),
            )

        if self.state.last_auto_context and self.state.last_auto_context.get("current_topic"):
            scope = self._topic_scope_from_text(
                str(self.state.last_auto_context["current_topic"]),
                alias_map,
            )
            if scope:
                self._register_topic_scope(
                    primary_map=primary_map,
                    leaf_map=leaf_map,
                    alias_map=alias_map,
                    scope=scope,
                )

        topic_tree = []
        for primary_name, primary_node in primary_map.items():
            children = [
                self._finalize_topic_node(leaf_node)
                for leaf_node in leaf_map.values()
                if leaf_node.get("parent_name") == primary_name
            ]
            children.sort(key=self._topic_sort_key)
            serialized_primary = self._finalize_topic_node(primary_node, children=children)
            topic_tree.append(serialized_primary)

        topic_tree.sort(key=self._topic_sort_key)

        topics: list[dict[str, Any]] = []
        for primary_node in topic_tree:
            if primary_node["children"]:
                topics.extend(primary_node["children"])
            else:
                topics.append({key: value for key, value in primary_node.items() if key != "children"})

        recommended_topic = self._select_practice_topic(
            preferred_topic=None,
            focus_mode="weakest",
            topics=topics,
        )
        recommended_entry = next((item for item in topics if item.get("name") == recommended_topic), None)
        return {
            "topics": topics,
            "topic_tree": topic_tree,
            "recommended_topic": recommended_topic,
            "recommended_topic_label": (
                recommended_entry.get("full_path_label") if recommended_entry else recommended_topic
            ),
            "recommended_topic_path": recommended_entry.get("topic_path", []) if recommended_entry else [],
        }

    def _existing_knowledge_sources(self) -> set[str]:
        """读取已保存知识条目的 source，用于可选去重。"""
        sources: set[str] = set()
        for note in self.note_tool.list_notes(
            note_type="knowledge_item",
            limit=10_000,
        ):
            metadata = note.get("metadata") or {}
            source = str(metadata.get("source", "")).strip()
            if source:
                sources.add(source)
        return sources

    def generate_practice_set(
        self,
        *,
        prompt: str | None = None,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        difficulty: str = "medium",
        question_count: int = 1,
        question_types: list[str] | None = None,
        focus_mode: str = "weakest",
        learner_state: Any = None,
    ) -> dict[str, Any]:
        normalized_question_types = self._normalize_question_types(question_types)
        topic_catalog = self.list_study_topics()
        selected_topic = self._select_practice_topic(
            preferred_topic=current_topic,
            focus_mode=focus_mode,
            topics=topic_catalog["topics"],
        )
        seed_text = prompt or selected_topic or learning_goal or "生成中文练习题目。"
        task_text = current_task or self._build_practice_task_text(
            topic=selected_topic,
            question_count=question_count,
            question_types=normalized_question_types,
        )
        brief = self.prepare_study_brief(
            seed_text=seed_text,
            intent="quiz",
            learning_goal=learning_goal,
            current_topic=selected_topic,
            current_task=task_text,
        )
        evidence = self.retriever.search(selected_topic or seed_text, top_k=5)
        notes = self.note_tool.search_notes(brief["current_topic"], limit=3) if brief["current_topic"] else []
        context = self.context_builder.build(
            learning_goal=brief["learning_goal"],
            current_task=brief["current_task"],
            current_topic=brief["current_topic"],
            learner_state=learner_state
            or {"mastery": self.get_mastery(brief["current_topic"], 0.5)},
            evidence=evidence,
            learning_notes=notes,
            output_spec=(
                "Return strict JSON with keys topic, focus_reason, questions. "
                "All content must be in Chinese (Simplified). "
                "Each question must include question_type, question, options, correct_option, "
                "reference_answer, rubric, difficulty."
            ),
        )

        fallback = self._heuristic_practice_set(
            topic=brief["current_topic"],
            difficulty=difficulty,
            question_count=question_count,
            question_types=normalized_question_types,
            prompt=prompt,
            focus_mode=focus_mode,
            topics=topic_catalog["topics"],
            evidence=evidence,
        )
        practice_set = fallback
        if not self.settings.should_use_mock_llm:
            model_prompt = (
                "MODE: quiz_batch\n"
                f"{JSON_ONLY_PROMPT}\n"
                "Generate a practice set in Chinese (Simplified). All questions, options, and reference answers must be in Chinese.\n"
                f"Topic: {brief['current_topic']}\n"
                f"Requested Count: {question_count}\n"
                f"Allowed Question Types: {', '.join(normalized_question_types)}\n"
                f"Difficulty: {difficulty}\n"
                f"User Prompt: {prompt or ''}\n\n"
                f"{context}"
            )
            payload = self._invoke_json(model_prompt, fallback)
            practice_set = self._normalize_practice_set(
                payload,
                fallback,
                difficulty=difficulty,
                question_count=question_count,
                question_types=normalized_question_types,
            )

        self.save_last_auto_context(brief)
        first_open_question = next(
            (question for question in practice_set["questions"] if question["question_type"] == "open_ended"),
            practice_set["questions"][0] if practice_set["questions"] else None,
        )
        result = {
            "title": f"练习题集：{brief['current_topic']}",
            "quiz_set": practice_set,
            "context": context,
            "evidence": evidence,
            "auto_context": brief,
            "topic_catalog": topic_catalog,
        }
        if first_open_question:
            result["quiz"] = {
                "question": first_open_question["question"],
                "question_type": first_open_question["question_type"],
                "reference_answer": first_open_question["reference_answer"],
                "rubric": first_open_question["rubric"],
                "difficulty": first_open_question["difficulty"],
            }
        return result

    def organize_knowledge(
        self,
        *,
        content: str,
        source: str = "manual",
        title: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        fallback = self._heuristic_knowledge_profile(content=content, source=source, title=title, topic=topic)
        if self.settings.should_use_mock_llm:
            return fallback

        prompt = (
            "MODE: organize_knowledge\n"
            f"{JSON_ONLY_PROMPT}\n"
            "请分析下面的知识内容，并返回 JSON。"
            "字段必须包含：title, primary_topic, category_path, tags, summary, learning_goal, current_task。\n"
            "除专有名词外，title、category_path、tags、summary、learning_goal、current_task 请尽量使用简体中文。\n\n"
            f"来源：{source}\n"
            f"已有标题：{title or ''}\n"
            f"已有主题：{topic or ''}\n"
            f"内容：\n{content[:5000]}"
        )
        payload = self._invoke_json(prompt, fallback)
        return self._normalize_profile(payload, fallback)

    def prepare_study_brief(
        self,
        *,
        seed_text: str,
        intent: str,
        learning_goal: str | None = None,
        current_topic: str | None = None,
        current_task: str | None = None,
        conversation_context: list[Any] | None = None,
    ) -> dict[str, Any]:
        fallback = self._heuristic_study_brief(
            seed_text=seed_text,
            intent=intent,
            learning_goal=learning_goal,
            current_topic=current_topic,
            current_task=current_task,
        )
        if self.settings.should_use_mock_llm:
            return fallback

        evidence = self.retriever.search(seed_text or current_topic or learning_goal or intent, top_k=3)
        evidence_preview = "\n".join(
            f"- 主题={item.get('metadata', {}).get('topic', '')}: {item.get('content', '')[:160]}"
            for item in evidence
        )
        prompt = (
            "MODE: derive_study_brief\n"
            f"{JSON_ONLY_PROMPT}\n"
            "请生成一个学习任务简报，并返回 JSON。"
            "字段必须包含：current_topic, learning_goal, current_task, category_path, title。\n"
            "除专有名词外，title、learning_goal、current_task、category_path 请尽量使用简体中文。\n\n"
            f"意图：{intent}\n"
            f"用户输入：{seed_text}\n"
            f"已有学习目标：{learning_goal or ''}\n"
            f"已有主题：{current_topic or ''}\n"
            f"已有任务：{current_task or ''}\n"
            f"对话上下文：{json.dumps(conversation_context or [], ensure_ascii=False)}\n"
            f"检索证据预览：\n{evidence_preview}"
        )
        payload = self._invoke_json(prompt, fallback)
        return self._normalize_brief(payload, fallback, intent=intent, seed_text=seed_text)

    def generate_reference_answer(
        self,
        *,
        question: str,
        current_topic: str,
        evidence: list[dict[str, Any]] | None = None,
        reference_answer: str | None = None,
    ) -> dict[str, Any]:
        fallback = self._heuristic_reference_answer(
            question=question,
            current_topic=current_topic,
            evidence=evidence or [],
            reference_answer=reference_answer,
        )
        if reference_answer:
            return fallback
        if self.settings.should_use_mock_llm:
            return fallback

        evidence_text = "\n".join(f"- {item.get('content', '')[:220]}" for item in (evidence or []))
        prompt = (
            "MODE: draft_reference_answer\n"
            f"{JSON_ONLY_PROMPT}\n"
            "请根据题目与证据生成参考答案，并返回 JSON。"
            "字段必须包含：reference_answer, rubric。\n"
            "reference_answer 与 rubric 默认使用简体中文。\n\n"
            f"当前主题：{current_topic}\n"
            f"题目：{question}\n"
            f"证据：\n{evidence_text}"
        )
        payload = self._invoke_json(prompt, fallback)
        reference_text = str(payload.get("reference_answer", "")).strip() or fallback["reference_answer"]
        rubric = payload.get("rubric") if isinstance(payload.get("rubric"), list) else fallback["rubric"]
        return {
            "reference_answer": reference_text,
            "rubric": [str(item).strip() for item in rubric if str(item).strip()],
        }

    def summarize_conversation_memory(
        self,
        *,
        message: str,
        answer: str,
        brief: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = {
            "title": f"对话沉淀：{brief['current_topic']}",
            "category_path": brief["category_path"],
            "tags": [brief["current_topic"], "对话复盘"],
            "summary": self._truncate_text(
                f"用户围绕 {brief['current_topic']} 发起对话，核心问题是：{message}。回答摘要：{answer}",
                260,
            ),
        }
        if self.settings.should_use_mock_llm:
            return fallback

        prompt = (
            "MODE: summarize_conversation_memory\n"
            f"{JSON_ONLY_PROMPT}\n"
            "请把这段学习对话沉淀为记忆摘要，并返回 JSON。"
            "字段必须包含：title, category_path, tags, summary。"
            "除专有名词外，返回内容默认使用简体中文。\n\n"
            f"自动识别主题：{brief['current_topic']}\n"
            f"分类路径：{json.dumps(brief['category_path'], ensure_ascii=False)}\n"
            f"用户消息：{message}\n"
            f"智能体回答：{answer[:3000]}"
        )
        payload = self._invoke_json(prompt, fallback)
        return {
            "title": str(payload.get("title", fallback["title"])).strip() or fallback["title"],
            "category_path": self._normalize_category_path(payload.get("category_path"), fallback["category_path"]),
            "tags": self._normalize_tags(payload.get("tags"), fallback["tags"]),
            "summary": str(payload.get("summary", fallback["summary"])).strip() or fallback["summary"],
        }

    def get_state(self) -> dict[str, Any]:
        return {
            "documents_indexed": self.retriever.count(),
            "notes": self.note_tool.summary(),
            "mastery_by_topic": dict(self.state.mastery_by_topic),
            "last_grade": self.state.last_grade,
            "last_auto_context": self.state.last_auto_context,
            "llm_mode": "mock" if self.settings.should_use_mock_llm else "openai",
            "retrieval_backend": getattr(
                self.retriever,
                "backend_name",
                type(self.retriever).__name__,
            ),
        }

    def grade_exam(
        self,
        *,
        questions: list[dict[str, Any]],
        topic: str = "",
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        total_score = 0
        total_max = 0

        for q in questions:
            question_id = q.get("question_id", "")
            question_type = q.get("question_type", "open_ended")
            student_answer = (q.get("student_answer") or "").strip()
            reference_answer = (q.get("reference_answer") or "").strip()
            question_text = q.get("question", "")

            if question_type == "multiple_choice":
                max_score = 5
                total_max += max_score
                options = self._normalize_choice_options(q.get("options"), [])
                correct_raw = self._resolve_correct_option(
                    q.get("correct_option"),
                    options,
                    reference_answer,
                )
                given = self._resolve_correct_option(student_answer, options, "") or student_answer.strip()
                is_correct = False
                if given and correct_raw:
                    if given.lower() == correct_raw.lower():
                        is_correct = True
                    else:
                        import difflib
                        ratio = difflib.SequenceMatcher(None, given.lower(), correct_raw.lower()).ratio()
                        is_correct = ratio > 0.85
                score = max_score if is_correct else 0
                total_score += score
                feedback = (
                    "Correct!" if is_correct
                    else (
                        f"Incorrect. The correct answer is: {correct_raw}"
                        if correct_raw
                        else "Incorrect. The correct answer could not be determined from the question payload."
                    )
                )
                results.append({
                    "question_id": question_id,
                    "score": score,
                    "max_score": max_score,
                    "feedback": feedback,
                    "student_answer": student_answer,
                    "correct_answer": correct_raw,
                })
            else:
                max_score = 10
                total_max += max_score
                if not student_answer:
                    results.append({
                        "question_id": question_id,
                        "score": 0,
                        "max_score": max_score,
                        "feedback": "No answer provided.",
                        "student_answer": "",
                        "correct_answer": reference_answer,
                    })
                    continue

                if self.settings.should_use_mock_llm:
                    score = 7
                    feedback = "Good effort. (Mock grading — no LLM configured)"
                    total_score += score
                    results.append({
                        "question_id": question_id,
                        "score": score,
                        "max_score": max_score,
                        "feedback": feedback,
                        "student_answer": student_answer,
                        "correct_answer": reference_answer,
                    })
                    continue

                grade_prompt = (
                    "MODE: grade_open_ended\n"
                    f"{JSON_ONLY_PROMPT}\n"
                    "Grade this open-ended answer on a scale of 1-10. Be fair but rigorous.\n"
                    "Return JSON with keys: score (int 1-10), feedback (string, 1-2 sentences in the same language as the answer).\n\n"
                    f"Question: {question_text}\n"
                    f"Reference Answer: {reference_answer}\n"
                    f"Student Answer: {student_answer}\n"
                )
                try:
                    payload = self._invoke_json(grade_prompt, {"score": 5, "feedback": "Unable to grade."})
                    score = max(1, min(10, int(payload.get("score", 5))))
                    feedback = payload.get("feedback", "Reviewed.")
                except Exception:
                    score = 5
                    feedback = "Grading unavailable — please review manually."

                total_score += score
                results.append({
                    "question_id": question_id,
                    "score": score,
                    "max_score": max_score,
                    "feedback": feedback,
                    "student_answer": student_answer,
                    "correct_answer": reference_answer,
                })

        ratio = total_score / max(total_max, 1)
        if ratio >= 0.9:
            summary = f"Excellent! You scored {total_score}/{total_max}. Strong understanding demonstrated."
        elif ratio >= 0.7:
            summary = f"Good work! You scored {total_score}/{total_max}. A few areas could use review."
        elif ratio >= 0.5:
            summary = f"You scored {total_score}/{total_max}. Review the questions where points were lost."
        else:
            summary = f"You scored {total_score}/{total_max}. Consider revisiting the study material."

        if topic:
            current_mastery = self.get_mastery(topic, 0.5)
            new_mastery = min(1.0, max(0.1, current_mastery + (ratio - 0.5) * 0.3))
            self.state.mastery_by_topic[topic] = round(new_mastery, 3)

        return {
            "results": results,
            "total_score": total_score,
            "total_max": total_max,
            "summary": summary,
        }

    def _build_llm(self) -> HelloAgentsLLM | MockLLM:
        if self.settings.should_use_mock_llm:
            return MockLLM()
        return HelloAgentsLLM(
            model=self.settings.openai_model,
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,
        )

    @staticmethod
    def _parse_json(raw_text: str, fallback: dict[str, Any]) -> dict[str, Any]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.M).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    return fallback
            return fallback

    def _invoke_json(self, user_prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.llm.invoke(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
            return self._parse_json(response.content, fallback)
        except Exception:
            return fallback

    def _heuristic_knowledge_profile(
        self,
        *,
        content: str,
        source: str,
        title: str | None,
        topic: str | None,
    ) -> dict[str, Any]:
        category_path = self._heuristic_category_path(content)
        primary_topic = topic or self._guess_primary_topic(content) or category_path[-1]
        guessed_title = title or self._guess_title(content, primary_topic)
        summary = self._summarize_text(content)
        tags = self._normalize_tags(
            [primary_topic, *category_path, source, self._guess_keyword(content)],
            [primary_topic, *category_path, source],
        )
        return {
            "title": guessed_title,
            "primary_topic": primary_topic,
            "category_path": category_path,
            "tags": tags,
            "summary": summary,
            "learning_goal": f"理解 {primary_topic} 的核心概念，并能结合场景解释其作用。",
            "current_task": "请基于知识库内容完成讲解、问答与知识复盘。",
        }

    def _heuristic_study_brief(
        self,
        *,
        seed_text: str,
        intent: str,
        learning_goal: str | None,
        current_topic: str | None,
        current_task: str | None,
    ) -> dict[str, Any]:
        topic = current_topic or self._extract_topic_from_retriever(seed_text) or self._guess_primary_topic(seed_text)
        if not topic and self.state.last_auto_context:
            topic = self.state.last_auto_context.get("current_topic")
        topic = topic or "当前学习主题"
        category_path = self._extract_category_from_retriever(seed_text) or self._heuristic_category_path(seed_text)
        title_prefix = {
            "explain": "概念讲解",
            "quiz": "生成练习",
            "grade": "答案批改",
            "chat": "自由对话",
        }.get(intent, "学习任务")
        generated_task = current_task or self._default_task_for_intent(intent, topic)
        generated_goal = learning_goal or self._default_goal_for_intent(intent, topic)
        return {
            "title": f"{title_prefix}：{topic}",
            "current_topic": topic,
            "learning_goal": generated_goal,
            "current_task": generated_task,
            "category_path": category_path,
        }

    def _heuristic_reference_answer(
        self,
        *,
        question: str,
        current_topic: str,
        evidence: list[dict[str, Any]],
        reference_answer: str | None,
    ) -> dict[str, Any]:
        if reference_answer:
            return {
                "reference_answer": reference_answer,
                "rubric": ["回答切题", "概念准确", "有场景或机制说明"],
            }
        evidence_lines = [item.get("content", "").strip() for item in evidence if item.get("content")]
        if evidence_lines:
            reference = self._truncate_text(" ".join(evidence_lines[:2]), 220)
        else:
            reference = f"{current_topic} 的定义、关键机制，以及它在当前问题中的一个具体应用。"
        return {
            "reference_answer": reference,
            "rubric": ["回答核心定义", "解释关键机制", "给出应用或例子"],
        }

    def _normalize_profile(self, payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        # 中文优先：如果模型返回纯英文摘要或标题，就优先回退到中文启发式结果，
        # 避免前端出现中英混杂的学习目标、摘要和分类文案。
        return {
            "title": self._prefer_localized_text(payload.get("title"), fallback["title"]),
            "primary_topic": str(payload.get("primary_topic", fallback["primary_topic"])).strip()
            or fallback["primary_topic"],
            "category_path": self._prefer_localized_list(
                self._normalize_category_path(payload.get("category_path"), fallback["category_path"]),
                fallback["category_path"],
            ),
            "tags": self._prefer_localized_list(
                self._normalize_tags(payload.get("tags"), fallback["tags"]),
                fallback["tags"],
            ),
            "summary": self._prefer_localized_text(payload.get("summary"), fallback["summary"]),
            "learning_goal": self._prefer_localized_text(
                payload.get("learning_goal"),
                fallback["learning_goal"],
            ),
            "current_task": self._prefer_localized_text(
                payload.get("current_task"),
                fallback["current_task"],
            ),
        }

    def _normalize_brief(
        self,
        payload: dict[str, Any],
        fallback: dict[str, Any],
        *,
        intent: str,
        seed_text: str,
    ) -> dict[str, Any]:
        topic = str(payload.get("current_topic", fallback["current_topic"])).strip() or fallback["current_topic"]
        return {
            "title": self._prefer_localized_text(payload.get("title"), fallback["title"]),
            "current_topic": topic,
            "learning_goal": self._prefer_localized_text(
                payload.get("learning_goal"),
                fallback["learning_goal"] or self._default_goal_for_intent(intent, topic),
            ),
            "current_task": self._prefer_localized_text(
                payload.get("current_task"),
                fallback["current_task"] or self._default_task_for_intent(intent, topic),
            ),
            "category_path": self._prefer_localized_list(
                self._normalize_category_path(
                    payload.get("category_path"),
                    fallback["category_path"] or self._heuristic_category_path(seed_text),
                ),
                fallback["category_path"] or self._heuristic_category_path(seed_text),
            ),
        }

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        """判断文本里是否包含中日韩字符，用于中文兜底。"""
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _prefer_localized_text(self, value: Any, fallback: str) -> str:
        """优先保留中文文本；若模型返回纯英文，则回退到中文启发式结果。"""
        candidate = str(value or "").strip()
        normalized_fallback = str(fallback or "").strip()
        if not candidate:
            return normalized_fallback
        if self._contains_cjk(normalized_fallback) and not self._contains_cjk(candidate):
            return normalized_fallback
        return candidate

    def _prefer_localized_list(self, values: list[str], fallback: list[str]) -> list[str]:
        """列表字段同样优先中文，防止分类路径和标签全部退回英文。"""
        cleaned_values = [str(item).strip() for item in values if str(item).strip()]
        cleaned_fallback = [str(item).strip() for item in fallback if str(item).strip()]
        if not cleaned_values:
            return cleaned_fallback
        if (
            cleaned_fallback
            and any(self._contains_cjk(item) for item in cleaned_fallback)
            and not any(self._contains_cjk(item) for item in cleaned_values)
        ):
            return cleaned_fallback
        return cleaned_values

    @staticmethod
    def _normalize_category_path(value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or fallback
        if isinstance(value, str) and value.strip():
            parts = [part.strip() for part in re.split(r"[/>、,，|]+", value) if part.strip()]
            return parts or fallback
        return fallback

    @staticmethod
    def _normalize_tags(value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            cleaned = [part.strip() for part in re.split(r"[、,，|]+", value) if part.strip()]
        else:
            cleaned = []
        ordered: list[str] = []
        for item in cleaned or fallback:
            if item and item not in ordered:
                ordered.append(item)
        return ordered[:8]

    def _heuristic_category_path(self, text: str) -> list[str]:
        lowered = text.lower()
        for rule in CATEGORY_RULES:
            if any(keyword in lowered for keyword in rule["keywords"]):
                return list(rule["path"])
        return ["通用知识", "未分类主题"]

    def _guess_primary_topic(self, text: str) -> str:
        lowered = text.lower()
        for rule in CATEGORY_RULES:
            if any(keyword in lowered for keyword in rule["keywords"]):
                return rule["topic"]
        heading_match = re.search(r"^\s*#*\s*([^\n。！？.!?]{4,40})", text, re.M)
        if heading_match:
            return heading_match.group(1).strip()
        mixed_match = re.search(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s]{3,24})", text)
        if mixed_match:
            return mixed_match.group(1).strip()
        return "当前学习主题"

    def _guess_title(self, text: str, topic: str) -> str:
        heading_match = re.search(r"^\s*#*\s*([^\n]{4,48})", text, re.M)
        if heading_match:
            candidate = heading_match.group(1).strip().strip("#").strip()
            if candidate:
                return candidate[:48]
        sentence = re.split(r"[。！？.!?\n]", text.strip())[0].strip()
        if sentence:
            return self._truncate_text(sentence, 42)
        return f"{topic} 学习资料"

    def _summarize_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[。！？.!?])\s+", cleaned)
        summary = " ".join(sentence.strip() for sentence in sentences[:2] if sentence.strip())
        return self._truncate_text(summary or cleaned, 180)

    def _guess_keyword(self, text: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z_-]{1,20}", text)
        if not tokens:
            return "学习资料"
        counts: dict[str, int] = {}
        for token in tokens:
            if len(token) <= 1:
                continue
            counts[token] = counts.get(token, 0) + 1
        if not counts:
            return "学习资料"
        return max(counts.items(), key=lambda item: item[1])[0]

    def _register_topic_scope(
        self,
        *,
        primary_map: dict[str, dict[str, Any]],
        leaf_map: dict[str, dict[str, Any]],
        alias_map: dict[str, dict[str, Any]],
        scope: dict[str, Any],
        note: dict[str, Any] | None = None,
        mastery: float | None = None,
    ) -> None:
        primary_name = str(scope.get("primary_name") or "").strip()
        selection_name = str(scope.get("selection_name") or primary_name).strip()
        if not primary_name or not selection_name:
            return

        primary_node = primary_map.setdefault(
            primary_name,
            self._create_topic_node(
                name=primary_name,
                display_name=primary_name,
                topic_path=[primary_name],
                level=1,
            ),
        )
        self._apply_topic_metrics(primary_node, note=note)

        secondary_name = str(scope.get("secondary_name") or "").strip()
        if secondary_name:
            leaf_node = leaf_map.setdefault(
                selection_name,
                self._create_topic_node(
                    name=selection_name,
                    display_name=secondary_name,
                    topic_path=list(scope.get("topic_path") or [primary_name, secondary_name]),
                    level=2,
                    parent_name=primary_name,
                ),
            )
            self._apply_topic_metrics(leaf_node, note=note, mastery=mastery)
        else:
            self._apply_topic_metrics(primary_node, mastery=mastery)

        for alias in scope.get("aliases", []):
            cleaned_alias = str(alias).strip()
            if cleaned_alias:
                alias_map.setdefault(cleaned_alias, scope)

        alias_map.setdefault(selection_name, scope)
        alias_map.setdefault(
            primary_name,
            {
                "selection_name": primary_name,
                "primary_name": primary_name,
                "secondary_name": "",
                "topic_path": [primary_name],
                "aliases": [primary_name],
            },
        )

    @staticmethod
    def _create_topic_node(
        *,
        name: str,
        display_name: str,
        topic_path: list[str],
        level: int,
        parent_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "display_name": display_name,
            "topic_path": topic_path,
            "level": level,
            "parent_name": parent_name,
            "mastery": None,
            "note_count": 0,
            "mistake_count": 0,
            "_mastery_values": [],
        }

    def _apply_topic_metrics(
        self,
        node: dict[str, Any],
        *,
        note: dict[str, Any] | None = None,
        mastery: float | None = None,
    ) -> None:
        if note:
            node["note_count"] += 1
            if note.get("note_type") == "mistake_record":
                node["mistake_count"] += 1
        if mastery is not None:
            node["_mastery_values"].append(float(mastery))

    def _finalize_topic_node(
        self,
        node: dict[str, Any],
        *,
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        mastery_values = [float(item) for item in node.get("_mastery_values", [])]
        mastery: float | None = None
        if mastery_values:
            mastery = round(sum(mastery_values) / len(mastery_values), 3)
        elif children:
            child_masteries = [float(item["mastery"]) for item in children if item.get("mastery") is not None]
            if child_masteries:
                mastery = round(sum(child_masteries) / len(child_masteries), 3)

        topic_path = [str(item).strip() for item in node.get("topic_path", []) if str(item).strip()]
        full_path_label = " / ".join(topic_path) if topic_path else str(node.get("display_name") or node.get("name") or "")
        return {
            "name": str(node.get("name") or "").strip(),
            "display_name": str(node.get("display_name") or node.get("name") or "").strip(),
            "full_path_label": full_path_label,
            "topic_path": topic_path,
            "level": int(node.get("level", 1) or 1),
            "parent_name": node.get("parent_name"),
            "mastery": mastery,
            "note_count": int(node.get("note_count", 0) or 0),
            "mistake_count": int(node.get("mistake_count", 0) or 0),
            "is_weak": mastery is not None and mastery < 0.65,
            "children": children or [],
        }

    @staticmethod
    def _topic_sort_key(item: dict[str, Any]) -> tuple[Any, int, int, str]:
        return (
            item["mastery"] if item.get("mastery") is not None else 9.0,
            -int(item.get("mistake_count", 0) or 0),
            -int(item.get("note_count", 0) or 0),
            str(item.get("full_path_label") or item.get("display_name") or item.get("name") or ""),
        )

    def _topic_scope_from_note(self, note: dict[str, Any]) -> dict[str, Any] | None:
        metadata = note.get("metadata", {}) if isinstance(note, dict) else {}
        raw_topic = self._topic_from_note(note) or metadata.get("title") or note.get("title")
        category_path = self._category_path_from_note(note)
        if not raw_topic and not category_path:
            return None

        fallback_primary = self._compact_topic_label(raw_topic or "", "")
        primary_name = self._compact_topic_label(category_path[0] if category_path else "", fallback_primary)
        if not primary_name:
            primary_name = fallback_primary or "未分类专题"

        secondary_fallback = category_path[1] if len(category_path) > 1 else ""
        secondary_name = self._compact_topic_label(raw_topic or "", secondary_fallback)
        if not secondary_name or secondary_name == primary_name:
            secondary_name = self._compact_topic_label(secondary_fallback, "")
        if secondary_name == primary_name:
            secondary_name = ""

        topic_path = [primary_name]
        selection_name = primary_name
        if secondary_name:
            topic_path.append(secondary_name)
            selection_name = " / ".join(topic_path)

        aliases = [
            raw_topic,
            metadata.get("title"),
            note.get("title"),
            *category_path,
        ]
        return {
            "primary_name": primary_name,
            "secondary_name": secondary_name,
            "selection_name": selection_name,
            "topic_path": topic_path,
            "aliases": [str(item).strip() for item in aliases if str(item).strip()],
        }

    def _topic_scope_from_text(
        self,
        raw_topic: str,
        alias_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        cleaned_topic = str(raw_topic or "").strip()
        if not cleaned_topic:
            return None
        if cleaned_topic in alias_map:
            return dict(alias_map[cleaned_topic])

        explicit_path = [part.strip() for part in cleaned_topic.split(" / ") if part.strip()]
        if len(explicit_path) >= 2:
            primary_name = self._compact_topic_label(explicit_path[0], explicit_path[0])
            secondary_name = self._compact_topic_label(explicit_path[-1], explicit_path[-1])
        else:
            category_path = self._extract_category_from_retriever(cleaned_topic) or []
            primary_name = self._compact_topic_label(
                category_path[0] if category_path else cleaned_topic,
                cleaned_topic,
            )
            secondary_name = self._compact_topic_label(cleaned_topic, category_path[1] if len(category_path) > 1 else "")
            if secondary_name == primary_name:
                secondary_name = ""

        topic_path = [primary_name]
        selection_name = primary_name
        if secondary_name:
            topic_path.append(secondary_name)
            selection_name = " / ".join(topic_path)

        return {
            "primary_name": primary_name,
            "secondary_name": secondary_name,
            "selection_name": selection_name,
            "topic_path": topic_path,
            "aliases": [cleaned_topic],
        }

    def _category_path_from_note(self, note: dict[str, Any]) -> list[str]:
        metadata = note.get("metadata", {}) if isinstance(note, dict) else {}
        category_path = metadata.get("category_path")
        if isinstance(category_path, list):
            return [str(item).strip() for item in category_path if str(item).strip()]
        return []

    def _compact_topic_label(self, text: str, fallback: str) -> str:
        # 这里做一层用户可读化，避免把超长原始标题直接塞进下拉框。
        candidate = re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n/|,，。；;:-")
        fallback_text = re.sub(r"\s+", " ", str(fallback or "")).strip(" \t\r\n/|,，。；;:-")
        if not candidate:
            return fallback_text

        for separator in ("：", ":", " - ", " – ", " — ", "，", ",", "。", "；", ";", "("):
            if separator in candidate:
                head = candidate.split(separator, 1)[0].strip()
                if 1 < len(head) <= 24:
                    candidate = head
                    break

        if not self._contains_cjk(candidate) and len(candidate) > 26:
            words = re.findall(r"[A-Za-z0-9+#._-]+", candidate)
            if words:
                candidate = " ".join(words[:4])

        if fallback_text and len(candidate) > 24 and len(fallback_text) < len(candidate):
            return fallback_text
        return candidate

    def _topic_from_note(self, note: dict[str, Any]) -> str | None:
        metadata = note.get("metadata", {}) if isinstance(note, dict) else {}
        topic = metadata.get("topic") or metadata.get("current_topic")
        if topic:
            cleaned = str(topic).strip()
            if cleaned:
                return cleaned
        tags = note.get("tags", []) if isinstance(note, dict) else []
        for tag in tags:
            cleaned = str(tag).strip()
            if cleaned and cleaned not in {"mistake_record", "conversation_summary", "knowledge_item"}:
                return cleaned
        return None

    def _select_practice_topic(
        self,
        *,
        preferred_topic: str | None,
        focus_mode: str,
        topics: list[dict[str, Any]],
    ) -> str:
        if preferred_topic and str(preferred_topic).strip():
            return str(preferred_topic).strip()

        normalized_mode = str(focus_mode or "weakest").strip().lower()
        if normalized_mode in {"weakest", "auto"}:
            weak_topics = [item for item in topics if item.get("mastery") is not None]
            if weak_topics:
                weak_topics.sort(
                    key=lambda item: (
                        item.get("mastery", 1.0),
                        -item.get("mistake_count", 0),
                        -item.get("note_count", 0),
                    )
                )
                return str(weak_topics[0]["name"])

        if self.state.last_auto_context and self.state.last_auto_context.get("current_topic"):
            return str(self.state.last_auto_context["current_topic"]).strip()

        if topics:
            return str(topics[0]["name"]).strip()

        return "Current Study Topic"

    @staticmethod
    def _build_practice_task_text(topic: str, question_count: int, question_types: list[str]) -> str:
        type_text = ", ".join(question_types)
        return (
            f"生成{question_count}道关于{topic}的练习题目，"
            f"题型包括：{type_text}。所有内容使用简体中文。"
        )

    @staticmethod
    def _normalize_question_types(question_types: list[str] | None) -> list[str]:
        raw_items = question_types or ["open_ended"]
        normalized: list[str] = []
        for item in raw_items:
            lowered = str(item).strip().lower()
            if lowered in {"multiple_choice", "mcq", "choice"}:
                canonical = "multiple_choice"
            elif lowered in {"open_ended", "open", "short_answer"}:
                canonical = "open_ended"
            else:
                continue
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized or ["open_ended"]

    # Keep this ASCII-only so option parsing stays stable even if the
    # surrounding file encoding changes on Windows terminals.
    @staticmethod
    def _normalize_choice_options(value: Any, fallback: list[str]) -> list[str]:
        raw_items = value if isinstance(value, list) else fallback
        normalized: list[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                label = str(item.get("label", "")).strip()
                text = str(item.get("text") or item.get("value") or item.get("content") or "").strip()
                option = f"{label}. {text}".strip() if label and text else text or label
            else:
                option = str(item).strip()
            if option:
                normalized.append(option)
        return normalized

    @staticmethod
    def _split_choice_option(option: str) -> tuple[str | None, str]:
        text = str(option).strip()
        match = re.match(r"^\s*([A-Ha-h]|\d+)[\.\):]\s*(.+)$", text)
        if not match:
            return None, text
        return match.group(1).upper(), match.group(2).strip()

    @staticmethod
    def _choice_index_from_value(value: str) -> int | None:
        cleaned = str(value).strip().upper().rstrip(".):")
        if not cleaned:
            return None
        if len(cleaned) == 1 and "A" <= cleaned <= "H":
            return ord(cleaned) - ord("A")
        if cleaned.isdigit():
            index = int(cleaned) - 1
            return index if index >= 0 else None
        return None

    def _match_choice_option(self, candidate: str, options: list[str]) -> str:
        cleaned = str(candidate).strip()
        if not cleaned or not options:
            return ""

        lowered = cleaned.lower()
        for option in options:
            if lowered == option.lower():
                return option

        candidate_label, candidate_body = self._split_choice_option(cleaned)
        if candidate_body and candidate_body.lower() != lowered:
            for option in options:
                option_label, option_body = self._split_choice_option(option)
                if candidate_body.lower() == option.lower() or candidate_body.lower() == option_body.lower():
                    return option
                if candidate_label and option_label == candidate_label:
                    return option

        option_index = self._choice_index_from_value(cleaned)
        if option_index is not None and option_index < len(options):
            return options[option_index]

        for option in options:
            option_label, option_body = self._split_choice_option(option)
            if option_label and cleaned.upper().rstrip(".):") == option_label:
                return option
            if option_body and lowered == option_body.lower():
                return option

        return ""

    def _resolve_correct_option(self, value: Any, options: list[str], fallback: str) -> str:
        matched = self._match_choice_option(str(value or "").strip(), options)
        if matched:
            return matched
        return self._match_choice_option(str(fallback or "").strip(), options)

    def _heuristic_practice_set(
        self,
        *,
        topic: str,
        difficulty: str,
        question_count: int,
        question_types: list[str],
        prompt: str | None,
        focus_mode: str,
        topics: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reference = self.generate_reference_answer(
            question=prompt or f"Explain {topic}",
            current_topic=topic,
            evidence=evidence,
        )
        reason = "Focused on the selected topic."
        if not prompt and focus_mode in {"weakest", "auto"}:
            reason = "Focused on the weakest or most recently active topic."
        if topics:
            matched_topic = next((item for item in topics if item.get("name") == topic), None)
            if matched_topic and matched_topic.get("mastery") is not None:
                reason = (
                    f"Focused on {topic} because its mastery is "
                    f"{int(float(matched_topic['mastery']) * 100)}%."
                )

        questions: list[dict[str, Any]] = []
        for index in range(question_count):
            question_type = question_types[index % len(question_types)]
            question_id = f"q{index + 1}"
            if question_type == "multiple_choice":
                questions.append(
                    {
                        "question_id": question_id,
                        "question_type": "multiple_choice",
                        "question": f"[{index + 1}] 以下哪个选项最符合{topic}的核心概念？",
                        "options": [
                            "明确目标并在回答前检索相关证据。",
                            "忽略上下文，直接凭第一印象回答。",
                            "尽可能添加不相关的信息。",
                            "将所有问题视为相同，跳过主题选择。",
                        ],
                        "correct_option": "明确目标并在回答前检索相关证据。",
                        "reference_answer": reference["reference_answer"],
                        "rubric": ["选择与主题目标及证据使用一致的选项。"],
                        "difficulty": difficulty,
                    }
                )
                continue

            questions.append(
                {
                    "question_id": question_id,
                    "question_type": "open_ended",
                    "question": f"[{index + 1}] 解释{topic}的核心概念并给出一个实际例子。",
                    "options": [],
                    "correct_option": "",
                    "reference_answer": reference["reference_answer"],
                    "rubric": reference["rubric"],
                    "difficulty": difficulty,
                }
            )

        return {
            "topic": topic,
            "focus_reason": reason,
            "difficulty": difficulty,
            "question_count": question_count,
            "question_types": question_types,
            "questions": questions,
        }

    def _normalize_practice_set(
        self,
        payload: dict[str, Any],
        fallback: dict[str, Any],
        *,
        difficulty: str,
        question_count: int,
        question_types: list[str],
    ) -> dict[str, Any]:
        raw_questions = payload.get("questions")
        normalized_questions: list[dict[str, Any]] = []
        if isinstance(raw_questions, list):
            for index, item in enumerate(raw_questions):
                if not isinstance(item, dict):
                    continue
                fallback_question = fallback["questions"][min(index, len(fallback["questions"]) - 1)]
                question_type = str(
                    item.get("question_type") or item.get("type") or fallback_question["question_type"]
                ).strip()
                if question_type not in {"multiple_choice", "open_ended"}:
                    question_type = fallback_question["question_type"]
                options = self._normalize_choice_options(item.get("options"), fallback_question["options"])
                reference_answer = str(
                    item.get("reference_answer", fallback_question["reference_answer"])
                ).strip() or fallback_question["reference_answer"]
                rubric_value = item.get("rubric")
                rubric = (
                    [str(entry).strip() for entry in rubric_value if str(entry).strip()]
                    if isinstance(rubric_value, list)
                    else fallback_question["rubric"]
                )
                question_text = str(item.get("question", fallback_question["question"])).strip()
                question_text = question_text or fallback_question["question"]
                difficulty_value = str(item.get("difficulty", difficulty)).strip() or difficulty
                correct_option = self._resolve_correct_option(
                    item.get("correct_option"),
                    options,
                    fallback_question["correct_option"],
                )

                if question_type == "multiple_choice":
                    fallback_options = self._normalize_choice_options(
                        fallback_question["options"],
                        fallback_question["options"],
                    )
                    fallback_correct_option = self._resolve_correct_option(
                        fallback_question["correct_option"],
                        fallback_options,
                        "",
                    )
                    if len(options) < 2 or not correct_option:
                        question_text = fallback_question["question"]
                        options = fallback_options
                        correct_option = fallback_correct_option
                        reference_answer = fallback_question["reference_answer"]
                        rubric = fallback_question["rubric"]
                        difficulty_value = fallback_question["difficulty"]
                    elif not reference_answer:
                        reference_answer = correct_option
                else:
                    options = []
                    correct_option = ""

                normalized_questions.append(
                    {
                        "question_id": str(item.get("question_id", fallback_question["question_id"])).strip()
                        or fallback_question["question_id"],
                        "question_type": question_type,
                        "question": question_text,
                        "options": options,
                        "correct_option": correct_option,
                        "reference_answer": reference_answer,
                        "rubric": rubric,
                        "difficulty": difficulty_value,
                    }
                )

        if not normalized_questions:
            normalized_questions = fallback["questions"]

        return {
            "topic": str(payload.get("topic", fallback["topic"])).strip() or fallback["topic"],
            "focus_reason": str(payload.get("focus_reason", fallback["focus_reason"])).strip()
            or fallback["focus_reason"],
            "difficulty": str(payload.get("difficulty", difficulty)).strip() or difficulty,
            "question_count": int(payload.get("question_count", question_count) or question_count),
            "question_types": self._normalize_question_types(payload.get("question_types") or question_types),
            "questions": normalized_questions[:question_count],
        }

    def _extract_topic_from_retriever(self, query: str) -> str | None:
        evidence = self.retriever.search(query, top_k=1)
        if not evidence:
            return None
        metadata = evidence[0].get("metadata", {})
        topic = metadata.get("topic")
        return str(topic).strip() if topic else None

    def _extract_category_from_retriever(self, query: str) -> list[str] | None:
        evidence = self.retriever.search(query, top_k=1)
        if not evidence:
            return None
        metadata = evidence[0].get("metadata", {})
        category_path = metadata.get("category_path")
        if isinstance(category_path, list) and category_path:
            return [str(item).strip() for item in category_path if str(item).strip()]
        return None

    @staticmethod
    def _default_goal_for_intent(intent: str, topic: str) -> str:
        if intent == "quiz":
            return f"检验并强化自己对 {topic} 的掌握程度。"
        if intent == "grade":
            return f"诊断当前对 {topic} 的理解薄弱点并完成改进。"
        if intent == "chat":
            return f"围绕 {topic} 进行对话，并把有效知识沉淀下来。"
        return f"理解 {topic}，并能够用清晰、结构化的方式解释它。"

    @staticmethod
    def _default_task_for_intent(intent: str, topic: str) -> str:
        if intent == "quiz":
            return f"请根据 {topic} 生成一道能够检验理解深度的练习题。"
        if intent == "grade":
            return f"请批改关于 {topic} 的回答，并指出概念、证据和应用层面的不足。"
        if intent == "chat":
            return f"请围绕 {topic} 进行自然对话，并给出可沉淀的知识总结。"
        return f"请围绕 {topic} 做概念讲解，并结合证据解释它为什么重要。"

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: max(limit - 3, 0)].rstrip() + "..."
