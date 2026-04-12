/**
 * Phòng lab Admin — gọi từng node LangGraph / tool / REST (dev).
 * Tài liệu kèm: lab-architecture.html (kiến trúc + data flow), lab-langgraph.html (sơ đồ graph).
 */

const $ = (id) => document.getElementById(id);

/** Slot mẫu khớp mock CAVITY (2026-03-31) — dùng xuyên suốt ví dụ lab. */
const LAB_SLOT_CAVITY_MOCK = {
  datetime_str: "2026-03-31T10:00:00+00:00",
  display: "Thứ 3, 31/03 – 10:00 (15p · Sâu răng / khắc phục sâu răng)",
  time_hm: "10:00",
  duration_minutes: 15,
  dental_case_code: "CAVITY",
};

const LAB_PERSONA_LINE =
  "Bệnh nhân mẫu xuyên suốt: chị Lan — đau răng hàm dưới bên phải vài ngày, nhói khi uống lạnh, muốn đặt lịch (mock tuần 2026-03-30).";

const BOOKING_FLOW_STEPS = [
  "Tin nhắn BN → classify_intent: consultation | select_slot | confirm_appointment | general.",
  "consultation → dental_specialist: rubric mock (triage_symptom_rubric_vi.json) trong prompt → JSON needs_visit, dental_case_code…",
  "needs_visit true → save_intake (PostgreSQL) → query_slots (get_mock_schedule đọc lich_trong_tuan_trong_vi.json) → root_respond + chip giờ.",
  "BN chọn giờ / đồng ý slot → confirm_booking hoặc booking_prepare → Reservation trong DB.",
];

