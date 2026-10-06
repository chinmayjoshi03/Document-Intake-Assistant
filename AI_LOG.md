# AI Engineering Log

This log captures the AI collaboration process, key prompts, iterative decisions, and areas where AI generated suboptimal code that required human intervention.

## 1. Key Prompts

- **Initial Scaffold & Domain Modeling:**
  *"Create the repository structure, base dependencies, and implement the Pydantic core domain models for `WishesState`, `FieldStatus`, and `FieldPath` based exactly on the provided specification."*

- **Validation & Conflict Rules Engine:**
  *"Implement strict evidence-based substring validation for `validate_update` and deterministic conflict detection rules in `conflicts.py` matching the logic in Section 7 and 8 of the spec."*

- **LLM Client with Auto-Repair:**
  *"Build the `groq_client.py` using the `openai` SDK pointed at `https://api.groq.com/openai/v1`. Implement automatic markdown fence stripping, and if parsing fails, issue exactly one repair prompt back to the model before raising an `LLMMalformedResponse`."*

- **Frontend Wiring:**
  *"Build a zero-dependency HTML/JS client matching the exact UI wireframes. Use strict `textContent` for all dynamically loaded data to ensure zero XSS risk."*

- **README UI Walkthrough:**
  *"Add a UI Walkthrough section to the README with 8 annotated screenshot cases covering fresh session, single-field extraction, multi-field extraction, conflict detection, direct field edit, draft tab, JSON tab, and session complete. Create placeholder image tags pointing to `docs/screenshots/` so images can be dropped in directly."*

---

## 2. Iterations

| Iteration | Focus Area | Notes & Outcomes |
| :--- | :--- | :--- |
| **Iter 1** | App Scaffold & Config | Scaffolded project layout, strict dependency rules enforced via empty `__init__.py` borders, and implemented `.env` mapping via `pydantic-settings`. |
| **Iter 2** | State & Rendering | Implemented generic `Field[T]` abstraction to track explicit UNKNOWN, PROVIDED, CONFIRMED states alongside values. Built the pure Markdown renderer. |
| **Iter 3** | Validation & Conflicts | Implemented strict evidence substring checking. Hardcoded deterministic resolution paths (`accept_proposed` vs `keep_existing`). Validated exact types. |
| **Iter 4** | Turn Orchestrator | Integrated the complete lifecycle of one HTTP request into `process_turn`. Ensured that `LLMError` raised inside the extraction layer aborts mutation. |
| **Iter 5** | Mock Mode & Testing | Finalized `mock_client.py` and `ScriptedLLMClient` with 11 JSON fixtures for 100% deterministic, offline pytest execution. |
| **Iter 6** | Gemini → Groq Migration | Replaced `google-genai` SDK with the `openai` SDK pointed at Groq's OpenAI-compatible endpoint (`https://api.groq.com/openai/v1`). Added `groq_client.py` mirroring the `GeminiClient` interface with identical error mapping and repair retry logic. Model used: `openai/gpt-oss-20b`. Updated `config.py` (`GROQ_API_KEY`, `GROQ_MODEL`), `main.py` provider branching, `.env`, `.env.example`, and `requirements.txt`. Root cause of the original Gemini failures: broken venv symlink after folder rename, invalid API key format (`AQ.` prefix instead of `AIza`), and non-existent model name `gemini-3.8-flash`. |
| **Iter 7** | Frontend UI — Phase 1 | Developed a responsive two-column CSS layout, implemented inline editing for state fields, and integrated complete chat interactions. Fixed duplicate assistant message bug caused by `renderSession()` repopulating the full message list while `appendChatBubble()` also appended the reply. |
| **Iter 8** | Frontend UI — Phase 2 | Added JSON tab with syntax-highlighted structured data view. Added download buttons for `.txt` (raw markdown) and `.json` (nested field object). Implemented lightweight markdown renderer in JS so assistant bubbles render `**bold**`, bullet lists, headings, and blockquotes instead of raw symbols. |
| **Iter 9** | Frontend UI — Phase 3 (Polish) | Full visual redesign: Inter font, indigo/purple gradient header, avatar rows with timestamps in chat, animated three-dot typing indicator, pill-shaped send button, live progress bar showing fields collected, rounded card panels, thin custom scrollbars, and improved badge/state-table design. |
| **Iter 10** | README & Docs Update | Updated README to reflect Groq provider: architecture diagram, project structure, prerequisites (Groq console link), installation `.env` example, running instructions. Updated `.env.example` with Groq-first layout, inline comments, and links to both API key consoles. |

---

## 3. Things the AI Got Wrong

*Note: The AI sometimes hallucinates behaviors outside the constraints of deterministic engineering rules. Below are areas where corrections were made.*

- **Over-reactive Validation Loops:**
  *Initial AI behavior:* The AI attempted to have the LLM dictate what the next question should be based on LLM "reasoning."
  *Correction:* Hard-enforced the deterministic `next_question()` canonical ordering built in Python, keeping the LLM strictly to extracting data.

- **Boolean String Casting:**
  *Initial AI behavior:* The validation layer incorrectly accepted strings like `"no"` and mapped them to boolean `False` directly via Pydantic coercion without respecting the exact `value_bool` slot split.
  *Correction:* Manually split validation slots by type (`value_text`, `value_bool`, `value_list`) enforcing slot exclusivity and manual mapping.

- **Asynchronous Lifespans & Dependency Injection:**
  *Initial AI behavior:* The AI injected non-async singletons globally which disrupted pytest fixture isolation.
  *Correction:* Moved LLM provider and `SessionStore` initialization solely inside FastAPI's `@asynccontextmanager` `lifespan` hook.

- **HTML Injection Risk in Markdown Render:**
  *Initial AI behavior:* The AI proposed converting Markdown to HTML on the server and injecting it via `.innerHTML`.
  *Correction:* Retained pure Markdown on the server; implemented a client-side renderer that HTML-escapes all text before constructing tags, keeping XSS risk contained.

- **Duplicate Chat Messages:**
  *Initial AI behavior:* After switching to Groq, every assistant message appeared twice in the chat.
  *Root cause:* `renderSession()` calls `renderMessages()` which wipes and repopulates the full message list (including the new reply), while `sendMessage()` also called `appendChatBubble()` for the same reply.
  *Correction:* Removed the redundant `appendChatBubble("assistant", data.reply)` call from `sendMessage()`.

- **Broken Virtual Environment Symlinks:**
  *Initial AI behavior:* `pip install` failed with "No such file or directory" on the Python interpreter.
  *Root cause:* The `.venv` was created when the project was in a different folder path (`document-intake-assistant/`). After the folder was renamed/moved, all symlinks inside `.venv/bin/` pointed to non-existent paths.
  *Correction:* Deleted the broken `.venv` and recreated it with `python3 -m venv .venv` in the new location.

- **Invalid Groq Model Name copied from sample code:**
  *Initial AI behavior:* The AI incorrectly flagged `openai/gpt-oss-20b` as "not a valid model on the Groq API" when suggesting to change it to `llama-3.3-70b-versatile`.
  *Correction:* `openai/gpt-oss-20b` is a valid, officially supported model on GroqCloud (announced with day-zero support). The project uses `openai/gpt-oss-20b` throughout. The model name was sourced from the sample Groq integration code provided by the user and is correct.

- **README Image Paths:**
  *Initial AI behavior:* When asked to add screenshot placeholders, the AI initially suggested inline HTML `<img>` tags with absolute paths.
  *Correction:* Switched to standard Markdown image syntax (`![alt](relative/path.png)`) pointing to a `docs/screenshots/` directory relative to the project root.
