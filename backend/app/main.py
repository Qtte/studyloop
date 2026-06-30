"""
StudyLoop Backend MVP —— FastAPI 应用入口。

═══════════════════════════════════════════════════════════════════════════════
架构概览
═══════════════════════════════════════════════════════════════════════════════

这个文件是 StudyLoop 的"门面"——HTTP 请求在这里被解析、验证、转发到 LangGraph 编排。

请求路径：
  HTTP Request
      │
      ▼
  Pydantic Model（自动校验，FastAPI 内置）
      │
      ▼
  get_study_graph().explain/quiz/grade(...)   ← LangGraph 入口
      │
      ▼
  StudyLoopGraph.invoke(payload)             ← 图编排
      │
      ▼
  return dict → FastAPI 自动 JSON 序列化

三层架构：
────────────────────────
┌─────────────────────────────┐
│  main.py          ← HTTP 层 │  请求解析 + 响应拼装
├─────────────────────────────┤
│  agents/graph.py  ← 编排层 │  StateGraph + 条件边
├─────────────────────────────┤
│  agents/nodes.py  ← 节点层 │  纯函数，每个做一件事
│  services/*.py    ← 业务层 │  检索/笔记/掌握度/上下文
│  schemas/*.py     ← 数据层 │  Pydantic 模型
└─────────────────────────────┘

依赖注入模式：
────────────────────────
- get_settings()       → 单例 BackendSettings（读取 .env）
- get_study_service()  → 单例 StudyAgentService（持有 settings + 所有服务）
- get_study_graph()    → 单例 StudyLoopGraph（持有 service + compiled graph）

三个都是 @lru_cache(maxsize=1) 单例：同一进程内只创建一次，后续请求复用。
这不是生产级的多租户方案（没有用户隔离），但 MVP 足够。
"""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from backend.app.agents.graph import StudyLoopGraph
from backend.app.config import BackendSettings, get_settings
from backend.app.mcp_server import init_mcp, mcp
from backend.app.schemas.study_loop import ExamGradingResponse, ExamSubmissionRequest
from backend.app.services.study_agent_service import StudyAgentService


# ═══════════════════════════════════════════════════════════════════════════
# 请求体模型
# ═══════════════════════════════════════════════════════════════════════════
# 这些模型只在 HTTP 层使用 —— 校验用户输入，不参与图编排。


class IngestRequest(BaseModel):
    """资料导入请求 —— 内容 + 可选元数据。"""
    content: str = Field(..., min_length=1)
    source: str = "manual"
    title: str | None = None
    topic: str | None = None


class ObsidianImportRequest(BaseModel):
    """Obsidian 知识库批量导入请求。"""

    vault_path: str = Field(..., min_length=1)
    include_subdirs: list[str] | None = None
    ignore_dirs: list[str] | None = None
    max_files: int | None = Field(default=None, ge=1, le=10000)
    min_chars: int = Field(default=80, ge=0, le=100000)
    dry_run: bool = False
    skip_existing: bool | None = None


class ExplainRequest(BaseModel):
    """讲解请求 —— 必填问题，其余可选（由后端自动推导）。"""
    question: str
    learning_goal: str | None = None
    current_topic: str | None = None
    current_task: str | None = None
    learner_state: dict[str, Any] | None = None
    conversation_context: list[Any] | None = None


class QuizRequest(BaseModel):
    """
    出题请求 —— 支持多维度控制。

    关键字段说明：
      - prompt:           直接给出题指令（优先级最高）
      - focus_mode:       "weakest"打最弱主题|"manual"手动指定|"auto"自动
      - question_types:   ["multiple_choice","open_ended"] 混合题型
      - question_count:   1-10 道
    """
    prompt: str | None = None
    learning_goal: str | None = None
    current_topic: str | None = None
    current_task: str | None = None
    difficulty: str = "medium"
    question_count: int = Field(default=1, ge=1, le=10)
    question_types: list[str] | None = None
    focus_mode: str = "weakest"
    learner_state: dict[str, Any] | None = None


