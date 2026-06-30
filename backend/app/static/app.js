const { createApp, computed } = Vue;

createApp({
  data() {
    return {
      activeView: "explain",
      loading: {
        health: false,
        state: false,
        notes: false,
        ingest: false,
        explain: false,
        quiz: false,
        grade: false,
      },
      health: {
        status: "等待检测",
        mode: "unknown",
        message: "后端启动后，这里会显示当前模式和健康状态。",
        tone: "pending",
      },
      forms: {
        ingest: {
          title: "上下文工程速记",
          topic: "Context Engineering",
          source: "manual",
          content: "上下文工程的重点不是把提示词写长，而是把学习目标、证据、错题历史和当前任务组织成稳定结构。检索到的证据应当支持回答，而不是堆砌无关内容。",
        },
        explain: {
          learning_goal: "理解上下文工程如何帮助学习型 agent",
          current_topic: "Context Engineering",
          current_task: "Explain the concept clearly.",
          question: "为什么学习型 agent 需要把证据和错题历史一起放进上下文？",
        },
        quiz: {
          learning_goal: "能够解释上下文工程的核心作用",
          current_topic: "Context Engineering",
          current_task: "Generate a short quiz.",
          difficulty: "medium",
        },
        grade: {
          learning_goal: "诊断学习者对主题的理解情况",
          current_topic: "Context Engineering",
          question: "为什么学习型 agent 不应该只依赖长提示词？",
          student_answer: "因为只写很长的提示词不够稳定，应该把学习目标、证据和历史错误一起组织起来，这样模型更容易输出一致结果。",
          reference_answer: "学习型 agent 需要工程化的上下文，而不是单纯拉长提示词。结构化上下文可以把目标、证据、错误历史和当前任务稳定地传递给模型。",
        },
      },
      outputs: {
        ingest: null,
        explain: "这里会显示讲解结果。",
        quiz: null,
        grade: null,
      },
      state: null,
      notes: null,
      notesQuery: "",
      stageItems: [
        {
          title: "材料入库",
          copy: "先把学习材料切块写入检索器，形成当前主题的可召回证据。",
        },
        {
          title: "图编排推理",
          copy: "LangGraph 串联检索、笔记、上下文构建和 LLM 调用，自动路由 explain / quiz / grade。",
        },
        {
          title: "记忆回写",
          copy: "批改后会更新掌握度、写入错题笔记，并生成下一轮学习计划。",
        },
      ],
    };
  },
  computed: {
    llmBadge() {
      return this.health.mode === "mock" ? "Mock LLM" : "Online LLM";
    },
    documentsIndexed() {
      return this.state?.documents_indexed ?? 0;
    },
    noteCount() {
      return this.state?.notes?.count ?? 0;
    },
    currentMastery() {
      const mastery = this.state?.mastery_by_topic ?? {};
      const values = Object.values(mastery);
      if (!values.length) return "--";
      const percent = Math.round(Math.max(...values) * 100);
      return `${percent}%`;
    },
    planItems() {
      return this.outputs.grade?.next_plan?.next_actions ?? [];
    },
    noteItems() {
      return this.notes?.notes ?? [];
    },
    timelineItems() {
      const items = [];
      if (this.outputs.ingest) {
        items.push({ title: "材料已导入", body: `已建立 ${this.outputs.ingest.documents_indexed} 个可检索片段。` });
      }
      if (this.outputs.quiz?.quiz?.question) {
        items.push({ title: "最近生成题目", body: this.outputs.quiz.quiz.question });
      }
      if (this.outputs.grade?.result?.feedback) {
        items.push({ title: "最近一次批改反馈", body: this.outputs.grade.result.feedback });
      }
      return items;
    },
  },
  methods: {
    async api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ? JSON.stringify(data.detail) : JSON.stringify(data));
      }
      return data;
    },
    async fetchHealth() {
      this.loading.health = true;
      try {
        const data = await this.api("/health", { method: "GET" });
        this.health = {
          status: data.status.toUpperCase(),
          mode: data.llm_mode,
          message: `当前后端状态正常，正在使用 ${data.llm_mode} 模式。`,
          tone: data.status === "ok" ? "ok" : "pending",
        };
      } catch (error) {
        this.health = {
          status: "ERROR",
          mode: "unknown",
          message: error.message,
          tone: "error",
        };
      } finally {
        this.loading.health = false;
      }
    },
    async fetchState() {
      this.loading.state = true;
      try {
        this.state = await this.api("/study/state", { method: "GET" });
      } catch (error) {
        this.state = { error: error.message };
      } finally {
        this.loading.state = false;
      }
    },
    async fetchNotes() {
      this.loading.notes = true;
      try {
        const path = this.notesQuery.trim()
          ? `/notes?query=${encodeURIComponent(this.notesQuery.trim())}`
          : "/notes";
        this.notes = await this.api(path, { method: "GET" });
      } catch (error) {
        this.notes = { error: error.message, notes: [] };
      } finally {
        this.loading.notes = false;
      }
    },
    async submitIngest() {
      this.loading.ingest = true;
      try {
        const data = await this.api("/materials/ingest", {
          method: "POST",
          body: JSON.stringify(this.forms.ingest),
        });
        this.outputs.ingest = data;
        this.activeView = "explain";
        await this.fetchState();
      } catch (error) {
        this.outputs.ingest = { error: error.message };
      } finally {
        this.loading.ingest = false;
      }
    },
    async submitExplain() {
      this.loading.explain = true;
      try {
        const data = await this.api("/study/explain", {
          method: "POST",
          body: JSON.stringify(this.forms.explain),
        });
        this.outputs.explain = data.answer || JSON.stringify(data, null, 2);
        this.activeView = "explain";
      } catch (error) {
        this.outputs.explain = error.message;
      } finally {
        this.loading.explain = false;
      }
    },
    async submitQuiz() {
      this.loading.quiz = true;
      try {
        const data = await this.api("/study/quiz", {
          method: "POST",
          body: JSON.stringify(this.forms.quiz),
        });
        this.outputs.quiz = data;
        if (data.quiz?.question) {
          this.forms.grade.current_topic = this.forms.quiz.current_topic;
          this.forms.grade.question = data.quiz.question;
          this.forms.grade.reference_answer = data.quiz.reference_answer;
        }
        this.activeView = "quiz";
      } catch (error) {
        this.outputs.quiz = { error: error.message };
      } finally {
        this.loading.quiz = false;
      }
    },
    async submitGrade() {
      this.loading.grade = true;
      try {
        const data = await this.api("/study/grade", {
          method: "POST",
          body: JSON.stringify(this.forms.grade),
        });
        this.outputs.grade = data;
        this.activeView = "grade";
        await Promise.all([this.fetchState(), this.fetchNotes()]);
      } catch (error) {
        this.outputs.grade = { error: error.message };
      } finally {
        this.loading.grade = false;
      }
    },
    pretty(value) {
      if (value == null) return "";
      if (typeof value === "string") return value;
      return JSON.stringify(value, null, 2);
    },
  },
  async mounted() {
    await Promise.all([this.fetchHealth(), this.fetchState(), this.fetchNotes()]);
  },
  template: `
    <div class="page">
      <aside class="sidebar fade-up">
        <div class="brand">
          <div class="brand-badge">S</div>
          <div>
            <h1>StudyLoop Studio</h1>
            <p>Vue 控制台版学习闭环前端</p>
          </div>
        </div>

        <div class="nav-list">
          <div class="nav-chip">
            <div>
              <strong>当前模式</strong>
              <div class="muted">{{ llmBadge }}</div>
            </div>
            <span class="metric-pill">{{ health.status }}</span>
          </div>
          <div class="nav-chip">
            <div>
              <strong>掌握度</strong>
              <div class="muted">当前最高 topic</div>
            </div>
            <span class="tag">{{ currentMastery }}</span>
          </div>
          <div class="nav-chip">
            <div>
              <strong>笔记总数</strong>
              <div class="muted">含错题记录</div>
            </div>
            <span class="tag">{{ noteCount }}</span>
          </div>
        </div>

        <p class="sidebar-note">
          这版页面直接消费现有 FastAPI 接口，不加前端构建步骤。你可以把它当作 StudyLoop 的交互式工作台，用来观察材料导入、图编排、掌握度更新和记忆回写的全过程。
        </p>
      </aside>

      <main class="main">
        <section class="hero panel fade-up delay-1">
          <div class="hero-stack">
            <div>
              <p class="eyebrow">LangGraph + FastAPI + Vue</p>
              <h2>把学习 Agent 做成一块真的能操作的工作台</h2>
              <p class="hero-copy">
                这不是简单表单页，而是一个围绕学习闭环组织的 Studio。左边是当前系统视角，右边是材料导入、讲解、出题、批改、计划回写的前台视图。
              </p>
            </div>
            <div class="timeline" v-if="timelineItems.length">
              <div class="timeline-item" v-for="item in timelineItems" :key="item.title">
                <h5>{{ item.title }}</h5>
                <p>{{ item.body }}</p>
              </div>
            </div>
          </div>

          <div class="hero-side">
            <div>
              <span :class="['health-pill', health.tone]">{{ health.status }}</span>
              <p class="status-caption">{{ health.message }}</p>
            </div>
            <div class="stat-grid">
              <div class="stat-card">
                <div class="stat-label">Indexed Chunks</div>
                <div class="stat-value">{{ documentsIndexed }}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Saved Notes</div>
                <div class="stat-value">{{ noteCount }}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Mastery Peak</div>
                <div class="stat-value">{{ currentMastery }}</div>
              </div>
            </div>
            <button class="ghost-btn" @click="fetchHealth" :disabled="loading.health">
              {{ loading.health ? '检测中...' : '刷新服务状态' }}
            </button>
          </div>
        </section>

        <section class="panel fade-up delay-2">
          <div class="panel-title">
            <div>
              <h3>学习闭环阶段</h3>
              <span>从材料到计划回写，一次性看完整链路</span>
            </div>
          </div>
          <div class="stage-grid">
            <article class="stage-card" v-for="(item, index) in stageItems" :key="item.title">
              <div class="stage-index">{{ index + 1 }}</div>
              <h4>{{ item.title }}</h4>
              <p class="stage-copy">{{ item.copy }}</p>
            </article>
          </div>
        </section>

        <section class="panel fade-up delay-3">
          <div class="panel-title">
            <div>
              <h3>交互工作区</h3>
              <span>用 Vue 管理所有表单状态和结果映射</span>
            </div>
            <div class="tag">Active: {{ activeView }}</div>
          </div>

          <div class="forms-grid">
            <article class="form-card wide">
              <div class="form-head">
                <div>
                  <h4>材料导入</h4>
                  <div class="muted">先把课程内容切块并写入检索器</div>
                </div>
                <span class="tag">Ingest</span>
              </div>
              <div class="form-stack">
                <div class="kv-grid">
                  <label>标题
                    <input v-model="forms.ingest.title" />
                  </label>
                  <label>Topic
                    <input v-model="forms.ingest.topic" />
                  </label>
                </div>
                <label>Source
                  <input v-model="forms.ingest.source" />
                </label>
                <label>学习材料
                  <textarea v-model="forms.ingest.content" rows="6"></textarea>
                </label>
                <button class="primary-btn" @click="submitIngest" :disabled="loading.ingest">
                  {{ loading.ingest ? '写入中...' : '写入材料并建立检索' }}
                </button>
              </div>
            </article>

            <article class="form-card">
              <div class="form-head">
                <div>
                  <h4>讲解知识点</h4>
                  <div class="muted">走 graph explain path</div>
                </div>
                <span class="tag">Explain</span>
              </div>
              <div class="form-stack">
                <label>学习目标
                  <input v-model="forms.explain.learning_goal" />
                </label>
                <label>当前主题
                  <input v-model="forms.explain.current_topic" />
                </label>
                <label>当前任务
                  <input v-model="forms.explain.current_task" />
                </label>
                <label>学习者问题
                  <textarea v-model="forms.explain.question" rows="5"></textarea>
                </label>
                <button class="primary-btn" @click="submitExplain" :disabled="loading.explain">
                  {{ loading.explain ? '讲解中...' : '生成讲解' }}
                </button>
              </div>
            </article>

            <article class="form-card">
              <div class="form-head">
                <div>
                  <h4>生成练习题</h4>
                  <div class="muted">结构化 QuizQuestion 输出</div>
                </div>
                <span class="tag">Quiz</span>
              </div>
              <div class="form-stack">
                <label>学习目标
                  <input v-model="forms.quiz.learning_goal" />
                </label>
                <label>当前主题
                  <input v-model="forms.quiz.current_topic" />
                </label>
                <label>难度
                  <select v-model="forms.quiz.difficulty">
                    <option value="easy">easy</option>
                    <option value="medium">medium</option>
                    <option value="hard">hard</option>
                  </select>
                </label>
                <button class="primary-btn" @click="submitQuiz" :disabled="loading.quiz">
                  {{ loading.quiz ? '生成中...' : '生成题目并同步到批改区' }}
                </button>
              </div>
            </article>

            <article class="form-card wide">
              <div class="form-head">
                <div>
                  <h4>批改与学习计划</h4>
                  <div class="muted">走 grade -> update_memory -> replan_learning_path</div>
                </div>
                <span class="tag">Grade</span>
              </div>
              <div class="form-stack">
                <div class="kv-grid">
                  <label>学习目标
                    <input v-model="forms.grade.learning_goal" />
                  </label>
                  <label>当前主题
                    <input v-model="forms.grade.current_topic" />
                  </label>
                </div>
                <label>题目
                  <textarea v-model="forms.grade.question" rows="3"></textarea>
                </label>
                <label>学生答案
                  <textarea v-model="forms.grade.student_answer" rows="4"></textarea>
                </label>
                <label>参考答案
                  <textarea v-model="forms.grade.reference_answer" rows="4"></textarea>
                </label>
                <button class="primary-btn" @click="submitGrade" :disabled="loading.grade">
                  {{ loading.grade ? '批改中...' : '批改答案并生成下一步计划' }}
                </button>
              </div>
            </article>
          </div>
        </section>

        <section class="panel fade-up delay-4">
          <div class="panel-title">
            <div>
              <h3>模型输出与系统状态</h3>
              <span>把讲解、题目、批改结果、状态和笔记并排观察</span>
            </div>
          </div>

          <div class="output-grid">
            <article class="output-card">
              <div class="output-head">
                <div>
                  <h4>讲解结果</h4>
                  <div class="output-meta">最新 explain 响应</div>
                </div>
                <span class="tag">Answer</span>
              </div>
              <div class="answer-box">{{ outputs.explain }}</div>
            </article>

            <article class="output-card">
              <div class="output-head">
                <div>
                  <h4>题目 JSON</h4>
                  <div class="output-meta">结构化 quiz payload</div>
                </div>
                <span class="tag">QuizQuestion</span>
              </div>
              <div class="result-box"><pre>{{ pretty(outputs.quiz) }}</pre></div>
            </article>

            <article class="output-card wide">
              <div class="output-head">
                <div>
                  <h4>批改结果与计划</h4>
                  <div class="output-meta">分数、错误类型、掌握度变化与后续建议</div>
                </div>
                <span class="tag">Grading + Plan</span>
              </div>
              <div class="meta-list" v-if="outputs.grade?.result">
                <div class="meta-chip">
                  <strong>分数</strong>
                  <div>{{ outputs.grade.result.score }}</div>
                </div>
                <div class="meta-chip">
                  <strong>错误类型</strong>
                  <div>{{ outputs.grade.result.mistake_type }}</div>
                </div>
                <div class="meta-chip">
                  <strong>掌握度变化</strong>
                  <div>{{ outputs.grade.mastery_before }} → {{ outputs.grade.mastery_after }}</div>
                </div>
                <div class="meta-chip">
                  <strong>反馈摘要</strong>
                  <div>{{ outputs.grade.result.feedback }}</div>
                </div>
              </div>
              <div class="empty-state" v-else>提交一次批改后，这里会展示结构化结果。</div>
              <div class="plan-box" v-if="outputs.grade"><pre>{{ pretty(outputs.grade) }}</pre></div>
            </article>

            <article class="output-card">
              <div class="output-head">
                <div>
                  <h4>当前系统状态</h4>
                  <div class="output-meta">documents_indexed / mastery / last_grade</div>
                </div>
                <button class="secondary-btn" @click="fetchState" :disabled="loading.state">
                  {{ loading.state ? '刷新中...' : '刷新状态' }}
                </button>
              </div>
              <div class="result-box"><pre>{{ pretty(state) }}</pre></div>
            </article>

            <article class="output-card">
              <div class="output-head">
                <div>
                  <h4>学习笔记与错题</h4>
                  <div class="output-meta">支持按关键词过滤</div>
                </div>
                <span class="tag">Notes</span>
              </div>
              <div class="toolbar">
                <input v-model="notesQuery" placeholder="搜索关键词，例如 context / evidence" />
                <button class="secondary-btn" @click="fetchNotes" :disabled="loading.notes">
                  {{ loading.notes ? '查询中...' : '刷新笔记' }}
                </button>
                <button class="ghost-btn" @click="notesQuery = ''; fetchNotes()">清空</button>
              </div>
              <div class="result-box"><pre>{{ pretty(notes) }}</pre></div>
            </article>
          </div>
        </section>
      </main>
    </div>
  `,
}).mount("#app");
