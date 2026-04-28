/**
 * Phòng lab Admin — gọi từng node LangGraph / tool / REST (dev).
 */

const $ = (id) => document.getElementById(id);

const LAB_SLOT_MOCK = {
  datetime_str: "2026-04-27T09:00:00+00:00",
  display: "Thứ 2, 27/04 – 09:00 (30p · Trám răng / Phục hồi thẩm mỹ)",
  time_hm: "09:00",
  duration_minutes: 30,
  category_code: "CAT-01",
};

const LAB_PERSONA_LINE =
  "Bệnh nhân mẫu: chị Lan — đau răng hàm dưới phải vài ngày, nhói khi uống lạnh, muốn đặt lịch (mock tuần 27/04–01/05/2026).";

const BOOKING_FLOW_STEPS = [
  "Tin nhắn BN → classify_intent: consultation | select_slot | confirm_appointment | general.",
  "consultation → dental_specialist: rubric (triage_symptom_rubric_vi.json) → JSON category_code + symptoms…",
  "specialist chốt → save_intake (PostgreSQL) → query_slots (get_mock_schedule) → root_respond + chip giờ.",
  "BN chọn giờ / đồng ý → confirm_booking → Reservation trong DB.",
];

const AGENTS = [
  {
    id: "classify_intent",
    title: "classify_intent",
    subtitle: "Root Orchestrator – phân luồng ý định",
    roleBlurb:
      "Chạy đầu mỗi lượt user. Dựa vào tin nhắn + state để quyết định nhánh. Lab: giả lập state qua state_patch.",
    inputDoc: [
      "message: câu BN vừa gửi.",
      "state_patch.messages: mảng { role, content } để dựng hội thoại.",
      "state_patch: triage_complete, intake_id, available_slots, pending_confirmation_slot…",
    ],
    outputDoc: [
      "updates.intent: consultation | select_slot | confirm_appointment | general.",
      "updates.current_agent, skip_root_respond.",
    ],
    defaultMessage:
      "Chào bạn, mình bị đau răng hàm dưới bên phải khoảng 3 ngày nay, nhói khi uống lạnh.",
    defaultStatePatch: "{}",
    scenarioExamples: [
      {
        label: "Mở đầu — chưa triage, mô tả triệu chứng",
        hint: "Kỳ vọng: consultation.",
        message: "Mình bị đau răng hàm dưới phải khoảng 3 ngày nay, nhói khi uống lạnh.",
        statePatch: "{}",
      },
      {
        label: "Đã triage + slot — BN nêu giờ",
        hint: "Kỳ vọng: select_slot.",
        message: "Em muốn khám lúc 09:00 ạ.",
        statePatch: JSON.stringify(
          {
            triage_complete: true,
            intake_id: 1,
            category_code: "CAT-01",
            available_slots: [LAB_SLOT_MOCK],
            symptoms_summary: "Đau răng hàm dưới phải, nhói khi lạnh",
            follow_up_count: 2,
          },
          null, 2
        ),
      },
      {
        label: "Chờ xác nhận — BN đồng ý",
        hint: "Kỳ vọng: confirm_appointment (shortcut).",
        message: "Đồng ý, em xác nhận.",
        statePatch: JSON.stringify(
          {
            triage_complete: true,
            intake_id: 1,
            pending_confirmation_slot: {
              datetime_str: LAB_SLOT_MOCK.datetime_str,
              display: LAB_SLOT_MOCK.display,
            },
            category_code: "CAT-01",
          },
          null, 2
        ),
      },
      {
        label: "FAQ / không liên quan",
        hint: "Kỳ vọng: general.",
        message: "Phòng khám có làm việc thứ 7 không?",
        statePatch: JSON.stringify({ triage_complete: false }, null, 2),
      },
    ],
  },
  {
    id: "dental_specialist",
    title: "dental_specialist",
    subtitle: "Chuyên gia tiếp nhận — chat văn bản, chốt JSON + category_code",
    roleBlurb:
      "Chạy khi consultation. Hỏi triệu chứng, chốt category_code (CAT-01→05). follow_up_count tăng mỗi lượt.",
    inputDoc: [
      "message: câu BN (triệu chứng hoặc trả lời câu hỏi).",
      "state_patch.follow_up_count: lượt đã hỏi (0 = đầu).",
      "state_patch.symptoms_summary: tóm tắt nếu đang giữa chừng.",
    ],
    outputDoc: [
      "messages: AIMessage (lời thoại cho BN).",
      "Khi chốt (có ```json): symptoms_summary, ai_diagnosis, category_code → specialist_concluded=true.",
    ],
    defaultMessage:
      "Em bị đau răng hàm dưới phải khoảng 3 ngày, nhói khi uống lạnh.",
    defaultStatePatch: JSON.stringify({ follow_up_count: 0 }, null, 2),
    scenarioExamples: [
      {
        label: "Lượt 1 — mô tả triệu chứng lần đầu",
        hint: "LLM thường hỏi thêm 1 câu.",
        message: "Em bị đau răng hàm dưới phải 3 ngày, nhói khi uống lạnh.",
        statePatch: JSON.stringify({ follow_up_count: 0 }, null, 2),
      },
      {
        label: "Lượt 2 — trả lời câu hỏi",
        hint: "Có thể chốt JSON hoặc hỏi tiếp.",
        message: "Không sưng má, không sốt. Chỉ nhói khi lạnh/ngọt.",
        statePatch: JSON.stringify(
          { follow_up_count: 1, symptoms_summary: "Đau răng hàm dưới phải, nhói khi lạnh/ngọt" },
          null, 2
        ),
      },
      {
        label: "BN nói hết rồi — chốt sớm",
        hint: "Node rule-based chốt không cần JSON LLM.",
        message: "Em chỉ có vậy thôi, không còn gì thêm.",
        statePatch: JSON.stringify(
          {
            follow_up_count: 1,
            symptoms_summary: "Đau răng hàm dưới phải, nhói khi lạnh",
            messages: [
              { role: "human", content: "Em bị đau răng hàm dưới phải." },
              { role: "ai", content: "Bạn mô tả thêm mức độ đau?" },
            ],
          },
          null, 2
        ),
      },
    ],
  },
  {
    id: "root_respond",
    title: "root_respond",
    subtitle: "Root — soạn câu trả lời (FAQ, giờ trống, xác nhận…)",
    roleBlurb:
      "Chạy sau classify hoặc sau booking node khi cần câu trả lời tổng hợp. Lab: dựng available_slots trong state_patch.",
    inputDoc: [
      "state_patch.intent: general | consultation | …",
      "state_patch.available_slots: mảng slot.",
      "state_patch.category_code: nhãn category.",
    ],
    outputDoc: [
      "messages: AIMessage cho BN.",
      "extra.message_ui: time_chips | confirm_actions.",
    ],
    defaultMessage: "Bạn cho mình xem giờ còn trống phù hợp nhé.",
    defaultStatePatch: JSON.stringify(
      {
        intent: "consultation",
        category_code: "CAT-01",
        triage_complete: true,
        available_slots: [LAB_SLOT_MOCK],
      },
      null, 2
    ),
    scenarioExamples: [
      {
        label: "Sau khi có slot — gợi ý chọn giờ",
        hint: "LLM tóm tắt giờ trống.",
        message: "Bạn cho mình xem giờ còn trống nhé.",
        statePatch: JSON.stringify(
          {
            intent: "consultation",
            category_code: "CAT-01",
            triage_complete: true,
            available_slots: [LAB_SLOT_MOCK],
          },
          null, 2
        ),
      },
      {
        label: "FAQ — không có slot",
        hint: "intent general, slots [].",
        message: "Phòng khám có làm việc thứ 7 không?",
        statePatch: JSON.stringify({ intent: "general", available_slots: [] }, null, 2),
      },
    ],
  },
];

