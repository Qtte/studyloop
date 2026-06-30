<template>
  <div class="app-shell">
    <aside class="sidebar-status">
      <div class="sidebar-brand">
        <div class="brand-mark">S</div>
        <div>
          <h1>StudyLoop Studio</h1>
          <p>把知识入库、自由对话、讲解练习和批改复盘串成一个学习闭环。</p>
        </div>
      </div>

      <div class="status-card-list">
        <div class="status-card">
          <span class="status-label">模型模式</span>
          <strong>{{ llmMode }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">已索引切片</span>
          <strong>{{ documentsIndexed }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">已保存笔记</span>
          <strong>{{ notesCount }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">最高掌握度</span>
          <strong>{{ masteryPeak }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">最佳主题</span>
          <strong>{{ masteryPeakTopic }}</strong>
        </div>
        <div class="status-card">
          <span class="status-label">后端状态</span>
          <strong :class="['health-text', health.status === 'ok' ? 'health-ok' : 'health-warn']">
            {{ health.status || 'unknown' }}
          </strong>
        </div>
      </div>

      <div class="plan-summary-card">
        <h3>最新计划</h3>
        <p>{{ lastPlanSummary }}</p>
      </div>
    </aside>

    <main class="main-workspace">
      <header class="workspace-header">
        <div>
          <h2>StudyLoop Studio</h2>
          <p>用户少填信息，Agent 自动分类、自动总结、自动生成参考答案，让学习过程更自然。</p>
        </div>
        <div class="header-actions">
          <span :class="['status-badge', health.status === 'ok' ? 'status-success' : 'status-pending']">
            {{ health.status || 'unknown' }}
          </span>
          <button class="secondary-btn" :disabled="loading.health" @click="refreshStatus">
            {{ loading.health ? '刷新中...' : '刷新状态' }}
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

          <IngestForm
            v-if="activeTab === 'knowledge'"
            v-model="forms.knowledge"
            :loading="loading.knowledge"
            @submit="handleIngest"
          />
          <ChatForm
            v-else-if="activeTab === 'chat'"
            v-model="forms.chat"
            :loading="loading.chat"
            @submit="handleChat"
          />
          <ExplainForm
            v-else-if="activeTab === 'explain'"
            v-model="forms.explain"
            :loading="loading.explain"
            @submit="handleExplain"
          />
          <QuizForm
            v-else-if="activeTab === 'quiz'"
            v-model="forms.quiz"
            :loading="loading.quiz"
            @submit="handleQuiz"
          />
          <GradeForm
            v-else
            v-model="forms.grade"
            :loading="loading.grade"
            :last-quiz="lastQuiz"
            @sync-quiz="syncQuizToGrade"
            @submit="handleGrade"
          />
        </div>

        <ResultPanel :result="currentResult" :loading="currentLoading" :error="error" />
      </section>
    </main>
  </div>
</template>

<script>
import IngestForm from './components/forms/IngestForm.vue'
import ChatForm from './components/forms/ChatForm.vue'
import ExplainForm from './components/forms/ExplainForm.vue'
import QuizForm from './components/forms/QuizForm.vue'
import GradeForm from './components/forms/GradeForm.vue'
import ResultPanel from './components/ResultPanel.vue'
import {
  chatWithAgent,
  explainConcept,
  generateQuiz,
  getHealth,
  getNotes,
  getStudyState,
  gradeAnswer,
  ingestMaterial,
} from './api/studyLoopApi'
import { normalizeApiResponse } from './utils/responseAdapter'

export default {
  name: 'App',
  components: {
    IngestForm,
    ChatForm,
    ExplainForm,
    QuizForm,
    GradeForm,
    ResultPanel,
  },
  data() {
    return {
      activeTab: 'knowledge',
      tabs: [
        { id: 'knowledge', label: '知识入库' },
        { id: 'chat', label: '自由对话' },
        { id: 'explain', label: '概念讲解' },
        { id: 'quiz', label: '生成练习' },
        { id: 'grade', label: '答案批改' },
      ],
      workflowSteps: ['入库', '分类', '对话', '讲解', '练习', '复盘'],
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
        grade: false,
      },
      error: null,
      currentResult: null,
      lastQuiz: null,
      notes: null,
      studyState: null,
      chatHistory: [],
      forms: {
        knowledge: {
          source: 'manual',
          content:
            '上下文工程是指在调用大模型之前，围绕当前任务去选择、压缩并结构化组织信息，让智能体拿到真正相关的上下文。',
        },
        chat: {
          message: '什么是上下文工程？为什么它比单纯拉长提示词更重要？',
          save_memory: true,
        },
        explain: {
          question: '上下文工程为什么会显著提升 Agent 的回答质量？',
        },
        quiz: {
          prompt: '围绕 Context Engineering 出一道中等难度的解释题。',
          difficulty: 'medium',
        },
        grade: {
          question: '',
          student_answer: '',
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
      if (this.currentResult && this.currentResult.kind === 'plan') return this.currentResult.plan.plan_summary
      if (
        this.currentResult &&
        this.currentResult.kind === 'grade' &&
        this.currentResult.grade &&
        this.currentResult.grade.learning_plan &&
        this.currentResult.grade.learning_plan.summary
      ) {
        return this.currentResult.grade.learning_plan.summary
      }
      return '你的下一步学习建议会显示在这里。'
    },
  },
  methods: {
    async refreshStatus() {
      this.loading.health = true
      try {
        const [health, studyState, notes] = await Promise.all([getHealth(), getStudyState(), getNotes()])
        this.health = health
        this.studyState = studyState
        this.notes = notes
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
        const payload = {
          message: this.forms.chat.message,
          save_memory: this.forms.chat.save_memory,
          conversation_context: this.chatHistory.slice(-6),
        }
        const raw = await chatWithAgent(payload)
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
        const raw = await generateQuiz(this.forms.quiz)
        this.currentResult = normalizeApiResponse(raw)
        this.lastQuiz = this.currentResult && this.currentResult.quiz ? this.currentResult.quiz : null
        this.syncQuizToGrade()
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.quiz = false
      }
    },
    async handleGrade() {
      this.error = null
      this.loading.grade = true
      try {
        const raw = await gradeAnswer(this.forms.grade)
        this.currentResult = normalizeApiResponse(raw)
        await this.refreshStatus()
      } catch (error) {
        this.error = error.message
      } finally {
        this.loading.grade = false
      }
    },
    syncQuizToGrade() {
      if (!this.lastQuiz) return
      this.forms.grade = {
        ...this.forms.grade,
        question: this.lastQuiz.question || this.forms.grade.question,
      }
    },
  },
  async mounted() {
    await this.refreshStatus()
  },
}
</script>