const AGENTS = [
  {
    id: "classify_intent",
    title: "classify_intent",
    subtitle: "Root Orchestrator – phân luồng ý định (LLM trả về một nhãn)",
    roleBlurb:
      "Chạy **đầu mỗi lượt** user trong graph. Dựa vào tin nhắn mới nhất + state (đã triage chưa, đã có slot chưa, đang chờ xác nhận không…) để quyết định nhánh. Ở lab bạn giả lập state qua state_patch.",
    inputDoc: [
      "message: câu BN vừa gửi (hoặc để trống nếu chỉ dùng messages trong state_patch).",
      "state_patch.messages: mảng { role: \"human\" | \"ai\", content } để dựng hội thoại — node chỉ xét vài tin gần nhất.",
      "state_patch quan trọng: triage_complete, intake_id, available_slots, pending_confirmation_slot, symptoms_summary, follow_up_count…",
      "Heuristic: tin kiểu “chỉ đặt lịch” khi chưa triage thường bị ép sang consultation (không cho đặt giờ trước triệu chứng).",
      "Nếu pending_confirmation_slot có display + BN nói “đồng ý” / “ok” → shortcut intent confirm_appointment (không cần LLM).",
    ],
    outputDoc: [
      "updates.intent: consultation | select_slot | confirm_appointment | general.",
      "updates.current_agent: \"root\"; skip_root_respond (bool) — luôn kiểm tra kèm các field khác trong JSON trả về.",
      "Không sinh câu thoại dài — chỉ metadata định tuyến; câu trả lời user do root_respond hoặc node khác xử lý.",
    ],
    defaultMessage:
      "Chào bạn, mình tên Lan. Mình bị đau răng hàm dưới bên phải khoảng 3 ngày nay, nhói khi uống nước lạnh. Mình muốn đặt lịch khám.",
    defaultStatePatch: "{}",
    scenarioExamples: [
      {
        label: "Mở đầu — chưa triage, mô tả triệu chứng + muốn hẹn",
        hint: "Thường ra consultation (đi tiếp specialist). Có thể general nếu LLM hiểu là chào hỏi đơn giản — kiểm tra JSON.",
        message:
          "Chào bạn, mình tên Lan. Mình bị đau răng hàm dưới bên phải khoảng 3 ngày nay, nhói khi uống nước lạnh. Mình muốn đặt lịch khám.",
        statePatch: "{}",
      },
      {
        label: "Đã triage + đã có slot — BN nêu giờ",
        hint: "Cần triage_complete, intake_id, available_slots không rỗng. Kỳ vọng select_slot (hoặc tương đương) để vào luồng đặt giờ.",
        message: "Em muốn khám lúc 10:00 ạ.",
        statePatch: JSON.stringify(
          {
            triage_complete: true,
            intake_id: 1,
            dental_case_code: "CAVITY",
            available_slots: [LAB_SLOT_CAVITY_MOCK],
            symptoms_summary: "Đau răng hàm dưới phải, nhói khi uống lạnh",
            follow_up_count: 2,
          },
          null,
          2
        ),
      },
      {
        label: "Đang chờ xác nhận slot — BN đồng ý",
        hint: "Có pending_confirmation_slot. Nội dung ngắn chứa “đồng ý” → intent confirm_appointment (shortcut, không gọi LLM).",
        message: "Đồng ý, em xác nhận đặt lịch này ạ.",
        statePatch: JSON.stringify(
          {
            triage_complete: true,
            intake_id: 1,
            pending_confirmation_slot: {
              datetime_str: LAB_SLOT_CAVITY_MOCK.datetime_str,
              display: LAB_SLOT_CAVITY_MOCK.display,
            },
            dental_case_code: "CAVITY",
          },
          null,
          2
        ),
      },
      {
        label: "FAQ / không liên quan đặt lịch",
        hint: "Kỳ vọng general để root_respond trả lời chung.",
        message: "Phòng khám có làm việc thứ 7 không bạn?",
        statePatch: JSON.stringify({ triage_complete: false }, null, 2),
      },
    ],
  },
  {
    id: "dental_specialist",
    title: "dental_specialist",
    subtitle: "Chuyên gia tiếp nhận — chỉ chat văn bản, chốt JSON khi đủ triệu chứng",
    roleBlurb:
      "Chỉ chạy khi graph đã định tuyến **consultation** (hoặc select_slot nhưng chưa triage — vẫn qua specialist). Mỗi lần gọi tăng follow_up_count; khi đạt ngưỡng MAX hoặc đủ JSON thì chuyển sang save_intake trong graph thật.",
    inputDoc: [
      "message: câu BN hiện tại (thường là triệu chứng hoặc trả lời câu hỏi làm rõ).",
      "state_patch.follow_up_count: số lượt đã hỏi (0 = lượt đầu).",
      "state_patch.symptoms_summary: tóm tắt tích lũy nếu đang mô phỏng giữa chừng.",
      "state_patch.messages: có thể thêm tin specialist trước đó (role ai) để mô phỏng đa lượt.",
      "Một số cụm “hết rồi / đủ rồi / không còn triệu chứng” kích hoạt nhánh chốt sớm không cần JSON đầy đủ từ LLM (xem code dental_specialist).",
    ],
    outputDoc: [
      "messages: thêm AIMessage (lời thoại cho BN; phần JSON fence bị ẩn khi hiển thị chat thật).",
      "Khi parse được ```json: symptoms_summary, ai_diagnosis, needs_visit, dental_case_code.",
      "follow_up_count tăng 1; last_agent_message, extra.message_ui (thường null).",
    ],
    defaultMessage:
      "Dạ em là Lan. Em bị đau răng hàm dưới bên phải khoảng 3 ngày nay, đau khoảng 6/10, nhói mạnh khi uống nước lạnh.",
    defaultStatePatch: JSON.stringify({ follow_up_count: 0 }, null, 2),
    scenarioExamples: [
      {
        label: "Lượt 1 — mô tả triệu chứng lần đầu (cùng flow Lan)",
        hint: "LLM thường hỏi thêm 1 câu làm rõ (chưa chốt JSON).",
        message:
          "Dạ em là Lan. Em bị đau răng hàm dưới bên phải khoảng 3 ngày nay, đau khoảng 6/10, nhói mạnh khi uống nước lạnh.",
        statePatch: JSON.stringify({ follow_up_count: 0 }, null, 2),
      },
      {
        label: "Lượt 2 — trả lời câu hỏi làm rõ (đã hỏi 1 lần)",
        hint: "Tăng follow_up_count; có symptoms_summary sơ bộ. Có thể vẫn hỏi tiếp hoặc bắt đầu chốt tùy model.",
        message:
          "Dạ em không thấy sưng má, cũng không sốt. Chỉ đau nhói khi chạm nước lạnh hoặc đồ ngọt thôi ạ.",
        statePatch: JSON.stringify(
          {
            follow_up_count: 1,
            symptoms_summary: "Đau răng hàm dưới phải vài ngày, nhói khi lạnh/ngọt",
          },
          null,
          2
        ),
      },
      {
        label: "BN báo không còn triệu chứng cần nói — chốt sớm",
        hint: "Cần ít nhất 2 tin human trong history; nội dung chứa “hết rồi/đủ rồi…”. Node có nhánh rule-based (không chờ JSON LLM).",
        message: "Dạ em chỉ có vậy thôi, không còn triệu chứng gì thêm ạ.",
        statePatch: JSON.stringify(
          {
            follow_up_count: 1,
            symptoms_summary: "Đau răng hàm dưới phải, nhói khi uống lạnh",
            messages: [
              { role: "human", content: "Em bị đau răng hàm dưới phải mấy ngày nay." },
              { role: "ai", content: "Bạn mô tả thêm mức độ đau được không?" },
            ],
          },
          null,
          2
        ),
      },
    ],
  },
  {
    id: "root_respond",
    title: "root_respond",
    subtitle: "Root — soạn câu trả lời (FAQ, danh sách giờ, xác nhận…)",
    roleBlurb:
      "Chạy sau classify hoặc sau các node booking khi cần **một câu trả lời tổng hợp** cho BN. Trong graph thật, sau query_slots state đã có available_slots — ở lab bạn dựng sẵn trong state_patch.",
    inputDoc: [
      "state_patch.intent: general | consultation | … — ảnh hưởng ngữ cảnh prompt nội bộ của node.",
      "state_patch.available_slots: mảng object giống output get_mock_schedule (datetime_str, display, time_hm, dental_case_code…).",
      "state_patch.dental_case_code: nhãn loại khám để root nhắc trong câu trả lời.",
      "message / messages: ngữ cảnh hội thoại (ví dụ BN vừa hỏi gì).",
      "Có thể mô phỏng booking_confirmed, selected_slot nếu test câu chốt lịch (tùy prompt).",
    ],
    outputDoc: [
      "messages: AIMessage nội dung hiển thị cho BN.",
      "extra.message_ui: object template time_chips | confirm_actions khi graph yêu cầu (lab ít set sẵn).",
    ],
    defaultMessage: "Bạn cho mình xem các giờ còn trống phù hợp với loại khám của mình nhé.",
    defaultStatePatch: JSON.stringify(
      {
        intent: "consultation",
        dental_case_code: "CAVITY",
        triage_complete: true,
        available_slots: [LAB_SLOT_CAVITY_MOCK],
      },
      null,
      2
    ),
    scenarioExamples: [
      {
        label: "Sau khi có slot — gợi ý chọn giờ (cùng slot mock Lan)",
        hint: "Giống ngữ cảnh sau query_slots trong graph; LLM tóm tắt giờ trống.",
        message: "Bạn cho mình xem các giờ còn trống phù hợp với loại khám của mình nhé.",
        statePatch: JSON.stringify(
          {
            intent: "consultation",
            dental_case_code: "CAVITY",
            triage_complete: true,
            available_slots: [LAB_SLOT_CAVITY_MOCK],
          },
          null,
          2
        ),
      },
      {
        label: "FAQ — không có slot trong state",
        hint: "intent general + available_slots [].",
        message: "Phòng khám có làm việc thứ 7 không bạn?",
        statePatch: JSON.stringify(
          { intent: "general", available_slots: [] },
          null,
          2
        ),
      },
      {
        label: "Nhiều giờ mock (SCALING) — đối chiếu bảng mock",
        hint: "Có thể copy thêm slot từ GET /schedule/slots hoặc file JSON.",
        message: "Mình muốn buổi chiều, bạn gợi ý giúp mình.",
        statePatch: JSON.stringify(
          {
            intent: "consultation",
            dental_case_code: "SCALING",
            triage_complete: true,
            available_slots: [
              {
                datetime_str: "2026-03-30T13:30:00+00:00",
                display: "Thứ 2, 30/03 – 13:30 (15p · Cạo vôi / vệ sinh răng miệng)",
                time_hm: "13:30",
                duration_minutes: 15,
                dental_case_code: "SCALING",
              },
              {
                datetime_str: "2026-03-30T13:45:00+00:00",
                display: "Thứ 2, 30/03 – 13:45 (15p · Cạo vôi / vệ sinh răng miệng)",
                time_hm: "13:45",
                duration_minutes: 15,
                dental_case_code: "SCALING",
              },
            ],
          },
          null,
          2
        ),
      },
    ],
  },
];

