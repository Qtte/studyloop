<template>
  <section class="result-panel">
    <div v-if="loading" class="empty-state">{{ ui.loading }}</div>
    <div v-else-if="error" class="error-box">{{ error }}</div>
    <div v-else-if="!result || result.kind === 'empty'" class="empty-state">
      {{ ui.empty }}
    </div>
    <template v-else>
      <div class="panel-title compact-title">
        <div>
          <h3>{{ result.title || ui.result }}</h3>
          <span>{{ subtitle }}</span>
        </div>
      </div>

      <div v-if="hasMeta" class="meta-grid">
        <div v-if="displayTitle" class="meta-card">
          <strong>{{ ui.metaTitle }}</strong>
          <p>{{ displayTitle }}</p>
        </div>
        <div v-if="displayTopic" class="meta-card">
          <strong>{{ ui.metaTopic }}</strong>
          <p>{{ displayTopic }}</p>
        </div>
        <div v-if="displayGoal" class="meta-card">
          <strong>{{ ui.metaGoal }}</strong>
          <p>{{ displayGoal }}</p>
        </div>
        <div v-if="displayTask" class="meta-card">
          <strong>{{ ui.metaTask }}</strong>
          <p>{{ displayTask }}</p>
        </div>
        <div v-if="displayCategoryPath" class="meta-card">
          <strong>{{ ui.metaCategory }}</strong>
          <p>{{ displayCategoryPath }}</p>
        </div>
        <div v-if="displayTags" class="meta-card">
          <strong>{{ ui.metaTags }}</strong>
          <p>{{ displayTags }}</p>
        </div>
      </div>

      <div v-if="memorySummaryText" class="result-block note-callout">
        <strong>{{ ui.memorySummary }}</strong>
        <p>{{ memorySummaryText }}</p>
      </div>

      <MarkdownRenderer v-if="result.kind === 'markdown'" :content="result.markdown || ''" />

      <div v-else-if="result.kind === 'quiz_set'" class="result-card quiz-card">
        <div class="result-card-header">
          <div>
            <h4>{{ ui.quizSetTitle }}</h4>
            <p>{{ result.quizSet.topic }} | {{ result.quizSet.question_count }} {{ ui.questionsSuffix }}</p>
          </div>
        </div>
        <div class="result-block" v-if="result.quizSet.focus_reason">
          <strong>{{ ui.focusReason }}</strong>
          <p>{{ result.quizSet.focus_reason }}</p>
        </div>
        <div class="result-block" v-for="(item, index) in result.quizSet.questions" :key="item.question_id || index">
          <strong>{{ index + 1 }}. {{ item.question_type === 'multiple_choice' ? ui.multipleChoice : ui.openEnded }}</strong>
          <p class="result-question">{{ item.question }}</p>
          <ul v-if="item.options && item.options.length">
            <li v-for="option in item.options" :key="option">{{ option }}</li>
          </ul>
          <div class="chip-row" v-if="item.rubric && item.rubric.length">
            <span class="result-chip" v-for="point in item.rubric" :key="point">{{ point }}</span>
          </div>
        </div>
        <div class="result-block note-callout">
          <strong>{{ ui.quizHintTitle }}</strong>
          <p>{{ ui.quizSetHintBody }}</p>
        </div>
      </div>

      <div v-else-if="result.kind === 'quiz'" class="result-card quiz-card">
        <div class="result-card-header">
          <div>
            <h4>{{ ui.quizTitle }}</h4>
            <p>{{ quizTypeLabel }}</p>
          </div>
          <button class="ghost-btn small-btn" @click="copyQuestion(result.quiz.question)">
            {{ ui.copyQuestion }}
          </button>
        </div>
        <div class="result-question">{{ result.quiz.question }}</div>
        <div class="chip-row" v-if="result.quiz.expected_points && result.quiz.expected_points.length">
          <span class="result-chip" v-for="item in result.quiz.expected_points" :key="item">{{ item }}</span>
        </div>
        <div class="result-block" v-if="result.quiz.grading_rubric && result.quiz.grading_rubric.length">
          <strong>{{ ui.gradingRubric }}</strong>
          <ul>
            <li v-for="item in result.quiz.grading_rubric" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block note-callout">
          <strong>{{ ui.quizHintTitle }}</strong>
          <p>{{ ui.quizHintBody }}</p>
        </div>
      </div>

      <div v-else-if="result.kind === 'grade'" class="result-card grade-card">
        <div class="result-card-header">
          <div>
            <h4>{{ ui.gradeTitle }}</h4>
            <p>{{ mistakeLabel }}</p>
          </div>
          <div class="score-badge">{{ result.grade.score }}</div>
        </div>
        <div class="mastery-meter" v-if="result.grade.new_mastery_score != null">
          <div class="mastery-meter-bar" :style="{ width: masteryPercent + '%' }"></div>
        </div>
        <div class="mastery-meta" v-if="result.grade.new_mastery_score != null">
          {{ ui.masteryChange }}{{ masteryBefore }} -> {{ result.grade.new_mastery_score }}
        </div>
        <div class="result-block" v-if="result.grade.feedback">
          <strong>{{ ui.feedback }}</strong>
          <p>{{ result.grade.feedback }}</p>
        </div>
        <div class="result-block" v-if="result.grade.reference_answer">
          <strong>{{ ui.referenceAnswer }}</strong>
          <MarkdownRenderer :content="result.grade.reference_answer" :compact="true" />
        </div>
        <div class="result-block" v-if="result.grade.next_action">
          <strong>{{ ui.nextAction }}</strong>
          <p>{{ result.grade.next_action }}</p>
        </div>
        <div v-if="result.grade.learning_plan" class="result-block">
          <strong>{{ ui.learningPlan }}</strong>
          <ul>
            <li v-for="item in learningPlanItems" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <div v-else-if="result.kind === 'plan'" class="result-card plan-card">
        <div class="result-card-header">
          <div>
            <h4>{{ ui.learningPlan }}</h4>
            <p>{{ ui.planSubtitle }}</p>
          </div>
        </div>
        <p class="plan-summary">{{ result.plan.plan_summary }}</p>
        <div class="result-block" v-if="result.plan.today_tasks && result.plan.today_tasks.length">
          <strong>{{ ui.todayTasks }}</strong>
          <ul>
            <li v-for="item in result.plan.today_tasks" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block" v-if="result.plan.next_three_days && result.plan.next_three_days.length">
          <strong>{{ ui.nextThreeDays }}</strong>
          <ul>
            <li v-for="item in result.plan.next_three_days" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block" v-if="result.plan.success_criteria && result.plan.success_criteria.length">
          <strong>{{ ui.successCriteria }}</strong>
          <ul>
            <li v-for="item in result.plan.success_criteria" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <div v-else class="result-box"><pre>{{ pretty(result.raw) }}</pre></div>

      <div v-if="result.evidence && result.evidence.length" class="evidence-block">
        <h4>{{ ui.evidence }}</h4>
        <div class="evidence-list">
          <div class="evidence-item" v-for="(item, index) in result.evidence" :key="index">
            <strong>{{ item.source || item.label || item.doc_id || `${ui.document} ${index + 1}` }}</strong>
            <p>{{ item.content || item.text || item.label || pretty(item) }}</p>
          </div>
        </div>
      </div>

      <details class="raw-viewer">
        <summary>{{ ui.rawJson }}</summary>
        <pre>{{ pretty(result.raw) }}</pre>
      </details>
    </template>
  </section>
