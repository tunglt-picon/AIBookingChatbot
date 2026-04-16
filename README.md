# SmileCare AI – Smart Dental Booking Assistant

> **Full MVP** — Multi-agent AI with LangGraph, FastAPI, PostgreSQL, Redis, and a modern chat UI.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Tech stack](#2-tech-stack)
3. [Category system](#3-category-system)
4. [Project structure](#4-project-structure)
5. [Quick start](#5-quick-start)
6. [Configuration](#6-configuration)
7. [LangGraph multi-agent design](#7-langgraph-multi-agent-design)
8. [API reference](#8-api-reference)
9. [User interface](#9-user-interface)
10. [Observability (Langfuse)](#10-observability-langfuse)
11. [Quality evaluation (Eval)](#11-quality-evaluation-eval)
12. [Production extensions](#12-production-extensions)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Architecture overview

```
┌────────────────────────────────────────────────────────────┐
│  CLIENT — index.html (chat + SSE) · admin lab HTML pages   │
└───────────────────────────┬────────────────────────────────┘
                            │  REST + SSE
┌───────────────────────────▼────────────────────────────────┐
│  FastAPI — /auth · /chat · /schedule · /admin/lab          │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│  LangGraph (Redis checkpoint) — classify_intent → … → END  │
│  · Root LLM: intent, FAQ, slot wording, booking UX         │
│  · Dental specialist LLM: triệu chứng → category_code     │
│  · Tools: get_mock_schedule, save_consult_intake,          │
│           book_appointment (+ helpers in schedule_tools)   │
└───────────────────────────┬────────────────────────────────┘
         ┌──────────────────┴──────────────────┐
         ▼                                      ▼
┌─────────────────┐                 ┌─────────────────────────┐
│  PostgreSQL     │                 │  Mock JSON (dev)        │
│  users, sessions│                 │  lich_trong_tuan…json   │
│  messages,      │                 │  triage_symptom_rubric… │
│  intakes, resv  │                 │                         │
└─────────────────┘                 └─────────────────────────┘
         ▲
         │
┌─────────────────┐
│  Redis Stack    │
│  graph state    │
│  (checkpoint)   │
└─────────────────┘
```

**Lab documentation (static HTML, no build):**

| Page | Purpose |
|------|---------|
| `frontend/admin.html` | Invoke agents/tools/REST against the API |
| `frontend/lab-architecture.html` | System layers + **data flow** (SSE, DB, mock files) |
| `frontend/lab-langgraph.html` | LangGraph nodes, edges, `AgentState` summary |

---

## 2. Tech stack

| Component | Technology | Notes |
|-----------|------------|-------|
| **Backend** | FastAPI 0.115 + Uvicorn | Async, SSE streaming |
| **ORM** | SQLAlchemy 2.0 (async) | asyncpg driver |
| **Database** | PostgreSQL 16 | Docker Compose |
| **Redis** | Redis Stack (RedisJSON + RediSearch) | LangGraph checkpoint persistence — state giữ nguyên sau restart |
| **Agents** | LangGraph 0.2 + langgraph-checkpoint-redis | Stateful multi-turn graph |
| **Default LLM** | Google Gemini **2.5 Flash-Lite** (`LLM_PROVIDER=google`) | API key; stable for new accounts |
| **Root agent** | `GOOGLE_ROOT_MODEL` or Ollama / OpenAI / compatible | Intent + booking UX |
| **Specialist agent** | `GOOGLE_SPECIALIST_MODEL` or same stack | Symptom intake + `category_code` (rubric in `data/mock/`) |
| **Observability** | Langfuse | Trace LLM calls |
| **Auth** | JWT (python-jose) + bcrypt | Stateless |
| **Frontend** | HTML + Tailwind + vanilla JS | No build step |
| **Containers** | Docker + Docker Compose | Dev and prod |

---

## 3. Category system

Hệ thống 5 danh mục khám — mỗi danh mục có thời lượng, khung giờ, và triệu chứng đặc trưng.

| Code | Tên | Thời lượng | Khung giờ |
|------|-----|-----------|-----------|
| **CAT-01** | Trám răng / Phục hồi thẩm mỹ | 45 phút | Sáng 08:00–11:30 · Chiều 13:30–17:00 |
| **CAT-02** | Điều trị Tủy / Nội nha | 60 phút | Sáng 08:00–11:00 · Chiều 13:30–16:30 |
| **CAT-03** | Nhổ răng / Tiểu phẫu | 40 phút | Sáng 07:30–11:00 · Chiều 13:30–16:00 |
| **CAT-04** | Nha khoa Trẻ em | 30 phút | Sáng 08:00–11:30 · Chiều 14:00–16:30 |
| **CAT-05** | Khám Tổng quát & X-Quang | 30 phút | Sáng 07:30–11:30 · Chiều 13:30–17:00 |

**Luồng phân loại:**

1. Bệnh nhân mô tả triệu chứng
2. `dental_specialist` thu thập thêm thông tin (tối đa `MAX_FOLLOW_UP_QUESTIONS` câu hỏi)
3. Agent dùng **symptom matrix** (152 triệu chứng) để chấm điểm category
4. Xác nhận với bệnh nhân — nếu 2 category gần nhau, cho bệnh nhân chọn
5. Tra lịch trống theo `category_code` + thời gian mong muốn

Định nghĩa chi tiết: `backend/app/domain/dental_cases.py` và `backend/data/mock/triage_symptom_rubric_vi.json`.

---

## 4. Project structure

```
AIBookingChatbot/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config.py            # Pydantic Settings (env vars)
│   │   ├── database.py          # SQLAlchemy async engine + Base
│   │   │
│   │   ├── models/              # SQLAlchemy ORM models (ERD)
│   │   │   ├── patient.py       # PatientUser, PatientProfile
│   │   │   ├── session.py       # BookingChatSession, BookingChatMessage
│   │   │   └── reservation.py   # BookingConsultIntake, Reservation
│   │   │
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   │   ├── auth.py
│   │   │   ├── chat.py
│   │   │   └── reservation.py
│   │   │
│   │   ├── api/v1/              # FastAPI routers
│   │   │   ├── auth.py          # /auth/register, /auth/login
│   │   │   ├── chat.py          # /chat/sessions/** (SSE)
│   │   │   ├── schedule.py      # /schedule/slots, week mock, reservations
│   │   │   └── admin_lab.py     # /admin/lab/** (dev agent/tool invoke)
│   │   │
│   │   ├── agents/              # LangGraph multi-agent system
│   │   │   ├── state.py         # AgentState TypedDict
│   │   │   ├── llm_factory.py   # LLM provider abstraction
│   │   │   ├── root_orchestrator.py  # classify_intent, root_respond, booking nodes
│   │   │   ├── dental_specialist.py  # Symptom intake + category classification
│   │   │   └── graph.py         # StateGraph + Redis checkpointer
│   │   │
│   │   ├── domain/              # Category profiles (CAT-01→05), slot generation
│   │   │   └── dental_cases.py
│   │   │
│   │   ├── tools/               # LangChain @tool (+ helpers)
│   │   │   ├── schedule_tools.py  # get_mock_schedule, book_appointment, resolve_requested_slot
│   │   │   └── intake_tools.py    # save_consult_intake
│   │   │
│   │   ├── services/            # Business logic
│   │   │   ├── auth_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── mock_week_schedule_loader.py
│   │   │   └── triage_rubric_loader.py
│   │   │
│   │   └── observability/
│   │       └── langfuse_client.py   # Langfuse tracing wrapper
│   │
│   ├── data/mock/               # Mock data (lich_trong_tuan_trong_vi + triage_symptom_rubric_vi)
│   ├── scripts/                 # generate_lich_trong_tuan_json.py (sinh lịch mock)
│   ├── uploads/                 # Static uploads dir (gitignored)
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── index.html               # Single-page app (Tailwind + vanilla JS)
│   ├── admin.html               # Admin lab (agents / tools / REST)
│   ├── lab-architecture.html    # Architecture + data flow (Mermaid)
│   ├── lab-langgraph.html       # LangGraph diagram
│   ├── js/
│   │   ├── api.js               # API client (Auth, Chat, Schedule)
│   │   ├── app.js               # UI logic, SSE handler
│   │   └── admin.js             # Admin lab UI
│   └── css/
│       └── custom.css           # Animations, custom components
│
├── docker-compose.yml
├── .env.example                 # Template – copy to backend/.env
├── .gitignore
└── README.md
```

---

## 5. Quick start

### Requirements

- Docker & Docker Compose (v2+)
- Ollama (for local LLMs): https://ollama.ai
- Python 3.11+ (if running the backend outside Docker)

### Step 1: Clone and configure

```bash
git clone <repo-url>
cd AIBookingChatbot

# Create backend env file
cp .env.example backend/.env
# Edit backend/.env as needed (see section 6)
```

### Step 2: Start PostgreSQL and Redis

```bash
docker compose up postgres redis -d
```

> **Lưu ý:** Docker Compose dùng `redis/redis-stack-server` (có RedisJSON + RediSearch) — bắt buộc cho `langgraph-checkpoint-redis`.

### Step 3: Pull AI models (Ollama)

```bash
# Root orchestrator – text model (example)
ollama pull qwen2.5:7b

# Start Ollama server
ollama serve
```

### Step 4: Run the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Tables are created automatically on first startup. Redis indexes are created by `langgraph-checkpoint-redis` on first graph compilation.

### Step 5: Open the UI

Open `frontend/index.html` with **Live Server** (VS Code) or:

```bash
cd frontend
python3 -m http.server 5500
# Open: http://localhost:5500
```

### Docker Compose (all-in-one)

```bash
docker compose up --build
# Backend: http://localhost:8000
# API docs: http://localhost:8000/api/docs
```

---

## 6. Configuration

All settings are read from `backend/.env`. See `.env.example` for the full list.

### Redis (LangGraph checkpoint)

```env
REDIS_URL=redis://localhost:6379
```

Graph state (conversation context, intent, category, slots) được persist trong Redis. Nếu Redis không kết nối được, backend tự fallback `MemorySaver` (mất state khi restart).

### LLM provider

#### Option A: Google Gemini (API key — default)

Use **Gemini 2.5** stable IDs — `gemini-2.0-flash` is deprecated for new API keys ([models doc](https://ai.google.dev/gemini-api/docs/models)).

```env
LLM_PROVIDER=google
GOOGLE_API_KEY=your-key
GOOGLE_ROOT_MODEL=gemini-2.5-flash-lite
GOOGLE_SPECIALIST_MODEL=gemini-2.5-flash-lite
```

Get a key at [Google AI Studio](https://aistudio.google.com/apikey).

| Model ID | Typical use |
|----------|-------------|
| `gemini-2.5-flash-lite` | Default — lowest cost/latency for routing + intake |
| `gemini-2.5-flash` | Better reasoning / structured JSON if needed |
| `gemini-2.5-pro` | Heavier tasks only |

#### Option B: Ollama (local)

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
ROOT_MODEL_NAME=qwen2.5:7b
SPECIALIST_MODEL_NAME=qwen2.5:7b
```

#### Option C: OpenAI API

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_ROOT_MODEL=gpt-4o-mini
OPENAI_SPECIALIST_MODEL=gpt-4o
```

#### Option D: OpenAI-compatible (Together, Groq, vLLM, LM Studio, …)

```env
LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://api.together.xyz/v1
OPENAI_COMPATIBLE_API_KEY=<your-key>
OPENAI_COMPATIBLE_ROOT_MODEL=Qwen/Qwen2.5-7B-Instruct-Turbo
OPENAI_COMPATIBLE_SPECIALIST_MODEL=Qwen/Qwen2.5-7B-Instruct-Turbo
```

### Merging control (limit follow-up questions)

```env
MAX_FOLLOW_UP_QUESTIONS=3
```

---

## 7. LangGraph multi-agent design

### Graph state (`AgentState`)

See `backend/app/agents/state.py` for the full TypedDict. Important fields:

| Field | Role |
|-------|------|
| `messages` | Conversation (reducer `add_messages`) |
| `intent` | `consultation` \| `select_slot` \| `confirm_appointment` \| `general` |
| `symptoms_summary`, `category_code`, `specialist_concluded`, `triage_complete` | After specialist / save_intake |
| `intake_id`, `available_slots`, `pending_confirmation_slot` | Booking handoff |
| `booking_confirmed`, `reservation_id`, `skip_root_respond` | Confirm path |
| `follow_up_count` | Caps specialist follow-up questions |

> **Lưu ý DB:** Cột trong PostgreSQL vẫn tên `dental_case_code` (tránh migration). Logic code dùng `category_code` và map khi ghi DB.

### Flow (each user message)

```
START → classify_intent
  ├── consultation → dental_specialist → (concluded?) → save_intake → query_slots → root_respond → END
  ├── select_slot  → dental_specialist if not triage_complete
  │                → confirm_booking if intake + slots ready
  │                → booking_prepare → confirm_booking if prerequisites missing
  ├── confirm_appointment → confirm_booking → root_respond or END (skip_root_respond)
  └── general → root_respond → END
```

Diagrams: `frontend/lab-langgraph.html` (edges) and `frontend/lab-architecture.html` (SSE + DB + mock files).

### Merging control

1. `follow_up_count` increments each specialist turn.
2. When `follow_up_count >= MAX_FOLLOW_UP_QUESTIONS` (default 3), the specialist must conclude (`specialist_concluded=True`), and the graph continues to `save_intake → query_slots → root_respond`.

### State persistence (Redis)

- **Default**: `RedisSaver` từ `langgraph-checkpoint-redis` — state được persist trong Redis, giữ nguyên sau restart.
- **Fallback**: Nếu Redis không kết nối được, tự động chuyển sang `MemorySaver` (mất state khi restart).

```python
# graph.py — _create_checkpointer()
from langgraph.checkpoint.redis import RedisSaver
checkpointer = RedisSaver.from_conn_string(settings.REDIS_URL)
checkpointer.setup()
```

---

## 8. API reference

### Authentication

```bash
POST /api/v1/auth/register
{
  "username": "johndoe",
  "password": "password123",
  "full_name": "John Doe",
  "phone": "+84901234567"
}

POST /api/v1/auth/login
{ "username": "johndoe", "password": "password123" }
# → { "access_token": "eyJ...", "patient_user_id": 1, ... }
```

### Chat sessions

```bash
POST /api/v1/chat/sessions
Authorization: Bearer <token>

GET /api/v1/chat/sessions
Authorization: Bearer <token>

GET /api/v1/chat/sessions/{session_id}
Authorization: Bearer <token>

POST /api/v1/chat/sessions/{session_id}/messages
Content-Type: multipart/form-data
  message: "Tôi bị đau răng, ê buốt khi ăn đồ lạnh"
  authorization: "Bearer <token>"

POST /api/v1/chat/sessions/{session_id}/close
Authorization: Bearer <token>
```

### Schedule and reservations (mock week JSON)

```bash
# Slot trống theo ngày + category — JWT not required in dev
GET /api/v1/schedule/slots?date=2026-04-21&case=CAT-01

# Lịch cả tuần
GET /api/v1/schedule/week/slots?case=CAT-02&week_start=2026-04-20

GET /api/v1/schedule/reservations
Authorization: Bearer <token>
```

### Admin lab (dev)

```bash
POST /api/v1/admin/lab/agents/invoke
POST /api/v1/admin/lab/tools/invoke
GET  /api/v1/admin/lab/mock-schedule-summary
GET  /api/v1/admin/lab/triage-rubric
```

### SSE event types

```javascript
{ "type": "status", "message": "Processing..." }

{ "type": "token", "content": "Chào bạn" }

{
  "type": "done",
  "session_id": 1,
  "agent": "specialist",
  "booking": {
    "reservation_id": 5,
    "selected_slot": "14:00, 21/04/2026"
  },
  "intake": {
    "intake_id": 3,
    "ai_diagnosis": "...",
    "category_code": "CAT-01"
  }
}

{ "type": "error", "message": "..." }
```

---

## 9. User interface

Single-page app under `frontend/` (no build step).

| Feature | Description |
|---------|-------------|
| **Login / register** | JWT auth |
| **Sessions** | New session, history |
| **Lab links** | Sidebar: Admin lab, LangGraph diagram, Architecture / data flow |
| **Streaming** | Token stream with cursor |
| **Status text** | Node progress hints |
| **Booking card** | Reservation id and time after confirm |
| **Quick actions** | Suggested prompts |
| **Responsive** | Mobile-friendly |

Set the API base in `frontend/index.html`:

```html
<script>
  window.APP_CONFIG = { apiBase: "http://localhost:8000/api/v1" };
</script>
```

---

## 10. Observability (Langfuse)

1. Enable in `.env`:

```env
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

2. Or self-host (Docker):

```bash
docker compose --profile langfuse up langfuse postgres
# UI: http://localhost:3000
```

Traces include session id (thread id), node spans, LLM calls, and tool calls such as `get_mock_schedule`, `book_appointment`, `save_consult_intake`.

---

## 11. Production extensions

### Alembic (DB migrations)

```bash
cd backend
alembic init alembic
# Configure alembic.ini and env.py
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

### Scaling notes

- **Backend**: Gunicorn + Uvicorn workers.
- **Redis**: Shared across all workers (graph state + checkpoint).
- **Uploads**: S3 / GCS instead of local `uploads/`.
- **Queue**: Celery + Redis for heavy jobs.
- **Redis TTL**: Cấu hình TTL cho checkpoint để tự xóa session cũ (xem `RedisSaver(ttl=...)` trong `langgraph-checkpoint-redis`).

---

## 12. Troubleshooting

### Database connection error

```
sqlalchemy.exc.OperationalError: could not connect to server
```

Ensure Postgres is running:

```bash
docker compose up postgres -d
```

### Redis connection error

```
redis.exceptions.ConnectionError: Error connecting to redis://localhost:6379
```

Backend sẽ tự fallback sang MemorySaver, nhưng state sẽ mất khi restart. Để fix:

```bash
docker compose up redis -d
# Kiểm tra: redis-cli ping → PONG
```

> **Lưu ý:** Dùng `redis/redis-stack-server` (không phải `redis:7-alpine`), vì `langgraph-checkpoint-redis` cần RedisJSON + RediSearch.

### Ollama unreachable

```
httpx.ConnectError: [Errno 111] Connection refused
```

Start Ollama and pull models:

```bash
ollama serve
ollama pull qwen2.5:7b
```

### Port in use

Change `docker-compose.yml` or:

```bash
uvicorn app.main:app --port 8001
```

### Reset all volumes

```bash
docker compose down -v   # removes volumes (PostgreSQL data + Redis data)
docker compose up postgres redis -d
```

---

## License

MIT © 2026 – SmileCare AI MVP
