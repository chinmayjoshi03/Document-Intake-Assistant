# Document Intake Assistant

A conversational assistant that interviews users to populate a structured **Personal Wishes Document**.

The core design principle is **"The LLM proposes — code decides"**:
- The LLM extracts structured update proposals from user natural language messages.
- Deterministic Python code handles all validation, type coercion, conflict detection, question progression, and document rendering.
- Structured domain state is the single source of truth; conversation transcripts are not used as state.

> **Disclaimer:** FICTIONAL DOCUMENT. NOT LEGAL ADVICE. For demonstration purposes only.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (static)                 │
│         index.html  app.js  styles.css              │
└────────────────────┬────────────────────────────────┘
                     │ HTTP
┌────────────────────▼────────────────────────────────┐
│                   api/routes.py                     │
│              (HTTP only, no business logic)         │
└────────────────────┬────────────────────────────────┘
                     │ calls
┌────────────────────▼────────────────────────────────┐
│               domain/turn.py                        │
│  (orchestrates one turn: validate→conflict→apply    │
│   →choose question→return response)                 │
├──────────┬──────────────────┬───────────────────────┤
│ updates  │   conflicts      │   questions           │
│ .py      │   .py            │   .py                 │
├──────────┴──────────────────┴───────────────────────┤
│               domain/state.py                       │
│  (WishesState, Field[T], FieldPath, helpers)        │
└─────────────────────────────────────────────────────┘
         ▲                          ▲
         │ ExtractionRequest/       │ render
         │ Response only            │
┌────────┴──────────┐   ┌──────────┴──────────────────┐
│    llm/           │   │   documents/renderer.py     │
│  base.py          │   │   (pure fn, no LLM)         │
│  gemini_client.py │   └─────────────────────────────┘
│  mock_client.py   │
│  prompts.py       │
└───────────────────┘
```

### Layering & Dependency Rules

1. **`domain/`** contains pure business logic and models. It never imports from `llm/`, `api/`, or `documents/`.
2. **`documents/`** depends solely on `domain/state.py`. It is a pure, deterministic rendering function with no clock calls or network access.
3. **`llm/`** depends only on `domain/state.py` (`FieldPath` enum). All LLM SDK dependencies (`google-genai`) are isolated inside `llm/gemini_client.py`.
4. **`api/`** depends only on `domain/turn.py`, `domain/session.py`, and `app/config.py`. It maps HTTP requests to turn execution and serializes responses.

---

## Project Structure

```
document-intake-assistant/
├── .env.example
├── .gitignore
├── README.md
├── PRODUCTION_NOTES.md
├── AI_LOG.md
├── frontend/
│   ├── index.html          # Semantic two-column UI layout
│   ├── styles.css          # Responsive styling (no external CSS framework)
│   └── app.js              # Thin client API connector (XSS-safe textContent rendering)
└── backend/
    ├── requirements.txt
    ├── pytest.ini
    ├── app/
    │   ├── __init__.py
    │   ├── config.py       # Pydantic Settings (.env configuration)
    │   ├── main.py         # FastAPI factory and static asset mounting
    │   ├── api/
    │   │   ├── __init__.py
    │   │   ├── routes.py   # REST endpoints & HTTP error mapping
    │   │   └── schemas.py  # Pydantic request/response schemas
    │   ├── domain/
    │   │   ├── __init__.py
    │   │   ├── state.py    # WishesState, FieldStatus, FieldPath
    │   │   ├── updates.py  # Update validation & evidence verification
    │   │   ├── conflicts.py# Contradiction detection & resolution
    │   │   ├── questions.py# Question bank & deterministic sequencing
    │   │   ├── session.py  # In-memory session store & audit logging
    │   │   └── turn.py     # Turn pipeline orchestration
    │   ├── documents/
    │   │   ├── __init__.py
    │   │   └── renderer.py # Pure Markdown document generator
    │   └── llm/
    │       ├── __init__.py
    │       ├── base.py     # Extraction schemas, Protocol, error hierarchy
    │       ├── prompts.py  # Prompt templates and repair prompts
    │       ├── mock_client.py   # Offline regex-based heuristic extractor
    │       └── gemini_client.py # Gemini 2.5 Flash client with repair retry
    └── tests/
        ├── conftest.py     # ScriptedLLMClient and fixture loaders
        ├── fixtures/       # 11 deterministic scenario fixtures
        ├── test_state.py
        ├── test_renderer.py
        ├── test_updates.py
        ├── test_conflicts.py
        ├── test_questions.py
        ├── test_mock_client.py
        ├── test_turn.py
        ├── test_api.py
        └── test_gemini_client.py
