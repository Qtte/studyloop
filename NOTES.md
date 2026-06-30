# StudyLoop 学习笔记

> 边看项目边记，用于面试时能讲清楚设计决策。

---

## Q1: 当前架构（两次HTTP） vs 真 HITL（LangGraph interrupt/Command）的区别

### 当前架构 —— 两次独立的 HTTP 请求

```
请求1: POST /study/quiz               请求2: POST /study/grade
┌──────────────────────┐              ┌──────────────────────┐
│ START                │              │ START                │
│  ↓                   │              │  ↓                   │
│ parse_user_intent    │              │ parse_user_intent    │
│  ↓                   │              │  ↓                   │
│ retrieve_materials   │              │ retrieve_materials   │
│  ↓                   │   ┌─前端─┐   │  ↓                   │
│ build_study_context  │   │展示  │   │ build_study_context  │
│  ↓                   │   │题目  │   │  ↓                   │
│ generate_quiz        │   │↓    │   │ grade_answer         │
│  ↓                   │   │学生  │   │  ↓                   │
│ END                  │   │作答  │   │ update_memory        │
│ (返回题目)            │   │↓    │   │  ↓                   │
└──────────────────────┘   │调   │   │ replan_learning_path │
                           │grade│   │  ↓                   │
                           │接口  │   │ END                  │
                           └─────┘   │ (返回批改+补练)       │
                                     └──────────────────────┘
```

**本质**：两次独立的图调用，state 不跨请求共享。前端充当人肉胶水。

### 真 HITL —— LangGraph `interrupt()` + `Command(resume=...)`

```
                     单次会话: POST /study/session/start  →  [等待回答]  →  POST /study/session/resume

┌────────────────────────────────────────────────────────────────┐
│ START → parse → retrieve → context → generate_quiz            │
│                                                    ↓          │
│                                            interrupt("await_answer")
│                                                    ↓          │
│                                        图暂停，返回题目给前端    │
│                                               ┌────────┐      │
│                                               │前端展示  │      │
│                                               │学生作答  │      │
│                                               │调resume │      │
│                                               └───┬────┘      │
│                                                   ↓           │
│                                    Command(resume="学生答案")   │
│                                                   ↓           │
│                                grade_answer → update_memory    │
│                                → replan → (弱→generate_quiz)   │
│                                                   ↓           │
│                                                  END          │
└────────────────────────────────────────────────────────────────┘
```

**本质**：同一次图调用，中途挂起等人类，resume 后从断点继续。

### 对比表

| | 当前（两次请求拼接） | HITL（interrupt/Command） |
|---|---|---|
| 图调用次数 | 2 次（从 START 开始） | 1 次（中断后继续） |
| state 连续性 | 不共享，new state | 同一个 state 实例 |
| 上下文 | grade 时重新 retrieve/build | 沿用 quiz 阶段的 context |
| 前端职责 | 手动取题目拼 grade 请求 | 只需回传答案给 resume |
| 补练回路 | 只在单次 grade 内生效 | 可再挂起、resume，真正"循环" |
| checkpoint | 无 | LangGraph 自动存（可接 SQLite） |

**类比**：当前是"打电话，每次挂断再拨号"；HITL 是"一个电话中途说等一下，回来继续"。

### 为什么当前没做 HITL
- HITL 需要在 graph 节点里写 `interrupt()`
- 前端需从"一次性 POST"变成"等响应+POST resume"，多一个状态机
- MVP 先验证闭环逻辑正确，再做 HITL 升级

---

## Q2: HITL（Human-in-the-Loop）实现

### 技术选型

LangGraph 0.6.11（没有新版 `interrupt()`/`Command` 函数），用 **`interrupt_after` 编译选项 + `MemorySaver` checkpointer** 实现 HITL。

核心机制：
```
graph.compile(checkpointer=memory_saver, interrupt_after=["generate_quiz"])
```

- 每次 `generate_quiz` 节点执行完后图自动暂停（不像新版用 `interrupt()` 函数，这里是声明式的）
- `invoke(None, config)` 恢复执行（`config` 必须带相同的 `thread_id`）
- `update_state(config, {"field": "value"})` 在恢复前注入学生答案
- 补练回路回连 `generate_quiz` 时，`interrupt_after` 对每轮都生效 → 自动再暂停

