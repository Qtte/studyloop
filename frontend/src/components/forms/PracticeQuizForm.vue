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
      {{ ui.focusMode }}
      <select :value="value.focusMode" @change="handleFocusModeChange($event.target.value)">
        <option value="weakest">{{ ui.focusWeakest }}</option>
        <option value="manual">{{ ui.focusManual }}</option>
      </select>
    </label>

    <template v-if="value.focusMode === 'manual'">
      <label>
        {{ ui.primaryTopic }}
        <select :value="value.currentPrimaryTopic" @change="handlePrimaryTopicChange($event.target.value)">
          <option value="">{{ ui.primaryTopicPlaceholder }}</option>
          <option v-for="item in normalizedTopicTree" :key="item.name" :value="item.name">
            {{ renderPrimaryLabel(item) }}
          </option>
        </select>
      </label>

      <label v-if="secondaryTopics.length">
        {{ ui.secondaryTopic }}
        <select :value="value.currentSecondaryTopic" @change="handleSecondaryTopicChange($event.target.value)">
          <option value="">{{ ui.secondaryTopicPlaceholder }}</option>
          <option v-for="item in secondaryTopics" :key="item.name" :value="item.name">
            {{ renderSecondaryLabel(item) }}
          </option>
        </select>
      </label>

      <p class="field-hint">
        {{ secondaryTopics.length ? ui.manualHintWithSecondary : ui.manualHintPrimaryOnly }}
      </p>
    </template>

    <p class="field-hint" v-else-if="recommendedHint">
      {{ ui.recommendedPrefix }}{{ recommendedHint }}
    </p>

    <label>
      {{ ui.questionCount }}
      <select :value="value.questionCount" @change="update('questionCount', Number($event.target.value))">
        <option :value="3">3</option>
        <option :value="5">5</option>
        <option :value="8">8</option>
      </select>
    </label>

    <div class="option-grid">
      <label class="checkbox-field">
        <input
          type="checkbox"
          :checked="hasQuestionType('multiple_choice')"
          @change="toggleQuestionType('multiple_choice', $event.target.checked)"
        />
        <span>{{ ui.multipleChoice }}</span>
      </label>
      <label class="checkbox-field">
        <input
          type="checkbox"
          :checked="hasQuestionType('open_ended')"
          @change="toggleQuestionType('open_ended', $event.target.checked)"
        />
        <span>{{ ui.openEnded }}</span>
      </label>
    </div>

    <label>
      {{ ui.difficulty }}
      <select :value="value.difficulty" @change="update('difficulty', $event.target.value)">
        <option value="easy">{{ ui.easy }}</option>
        <option value="medium">{{ ui.medium }}</option>
        <option value="hard">{{ ui.hard }}</option>
      </select>
    </label>

    <label>
      {{ ui.prompt }}
      <textarea
        :value="value.prompt"
        rows="4"
        :placeholder="ui.promptPlaceholder"
        @input="update('prompt', $event.target.value)"
      ></textarea>
    </label>

    <button class="primary-btn" :disabled="loading" @click="$emit('submit')">
      {{ loading ? ui.loading : ui.submit }}
    </button>
  </div>
</template>

