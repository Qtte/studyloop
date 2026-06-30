export const GradeForm = {
  props: {
    modelValue: { type: Object, required: true },
    loading: { type: Boolean, default: false },
    lastQuiz: { type: Object, default: null },
  },
  emits: ["update:modelValue", "submit", "sync-quiz"],
  methods: {
    update(field, value) {
      this.$emit("update:modelValue", { ...this.modelValue, [field]: value });
    },
  },
  template: `
    <div class="form-card">
      <div class="form-head">
        <div>
          <h4>批改与计划</h4>
          <p>批改答案、更新记忆，并生成下一步学习计划。</p>
        </div>
        <span class="tab-tag">Grade</span>
      </div>
      <div class="inline-actions" v-if="lastQuiz">
        <button class="ghost-btn small-btn" @click="$emit('sync-quiz')">从最近练习题同步</button>
      </div>
      <label>学习目标
        <input :value="modelValue.learning_goal" placeholder="例如：诊断学习者理解情况" @input="update('learning_goal', $event.target.value)" />
      </label>
      <label>当前主题
        <input :value="modelValue.current_topic" placeholder="例如：Context Engineering" @input="update('current_topic', $event.target.value)" />
      </label>
      <label>题目
        <textarea :value="modelValue.question" rows="4" placeholder="将要批改的题目" @input="update('question', $event.target.value)"></textarea>
      </label>
      <label>学生答案
        <textarea :value="modelValue.student_answer" rows="6" placeholder="学习者的回答" @input="update('student_answer', $event.target.value)"></textarea>
      </label>
      <label>参考答案（选填）
        <textarea :value="modelValue.reference_answer" rows="5" placeholder="可选。如果不填，后端会用主题和上下文辅助批改。" @input="update('reference_answer', $event.target.value)"></textarea>
      </label>
      <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
        {{ loading ? '批改中...' : '批改并更新计划' }}
      </button>
    </div>
  `,
};