### 图结构（与主图不同）

主图有三岔路（`_route_after_context` 分 explain/quiz/grade），HITL 图是线性链：

```
START → parse → retrieve → context → generate_quiz
                                           │ [⏸ 暂停，等学生作答]
                                           │ update_state({"user_answer": ...})
                                           │ invoke(None, config)
                                           ▼
                                      grade_answer
                                           │
                                           ▼
                                      update_memory（session_rounds++）
                                           │
                                           ▼
                                      replan_learning_path
                                           │
                               ┌───────────┴───────────┐
                               │ mastery<0.6 &          │ mastery≥0.6
                               │ rounds<max(3)          │ or rounds≥3
                               ▼                        ▼
                         generate_quiz               END
                         [⏸ 再暂停       (session_complete=True)
                          等补练答案]
```

### 两个入口方法（`StudyLoopGraph`）

| 方法 | 触发 | 返回 |
|---|---|---|
| `session_start()` | `POST /study/session/start` | `thread_id`, `quiz`, `context` |
| `session_resume(thread_id, student_answer)` | `POST /study/session/resume` | `session_complete`, `result`, `next_action` |

### 关键设计决策

**`session_rounds` 在 `update_memory` 节点递增**
每次完成批改（一轮问答）`session_rounds++`。在 `_route_hitl_replan` 判断是否达到 `max_session_rounds（3）`。

**同一个 `MemorySaver` checkpointer 实例**
`MemorySaver` 存在 `StudyLoopGraph` 实例中（`self.checkpointer`）。`session_start` 和 `session_resume` 通过 `thread_id` 定位同一个 checkpoint。目前是进程内内存，重启丢失——升级到 `SqliteSaver` 即可持久化。

**`_route_hitl_replan` 与 `_route_after_replan` 无关**
两个图用各自的路由函数：主图用 `_route_after_replan`（看 `retry_count < max_retries`），HITL 图用 `_route_hitl_replan`（看 `session_rounds < max_session_rounds`）。

### 仍然不在本轮做的
- 前端轮询/SSE 适配（当前需前端自己调两次接口）
- `SqliteSaver` 持久化（`InMemorySaver` 已满足测试）
- `interrupt_before` 在 `grade_answer` 前再加一道确认（学生确认提交）

---

**问**：现在持久化存储存的是什么内容，具体是什么？

**答**：所有持久化数据集中在一个 SQLite 文件，默认路径 `backend/data/notes/study_history.db`（可被 `STUDY_HISTORY_DB_PATH` 覆盖）。

### 四张表

**表 1: `notes` — 学习笔记**
```sql
id            TEXT PRIMARY KEY       -- UUID 前 12 字符
title         TEXT NOT NULL          -- "Mistake record: xxx" / "知识入库：xxx"
note_type     TEXT NOT NULL          -- mistake_record | knowledge_item | conversation_summary
content       TEXT NOT NULL          -- Markdown 正文
metadata_json TEXT NOT NULL          -- {score, topic, mastery_before, mastery_after}
preview       TEXT                   -- content 前 160 字符（列表预览用）
created_at    TEXT NOT NULL
updated_at    TEXT NOT NULL
```
存了三类笔记：
- `mistake_record`：每次 grade 创建的错题记录（题目 + 学生作答 + 反馈）
- `knowledge_item`：ingest 材料时创建的知识条目（自动摘要/分类）
- `conversation_summary`：chat 的对话沉淀

**表 2: `note_tags` — 笔记标签（多对多）**
```sql
note_id TEXT NOT NULL → notes.id
tag     TEXT NOT NULL  -- 如 "Context Engineering", "mistake_record"
```

**表 3: `topic_mastery` — 主题掌握度**
```sql
topic      TEXT PRIMARY KEY          -- "Context Engineering"
mastery    REAL NOT NULL             -- 0.0 ~ 1.0
updated_at TEXT NOT NULL
```
持久化的核心价值——重启后掌握度不归零。

**表 4: `study_state` — 状态快照**
```sql
key        TEXT PRIMARY KEY          -- "last_grade" | "last_auto_context"
value_json TEXT NOT NULL             -- 批改结果 / 学习简报的完整 JSON
updated_at TEXT NOT NULL
```

### 没有持久化的内容

