# SmileCare AI – Smart Dental Booking Assistant

> **Full MVP** — Multi-agent AI with LangGraph, FastAPI, PostgreSQL, Redis, and a modern chat UI.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Tech stack](#2-tech-stack)
3. [Project structure](#3-project-structure)
4. [Quick start](#4-quick-start)
5. [Configuration](#5-configuration)
6. [LangGraph multi-agent design](#6-langgraph-multi-agent-design)
7. [API reference](#7-api-reference)
8. [User interface](#8-user-interface)
9. [Observability (Langfuse)](#9-observability-langfuse)
10. [Quality evaluation (Eval)](#10-quality-evaluation-eval)
11. [Production extensions](#11-production-extensions)
12. [Troubleshooting](#12-troubleshooting)

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
│  LangGraph (MemorySaver) — classify_intent → … → END       │
│  · Root LLM: intent, FAQ, slot wording, booking UX         │
│  · Dental specialist LLM: text intake + triage rubric JSON │
│  · Tools: get_mock_schedule, save_consult_intake,          │
│           book_appointment (+ helpers in schedule_tools)   │
└───────────────────────────┬────────────────────────────────┘
         ┌──────────────────┴──────────────────┐
         ▼                                      ▼
┌─────────────────┐                 ┌─────────────────────────┐
│  PostgreSQL     │                 │  Mock JSON (dev)        │
│  users, sessions│                 │  lich_trong_tuan…json   │
│  messages,      │                 │  triage_symptom_rubric… │
│  intakes, resv  │                 │  (+ triage_examples.tsv) │
└─────────────────┘                 └─────────────────────────┘

Optional: Redis container in Docker Compose is **not used by app code** yet (reserved for future cache / rate limit).
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
| **Redis (Compose)** | Redis 7 | Optional sidecar — **not imported in Python** yet |
| **Agents** | LangGraph 0.2 | Stateful multi-turn graph |
| **Root agent** | Configurable text LLM (Ollama / OpenAI / compatible) | Intent + booking UX |
| **Specialist agent** | Configurable **text** LLM | Symptom intake + `dental_case_code` (rubric in `data/mock/`) |
| **Observability** | Langfuse | Trace LLM calls |
| **Eval (stub)** | DeepEval / Ragas | Placeholders, ready to wire |
| **Auth** | JWT (python-jose) + bcrypt | Stateless |
| **Frontend** | HTML + Tailwind + vanilla JS | No build step |
| **Containers** | Docker + Docker Compose | Dev and prod |

---

## 3. Project structure

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
│   │   │   ├── root_orchestrator.py  # classify_intent, root_respond, booking_prepare, etc.
│   │   │   ├── dental_specialist.py  # Text intake node + rubric prompt
│   │   │   └── graph.py         # StateGraph definition & compilation
│   │   │
│   │   ├── domain/              # Dental case profiles (duration, windows)
│   │   │   └── dental_cases.py
│   │   │
│   │   ├── tools/               # LangChain @tool (+ helpers)
│   │   │   ├── schedule_tools.py  # get_mock_schedule, book_appointment, resolve_requested_slot, …
│   │   │   └── intake_tools.py    # save_consult_intake
│   │   │
│   │   ├── services/            # Business logic
│   │   │   ├── auth_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── mock_week_schedule_loader.py
│   │   │   └── triage_rubric_loader.py
│   │   │
│   │   └── observability/
│   │       ├── langfuse_client.py   # Langfuse tracing wrapper
│   │       └── eval_placeholders.py # DeepEval/Ragas stubs
│   │
│   ├── data/mock/               # lich_trong_tuan_trong_vi.json, triage_*.json/.tsv
│   ├── uploads/                 # Static uploads dir (gitignored)
│   ├── requirements.txt
│   └── Dockerfile
│
├── scripts/                     # Repo root helpers
│   └── build_triage_symptom_rubric.py  # rebuild triage_symptom_rubric_vi.json from TSV
│
├── frontend/
│   ├── index.html               # Single-page app (Tailwind + vanilla JS)
│   ├── admin.html               # Admin lab (agents / tools / REST)
│   ├── lab-architecture.html    # Architecture + data flow (Mermaid)
│   ├── lab-langgraph.html       # LangGraph diagram
│   ├── js/
│   │   ├── api.js               # API client (Auth, Chat, Schedule)
│   │   └── app.js               # UI logic, SSE handler
│   └── css/
│       └── custom.css           # Animations, custom components
│
├── docker-compose.yml
├── .env.example                 # Template – copy to backend/.env
├── .gitignore
└── README.md
```

---

## 4. Quick start

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
# Edit backend/.env as needed (see section 5)
```

### Step 2: Start PostgreSQL and Redis

```bash
docker compose up postgres redis -d
```

### Step 3: Pull AI models (Ollama)

```bash
# Root orchestrator – text model (example)
ollama pull qwen3.5:9b

# Dental specialist – text model (can match root model)
ollama pull qwen2.5:7b

# Start Ollama server
ollama serve
```

### Step 4: Run the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Tables are created automatically on first startup.

### Step 5: Open the UI

Open `frontend/index.html` with **Live Server** (VS Code) or:

```bash
cd frontend
python -m http.server 5500
# Open: http://localhost:5500
```

### Docker Compose (all-in-one)

```bash
docker compose up --build
# Backend: http://localhost:8000
# API docs: http://localhost:8000/api/docs
```

---

## 5. Configuration

All settings are read from `backend/.env`. See `.env.example` for the full list.

### LLM provider

#### Option A: Ollama (local, default)

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
ROOT_MODEL_NAME=qwen2.5:7b
SPECIALIST_MODEL_NAME=llava:7b
```

#### Option B: OpenAI API

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_ROOT_MODEL=gpt-4o-mini
OPENAI_SPECIALIST_MODEL=gpt-4o
```

#### Option C: OpenAI-compatible (Together, Groq, vLLM, LM Studio, …)

```env
LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://api.together.xyz/v1
OPENAI_COMPATIBLE_API_KEY=<your-key>
OPENAI_COMPATIBLE_ROOT_MODEL=Qwen/Qwen2.5-7B-Instruct-Turbo
OPENAI_COMPATIBLE_SPECIALIST_MODEL=meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo
```

### Merging control (limit follow-up questions)

```env
# Max follow-up questions from the specialist before forcing a conclusion
MAX_FOLLOW_UP_QUESTIONS=3
```

---

## 6. LangGraph multi-agent design

### Graph state (`AgentState`)

See `backend/app/agents/state.py` for the full TypedDict. Important fields:

| Field | Role |
|-------|------|
| `messages` | Conversation (reducer `add_messages`) |
| `intent` | `consultation` \| `select_slot` \| `confirm_appointment` \| `general` |
| `symptoms_summary`, `dental_case_code`, `triage_complete` | After specialist / save_intake |
| `intake_id`, `available_slots`, `pending_confirmation_slot` | Booking handoff |
| `booking_confirmed`, `reservation_id`, `skip_root_respond` | Confirm path |
| `follow_up_count` | Caps specialist follow-up questions |

### Flow (each user message)

```
START → classify_intent
  ├── consultation → dental_specialist → (needs_visit?) → save_intake → query_slots → root_respond → END
  ├── select_slot  → dental_specialist if not triage_complete
  │                → confirm_booking if intake + slots ready
  │                → booking_prepare → confirm_booking if prerequisites missing
  ├── confirm_appointment → confirm_booking → root_respond or END (skip_root_respond)
  └── general → root_respond → END
```

Diagrams: `frontend/lab-langgraph.html` (edges) and `frontend/lab-architecture.html` (SSE + DB + mock files).

### Merging control

1. `follow_up_count` increments each specialist turn.
2. When `follow_up_count >= MAX_FOLLOW_UP_QUESTIONS` (default 3), the specialist must conclude, `needs_visit` is set, and the graph continues to `save_intake → query_slots → root_respond`.

### State persistence

- **MVP**: `MemorySaver` (in-process; lost on server restart).
- **Production**: use `AsyncPostgresSaver` or `AsyncRedisSaver`.

```python
# graph.py – production upgrade (example)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
checkpointer = AsyncPostgresSaver.from_conn_string(settings.DATABASE_URL)
```

---

## 7. API reference

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
  message: "I'd like to book a cleaning"
  authorization: "Bearer <token>"
  # Text only today — no image field on this endpoint.

POST /api/v1/chat/sessions/{session_id}/close
Authorization: Bearer <token>
```

### Schedule and reservations (mock week JSON)

```bash
# Day slots from mock file — JWT not required in dev
GET /api/v1/schedule/slots?date=2026-03-30&case=SCALING

GET /api/v1/schedule/week/slots?case=CAVITY&week_start=2026-03-30

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

{ "type": "token", "content": "Hello" }

{
  "type": "done",
  "session_id": 1,
  "agent": "specialist",
  "booking": {
    "reservation_id": 5,
    "selected_slot": "14:00, 24/03/2026"
  },
  "intake": {
    "intake_id": 3,
    "needs_visit": true,
    "ai_diagnosis": "..."
  }
}

{ "type": "error", "message": "..." }
```

---

## 8. User interface

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
| **Image lightbox** | Full-size image |
| **Responsive** | Mobile-friendly |

Set the API base in `frontend/index.html`:

```html
<script>
  window.APP_CONFIG = { apiBase: "http://localhost:8000/api/v1" };
</script>
```

---

## 9. Observability (Langfuse)

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

## 10. Quality evaluation (Eval)

See `backend/app/observability/eval_placeholders.py` for DeepEval/Ragas **stubs**. They are **not** invoked from the chat pipeline until you wire them yourself.

### Example DeepEval metric

```python
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

metric = GEval(
    name="SymptomCoverage",
    criteria="Does extracted_symptoms cover everything the patient mentioned?",
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
)
```

### Tracked metrics (planned)

| Metric | Description |
|--------|-------------|
| `symptom_extraction_accuracy` | Symptom capture quality |
| `diagnosis_relevance` | Alignment of intake summary with symptoms |
| `booking_success_rate` | Sessions ending in a booking |
| `follow_up_efficiency` | Avg follow-ups before conclusion |

---

## 11. Production extensions

### Postgres checkpointer

```python
# backend/app/agents/graph.py
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def get_graph():
    async with AsyncPostgresSaver.from_conn_string(
        settings.DATABASE_URL.replace("+asyncpg", "")
    ) as checkpointer:
        await checkpointer.setup()
        return _build_graph().compile(checkpointer=checkpointer)
```

### Alembic

```bash
cd backend
alembic init alembic
# Configure alembic.ini and env.py
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

### Scaling notes

- **Backend**: Gunicorn + Uvicorn workers.
- **Checkpointer**: Redis or Postgres shared across workers.
- **Uploads**: S3 / GCS instead of local `uploads/`.
- **Queue**: Celery + Redis for heavy jobs.

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

### Ollama unreachable

```
httpx.ConnectError: [Errno 111] Connection refused
```

Start Ollama and pull models:

```bash
ollama serve
ollama pull qwen2.5:7b
ollama pull llava:7b
```

### Port in use

Change `docker-compose.yml` or:

```bash
uvicorn app.main:app --port 8001
```

### Reset database volumes

```bash
docker compose down -v   # removes volumes
docker compose up postgres redis -d
```

---

## License

MIT © 2026 – SmileCare AI MVP
