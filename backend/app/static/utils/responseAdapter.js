function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function unescapeNewlines(value) {
  return value.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
}

function tryParseString(value) {
  const trimmed = value.trim();
  if (!trimmed) return { parsed: null, asText: "" };

  try {
    return { parsed: JSON.parse(trimmed), asText: trimmed };
  } catch {
    return { parsed: null, asText: unescapeNewlines(trimmed) };
  }
}

function extractEvidence(source) {
  if (!isObject(source)) return [];
  if (Array.isArray(source.evidence)) return source.evidence;
  if (Array.isArray(source.evidence_used)) {
    return source.evidence_used.map((item) => ({ label: typeof item === "string" ? item : JSON.stringify(item) }));
  }
  if (Array.isArray(source.retrieved_evidence)) return source.retrieved_evidence;
  return [];
}

function normalizeMarkdown(source, raw) {
  return {
    kind: "markdown",
    title: source.title || "AI 讲解",
    markdown: source.answer || source.content || source.markdown || source.explanation || "",
    evidence: extractEvidence(source),
    raw,
  };
}

function normalizeQuiz(source, raw) {
  const quiz = source.quiz && isObject(source.quiz) ? source.quiz : source;
  return {
    kind: "quiz",
    title: "练习题",
    quiz: {
      question: quiz.question || "",
      question_type: quiz.question_type || quiz.type || "开放题",
      difficulty: quiz.difficulty || "medium",
      expected_points: quiz.expected_points || quiz.rubric || [],
      grading_rubric: quiz.grading_rubric || quiz.rubric || [],
      evidence_used: quiz.evidence_used || extractEvidence(source),
      reference_answer: quiz.reference_answer || "",
    },
    evidence: extractEvidence(source),
    raw,
  };
}

function normalizePlan(source, raw) {
  const plan = source.next_plan && isObject(source.next_plan) ? source.next_plan : source.plan && isObject(source.plan) ? source.plan : source;
  return {
    kind: "plan",
    title: "学习计划",
    plan: {
      plan_summary: plan.plan_summary || plan.summary || "",
      today_tasks: plan.today_tasks || plan.next_actions || [],
      next_three_days: plan.next_three_days || plan.focus_areas || [],
      success_criteria: plan.success_criteria || plan.recommended_question_types || [],
    },
    raw,
  };
}

function normalizeGrade(source, raw) {
  const grade = source.result && isObject(source.result) ? source.result : source;
  return {
    kind: "grade",
    title: "批改结果",
    grade: {
      score: grade.score ?? 0,
      correct_points: grade.correct_points || [],
      missing_points: grade.missing_points || [],
      misconceptions: grade.misconceptions || [],
      feedback: grade.feedback || "",
      reference_answer: grade.reference_answer || source.reference_answer || "",
      next_action: grade.next_action || source.next_plan?.summary || grade.suggested_note || "",
      mistake_type: grade.mistake_type || "unknown",
      evidence_used: grade.evidence_used || extractEvidence(source),
      new_mastery_score: source.mastery_after ?? grade.new_mastery_score ?? null,
      old_mastery_score: source.mastery_before ?? grade.old_mastery_score ?? null,
      learning_plan: source.next_plan || null,
    },
    evidence: extractEvidence(source),
    raw,
  };
}

export function normalizeApiResponse(raw) {
  if (raw == null || raw === "") {
    return { kind: "empty", raw };
  }

  if (typeof raw === "string") {
    const { parsed, asText } = tryParseString(raw);
    if (parsed !== null) return normalizeApiResponse(parsed);
    return {
      kind: "markdown",
      title: "文本结果",
      markdown: asText,
      raw,
    };
  }

  if (Array.isArray(raw)) {
    return {
      kind: "json",
      title: "原始数据",
      raw,
    };
  }

  if (!isObject(raw)) {
    return {
      kind: "markdown",
      title: "文本结果",
      markdown: String(raw),
      raw,
    };
  }

  if (raw.answer || raw.content || raw.markdown || raw.explanation) {
    return normalizeMarkdown(raw, raw);
  }

  if (isObject(raw.quiz) || (raw.question && (raw.expected_points || raw.reference_answer || raw.rubric))) {
    return normalizeQuiz(raw, raw);
  }

  if ((isObject(raw.result) && (raw.result.score != null || raw.result.feedback)) || (raw.score != null && raw.feedback)) {
    return normalizeGrade(raw, raw);
  }

  if (raw.next_plan || raw.plan_summary || raw.today_tasks) {
    return normalizePlan(raw, raw);
  }

  return {
    kind: "json",
    title: "原始响应",
    raw,
  };
}
