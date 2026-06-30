# StudyLoop 评测集说明

这个目录用于给 StudyLoop 项目维护一套可重复执行的评测集，方便你后面做：

- 检索质量回归测试
- 概念讲解输出检查
- 习题集生成检查
- 批改与学习闭环检查
- 简历/项目答辩时展示“我不仅做了 Agent，还做了评测”

## 目录结构

```text
evals/
├─ cases/
│  ├─ explain_cases.jsonl
│  ├─ grade_cases.jsonl
│  ├─ quiz_cases.jsonl
│  ├─ retrieval_cases.jsonl
│  └─ workflow_cases.jsonl
├─ fixtures/
│  └─ knowledge_base.jsonl
├─ reports/
│  └─ metrics_*.json
└─ run_eval_suite.py
```

## 当前设计

`fixtures/knowledge_base.jsonl`

- 作为评测前自动导入的知识种子
- 让评测不依赖你手工先去前端上传资料

`cases/*.jsonl`

- 每一行都是一个独立用例
- 后续你可以持续往里追加，不需要改脚本结构
- 支持 `profile` 字段区分 `smoke` 与 `extended` 两档评测规模

`run_eval_suite.py`

- 会自动创建独立运行目录
- 会自动导入评测知识种子
- 支持单独跑某一个 suite，也支持一键跑全部
- 会自动输出 `metrics_时间戳.json` 报告，方便做版本对比
- 默认启用几项加速策略：
  - 种子知识走“快速导入”，不再调用知识整理 LLM
  - 每个 suite 共享一个已初始化的 runtime
  - 每个 case 执行前会把 SQLite 状态回滚到基线快照
  - 真实全量评测默认自动并行 2 个 suite
  - 简报推导 / 参考答案这类辅助步骤默认走启发式，优先验证主链路

## 推荐运行方式

先使用 mock LLM 跑通结构：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --use-mock-llm
```

如果你想跑扩展评测集：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --case-profile extended --use-mock-llm
```

如果你要验证真实大模型链路：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all
```

如果你要连 Qdrant 一起验证：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --use-qdrant
```

如果你想在真实链路下跑扩展评测集：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --case-profile extended --use-qdrant
```

如果你想强制让“辅助步骤”也全部走真实 LLM：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --use-qdrant --full-llm-path
```

如果你想直接拿到结构化指标报告：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --use-mock-llm --json
```

报告会默认写到 `evals/reports/`。

如果你要手动指定并行度：

```powershell
D:\ProSoftware\Anaconda\envs\fit\python.exe .\evals\run_eval_suite.py --suite all --use-qdrant --parallel-suites 2
```

## 评测规模

- `smoke`：默认核心回归集，适合日常开发与真实链路快速验收
- `extended`：在 `smoke` 基础上追加更多主题和更多边界场景，适合阶段性回归
- `all`：不过滤 profile，运行所有用例

## 如何扩充用例

### 1. 检索评测

适合验证：

- 某个问题能不能检索到正确主题
- 检索返回内容是否包含关键证据
- 检索排名是否足够靠前，能不能量化 `Hit@k / Recall@k / MRR@k / Precision@k`

示例字段：

```json
{
  "id": "retrieval-context-001",
  "query": "上下文工程为什么比长提示词更可控",
  "expected_topic": "Context Engineering",
  "gold_titles": ["上下文工程基础", "上下文工程的四要素"],
  "must_contain_any": ["学习目标", "证据", "历史错误"],
  "top_k": 3
}
```

### 2. 概念讲解评测

适合验证：

- 讲解有没有围绕主题
- 有没有覆盖你想要的关键点
- 在真实 LLM 下更推荐使用“弱结构断言”
  - 例如：最小回答长度、必须提到主题、至少返回 N 条证据
  - 不建议只用几个固定关键词硬匹配

### 3. 习题集评测

适合验证：

- 是否真的按主题出题
- 是否生成了足够数量的题
- 题型覆盖是否符合预期

### 4. 批改评测

适合验证：

- 分数区间是否合理
- 错误类型是否合理

### 5. 工作流评测

适合验证：

- 批改后是否触发补救题
- `generate_quiz` 循环是否按预期工作
- 掌握度状态是否被更新

## 你后面最值得补的真实数据

如果你想把这个项目做成“能写进简历的工程项目”，建议优先补这三类数据：

1. 你真实学习过的资料片段
2. 你真实写过的错误答案
3. 你认为“高质量讲解/高质量出题”的标准答案

这样评测集就不再只是 demo，而是你的真实学习行为数据。

## 哪些指标最适合写进简历

你后面可以重点展示这几类数字：

- 检索质量：`Hit@3`、`Recall@3`、`MRR@3`
- 工作流稳定性：`overall_pass_rate`、`workflow pass_rate`
- 批改质量：`grade avg_score` 与错误类型命中率
- 执行效率：`wall_clock_ms`、`total_duration_ms`、`avg_duration_ms`

一个比较自然的写法是：

- 为 StudyLoop 构建多套 Agent 评测集，覆盖检索、讲解、出题、批改与闭环工作流。
- 建立结构化评测报告，支持统计 `Hit@3 / Recall@3 / MRR@3 / pass_rate` 等指标，用于回归验证和版本对比。
