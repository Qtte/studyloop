function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function unescapeNewlines(value) {
  return value.replace(/\\n/g, '\n').replace(/\\t/g, '\t')
}

function tryParseString(value) {
  const trimmed = value.trim()
  if (!trimmed) {
    return { parsed: null, asText: '' }
  }

  try {
    return { parsed: JSON.parse(trimmed), asText: trimmed }
  } catch (error) {
    return { parsed: null, asText: unescapeNewlines(trimmed) }
  }
}

function extractEvidence(source) {
  if (!isObject(source)) return []
  if (Array.isArray(source.evidence)) return source.evidence

  if (Array.isArray(source.evidence_used)) {
    return source.evidence_used.map((item) => ({
      label: typeof item === 'string' ? item : JSON.stringify(item),
    }))
  }

  return []
}

function normalizeMarkdown(source, raw) {
  return {
    kind: 'markdown',
    title: source.title || '结果',
    markdown: source.answer || source.content || source.markdown || source.explanation || '',
    evidence: extractEvidence(source),
    classification: source.classification || null,
    autoContext: source.auto_context || null,
    memorySummary: source.memory_summary || null,
    raw,
  }
}

function normalizeQuiz(source, raw) {
  const quiz = source.quiz && isObject(source.quiz) ? source.quiz : source

  return {
    kind: 'quiz',
    title: '练习题',
    quiz: {
      question: quiz.question || '',
      question_type: quiz.question_type || quiz.type || 'short_answer',
      difficulty: quiz.difficulty || 'medium',
      expected_points: quiz.expected_points || quiz.rubric || [],
      grading_rubric: quiz.grading_rubric || quiz.rubric || [],
      evidence_used: quiz.evidence_used || extractEvidence(source),
      reference_answer: quiz.reference_answer || '',
    },
    evidence: extractEvidence(source),
    autoContext: source.auto_context || null,
    raw,
  }
}

function normalizeQuizSet(source, raw) {
  const quizSet = source.quiz_set && isObject(source.quiz_set) ? source.quiz_set : source

  return {
    kind: 'quiz_set',
    title: source.title || '练习集',
    quizSet: {
      topic: quizSet.topic || '',
      focus_reason: quizSet.focus_reason || '',
      difficulty: quizSet.difficulty || 'medium',
      question_count: quizSet.question_count || (Array.isArray(quizSet.questions) ? quizSet.questions.length : 0),
      question_types: Array.isArray(quizSet.question_types) ? quizSet.question_types : [],
      questions: Array.isArray(quizSet.questions)
        ? quizSet.questions.map((item, index) => ({
            question_id: item.question_id || `q${index + 1}`,
            question_type: item.question_type || item.type || 'open_ended',
            question: item.question || '',
            options: Array.isArray(item.options) ? item.options : [],
            correct_option: item.correct_option || '',
            reference_answer: item.reference_answer || '',
            rubric: item.rubric || [],
            difficulty: item.difficulty || quizSet.difficulty || 'medium',
          }))
        : [],
    },
    evidence: extractEvidence(source),
    autoContext: source.auto_context || null,
    raw,
  }
}

function normalizePlan(source, raw) {
  const plan = source.next_plan && isObject(source.next_plan) ? source.next_plan : source

  return {
    kind: 'plan',
    title: '学习计划',
    plan: {
      plan_summary: plan.plan_summary || plan.summary || '',
      today_tasks: plan.today_tasks || plan.next_actions || [],
      next_three_days: plan.next_three_days || plan.focus_areas || [],
      success_criteria: plan.success_criteria || plan.recommended_question_types || [],
    },
    raw,
  }
}

function normalizeGrade(source, raw) {
  const grade = source.result && isObject(source.result) ? source.result : source
  const nextPlanSummary =
    source.next_plan && isObject(source.next_plan) ? source.next_plan.summary || '' : ''

  return {
    kind: 'grade',
    title: '批改结果',
    grade: {
      score: grade.score || 0,
      correct_points: grade.correct_points || [],
      missing_points: grade.missing_points || [],
      misconceptions: grade.misconceptions || [],
      feedback: grade.feedback || '',
      reference_answer: grade.reference_answer || source.reference_answer || '',
      next_action: grade.next_action || grade.suggested_note || nextPlanSummary,
      mistake_type: grade.mistake_type || 'unknown',
      evidence_used: grade.evidence_used || extractEvidence(source),
      new_mastery_score:
        source.mastery_after != null ? source.mastery_after : grade.new_mastery_score,
      old_mastery_score:
        source.mastery_before != null ? source.mastery_before : grade.old_mastery_score,
      learning_plan: source.next_plan || null,
    },
    evidence: extractEvidence(source),
    autoContext: source.auto_context || null,
    raw,
  }
}

export function normalizeApiResponse(raw) {
  if (raw == null || raw === '') {
    return { kind: 'empty', raw }
  }

  if (typeof raw === 'string') {
    const parsedResult = tryParseString(raw)
    if (parsedResult.parsed !== null) {
      return normalizeApiResponse(parsedResult.parsed)
    }

    return {
      kind: 'markdown',
      title: '结果',
      markdown: parsedResult.asText,
      raw,
    }
  }

  if (Array.isArray(raw)) {
    return { kind: 'json', title: '原始数据', raw }
  }

  if (!isObject(raw)) {
    return {
      kind: 'markdown',
      title: '结果',
      markdown: String(raw),
      raw,
    }
  }

  if (raw.answer || raw.content || raw.markdown || raw.explanation) {
    return normalizeMarkdown(raw, raw)
  }

  if ((raw.quiz_set && isObject(raw.quiz_set)) || (raw.questions && Array.isArray(raw.questions))) {
    const questions =
      raw.quiz_set && Array.isArray(raw.quiz_set.questions)
        ? raw.quiz_set.questions
        : Array.isArray(raw.questions)
          ? raw.questions
          : []
    if (questions.length <= 1 && raw.quiz && isObject(raw.quiz)) {
      return normalizeQuiz(raw, raw)
    }
    return normalizeQuizSet(raw, raw)
  }

  if (
    (raw.quiz && isObject(raw.quiz)) ||
    (raw.question && (raw.expected_points || raw.reference_answer || raw.rubric))
  ) {
    return normalizeQuiz(raw, raw)
  }

  if (
    (raw.result && isObject(raw.result) && (raw.result.score != null || raw.result.feedback)) ||
    (raw.score != null && raw.feedback)
  ) {
    return normalizeGrade(raw, raw)
  }

  if (raw.next_plan || raw.plan_summary || raw.today_tasks) {
    return normalizePlan(raw, raw)
  }

  return { kind: 'json', title: '原始数据', raw }
}
