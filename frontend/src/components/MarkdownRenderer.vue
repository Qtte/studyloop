<template>
  <div v-if="content" class="markdown-body" :class="{ compact: compact }" v-html="renderedHtml"></div>
  <div v-else class="empty-state compact-empty">暂无内容</div>
</template>

<script>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const markdown = new MarkdownIt({
  html: true,
  breaks: true,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language }).value}</code></pre>`
      } catch (error) {
        // Fall back to MarkdownIt's default escaping below.
      }
    }

    return `<pre class="hljs"><code>${markdown.utils.escapeHtml(code)}</code></pre>`
  },
})

export default {
  name: 'MarkdownRenderer',
  props: {
    content: {
      type: String,
      default: '',
    },
    compact: {
      type: Boolean,
      default: false,
    },
  },
  computed: {
    renderedHtml() {
      return DOMPurify.sanitize(markdown.render(this.content || ''))
    },
  },
}
</script>
