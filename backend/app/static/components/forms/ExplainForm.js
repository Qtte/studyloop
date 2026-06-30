export const ExplainForm = {
  props: {
    modelValue: { type: Object, required: true },
    loading: { type: Boolean, default: false },
  },
  emits: ["update:modelValue", "submit"],
  methods: {
    update(field, value) {
      this.$emit("update:modelValue", { ...this.modelValue, [field]: value });
    },
  },
  template: `
    <div class="form-card">
      <div class="form-head">
        <div>
          <h4>知识讲解</h4>
          <p>根据学习目标、主题和已检索证据生成讲解内容。</p>
        </div>
        <span class="tab-tag">Explain</span>
      </div>
      <label>学习目标
        <input :value="modelValue.learning_goal" placeholder="你希望学会什么" @input="update('learning_goal', $event.target.value)" />
      </label>
      <label>当前主题
        <input :value="modelValue.current_topic" placeholder="例如：Context Engineering" @input="update('current_topic', $event.target.value)" />
      </label>
      <label>当前任务
        <input :value="modelValue.current_task" placeholder="例如：Explain the concept clearly." @input="update('current_task', $event.target.value)" />
      </label>
      <label>提问内容
        <textarea :value="modelValue.question" rows="8" placeholder="你想让 Agent 解释什么" @input="update('question', $event.target.value)"></textarea>
      </label>
      <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
        {{ loading ? '生成中...' : '生成讲解' }}
      </button>
    </div>
  `,
};
