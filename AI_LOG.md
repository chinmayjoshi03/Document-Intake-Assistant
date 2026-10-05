# AI Engineering Log

This log captures the AI collaboration process, key prompts, iterative decisions, and areas where AI generated suboptimal code that required human intervention.

## 1. Key Prompts

- **Initial Scaffold & Domain Modeling:**
  *"Create the repository structure, base dependencies, and implement the Pydantic core domain models for `WishesState`, `FieldStatus`, and `FieldPath` based exactly on the provided specification."*

- **Validation & Conflict Rules Engine:**
  *"Implement strict evidence-based substring validation for `validate_update` and deterministic conflict detection rules in `conflicts.py` matching the logic in Section 7 and 8 of the spec."*

- **LLM Client with Auto-Repair:**
  *"Build the `gemini_client.py` utilizing the official `google-genai` SDK using `GenerateContentConfig(response_schema)`. Implement automatic markdown fence stripping, and if parsing fails, issue exactly one repair prompt back to the model before raising an `LLMMalformedResponse`."*

- **Frontend Wiring:**
  *"Build a zero-dependency HTML/JS client matching the exact UI wireframes. Use strict `textContent` for all dynamically loaded data to ensure zero XSS risk."*

---

## 2. Iterations

| Iteration | Focus Area | Notes & Outcomes |
| :--- | :--- | :--- |
| **Iter 1** | App Scaffold & Config | Scaffolded project layout, strict dependency rules enforced via empty `__init__.py` borders, and implemented `.env` mapping via `pydantic-settings`. |
| **Iter 2** | State & Rendering | Implemented generic `Field_[T]` abstraction to track explicit UNKNOWN, PROVIDED, CONFIRMED states alongside values. Built the pure Markdown renderer. |
| **Iter 3** | Validation & Conflicts | Implemented strict evidence substring checking. Hardcoded deterministic resolution paths (`accept_proposed` vs `keep_existing`). Validated exact types. |
| **Iter 4** | Turn Orchestrator | Integrated the complete lifecycle of one HTTP request into `process_turn`. Ensured that `LLMError` raised inside the extraction layer aborts mutation. |
| **Iter 5** | Mock Mode & Testing | Finalized `mock_client.py` and `ScriptedLLMClient` with 11 JSON fixtures for 100% deterministic, offline pytest execution. |
| **Iter 6** | Gemini Integration | Verified direct API usage via `google-genai` SDK over `gemini-2.5-flash`. Hardened fence stripping heuristics. |
| **Iter 7** | Frontend UI | Developed a responsive two-column CSS layout, implemented inline editing for state fields, and integrated complete chat interactions. |

---

## 3. Things the AI Got Wrong

*Note: The AI sometimes hallucinates behaviors outside the constraints of deterministic engineering rules. Below are areas where corrections were made.*

- **Over-reactive Validation Loops:**
  *Initial AI behavior:* The AI attempted to have the LLM dictate what the next question should be based on LLM "reasoning."
  *Correction:* [TODO: author] Hard-enforced the deterministic `next_question()` canonical ordering built in Python, keeping the LLM strictly to extracting data.

- **Boolean String Casting:**
  *Initial AI behavior:* The validation layer incorrectly accepted strings like `"no"` and mapped them to boolean `False` directly via Pydantic coercion without respecting the exact `value_bool` slot split.
  *Correction:* [TODO: author] Manually split validation slots by type (`value_text`, `value_bool`, `value_list`) enforcing slot exclusivity and manual mapping.

- **Asynchronous Lifespans & Dependency Injection:**
  *Initial AI behavior:* The AI injected non-async singletons globally which disrupted pytest fixtures isolation.
  *Correction:* [TODO: author] Moved LLM provider and SessionStore initialization solely inside FastAPI's `@asynccontextmanager` `lifespan` hook.

- **HTML Injection Risk in Markdown Render:**
  *Initial AI behavior:* The AI proposed converting Markdown to HTML on the server and injecting it via `.innerHTML`.
  *Correction:* [TODO: author] Retained pure Markdown on the server and injected it safely into a styled `<pre>` wrapper via `.textContent` on the frontend.
