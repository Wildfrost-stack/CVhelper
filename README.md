# CVhelper / VailAudit — Privacy-Preserving Agentic Resume Auditor

Two AI agents run in sequence over every resume or code submission:

1. **Agent 1 — Privacy Officer** (`run_privacy_agent`) strips names, emails,
   phone numbers, and locations before anything is evaluated. This fails
   *closed* — if redaction can't be completed, the request is rejected
   rather than falling back to raw text (`PrivacyAgentError`).
2. **Agent 2 — Auditor / Scorer** (`run_auditor_agent`, `run_scoring_agent`)
   scores the redacted submission on merit only, with zero identity signal
   in the loop.

Access to the public audit endpoint is metered with **x402** on the
**Algorand Testnet**, settled through the **GoPlausible facilitator**.

```
.
├── app.py                  # FastAPI backend: auth, DB, x402 payment gate, routes
├── ai.py                   # Groq-backed agent functions used by app.py
├── requirements.txt        # Python backend dependencies
├── VailAudit.html           # Static frontend (landing page + live demo)
├── package.json             # Frontend deps: @x402-avm/*, @perawallet/connect
├── src/
│   └── x402-client.js       # Wallet connect + x402 payment client (source)
└── dist/
    └── x402-client.bundle.js  # Built output — created by `npm run build`
```

## 1. Backend setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file next to `app.py`:

```bash
# --- LLM ---
GROQ_API_KEY=your_groq_api_key

# --- Auth ---
JWT_SECRET_KEY=change-me-in-production

# --- Database ---
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres

# --- x402 payments (Algorand Testnet, GoPlausible facilitator) ---
AVM_ADDRESS=YOUR_ALGORAND_TESTNET_RECEIVING_ADDRESS
FACILITATOR_URL=https://facilitator.goplausible.xyz
X402_PRICE=$0.01
```

`AVM_ADDRESS` must be a testnet address you control — this is where audit
payments settle. `app.py` will refuse to start without it.

Run the API:

```bash
uvicorn app:app --reload --port 8000
```

On first run it creates `audit_records` and `users` tables automatically
(see the `lifespan` handler in `app.py`).

### Routes worth knowing

| Route | Auth | Notes |
|---|---|---|
| `POST /register`, `POST /token` | — | JWT username/password auth |
| `POST /api/v1/audit`, `/api/v1/audit/pdf`, `GET /api/v1/audits` | JWT | Authenticated audit history |
| `POST /api/audit` | **x402** | Public endpoint `VailAudit.html` calls. Returns `402` until a valid Algorand Testnet payment is attached, then runs the audit and stores it. |

## 2. Frontend setup

The frontend is a static page (`VailAudit.html`) plus a small bundled JS
module that handles wallet connection and the x402 payment flow.

```bash
npm install
npm run build     # bundles src/x402-client.js -> dist/x402-client.bundle.js
```

Then serve the folder (not `file://`, since the page loads
`dist/x402-client.bundle.js` via `<script src>`):

```bash
python -m http.server 5500
# open http://localhost:5500/VailAudit.html
```

If you change `API_BASE_URL` inside `VailAudit.html` (defaults to
`http://localhost:8000`), point it at wherever `uvicorn` is running.

## 3. Demoing the live x402 flow

1. Fund your Algorand **testnet** wallet (Pera/Defly/etc.) with test
   ALGO — use the [Algorand testnet dispenser](https://bank.testnet.algorand.network/).
2. Start the backend (`uvicorn app:app --reload`) and serve the frontend.
3. Open `VailAudit.html`, click **Connect Wallet**, approve the connection
   in your wallet app.
4. Paste a sample resume (or use **Load Sample**) and click **Run Privacy
   Audit**. The page will:
   - hit `POST /api/audit`, get `402 Payment Required`,
   - ask your wallet to sign the payment via `x402Client.fetch()`,
   - resend the request with the signed payment attached,
   - show `[Payment] Settled on Algorand Testnet…` in the terminal log
     once the facilitator confirms.
5. Verify the settled transaction on [Lora Testnet Explorer](https://lora.algokit.io/testnet)
   using your `AVM_ADDRESS`.

## 4. Troubleshooting

- **`AVM_ADDRESS environment variable is required`** — set it in `.env`
  before starting `app.py`.
- **Wallet button does nothing / `Connect an Algorand testnet wallet
  first.`** — run `npm run build` first; `VailAudit.html` loads the
  wallet/payment logic from `dist/x402-client.bundle.js`, which doesn't
  exist until you build it.
- **Stuck on `402` after signing** — check `FACILITATOR_URL` is reachable
  and that your wallet is actually on Testnet (chain ID `416002`), not
  Mainnet.
- **CORS errors** — `app.py` currently allows all origins
  (`allow_origins=["*"]`) so the static HTML can be opened from anywhere;
  tighten this before deploying publicly.
