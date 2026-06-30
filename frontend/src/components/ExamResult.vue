<template>
  <div class="exam-result">
    <div class="result-header">
      <h2>{{ ui.title }}</h2>
      <div class="score-circle" :class="scoreColorClass">
        <span class="score-num">{{ result.total_score }}</span>
        <span class="score-divider">/</span>
        <span class="score-max">{{ result.total_max }}</span>
      </div>
      <p class="summary-text">{{ result.summary }}</p>
    </div>

    <div class="result-body">
      <div
        v-for="item in result.results"
        :key="item.question_id"
        class="result-item"
      >
        <div class="result-item-header">
          <span class="result-qid">{{ item.question_id }}</span>
          <span
            :class="['result-score-chip', item.score === item.max_score ? 'perfect' : item.score > 0 ? 'partial' : 'zero']"
          >
            {{ item.score }}/{{ item.max_score }}
          </span>
        </div>
        <p class="student-answer">
          <strong>{{ ui.yourAnswer }}:</strong>
          <span :class="{ empty: !item.student_answer }">
            {{ item.student_answer || ui.noAnswer }}
          </span>
        </p>
        <p v-if="item.correct_answer" class="correct-answer">
          <strong>{{ ui.correctAnswer }}:</strong> {{ item.correct_answer }}
        </p>
        <p class="feedback">
          <strong>{{ ui.feedbackLabel }}:</strong> {{ item.feedback }}
        </p>
      </div>
    </div>

    <div class="result-footer">
      <button class="primary-btn" @click="$emit('retry')">{{ ui.retryBtn }}</button>
      <button class="secondary-btn" @click="$emit('back')">{{ ui.backBtn }}</button>
    </div>
  </div>
</template>

<script>
export default {
  name: 'ExamResult',
  props: {
    result: { type: Object, default: () => ({}) },
  },
  data() {
    return {
      ui: {
        title: '试卷批改结果',
        yourAnswer: '你的答案',
        noAnswer: '(未作答)',
        correctAnswer: '正确答案',
        feedbackLabel: '反馈',
        retryBtn: '再做一次',
        backBtn: '返回首页',
      },
    }
  },
  computed: {
    scoreColorClass() {
      if (!this.result.total_max) return ''
      const ratio = this.result.total_score / this.result.total_max
      if (ratio >= 0.8) return 'score-high'
      if (ratio >= 0.5) return 'score-mid'
      return 'score-low'
    },
  },
}
</script>

<style scoped>
.exam-result {
  max-width: 800px;
  margin: 0 auto;
}

.result-header {
  text-align: center;
  padding: 32px 24px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  margin-bottom: 24px;
}

.result-header h2 {
  margin: 0 0 16px;
  font-size: 1.3rem;
}

.score-circle {
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  padding: 16px 28px;
  border-radius: 16px;
  margin-bottom: 16px;
}

.score-circle.score-high { background: #f0fdf4; color: #16a34a; }
.score-circle.score-mid  { background: #fffbeb; color: #d97706; }
.score-circle.score-low  { background: #fef2f2; color: #dc2626; }

.score-num {
  font-size: 2.5rem;
  font-weight: 700;
}

.score-divider {
  font-size: 1.5rem;
  margin: 0 4px;
  opacity: 0.5;
}

.score-max {
  font-size: 1.5rem;
  font-weight: 500;
}

.summary-text {
  font-size: 1rem;
  color: #555;
  margin: 0;
}

.result-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-item {
  background: #fff;
  border-radius: 10px;
  padding: 20px;
  box-shadow: 0 1px 6px rgba(0,0,0,0.05);
}

.result-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.result-qid {
  font-weight: 600;
  color: #333;
}

.result-score-chip {
  padding: 4px 12px;
  border-radius: 20px;
  font-weight: 600;
  font-size: 0.9rem;
}

.result-score-chip.perfect { background: #f0fdf4; color: #16a34a; }
.result-score-chip.partial { background: #fffbeb; color: #d97706; }
.result-score-chip.zero   { background: #fef2f2; color: #dc2626; }

.student-answer, .correct-answer, .feedback {
  margin: 6px 0;
  font-size: 0.95rem;
  line-height: 1.5;
}

.empty {
  color: #999;
  font-style: italic;
}

.result-footer {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 24px;
}

.primary-btn {
  padding: 10px 28px;
  background: #4f46e5;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 1rem;
  cursor: pointer;
  font-weight: 500;
}

.secondary-btn {
  padding: 10px 20px;
  background: #fff;
  color: #666;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.95rem;
}
</style>