const TOOLS = [
  {
    id: "get_mock_schedule",
    title: "get_mock_schedule",
    subtitle: "Đọc file lich_trong_tuan_trong_vi.json",
    inputDoc: [
      'scope: "day" | "week".',
      '{ "scope": "day", "date_str": "2026-04-27", "category_code": "CAT-01" }',
      '{ "scope": "week", "category_code": "CAT-05" }',
    ],
    outputDoc: [
      "day: scope, ok, date, category_code, slots[].",
      "week: scope, ok, meta, ngay[].",
    ],
    defaultArgs: '{\n  "scope": "day",\n  "date_str": "2026-04-27",\n  "category_code": "CAT-01"\n}',
  },
  {
    id: "book_appointment",
    title: "book_appointment",
    subtitle: "Tool – ghi Reservation (dev: không JWT)",
    inputDoc: ['{ "patient_user_id": 1, "intake_id": 1, "datetime_str": "2026-04-27T09:00:00+00:00" }'],
    outputDoc: ["reservation_id, datetime_str, display, status hoặc error."],
    defaultArgs: '{\n  "patient_user_id": 1,\n  "intake_id": 1,\n  "datetime_str": "2026-04-27T09:00:00+00:00"\n}',
  },
  {
    id: "save_consult_intake",
    title: "save_consult_intake",
    subtitle: "Tool – lưu BookingConsultIntake",
    inputDoc: ["patient_user_id, session_id, symptoms, ai_diagnosis, category_code."],
    outputDoc: ["intake_id, symptoms."],
    defaultArgs: '{\n  "patient_user_id": 1,\n  "session_id": 1,\n  "symptoms": "Đau răng hàm dưới",\n  "ai_diagnosis": "Ghi chú tiếp nhận.",\n  "category_code": "CAT-01"\n}',
  },
  {
    id: "resolve_requested_slot",
    title: "resolve_requested_slot",
    subtitle: "Hàm Python – khớp giờ xin với lưới slot",
    inputDoc: ['{ "date_iso": "2026-04-27", "hour": 9, "minute": 0, "category_code": "CAT-01" }'],
    outputDoc: ["kind: exact_available | suggest | closed; slot hoặc alternatives."],
    defaultArgs: '{\n  "date_iso": "2026-04-27",\n  "hour": 9,\n  "minute": 0,\n  "category_code": "CAT-01"\n}',
  }
];