class GradeRequest(BaseModel):
    """批改请求 —— 题目 + 学生作答，参考答案可选。"""
    question: str
    student_answer: str
    current_topic: str | None = None
    reference_answer: str | None = None
    learning_goal: str | None = None


class ChatRequest(BaseModel):
    """自由对话请求 —— 非结构化聊天 + 可选记忆保存。"""
    message: str = Field(..., min_length=1)
    conversation_context: list[Any] | None = None
    save_memory: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# 单例工厂（依赖注入）
# ═══════════════════════════════════════════════════════════════════════════


@lru_cache(maxsize=1)
def get_study_service() -> StudyAgentService:
    """创建并缓存 StudyAgentService 单例。"""
    settings: BackendSettings = get_settings()
    return StudyAgentService(settings)


@lru_cache(maxsize=1)
def get_study_graph() -> StudyLoopGraph:
    """创建并缓存 StudyLoopGraph 单例（内含 compiled LangGraph）。"""
    return StudyLoopGraph(get_study_service())


# ═══════════════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="StudyLoop Backend MVP", version="0.2.0")

# ── CORS 配置 ──
# 允许本地开发前端（Vite dev server on 8080/8081 + FastAPI on 8765/8000）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost:8765",
        "http://127.0.0.1:8765",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 静态文件挂载 ──
# 如果前端 build 产物（frontend/dist/）存在则用 build 产物，
# 否则 fallback 到自带的 static 目录（旧版调试页面）。
# "/" 路由返回 index.html，实现 SPA 模式。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_STATIC_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
STATIC_DIR = FRONTEND_DIST_DIR if FRONTEND_DIST_DIR.exists() else LEGACY_STATIC_DIR
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ── MCP 服务挂载 ──
# 在 /mcp 路径上提供 SSE 传输的 MCP 工具，供 Claude Desktop 等客户端连接。
# 初始化时注入 StudyAgentService 实例。
init_mcp(get_study_service())
app.mount("/mcp", mcp.sse_app())