| 数据 | 原因 |
|---|---|
| `SimpleKeywordRetriever` 的文档切片（内存 list） | 重建成本低，不值得持久化 |
| `MemorySaver` checkpoint（HITL 中间状态） | 进程内够用，升级到 SqliteSaver 即可 |
| 图结构（graph） | 每次启动从代码重建 |

### 一句话
持久化存了三样东西：**笔记正文（含错题）**、**各主题掌握度分数**、**最近一次批改结果和学习简报**。重启后 `GET /study/state` 能看到和上次一致的掌握度。不持久化的是检索索引和 HITL 中间状态。

---

## Q4: SQLite notes 表 vs Qdrant 存储的不同

**问**：sqlite中notes表存储的内容和qdrant数据库存储的东西有什么不同？

**答**：一句话：SQLite 存的是"学习过程的记录"（发生了啥），Qdrant 存的是"学习材料的索引"（资料在哪）。

### 逐维对比

| 维度 | SQLite `notes` | Qdrant |
|---|---|---|
| **角色** | 学习历史数据库 | 语义检索索引 |
| **存什么** | 错题记录、知识笔记、对话总结、掌握度 | 学习材料切片 + 向量 embedding |
| **一条记录典型内容** | `{title, content, note_type, score, topic}` | `{vector:[0.12,-0.34,...], content:"..."}` |
| **检索方式** | 关键词 SQL LIKE + 标签过滤 | 向量余弦相似度（语义匹配） |
| **写时机** | grade 后写错题；ingest 后写知识笔记；chat 后写总结 | ingest 时写入（chunk + 向量化） |
| **读时机** | retrieve_learning_notes 节点 → [Mistake History] / [Learning Notes] | retrieve_materials 节点 → [Evidence] |
| **数据来源** | 学生和 LLM 互动产生的反馈/成果 | 用户上传的原始学习资料 |
| **是否持久化** | ✅ 是（SQLite 文件落盘） | ✅ 是（Qdrant 独立服务，默认未启用） |
| **当前是否在用** | ✅ 强制使用 | ❌ 未配 QDRANT_URL 时回退到 SimpleKeywordRetriever |

### 在 10 段上下文中的分工

SQLite `notes` 填这两段：
```
[Mistake History]    ← 上次错了什么
[Learning Notes]     ← 积累了什么
```

Qdrant / KeywordRetriever 填这段：
```
[Evidence]           ← 原始资料中相关内容
```

直观理解：
1. 导入关于 Context Engineering 的文章 → 内容被 chunk 后进 **Qdrant**
2. 问"什么是 Context Engineering" → 从 **Qdrant** 搜最相关段落当证据
3. LLM 回答了你，你答错了 → 批改结果 + 错题记录进 **SQLite notes**
4. 下次继续学 → 从 **SQLite** 读掌握度 0.42 + 上次错题，从 **Qdrant** 搜新证据，LLM 知道"这人上次错在证据使用，这次针对性讲"

### 为什么用两个库

查法完全不同：
- 查错题历史：`WHERE note_type='mistake_record' AND tag LIKE '%xxx%'` → 结构化过滤，SQLite 擅长
- 查学习材料：`cosine_sim("问题", 所有文档向量)` → 语义搜索，Qdrant 擅长

两个库解决两个不同的搜索问题，彼此不可替代。

---

## Q6: 当前 LangGraph 有几张图、几条条件边

**问**：我现在的langgraph是几个分支？

**答**：两张图，共 3 条条件边。

### 主图（`StudyLoopGraph._build_graph()`）

```
                  ┌─ explain_concept ───→ END
                  │
build_study_context ── generate_quiz ────→ END        ← 条件边① _route_after_context（3 分支）
                  │
                  └─ grade_answer → update_memory → replan_learning_path
                                                         │
                                                    ┌────┴────┐
                                                    │ 条件边② │ _route_after_replan
                                                    │         │（2 分支：remediate / done）
                                                    ▼         ▼
                                              generate_quiz  END
                                              （补练回边）
```

| 条件边 | 函数 | 分支数 | 类型 |
|---|---|---|---|
| ① | `_route_after_context` | 3（explain / quiz / grade） | 路由 |
| ② | `_route_after_replan` | 2（remediate / done） | 循环回边 |

