import { MarkdownRenderer } from "./MarkdownRenderer.js";

const RawJsonViewer = {
  props: {
    raw: {
      type: [Object, Array, String, Number, Boolean],
      default: null,
    },
  },
  computed: {
    pretty() {
      if (this.raw == null) return "";
      if (typeof this.raw === "string") return this.raw;
      return JSON.stringify(this.raw, null, 2);
    },
  },
  template: `
    <details class="raw-viewer">
      <summary>查看原始 JSON</summary>
      <pre>{{ pretty }}</pre>
    </details>
  `,
};

const QuizCard = {
  props: ["quiz"],
  methods: {
    async copyQuestion() {
      await navigator.clipboard.writeText(this.quiz.question || "");
    },
  },
  template: `
    <div class="result-card quiz-card">
      <div class="result-card-header">
        <div>
          <h4>练习题</h4>
          <p>{{ quiz.question_type }} · {{ quiz.difficulty }}</p>
        </div>
        <button class="ghost-btn small-btn" @click="copyQuestion">复制题目</button>
      </div>
      <div class="result-question">{{ quiz.question }}</div>
      <div class="chip-row">
        <span class="result-chip" v-for="item in quiz.expected_points" :key="item">{{ item }}</span>
      </div>
      <div class="result-block" v-if="quiz.reference_answer">
        <strong>参考答案</strong>
        <div>{{ quiz.reference_answer }}</div>
      </div>
      <div class="result-block" v-if="quiz.grading_rubric?.length">
        <strong>评分要点</strong>
        <ul>
          <li v-for="item in quiz.grading_rubric" :key="item">{{ item }}</li>
        </ul>
      </div>
    </div>
  `,
};

const LearningPlanCard = {
  props: ["plan"],
  template: `
    <div class="result-card plan-card">
      <div class="result-card-header">
        <div>
          <h4>学习计划</h4>
          <p>基于本轮答题结果的下一步建议</p>
        </div>
      </div>
      <p class="plan-summary">{{ plan.plan_summary }}</p>
      <div class="result-block" v-if="plan.today_tasks?.length">
        <strong>今天先做</strong>
        <ul>
          <li v-for="item in plan.today_tasks" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div class="result-block" v-if="plan.next_three_days?.length">
        <strong>接下来关注</strong>
        <ul>
          <li v-for="item in plan.next_three_days" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div class="result-block" v-if="plan.success_criteria?.length">
        <strong>完成标准</strong>
        <ul>
          <li v-for="item in plan.success_criteria" :key="item">{{ item }}</li>
        </ul>
      </div>
    </div>
  `,
};

const GradeCard = {
  components: { MarkdownRenderer, LearningPlanCard },
  props: ["grade"],
  computed: {
    masteryPercent() {
      if (typeof this.grade.new_mastery_score !== "number") return 0;
      return Math.round(this.grade.new_mastery_score * 100);
    },
  },
  template: `
    <div class="result-card grade-card">
      <div class="result-card-header">
        <div>
          <h4>批改结果</h4>
          <p>错误类型：{{ grade.mistake_type }}</p>
        </div>
        <div class="score-badge">{{ grade.score }}</div>
      </div>
      <div class="mastery-meter" v-if="grade.new_mastery_score != null">
        <div class="mastery-meter-bar" :style="{ width: masteryPercent + '%' }"></div>
      </div>
      <div class="mastery-meta" v-if="grade.new_mastery_score != null">
        掌握度：{{ grade.old_mastery_score ?? '--' }} → {{ grade.new_mastery_score }}
      </div>
      <div class="result-block" v-if="grade.feedback">
        <strong>反馈</strong>
        <p>{{ grade.feedback }}</p>
      </div>
      <div class="result-columns">
        <div class="result-block" v-if="grade.correct_points?.length">
          <strong>回答正确点</strong>
          <ul>
            <li v-for="item in grade.correct_points" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div class="result-block" v-if="grade.missing_points?.length">
          <strong>缺失点</strong>
          <ul>
            <li v-for="item in grade.missing_points" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>
      <div class="result-block" v-if="grade.misconceptions?.length">
        <strong>常见误区</strong>
        <ul>
          <li v-for="item in grade.misconceptions" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div class="result-block" v-if="grade.reference_answer">
        <strong>参考答案</strong>
        <MarkdownRenderer :content="grade.reference_answer" compact />
      </div>
      <div class="result-block" v-if="grade.next_action">
        <strong>下一步建议</strong>
        <p>{{ grade.next_action }}</p>
      </div>
      <LearningPlanCard v-if="grade.learning_plan" :plan="{
        plan_summary: grade.learning_plan.summary || grade.learning_plan.plan_summary || '',
        today_tasks: grade.learning_plan.next_actions || grade.learning_plan.today_tasks || [],
        next_three_days: grade.learning_plan.focus_areas || grade.learning_plan.next_three_days || [],
        success_criteria: grade.learning_plan.recommended_question_types || grade.learning_plan.success_criteria || []
      }" />
    </div>
  `,
};

export const ResultPanel = {
  name: "ResultPanel",
  components: {
    MarkdownRenderer,
    RawJsonViewer,
    QuizCard,
    GradeCard,
    LearningPlanCard,
  },
  props: {
    result: {
      type: Object,
      default: null,
    },
    loading: {
      type: Boolean,
      default: false,
    },
    error: {
      type: String,
      default: null,
    },
  },
  template: `
    <section class="result-panel">
      <div v-if="loading" class="empty-state">正在请求模型输出，请稍候...</div>
      <div v-else-if="error" class="error-box">{{ error }}</div>
      <div v-else-if="!result || result.kind === 'empty'" class="empty-state">
        还没有结果。请选择一个学习动作开始。
      </div>
      <template v-else>
        <div class="panel-title compact-title">
          <div>
            <h3>{{ result.title || '结果' }}</h3>
            <span>按结果类型自动适配展示方式</span>
          </div>
        </div>

        <MarkdownRenderer v-if="result.kind === 'markdown'" :content="result.markdown || ''" />
        <QuizCard v-else-if="result.kind === 'quiz'" :quiz="result.quiz" />
        <GradeCard v-else-if="result.kind === 'grade'" :grade="result.grade" />
        <LearningPlanCard v-else-if="result.kind === 'plan'" :plan="result.plan" />
        <div v-else class="result-box"><pre>{{ JSON.stringify(result.raw, null, 2) }}</pre></div>

        <div v-if="result.evidence?.length" class="evidence-block">
          <h4>Evidence 引用</h4>
          <div class="evidence-list">
            <div class="evidence-item" v-for="(item, index) in result.evidence" :key="index">
              <strong>{{ item.source || item.label || item.doc_id || ('证据 ' + (index + 1)) }}</strong>
              <p>{{ item.content || item.text || item.label || prettyEvidence(item) }}</p>
            </div>
          </div>
        </div>

        <RawJsonViewer :raw="result.raw" />
      </template>
    </section>
  `,
  methods: {
    prettyEvidence(item) {
      if (!item) return "";
      if (typeof item === "string") return item;
      return JSON.stringify(item, null, 2);
    },
  },
};