const TOOLS = [
  {
    id: "get_mock_schedule",
    title: "get_mock_schedule",
    subtitle: "Một tool duy nhất — chỉ đọc file lich_trong_tuan_trong_vi.json",
    inputDoc: [
      'scope: "day" | "week".',
      'Ngày: { "scope": "day", "date_str": "2026-03-30", "dental_case_code": "SCALING" } — bỏ date_str → ngày đầu tiên trong file có slot cho mã đó.',
      'Tuần: { "scope": "week", "dental_case_code": "CAVITY", "week_start_iso": null } — week_start_iso phải khớp meta.tuan_bat_dau_iso nếu truyền.',
      "So sánh với bảng «Dữ liệu mock» cột bên phải.",
    ],
    outputDoc: [
      "day: scope, ok, date, dental_case_code, slots[], nguon_du_lieu; hoặc ok=false + loi nếu ngày không có trong file.",
      "week: scope, ok, meta, ngay[], nguon_du_lieu (cấu trúc giống GET /schedule/week/slots).",
    ],
    defaultArgs: '{\n  "scope": "day",\n  "date_str": "2026-03-30",\n  "dental_case_code": "SCALING"\n}',
  },
  {
    id: "book_appointment",
    title: "book_appointment",
    subtitle: "Tool – ghi Reservation (patient_user_id = bạn)",
    inputDoc: [
      '{ "patient_user_id": 1, "intake_id": 1, "datetime_str": "2026-04-01T08:00:00+00:00" }',
      "Truyền đủ patient_user_id (dev không kiểm JWT).",
    ],
    outputDoc: ["reservation_id, datetime_str, display, status hoặc error."],
    defaultArgs:
      '{\n  "patient_user_id": 1,\n  "intake_id": 1,\n  "datetime_str": "2026-04-01T09:00:00+00:00"\n}',
  },
  {
    id: "save_consult_intake",
    title: "save_consult_intake",
    subtitle: "Tool – lưu BookingConsultIntake",
    inputDoc: [
      "patient_user_id, session_id, symptoms, ai_diagnosis, needs_visit, dental_case_code (tùy chọn).",
      "Dev: không ép JWT — truyền đúng patient_user_id trong args.",
    ],
    outputDoc: ["intake_id, needs_visit, symptoms."],
    defaultArgs:
      '{\n  "patient_user_id": 1,\n  "session_id": 1,\n  "symptoms": "Đau răng hàm dưới",\n  "ai_diagnosis": "Ghi chú tiếp nhận (không thay chẩn đoán).",\n  "needs_visit": true,\n  "dental_case_code": "CAVITY"\n}',
  },
  {
    id: "resolve_requested_slot",
    title: "resolve_requested_slot",
    subtitle: "Hàm Python (không phải @tool) – khớp giờ xin với lưới slot",
    inputDoc: [
      '{ "date_iso": "2026-03-31", "hour": 14, "minute": 15, "dental_case_code": "SCALING" }',
    ],
    outputDoc: [
      "kind: exact_available | suggest | closed; slot hoặc alternatives.",
    ],
    defaultArgs:
      '{\n  "date_iso": "2026-03-31",\n  "hour": 14,\n  "minute": 0,\n  "dental_case_code": "SCALING"\n}',
  },
  {
    id: "infer_date_str_from_user_text",
    title: "infer_date_str_from_user_text",
    subtitle: "Hàm – suy ra YYYY-MM-DD từ cụm thứ / ngày mai",
    inputDoc: ['{ "user_text": "em muốn khám thứ 6 tuần này" }'],
    outputDoc: ["Chuỗi YYYY-MM-DD hoặc null."],
    defaultArgs: '{\n  "user_text": "Tôi muốn đặt lịch ngày mai"\n}',
  },
];

