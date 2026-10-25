# LinkChat: working notes for Claude

Read this first when resuming work. It records what the app is, the decisions
already made, how it is deployed, and what is still open. The README explains
the product and architecture in depth; this file is the hand-off.

## What this is

Paste a link or drop a file, chat with it. FastAPI backend (`backend/`), Next.js
15 frontend (`frontend/`). Retrieval-augmented generation written without a
framework: loaders -> chunker -> local fastembed embeddings -> Postgres with
pgvector (cosine + full-text, fused with reciprocal rank fusion) -> a pool of
free open-weight models behind circuit breakers and failover. Twelve modes
(ask, summarise, outline, flashcards, ...). The owner chose **no LangChain or
LlamaIndex on purpose**; keep it that way.

## Live deployment

| Piece | Where | Notes |
|---|---|---|
| Frontend | Vercel, `https://link-chat-ai.vercel.app` | root directory `frontend`, env `NEXT_PUBLIC_API_URL` = backend URL |
| Backend | Render free tier, `https://linkchat-ai.onrender.com` | root directory `backend`, Docker, health path `/api/v1/health`, sleeps after 15 min idle, no disk |
| Database | Neon free tier, **Singapore (ap-southeast-1)**, pooled connection string | pgvector enabled; schema in `backend/db/schema.postgres.sql` is applied on every start |
| Repo | `https://github.com/code-danish2002/LinkChat.AI.git`, branch `main` | Render and Vercel auto-deploy on push |

Everything must stay on free tiers. The owner does not want to spend money.

Render environment variables that must exist: `PORT=8000`, `DATABASE_URL`
(the Singapore pooled string), `ENVIRONMENT=production`, `AUTH_SECRET`
(random hex), `CORS_ORIGINS` (the Vercel origin), `EMBEDDING_MODELS=BAAI/bge-small-en-v1.5`
(512 MB RAM cannot hold the 1 GB default), `MAX_CONCURRENT_INGESTIONS=1`,
provider keys (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKEN`, ...), and the
email settings below. Secrets live only in Render and in the gitignored
`backend/.env`; never in the repo.

## Data model and identity (decided, implemented, tested)

- **Shared documents.** `documents` is keyed by normalised URL or `upload:<hash>`;
  `chunks` hold text + `vector` embedding + tsvector. Ten users adding one URL
  share one document. A trigger deletes a document when its last `sources`
  row goes, never before.
- **Ownership.** `sources`, `collections`, `conversations` carry `owner_id`,
  which is a guest id or a user id. Every repository query filters on it.
- **Guests.** Browser generates `gst_<hex>` once, keeps it in localStorage, sends
  it as `X-Guest-Id`. Data deleted by an hourly job 24 h after last request
  (`GUEST_TTL_HOURS`). "Forget me now" deletes immediately.
- **Users.** Email + six-digit code, no password. Bearer token in
  `Authorization` header (not cookies: frontend and API are on different
  domains). Session lasts 7 days, extended on use. Signing in from a guest moves
  the guest's rows into the account and collapses duplicate sources.
- **Codes and tokens are stored as HMAC hashes** with `AUTH_SECRET`.
- Uploaded files are not kept; only their extracted chunks are.
- Vector column has no fixed dimension (Render uses 384-dim, laptop 768-dim).
  `documents.embedding_model` records the builder; a mismatch raises a stale
  index error and the UI offers Re-read.

## Email for login codes

`EMAIL_PROVIDER` is `console` (log only; in development the code is also
returned to the UI, **never in production**), `smtp`, or `resend`. SMTP sender
implemented and tested (587 STARTTLS or 465 TLS). Verified on 2026-10-25:

- Gmail app password works (needs 2-Step Verification; ~500 recipients/day).
- Brevo free SMTP relay works (300/day, no card, may hold new accounts a day).
- Zoho free plan has no SMTP any more. Temp-mail addresses cannot send.

**Still to do by the owner:** create a dedicated Gmail account, make an app
password, set on Render: `EMAIL_PROVIDER=smtp`, `SMTP_HOST=smtp.gmail.com`,
`SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`. Until then
guests work in production but sign-in codes only reach the Render log.

## Known limits and the next agreed step

- **YouTube captions fail from every datacenter IP** (Render included);
  verified live, the API returns `IpBlocked`. Agreed next step: a fallback in
  `backend/app/ingestion/youtube.py` that, on the blocked error, calls a
  third-party transcript API read from an env var, converting its segments to
  timed `Segment`s. Free tiers found on 2026-10-25: Supadata ~100/month,
  TranscriptAPI 100 starter credits, Apify ~$5 usage/month (~500 videos),
  ytranscript.com 50/month. Chain two of them like the model pool. The official
  YouTube Data API cannot do this (owner-only captions). Residential proxies
  cost money and were declined.
- Single backend process: rate limiter, breakers and the in-flight set are in
  memory.
- Provider free tiers cap total throughput at roughly forty questions a minute.

## Working on it

```bash
make setup                                  # venv + npm install + .env copies
make backend                                # http://localhost:8000, needs DATABASE_URL in backend/.env
make frontend                               # http://localhost:3000
cd backend && .venv/bin/python -m pytest -q # ~220 tests, no network, in-memory store
TEST_DATABASE_URL=postgresql://... .venv/bin/python -m pytest -q tests/test_postgres.py  # real adapters, cleans up after itself
make lint                                   # ruff, eslint, tsc
```

Local `.env` points at the production Neon database; create a Neon branch for
experiments if that becomes a problem. The default local embedding model is 1 GB;
set `EMBEDDING_MODELS=BAAI/bge-small-en-v1.5` to match Render.

Conventions: ports in `backend/app/domain/ports.py`, adapters wired once in
`backend/app/core/container.py`, services never import adapters. Tests use the
in-memory doubles in `backend/tests/fakes.py`, which mirror the schema's rules.
Ruff line length 120. Keep the README's measured claims honest; update it when
behaviour changes.

## Session history

- 2026-10-25: moved from SQLite + local files to Neon Postgres; added shared
  documents, guest/user identity, email-code login, SMTP/Resend senders,
  maintenance job, frontend auth UI. Verified end to end against Neon with a
  real model answer. Render env vars for the database and auth still had to be
  set by the owner at the end of the session.