const REST = [
  {
    id: "rest_slots",
    title: "GET /schedule/slots",
    subtitle: "Dữ liệu file mock (không JWT)",
    inputDoc: ["Query: date (YYYY-MM-DD), case (CAT-01→05)."],
    outputDoc: ["SlotsResponse: date, category_code, slots[]."],
    fields: [
      { name: "date", type: "text", placeholder: "2026-04-27", label: "date" },
      { name: "case", type: "text", placeholder: "CAT-01", label: "case" },
    ],
  },
  {
    id: "rest_week",
    title: "GET /schedule/week/slots",
    subtitle: "File mock cả tuần (không JWT)",
    inputDoc: ["Query: case, week_start (YYYY-MM-DD)."],
    outputDoc: ["Payload build_week_availability_payload."],
    fields: [
      { name: "case", type: "text", placeholder: "CAT-01 hoặc để trống", label: "case" },
      { name: "week_start", type: "text", placeholder: "2026-04-27", label: "week_start" },
    ],
  },
  {
    id: "rest_reservations",
    title: "GET /schedule/reservations",
    subtitle: "Cần header Authorization (JWT)",
    inputDoc: ["Bearer token trong header."],
    outputDoc: ["Mảng ReservationResponse."],
    fields: [],
  },
];

const BENCHMARKS = [
  {
    id: "benchmark_lab",
    title: "Benchmark (UI)",
    subtitle: "Dataset chỉnh tay + chạy benchmark",
    inputDoc: [
      "Chọn dataset JSONL, chỉnh trực tiếp từng dòng (JSON object).",
      "Bấm Lưu dataset để ghi file backend/evals/datasets/*.jsonl.",
      "Bấm Chạy benchmark để lấy accuracy + p50/p95 ngay trên UI.",
    ],
    outputDoc: [
      "intent_routing_accuracy.accuracy",
      "triage_quality_accuracy.accuracy",
      "latency_ms.avg/p50/p95 + details từng case",
    ],
  },
];

let selected = { kind: "agent", id: "classify_intent" };

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function renderDocLines(container, lines) {
  container.replaceChildren();
  const ul = document.createElement("ul");
  ul.className = "list-disc space-y-2.5 pl-5 text-sm text-slate-300 leading-relaxed marker:text-slate-500";
  lines.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    ul.appendChild(li);
  });
  container.appendChild(ul);
}

function labFlash(msg) {
  const t = document.createElement("div");
  t.className =
    "fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-xl bg-slate-800 text-slate-100 text-sm shadow-xl border border-slate-600 max-w-sm text-center";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2200);
}

function applyAgentExample(message, statePatch) {
  const m = $("message");
  const sp = $("state_patch");
  if (m) m.value = message;
  if (sp) sp.value = statePatch;
  labFlash("Đã điền message + state_patch — kiểm tra và bấm Chạy.");
}

function renderAgentExtras(spec) {
  const root = $("panel-agent-extras");
  if (!root || selected.kind !== "agent") {
    if (root) root.replaceChildren();
    return;
  }
  root.replaceChildren();

  const nav = el("div", "rounded-xl border border-violet-500/25 bg-violet-950/20 p-4");
  nav.appendChild(el("p", "text-sm font-semibold text-violet-200 mb-2", "Luồng đặt lịch"));
  const navOl = document.createElement("ol");
  navOl.className = "list-decimal pl-5 space-y-1.5 text-xs text-slate-400 leading-relaxed";
  BOOKING_FLOW_STEPS.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s;
    navOl.appendChild(li);
  });
  nav.appendChild(navOl);
  nav.appendChild(el("p", "text-xs text-slate-500 mt-3 pt-3 border-t border-violet-500/20 leading-relaxed", LAB_PERSONA_LINE));
  root.appendChild(nav);

  if (spec.roleBlurb) {
    const blurb = el("div", "rounded-xl border border-slate-600/40 bg-slate-900/35 p-4");
    blurb.appendChild(el("p", "text-sm font-medium text-slate-200 mb-2", "Vai trò node này"));
    blurb.appendChild(el("p", "text-sm text-slate-400 leading-relaxed", spec.roleBlurb));
    root.appendChild(blurb);
  }

  if (!spec.scenarioExamples?.length) return;

  root.appendChild(el("p", "text-sm font-semibold text-sky-300/90 mt-1", "Ví dụ input (bấm «Điền mẫu này» rồi Chạy)"));

  spec.scenarioExamples.forEach((ex, i) => {
    const card = el("div", "rounded-xl border border-slate-700/60 bg-slate-900/40 p-4 space-y-2");
    const head = el("div", "flex flex-wrap items-start justify-between gap-2");
    head.appendChild(el("span", "text-sm font-medium text-white pr-2", `${i + 1}. ${ex.label}`));
    const btn = el("button", "shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium bg-sky-600/85 hover:bg-sky-500 text-white", "Điền mẫu này");
    btn.type = "button";
    btn.addEventListener("click", () => applyAgentExample(ex.message, ex.statePatch));
    head.appendChild(btn);
    card.appendChild(head);
    if (ex.hint) card.appendChild(el("p", "text-xs text-amber-200/75 leading-relaxed", ex.hint));
    const pre = el("pre", "text-[11px] font-mono text-slate-400 bg-slate-950/70 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed");
    pre.textContent = `message:\n${ex.message}\n\nstate_patch:\n${ex.statePatch}`;
    card.appendChild(pre);
    root.appendChild(card);
  });
}

function findSpec() {
  if (selected.kind === "agent") return AGENTS.find((a) => a.id === selected.id);
  if (selected.kind === "tool") return TOOLS.find((t) => t.id === selected.id);
  if (selected.kind === "benchmark") return BENCHMARKS.find((b) => b.id === selected.id);
  return REST.find((r) => r.id === selected.id);
}

