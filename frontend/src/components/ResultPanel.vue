<template>
  <section class="result-panel">
    <div v-if="loading" class="empty-state">正在加载最新结果...</div>
    <div v-else-if="error" class="error-box">{{ error }}</div>
    <div v-else-if="!result || result.kind === 'empty'" class="empty-state">
      执行一次讲解、出题或批改后，结果会显示在这里。
    </div>
    <template v-else>
      <div class="panel-title compact-title">
        <div>
          <h3>{{ result.title || '结果' }}</h3>
          <span>StudyLoop Agent 的结构化输出结果。</span>
        </div>
      </div>

      <MarkdownRenderer v-if="result.kind === 'markdown'" :content="result.markdown || ''" />

      <div v-else-if="result.kind === 'quiz'" class="result-card quiz-card">
        <div class="result-card-header">
          <div>
            <h4>练习题</h4>
            <p>{{ result.quiz.question_type }} | {{ result.quiz.difficulty }}</p>
          </div>
          <button class="ghost-btn small-btn" @click="copyQuestion(result.quiz.question)">复制题目</button>
        </div>
        <div class="result-question">{{ result.quiz.question }}</div>
        <div class="chip-row">
          <span class="result-chip" v-for="item in result.quiz.expected_points" :key="item">{{ item }}</span>
        </div>
        <div class="result-block" v-if="result.quiz.reference_answer">
          <strong>参考答案</strong>
          <div>{{ result.quiz.reference_answer }}</div>
        </div>
        <div class="result-block" v-if="result.quiz.grading_rubric && result.quiz.grading_rubric.length">
          <strong>评分标准</strong>
          <ul>
            <li v-for="item in result.quiz.grading_rubric" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <div v-else-if="result.kind === 'grade'" class="result-card grade-card">
        <div class="result-card-header">
          <div>
            <h4>批改结果</h4>
            <p>错误类型：{{ result.grade.mistake_type }}</p>
          </div>
          <div class="score-badge">{{ result.grade.score }}</div>
        </div>
        <div class="mastery-meter" v-if="result.grade.new_mastery_score !== null">
          <div class="mastery-meter-bar" :style="{ width: masteryPercent + '%' }"></div>
        </div>
        <div class="mastery-meta" v-if="result.grade.new_mastery_score !== null">
          掌握度：{{ masteryBefore }} -> {{ result.grade.new_mastery_score }}
        </div>
        <div class="result-block" v-if="result.grade.feedback">
          <strong>反馈建议</strong>
          <p>{{ result.grade.feedback }}</p>
        </div>
        <div class="result-block" v-if="result.grade.reference_answer">
          <strong>参考答案</strong>
          <MarkdownRenderer :content="result.grade.reference_answer" :compact="true" />
        </div>
        <div class="result-block" v-if="result.grade.next_action">
          <strong>下一步建议</strong>
          <p>{{ result.grade.next_action }}</p>
        </div>
        <div v-if="result.grade.learning_plan" class="result-block">
          <strong>学习计划</strong>
          <ul>
            <li v-for="item in learningPlanItems" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <div v-else-if="result.kind === 'plan'" class="result-card plan-card">
        <div class="result-card-header">
          <div>
            <h4>学习计划</h4>
            <p>基于当前学习状态生成的后续建议。</p>
          </div>
        </div>
        <p class="plan-summary">{{ result.plan.plan_summary }}</p>
        <div class="result-block" v-if="result.plan.today_tasks && result.plan.today_tasks.length">
          <strong>今日任务</strong>
          <ul>
            <li v-for="item in result.plan.today_tasks" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block" v-if="result.plan.next_three_days && result.plan.next_three_days.length">
          <strong>未来三天</strong>
          <ul>
            <li v-for="item in result.plan.next_three_days" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block" v-if="result.plan.success_criteria && result.plan.success_criteria.length">
          <strong>达成标准</strong>
          <ul>
            <li v-for="item in result.plan.success_criteria" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <div v-else class="result-box"><pre>{{ pretty(result.raw) }}</pre></div>

      <div v-if="result.evidence && result.evidence.length" class="evidence-block">
        <h4>检索依据</h4>
        <div class="evidence-list">
          <div class="evidence-item" v-for="(item, index) in result.evidence" :key="index">
            <strong>{{ item.source || item.label || item.doc_id || ('文档 ' + (index + 1)) }}</strong>
            <p>{{ item.content || item.text || item.label || pretty(item) }}</p>
          </div>
        </div>
      </div>

      <details class="raw-viewer">
        <summary>原始 JSON</summary>
        <pre>{{ pretty(result.raw) }}</pre>
      </details>
    </template>
  </section>
</template>

<script>
import MarkdownRenderer from './MarkdownRenderer.vue'

export default {
  name: 'ResultPanel',
  components: { MarkdownRenderer: MarkdownRenderer },
  props: {
    result: { type: Object, default: null },
    loading: { type: Boolean, default: false },
    error: { type: String, default: null },
  },
  computed: {
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
