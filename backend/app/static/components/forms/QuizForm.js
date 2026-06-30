export const QuizForm = {
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
          <h4>生成练习</h4>
          <p>生成结构化练习题，并可一键同步到批改区。</p>
        </div>
        <span class="tab-tag">Quiz</span>
      </div>
      <label>学习目标
        <input :value="modelValue.learning_goal" placeholder="例如：能够解释上下文工程" @input="update('learning_goal', $event.target.value)" />
      </label>
      <label>当前主题
        <input :value="modelValue.current_topic" placeholder="例如：Context Engineering" @input="update('current_topic', $event.target.value)" />
      </label>
      <label>当前任务
        <input :value="modelValue.current_task" placeholder="Generate a short quiz." @input="update('current_task', $event.target.value)" />
      </label>
      <label>难度
        <select :value="modelValue.difficulty" @change="update('difficulty', $event.target.value)">
          <option value="easy">easy</option>
          <option value="medium">medium</option>
          <option value="hard">hard</option>
        </select>
      </label>
      <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
        {{ loading ? '生成中...' : '生成练习题' }}
      </button>
    </div>
  `,
};