function renderNav() {
  const na = $("nav-agents");
  const nt = $("nav-tools");
  const nr = $("nav-rest");
  const nb = $("nav-tools");
  na.replaceChildren();
  nt.replaceChildren();
  nr.replaceChildren();

  const btnCls = (active) =>
    "w-full text-left px-2.5 py-1.5 rounded-lg text-xs transition-colors break-words " +
    (active
      ? "bg-sky-600/25 text-sky-300 border border-sky-500/30"
      : "text-slate-400 hover:bg-slate-700/50 hover:text-white border border-transparent");

  AGENTS.forEach((a) => {
    const b = el("button", btnCls(selected.kind === "agent" && selected.id === a.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${a.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${a.subtitle}</span>`;
    b.addEventListener("click", () => { selected = { kind: "agent", id: a.id }; renderNav(); renderPanel(); });
    na.appendChild(b);
  });

  TOOLS.forEach((t) => {
    const b = el("button", btnCls(selected.kind === "tool" && selected.id === t.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${t.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${t.subtitle}</span>`;
    b.addEventListener("click", () => { selected = { kind: "tool", id: t.id }; renderNav(); renderPanel(); });
    nt.appendChild(b);
  });

  REST.forEach((r) => {
    const b = el("button", btnCls(selected.kind === "rest" && selected.id === r.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${r.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${r.subtitle}</span>`;
    b.addEventListener("click", () => { selected = { kind: "rest", id: r.id }; renderNav(); renderPanel(); });
    nr.appendChild(b);
  });

  BENCHMARKS.forEach((bmk) => {
    const b = el("button", btnCls(selected.kind === "benchmark" && selected.id === bmk.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${bmk.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${bmk.subtitle}</span>`;
    b.addEventListener("click", () => { selected = { kind: "benchmark", id: bmk.id }; renderNav(); renderPanel(); });
    nb.appendChild(b);
  });
}

function renderPanel() {
  const spec = findSpec();
  if (!spec) return;

  $("panel-title").textContent = spec.title;
  $("panel-desc").textContent = spec.subtitle;
  renderDocLines($("panel-input-doc"), spec.inputDoc);
  renderDocLines($("panel-output-doc"), spec.outputDoc);

  const extras = $("panel-agent-extras");
  if (extras) {
    if (selected.kind === "agent") {
      extras.classList.remove("hidden");
      renderAgentExtras(spec);
    } else {
      extras.classList.add("hidden");
      extras.replaceChildren();
    }
  }

  const form = $("panel-form");
  form.replaceChildren();

  if (selected.kind === "agent") {
    form.appendChild(labelInput("message", "Tin nhắn", "textarea", spec.defaultMessage));
    form.appendChild(labelInput("session_id", "session_id", "number", "1"));
    form.appendChild(labelInput("patient_user_id", "patient_user_id", "number", "1"));
    form.appendChild(labelInput("state_patch", "state_patch (JSON)", "textarea", spec.defaultStatePatch || "{}"));
  } else if (selected.kind === "tool") {
    form.appendChild(labelInput("tool_args", "args (JSON)", "textarea", spec.defaultArgs || "{}"));
  } else if (selected.kind === "benchmark") {
    form.appendChild(labelInput("benchmark_dataset", "Dataset", "text", "intent_routing.jsonl"));
    form.appendChild(labelInput("benchmark_rows", "Rows (JSON array)", "textarea", "[]"));
    const actions = el("div", "flex flex-wrap gap-2 pt-2");
    const btnLoad = el("button", "px-3 py-2 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 text-slate-100", "Tải dataset");
    btnLoad.type = "button";
    btnLoad.addEventListener("click", async () => {
      try {
        await loadBenchmarkDatasetToForm();
      } catch (e) {
        setResult({ error: e instanceof Error ? e.message : String(e) }, "lỗi");
      }
    });
    const btnSave = el("button", "px-3 py-2 rounded-lg text-xs font-medium bg-emerald-700 hover:bg-emerald-600 text-white", "Lưu dataset");
    btnSave.type = "button";
    btnSave.addEventListener("click", async () => {
      try {
        await saveBenchmarkDatasetFromForm();
      } catch (e) {
        setResult({ error: e instanceof Error ? e.message : String(e) }, "lỗi");
      }
    });
    const btnList = el("button", "px-3 py-2 rounded-lg text-xs font-medium bg-indigo-700 hover:bg-indigo-600 text-white", "Danh sách dataset");
    btnList.type = "button";
    btnList.addEventListener("click", async () => {
      try {
        await listBenchmarkDatasetsToResult();
      } catch (e) {
        setResult({ error: e instanceof Error ? e.message : String(e) }, "lỗi");
      }
    });
    actions.appendChild(btnLoad);
    actions.appendChild(btnSave);
    actions.appendChild(btnList);
    form.appendChild(actions);
  } else {
    spec.fields.forEach((f) => {
      form.appendChild(labelInput(`rest_${f.name}`, f.label, f.type, "", f.placeholder));
    });
  }
}

function labelInput(id, label, type, value, placeholder) {
  const wrap = el("div", "space-y-1.5");
  const lab = el("label", "block text-sm font-medium text-slate-300", label);
  lab.setAttribute("for", id);
  let input;
  if (type === "textarea") {
    input = el("textarea", "w-full min-h-[120px] resize-y bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-3 text-sm text-slate-100 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50");
    input.rows = 5;
    // JSON inputs nên cao hơn để giảm phải scroll khi test nhiều lượt.
    if (id === "state_patch" || id === "tool_args") {
      input.className = input.className.replace("min-h-[120px]", "min-h-[360px]");
      input.rows = 16;
    }
    // Benchmark rows thường dài; cho cao hơn để dễ quan sát toàn bộ JSON array.
    if (id === "benchmark_rows") {
      input.className = input.className.replace("min-h-[120px]", "min-h-[520px]");
      input.rows = 24;
    }
  } else if (type === "number") {
    input = el("input", "w-full max-w-[12rem] bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-2.5 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50");
    input.type = "number";
    input.min = "1";
  } else {
    input = el("input", "w-full bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-2.5 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50");
    input.type = type || "text";
  }
  input.id = id;
  if (placeholder) input.placeholder = placeholder;
  if (value !== undefined && value !== null) input.value = value;
  wrap.appendChild(lab);
  wrap.appendChild(input);
  return wrap;
}

function getFormValues() {
  const spec = findSpec();
  if (selected.kind === "agent") {
    const msg = $("message")?.value ?? "";
    const sid = parseInt($("session_id")?.value || "1", 10) || 1;
    const pid = parseInt($("patient_user_id")?.value || "1", 10) || 1;
    let patch = {};
    const raw = ($("state_patch")?.value || "").trim();
    if (raw) {
      try { patch = JSON.parse(raw); } catch (e) { throw new Error("state_patch JSON: " + e.message); }
    }
    return { type: "agent", body: { agent: spec.id, message: msg, session_id: sid, patient_user_id: pid, state_patch: patch } };
  }
  if (selected.kind === "tool") {
    let args = {};
    const raw = ($("tool_args")?.value || "").trim();
    if (raw) {
      try { args = JSON.parse(raw); } catch (e) { throw new Error("args JSON: " + e.message); }
    }
    return { type: "tool", body: { tool: spec.id, args } };
  }
  if (selected.kind === "benchmark") {
    const ds = ($("benchmark_dataset")?.value || "").trim() || "intent_routing.jsonl";
    let rows = [];
    const raw = ($("benchmark_rows")?.value || "").trim();
    if (raw) {
      try { rows = JSON.parse(raw); } catch (e) { throw new Error("benchmark_rows JSON: " + e.message); }
    }
    if (!Array.isArray(rows)) throw new Error("benchmark_rows phải là mảng JSON.");
    return { type: "benchmark", body: { dataset: ds, rows } };
  }
  return { type: "rest", spec };
}

async function loadBenchmarkDatasetToForm() {
  const name = ($("benchmark_dataset")?.value || "").trim();
  if (!name) throw new Error("Vui lòng nhập tên dataset.");
  const data = await window.DentalApp.AdminLabAPI.getBenchmarkDataset(name);
  $("benchmark_rows").value = JSON.stringify(data.rows || [], null, 2);
  labFlash(`Đã tải dataset ${data.dataset}`);
}

async function saveBenchmarkDatasetFromForm() {
  const name = ($("benchmark_dataset")?.value || "").trim();
  if (!name) throw new Error("Vui lòng nhập tên dataset.");
  let rows = [];
  try {
    rows = JSON.parse($("benchmark_rows")?.value || "[]");
  } catch (e) {
    throw new Error("Rows JSON không hợp lệ: " + e.message);
  }
  if (!Array.isArray(rows)) throw new Error("Rows phải là mảng JSON.");
  const data = await window.DentalApp.AdminLabAPI.saveBenchmarkDataset(name, rows);
  setResult(data, "saved");
  labFlash(`Đã lưu ${data.saved_rows} dòng vào ${data.dataset}`);
}

async function listBenchmarkDatasetsToResult() {
  const data = await window.DentalApp.AdminLabAPI.listBenchmarkDatasets();
  setResult(data, "datasets");
}

function setResult(obj, meta) {
  $("result-json").textContent = JSON.stringify(obj, null, 2);
  $("result-meta").textContent = meta || "";
  renderBenchmarkVisual(obj);
}

function _fmtPct(v) {
  const n = Number(v || 0);
  return `${(n * 100).toFixed(1)}%`;
}

function _pctToHue(value) {
  const n = Math.max(0, Math.min(1, Number(value || 0)));
  // 0 -> red(0), 1 -> green(120)
  return Math.round(120 * n);
}

function _scoreBar(rate) {
  const n = Math.max(0, Math.min(1, Number(rate || 0)));
  const hue = _pctToHue(n);
  const wrap = el("div", "mt-2 h-2 rounded-full bg-slate-800 overflow-hidden");
  const fill = el("div", "h-full rounded-full");
  fill.style.width = `${(n * 100).toFixed(1)}%`;
  fill.style.backgroundColor = `hsl(${hue} 75% 45%)`;
  wrap.appendChild(fill);
  return wrap;
}

function benchmarksForDataset(datasetName) {
  const raw = String(datasetName || "").trim().toLowerCase().replace(/\.jsonl$/, "");
  if (raw === "intent_routing") return ["intent_routing_accuracy"];
  if (raw === "triage_quality") return ["triage_quality_accuracy"];
  return ["intent_routing_accuracy", "triage_quality_accuracy"];
}

function renderBenchmarkVisual(obj) {
  const root = $("benchmark-visual");
  if (!root) return;
  root.replaceChildren();
  const bm = obj?.benchmarks;
  if (!bm || typeof bm !== "object") {
    root.classList.add("hidden");
    return;
  }
  root.classList.remove("hidden");

  const cards = el("div", "grid grid-cols-1 md:grid-cols-2 gap-2");
  const cardSpec = [
    { key: "intent_routing_accuracy", label: "Intent accuracy", rate: "accuracy" },
    { key: "triage_quality_accuracy", label: "Triage accuracy", rate: "accuracy" },
  ];
  const activeCards = cardSpec.filter((spec) => Object.prototype.hasOwnProperty.call(bm, spec.key));
  activeCards.forEach((spec) => {
    const data = bm[spec.key] || {};
    const lat = data.latency_ms || {};
    const rateVal = Number(data[spec.rate] || 0);
    const hue = _pctToHue(rateVal);
    const wrap = el("div", "rounded-lg border border-slate-700 bg-slate-900/50 p-3");
    wrap.appendChild(el("p", "text-xs text-slate-400", spec.label));
    const score = el("p", "text-lg font-semibold", _fmtPct(rateVal));
    score.style.color = `hsl(${hue} 75% 62%)`;
    wrap.appendChild(score);
    wrap.appendChild(el("p", "text-[11px] text-slate-500", `p50 ${lat.p50 ?? 0}ms · p95 ${lat.p95 ?? 0}ms`));
    wrap.appendChild(_scoreBar(rateVal));
    cards.appendChild(wrap);
  });
  if (activeCards.length) root.appendChild(cards);

  const tableWrap = el("div", "rounded-lg border border-slate-700 overflow-x-auto");
  const table = el("table", "w-full text-xs text-left border-collapse min-w-[680px]");
  const thead = el("thead", "bg-slate-800 text-slate-300");
  const hrow = el("tr");
  ["Benchmark", "Case", "Expected", "Predicted", "Latency(ms)", "Kết quả"].forEach((h) => {
    hrow.appendChild(el("th", "px-2 py-2 border-b border-slate-700", h));
  });
  thead.appendChild(hrow);
  table.appendChild(thead);
  const tbody = el("tbody", "divide-y divide-slate-800");

  function pushRows(benchmarkKey, expectedKey, predictedKey) {
    const detail = bm?.[benchmarkKey]?.details || [];
    detail.slice(0, 30).forEach((d) => {
      const tr = el("tr", "hover:bg-slate-800/50");
      tr.appendChild(el("td", "px-2 py-1.5 text-slate-400", benchmarkKey));
      tr.appendChild(el("td", "px-2 py-1.5 text-slate-200", String(d.id || "—")));
      tr.appendChild(el("td", "px-2 py-1.5 text-slate-200", String(d[expectedKey] ?? "—")));
      tr.appendChild(el("td", "px-2 py-1.5 text-slate-200", String(d[predictedKey] ?? d.reservation_id ?? "—")));
      tr.appendChild(el("td", "px-2 py-1.5 text-slate-300", String(d.latency_ms ?? "—")));
      tr.appendChild(el("td", "px-2 py-1.5", d.ok ? "✅" : "❌"));
      tbody.appendChild(tr);
    });
  }

  if (Object.prototype.hasOwnProperty.call(bm, "intent_routing_accuracy")) {
    pushRows("intent_routing_accuracy", "expected_intent", "predicted_intent");
  }
  if (Object.prototype.hasOwnProperty.call(bm, "triage_quality_accuracy")) {
    pushRows("triage_quality_accuracy", "expected_category_code", "predicted_category_code");
  }

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  root.appendChild(tableWrap);
}

function _deepClone(v) {
  return JSON.parse(JSON.stringify(v));
}

function _toLabPatchMessage(m) {
  if (!m || typeof m !== "object") return null;
  const t = String(m.type || "").toLowerCase();
  const content = typeof m.content === "string" ? m.content : String(m.content ?? "");
  if (!content.trim()) return null;
  if (t === "human") return { role: "human", content };
  if (t === "ai" || t === "assistant" || t === "system" || t === "tool") {
    return { role: "ai", content, name: m.name || "assistant" };
  }
  return null;
}

function _appendIfDifferent(arr, msg) {
  if (!msg) return;
  const last = arr[arr.length - 1];
  if (last && last.role === msg.role && last.content === msg.content) return;
  arr.push(msg);
}

function buildNextStatePatch(currentPatch, currentMessage, updates) {
  const next = _deepClone(currentPatch || {});
  const priorMsgs = Array.isArray(next.messages) ? _deepClone(next.messages) : [];
  delete next.messages;

  // Merge all update fields except messages (messages is handled separately below).
  Object.entries(updates || {}).forEach(([k, v]) => {
    if (k === "messages") return;
    next[k] = _deepClone(v);
  });

  const mergedMsgs = Array.isArray(priorMsgs) ? priorMsgs : [];
  const humanMsg = (currentMessage || "").trim();
  if (humanMsg) _appendIfDifferent(mergedMsgs, { role: "human", content: humanMsg });

  const aiMsgs = Array.isArray(updates?.messages) ? updates.messages.map(_toLabPatchMessage).filter(Boolean) : [];
  aiMsgs.forEach((m) => _appendIfDifferent(mergedMsgs, m));

  // Keep recent context only, avoid textarea becoming too large.
  next.messages = mergedMsgs.slice(-20);
  return next;
}

/**
 * Chỉ giữ các khóa lab cần để chạy lượt kế của dental_specialist.
 * Bỏ last_agent_message, current_agent, extra, reset booking… (API trả đủ graph state).
 */
function pickDentalSpecialistLabPatch(patch) {
  const order = [
    "follow_up_count",
    "symptoms_summary",
    "specialist_concluded",
    "category_code",
    "ai_diagnosis",
    "messages",
  ];
  const out = {};
  for (const k of order) {
    if (Object.prototype.hasOwnProperty.call(patch, k)) {
      out[k] = patch[k];
    }
  }
  return out;
}

function applyAgentRunAutoFill(pack, resultData) {
  const statePatchEl = $("state_patch");
  if (!statePatchEl) return;
  const updates = resultData?.updates;
  if (!updates || typeof updates !== "object") return;
  let nextPatch = buildNextStatePatch(pack.body.state_patch || {}, pack.body.message || "", updates);
  if (pack.body?.agent === "dental_specialist") {
    nextPatch = pickDentalSpecialistLabPatch(nextPatch);
  }
  statePatchEl.value = JSON.stringify(nextPatch, null, 2);
  const hint =
    pack.body?.agent === "dental_specialist"
      ? "Đã điền state_patch (lượt 2+: follow_up_count, symptoms_summary khi có, messages…)."
      : "Đã tự động điền state_patch cho lượt kế tiếp.";
  labFlash(hint);
}

function _mockEl(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null && text !== "") n.textContent = text;
  return n;
}

function renderMockSummary(data) {
  const root = $("mock-summary-content");
  if (!root) return;
  root.replaceChildren();
  const meta = data.meta || {};
  const metaBox = _mockEl("div", "rounded-lg bg-slate-950/50 border border-slate-700/60 p-3 mb-3 space-y-1.5 text-xs");
  const row = (label, value) => {
    const r = _mockEl("div", "flex flex-col sm:flex-row sm:gap-2 sm:items-baseline");
    r.appendChild(_mockEl("span", "text-slate-500 shrink-0 min-w-[7rem]", label));
    r.appendChild(_mockEl("span", "text-slate-200 font-mono break-all", value));
    return r;
  };
  metaBox.appendChild(row("Tệp", String(data.tep_json || "—")));
  metaBox.appendChild(row("Tuần bắt đầu", String(meta.tuan_bat_dau_iso || "—")));
  root.appendChild(metaBox);

  const wrap = _mockEl("div", "overflow-x-auto rounded-lg border border-slate-700/60");
  const table = _mockEl("table", "w-full text-xs text-left border-collapse min-w-[320px]");
  const thead = _mockEl("thead", "bg-slate-800/90 text-slate-400");
  const thr = _mockEl("tr");
  ["Ngày", "Thứ", "Làm việc", "Slot theo loại"].forEach((h) => {
    thr.appendChild(_mockEl("th", "px-3 py-2.5 font-semibold border-b border-slate-600/80 whitespace-nowrap", h));
  });
  thead.appendChild(thr);
  table.appendChild(thead);

  const tbody = _mockEl("tbody", "divide-y divide-slate-700/50");
  (data.cac_ngay || []).forEach((d) => {
    const tr = _mockEl("tr", "hover:bg-slate-800/40 transition-colors");
    tr.appendChild(_mockEl("td", "px-3 py-2 font-mono text-sky-200/90 whitespace-nowrap", d.date_iso || "—"));
    tr.appendChild(_mockEl("td", "px-3 py-2 text-slate-300", d.ten_thu_vi || "—"));
    const workTd = _mockEl("td", "px-3 py-2");
    const open = !!d.la_ngay_lam_viec_phong_kham;
    workTd.appendChild(_mockEl("span",
      open ? "inline-flex px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/25"
           : "inline-flex px-2 py-0.5 rounded-md text-[11px] font-medium bg-slate-600/30 text-slate-400 border border-slate-600/40",
      open ? "Có" : "Không"));
    tr.appendChild(workTd);
    const slotTd = _mockEl("td", "px-3 py-2 align-top");
    const counts = d.so_slot_theo_loai || {};
    const entries = Object.entries(counts).filter(([, n]) => n > 0);
    if (!entries.length) {
      slotTd.appendChild(_mockEl("span", "text-slate-500 italic", "Không có slot"));
    } else {
      const chipRow = _mockEl("div", "flex flex-wrap gap-1");
      entries.forEach(([code, n]) => {
        const chip = _mockEl("span", "inline-flex items-baseline gap-0.5 px-1.5 py-px rounded bg-amber-500/10 text-amber-100/85 border border-amber-500/15 font-mono text-[10px]");
        chip.appendChild(_mockEl("span", "font-semibold text-amber-200", code));
        chip.appendChild(_mockEl("span", "text-amber-200/60", "·"));
        chip.appendChild(_mockEl("span", "text-amber-100", String(n)));
        chipRow.appendChild(chip);
      });
      slotTd.appendChild(chipRow);
    }
    tr.appendChild(slotTd);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  root.appendChild(wrap);

  const codes = data.cac_ma_loai_kham_trong_file || [];
  if (codes.length) {
    const foot = _mockEl("div", "mt-3 pt-3 border-t border-slate-700/50");
    foot.appendChild(_mockEl("p", "text-[11px] text-slate-500 mb-2", "Category trong file"));
    const rowChips = _mockEl("div", "flex flex-wrap gap-1.5");
    codes.forEach((c) => { rowChips.appendChild(_mockEl("span", "px-2 py-0.5 rounded text-[11px] font-mono bg-slate-700/60 text-slate-200 border border-slate-600/50", c)); });
    foot.appendChild(rowChips);
    root.appendChild(foot);
  }
}

async function loadMockSummary() {
  const el = $("mock-summary-content");
  if (!el) return;
  el.replaceChildren();
  el.appendChild(_mockEl("p", "text-xs text-slate-500", "Đang tải…"));
  try {
    const data = await window.DentalApp.AdminLabAPI.getMockScheduleSummary();
    renderMockSummary(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    el.replaceChildren();
    el.appendChild(_mockEl("p", "text-xs text-red-400", "Không tải được: " + msg));
  }
}

function renderInspectResult(data) {
  const summaryEl = $("inspect-summary");
  const jsonEl = $("inspect-state-json");
  if (!summaryEl || !jsonEl) return;

  const has = !!data?.has_checkpoint;
  const nextNodes = Array.isArray(data?.next_nodes) ? data.next_nodes : [];
  const state = data?.state || {};
  const bits = [
    `checkpoint: ${has ? "có" : "không"}`,
    `intent: ${state.intent ?? "—"}`,
    `current_agent: ${state.current_agent ?? "—"}`,
    `triage_complete: ${state.triage_complete ?? "—"}`,
    `booking_confirmed: ${state.booking_confirmed ?? "—"}`,
    `next: ${nextNodes.length ? nextNodes.join(", ") : "END/không có"}`,
  ];
  summaryEl.textContent = bits.join("  |  ");
  jsonEl.textContent = JSON.stringify(data, null, 2);
}

async function inspectSessionState() {
  const sidRaw = $("inspect-session-id")?.value ?? "1";
  const sid = parseInt(sidRaw, 10) || 1;
  const btn = $("btn-inspect-session");
  const summaryEl = $("inspect-summary");
  const jsonEl = $("inspect-state-json");
  if (btn) btn.disabled = true;
  if (summaryEl) summaryEl.textContent = "Đang tải state...";
  if (jsonEl) jsonEl.textContent = "{}";
  try {
    let data;
    const adminApi = window.DentalApp?.AdminLabAPI;
    if (adminApi && typeof adminApi.getSessionState === "function") {
      data = await adminApi.getSessionState(sid);
    } else {
      // Fallback for stale cached api.js in browser.
      const base = window.DentalApp?.ApiConfig?.apiBase ?? "http://127.0.0.1:8000/api/v1";
      const token = window.DentalApp?.Auth?.getToken?.();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await fetch(`${base}/admin/lab/sessions/${sid}/state`, { headers });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || "Inspect request failed");
      }
      data = await res.json();
    }
    renderInspectResult(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (summaryEl) summaryEl.textContent = "Lỗi: " + msg;
    if (jsonEl) jsonEl.textContent = JSON.stringify({ error: msg }, null, 2);
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const { Auth, AdminLabAPI, ScheduleAPI } = window.DentalApp;
  $("admin-gate")?.classList.add("hidden");
  $("admin-main")?.classList.remove("hidden");
  renderNav();
  renderPanel();
  loadMockSummary();

  if (!Auth.isLoggedIn()) $("btn-admin-logout")?.classList.add("hidden");

  $("btn-admin-logout")?.addEventListener("click", () => { Auth.logout(); window.location.href = "index.html"; });
  $("btn-refresh-mock")?.addEventListener("click", () => loadMockSummary());
  $("btn-inspect-session")?.addEventListener("click", () => inspectSessionState());
  $("btn-clear-result")?.addEventListener("click", () => setResult({}, ""));
  inspectSessionState();

  $("btn-run")?.addEventListener("click", async () => {
    const btn = $("btn-run");
    btn.disabled = true;
    const t0 = performance.now();
    try {
      const pack = getFormValues();
      let data;
      if (pack.type === "agent") {
        data = await AdminLabAPI.invokeAgent(pack.body);
        applyAgentRunAutoFill(pack, data);
      } else if (pack.type === "tool") {
        data = await AdminLabAPI.invokeTool(pack.body);
      } else if (pack.type === "benchmark") {
        data = await AdminLabAPI.runBenchmark({
          dataset: pack.body.dataset,
          rows: pack.body.rows,
          benchmarks: benchmarksForDataset(pack.body.dataset),
        });
      } else {
        const s = pack.spec;
        if (s.id === "rest_slots") {
          data = await ScheduleAPI.getSlots($("rest_date")?.value?.trim() || null, $("rest_case")?.value?.trim() || null);
        } else if (s.id === "rest_week") {
          data = await ScheduleAPI.getWeekSlots($("rest_case")?.value?.trim() || null, $("rest_week_start")?.value?.trim() || null);
        } else if (s.id === "rest_reservations") {
          data = await ScheduleAPI.listReservations();
        }
      }
      setResult(data, `${Math.round(performance.now() - t0)} ms`);
    } catch (e) {
      setResult({ error: e instanceof Error ? e.message : String(e) }, "lỗi");
    } finally {
      btn.disabled = false;
    }
  });
});