### HITL 图（`StudyLoopGraph._build_hitl_graph()`）

```
START → ... → build_study_context → generate_quiz [⏸ 暂停]
                                         │ resume
                                         ▼
                                   grade_answer → ... → replan_learning_path
                                                              │
                                                         ┌────┴────┐
                                                         │ 条件边③│ _route_hitl_replan
                                                         │         │（2 分支）
                                                         ▼         ▼
                                                   generate_quiz  END
                                                   [⏸ 再暂停]   （会话结束）
```

| 条件边 | 函数 | 分支数 | 类型 |
|---|---|---|---|
| ③ | `_route_hitl_replan` | 2（continue_quiz / complete） | 循环回边 |

### 汇总

- **2 张图**：主图（同步调用）+ HITL 图（带 interrupt_after 暂停）
- **3 条条件边**：① 路由（3 岔）+ ② 回边（2 岔）+ ③ HITL 回边（2 岔）
- **2 条循环回边**：掌握度不达标时都回连 `generate_quiz`
- HITL 图的回边会再次触发 `interrupt_after`，形成多轮交互

---



## Q5: 知识笔记是什么，在哪里生成，和 Qdrant 的关系

**问**：什么是知识笔记，在哪里生成的？知识笔记是学习文件存放进qdrant的时候生成，然后放进sqlite的吗？

**答**：对，理解完全正确。一次 `POST /materials/ingest` 走两条线：

```
用户传的内容
    │
    ├─→ 1. chunk（切成 600 字符一段）
    │        每个 chunk → Qdrant（或 keyword retriever）
    │        { content, metadata: {topic, title, tags}, vector:[...] }
    │        ↑ 检索用：问题→搜最相关 chunk→填 [Evidence] 段
    │
    └─→ 2. organize_knowledge（LLM / 启发式提取摘要+标题+分类）
                 ↓
            create_note(note_type="knowledge_item") → SQLite notes
            { title, note_type, content: "## 自动摘要\n...\n## 自动分类\n...",
              仅存摘要和分类，不存原始全文 },
            ↑ 学习历史用：笔记列表、[Learning Notes] 段、掌握度关联
```

### Qdrant 和 SQLite 存的不一样

| | Qdrant | SQLite notes (knowledge_item) |
|---|---|---|
| **存什么** | 被切碎的原文段落（每段一个向量） | 整篇的摘要+分类+原始全文 |
| **粒度** | 600 字符一段，一篇资料切成 N 段 | 一条笔记概括整篇 |
| **查法** | 向量余弦相似度（语义匹配） | 关键词 LIKE + 标签过滤 |
| **用途** | 给 LLM 当证据（[Evidence] 段） | 给用户展示笔记列表、关联掌握度主题、学习历史 |

### 代码路径

`study_agent_service.py:387` → `ingest_material()`:
1. `organize_knowledge()`（LLM/heuristic 提取 title/summary/tags）
2. `chunk_text()` 切段 → `retriever.add_documents()`（进入 Qdrant 或 keyword）
3. `note_tool.create_note(note_type="knowledge_item")`（进入 SQLite）

两者是**同一份 ingest 请求同时写入的**，互不覆盖，解决不同的问题。

---

## 简历版本（最终定稿）

**项目名称**：StudyLoop —— 基于 LangGraph 的自适应学习闭环 Agent

**项目简介**：
基于 LangGraph / FastAPI / Vue / Qdrant / SQLite 实现个人学习 Agent StudyLoop，支持知识入库、讲解、出题、批改与掌握度追踪闭环；核心围绕结构化上下文构建、检索增强（RAG）、学习历史持久化与 HITL 人机协同补练。

**技术栈**：
Python · LangGraph · FastAPI · Pydantic · StateGraph · SQLite · Qdrant · RAG · Vue 3

**主要内容**：

