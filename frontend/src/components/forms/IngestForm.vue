<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>知识入库</h4>
        <p>你只需要提供内容，Agent 会自动生成标题、主题、分类路径和摘要，并沉淀到知识库里。</p>
      </div>
      <span class="tab-tag">知识入库</span>
    </div>
    <label>资料来源
      <input :value="value.source" placeholder="例如：manual / article / lecture / file" @input="update('source', $event.target.value)" />
    </label>
    <label>上传文本文件
      <input type="file" accept=".txt,.md,.text" @change="handleFileChange" />
    </label>
    <label>知识内容
      <textarea :value="value.content" rows="12" placeholder="粘贴课程摘要、教材片段、文章内容或你自己的学习笔记" @input="update('content', $event.target.value)"></textarea>
    </label>
    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">{{ loading ? '入库中...' : '入库并自动分类' }}</button>
  </div>
</template>

<script>
export default {
  name: 'IngestForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
  },
  methods: {
    update(field, fieldValue) {
      this.$emit('input', { ...this.value, [field]: fieldValue })
    },
    handleFileChange(event) {
      const file = event.target.files && event.target.files[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = () => {
        this.$emit('input', {
          ...this.value,
          source: file.name,
          content: String(reader.result || ''),
        })
      }
      reader.readAsText(file)
    },
  },
}
</script>
