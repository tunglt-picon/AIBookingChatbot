/**
 * Dental AI Chat Application – Main UI Logic
 *
 * Views:
 *   #view-auth  – Login / Register
 *   #view-app   – Main application (sidebar + chat)
 */

/* ══════════════════════════════════════════════════════════════════════════
   STATE
══════════════════════════════════════════════════════════════════════════ */

const State = {
  currentSessionId: null,
  isStreaming: false,
  sessions: [],
};

/* ══════════════════════════════════════════════════════════════════════════
   DOM REFERENCES
══════════════════════════════════════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);

/**
 * Safe classList helpers.
 * NOTE: `$(id)?.classList.add("x")` is WRONG — if id is missing, `?.` yields undefined
 * and then `.add` throws. Always use these or `el?.classList?.add`.
 */
function clsAdd(id, token) {
  const el = $(id);
  if (el) el.classList.add(token);
}
function clsRemove(id, token) {
  const el = $(id);
  if (el) el.classList.remove(token);
}

/* ══════════════════════════════════════════════════════════════════════════
   BOOTSTRAP
══════════════════════════════════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", () => {
  const { Auth } = window.DentalApp;

  if (Auth.isLoggedIn()) {
    showApp();
  } else {
    showAuth();
  }

  bindAuthForms();
  bindChatControls();

  // Must run after DOM exists — top-level $("btn-new-session") was null at parse time.
  const newSessionBtn = $("btn-new-session");
  if (newSessionBtn) {
    newSessionBtn.addEventListener("click", () => {
      startNewSession();
    });
  }
});

/* ══════════════════════════════════════════════════════════════════════════
   VIEW SWITCHING
══════════════════════════════════════════════════════════════════════════ */

function showAuth() {
  clsRemove("view-auth", "hidden");
  clsAdd("view-app", "hidden");
}

function showApp() {
  const { Auth } = window.DentalApp;
  clsAdd("view-auth", "hidden");
  clsRemove("view-app", "hidden");

  const user = Auth.getUser();
  if (user) {
    const nameEl = $("user-name");
    const avEl = $("user-avatar-initials");
    if (nameEl) nameEl.textContent = user.full_name || user.username;
    if (avEl) {
      avEl.textContent = (user.full_name || user.username).charAt(0).toUpperCase();
    }
  }

  // Composer must be visible whenever the main app is shown (not only after openSession).
  clsRemove("chat-input-area", "hidden");

  loadSessions();
}

/* ══════════════════════════════════════════════════════════════════════════
   AUTH FORMS
══════════════════════════════════════════════════════════════════════════ */

function bindAuthForms() {
  const { AuthAPI } = window.DentalApp;

  // Tab switching
  $("tab-login").addEventListener("click", () => switchAuthTab("login"));
  $("tab-register").addEventListener("click", () => switchAuthTab("register"));

  // Login
  $("form-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("btn-login");
    setButtonLoading(btn, true);
    clearAuthError();
    try {
      await AuthAPI.login({
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      });
      showApp();
    } catch (err) {
      showAuthError(err.message);
    } finally {
      setButtonLoading(btn, false);
    }
  });

  // Register
  $("form-register").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("btn-register");
    setButtonLoading(btn, true);
    clearAuthError();
    try {
      await AuthAPI.register({
        username: $("reg-username").value.trim(),
        password: $("reg-password").value,
        full_name: $("reg-fullname").value.trim(),
        phone: $("reg-phone").value.trim() || null,
      });
      showApp();
    } catch (err) {
      showAuthError(err.message);
    } finally {
      setButtonLoading(btn, false);
    }
  });

  // Logout
  $("btn-logout").addEventListener("click", () => {
    window.DentalApp.Auth.logout();
    State.currentSessionId = null;
    State.sessions = [];
    clearChat();
    showAuth();
  });
}

function switchAuthTab(tab) {
  const isLogin = tab === "login";
  $("tab-login").classList.toggle("text-sky-400", isLogin);
  $("tab-login").classList.toggle("border-b-2", isLogin);
  $("tab-login").classList.toggle("border-sky-400", isLogin);
  $("tab-register").classList.toggle("text-sky-400", !isLogin);
  $("tab-register").classList.toggle("border-b-2", !isLogin);
  $("tab-register").classList.toggle("border-sky-400", !isLogin);
  $("form-login").classList.toggle("hidden", !isLogin);
  $("form-register").classList.toggle("hidden", isLogin);
}