```
项目简介：基于 LangGraph / FastAPI / Vue 3 / Qdrant / SQLite 构建个人学习 Agent StudyLoop，支持知识入库、概念讲解、专题练习、答案批改与掌握度追踪闭环；实现结构化上下文工程、RAG 检索增强、学习历史持久化、HITL 人机协同补练，并通过 MCP 将核心能力开放为标准工具接口。

技术栈：LangGraph、FastAPI、Pydantic、Vue 3、Qdrant、SQLite、RAG、MCP

主要工作：
\begin{itemize}
    \item 基于 LangGraph StateGraph 设计 9 节点学习工作流，通过条件边实现 explain / quiz / grade 三分支路由，并在掌握度低于阈值时触发补练循环；结合 interrupt\_after 与 MemorySaver checkpoint 实现 HITL 多轮会话，支持“出题 $\rightarrow$ 暂停作答 $\rightarrow$ 批改 $\rightarrow$ 补练 $\rightarrow$ 再作答”的闭环交互。
    \item 构建 Qdrant + SQLite 双层记忆架构：Qdrant 管理知识库向量索引与语义检索，SQLite 持久化学习历史、错题记录与主题掌握度状态，支撑个性化复盘、弱项追踪与学习状态恢复。
    \item 设计启发式规则 + LLM 混合分类链路，资料导入时自动生成标题、主题、摘要与分类路径，并实现 10 段 GSSC 上下文组装流程，将检索证据、学习笔记、错题历史与掌握度状态结构化注入 Agent 上下文。
    \item 基于 FastMCP 将讲解、出题、批改、检索、状态查询等能力暴露为 MCP 工具接口，并设计 JSONL 评测集与自动化验收脚本，覆盖检索、讲解、出题、批改与闭环工作流 5 类场景；在真实 LLM + Qdrant 链路下完成 11/11 用例通过，Top-3 检索命中率与召回率达到 100\%，并编写 28 个 pytest 用例覆盖检索、上下文构建、图编排与 API 等核心模块。
\end{itemize}
```

**面试准备要点**：
- 条件边要讲清楚"retry_count 在 generate_quiz 递增而非 replan 递增"的踩坑经历
- HITL 要对比"两次 HTTP 请求拼接"和"interrupt_after + resume"的差别
- 双层架构要讲清楚"SQLite 管历史、Qdrant 管语义"的分工逻辑
- 必准备一道"如果现在重构会怎么改进"——答案是 SqliteSaver 做 checkpoint 持久化 + function calling 替代正则 JSON 解析

---

## Q7: 测评体系（三层结构化评测）

> 这套测评不是只测一个点，而是分了三层，分别对应"代码对不对""接口通不通""Agent 效果稳不稳"。

### 第一层：pytest 单测 / 集成测试

路径：[tests/](D:\Code\Python\HelloAgents-main\tests)

测的是"代码对不对"——功能正确性和回归测试，覆盖：
- 检索服务
- 上下文构建
- 图节点与条件边
- HITL 会话
- SQLite 笔记 / 学习历史
- API 接口

### 第二层：API 验收测试

路径：[acceptance_check.ps1](D:\Code\Python\HelloAgents-main\scripts\acceptance_check.ps1)

测的是"系统能不能端到端跑通"——直接走后端接口，验证：
- `/health`
- `/study/topics`
- `/study/explain`
- `/study/quiz`
- `/study/grade`
- `/study/state`

### 第三层：evals/ 结构化 Agent 测评

路径：[run_eval_suite.py](D:\Code\Python\HelloAgents-main\evals\run_eval_suite.py) / [README.md](D:\Code\Python\HelloAgents-main\evals\README.md)

分两档：
- **smoke**：核心快速回归集，默认跑
- **extended**：扩展覆盖集，主题更多、场景更全

---

### evals 具体测什么

共 5 类：

#### 1. retrieval —— RAG 召回效果

最接近"RAG 召回效果"的部分。测：
- 能不能检索到正确主题
- Top-k 里有没有相关文档
- 文档内容里有没有关键证据

指标：**Hit@k**、**Recall@k**、**MRR@k**、**Precision@k**

#### 2. explain —— 讲解链路

不是只看模型会不会说话，而是看：
- 是否围绕目标主题
- 回答是否达到最小长度
- 是否真的带回了证据
- 是否明显跑题

#### 3. quiz —— 出题链路

关注：
- 题量够不够
- 题型对不对
- 主题有没有偏
- 返回结构是否稳定

#### 4. grade —— 批改链路

关注：
- 分数是否落在合理区间
- 错误类型分类是否合理
- 批改输出结构是否稳定

#### 5. workflow —— 闭环工作流

