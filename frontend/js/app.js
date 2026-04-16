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

async function sendMessage(text, options = {}) {
  const { ChatAPI } = window.DentalApp;
  const { silent = false } = options;

  State.isStreaming = true;
  setSendButtonDisabled(true);
  clsAdd("chat-empty-state", "hidden");

  // Show user bubble only for free-typed messages.
  // Clicks on inline option chips should feel like "in-place selection",
  // not like the system is typing & sending on behalf of the user.
  if (!silent) {
    appendMessage({
      sender_type: "PATIENT_USER",
      content: text,
      image_url: null,
    });
  }

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
  const side = isPatient ? "patient" : "assistant";
  const grouped = isGroupedWithPrevious(side);

  const wrapper = document.createElement("div");
  wrapper.className =
    "msg-enter chat-row flex " +
    (grouped ? "gap-1.5 " : "gap-3 ") +
    (isPatient ? "flex-row-reverse" : "flex-row");
  wrapper.dataset.role = "chat-row";
  wrapper.dataset.senderSide = side;
  if (grouped) wrapper.classList.add("chat-row--grouped");

  // Avatar
  const avatar = document.createElement("div");
  avatar.className =
    "chat-avatar flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold";
  if (isPatient) {
    avatar.className += " chat-avatar--user text-white";
    avatar.textContent = "BN";
  } else {
    avatar.className += " chat-avatar--assistant text-white";
    avatar.textContent = "SC";
  }
  if (grouped) avatar.classList.add("chat-avatar--ghost");

  // Bubble
  const bubble = document.createElement("div");
  bubble.className =
    "chat-bubble max-w-[78%] rounded-2xl px-4 py-3 text-sm leading-relaxed " +
    (isPatient
      ? "chat-bubble--user text-white rounded-tr-sm"
      : "chat-bubble--assistant text-slate-100 rounded-tl-sm");

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

  const time = document.createElement("div");
  time.className = "chat-time";
  time.textContent = formatMessageTime(msg.created_at);
  bubble.appendChild(time);

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  $("chat-messages")?.appendChild(wrapper);

  return wrapper;
}

