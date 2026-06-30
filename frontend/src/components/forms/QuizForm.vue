<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>生成练习题</h4>
        <p>你可以给一个简单提示，或者留空让 Agent 基于最近主题自动出题。</p>
      </div>
      <span class="tab-tag">自动出题</span>
    </div>
    <label>出题提示
      <textarea :value="value.prompt" rows="6" placeholder="例如：围绕 Context Engineering 出一道中等难度的解释题。" @input="update('prompt', $event.target.value)"></textarea>
    </label>
    <label>难度
      <select :value="value.difficulty" @change="update('difficulty', $event.target.value)">
        <option value="easy">简单</option>
        <option value="medium">中等</option>
        <option value="hard">困难</option>
      </select>
    </label>
    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">{{ loading ? '出题中...' : '生成练习题' }}</button>
  </div>
</template>

<script>
export default {
  name: 'QuizForm',
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
