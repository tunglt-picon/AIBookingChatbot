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
┌─────────────────────────────────────────────────────────┐
│                      CLIENT LAYER                       │
│              Web App (HTML / JS / Tailwind)             │
└────────────────────────┬────────────────────────────────┘
                         │  REST / SSE (streaming)
┌────────────────────────▼────────────────────────────────┐
│              API GATEWAY / BACKEND (FastAPI)             │
│    Auth (JWT) · Rate limit · Upload · SSE streaming     │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              MULTI AGENT (LangGraph)                    │
│  ┌─────────────────────┐   ┌─────────────────────────┐ │
│  │  ROOT ORCHESTRATOR  │   │   DENTAL SPECIALIST     │ │
│  │   (text model)      │◄──│      (VLM)             │ │
│  │ · Intent classify   │   │ · Image + text intake   │ │
│  │ · Slot presentation │   │ · Symptom collection    │ │
│  │ · Booking confirm   │   │ · Structured intake     │ │
│  └────────┬────────────┘   └─────────────────────────┘ │
│           │  Tools                                       │
│  ┌────────▼──────────────────────────────────────────┐  │
│  │  TOOL LAYER: get_available_slots · book_appointment│  │
│  │              save_consult_intake                   │  │
│  └───────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                    DATA LAYER                           │
│    PostgreSQL (users, sessions, intakes, reservations)  │
│    Redis (image cache, rate limiting, session state)    │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Tech stack

| Component | Technology | Notes |
|-----------|------------|-------|
| **Backend** | FastAPI 0.115 + Uvicorn | Async, SSE streaming |
| **ORM** | SQLAlchemy 2.0 (async) | asyncpg driver |
| **Database** | PostgreSQL 16 | Docker Compose |
| **Cache / state** | Redis 7 | Image cache, rate limit |
| **Agents** | LangGraph 0.2 | Stateful multi-turn graph |
| **Root agent** | Configurable (e.g. Qwen via Ollama) | Intent + booking UX |
| **Specialist agent** | Configurable VLM (e.g. LLaVA / MedGemma) | Vision + intake |
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
│   │   │   └── schedule.py      # /schedule/slots, /schedule/reservations
│   │   │
│   │   ├── agents/              # LangGraph multi-agent system
│   │   │   ├── state.py         # AgentState TypedDict
│   │   │   ├── llm_factory.py   # LLM provider abstraction
│   │   │   ├── root_orchestrator.py  # classify_intent, root_respond, booking_prepare, etc.
│   │   │   ├── dental_specialist.py  # VLM intake node
│   │   │   └── graph.py         # StateGraph definition & compilation
│   │   │
│   │   ├── tools/               # LangChain tools (called by agents)
│   │   │   ├── schedule_tools.py  # get_available_slots, book_appointment
│   │   │   └── intake_tools.py    # save_consult_intake
│   │   │
│   │   ├── services/            # Business logic
│   │   │   ├── auth_service.py
│   │   │   ├── chat_service.py
│   │   │   └── redis_service.py
│   │   │
│   │   └── observability/
│   │       ├── langfuse_client.py   # Langfuse tracing wrapper
│   │       └── eval_placeholders.py # DeepEval/Ragas stubs
│   │
│   ├── uploads/                 # Uploaded images (gitignored)
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── index.html               # Single-page app (Tailwind + vanilla JS)
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

# Dental specialist – vision model (pick one)
ollama pull alibayram/medgemma:4b
# ollama pull llava-med:latest   # if available

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

```python
class AgentState(TypedDict):
    session_id: int
    patient_user_id: int
    messages: Annotated[list[AnyMessage], add_messages]  # merged by LangGraph
    intent: str          # "consultation" | "select_slot" | "general"
    image_base64: str    # optional dental image (multimodal)
    symptoms_summary: str
    ai_diagnosis: str
    needs_visit: bool
    follow_up_count: int  # merging control
    intake_id: int
    available_slots: list[dict]
    booking_confirmed: bool
    ...
```

### Flow (each user message)

```
START
  │
  ▼
classify_intent          ← Root orchestrator
  │
  ├── "consultation"
  │      ▼
  │   dental_specialist
  │      ├── needs_visit=False → END  (reply to user, wait next turn)
  │      └── needs_visit=True
  │             ▼
  │          save_intake → query_slots → root_respond → END
  │
  ├── "select_slot"
  │      ├── missing intake or slots → booking_prepare → confirm_booking
  │      └── otherwise → confirm_booking
  │             ▼
  │          root_respond → END
  │
  └── "general"
         ▼
      root_respond → END
```

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
  image: <file>   (optional)

POST /api/v1/chat/sessions/{session_id}/close
Authorization: Bearer <token>
```

### Schedule and reservations

```bash
GET /api/v1/schedule/slots?date=2026-03-25
Authorization: Bearer <token>

GET /api/v1/schedule/reservations
Authorization: Bearer <token>
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
| **Image upload** | Drag-and-drop or file picker, preview |
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

Traces include session id (thread id), node spans, LLM calls, and tool calls (`get_available_slots`, `book_appointment`, `save_consult_intake`).

---

## 10. Quality evaluation (Eval)

See `backend/app/observability/eval_placeholders.py` for DeepEval/Ragas stubs.

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

### Vision not working

If the model is text-only, images are ignored. Use a VLM (e.g. `llava:7b`, `gpt-4o`, or an OpenAI-compatible vision model).

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