<script>
export default {
  name: 'PracticeQuizForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
    topicOptions: { type: Array, default: () => [] },
    topicTree: { type: Array, default: () => [] },
    recommendedTopic: { type: String, default: '' },
    recommendedTopicLabel: { type: String, default: '' },
    recommendedTopicPath: { type: Array, default: () => [] },
  },
  data() {
    return {
      ui: {
        title: '生成练习题',
        subtitle: '先按薄弱点自动出题，也可以按一级专题或二级专题批量生成一套练习。',
        tag: '专题练习',
        focusMode: '练习模式',
        focusWeakest: '优先练薄弱专题',
        focusManual: '手动选择专题',
        primaryTopic: '一级专题',
        primaryTopicPlaceholder: '请选择一个一级专题',
        secondaryTopic: '二级专题',
        secondaryTopicPlaceholder: '不选则按一级专题综合出题',
        recommendedPrefix: '当前建议优先练习：',
        manualHintWithSecondary: '已支持细分专题。选中二级专题时会更聚焦，不选则围绕一级专题综合出题。',
        manualHintPrimaryOnly: '当前一级专题下还没有更细的专题，系统会按这个主题综合出题。',
        questionCount: '题目数量',
        multipleChoice: '选择题',
        openEnded: '开放题',
        difficulty: '难度',
        easy: '简单',
        medium: '中等',
        hard: '困难',
        prompt: '可选提示',
        promptPlaceholder: '例如：更偏向应用题，少一点定义题。不填也可以。',
        loading: '出题中...',
        submit: '生成练习集',
      },
    }
  },
  computed: {
    normalizedTopicTree() {
      if (Array.isArray(this.topicTree) && this.topicTree.length) {
        return this.topicTree
      }
      return (Array.isArray(this.topicOptions) ? this.topicOptions : []).map((item) => ({
        ...item,
        display_name: item.display_name || item.name,
        children: [],
      }))
    },
    selectedPrimaryNode() {
      return (
        this.normalizedTopicTree.find((item) => item.name === this.value.currentPrimaryTopic) ||
        this.normalizedTopicTree[0] ||
        null
      )
    },
    secondaryTopics() {
      if (!this.selectedPrimaryNode || !Array.isArray(this.selectedPrimaryNode.children)) {
        return []
      }
      return this.selectedPrimaryNode.children
    },
    recommendedHint() {
      if (this.recommendedTopicLabel) return this.recommendedTopicLabel
      if (Array.isArray(this.recommendedTopicPath) && this.recommendedTopicPath.length) {
        return this.recommendedTopicPath.join(' / ')
      }
      return this.recommendedTopic
    },
  },
  methods: {
    update(field, fieldValue) {
      this.$emit('input', { ...this.value, [field]: fieldValue })
    },
    handleFocusModeChange(mode) {
      const nextValue = { ...this.value, focusMode: mode }
      if (mode !== 'manual') {
        nextValue.currentTopic = ''
      } else {
        nextValue.currentTopic = this.value.currentSecondaryTopic || this.value.currentPrimaryTopic || ''
      }
      this.$emit('input', nextValue)
    },
    handlePrimaryTopicChange(primaryTopic) {
      this.$emit('input', {
        ...this.value,
        currentPrimaryTopic: primaryTopic,
        currentSecondaryTopic: '',
        currentTopic: primaryTopic,
      })
    },
    handleSecondaryTopicChange(secondaryTopic) {
      this.$emit('input', {
        ...this.value,
        currentSecondaryTopic: secondaryTopic,
        currentTopic: secondaryTopic || this.value.currentPrimaryTopic || '',
      })
    },
    hasQuestionType(questionType) {
      return Array.isArray(this.value.questionTypes) && this.value.questionTypes.includes(questionType)
    },
    toggleQuestionType(questionType, checked) {
      const current = Array.isArray(this.value.questionTypes) ? this.value.questionTypes.slice() : []
      const next = checked ? current.concat(questionType) : current.filter((item) => item !== questionType)
      this.$emit('input', {
        ...this.value,
        questionTypes: next.length ? Array.from(new Set(next)) : ['open_ended'],
      })
    },
    renderPrimaryLabel(item) {
      return this.renderTopicLabel(item)
    },
    renderSecondaryLabel(item) {
      return this.renderTopicLabel(item)
    },
    renderTopicLabel(item) {
      const name = item.display_name || item.name
      const mastery = item.mastery != null ? ` | ${Math.round(item.mastery * 100)}%` : ''
      const weakTag = item.is_weak ? ' | 薄弱' : ''
      return `${name}${mastery}${weakTag}`
    },
  },
}
</script>