</template>

<script>
import MarkdownRenderer from './MarkdownRenderer.vue'

export default {
  name: 'StudyResultPanel',
  components: { MarkdownRenderer },
  props: {
    result: { type: Object, default: null },
    loading: { type: Boolean, default: false },
    error: { type: String, default: null },
  },
  data() {
    return {
      ui: {
        loading: '正在加载最新结果...',
        empty: '执行一次入库、对话、讲解或出题后，结果会显示在这里。',
        result: '结果',
        metaTitle: '标题',
        metaTopic: '主题',
        metaGoal: '学习目标',
        metaTask: '当前任务',
        metaCategory: '分类路径',
        metaTags: '标签',
        memorySummary: '对话沉淀摘要',
        quizSetTitle: '专题练习集',
        quizTitle: '练习题',
        questionsSuffix: '题',
        focusReason: '出题原因',
        multipleChoice: '选择题',
        openEnded: '开放题',
        copyQuestion: '复制题目',
        gradingRubric: '评分标准',
        quizHintTitle: '提示',
        quizHintBody:
          '参考答案已保留在后端，这里不直接展示，你可以直接作答或继续生成同主题练习。',
        quizSetHintBody:
          '这里的练习集会把选择题和开放题一起给出，点击进入答题页后可以直接成套作答。',
        gradeTitle: '批改结果',
        masteryChange: '掌握度：',
        feedback: '反馈建议',
        referenceAnswer: '参考答案',
        nextAction: '下一步建议',
        learningPlan: '学习计划',
        planSubtitle: '基于当前学习状态生成的后续建议。',
        todayTasks: '今日任务',
        nextThreeDays: '未来三天',
        successCriteria: '达成标准',
        evidence: '检索依据',
        document: '文档',
        rawJson: '原始 JSON',
      },
    }
  },
  computed: {
    subtitle() {
      if (!this.result) return ''
      if (this.result.kind === 'quiz') {
        return '结构化练习题，可以直接作答或继续扩展练习。'
      }
      if (this.result.kind === 'quiz_set') {
        return '系统已经按专题批量出题，更适合集中练习。'
      }
      if (this.result.kind === 'grade') {
        return '批改结果会同步到掌握度和学习计划。'
      }
      if (this.result.kind === 'plan') {
        return '这是 Agent 基于你当前状态给出的后续建议。'
      }
      return '这是 StudyLoop Agent 的结构化输出。'
    },
    masteryPercent() {
      if (!this.result || !this.result.grade || this.result.grade.new_mastery_score == null) return 0
      return Math.round(this.result.grade.new_mastery_score * 100)
    },
    masteryBefore() {
      if (!this.result || !this.result.grade || this.result.grade.old_mastery_score == null) return '--'
      return this.result.grade.old_mastery_score
    },
    learningPlanItems() {
      if (!this.result || !this.result.grade || !this.result.grade.learning_plan) return []
      const plan = this.result.grade.learning_plan
      return plan.next_actions || plan.today_tasks || []
    },
    classification() {
      return this.result && (this.result.classification || (this.result.raw && this.result.raw.classification))
        ? this.result.classification || this.result.raw.classification
        : null
    },
    autoContext() {
      return this.result && (this.result.autoContext || (this.result.raw && this.result.raw.auto_context))
        ? this.result.autoContext || this.result.raw.auto_context
        : null
    },
    memorySummary() {
      return this.result && (this.result.memorySummary || (this.result.raw && this.result.raw.memory_summary))
        ? this.result.memorySummary || this.result.raw.memory_summary
        : null
    },
    displayTitle() {
      if (this.classification && this.classification.title) return this.classification.title
      if (this.autoContext && this.autoContext.title) return this.autoContext.title
      if (this.memorySummary && this.memorySummary.title) return this.memorySummary.title
      return ''
    },
    displayTopic() {
      if (this.classification && this.classification.primary_topic) return this.classification.primary_topic
      if (this.autoContext && this.autoContext.current_topic) return this.autoContext.current_topic
      return ''
    },
    displayGoal() {
      if (this.classification && this.classification.learning_goal) return this.classification.learning_goal
      if (this.autoContext && this.autoContext.learning_goal) return this.autoContext.learning_goal
      return ''
    },
    displayTask() {
      if (this.classification && this.classification.current_task) return this.classification.current_task
      if (this.autoContext && this.autoContext.current_task) return this.autoContext.current_task
      return ''
    },
    displayCategoryPath() {
      const path =
        (this.classification && this.classification.category_path) ||
        (this.autoContext && this.autoContext.category_path) ||
        (this.memorySummary && this.memorySummary.category_path) ||
        []
      return Array.isArray(path) && path.length ? path.join(' / ') : ''
    },
    displayTags() {
      const tags =
        (this.classification && this.classification.tags) || (this.memorySummary && this.memorySummary.tags) || []
      return Array.isArray(tags) && tags.length ? tags.join(' / ') : ''
    },
    memorySummaryText() {
      return this.memorySummary && this.memorySummary.summary ? this.memorySummary.summary : ''
    },
    hasMeta() {
      return Boolean(
        this.displayTitle ||
          this.displayTopic ||
          this.displayGoal ||
          this.displayTask ||
          this.displayCategoryPath ||
          this.displayTags
      )
    },
    quizTypeLabel() {
      if (!this.result || !this.result.quiz) return ''
      return `${this.result.quiz.question_type} | ${this.result.quiz.difficulty}`
    },
    mistakeLabel() {
      if (!this.result || !this.result.grade) return ''
      return `错误类型：${this.result.grade.mistake_type}`
    },
  },
  methods: {
    pretty(value) {
      if (value == null) return ''
      if (typeof value === 'string') return value
      return JSON.stringify(value, null, 2)
    },
    async copyQuestion(question) {
      if (!question) return
      try {
        await navigator.clipboard.writeText(question)
      } catch (error) {
        // Ignore clipboard failures in unsupported environments.
      }
    },
  },
}
</script>