@app.get("/")
def frontend() -> FileResponse:
    """SPA 入口 —— 返回前端 index.html。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    """
    健康检查 —— 返回当前 LLM 模式。

    前端用这个接口在 sidebar 显示"Mock 模式"或"OpenAI 模式"的 badge。
    """
    settings = get_settings()
    return {
        "status": "ok",
        "llm_mode": "mock" if settings.should_use_mock_llm else "openai",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 核心学习闭环 API
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/knowledge/ingest")
def ingest_material(request: IngestRequest) -> dict[str, Any]:
    """
    资料导入 —— 将学习材料切块、入检索库、创建知识笔记。

    这是学习闭环的第一步：先把资料"喂"给系统。
    导入后：
      - 内容被切分为 600 字符左右的段落（chunk_text）
      - 存入关键词检索器（SimpleKeywordRetriever）
      - 创建一条 note_type="knowledge_item" 的笔记（含自动摘要/分类）
      - 主题被加入主题目录（list_study_topics 可查）

    返回字段：
      - title:               知识条目标题（AI 或启发式生成）
      - answer:              入库确认 + 自动摘要（Markdown）
      - chunk_count:         切分后的段落数
      - documents_indexed:   当前索引总量
      - classification:      {title, primary_topic, category_path, tags, summary}
    """
    service = get_study_service()
    return service.ingest_material(
        content=request.content,
        source=request.source,
        title=request.title,
        topic=request.topic,
    )


@app.post("/knowledge/import-obsidian")
def import_obsidian_vault(request: ObsidianImportRequest) -> dict[str, Any]:
    """批量导入本地 Obsidian vault 中的 Markdown 笔记。"""
    service = get_study_service()
    return service.import_obsidian_vault(
        vault_path=request.vault_path,
        include_subdirs=request.include_subdirs,
        ignore_dirs=request.ignore_dirs,
        max_files=request.max_files,
        min_chars=request.min_chars,
        dry_run=request.dry_run,
        skip_existing=request.skip_existing,
    )


@app.post("/study/explain")
def study_explain(request: ExplainRequest) -> dict[str, Any]:
    """
    知识讲解 —— 围绕问题生成有证据支撑的概念解释。

    流程（通过 LangGraph）：
      parse_user_intent → retrieve_materials → retrieve_learning_notes
      → build_study_context → explain_concept → END

    返回字段：
      - answer:     Markdown 格式的概念讲解
      - context:    10 段标准化学习上下文
      - evidence:   检索到的资料切片
      - auto_context: AI/启发式推导的学习简报（当前主题、学习目标等）
    """
    graph = get_study_graph()
    return graph.explain(
        question=request.question,
        learning_goal=request.learning_goal,
        current_topic=request.current_topic,
        current_task=request.current_task,
        learner_state=request.learner_state,
        conversation_context=request.conversation_context,
    )


@app.post("/study/quiz")
def study_quiz(request: QuizRequest) -> dict[str, Any]:
    """
    生成练习 —— 产出一道或多道练习题（单选 +/或 开放题）。

    流程（通过 LangGraph）：
      parse_user_intent → retrieve_materials → retrieve_learning_notes
      → build_study_context → generate_quiz → END

    返回字段：
      - quiz:        第一道开放题（可同步到批改区）
      - quiz_set:    完整题集 {topic, focus_reason, questions[{question_id, question_type,
                      question, options, correct_option, reference_answer, rubric, difficulty}]}
      - context:     10 段学习上下文
      - evidence:    检索证据
      - auto_context: 学习简报
    """
    graph = get_study_graph()
    return graph.quiz(
        prompt=request.prompt,
        learning_goal=request.learning_goal,
        current_topic=request.current_topic,
        current_task=request.current_task,
        difficulty=request.difficulty,
        question_count=request.question_count,
        question_types=request.question_types,
        focus_mode=request.focus_mode,
        learner_state=request.learner_state,
    )


@app.post("/study/grade")
def study_grade(request: GradeRequest) -> dict[str, Any]:
    """
    答案批改 —— 评分、错误分类、掌握度更新、学习重规划。

    这是学习闭环最"厚"的接口：
    1. 预生成参考答案（如果用户没提供）
    2. 进入 LangGraph：grade_answer → update_memory → replan_learning_path
    3. ★ 如果掌握度 < 0.6：条件边回连 generate_quiz 出一道补练题

    返回字段：
      - result:             {score, mistake_type, feedback, evidence_used, suggested_note}
      - mastery_before:     批改前掌握度
      - mastery_after:      批改后掌握度
      - next_plan:          {summary, focus_areas, next_actions, recommended_question_types}
      - remediation_quiz:   补练题目（掌握度不达标时非空）★
      - retry_count:        补练次数（0 或 1）
      - mastery_threshold:  补练触发阈值（当前 0.6）
    """
    graph = get_study_graph()
    return graph.grade(
        learning_goal=request.learning_goal,
        current_topic=request.current_topic,
        question=request.question,
        student_answer=request.student_answer,
        reference_answer=request.reference_answer,
    )


@app.post("/study/session/start")
def session_start(request: QuizRequest) -> dict[str, Any]:
    """
    HITL 交互式学习会话 —— 启动并出题（等待学生作答后调 /study/session/resume）。

    与普通 /study/quiz 的区别：
      - 图在出题后暂停（interrupt_after），不会一次性走到 END
      - 返回的 thread_id 需要在下一步传给 /study/session/resume
      - 恢复后继续走批改 → 更新掌握度 → 重规划，若掌握度不足还会再暂停出补练题

    请求体（同 QuizRequest）：
      prompt / current_topic / difficulty / question_count / question_types / focus_mode

    返回：
      thread_id:   会话 ID（调 resume 时传入）
      quiz:        第一道题目
      context:     10 段上下文
      evidence:    检索证据
      auto_context: 学习简报

    调用方拿到结果后：
      1. 展示 quiz 给学生
      2. 收集学生答案
      3. 调用 POST /study/session/resume { thread_id, student_answer }
    """
    graph = get_study_graph()
    return graph.session_start(
        prompt=request.prompt,
        learning_goal=request.learning_goal,
        current_topic=request.current_topic,
        current_task=request.current_task,
        difficulty=request.difficulty,
        question_count=request.question_count,
        question_types=request.question_types,
        focus_mode=request.focus_mode,
    )


class SessionResumeRequest(BaseModel):
    """HITL 会话恢复请求 —— 传入学生作答继续图编排。"""

    thread_id: str
    student_answer: str = Field(..., min_length=1)
    reference_answer: str | None = None


@app.post("/study/session/resume")
def session_resume(request: SessionResumeRequest) -> dict[str, Any]:
    """
    HITL 交互式学习会话 —— 恢复图执行，注入学生作答。

    流程：
      注入学生答案 → 恢复图执行 → grade_answer → update_memory → replan
      →（若掌握度不达标且轮次未超上限）→ generate_quiz → 再次暂停 → 返回补练题
      或 → END

    返回：
      session_complete: True 表示会话已结束
      result:           批改结果 {score, mistake_type, feedback, evidence_used, suggested_note}
      mastery_before/after
      next_plan:        下一步学习建议
      session_rounds:   已完成轮次
      quiz:             当 session_complete=False 时，为新的补练题
      next_action:      "answer"（需要学生继续答题）| "done"（会话结束）
    """
    graph = get_study_graph()
    return graph.session_resume(
        thread_id=request.thread_id,
        student_answer=request.student_answer,
        reference_answer=request.reference_answer,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 辅助 API
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/chat")
def study_chat(request: ChatRequest) -> dict[str, Any]:
    """
    自由对话 —— 围绕学习主题的非结构化聊天。

    与 explain 不同：chat 模式会保存对话总结笔记（save_memory=True 时），
    而 explain 是一次性讲解不保存。
    """
    service = get_study_service()
    return service.chat(
        message=request.message,
        conversation_context=request.conversation_context,
        save_memory=request.save_memory,
    )


@app.get("/notes")
def get_notes(
    query: str | None = None,
    note_type: str | None = None,
    tag: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """
    笔记查询 —— 按关键词搜索 或 按类型/标签过滤。

    查询模式：
      - 传 query → 关键词搜索（search_notes）
      - 不传 query → 列表过滤（list_notes）
    """
    service = get_study_service()
    if query:
        return {"notes": service.note_tool.search_notes(query, limit=limit)}
    return {
        "notes": service.note_tool.list_notes(
            note_type=note_type, tag=tag, limit=limit
        )
    }


@app.get("/study/state")
def get_study_state() -> dict[str, Any]:
    """
    学习状态总览 —— 前端 sidebar 用。

    返回：
      - documents_indexed:  检索库总量
      - notes:              笔记统计（by_type 分布）
      - mastery_by_topic:   各主题掌握度
      - last_grade:         最近一次批改结果
      - llm_mode:           mock / openai
      - retrieval_backend:  keyword_memory / qdrant_vector / hybrid
    """
    service = get_study_service()
    return service.get_state()


@app.get("/study/topics")
def get_study_topics() -> dict[str, Any]:
    """
    主题目录 —— 返回层级化的主题树 + 推荐练习主题。

    返回：
      - topics:             扁平主题列表（含 mastery / note_count / mistake_count）
      - topic_tree:         层级主题树（二级结构）
      - recommended_topic:  推荐练习主题（当前最弱或最近活跃）
    """
    service = get_study_service()
    return service.list_study_topics()


# ═══════════════════════════════════════════════════════════════════════════
# 考试批量批改（独立流程，不走图编排）
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/study/exam/submit", response_model=ExamGradingResponse)
def submit_exam(request: ExamSubmissionRequest) -> dict[str, Any]:
    """
    考试批量提交和批改 —— 多道题一起提交，一次给结果。

    这与正常的 grade 流程不同：
      - 不走 LangGraph（不触发记忆更新/重规划/补练回路）
      - 选择题：精确匹配 correct_option（或模糊匹配选字母）
      - 开放题：逐题调 LLM 打分（或 mock 返回 7）
      - 计算总分和总评

    适用场景：学生做完一套练习后一次性提交，而不是逐题批改。
    """
    service = get_study_service()
    questions = [
        (
            question.model_dump()
            if hasattr(question, "model_dump")
            else question.dict()
        )
        for question in request.questions
    ]
    return service.grade_exam(questions=questions, topic=request.topic)
