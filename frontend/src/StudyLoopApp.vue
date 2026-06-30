<template>
  <div class="app-shell">
    <aside class="sidebar-status">
      <div class="sidebar-brand">
        <div class="brand-mark">S</div>
        <div>
          <h1>{{ ui.brandTitle }}</h1>
          <p>{{ ui.brandSubtitle }}</p>
        </div>
      </div>

      <div class="status-card-list">
        <div class="status-card">
          <span class="status-label">{{ ui.llmMode }}</span>
          <strong>{{ llmMode }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">{{ ui.documentsIndexed }}</span>
          <strong>{{ documentsIndexed }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">{{ ui.notesCount }}</span>
          <strong>{{ notesCount }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">{{ ui.masteryPeak }}</span>
          <strong>{{ masteryPeak }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">{{ ui.masteryPeakTopic }}</span>
          <strong>{{ masteryPeakTopic }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">{{ ui.backend }}</span>
          <strong :class="['health-text', health.status === 'ok' ? 'health-ok' : 'health-warn']">
            {{ health.status || 'unknown' }}
          </strong>
        </div>
      </div>

      <div class="plan-summary-card">
        <h3>{{ ui.latestPlan }}</h3>
        <p>{{ lastPlanSummary }}</p>
      </div>
    </aside>

    <main class="main-workspace">
      <!-- Exam view mode -->
      <div v-if="viewMode === 'exam'" class="exam-fullscreen">
        <ExamPage
          :quiz-set="examPaper"
          :loading="examLoading"
          :error="examError"
          :submitting="examSubmitting"
          @submit="handleExamSubmit"
          @back="exitExamMode"
        />
      </div>
      <div v-else-if="viewMode === 'exam_result'" class="exam-fullscreen">
        <ExamResult
          :result="examResult"
          @retry="handleExamRetry"
          @back="exitExamMode"
        />
      </div>

      <!-- Normal view mode -->
      <template v-else>
      <header class="workspace-header">
        <div>
          <h2>{{ ui.headerTitle }}</h2>
          <p>{{ ui.headerSubtitle }}</p>
        </div>
        <div class="header-actions">
          <span :class="['status-badge', health.status === 'ok' ? 'status-success' : 'status-pending']">
            {{ health.status || 'unknown' }}
          </span>
          <button class="secondary-btn" :disabled="loading.health" @click="refreshStatus">
            {{ loading.health ? ui.refreshing : ui.refresh }}
          </button>
        </div>
      </header>

      <section class="workflow-stepper">
        <div v-for="step in workflowSteps" :key="step" class="workflow-step">
          <span>{{ step }}</span>
        </div>
      </section>

      <section class="workspace-grid">
        <div class="operation-panel">
          <div class="operation-tabs">
            <button
              v-for="tab in tabs"
              :key="tab.id"
              :class="['tab-button', { active: activeTab === tab.id }]"
              @click="activeTab = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>

          <KnowledgeIngestForm
            v-if="activeTab === 'knowledge'"
            v-model="forms.knowledge"
            :loading="loading.knowledge"
            @submit="handleIngest"
          />
          <ChatConversationForm
            v-else-if="activeTab === 'chat'"
            v-model="forms.chat"
            :loading="loading.chat"
            @submit="handleChat"
          />
          <ConceptExplainForm
            v-else-if="activeTab === 'explain'"
            v-model="forms.explain"
            :loading="loading.explain"
            :weak-topics="weakTopics"
            @submit="handleExplain"
          />
          <PracticeQuizForm
            v-else-if="activeTab === 'quiz'"
            v-model="forms.quiz"
            :loading="loading.quiz"
            :topic-options="practiceTopics"
            :topic-tree="practiceTopicTree"
            :recommended-topic="recommendedPracticeTopic"
            :recommended-topic-label="recommendedPracticeTopicLabel"
            :recommended-topic-path="recommendedPracticeTopicPath"
            @submit="handleQuiz"
          />
        </div>

        <StudyResultPanel :result="currentResult" :loading="currentLoading" :error="error" />
      </section>
      </template>
    </main>
  </div>
</template>

<script>
import KnowledgeIngestForm from './components/forms/KnowledgeIngestForm.vue'
import ChatConversationForm from './components/forms/ChatConversationForm.vue'
import ConceptExplainForm from './components/forms/ConceptExplainForm.vue'
import PracticeQuizForm from './components/forms/PracticeQuizForm.vue'
import StudyResultPanel from './components/StudyResultPanel.vue'
import ExamPage from './components/ExamPage.vue'
import ExamResult from './components/ExamResult.vue'
import {
  chatWithAgent,
  explainConcept,
  generateQuiz,
  getHealth,
  getNotes,
  getStudyState,
  getStudyTopics,
  ingestMaterial,
  submitExam,
} from './api/studyLoopApi'
import { normalizeApiResponse } from './utils/responseAdapter'

export default {
  name: 'StudyLoopApp',
  components: {
    KnowledgeIngestForm,
    ChatConversationForm,
    ConceptExplainForm,
    PracticeQuizForm,
    StudyResultPanel,
    ExamPage,
    ExamResult,
  },
  data() {
    return {
      ui: {
        brandTitle: 'StudyLoop Studio',
        brandSubtitle: '把知识入库、自由对话、概念讲解和答题复盘串成一个学习闭环。',
        llmMode: '模型模式',
        documentsIndexed: '已索引切片',
        notesCount: '已保存笔记',
        masteryPeak: '最高掌握度',
        masteryPeakTopic: '最佳主题',
        backend: '后端状态',
        latestPlan: '最新计划',
        headerTitle: '少填表单，多让 Agent 自动总结',
        headerSubtitle: '知识入库时自动生成标题、主题和分类；对话、讲解、出题、答题时自动补全上下文。',
        refresh: '刷新状态',
        refreshing: '刷新中...',
        emptyPlan: '你的下一步学习建议会显示在这里。',
      },
      activeTab: 'knowledge',
      viewMode: 'normal', // 'normal' | 'exam' | 'exam_result'
      examPaper: null,
      examResult: null,
      examLoading: false,
      examSubmitting: false,
      examError: null,
      tabs: [
        { id: 'knowledge', label: '知识入库' },
        { id: 'chat', label: '自由对话' },
        { id: 'explain', label: '概念讲解' },
        { id: 'quiz', label: '生成练习' },
      ],
      workflowSteps: [
        '入库',
        '分类',
        '对话',
        '讲解',
        '练习',
        '复盘',
      ],
      health: {
        status: 'unknown',
        llm_mode: 'unknown',
      },
      loading: {
        health: false,
        knowledge: false,
        chat: false,
        explain: false,
        quiz: false,
      },
      error: null,
      currentResult: null,
      notes: null,
      studyState: null,
      practiceTopicCatalog: {
        topics: [],
        recommended_topic: '',
        recommended_topic_label: '',
        recommended_topic_path: [],
        topic_tree: [],
      },
      chatHistory: [],
      forms: {
        knowledge: {
          source: 'manual',
          content:
            '上下文工程是在调用大模型之前，围绕当前任务去选择、压缩并结构化组织信息，让 Agent 拿到真正相关的上下文。',
        },
        chat: {
          message:
            '什么是上下文工程？为什么它比单纯拉长提示词更重要？',
          saveMemory: true,
        },
        explain: {
          question:
            '上下文工程为什么会显著提升 Agent 的回答质量？',
        },
        quiz: {
          prompt: '',
          currentTopic: '',
          currentPrimaryTopic: '',
          currentSecondaryTopic: '',
          focusMode: 'weakest',
          difficulty: 'medium',
          questionCount: 5,
          questionTypes: ['multiple_choice', 'open_ended'],
        },
      },
    }
  },
  computed: {
    currentLoading() {
      return this.loading[this.activeTab] || false
    },
    llmMode() {
      return this.health.llm_mode || this.health.mode || 'unknown'
    },
    documentsIndexed() {
      if (!this.studyState || this.studyState.documents_indexed == null) return 0
      return this.studyState.documents_indexed
    },
    notesCount() {
      if (!this.studyState || !this.studyState.notes || this.studyState.notes.count == null) return 0
      return this.studyState.notes.count
    },
    masteryPeak() {
      const mastery = this.studyState && this.studyState.mastery_by_topic ? this.studyState.mastery_by_topic : {}
      const values = Object.values(mastery)
      if (!values.length) return '--'
      return `${Math.round(Math.max(...values) * 100)}%`
    },
    masteryPeakTopic() {
      const mastery = this.studyState && this.studyState.mastery_by_topic ? this.studyState.mastery_by_topic : {}
      const entries = Object.entries(mastery)
      if (!entries.length) return '--'
      entries.sort((a, b) => b[1] - a[1])
      return entries[0][0]
    },
    lastPlanSummary() {
      if (this.currentResult && this.currentResult.kind === 'plan') {
        return this.currentResult.plan.plan_summary
      }
      if (
        this.currentResult &&
        this.currentResult.kind === 'grade' &&
        this.currentResult.grade &&
        this.currentResult.grade.learning_plan &&
        this.currentResult.grade.learning_plan.summary
      ) {
        return this.currentResult.grade.learning_plan.summary
      }
      return this.ui.emptyPlan
    },
    practiceTopics() {
      return this.practiceTopicCatalog && Array.isArray(this.practiceTopicCatalog.topics)
        ? this.practiceTopicCatalog.topics
        : []
    },
    practiceTopicTree() {
      return this.practiceTopicCatalog && Array.isArray(this.practiceTopicCatalog.topic_tree)
        ? this.practiceTopicCatalog.topic_tree
        : []
    },
    recommendedPracticeTopic() {
      return this.practiceTopicCatalog ? this.practiceTopicCatalog.recommended_topic || '' : ''
    },
    recommendedPracticeTopicLabel() {
      return this.practiceTopicCatalog ? this.practiceTopicCatalog.recommended_topic_label || '' : ''
    },
    recommendedPracticeTopicPath() {
      return this.practiceTopicCatalog && Array.isArray(this.practiceTopicCatalog.recommended_topic_path)
        ? this.practiceTopicCatalog.recommended_topic_path
        : []
    },
    weakTopics() {
      const topics = this.practiceTopics
      if (!topics.length) return []
      const sorted = [...topics].sort((a, b) => {
        const ma = a.mastery != null ? a.mastery : 1
        const mb = b.mastery != null ? b.mastery : 1
        return ma - mb
      })
      return sorted
    },
  },
  methods: {
    async refreshStatus() {
      this.loading.health = true
      try {
        const [health, studyState, notes, topicCatalog] = await Promise.all([
          getHealth(),
          getStudyState(),
          getNotes(),
          getStudyTopics(),
        ])
        this.health = health
        this.studyState = studyState
        this.notes = notes
        this.practiceTopicCatalog = topicCatalog
        this.syncQuizTopicSelection()
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.health = false
      }
    },
    async handleIngest() {
      this.error = null
      this.loading.knowledge = true
      try {
        const raw = await ingestMaterial(this.forms.knowledge)
        this.currentResult = normalizeApiResponse(raw)
        this.activeTab = 'chat'
        await this.refreshStatus()
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.knowledge = false
      }
    },
    async handleChat() {
      this.error = null
      this.loading.chat = true
      try {
        const raw = await chatWithAgent({
          message: this.forms.chat.message,
          save_memory: this.forms.chat.saveMemory,
          conversation_context: this.chatHistory.slice(-6),
        })
        this.currentResult = normalizeApiResponse(raw)
        this.chatHistory = this.chatHistory.concat([
          { role: 'user', content: this.forms.chat.message },
          { role: 'assistant', content: raw.answer || '' },
        ])
        this.forms.chat.message = ''
        await this.refreshStatus()
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.chat = false
      }
    },
    async handleExplain() {
      this.error = null
      this.loading.explain = true
      try {
        const raw = await explainConcept({
          question: this.forms.explain.question,
          current_topic: this.forms.explain.current_topic || undefined,
          conversation_context: this.chatHistory.slice(-6),
        })
        this.currentResult = normalizeApiResponse(raw)
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.explain = false
      }
    },
    async handleQuiz() {
      this.error = null
      this.loading.quiz = true
      try {
        const manualTopic = this.resolveManualQuizTopic()
        const raw = await generateQuiz({
          prompt: this.forms.quiz.prompt,
          current_topic: this.forms.quiz.focusMode === 'manual' ? manualTopic : null,
          difficulty: this.forms.quiz.difficulty,
          question_count: this.forms.quiz.questionCount,
          question_types: this.forms.quiz.questionTypes,
          focus_mode: this.forms.quiz.focusMode,
        })
        this.currentResult = normalizeApiResponse(raw)
        if (this.currentResult && this.currentResult.kind === 'quiz_set') {
          this.enterExamMode()
        }
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.quiz = false
      }
    },
    enterExamMode() {
      this.examPaper = this.currentResult
      this.examError = null
      this.examLoading = false
      this.examSubmitting = false
      this.examResult = null
      this.viewMode = 'exam'
    },
    exitExamMode() {
      this.viewMode = 'normal'
      this.examPaper = null
      this.examResult = null
      this.examError = null
      this.examLoading = false
      this.examSubmitting = false
    },
    async handleExamSubmit(payload) {
      this.examSubmitting = true
      this.examError = null
      try {
        const result = await submitExam(payload)
        this.examResult = result
        this.viewMode = 'exam_result'
        await this.refreshStatus()
      } catch (error) {
        this.examError = error.message
      } finally {
        this.examSubmitting = false
      }
    },
    handleExamRetry() {
      this.examResult = null
      this.viewMode = 'exam'
    },
    resolveManualQuizTopic() {
      if (this.forms.quiz.currentSecondaryTopic) return this.forms.quiz.currentSecondaryTopic
      if (this.forms.quiz.currentPrimaryTopic) return this.forms.quiz.currentPrimaryTopic
      return this.forms.quiz.currentTopic || ''
    },
    syncQuizTopicSelection() {
      const tree = this.practiceTopicTree
      if (!tree.length) return

      const recommendedPrimary = this.recommendedPracticeTopicPath[0] || ''
      const recommendedSecondary =
        this.recommendedPracticeTopicPath.length > 1 ? this.recommendedPracticeTopic : ''

      const primaryNames = tree.map((item) => item.name)
      const currentPrimary = primaryNames.includes(this.forms.quiz.currentPrimaryTopic)
        ? this.forms.quiz.currentPrimaryTopic
        : recommendedPrimary || primaryNames[0]

      const currentPrimaryNode = tree.find((item) => item.name === currentPrimary) || tree[0]
      const children = currentPrimaryNode && Array.isArray(currentPrimaryNode.children) ? currentPrimaryNode.children : []
      const childNames = children.map((item) => item.name)
      const currentSecondary = childNames.includes(this.forms.quiz.currentSecondaryTopic)
        ? this.forms.quiz.currentSecondaryTopic
        : childNames.includes(recommendedSecondary)
          ? recommendedSecondary
          : ''

      this.forms.quiz = {
        ...this.forms.quiz,
        currentPrimaryTopic: currentPrimary,
        currentSecondaryTopic: currentSecondary,
        currentTopic: currentSecondary || currentPrimary,
      }
    },
  },
  async mounted() {
    await this.refreshStatus()
  },
}
</script>

<style scoped>
.exam-fullscreen {
  width: 100%;
  padding: 24px;
}
</style>
