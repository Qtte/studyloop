<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>自由对话</h4>
        <p>直接和 LLM 交流，Agent 会自动总结这次对话并分类保存，方便以后复盘和检索。</p>
      </div>
      <span class="tab-tag">对话沉淀</span>
    </div>
    <label>对话内容
      <textarea :value="value.message" rows="10" placeholder="例如：帮我解释一下上下文工程和长提示词之间的区别。" @input="update('message', $event.target.value)"></textarea>
    </label>
    <label class="checkbox-field">
      <input :checked="value.save_memory" type="checkbox" @change="update('save_memory', $event.target.checked)" />
      <span>自动总结并保存本轮对话</span>
    </label>
    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">{{ loading ? '思考中...' : '发送并沉淀' }}</button>
  </div>
</template>

<script>
export default {
  name: 'ChatForm',
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