```

---

## Getting Started

### Prerequisites

- Python 3.9+
- pip

### Installation

1. Navigate to the backend directory and set up a virtual environment:

```bash
cd document-intake-assistant/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment variables (optional for mock mode):

```bash
cp ../.env.example .env
```

Edit `.env` if you want to use the live Gemini API:
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-actual-api-key
GEMINI_MODEL=gemini-2.5-flash
LLM_TIMEOUT_SECONDS=20
```

---

## Running the Application

### 1. Offline / Mock Mode (No API key needed)

```bash
cd document-intake-assistant/backend
source .venv/bin/activate
LLM_PROVIDER=mock uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

### 2. Gemini Live Mode

```bash
cd document-intake-assistant/backend
source .venv/bin/activate
GEMINI_API_KEY="your-api-key" LLM_PROVIDER=gemini uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

---

## Running Tests

Run the complete test suite (195 tests, zero network calls required):

```bash
cd document-intake-assistant/backend
source .venv/bin/activate
pytest
```

---

## API Summary & Examples

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health status and LLM provider configuration |
| `POST` | `/api/sessions` | Create a new intake session with greeting |
| `GET` | `/api/sessions/{id}` | Get current session state, messages, and draft |
| `POST` | `/api/sessions/{id}/messages` | Send a user message and advance the turn |
| `PATCH` | `/api/sessions/{id}/state` | Direct edit field values (bypasses evidence check) |
| `POST` | `/api/sessions/{id}/reset` | Reset state to empty |

### Example `curl` Commands

#### Create a Session
```bash
curl -X POST http://localhost:8000/api/sessions
```
Response:
```json
{
  "session_id": "019234ab-cdef-7000-8000-000000000001",
  "reply": "Hello! I will help you record your personal wishes for your document. What is your full legal name?",
  "missing_fields": ["full_name", "home_address", "covers_worldwide_assets", "has_children", "executor.name", "executor.relationship", "specific_gifts", "additional_wishes"],
  "is_complete": false
}
```

#### Send a Message
```bash
curl -X POST http://localhost:8000/api/sessions/019234ab-cdef-7000-8000-000000000001/messages \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is Jane Doe and I live at 123 Main St."}'
```

#### Direct State Edit (PATCH)
```bash
curl -X PATCH http://localhost:8000/api/sessions/019234ab-cdef-7000-8000-000000000001/state \
  -H "Content-Type: application/json" \
  -d '{
    "updates": [
      {"field": "has_children", "value_bool": false}
    ]
  }'
```

---

## Key Design Decisions

1. **Propose / Decide Separation:**
   The LLM is constrained to output structured JSON adhering to `ExtractionResponse`. It cannot directly update session state or decide the next action. Python code validates all updates before applying them.

2. **Evidence Substring Verification:**
   Every proposed text or list value from the LLM must have a corresponding non-empty `evidence` quote matching a substring in the user message. This prevents hallucinations and ungrounded updates.

3. **Deterministic Question Sequencing:**
   The assistant's next question is determined strictly by evaluating `WishesState.missing_paths()` against the canonical field ordering. If the user provides multiple fields in one message, the system skips all filled fields automatically.

4. **Pure Document Rendering:**
   The markdown document generator is a deterministic function `render_document(state, date)`. It takes the current state and a date parameter with no hidden dependencies or side effects.

---

## Known Limitations

- **In-Memory Session Storage:** Active sessions are held in a memory dictionary protected with `asyncio.Lock`. Server restarts will clear active sessions.
- **Single-Process Deployment:** Designed to run in a single process hosting both FastAPI endpoints and static frontend assets.
- **No User Authentication:** Intended as an intake prototype; production use requires tenant isolation and authentication.
