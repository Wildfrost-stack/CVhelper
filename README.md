# CVhelper — connecting the three files

## How the pieces fit

- **`cvhelper.html`** — the whole website. It's a single static file with a
  `fetch()` call baked in:
  `POST http://localhost:8000/api/audit` with `job_role` + either `text` or
  a PDF `file`, expecting back
  `{ sanitized_text, entity_types, entities_redacted, scores: [{label, score}], review, llm_provider }`.
  If that call fails for any reason, it silently falls back to a fake
  in-browser mock — which is why it "worked" before but never touched your
  real backend.
- **`app.py`** — the FastAPI server. Originally it only exposed
  `/api/v1/audit` (JSON body, requires a JWT login) — a different shape and
  a different URL than what the HTML calls. I added a new **`POST /api/audit`**
  route that matches the HTML's contract exactly, with no login required
  (the page has no login UI, so a protected `/api/v1/...` route was
  unreachable from it). Every request still gets saved to the database.
- **`ai.py`** — the Groq-backed agents. It had a privacy redactor and a
  markdown-report auditor, but nothing that returns the four numeric scores
  the HTML's progress bars need. I added **`run_scoring_agent()`**, which
  asks the model for a small structured JSON object of 4 scores instead of
  free-text.

## What I changed

| File | Change |
|---|---|
| `ai.py` | Added `run_scoring_agent()` — returns `{"labels": [...], "scores": [...]}` |
| `app.py` | Added `POST /api/audit` (matches the HTML's contract); loosened CORS to `allow_origins=["*"]` since the page has no cookies to protect |
| `cvhelper.html` | No changes needed — it already points at `http://localhost:8000` |

## Running it

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables** — copy `.env.example` to `.env` and fill in:
   - `GROQ_API_KEY` — free key from https://console.groq.com/keys
   - `DATABASE_URL` — needs a running Postgres instance by default.
     If you don't want to set up Postgres, swap it for SQLite instead —
     change the `DATABASE_URL` default in `app.py` to:
     ```
     sqlite+aiosqlite:///./cvhelper.db
     ```
     and add `aiosqlite` to `requirements.txt`.

3. **Start the backend**
   ```bash
   uvicorn app:app --reload --port 8000
   ```
   Leave this running — it's your API server at `http://localhost:8000`.

4. **Open the website** — just double-click `cvhelper.html`, or serve it
   so it isn't a `file://` URL:
   ```bash
   python -m http.server 5500
   ```
   then visit `http://localhost:5500/cvhelper.html`.

5. **Use it** — paste resume text or upload a PDF, pick a role, click run.
   The terminal log will show it hitting the real backend instead of the
   "Backend unreachable" mock path.

## Notes / things worth knowing

- The public `/api/audit` endpoint has no auth, by design — anyone who can
  reach your server can call it and burn your Groq quota. Fine for local
  dev/demo; if you deploy this publicly, add rate limiting or reuse the
  existing JWT flow (`/register` + `/token`) and update the HTML to log in
  first.
- The old `/api/v1/audit`, `/api/v1/audit/pdf`, and `/api/v1/audits`
  endpoints still work exactly as before (JWT-protected) — nothing there
  was removed, in case you build an authenticated admin view later.
- `run_scoring_agent()` falls back to a fixed default (`60/100` on all four
  axes) if the model returns malformed JSON, so a bad LLM response never
  crashes the request.
