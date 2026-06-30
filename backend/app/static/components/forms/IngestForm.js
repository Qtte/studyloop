export const IngestForm = {
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
          <h4>材料导入</h4>
          <p>先把学习资料送进检索器，供 explain / quiz / grade 使用。</p>
        </div>
        <span class="tab-tag">Ingest</span>
      </div>
      <label>标题
        <input :value="modelValue.title" placeholder="例如：上下文工程速记" @input="update('title', $event.target.value)" />
      </label>
      <label>主题 Topic
        <input :value="modelValue.topic" placeholder="例如：Context Engineering" @input="update('topic', $event.target.value)" />
      </label>
      <label>资料来源
        <input :value="modelValue.source" placeholder="manual / article / lecture" @input="update('source', $event.target.value)" />
      </label>
      <label>学习材料
        <textarea :value="modelValue.content" rows="10" placeholder="把你的学习资料贴到这里" @input="update('content', $event.target.value)"></textarea>
      </label>
      <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
        {{ loading ? '导入中...' : '导入资料' }}
      </button>
    </div>
  `,
};
