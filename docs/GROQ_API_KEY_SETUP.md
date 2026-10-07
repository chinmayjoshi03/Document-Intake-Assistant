# Getting a Groq API Key

This guide walks you through creating a free Groq API key to use with the Document Intake Assistant.

---

## Step 1 — Create a Groq account

1. Open [https://console.groq.com](https://console.groq.com) in your browser.
2. Click **Sign Up** (top-right corner).
3. Sign up with your Google account, GitHub account, or email address.
4. Complete email verification if prompted.

---

## Step 2 — Generate an API key

1. After logging in, click **API Keys** in the left sidebar.

   ![API Keys in sidebar](screenshots/groq_sidebar_api_keys.png)
   *(If you don't see the screenshot, navigate to: Console → API Keys)*

2. Click **Create API Key**.
3. Give it a name (e.g. `document-intake-assistant`).
4. Click **Submit**.
5. **Copy the key immediately** — it starts with `gsk_` and is only shown once.

   > ⚠ Store it somewhere safe (e.g. a password manager). You cannot retrieve it again after closing the dialog.

---

## Step 3 — Add the key to your `.env` file

In the project root, open (or create) `.env` and set:

```env
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-20b
LLM_TIMEOUT_SECONDS=20
```

You can copy `.env.example` as a starting point:

**macOS / Linux**
```bash
cp .env.example .env
```

**Windows**
```powershell
copy .env.example .env
```

Then replace `gsk_your_key_here` with the key you copied in Step 2.

---

## Step 4 — Verify it works

Start the backend and open the app:

**macOS / Linux**
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

**Windows**
```powershell
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). The header should show no warning banner, and messages should receive real AI responses.

---

## Free tier limits

Groq offers a free tier with generous rate limits for development:

| Limit | Value (free tier) |
|---|---|
| Requests per minute | 30 |
| Tokens per minute | 6,000 |
| Tokens per day | 500,000 |

For more details see the [Groq rate limits page](https://console.groq.com/docs/rate-limits).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `503 Service Unavailable` on messages | Key not set or wrong format | Confirm `GROQ_API_KEY` starts with `gsk_` and is in `.env` |
| `AI service not configured` banner | `LLM_PROVIDER` not set to `groq` | Set `LLM_PROVIDER=groq` in `.env` |
| `429 Too Many Requests` | Free tier rate limit hit | Wait ~60 seconds and retry, or upgrade your Groq plan |
| Key accepted but wrong answers | Wrong model name | Confirm `GROQ_MODEL=openai/gpt-oss-20b` |

---

## No API key? Use mock mode

If you just want to explore the UI without an API key, run the app in mock mode — no key required:

**macOS / Linux**
```bash
LLM_PROVIDER=mock uvicorn app.main:app --reload --port 8000
```

**Windows**
```powershell
set LLM_PROVIDER=mock && uvicorn app.main:app --reload --port 8000
```