Agent 特有的重点，测：
- 低掌握度时会不会触发补练
- 高掌握度时会不会正常结束
- `generate_quiz` 循环、条件边、掌握度更新是否按预期工作

---

### 为什么要做这些测评

Agent 项目不是"接口返回 200"就算成功，它很容易坏在这些地方：

| 问题 | 表现 |
|------|------|
| RAG 没召回到对的知识 | 答非所问 |
| 上下文拼装后把重点冲掉了 | 回答泛化 |
| 图路由走错分支 | explain 走到了 grade 逻辑 |
| 低掌握度没有触发补练 | 闭环断了 |
| 批改结果结构不稳定 | 前端解析崩溃 |
| 改了一个节点后别的链路悄悄退化 | 回归 bug |

所以这套测评的目的不是只看"模型答得像不像"，而是把整个系统拆成可验证的层次：

```
代码层  →  API 层  →  Agent 行为层
(pytest)  (验收脚本)  (evals)
```

### 一句话总结

现在这套测评里：
- **retrieval** 是在测 RAG 的召回和排序效果
- **explain / quiz / grade / workflow** 是在测端到端 Agent 效果
- 整体上更偏**"工程化回归验证"**，不是纯学术意义上的大模型 benchmark

---

## Q8: 单元测试 vs 集成测试 vs API 验收 vs Agent 测评的区别

用项目里的实际代码来区分这四层。

### 单元测试 —— 测"一个函数对不对"

**特征**：不启动服务器、不连数据库、不调外部服务，只测一个类/一个方法的逻辑。

**项目实例**：`tests/test_retrieval_service.py:17`

```python
def test_retriever_adds_documents_and_ranks_overlap():
    retriever = SimpleKeywordRetriever()
    retriever.add_documents([
        {"content": "Python uses indentation to define code blocks.", "source": "doc-a"},
        {"content": "Retrieval practice strengthens long-term memory.", "source": "doc-b"},
    ])
    results = retriever.search("retrieval memory", top_k=2)
    assert len(results) == 1
    assert results[0]["source"] == "doc-b"
```

new 一个 retriever → 塞三条数据 → 搜一下 → 断言结果。没有数据库，没有 HTTP，没有 LLM。

其他例子：`test_chunk_text_splits_large_content`（测试文本切分逻辑）、`test_embed_base_url_normalization_strips_endpoint_suffix`（测试 URL 规范化）。

### 集成测试 —— 测"多个组件合起来对不对"

**特征**：需要把几块拼起来跑，比如 Service + Graph + SQLite（但仍然是代码内调用，不启动 HTTP 服务器）。

**项目实例**：`tests/test_study_loop_graph.py:34`

```python
def test_graph_explain_path(tmp_path):
    _service, graph = build_graph(tmp_path)   # 创建 Service + Graph + 临时 SQLite
    result = graph.explain(question="Why do we need retrieved evidence?")
    assert result["answer"]
    assert result["context"]
    assert result["evidence"]
```

对比单元测试：
- 需要 `tmp_path`（集成了文件系统）
- 需要 `build_graph`（集成了 Service、Graph、SQLite、检索器）
- 仍然**不启动 HTTP 服务器**，不走网络

### 单元测试 vs 集成测试 —— 对比表

| | 单元测试 | 集成测试 |
|---|---|---|
| 测什么 | 一个类/方法 | 多个组件协作 |
| 依赖 | 无（mock 或纯内存） | 文件系统、数据库、其他 Service |
| 启动服务 | ❌ | ❌ |
| 典型例子 | `SimpleKeywordRetriever.search()` 返回正确 | `graph.explain()` 走完检索→上下文→LLM→出答案 |
| 定位失败时 | "这个函数逻辑错了" | "几个组件连接的地方出问题了" |
| 项目文件 | `test_retrieval_service.py` | `test_study_loop_graph.py`、`test_api_graph.py` |

### API 验收测试 —— 测"HTTP 接口通不通"

**特征**：启动真实服务器，发 HTTP 请求，验证响应状态码和 body 结构。黑盒视角——不关心内部实现，只看"接口返回了什么"。

**项目实例**：`scripts/acceptance_check.ps1`