function showAuthError(msg) {
  const el = $("auth-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearAuthError() {
  $("auth-error").classList.add("hidden");
}

/* ══════════════════════════════════════════════════════════════════════════
   SESSION MANAGEMENT
══════════════════════════════════════════════════════════════════════════ */

async function loadSessions() {
  const { ChatAPI } = window.DentalApp;
  try {
    State.sessions = await ChatAPI.listSessions();
    renderSessionList();

    if (State.sessions.length > 0) {
      openSession(State.sessions[0].id);
    } else {
      startNewSession();
    }
  } catch (err) {
    showToast("Could not load sessions: " + err.message, "error");
  }
}

function renderSessionList() {
  const container = $("session-list");
  if (!container) return;
  container.innerHTML = "";

  State.sessions.forEach((s) => {
    const div = document.createElement("div");
    div.className =
      "session-item cursor-pointer flex items-center gap-2 px-3 py-2 rounded-lg " +
      "text-slate-400 hover:bg-slate-700/50 hover:text-white transition-colors text-sm";
    div.dataset.id = s.id;

    const statusDot =
      s.status === "PROCESSING"
        ? "bg-emerald-400"
        : s.status === "COMPLETED"
        ? "bg-sky-400"
        : "bg-slate-500";

    div.innerHTML = `
      <span class="w-2 h-2 rounded-full flex-shrink-0 ${statusDot}"></span>
      <span class="truncate">${formatSessionLabel(s)}</span>
      <span class="ml-auto text-xs text-slate-500 flex-shrink-0">
        ${formatSessionTime(s.created_at)}
      </span>`;

    if (s.id === State.currentSessionId) {
      div.classList.add("bg-slate-700/70", "text-white");
    }

    div.addEventListener("click", () => openSession(s.id));
    container.appendChild(div);
  });
}

async function openSession(sessionId) {
  const { ChatAPI } = window.DentalApp;
  State.currentSessionId = sessionId;
  renderSessionList();

  clearChat();
  clsAdd("chat-empty-state", "hidden");
  clsRemove("chat-input-area", "hidden");

  try {
    const session = await ChatAPI.getSession(sessionId);
    (session.messages || []).forEach((msg) => appendMessage(msg));
    scrollToBottom();
  } catch (err) {
    showToast("Could not load session: " + err.message, "error");
  }
}

async function startNewSession() {
  const { ChatAPI } = window.DentalApp;
  try {
    const session = await ChatAPI.createSession();
    State.sessions.unshift(session);
    renderSessionList();
    State.currentSessionId = session.id;
    clearChat();
    clsRemove("chat-empty-state", "hidden");
    clsRemove("chat-input-area", "hidden");
  } catch (err) {
    showToast("Could not create a new session: " + err.message, "error");
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   CHAT CONTROLS
══════════════════════════════════════════════════════════════════════════ */

function bindChatControls() {
  const form = $("chat-form");
  const input = $("chat-input");
  if (!form || !input) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || State.isStreaming) return;
    if (!State.currentSessionId) {
      showToast("Select or create a session first.", "warning");
      return;
    }
    input.value = "";
    await sendMessage(text);
  });

  // Enter = send; Cmd+Enter (Mac) or Ctrl+Enter (Windows) = new line in textarea
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (e.isComposing || e.keyCode === 229) return;
    if (e.metaKey || e.ctrlKey) {
      return;
    }
    e.preventDefault();
    form.requestSubmit();
  });
}

