<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>{{ ui.title }}</h4>
        <p>{{ ui.subtitle }}</p>
      </div>
      <span class="tab-tag">{{ ui.tag }}</span>
    </div>

    <label>
      {{ ui.source }}
      <input :value="value.source" :placeholder="ui.sourcePlaceholder" @input="update('source', $event.target.value)" />
    </label>

    <label>
      {{ ui.file }}
      <input type="file" accept=".txt,.md,.text" @change="handleFileChange" />
    </label>

    <label>
      {{ ui.content }}
      <textarea
        :value="value.content"
        rows="12"
        :placeholder="ui.contentPlaceholder"
        @input="update('content', $event.target.value)"
      ></textarea>
    </label>

    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
      {{ loading ? ui.loading : ui.submit }}
    </button>
  </div>
</template>

<script>
export default {
  name: 'KnowledgeIngestForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
  },
  data() {
    return {
      ui: {
        title: '\u77e5\u8bc6\u5165\u5e93',
        subtitle:
          '\u4f60\u53ea\u9700\u8981\u63d0\u4f9b\u5185\u5bb9\uff0cAgent \u4f1a\u81ea\u52a8\u751f\u6210\u6807\u9898\u3001\u4e3b\u9898\u3001\u5206\u7c7b\u8def\u5f84\u548c\u6458\u8981\uff0c\u5e76\u6c89\u6dc0\u5230\u77e5\u8bc6\u5e93\u91cc\u3002',
        tag: '\u81ea\u52a8\u5206\u7c7b',
        source: '\u8d44\u6599\u6765\u6e90',
        file: '\u4e0a\u4f20\u6587\u672c\u6587\u4ef6',
        content: '\u77e5\u8bc6\u5185\u5bb9',
        sourcePlaceholder: 'manual / article / lecture / file',
        contentPlaceholder:
          '\u7c98\u8d34\u8bfe\u7a0b\u6458\u8981\u3001\u6559\u6750\u7247\u6bb5\u3001\u6587\u7ae0\u5185\u5bb9\u6216\u4f60\u81ea\u5df1\u7684\u5b66\u4e60\u7b14\u8bb0',
        loading: '\u5165\u5e93\u4e2d...',
        submit: '\u5165\u5e93\u5e76\u81ea\u52a8\u5206\u7c7b',
      },
    }
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