```powershell
# 启动服务器 → 依次调用：
GET  /health                              # 验证服务是否活着
POST /knowledge/ingest { content: "..." } # 导入知识
POST /study/quiz      { topic: "Redis" }  # 出题

# 断言：
# - HTTP 200（不是 500）
# - 响应有 question 字段
# - 响应格式是 JSON
```

**关键区别**：验收测试走的是**真实 HTTP 链路**（启动 uvicorn → 发 HTTP 请求），测的是"部署后能不能用"。

### 结构化 Agent 测评 —— 测"Agent 效果稳不稳"

**特征**：不启动 HTTP 服务器，直接调用 Python 里的 Service/Graph。但和集成测试不同——它有**标准化的评测用例集 + 量化指标**。

**项目实例**：`evals/cases/retrieval_cases.jsonl`

```json
{"id":"retrieval-context-001",
 "query":"Context Engineering 学习目标 证据 历史错误",
 "expected_topic":"Context Engineering",
 "gold_titles":["上下文工程基础","上下文工程的四要素"],
 "must_contain_any":["学习目标","证据","历史错误","当前任务"],
 "top_k":3}
```

`run_eval_suite.py` 会加载所有 case → 执行 → 算指标：

```
Hit@3:  6/6 = 1.0
MRR@3:  0.944
Recall@3: 1.0
```

集成测试回答"这个函数调通了吗"（pass/fail），Agent 测评回答"这个功能效果如何"（Hit@3 多少、MRR 多少）。

### 四层对比总表

| 维度 | 单元测试 | 集成测试 | API 验收 | Agent 测评 |
|------|----------|----------|----------|------------|
| 测什么 | 一个函数的逻辑 | 几个组件协作 | HTTP 端点 | Agent 行为效果 |
| 是否启动 HTTP | ❌ | ❌ | ✅ 真实启动 | ❌ |
| 是否用真实 LLM | ❌ mock | ✅/❌ 可选 | ✅ 真实 | ✅/❌ 可选 |
| 输出 | pass/fail | pass/fail | pass/fail | 量化指标 |
| 定位 | "函数 BUG" | "组件连接 BUG" | "接口 BUG" | "效果退化" |
| 典型发现 | chunk 越界 | grade 节点拿不到 context | 路由参数传错 | 讲解跑题了 |
| 运行频率 | 每次提交 | 每次提交 | 部署前 | 版本发布前 |

---

## Q9: 评测集是怎么设计的

### 整体架构

```
evals/
├─ cases/                    ← 用例数据（JSONL，每行一条独立 case）
│  ├─ retrieval_cases.jsonl
│  ├─ explain_cases.jsonl
│  ├─ quiz_cases.jsonl
│  ├─ grade_cases.jsonl
│  └─ workflow_cases.jsonl
├─ fixtures/
│  └─ knowledge_base.jsonl   ← 评测前自动导入的知识种子
├─ reports/
│  └─ metrics_时间戳.json    ← 每次运行的结构化指标报告
└─ run_eval_suite.py         ← 评测引擎
```

### 6 个设计决策

#### 1. 数据驱动：用例是 JSONL 文件，不是代码

每个 case 是一行 JSON，写在 `cases/*.jsonl` 里。新增一个场景只需追加一行，不需要改 Python 代码。

```json
{"id":"retrieval-context-001",
 "query":"Context Engineering 学习目标 证据 历史错误",
 "expected_topic":"Context Engineering",
 "gold_titles":["上下文工程基础","上下文工程的四要素"],
 "top_k":3}
```

对比 pytest：pytest 的用例是**函数 + assert**，这里是用例是**数据 + 预期指标**。好处是产品和运营同学也能加 case。

#### 2. profile 分级：smoke vs extended

每个 case 有一个 `profile` 字段，通过 `case_matches_profile()` 函数筛选：

```python
def case_matches_profile(case, selected_profile):
    # smoke: 只跑 smoke
    # extended: 跑 smoke + extended
    # all: 跑全部
```

**为什么**：每次提交都跑全量评测太慢。smoke 只覆盖核心场景（~2-3 条/类），提交时快速验证；extended 覆盖边界+多主题，发布前跑。

具体选择逻辑是 rank 比较：smoke=0, extended=1，选 ≤ 当前 profile rank 的 case。

#### 3. 策略模式：每个 suite 一个 evaluator 函数

