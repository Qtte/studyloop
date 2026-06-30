<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>答案批改</h4>
        <p>只保留题目和你的回答，参考答案会由 Agent 结合知识库和上下文自动生成。</p>
      </div>
      <span class="tab-tag">批改</span>
    </div>
    <div class="inline-actions" v-if="lastQuiz">
      <button class="ghost-btn small-btn" @click="$emit('sync-quiz')">同步最近题目</button>
    </div>
    <label>题目内容
      <textarea :value="value.question" rows="4" placeholder="粘贴需要批改的题目" @input="update('question', $event.target.value)"></textarea>
    </label>
    <label>我的回答
      <textarea :value="value.student_answer" rows="6" placeholder="输入你的作答内容" @input="update('student_answer', $event.target.value)"></textarea>
    </label>
    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">{{ loading ? '批改中...' : '批改并更新计划' }}</button>
  </div>
</template>

<script>
export default {
  name: 'GradeForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
    lastQuiz: { type: Object, default: null },
  },
  methods: {
    update(field, fieldValue) {
      this.$emit('input', { ...this.value, [field]: fieldValue })
    },
  },
}
</script>