async function sendMessage(text) {
  const { ChatAPI } = window.DentalApp;

  State.isStreaming = true;
  setSendButtonDisabled(true);
  clsAdd("chat-empty-state", "hidden");

  // Show user message immediately
  appendMessage({
    sender_type: "PATIENT_USER",
    content: text,
    image_url: null,
  });

  // Show typing indicator
  const typingId = showTypingIndicator();
  scrollToBottom();

  let agentMsgEl = null;
  let streamAcc = "";

  try {
    await ChatAPI.sendMessage(
      State.currentSessionId,
      text,
      {
        onStatus: (msg) => {
          updateTypingLabel(typingId, msg);
        },
        onToken: (token) => {
          streamAcc += token;
          removeTypingIndicator(typingId);
          if (!agentMsgEl) {
            agentMsgEl = appendStreamingMessage();
          }
          appendTokenToMessage(agentMsgEl, token);
          scrollToBottom();
        },
        onDone: (event) => {
          removeTypingIndicator(typingId);
          if (!agentMsgEl && (streamAcc.trim() || event.ui)) {
            agentMsgEl = appendStreamingMessage();
            agentMsgEl.dataset.rawText = streamAcc;
            const td = agentMsgEl.querySelector(".message-rich-text");
            if (td) td.innerHTML = renderRichText(streamAcc);
          }
          if (agentMsgEl) finaliseStreamingMessage(agentMsgEl);
          if (event.ui && agentMsgEl) {
            injectAssistantMessageUi(agentMsgEl, event.ui);
          }

          if (event.booking) {
            appendBookingConfirmation(event.booking);
            // Refresh session list to show COMPLETED
            loadSessions();
          }

          scrollToBottom();
        },
        onError: (err) => {
          removeTypingIndicator(typingId);
          showToast("Error: " + err.message, "error");
        },
      }
    );
  } catch (err) {
    removeTypingIndicator(typingId);
    const inp = $("chat-input");
    if (inp) inp.value = text;
    showToast("Could not send message: " + err.message, "error");
  } finally {
    State.isStreaming = false;
    setSendButtonDisabled(false);
    $("chat-input")?.focus();
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   MESSAGE RENDERING
══════════════════════════════════════════════════════════════════════════ */

function appendMessage(msg) {
  const isPatient = msg.sender_type === "PATIENT_USER";
  const isSpecialist = msg.sender_type === "SPECIALIST_AGENT";

  const wrapper = document.createElement("div");
  wrapper.className =
    "msg-enter flex gap-3 " + (isPatient ? "flex-row-reverse" : "flex-row");

  // Avatar
  const avatar = document.createElement("div");
  avatar.className =
    "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold";
  if (isPatient) {
    avatar.className += " bg-sky-600 text-white";
    avatar.textContent = "B";
  } else if (isSpecialist) {
    avatar.className += " bg-violet-600 text-white";
    avatar.textContent = "AI";
  } else {
    avatar.className += " bg-emerald-700 text-white";
    avatar.textContent = "R";
  }

  // Bubble
  const bubble = document.createElement("div");
  bubble.className =
    "max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed " +
    (isPatient
      ? "bg-sky-600 text-white rounded-tr-sm"
      : "bg-slate-700/80 text-slate-100 rounded-tl-sm");

  if (msg.image_url) {
    const img = document.createElement("img");
    img.src = msg.image_url.startsWith("blob:")
      ? msg.image_url
      : `http://localhost:8000${msg.image_url}`;
    img.className =
      "rounded-xl max-w-full mb-2 cursor-pointer hover:opacity-90 transition";
    img.style.maxHeight = "200px";
    img.addEventListener("click", () => openImageLightbox(img.src));
    bubble.appendChild(img);
  }

  const text = document.createElement("div");
  text.className = "message-rich-text";
  text.innerHTML = renderRichText(msg.content ?? "");
  bubble.appendChild(text);

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  $("chat-messages")?.appendChild(wrapper);

  return wrapper;
}

function appendStreamingMessage() {
  const wrapper = document.createElement("div");
  wrapper.className = "msg-enter flex gap-3 flex-row";

  const avatar = document.createElement("div");
  avatar.className =
    "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold bg-emerald-700 text-white";
  avatar.textContent = "AI";

  const bubble = document.createElement("div");
  bubble.className =
    "max-w-[75%] rounded-2xl rounded-tl-sm px-4 py-3 text-sm leading-relaxed bg-slate-700/80 text-slate-100 stream-cursor";
  bubble.dataset.rawText = "";

  const textDiv = document.createElement("div");
  textDiv.className = "message-rich-text";
  bubble.appendChild(textDiv);

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  $("chat-messages")?.appendChild(wrapper);
  return bubble;
}

function appendTokenToMessage(el, token) {
  const next = (el.dataset.rawText ?? "") + token;
  el.dataset.rawText = next;
  const textDiv = el.querySelector(".message-rich-text");
  if (textDiv) textDiv.innerHTML = renderRichText(next);
}

function finaliseStreamingMessage(el) {
  el.classList.remove("stream-cursor");
  if (el.dataset.rawText) {
    const textDiv = el.querySelector(".message-rich-text");
    if (textDiv) textDiv.innerHTML = renderRichText(el.dataset.rawText);
  }
}

function appendBookingConfirmation(booking) {
  const container = $("chat-messages");
  if (!container) return;
  const card = document.createElement("div");
  card.className = "msg-enter mx-auto my-2 booking-card rounded-2xl p-4 text-sm max-w-sm";
  card.innerHTML = `
    <div class="flex items-center gap-2 mb-2">
      <svg class="w-5 h-5 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <span class="font-semibold text-cyan-400">Booking confirmed</span>
    </div>
    <div class="text-slate-300 space-y-1">
      <p><span class="text-slate-400">Reference:</span>
         <span class="font-mono text-white">#${booking.reservation_id}</span></p>
      <p><span class="text-slate-400">Appointment:</span>
         <span class="text-white">${booking.selected_slot ?? "—"}</span></p>
    </div>
    <p class="mt-2 text-xs text-slate-500">Please arrive 10 minutes early. Thank you.</p>`;
  container.appendChild(card);
}

function showTypingIndicator(label = "Working…") {
  const id = "typing-" + Date.now();
  const wrapper = document.createElement("div");
  wrapper.id = id;
  wrapper.className = "flex gap-3 flex-row";

  wrapper.innerHTML = `
    <div class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center
                text-xs font-bold bg-emerald-700 text-white">AI</div>
    <div class="bg-slate-700/80 rounded-2xl rounded-tl-sm px-4 py-3 text-sm">
      <span class="typing-label text-slate-400 text-xs status-pulse mr-2">${label}</span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
    </div>`;

  $("chat-messages")?.appendChild(wrapper);
  return id;
}

function updateTypingLabel(id, label) {
  const el = document.querySelector(`#${id} .typing-label`);
  if (el) el.textContent = label;
}

function removeTypingIndicator(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function clearChat() {
  const container = $("chat-messages");
  if (!container) return;
  // #chat-empty-state lives inside this container — do not wipe it or getElementById breaks.
  [...container.children].forEach((child) => {
    if (child.id !== "chat-empty-state") {
      child.remove();
    }
  });
}

function scrollToBottom() {
  const el = $("chat-messages");
  if (el) el.scrollTop = el.scrollHeight;
}

/* ══════════════════════════════════════════════════════════════════════════
   IMAGE LIGHTBOX
══════════════════════════════════════════════════════════════════════════ */

function openImageLightbox(src) {
  const overlay = document.createElement("div");
  overlay.className =
    "fixed inset-0 z-50 bg-black/80 img-preview-overlay flex items-center justify-center p-4";
  overlay.innerHTML = `
    <img src="${src}" class="max-w-full max-h-full rounded-xl shadow-2xl" />`;
  overlay.addEventListener("click", () => overlay.remove());
  document.body.appendChild(overlay);
}

/* ══════════════════════════════════════════════════════════════════════════
   UTILITY
══════════════════════════════════════════════════════════════════════════ */

function setSendButtonDisabled(disabled) {
  const btn = $("btn-send");
  if (!btn) return;
  btn.disabled = disabled;
  btn.classList.toggle("opacity-50", disabled);
  btn.classList.toggle("cursor-not-allowed", disabled);
}

function setButtonLoading(btn, loading) {
  btn.disabled = loading;
  btn.dataset.originalText = btn.dataset.originalText ?? btn.textContent;
  btn.textContent = loading ? "Vui lòng chờ..." : btn.dataset.originalText;
}

function showToast(message, type = "info") {
  const colors = {
    info: "bg-slate-700 text-white",
    success: "bg-emerald-700 text-white",
    warning: "bg-amber-600 text-white",
    error: "bg-red-700 text-white",
  };
  const toast = document.createElement("div");
  toast.className =
    `fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-xl text-sm
     toast-enter max-w-xs ${colors[type] ?? colors.info}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.classList.replace("toast-enter", "toast-exit");
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

/** Sidebar row title: short date label (browser locale vi-VN). */
function formatSessionLabel(session) {
  const dt = new Date(session.created_at);
  const date = dt.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" });
  return `Phiên ${date}`;
}

function formatSessionTime(createdAt) {
  const dt = new Date(createdAt);
  return dt.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Inline **bold** (markdown-style) after escaping the rest. */
function formatInlineMarkdown(line) {
  if (!line) return "";
  const parts = line.split(/(\*\*[^*]+\*\*)/g);
  return parts
    .map((chunk) => {
      if (chunk.startsWith("**") && chunk.endsWith("**") && chunk.length > 4) {
        return `<strong class="font-semibold text-slate-50">${escapeHtml(chunk.slice(2, -2))}</strong>`;
      }
      return escapeHtml(chunk);
    })
    .join("");
}

function renderRichText(content) {
  const raw = String(content ?? "").trim();
  if (!raw) return "";
  const lines = raw.split(/\r?\n/);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) {
      i += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        const inner = lines[i].trim().replace(/^[-*]\s+/, "");
        items.push(`<li>${formatInlineMarkdown(inner)}</li>`);
        i += 1;
      }
      out.push(`<ul class="list-disc pl-5 space-y-1">${items.join("")}</ul>`);
      continue;
    }
    if (/^\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        const inner = lines[i].trim().replace(/^\d+[.)]\s+/, "");
        items.push(`<li>${formatInlineMarkdown(inner)}</li>`);
        i += 1;
      }
      out.push(`<ol class="list-decimal pl-5 space-y-1">${items.join("")}</ol>`);
      continue;
    }
    out.push(`<p class="mb-2 last:mb-0 leading-relaxed">${formatInlineMarkdown(line)}</p>`);
    i += 1;
  }
  return out.join("");
}

function sendQuickReply(text) {
  const form = $("chat-form");
  const input = $("chat-input");
  if (!form || !input || State.isStreaming) return;
  if (!State.currentSessionId) {
    showToast("Chọn hoặc tạo phiên trước.", "warning");
    return;
  }
  input.value = text;
  form.requestSubmit();
}

/** Khung xác nhận: ô giờ + Đồng ý / Hủy / Chọn lại (mở picker tại chỗ). */
function mountConfirmActionsPanel(panel, bubbleEl) {
  const slotDisplay = (bubbleEl.dataset.confirmSlotDisplay || "").trim();
  panel.replaceChildren();

  const lab = document.createElement("p");
  lab.className = "msg-confirm-label";
  lab.textContent = "Thời gian đã chọn";
  panel.appendChild(lab);

  const slotBox = document.createElement("div");
  slotBox.className = "msg-confirm-slot";
  slotBox.setAttribute("role", "status");
  slotBox.textContent = slotDisplay;
  panel.appendChild(slotBox);

  const hint = document.createElement("p");
  hint.className = "msg-confirm-hint";
  hint.textContent = "Bạn có muốn xác nhận đặt lịch này không?";
  panel.appendChild(hint);

  const row = document.createElement("div");
  row.className = "msg-confirm-actions";
  const ok = document.createElement("button");
  ok.type = "button";
  ok.className = "msg-confirm-btn msg-confirm-btn--ok";
  ok.textContent = "Đồng ý";
  ok.addEventListener("click", () => sendQuickReply("Đồng ý"));
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "msg-confirm-btn msg-confirm-btn--cancel";
  cancel.textContent = "Hủy";
  cancel.addEventListener("click", () => sendQuickReply("Hủy"));
  const reschedule = document.createElement("button");
  reschedule.type = "button";
  reschedule.className = "msg-confirm-btn msg-confirm-btn--reschedule";
  reschedule.textContent = "Chọn lại thời gian";
  reschedule.addEventListener("click", (e) => {
    e.preventDefault();
    beginInlineReschedule(bubbleEl, panel);
  });
  row.appendChild(ok);
  row.appendChild(cancel);
  row.appendChild(reschedule);
  panel.appendChild(row);
}

/** Tải giờ trống qua API, hiển thị chip trong cùng khung (không gửi tin nhắn). */
async function beginInlineReschedule(bubbleEl, panel) {
  const dateIso = (bubbleEl.dataset.confirmDateIso || "").trim();
  if (!dateIso) {
    sendQuickReply("Tôi muốn chọn lại thời gian");
    return;
  }
  if (State.isStreaming) return;

  panel.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "text-xs text-slate-400";
  loading.textContent = "Đang tải giờ trống…";
  panel.appendChild(loading);

  try {
    const { ScheduleAPI } = window.DentalApp;
    const caseCode = (bubbleEl.dataset.confirmDentalCase || "").trim();
    const data = await ScheduleAPI.getSlots(dateIso, caseCode || null);
    const slots = data?.slots || [];
    if (slots.length === 0) {
      showToast("Không có giờ trống cho ngày này.", "warning");
      mountConfirmActionsPanel(panel, bubbleEl);
      return;
    }
    mountInlineTimePickerPanel(panel, bubbleEl, data);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    showToast("Không tải được lịch: " + msg, "error");
    mountConfirmActionsPanel(panel, bubbleEl);
  }
}

/** Tách "Thứ 6, 03/04 – 08:00" (en-dash hoặc hyphen) → { datePart, timePart }. */
function splitSlotDisplay(display) {
  const s = String(display || "").trim();
  const en = "\u2013";
  let idx = s.lastIndexOf(en);
  if (idx < 0) idx = s.lastIndexOf("-");
  if (idx < 0) return { datePart: "", timePart: s };
  return {
    datePart: s.slice(0, idx).trim(),
    timePart: s.slice(idx + 1).trim(),
  };
}

function mountInlineTimePickerPanel(panel, bubbleEl, apiResponse) {
  panel.replaceChildren();
  const slots = apiResponse?.slots || [];
  const first = slots[0]?.display;
  const dateDisp =
    (first && splitSlotDisplay(first).datePart) || apiResponse?.date || "";

  const cap = document.createElement("p");
  cap.className = "text-xs text-slate-400 mb-2";
  cap.textContent = dateDisp ? `Giờ còn trống — ${dateDisp}` : "Giờ còn trống";
  panel.appendChild(cap);

  const row = document.createElement("div");
  row.className = "flex flex-wrap gap-2 msg-ui-chips mb-2";
  slots.forEach((slot) => {
    const raw = slot.time_hm || splitSlotDisplay(slot.display).timePart;
    const label = String(raw || "")
      .split("(")[0]
      .trim();
    if (!label) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "slot-chip-btn";
    btn.textContent = label;
    btn.addEventListener("click", () => {
      sendQuickReply(`Tôi muốn khám lúc ${label}`);
    });
    row.appendChild(btn);
  });
  panel.appendChild(row);

  const back = document.createElement("button");
  back.type = "button";
  back.className = "msg-confirm-linkback";
  back.textContent = "← Quay lại xác nhận";
  back.addEventListener("click", () => mountConfirmActionsPanel(panel, bubbleEl));
  panel.appendChild(back);
}

/** Structured UI under assistant bubble (time chips, confirm buttons). */
function injectAssistantMessageUi(bubbleEl, ui) {
  if (!bubbleEl || !ui || !ui.template) return;
  bubbleEl.querySelectorAll(".msg-ui-root").forEach((n) => n.remove());

  const root = document.createElement("div");
  root.className = "msg-ui-root mt-3 pt-3 border-t border-slate-600/60";

  if (ui.template === "time_chips" && Array.isArray(ui.times)) {
    if (ui.dental_case_code) {
      bubbleEl.dataset.confirmDentalCase = String(ui.dental_case_code).trim();
    }
    const labels = ui.times.map((t) => String(t).trim()).filter(Boolean);
    if (labels.length === 0) {
      return;
    }
    const cap = document.createElement("p");
    cap.className = "text-xs text-slate-400 mb-2";
    cap.textContent = ui.date_label
      ? `Giờ còn trống — ${ui.date_label}`
      : "Giờ còn trống";
    root.appendChild(cap);
    const row = document.createElement("div");
    row.className = "flex flex-wrap gap-2 msg-ui-chips";
    labels.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot-chip-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        sendQuickReply(`Tôi muốn khám lúc ${label}`);
      });
      row.appendChild(btn);
    });
    root.appendChild(row);
  } else if (ui.template === "confirm_actions" && ui.slot_display) {
    bubbleEl.dataset.confirmDateIso = String(ui.date_iso || "").trim();
    bubbleEl.dataset.confirmSlotDisplay = String(ui.slot_display).trim();
    if (ui.dental_case_code) {
      bubbleEl.dataset.confirmDentalCase = String(ui.dental_case_code).trim();
    }
    const panel = document.createElement("div");
    panel.className = "msg-confirm-panel";
    mountConfirmActionsPanel(panel, bubbleEl);
    root.appendChild(panel);
  } else {
    return;
  }

  bubbleEl.appendChild(root);
}