const REST = [
  {
    id: "rest_slots",
    title: "GET /schedule/slots",
    subtitle: "Chỉ dữ liệu file mock JSON (không JWT)",
    inputDoc: ["Query: date (YYYY-MM-DD), case (mã loại khám)."],
    outputDoc: ["SlotsResponse: date, dental_case_code, slots[]."],
    fields: [
      { name: "date", type: "text", placeholder: "2026-03-30", label: "date" },
      {
        name: "case",
        type: "text",
        placeholder: "SCALING",
        label: "case",
      },
    ],
  },
  {
    id: "rest_week",
    title: "GET /schedule/week/slots",
    subtitle: "File mock cả tuần (không JWT)",
    inputDoc: ["Query: case, week_start (YYYY-MM-DD)."],
    outputDoc: ["Payload build_week_availability_payload."],
    fields: [
      {
        name: "case",
        type: "text",
        placeholder: "CAVITY hoặc để trống",
        label: "case",
      },
      {
        name: "week_start",
        type: "text",
        placeholder: "2026-03-30",
        label: "week_start",
      },
    ],
  },
  {
    id: "rest_reservations",
    title: "GET /schedule/reservations",
    subtitle: "Vẫn cần header Authorization (JWT)",
    inputDoc: ["Bearer token trong header (api.js tự gắn nếu đã đăng nhập ở index)."],
    outputDoc: ["Mảng ReservationResponse."],
    fields: [],
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
  ul.className =
    "list-disc space-y-2.5 pl-5 text-sm text-slate-300 leading-relaxed marker:text-slate-500";
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
  nav.appendChild(
    el("p", "text-sm font-semibold text-violet-200 mb-2", "Luồng đặt lịch — thứ tự tham chiếu")
  );
  const navOl = document.createElement("ol");
  navOl.className = "list-decimal pl-5 space-y-1.5 text-xs text-slate-400 leading-relaxed";
  BOOKING_FLOW_STEPS.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s;
    navOl.appendChild(li);
  });
  nav.appendChild(navOl);
  nav.appendChild(
    el(
      "p",
      "text-xs text-slate-500 mt-3 pt-3 border-t border-violet-500/20 leading-relaxed",
      LAB_PERSONA_LINE
    )
  );
  root.appendChild(nav);

  if (spec.roleBlurb) {
    const blurb = el("div", "rounded-xl border border-slate-600/40 bg-slate-900/35 p-4");
    blurb.appendChild(el("p", "text-sm font-medium text-slate-200 mb-2", "Vai trò node này"));
    const p = el("p", "text-sm text-slate-400 leading-relaxed", "");
    p.textContent = spec.roleBlurb;
    blurb.appendChild(p);
    root.appendChild(blurb);
  }

  if (!spec.scenarioExamples?.length) {
    return;
  }

  root.appendChild(
    el(
      "p",
      "text-sm font-semibold text-sky-300/90 mt-1",
      "Ví dụ input (cùng flow Lan — bấm «Điền mẫu này» rồi Chạy)"
    )
  );

  spec.scenarioExamples.forEach((ex, i) => {
    const card = el("div", "rounded-xl border border-slate-700/60 bg-slate-900/40 p-4 space-y-2");
    const head = el("div", "flex flex-wrap items-start justify-between gap-2");
    head.appendChild(
      el("span", "text-sm font-medium text-white pr-2", `${i + 1}. ${ex.label}`)
    );
    const btn = el(
      "button",
      "shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium bg-sky-600/85 hover:bg-sky-500 text-white",
      "Điền mẫu này"
    );
    btn.type = "button";
    btn.addEventListener("click", () => applyAgentExample(ex.message, ex.statePatch));
    head.appendChild(btn);
    card.appendChild(head);
    if (ex.hint) {
      card.appendChild(el("p", "text-xs text-amber-200/75 leading-relaxed", ex.hint));
    }
    const pre = el(
      "pre",
      "text-[11px] font-mono text-slate-400 bg-slate-950/70 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed"
    );
    pre.textContent = `message:\n${ex.message}\n\nstate_patch:\n${ex.statePatch}`;
    card.appendChild(pre);
    root.appendChild(card);
  });
}