```
evaluator_map = {
    "retrieval": evaluate_retrieval_case,   # 调 retriever.search → 算 Hit@k/MRR
    "explain":   evaluate_explain_case,     # 调 graph.explain → 检查答案长度/证据/跑题
    "quiz":      evaluate_quiz_case,        # 调 graph.quiz → 检查题量/题型/主题
    "grade":     evaluate_grade_case,       # 调 graph.grade → 检查分数区间/错误类型
    "workflow":  evaluate_workflow_case,    # 调 graph.grade → 检查补练触发/重试次数
}
```

新增一类评测只需要写一个新的 `evaluate_xxx_case()` 函数，在 map 里注册，然后在 `cases/` 下放对应的 JSONL 文件。

每个 evaluator 的判定逻辑不同，但都返回统一的 `CaseResult`：

```python
@dataclass
class CaseResult:
    suite: str          # retrieval / explain / quiz / grade / workflow
    case_id: str        # 用例 ID
    passed: bool        # 是否通过
    details: str        # 可读的详细结果
    metrics: dict       # 量化指标（Hit@k、score、count 等）
    payload_preview: dict
```

#### 4. 运行时隔离：每个 suite 独立 SQLite + 每次 case 前回滚

```python
@dataclass
class EvalRuntimeContext:
    runtime_dir: Path           # 每个 suite 独立的运行目录
    baseline_db_path: Path      # 种子导入后的 SQLite 基线
    service: StudyAgentService
    graph: StudyLoopGraph
```

流程：

```
1. build_runtime_context()
   → 创建临时目录
   → 建 Service + Graph
   → 导入 knowledge_base.jsonl 知识种子
   → 备份 SQLite 快照（baseline）

2. 对每个 case:
   → restore_runtime_context()     # SQLite backup 回滚到基线
   → evaluator(case)               # 执行评测
   → 记录结果

3. cleanup_runtime_context()
   → 删除临时目录
   → 清理 Qdrant collection
```

**为什么不用 pytest 的参数化**：pytest 共享同一个进程状态，case 之间会互相污染（掌握度变了、笔记多了）。SQLite backup 回滚比每次重建 Service 快得多。

#### 5. 加速策略：不同 suite 不同裁剪力度

`apply_eval_accelerators()` 在真实 LLM 模式下减少非核心调用：

| suite | max_tokens | 检索 top_k | 笔记数量 | 内容截断 |
|-------|-----------|-----------|---------|---------|
| grade/workflow | 1200 | 2 | 1 | 160-180 字符 |
| quiz | 1600 | 3 | 1 | 180-220 字符 |
| explain | 2200 | 不限 | 2 | 260 字符 |
| retrieval | 2200 | 不限 | 2 | 260 字符 |

同时用 **Method Monkey Patching** 替换辅助方法：
- `service.generate_reference_answer` → 启发式（不调 LLM）
- `service.prepare_study_brief` → 启发式
- 检索结果加缓存（相同 query 不重复搜）

#### 6. 量化指标：retrieval 算全套信息检索指标

```python
def compute_retrieval_metrics(results, case):
    return {
        "hit_at_k": hit_at_k,          # 是否有相关结果在 top-k
        "mrr_at_k": mrr_at_k,          # 第一个相关结果排第几（倒数）
        "precision_at_k": precision,    # top-k 中相关比例
        "recall_at_k": recall,         # 相关文档召回比例
    }
```

非 retrieval 的 suite 也有各自的量化指标但不走 IR 公式，比如 grade 看 `score` 是否落在期望区间，workflow 看 `retry_count` 是否匹配预期。

---

### 整体设计原则总结

| 原则 | 体现 |
|------|------|
| **数据驱动** | case 是 JSONL，不是代码，新增不改代码 |
| **分级递进** | smoke → extended，快速反馈 vs 全面覆盖 |
| **策略模式** | evaluator_map 统一调度，新增 suite 只需加函数+文件 |
| **隔离可靠** | 每个 case 前 SQLite 回滚，状态不污染 |
| **按需加速** | 不同 suite 不同裁剪力度，不牺牲核心链路 |
| **量化可对比** | 每次跑输出 metrics_时间戳.json，版本间可 diff |
| 项目文件 | `test_xxx.py` | `test_xxx_graph.py` | `acceptance_check.ps1` | `evals/run_eval_suite.py`