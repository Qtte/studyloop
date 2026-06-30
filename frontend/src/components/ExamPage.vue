<template>
  <div class="exam-page">
    <!-- Loading state -->
    <div v-if="loading" class="exam-loading">
      <div class="spinner"></div>
      <p>{{ ui.loadingText }}</p>
    </div>

    <!-- Error state -->
    <div v-else-if="error" class="exam-error">
      <p>{{ error }}</p>
      <button class="secondary-btn" @click="$emit('back')">{{ ui.backBtn }}</button>
    </div>

    <!-- Exam paper -->
    <div v-else class="exam-paper">
      <div class="exam-header">
        <h2>{{ examTitle }}</h2>
        <p class="exam-meta">{{ ui.totalQuestions }}: {{ questions.length }} | {{ ui.topic }}: {{ topic }}</p>
      </div>

      <div class="exam-body">
        <div
          v-for="(q, index) in questions"
          :key="q.question_id || index"
          class="question-card"
        >
          <div class="question-header">
            <span class="question-number">{{ index + 1 }}</span>
            <span class="question-type-badge">{{ q.question_type === 'multiple_choice' ? ui.mcBadge : ui.oeBadge }}</span>
            <span v-if="q.difficulty" class="difficulty-badge">{{ q.difficulty }}</span>
          </div>

          <p class="question-text">{{ q.question }}</p>

          <!-- Multiple choice -->
          <div v-if="q.question_type === 'multiple_choice' && q.options && q.options.length" class="options-list">
            <button
              v-for="option in q.options"
              :key="option"
              :class="['option-btn', { selected: answers[q.question_id] === option }]"
              @click="selectOption(q.question_id, option)"
            >
              {{ option }}
            </button>
          </div>

          <!-- Open ended -->
          <div v-else class="open-ended-area">
            <textarea
              :value="answers[q.question_id] || ''"
              :placeholder="ui.answerPlaceholder"
              rows="5"
              @input="updateAnswer(q.question_id, $event.target.value)"
            ></textarea>
          </div>
        </div>
      </div>

      <div class="exam-footer">
        <button
          class="primary-btn submit-exam-btn"
          :disabled="submitting || !hasAnyAnswer"
          @click="handleSubmit"
        >
          {{ submitting ? ui.submitting : ui.submitBtn }}
        </button>
        <button class="secondary-btn" @click="$emit('back')">{{ ui.backBtn }}</button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'ExamPage',
  props: {
    quizSet: { type: Object, default: () => ({}) },
    loading: { type: Boolean, default: false },
    error: { type: String, default: null },
    submitting: { type: Boolean, default: false },
  },
  data() {
    return {
      answers: {},
      ui: {
        loadingText: '正在生成试卷...',
        backBtn: '返回',
        totalQuestions: '总题数',
        topic: '主题',
        mcBadge: '选择题',
        oeBadge: '问答题',
        answerPlaceholder: '请输入你的答案...',
        submitBtn: '提交试卷',
        submitting: '批改中...',
      },
    }
  },
  computed: {
    questions() {
      const qs = this.quizSet && this.quizSet.quizSet && Array.isArray(this.quizSet.quizSet.questions)
        ? this.quizSet.quizSet.questions
        : []
      return qs
    },
    topic() {
      return (this.quizSet && this.quizSet.quizSet && this.quizSet.quizSet.topic) || ''
    },
    examTitle() {
      return (this.quizSet && this.quizSet.title) || '练习试卷'
    },
    hasAnyAnswer() {
      return this.questions.some(q => {
        const ans = this.answers[q.question_id]
        return ans && ans.trim()
      })
    },
  },
  methods: {
    selectOption(questionId, option) {
      this.answers = { ...this.answers, [questionId]: option }
    },
    updateAnswer(questionId, value) {
      this.answers = { ...this.answers, [questionId]: value }
    },
    handleSubmit() {
      const payload = {
        questions: this.questions.map(q => ({
          question_id: q.question_id,
          question: q.question,
          question_type: q.question_type,
          options: q.options || [],
          correct_option: q.correct_option || '',
          reference_answer: q.reference_answer || '',
          student_answer: this.answers[q.question_id] || '',
        })),
        topic: this.topic,
      }
      this.$emit('submit', payload)
    },
  },
}
</script>

<style scoped>
.exam-page {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
}

.exam-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  gap: 20px;
  color: #666;
}

.spinner {
  width: 48px;
  height: 48px;
  border: 4px solid #e0e0e0;
  border-top: 4px solid #4f46e5;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.exam-error {
  padding: 40px 20px;
  text-align: center;
  color: #dc2626;
}

.exam-paper {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  overflow: hidden;
}

.exam-header {
  padding: 24px;
  border-bottom: 1px solid #eee;
}

.exam-header h2 {
  margin: 0 0 8px;
  font-size: 1.25rem;
}

.exam-meta {
  color: #888;
  font-size: 0.9rem;
  margin: 0;
}

.exam-body {
  padding: 24px;
}

.question-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 20px;
}

.question-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.question-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: #4f46e5;
  color: #fff;
  border-radius: 50%;
  font-size: 0.85rem;
  font-weight: 600;
}

.question-type-badge {
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: #eef2ff;
  color: #4f46e5;
}

.difficulty-badge {
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 4px;
  background: #f0fdf4;
  color: #16a34a;
}

.question-text {
  font-size: 1.05rem;
  line-height: 1.6;
  margin: 0 0 16px;
}

.options-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.option-btn {
  padding: 12px 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  background: #fafafa;
  text-align: left;
  cursor: pointer;
  font-size: 0.95rem;
  transition: all 0.15s;
}

.option-btn:hover {
  border-color: #4f46e5;
  background: #eef2ff;
}

.option-btn.selected {
  border-color: #4f46e5;
  background: #4f46e5;
  color: #fff;
}

.open-ended-area textarea {
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 0.95rem;
  line-height: 1.5;
  resize: vertical;
  font-family: inherit;
}

.open-ended-area textarea:focus {
  outline: none;
  border-color: #4f46e5;
}

.exam-footer {
  padding: 20px 24px;
  border-top: 1px solid #eee;
  display: flex;
  gap: 12px;
  justify-content: flex-end;
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

.primary-btn:disabled {
  background: #c7d2fe;
  cursor: not-allowed;
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

.submit-exam-btn {
  min-width: 140px;
}
</style>