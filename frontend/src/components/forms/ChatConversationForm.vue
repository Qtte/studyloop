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
      {{ ui.message }}
      <textarea
        :value="value.message"
        rows="10"
        :placeholder="ui.messagePlaceholder"
        @input="update('message', $event.target.value)"
      ></textarea>
    </label>

    <label class="checkbox-field">
      <input :checked="value.saveMemory" type="checkbox" @change="update('saveMemory', $event.target.checked)" />
      <span>{{ ui.saveMemory }}</span>
    </label>

    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
      {{ loading ? ui.loading : ui.submit }}
    </button>
  </div>
</template>

<script>
export default {
  name: 'ChatConversationForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
  },
  data() {
    return {
      ui: {
        title: '\u81ea\u7531\u5bf9\u8bdd',
        subtitle:
          '\u76f4\u63a5\u548c LLM \u4ea4\u6d41\uff0cAgent \u4f1a\u81ea\u52a8\u603b\u7ed3\u8fd9\u6b21\u5bf9\u8bdd\u5e76\u5206\u7c7b\u4fdd\u5b58\uff0c\u65b9\u4fbf\u4ee5\u540e\u590d\u76d8\u548c\u68c0\u7d22\u3002',
        tag: '\u5bf9\u8bdd\u6c89\u6dc0',
        message: '\u5bf9\u8bdd\u5185\u5bb9',
        messagePlaceholder:
          '\u4f8b\u5982\uff1a\u5e2e\u6211\u89e3\u91ca\u4e00\u4e0b\u4e0a\u4e0b\u6587\u5de5\u7a0b\u548c\u957f\u63d0\u793a\u8bcd\u4e4b\u95f4\u7684\u533a\u522b\u3002',
        saveMemory: '\u81ea\u52a8\u603b\u7ed3\u5e76\u4fdd\u5b58\u672c\u8f6e\u5bf9\u8bdd',
        loading: '\u601d\u8003\u4e2d...',
        submit: '\u53d1\u9001\u5e76\u6c89\u6dc0',
      },
    }
  },
  methods: {
    update(field, fieldValue) {
      this.$emit('input', { ...this.value, [field]: fieldValue })
    },
  },
}
</script>
