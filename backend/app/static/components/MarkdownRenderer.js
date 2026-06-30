import MarkdownIt from "https://cdn.jsdelivr.net/npm/markdown-it@14/+esm";
import DOMPurify from "https://cdn.jsdelivr.net/npm/dompurify@3.1.6/+esm";
import hljs from "https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/+esm";

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      try {
        return `<pre class=\"hljs\"><code>${hljs.highlight(code, { language }).value}</code></pre>`;
      } catch {
        return `<pre class=\"hljs\"><code>${md.utils.escapeHtml(code)}</code></pre>`;
      }
    }
    return `<pre class=\"hljs\"><code>${md.utils.escapeHtml(code)}</code></pre>`;
  },
});

export const MarkdownRenderer = {
  name: "MarkdownRenderer",
  props: {
    content: {
      type: String,
      default: "",
    },
    compact: {
      type: Boolean,
      default: false,
    },
  },
  computed: {
    renderedHtml() {
      if (!this.content) return "";
      const safe = DOMPurify.sanitize(md.render(this.content));
      return safe;
    },
  },
  template: `
    <div v-if="content" class="markdown-body" :class="{ compact }" v-html="renderedHtml"></div>
    <div v-else class="empty-state compact-empty">暂无可渲染内容。</div>
  `,
};
