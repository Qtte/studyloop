<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>概念讲解</h4>
        <p>你只需要提出问题，学习目标、当前主题和任务会由 Agent 自动总结。</p>
      </div>
      <span class="tab-tag">讲解</span>
    </div>
    <label>提问内容
      <textarea :value="value.question" rows="8" placeholder="例如：上下文工程为什么会显著影响 Agent 的回答质量？" @input="update('question', $event.target.value)"></textarea>
    </label>
    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">{{ loading ? '讲解生成中...' : '生成讲解' }}</button>
  </div>
</template>

<script>
export default {
  name: 'ExplainForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
  },
  methods: {
    update(field, fieldValue) {
      this.$emit('input', { ...this.value, [field]: fieldValue })
    },
  },
}
</script>