function findSpec() {
  if (selected.kind === "agent") return AGENTS.find((a) => a.id === selected.id);
  if (selected.kind === "tool") return TOOLS.find((t) => t.id === selected.id);
  return REST.find((r) => r.id === selected.id);
}

function renderNav() {
  const na = $("nav-agents");
  const nt = $("nav-tools");
  const nr = $("nav-rest");
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
    b.addEventListener("click", () => {
      selected = { kind: "agent", id: a.id };
      renderNav();
      renderPanel();
    });
    na.appendChild(b);
  });

  TOOLS.forEach((t) => {
    const b = el("button", btnCls(selected.kind === "tool" && selected.id === t.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${t.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${t.subtitle}</span>`;
    b.addEventListener("click", () => {
      selected = { kind: "tool", id: t.id };
      renderNav();
      renderPanel();
    });
    nt.appendChild(b);
  });

  REST.forEach((r) => {
    const b = el("button", btnCls(selected.kind === "rest" && selected.id === r.id));
    b.type = "button";
    b.innerHTML = `<span class="font-semibold text-slate-100 leading-tight block">${r.title}</span><span class="text-[11px] text-slate-500 leading-snug block mt-0.5">${r.subtitle}</span>`;
    b.addEventListener("click", () => {
      selected = { kind: "rest", id: r.id };
      renderNav();
      renderPanel();
    });
    nr.appendChild(b);
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
    form.appendChild(
      labelInput("message", "Tin nhắn (nếu không dùng messages trong state_patch)", "textarea", spec.defaultMessage)
    );
    form.appendChild(labelInput("session_id", "session_id (số, cho log Langfuse/thread)", "number", "1"));
    form.appendChild(labelInput("patient_user_id", "patient_user_id (dev, không JWT)", "number", "1"));
    form.appendChild(
      labelInput(
        "state_patch",
        "state_patch (JSON, tùy chọn). Ví dụ: follow_up_count, available_slots, triage_complete…",
        "textarea",
        spec.defaultStatePatch || "{}"
      )
    );
  } else if (selected.kind === "tool") {
    form.appendChild(
      labelInput("tool_args", "args (JSON object)", "textarea", spec.defaultArgs || "{}")
    );
  } else {
    spec.fields.forEach((f) => {
      form.appendChild(
        labelInput(`rest_${f.name}`, f.label, f.type, "", f.placeholder)
      );
    });
  }
}

function labelInput(id, label, type, value, placeholder) {
  const wrap = el("div", "space-y-1.5");
  const lab = el("label", "block text-sm font-medium text-slate-300", label);
  lab.setAttribute("for", id);
  let input;
  if (type === "textarea") {
    input = el(
      "textarea",
      "w-full min-h-[120px] bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-3 text-sm text-slate-100 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50"
    );
    input.rows = 5;
  } else if (type === "number") {
    input = el(
      "input",
      "w-full max-w-[12rem] bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-2.5 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50"
    );
    input.type = "number";
    input.min = "1";
  } else {
    input = el(
      "input",
      "w-full bg-slate-950/50 border border-slate-600 rounded-xl px-3.5 py-2.5 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-500/50"
    );
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
      try {
        patch = JSON.parse(raw);
      } catch (e) {
        throw new Error("state_patch không phải JSON hợp lệ: " + e.message);
      }
    }
    return {
      type: "agent",
      body: {
        agent: spec.id,
        message: msg,
        session_id: sid,
        patient_user_id: pid,
        state_patch: patch,
      },
    };
  }
  if (selected.kind === "tool") {
    let args = {};
    const raw = ($("tool_args")?.value || "").trim();
    if (raw) {
      try {
        args = JSON.parse(raw);
      } catch (e) {
        throw new Error("args không phải JSON hợp lệ: " + e.message);
      }
    }
    return { type: "tool", body: { tool: spec.id, args } };
  }
  return { type: "rest", spec };
}

function setResult(obj, meta) {
  $("result-json").textContent = JSON.stringify(obj, null, 2);
  $("result-meta").textContent = meta || "";
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
  const moTaBlock = _mockEl("div", "pt-2 mt-1 border-t border-slate-700/50");
  moTaBlock.appendChild(_mockEl("span", "text-slate-500 block text-[11px] mb-1", "Mô tả"));
  moTaBlock.appendChild(
    _mockEl("p", "text-slate-400 leading-relaxed", meta.mo_ta || "—")
  );
  metaBox.appendChild(moTaBlock);
  root.appendChild(metaBox);

  const wrap = _mockEl("div", "overflow-x-auto rounded-lg border border-slate-700/60");
  const table = _mockEl("table", "w-full text-xs text-left border-collapse min-w-[320px]");
  const thead = _mockEl("thead", "bg-slate-800/90 text-slate-400");
  const thr = _mockEl("tr", "");
  ["Ngày", "Thứ", "Làm việc", "Slot theo loại (số lượng)"].forEach((h, i) => {
    const th = _mockEl("th", "px-3 py-2.5 font-semibold border-b border-slate-600/80 whitespace-nowrap", h);
    if (i === 3) th.className += " min-w-[10rem]";
    thr.appendChild(th);
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
    workTd.appendChild(
      _mockEl(
        "span",
        open
          ? "inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/25"
          : "inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium bg-slate-600/30 text-slate-400 border border-slate-600/40",
        open ? "Có" : "Không"
      )
    );
    tr.appendChild(workTd);

    const slotTd = _mockEl("td", "px-3 py-2 align-top");
    const counts = d.so_slot_theo_loai || {};
    const entries = Object.entries(counts).filter(([, n]) => n > 0);
    if (entries.length === 0) {
      slotTd.appendChild(_mockEl("span", "text-slate-500 italic", "Không có slot"));
    } else {
      const chipRow = _mockEl("div", "flex flex-wrap gap-1");
      entries.forEach(([code, n]) => {
        const chip = _mockEl(
          "span",
          "inline-flex items-baseline gap-0.5 px-1.5 py-px rounded bg-amber-500/10 text-amber-100/85 border border-amber-500/15 font-mono text-[10px] leading-tight"
        );
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
    foot.appendChild(_mockEl("p", "text-[11px] text-slate-500 mb-2", "Mã loại khám có trong file"));
    const rowChips = _mockEl("div", "flex flex-wrap gap-1.5");
    codes.forEach((c) => {
      rowChips.appendChild(
        _mockEl(
          "span",
          "px-2 py-0.5 rounded text-[11px] font-mono bg-slate-700/60 text-slate-200 border border-slate-600/50",
          c
        )
      );
    });
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
    el.appendChild(_mockEl("p", "text-xs text-red-400", "Không tải được tóm tắt mock: " + msg));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const { Auth, AdminLabAPI, ScheduleAPI } = window.DentalApp;

  $("admin-gate")?.classList.add("hidden");
  $("admin-main")?.classList.remove("hidden");
  renderNav();
  renderPanel();
  loadMockSummary();

  if (!Auth.isLoggedIn()) {
    $("btn-admin-logout")?.classList.add("hidden");
  }

  $("btn-admin-logout")?.addEventListener("click", () => {
    Auth.logout();
    window.location.href = "index.html";
  });

  $("btn-refresh-mock")?.addEventListener("click", () => loadMockSummary());

  $("btn-clear-result")?.addEventListener("click", () => setResult({}, ""));

  $("btn-run")?.addEventListener("click", async () => {
    const btn = $("btn-run");
    btn.disabled = true;
    const t0 = performance.now();
    try {
      const pack = getFormValues();
      let data;
      if (pack.type === "agent") {
        data = await AdminLabAPI.invokeAgent(pack.body);
      } else if (pack.type === "tool") {
        data = await AdminLabAPI.invokeTool(pack.body);
      } else {
        const s = pack.spec;
        if (s.id === "rest_slots") {
          const date = $("rest_date")?.value?.trim() || null;
          const c = $("rest_case")?.value?.trim() || null;
          data = await ScheduleAPI.getSlots(date, c);
        } else if (s.id === "rest_week") {
          const c = $("rest_case")?.value?.trim() || null;
          const w = $("rest_week_start")?.value?.trim() || null;
          data = await ScheduleAPI.getWeekSlots(c || null, w || null);
        } else if (s.id === "rest_reservations") {
          data = await ScheduleAPI.listReservations();
        }
      }
      const ms = Math.round(performance.now() - t0);
      setResult(data, `${ms} ms`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult({ error: msg }, "lỗi");
    } finally {
      btn.disabled = false;
    }
  });
});
