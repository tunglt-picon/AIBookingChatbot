/**
 * API client – thin wrapper around the FastAPI backend.
 *
 * `window.APP_CONFIG`:
 * - apiBase: URL đầy đủ tới prefix `/api/v1`, hoặc `"auto"` / bỏ qua để tự suy từ `window.location` (LAN).
 * - apiPort: cổng backend (mặc định 8000), chỉ dùng khi apiBase là auto.
 */

function resolveRuntimeApiBase() {
  const cfg = window.APP_CONFIG || {};
  const explicit = cfg.apiBase;
  if (typeof explicit === "string") {
    const t = explicit.trim();
    if (t && t !== "auto") {
      return t.replace(/\/$/, "");
    }
  }
  const portRaw = cfg.apiPort != null ? String(cfg.apiPort) : "8000";
  const port = portRaw.trim() || "8000";
  const loc = window.location;
  const host = loc.hostname;
  if (!host) {
    return `http://127.0.0.1:${port}/api/v1`;
  }
  const scheme = loc.protocol === "https:" ? "https" : "http";
  return `${scheme}://${host}:${port}/api/v1`;
}

function apiBaseToBackendOrigin(apiBase) {
  try {
    const base = apiBase.replace(/\/$/, "");
    if (base.endsWith("/api/v1")) {
      return base.slice(0, -"/api/v1".length) || new URL(base + "/").origin;
    }
    return new URL(base.endsWith("/") ? base : `${base}/`).origin;
  } catch {
    return "http://127.0.0.1:8000";
  }
}

const API_BASE = resolveRuntimeApiBase();
const BACKEND_ORIGIN = apiBaseToBackendOrigin(API_BASE);

/* ── Token storage ──────────────────────────────────────────────────────── */

const Auth = {
  getToken: ()  => localStorage.getItem("dental_token"),
  setToken: (t) => localStorage.setItem("dental_token", t),
  clearToken: () => localStorage.removeItem("dental_token"),

  getUser: () => {
    const raw = localStorage.getItem("dental_user");
    return raw ? JSON.parse(raw) : null;
  },
  setUser: (u) => localStorage.setItem("dental_user", JSON.stringify(u)),
  clearUser: () => localStorage.removeItem("dental_user"),

  isLoggedIn: () => !!localStorage.getItem("dental_token"),

  logout: () => {
    Auth.clearToken();
    Auth.clearUser();
  },
};

/* ── Generic fetch helpers ──────────────────────────────────────────────── */

async function apiFetch(path, options = {}) {
  const token = Auth.getToken();
  const headers = { ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    let detail = err.detail ?? "Request failed";
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => (typeof item === "object" && item.msg ? item.msg : String(item)))
        .join("; ");
    }
    throw new APIError(String(detail), res.status);
  }
  if (res.status === 204) return null;
  return res.json();
}

class APIError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
    this.name = "APIError";
  }
}

/* ── Auth endpoints ─────────────────────────────────────────────────────── */

const AuthAPI = {
  async register({ username, password, full_name, phone, address }) {
    const data = await apiFetch("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, full_name, phone, address }),
    });
    Auth.setToken(data.access_token);
    Auth.setUser({
      id: data.patient_user_id,
      username: data.username,
      full_name: data.full_name,
    });
    return data;
  },

  async login({ username, password }) {
    const data = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    Auth.setToken(data.access_token);
    Auth.setUser({
      id: data.patient_user_id,
      username: data.username,
      full_name: data.full_name,
    });
    return data;
  },
};

/* ── Chat endpoints ─────────────────────────────────────────────────────── */

const ChatAPI = {
  async createSession() {
    return apiFetch("/chat/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async listSessions() {
    return apiFetch("/chat/sessions");
  },

  async getSession(sessionId) {
    return apiFetch(`/chat/sessions/${sessionId}`);
  },

  async closeSession(sessionId) {
    return apiFetch(`/chat/sessions/${sessionId}/close`, { method: "POST" });
  },

  /**
   * Gửi tin nhắn văn bản và nhận SSE stream.
   *
   * @param {number}      sessionId
   * @param {string}      message
   * @param {object}      callbacks  { onToken, onStatus, onDone, onError }
   */
  async sendMessage(sessionId, message, callbacks = {}) {
    const token = Auth.getToken();
    if (!token) throw new APIError("Not authenticated", 401);

    const form = new FormData();
    form.append("message", message);
    form.append("authorization", `Bearer ${token}`);

    const response = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      body: form,
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new APIError(err.detail ?? "Send failed", response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr) continue;

        let event;
        try { event = JSON.parse(jsonStr); } catch { continue; }

        switch (event.type) {
          case "token":
            callbacks.onToken?.(event.content);
            break;
          case "status":
            callbacks.onStatus?.(event.message);
            break;
          case "done":
            callbacks.onDone?.(event);
            break;
          case "error":
            callbacks.onError?.(new Error(event.message));
            break;
        }
      }
    }
  },
};

/* ── Schedule endpoints ─────────────────────────────────────────────────── */

const ScheduleAPI = {
  /**
   * @param {string|null} date  YYYY-MM-DD
   * @param {string|null} categoryCode  CAT-01 | CAT-02 | CAT-03 | CAT-04 | CAT-05
   */
  async getSlots(date = null, categoryCode = null) {
    const q = new URLSearchParams();
    if (date) q.set("date", date);
    if (categoryCode) q.set("case", categoryCode);
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return apiFetch(`/schedule/slots${suffix}`);
  },

  async listReservations() {
    return apiFetch("/schedule/reservations");
  },

  /** Lịch mock cả tuần (file JSON). */
  async getWeekSlots(categoryCode = null, weekStartIso = null) {
    const q = new URLSearchParams();
    if (categoryCode) q.set("case", categoryCode);
    if (weekStartIso) q.set("week_start", weekStartIso);
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return apiFetch(`/schedule/week/slots${suffix}`);
  },
};

/* ── Admin lab (JWT) ───────────────────────────────────────────────────── */

const AdminLabAPI = {
  async invokeAgent(payload) {
    return apiFetch("/admin/lab/agents/invoke", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async invokeTool(payload) {
    return apiFetch("/admin/lab/tools/invoke", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  /** Tóm tắt file mock lịch (không cần đăng nhập phía BE giai đoạn dev). */
  async getMockScheduleSummary() {
    return apiFetch("/admin/lab/mock-schedule-summary");
  },
  async getSessionState(sessionId) {
    return apiFetch(`/admin/lab/sessions/${sessionId}/state`);
  },
  async listBenchmarkDatasets() {
    return apiFetch("/admin/lab/benchmarks/datasets");
  },
  async getBenchmarkDataset(name) {
    return apiFetch(`/admin/lab/benchmarks/datasets/${encodeURIComponent(name)}`);
  },
  async saveBenchmarkDataset(name, rows) {
    return apiFetch(`/admin/lab/benchmarks/datasets/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ rows }),
    });
  },
  async runBenchmark(payload = {}) {
    return apiFetch("/admin/lab/benchmarks/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};

/* ── Exports (module-style) ──────────────────────────────────────────────── */

window.DentalApp = {
  Auth,
  AuthAPI,
  ChatAPI,
  ScheduleAPI,
  AdminLabAPI,
  APIError,
  ApiConfig: { apiBase: API_BASE, backendOrigin: BACKEND_ORIGIN },
};
