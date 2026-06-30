function getApiBaseUrl() {
  if (typeof process !== 'undefined' && process.env && process.env.VUE_APP_API_BASE_URL) {
    return process.env.VUE_APP_API_BASE_URL.replace(/\/$/, '')
  }

  if (
    typeof window !== 'undefined' &&
    window.location &&
    window.location.hostname === 'localhost' &&
    window.location.port === '8080'
  ) {
    return 'http://localhost:8765'
  }

  return ''
}

const API_BASE_URL = getApiBaseUrl()

async function request(path, options = {}) {
  try {
    const headers = {
      ...(options.headers || {}),
    }

    if (options.body != null && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json'
    }

    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers,
      ...options,
    })
    const text = await response.text()
    const data = text ? JSON.parse(text) : null
    if (!response.ok) {
      throw new Error(data && data.detail ? JSON.stringify(data.detail) : text || `HTTP ${response.status}`)
    }
    return data
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : String(error))
  }
}

export function getHealth() {
  return request('/health', { method: 'GET' })
}

export function ingestMaterial(payload) {
  return request('/knowledge/ingest', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function chatWithAgent(payload) {
  return request('/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function explainConcept(payload) {
  return request('/study/explain', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function generateQuiz(payload) {
  return request('/study/quiz', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getNotes(query = '') {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : ''
  return request(`/notes${suffix}`, { method: 'GET' })
}

export function getStudyState() {
  return request('/study/state', { method: 'GET' })
}

export function getStudyTopics() {
  return request('/study/topics', { method: 'GET' })
}

export function submitExam(payload) {
  return request('/study/exam/submit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