function appendStreamingMessage() {
  const grouped = isGroupedWithPrevious("assistant");
  const wrapper = document.createElement("div");
  wrapper.className = "msg-enter chat-row flex flex-row " + (grouped ? "gap-1.5" : "gap-3");
  wrapper.dataset.role = "chat-row";
  wrapper.dataset.senderSide = "assistant";
  if (grouped) wrapper.classList.add("chat-row--grouped");

  const avatar = document.createElement("div");
  avatar.className =
    "chat-avatar chat-avatar--assistant flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white";
  avatar.textContent = "SC";
  if (grouped) avatar.classList.add("chat-avatar--ghost");

  const bubble = document.createElement("div");
  bubble.className =
    "chat-bubble chat-bubble--assistant max-w-[78%] rounded-2xl rounded-tl-sm px-4 py-3 text-sm leading-relaxed text-slate-100 stream-cursor";
  bubble.dataset.rawText = "";
  bubble.dataset.createdAt = new Date().toISOString();

  const textDiv = document.createElement("div");
  textDiv.className = "message-rich-text";
  bubble.appendChild(textDiv);

  const time = document.createElement("div");
  time.className = "chat-time";
  time.textContent = formatMessageTime(bubble.dataset.createdAt);
  bubble.appendChild(time);

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
  const t = el.querySelector(".chat-time");
  if (t) t.textContent = formatMessageTime(el.dataset.createdAt || new Date().toISOString());
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
      <span class="font-semibold text-cyan-300">Đặt lịch thành công</span>
    </div>
    <div class="text-slate-300 space-y-1">
      <p><span class="text-slate-400">Mã lịch hẹn:</span>
         <span class="font-mono text-white">#${booking.reservation_id}</span></p>
      <p><span class="text-slate-400">Thời gian:</span>
         <span class="text-white">${booking.selected_slot ?? "—"}</span></p>
    </div>
    <p class="mt-2 text-xs text-slate-500">Bạn nên đến sớm 10 phút để làm thủ tục nhanh hơn.</p>`;
  container.appendChild(card);
}

function showTypingIndicator(label = "Working…") {
  const id = "typing-" + Date.now();
  const wrapper = document.createElement("div");
  wrapper.id = id;
  wrapper.className = "flex gap-3 flex-row";

  wrapper.innerHTML = `
    <div class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center
                text-xs font-bold chat-avatar chat-avatar--assistant text-white">SC</div>
    <div class="chat-bubble chat-bubble--assistant rounded-2xl rounded-tl-sm px-4 py-3 text-sm">
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

function getLastChatRow() {
  const container = $("chat-messages");
  if (!container) return null;
  const rows = container.querySelectorAll(".chat-row[data-role='chat-row']");
  if (!rows.length) return null;
  return rows[rows.length - 1];
}

function isGroupedWithPrevious(side) {
  const last = getLastChatRow();
  if (!last) return false;
  return last.dataset.senderSide === side;
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

function formatMessageTime(createdAt) {
  if (!createdAt) return formatSessionTime(new Date().toISOString());
  const dt = new Date(createdAt);
  if (Number.isNaN(dt.getTime())) return "";
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
  if (State.isStreaming) return;
  if (!State.currentSessionId) {
    showToast("Chọn hoặc tạo phiên trước.", "warning");
    return;
  }
  const clean = String(text || "").trim();
  if (!clean) return;
  // Silent: click vào option trong bubble AI không tạo bubble "user ảo".
  sendMessage(clean, { silent: true });
}

function markChipSelected(btn, labels) {
  if (!btn) return;
  const arr = Array.isArray(labels) ? labels : [labels];
  arr.forEach((l) => {
    const txt = String(l || "").trim();
    if (!txt) return;
    const all = btn.parentElement ? btn.parentElement.querySelectorAll("button") : [];
    all.forEach((x) => {
      if (x.textContent.trim() === txt) {
        x.classList.add("slot-chip-btn--chosen");
      }
    });
  });
}

function disableUiChipsInBubble(bubbleEl, chosen) {
  if (!bubbleEl) return;
  const chosenArr = chosen == null
    ? []
    : (Array.isArray(chosen) ? chosen : [chosen]).map((s) => String(s || "").trim()).filter(Boolean);
  bubbleEl.querySelectorAll(".msg-ui-root button").forEach((b) => {
    b.disabled = true;
    b.classList.add("slot-chip-btn--disabled");
    if (chosenArr.length > 0 && chosenArr.includes(b.textContent.trim())) {
      b.classList.remove("slot-chip-btn--disabled");
      b.classList.add("slot-chip-btn--chosen");
    }
  });
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
  ok.addEventListener("click", () => {
    disableUiChipsInBubble(bubbleEl, "Đồng ý");
    sendQuickReply("Đồng ý");
  });
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "msg-confirm-btn msg-confirm-btn--cancel";
  cancel.textContent = "Hủy";
  cancel.addEventListener("click", () => {
    disableUiChipsInBubble(bubbleEl, "Hủy");
    sendQuickReply("Hủy");
  });
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
    const caseCode = (bubbleEl.dataset.confirmCategoryCode || "").trim();
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
      disableUiChipsInBubble(bubbleEl, label);
      sendQuickReply(label);
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
  const rowEl = bubbleEl.closest(".chat-row");
  const grouped = rowEl && rowEl.classList.contains("chat-row--grouped");
  const tpl = ui.template;

  // Chips/panel nên sát bubble hơn khi group liên tiếp để tránh cảm giác “lỗi layout”.
  if (tpl === "day_chips" || tpl === "datetime_chips") {
    root.className = `msg-ui-root ${grouped ? "mt-2" : "mt-2"} ${grouped ? "" : ""}`;
  } else {
    root.className = `msg-ui-root ${grouped ? "mt-2 pt-2" : "mt-3 pt-3"} ${
      tpl === "confirm_actions" ? "" : "border-t border-slate-600/60"
    }`;
  }

  if (ui.template === "category_confirm" && Array.isArray(ui.actions)) {
    const cap = document.createElement("p");
    cap.className = "text-xs text-slate-400 mb-2";
    cap.textContent = "Xác nhận nhóm khám";
    root.appendChild(cap);
    const row = document.createElement("div");
    row.className = "flex flex-wrap gap-2 msg-ui-chips";
    ui.actions
      .map((t) => String(t).trim())
      .filter(Boolean)
      .forEach((label) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "slot-chip-btn";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          disableUiChipsInBubble(bubbleEl, label);
          sendQuickReply(label);
        });
        row.appendChild(btn);
      });
    root.appendChild(row);
  } else if (ui.template === "day_chips" && Array.isArray(ui.days)) {
    const labels = ui.days.map((t) => String(t).trim()).filter(Boolean);
    if (labels.length === 0) return;
    const cap = document.createElement("p");
    cap.className = "text-xs text-slate-400 mb-2";
    cap.textContent = "Chọn 1 hoặc nhiều ngày bạn rảnh";
    root.appendChild(cap);
    const row = document.createElement("div");
    row.className = "flex flex-wrap gap-2 msg-ui-chips";
    const selected = new Set();
    labels.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot-chip-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        if (selected.has(label)) {
          selected.delete(label);
          btn.classList.remove("slot-chip-btn--primary");
        } else {
          selected.add(label);
          btn.classList.add("slot-chip-btn--primary");
        }
      });
      row.appendChild(btn);
    });
    root.appendChild(row);
    const action = document.createElement("button");
    action.type = "button";
    action.className = "slot-chip-btn slot-chip-btn--primary mt-2";
    action.textContent = "Xác nhận ngày đã chọn";
    action.addEventListener("click", () => {
      if (selected.size === 0) return;
      const chosen = Array.from(selected);
      const payload = chosen.join(", ");
      disableUiChipsInBubble(bubbleEl, chosen);
      sendQuickReply(payload);
    });
    root.appendChild(action);
  } else if (ui.template === "datetime_chips" && Array.isArray(ui.options)) {
    const labels = ui.options.map((t) => String(t).trim()).filter(Boolean);
    if (labels.length === 0) return;
    const cap = document.createElement("p");
    cap.className = "text-xs text-slate-400 mb-2";
    cap.textContent = "Chọn khung giờ phù hợp";
    root.appendChild(cap);
    const row = document.createElement("div");
    row.className = "flex flex-wrap gap-2 msg-ui-chips";
    labels.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot-chip-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        disableUiChipsInBubble(bubbleEl, label);
        sendQuickReply(label);
      });
      row.appendChild(btn);
    });
    root.appendChild(row);
  } else if (ui.template === "time_chips" && Array.isArray(ui.times)) {
    if (ui.category_code) {
      bubbleEl.dataset.confirmCategoryCode = String(ui.category_code).trim();
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
        disableUiChipsInBubble(bubbleEl, label);
        sendQuickReply(label);
      });
      row.appendChild(btn);
    });
    root.appendChild(row);
  } else if (ui.template === "confirm_actions" && ui.slot_display) {
    bubbleEl.dataset.confirmDateIso = String(ui.date_iso || "").trim();
    bubbleEl.dataset.confirmSlotDisplay = String(ui.slot_display).trim();
    if (ui.category_code) {
      bubbleEl.dataset.confirmCategoryCode = String(ui.category_code).trim();
    }
    const panel = document.createElement("div");
    panel.className = "msg-confirm-panel";
    mountConfirmActionsPanel(panel, bubbleEl);
    root.appendChild(panel);
  } else {
    return;
  }

  bubbleEl.appendChild(root);

  // Đảm bảo timestamp luôn nằm dưới cùng (sau các chips/panel UI).
  const timeEl = bubbleEl.querySelector(".chat-time");
  if (timeEl) bubbleEl.appendChild(timeEl);
}
