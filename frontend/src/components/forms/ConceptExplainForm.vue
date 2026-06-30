<template>
  <div class="form-card">
    <div class="form-head">
      <div>
        <h4>{{ ui.title }}</h4>
        <p>{{ ui.subtitle }}</p>
      </div>
      <span class="tab-tag">{{ ui.tag }}</span>
    </div>

    <div v-if="weakTopics.length" class="topic-list">
      <div
        v-for="topic in weakTopics"
        :key="topic.name"
        class="topic-card"
        :class="{ 'is-loading': loading }"
        @click="handleTopicClick(topic)"
      >
        <div class="topic-card-header">
          <strong class="topic-name">{{ topic.display_name || topic.name }}</strong>
          <span v-if="topic.mistake_count" class="mistake-badge">{{ ui.mistakePrefix }}{{ topic.mistake_count }}</span>
        </div>
        <div class="mastery-bar-track">
          <div
            class="mastery-bar-fill"
            :class="masteryBarClass(topic.mastery)"
            :style="{ width: (topic.mastery != null ? Math.round(topic.mastery * 100) : 0) + '%' }"
          ></div>
        </div>
        <div class="topic-card-footer">
          <span class="mastery-text">
            {{ ui.masteryLabel }} {{ topic.mastery != null ? Math.round(topic.mastery * 100) + '%' : '--' }}
          </span>
          <span class="topic-path">{{ topic.full_path_label || topic.name }}</span>
        </div>
      </div>
    </div>

    <div v-else class="empty-state">
      <p>{{ ui.empty }}</p>
    </div>
  </div>
</template>

<script>
export default {
  name: 'ConceptExplainForm',
  props: {
    value: { type: Object, required: true },
    loading: { type: Boolean, default: false },
    weakTopics: { type: Array, default: () => [] },
  },
  data() {
    return {
      ui: {
        title: '\u6982\u5ff5\u8bb2\u89e3',
        subtitle:
          '\u4ece\u8584\u5f31\u77e5\u8bc6\u70b9\u4e2d\u9009\u62e9\u4e00\u4e2a\uff0c\u7cfb\u7edf\u4f1a\u81ea\u52a8\u4e3a\u4f60\u751f\u6210\u6709\u8bc1\u636e\u652f\u6491\u7684\u8bb2\u89e3\u3002',
        tag: '\u8bb2\u89e3',
        mistakePrefix: '\u9519',
        masteryLabel: '\u638c\u63e1\u5ea6',
        empty:
          '\u8fd8\u6ca1\u6709\u5b66\u4e60\u6570\u636e\uff0c\u8bf7\u5148\u5bfc\u5165\u6750\u6599\u6216\u5b8c\u6210\u51e0\u6b21\u5b66\u4e60\u3002',
        masteryHigh: '\u826f\u597d',
        masteryMedium: '\u4e2d\u7b49',
        masteryLow: '\u5f31',
      },
    }
  },
  methods: {
    handleTopicClick(topic) {
      if (this.loading) return
      this.$emit('input', {
        question: '\u8bf7\u89e3\u91ca ' + (topic.display_name || topic.name),
        current_topic: topic.display_name || topic.name,
        topic_name: topic.name,
        full_path_label: topic.full_path_label || topic.name,
      })
      this.$emit('submit')
    },
    masteryBarClass(mastery) {
      if (mastery == null) return ''
      if (mastery >= 0.65) return 'bar-good'
      if (mastery >= 0.4) return 'bar-medium'
      return 'bar-low'
    },
  },
}
</script>

<style scoped>
.topic-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.topic-card {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.topic-card:hover {
  border-color: #f0883e;
  box-shadow: 0 1px 4px rgba(240, 136, 62, 0.12);
}

.topic-card.is-loading {
  opacity: 0.55;
  pointer-events: none;
}

.topic-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.topic-name {
  font-size: 15px;
  color: #222;
}

.mistake-badge {
  font-size: 12px;
  background: #fff1f0;
  color: #cf1322;
  padding: 1px 8px;
  border-radius: 8px;
}

.mastery-bar-track {
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  margin-bottom: 6px;
  overflow: hidden;
}

.mastery-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}

.bar-low {
  background: #ff4d4f;
}

.bar-medium {
  background: #faad14;
}

.bar-good {
  background: #52c41a;
}

.topic-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: #888;
}

.mastery-text {
  font-weight: 500;
}

.topic-path {
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #999;
  font-size: 14px;
}
</style>
