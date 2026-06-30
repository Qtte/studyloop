"""
Centralized prompts for the StudyLoop LangGraph workflow.

═══════════════════════════════════════════════════════════════════════════════
Prompt 工程说明
═══════════════════════════════════════════════════════════════════════════════

这些 prompt 是 StudyLoop 的"系统指令层"。它们的角色：

MAIN_SYSTEM_PROMPT  ── 全局系统角色（注入到每次 LLM 调用）
                       定义了"身份"：你是一个耐心的学习助手，使用 evidence。

QUIZ_GENERATOR_PROMPT ── 出题模式指令（当前未直接使用，generate_quiz 委托了
                         service.generate_practice_set 自带 prompt）

ANSWER_GRADER_PROMPT  ── 批改模式指令（告诉 LLM 怎么评分、怎么分类错误）

REPLANNER_PROMPT      ── 重规划模式指令（告诉 LLM 怎么生成下一步学习计划）

设计原则：
────────────────────────
- 所有这些 prompt 都是"片段"——它们被拼接到 10 段上下文中作为前缀
- 实际调用格式：{PROMPT}\n\n{state.study_context}\n\n{具体参数}
- study_context 已经包含了 [Role & Policies] 段（MAIN_SYSTEM_PROMPT 作为内容），
  所以这些 prompt 在上面再加一层前缀，确保 LLM 先读指令再看证据

为什么不用更长的 prompt？
────────────────────────
- 短 prompt 更可预测，减少 LLM 自由发挥空间
- 所有具体约束（输出格式、证据要求）都已编码在 study_context 的 10 段中
- 这里的 prompt 只是"模式标记"，不是完整指令
"""

MAIN_SYSTEM_PROMPT = (
    "You are StudyLoop, a patient study agent. "
    "Use retrieved evidence and learner history carefully. "
    "Be pedagogically clear, concise, and grounded in the supplied study context."
)

QUIZ_GENERATOR_PROMPT = """MODE: quiz
Return JSON only.
Generate one study question from the supplied context.
The JSON must contain: question, reference_answer, rubric, difficulty.
Keep the question aligned with the learner goal and current topic.
"""

ANSWER_GRADER_PROMPT = """MODE: grade
Return JSON only.
Grade the learner answer using the supplied study context and evidence.
The JSON must contain: score, mistake_type, feedback, evidence_used, suggested_note.
Use mistake_type from: correct, concept_confusion, concept_missing, shallow_answer, missing_evidence, application_weak.
"""

REPLANNER_PROMPT = """MODE: replan
Return JSON only.
Create a short learning plan after grading.
The JSON must contain: summary, focus_areas, next_actions, recommended_question_types.
Focus on the learner's weakest areas and propose concrete next steps.
"""
