const DEFAULT_API_BASE = "http://localhost:8000";

function resolveBaseUrl() {
  if (typeof window !== "undefined") {
    const configured = window.__STUDYLOOP_API_BASE_URL__;
    if (configured) return configured.replace(/\/$/, "");
    if (window.location?.origin?.startsWith("http")) return window.location.origin;
  }
  return DEFAULT_API_BASE;
}

const API_BASE_URL = resolveBaseUrl();

async function request(path, options = {}) {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });

    const text = await response.text();
    const data = text ? JSON.parse(text) : null;

    if (!response.ok) {
      throw new Error(data?.detail ? JSON.stringify(data.detail) : text || `HTTP ${response.status}`);
    }

    return data;
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : String(error));
  }
}

export function getHealth() {
  return request("/health", { method: "GET" });
}

export function ingestMaterial(payload) {
  return request("/materials/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function explainConcept(payload) {
  return request("/study/explain", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function generateQuiz(payload) {
  return request("/study/quiz", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function gradeAnswer(payload) {
  return request("/study/grade", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getNotes(query = "") {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
  return request(`/notes${suffix}`, { method: "GET" });
}

export function getStudyState() {
  return request("/study/state", { method: "GET" });
}

export { API_BASE_URL };
