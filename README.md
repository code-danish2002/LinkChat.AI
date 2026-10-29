# LinkChat

Paste a link or drop a file, then chat with it. Works with web pages, Wikipedia, documentation, product pages, PDFs, Word documents, slide decks, spreadsheets, EPUB books, YouTube videos and audio recordings. Answers are grounded in the source and cite the exact excerpts used, with timestamps for anything spoken. Every model is open-weight, served through a pool of providers with automatic failover: if one is down, rate-limited or retired, the next one answers.

```
frontend (Next.js 15 + Tailwind 4)  ──HTTP/SSE──▶  backend (FastAPI)
                                                   ├─ ingestion  web · PDF · Office · EPUB · YouTube · audio · uploads
                                                   ├─ embeddings fastembed, local and open-source
                                                   ├─ index      cosine + BM25, fused with reciprocal rank fusion
                                                   ├─ modes      ask · summarise · outline · extract · quiz · …
                                                   └─ model pool open models, circuit breakers, rate-limit pacing
```

## Quick start

Requirements: Python 3.11 or newer (3.12 recommended), Node 20 or newer, and [uv](https://docs.astral.sh/uv/) or plain pip.

```bash
cd linkchat
make setup                      # backend venv, frontend deps, .env files
```

Add at least one provider key to `backend/.env`. Groq and OpenRouter both have free tiers that need no payment details:

```
GROQ_API_KEY=gsk_...
```

Then run both servers in separate terminals:

```bash
make backend                    # http://localhost:8000   (API docs at /docs)
make frontend                   # http://localhost:3000
```

The first backend start downloads the embedding model, about 130 MB, into `backend/data/embedding-models`.

Without `make`:

```bash
cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env            # add a key
.venv/bin/uvicorn app.main:app --reload --port 8000

cd ../frontend && npm install && cp .env.example .env.local && npm run dev
```

### Docker

```bash
cp backend/.env.example backend/.env   # add a key
docker compose up --build
```

Both images were built and run end to end on 2026-10-17. Measured footprint:

| | Image | Memory in use |
|---|---|---|
| backend | 687 MB | 433 MB |
| frontend | 346 MB | 33 MB |

No GPU is needed, because the only model running on your own hardware is the
embedding model. A one-gigabyte instance is comfortable.

The embedding model is not bundled in the image. fastembed downloads it on first
start into `data/embedding-models`. That cache and in-flight uploads are the
only things written to local disk, so the backend runs happily on hosts with
an ephemeral filesystem: everything a user would miss lives in Postgres.

### What is stored where

Records, chunks and embeddings all live in one Postgres database with the
pgvector extension. Neon's free tier is enough to launch on and needs no card.
The schema is in `backend/db/schema.postgres.sql`; it is idempotent and applied
on every start, so a fresh database needs no manual setup.

| Table | Holds |
|---|---|
| `documents` | one row per piece of content, keyed by normalised URL or upload hash: title, kind, ingestion status, which embedding model built it |
| `chunks` | the document's text pieces with their `vector` embedding, timestamps and a full-text column |
| `sources` | an owner's handle on a document; ten people adding one URL are ten rows pointing at one document |
| `collections`, `collection_sources` | an owner's named groups of their sources |
| `conversations`, `messages` | an owner's chat threads, with citations as JSON |
| `users`, `sessions`, `login_codes` | email-code sign-in; codes and tokens are stored only as hashes |
| `guests` | browser-generated ids and when they were last seen |

**Content is shared, ownership is not.** A URL is read and embedded once however
many people add it, and the second person to add something still being read
simply attaches to the work in progress. A database trigger deletes a document
the moment the last source pointing at it is deleted, and never before.

**Every owned row carries `owner_id`**, and every repository query filters on
it, so a source, collection or conversation is unreachable to anyone but its
owner even when its id is known.

Local disk under `backend/data` holds only the embedding model cache and
uploaded files while they are being ingested; the original file is discarded
once its text is in the database.

### Load, and what runs out first

Three defences, each sized from a measurement rather than a guess.

**Spreading users across the pool.** Providers meter each model separately, so
which model a request starts on decides whose budget it spends. A conversation
is assigned a model by hashing its id, so different conversations start on
different models and one thread keeps a consistent voice. Failover still walks
the whole pool, so an assigned model being busy or broken costs nothing but the
next attempt.

**Bounded ingestion.** Embedding one long article costs about sixteen seconds of
CPU and holds the document in memory, and submissions used to start immediately
with no limit, which is what actually exhausted a small instance. Submissions
are now accepted in milliseconds and queued behind `MAX_CONCURRENT_INGESTIONS`.
Twenty long articles submitted at once:

| | Memory growth | Time to finish all |
|---|---|---|
| Unbounded, as it was | +643 MB | 235 s |
| Capped at four | +80 MB | 210 s |

Same work, eight times less memory, and marginally faster for less thrashing.

**Rate limits**, per client address, per minute: chat, ingestion and everything
else are counted separately because their costs differ by orders of magnitude.
Health checks are exempt so monitoring keeps working. The counters live in
memory, which is correct while the application runs as a single process.

**What still runs out first is the provider budget, and no amount of
engineering here moves it.** One question costs about 2,650 tokens and the pool
allows roughly 112,000 tokens a minute, so about forty questions a minute in
total, with a hard cap of 3,000 Groq requests a day. That suits a few dozen
active users. Thousands needs paid model capacity.

### Who owns what

Two kinds of visitor, and the difference is only how long their data lives.

**Guests** need no sign-up. The browser makes up an id on first visit, keeps it
in localStorage and sends it as `X-Guest-Id` on every request. Everything the
guest adds belongs to that id and is deleted by an hourly job once the guest
has been quiet for `GUEST_TTL_HOURS` (24 by default). A "Forget me now" button
deletes it immediately.

**Users** sign in with their email and a six-digit code, no password. A session
token is returned, stored by the browser and sent as `Authorization: Bearer`;
it lasts `SESSION_TTL_HOURS` (a week) and is extended on use. Signing in from a
guest moves everything that guest made into the account, collapsing any
duplicate of a document the account already held, so nothing typed before
signing in is lost. Headers rather than cookies because the frontend and the
API live on different domains, where browsers treat a cookie as third-party.

Codes go out through `EMAIL_PROVIDER`: `resend` sends real email (free tier at
https://resend.com), `console` prints the code to the server log. In
development the console code is also returned to the UI so sign-in works
without an email account; in production it never is.

### Deploying

The frontend is a standalone Next.js build and fits a free static or Node host
such as Vercel. The backend is a single always-on process that needs only a
`DATABASE_URL`; it keeps no state on disk that matters, so Render's free tier
works. Provider free tiers are tied to your account rather than your IP, so the
same keys keep working from a server.

Required in production: `DATABASE_URL`, `AUTH_SECRET` (random, so sessions
survive restarts), `CORS_ORIGINS` set to the frontend's origin, and an email
provider so users can sign in.

One exception: **YouTube refuses caption requests from most datacenter IP
ranges**, so YouTube sources that work on your laptop will fail once deployed.
Everything else is unaffected.

## What it does

**Reads almost anything.** A URL is matched to a loader; a file upload goes through the same pipeline. HTML goes through readability extraction with a DOM fallback, PDFs through pypdf, Word, slides and spreadsheets through their own extractors, EPUB chapter by chapter. YouTube captions and audio files become timed transcripts. Uploads are content-addressed, so the same file twice is one source.

**Multilingual.** The default embedding model is multilingual, so a question in
Hindi, Spanish or French finds the answer in an English source and vice versa.
Measured on cross-language retrieval, English documents with questions in four
languages:

| Embedding model | Cross-language | English top-1 | Size |
|---|---|---|---|
| paraphrase-multilingual-mpnet-base-v2 (default) | 5/5 | 7/8 | 1.0 GB |
| paraphrase-multilingual-MiniLM-L12-v2 | 5/5 | 6/8 | 0.22 GB |
| bge-small-en-v1.5 (English only) | 3/5 | 6/8 | 0.07 GB |

The default is better at English too, so the only cost is the download. All of
these run on your CPU, so there is no key and no per-question cost.

Thirty-two languages are detected by name, across fifteen writing systems,
with no misdetections in the test set. That includes languages sharing a script,
such as Ukrainian against Russian and Urdu against Arabic, which are separated
by distinctive letters, and close relatives such as Danish against Norwegian,
separated by function words. Anything less than a clear winner returns nothing
and the prompt falls back to generic wording, because naming the wrong language
is worse than naming none: the model obeys it.

The reply language is decided by naming it in the prompt, after detecting it
from the question. Telling a model to "use the same language as the question"
is not enough: a conversation that switches between two Latin-script languages
keeps answering in the earlier one. Detection is dependency-free, exact for
non-Latin scripts and marker-based for Latin ones, and falls back to generic
wording when unsure. Eight alternating turns across four languages now come back
in the right language, where the prompt-only version managed three of five.

**Non-English videos and audio keep their original language.** A caption track
is chosen by preferring, in order, the uploader's own captions in a language you
configured, auto-generated captions in one, then the language actually spoken.
Machine translation is a last resort rather than the normal path, because the
embedding model is multilingual and the chat models read these languages
directly, so translating first would only add errors. Audio behaves the same
way: Whisper detects the spoken language and transcribes it as spoken.

Verified end to end on 2026-10-17: a Korean-only YouTube video answered
correctly in Korean, English and Hindi from the same Korean excerpts, and
French, Arabic and Danish recordings each answered both in their own language
and in English.

**Answers from the source, with citations.** Chunks are embedded locally with fastembed and stored in a per-source index. Searches fuse cosine similarity with BM25 keyword scores using reciprocal rank fusion, which keeps exact names, product codes and numbers findable. Every answer cites `[n]` markers that the interface turns into clickable excerpts, and spoken sources link back to the second.

**Twelve modes, not just questions.** Asking a question retrieves the best passages. Every other mode reads the whole document and needs no typing at all: summarise, key points, outline with timestamps, action items, extract to a table, flashcards, quiz, explain simply, translate, fact-check a claim, or rewrite as a blog post. Adding one is a single entry in `backend/app/services/modes.py`.

**Talk to it.** The mic in the composer opens a hands-free voice conversation.
A voice-activity detector (Silero, running in the browser) notices when you
stop talking and sends that utterance as audio; the server transcribes it with
the same Whisper step that reads uploaded recordings, answers from the source
as usual, and the answer is spoken sentence by sentence while it is still
streaming, so the first words are heard about two seconds after you fall
silent. The microphone is off while the answer plays and for a moment afterwards, so the app never hears its own voice; "Interrupt by speaking" turns it back on for headphone users. Spoken answers
are written for the ear (two to four plain sentences, no lists) but keep their
citations, which stay on screen and in the saved chat. Every answer also has a
"Read aloud" button, and a speaker toggle reads typed answers as they arrive.
Speech output uses the browser's own voices, chosen to match the language of
the question; nothing is paid for and no audio leaves the browser except the
question itself.

**The answering model is visible.** Which pool member is replying is named
under the answer and pinned beside the composer, live while it types rather
than only once it has finished. Conversations are assigned a model, so that
label is stable between turns and makes failover legible when it happens.

**Collections.** Group sources and ask one question across all of them. Each index is searched in parallel and the results merged, with every citation labelled by which source it came from.

### Modes

| Mode | Scope | Needs input | What it does |
|---|---|---|---|
| Ask | passages | yes | Answers a question from the best matching excerpts |
| Summarise | whole source | no | What it is, then the substance |
| Key points | whole source | no | Up to eight concrete takeaways |
| Outline | whole source | no | Sections in order, with timestamps when timed |
| Action items | whole source | no | Checklist of tasks, owners and deadlines |
| Extract data | whole source | optional | A Markdown table, columns you name or it picks |
| Flashcards | whole source | no | Eight to twelve question and answer pairs |
| Quiz me | whole source | no | Five multiple-choice questions with an answer key |
| Explain simply | whole source | no | Plain language, no jargon |
| Translate | whole source | yes | A summary in the language you name |
| Fact-check | passages | yes | Supported, contradicted or not addressed, with quotes |
| Blog post | whole source | no | The material rewritten as an article |

## The model pool

`backend/config/models.yaml` lists providers and models. Every provider speaks the OpenAI-compatible chat API, so one client serves them all, and speech-to-text reuses the same credentials.

| Provider | Needs | Payment details to get started |
|---|---|---|
| Groq | `GROQ_API_KEY` | no |
| OpenRouter (`:free` models) | `OPENROUTER_API_KEY` | no |
| Ollama Cloud, hosted | `OLLAMA_API_KEY` | no, but check your plan's limits |
| Hugging Face | `HF_TOKEN` | no, but a small quota |
| Ollama, local | `OLLAMA_ENABLED=true` | no account at all, runs on your machine |
| Cerebras | `CEREBRAS_API_KEY` | yes, at the time of writing |
| Together | `TOGETHER_API_KEY` | usually |

Ollama appears twice because it is two different things. **Ollama Cloud** is a
hosted API behind a key, with nothing to download. **Local Ollama** is a server
on your own machine with no key and no rate limit, but you download each model
and it runs at whatever speed your hardware allows. They are independent, and
enabling one does not require the other.

A provider joins the pool only when its key is set. Models are tried in
`priority` order, and each has a circuit breaker.

Set `bulk: false` on a model that answers well but slowly. Whole-document modes
make many requests, so one slow member drags out the entire run; flagged models
still serve interactive answers at their normal priority but sit out condensing.
Marking three slow models this way cut a 39-chunk article summary from 1003
seconds to 241.

The breaker reacts differently to each kind of failure:

- transient errors open it after two consecutive failures, with an exponential cooldown from 60 seconds up to 15 minutes;
- a rate limit opens it immediately;
- a bad key or unknown model id parks it for an hour;
- an oversized request leaves it closed, because the model is healthy and a smaller request will succeed;
- a model that streams no content steps aside for one base cooldown without escalating, because returning nothing still costs a round trip but does not mean the model is broken.

`GET /api/v1/models` and the Model pool panel show live state per model, including whether the provider still lists it.

### What is not available, whatever you read elsewhere

Anthropic's Claude and Google's Gemini have no open weights, and neither is on
Hugging Face or any other open-model host; both are closed, API-only services.
Searching the Hugging Face router for them returns nothing.

Their open-weight counterparts are already in this pool. OpenAI's open models
are `gpt-oss-120b` and `gpt-oss-20b`, served here by Groq and Ollama Cloud.
Google's open model is Gemma, not Gemini, and Gemma 4 31B is in the pool too.
So the models people usually mean are already present, reached through faster
providers than Hugging Face.

### Model ids go stale

Hosted free catalogues rotate, often within weeks. When a model disappears the pool fails over, but the entry is dead weight. Check and fix it with:

```bash
make verify-models
```

It prints each configured model as served or not served, and the backend logs
the same at startup.

A served model is not necessarily a usable one. The listing is public, but the
chat endpoint can still refuse: a model may need a paid plan (HTTP 402), be
gated for your account (403), have no provider currently serving it (404), or be
out of capacity (429). Checking that properly means streaming one real
completion per model, which is what `make verify-models` cannot do for you.

One trap worth knowing: a Hugging Face **Read** token is not enough. Calls fail
with `403 This authentication method does not have sufficient permissions`
until the token also carries the "Make calls to Inference Providers" scope,
which means creating a fine-grained token rather than a read one.

### Free tiers and the per-minute budget

Free tiers cap tokens per minute far below their context windows. Groq, measured from its own response headers, allows 1000 requests per day and 8000 tokens per minute per model, and rejects any single request above 7000 input tokens.

That shapes how whole-document modes work. A short source is one request. A long one is condensed window by window and then combined, and if the combined notes are still too large they are condensed again. Requests are paced against the minute budget, condensing rotates across the pool so each model's separate budget is used, and when a rate limit does trip the run waits for the cooldown instead of giving up. Progress messages explain every pause.

The practical consequence: a 17000-token paper summarises in minutes rather than seconds on a single free key, and most of that is waiting. Adding a second provider key roughly doubles throughput. The relevant knobs are `MODE_MAX_INPUT_TOKENS` and `MODE_TOKENS_PER_MINUTE`, the latter being one model's budget rather than the pool's.

## Project layout

```
backend/
  app/
    api/            HTTP layer: routes, schemas, SSE, error mapping
    core/           settings, container (composition root), errors, ids, url utils
    domain/         entities and ports, the interfaces services depend on
    ingestion/      loaders, extractors, chunker, resolver, browser fallback
    providers/
      llm/          registry, circuit breakers, OpenAI-compatible client, failover router, validation
      embeddings/   fastembed adapter with fallbacks
      transcription/ Whisper through any OpenAI-compatible provider
      email/        login-code delivery: console (log) or Resend
    services/       ingestion, retrieval, chat, whole-document modes, pacing, prompts, collections, auth, maintenance
    storage/        Postgres repositories (documents, sources, collections, conversations, identity) and the pgvector store
  config/models.yaml
  db/schema.postgres.sql
  tests/            200+ tests, no network: fake embeddings, fake providers, in-memory store; plus a Postgres suite
frontend/
  src/app/          routes: / (home), /s/[id] (source), /c/[id] (collection)
  src/components/   chat, sources, collections, models, auth, layout, ui
  src/hooks/        useSources, useSource, useCollections, useConversation, useChat, useCatalogue, useModelPool
  src/lib/api/      typed client, SSE reader, types
  src/lib/auth.ts   guest id and session token, sent as headers
  src/lib/speech.ts WAV encoding, sentence-by-sentence speech output, voice and language choice
```

The backend follows ports and adapters: services depend on interfaces in `app/domain/ports.py`, and concrete adapters are wired once in `app/core/container.py`. Swapping Postgres for another store, or pgvector for a hosted vector database, touches the adapter and the container and nothing else; the test suite runs the same services against an in-memory store.

### Adding a source service

1. Implement `SourceLoader` in `backend/app/ingestion/`. `supports(url)` decides whether it handles a URL, `load(url)` returns a `LoadedDocument`. Use timed `Segment`s for anything with timestamps and they become clickable citations.
2. Add the kind to `SourceKind` if it is new, give it a hint in `app/services/prompts.py`, and an icon in `frontend/src/components/sources/KindIcon.tsx`.
3. Register the loader in `Container.build()` before `HttpLoader`, which is the catch-all.

Chunking, indexing, retrieval, modes and chat are source-agnostic, so nothing else changes.

### Adding a mode

Add one `ModeSpec` to `backend/app/services/modes.py`. Set `scope` to `retrieval` for a question-shaped task or `document` to read the whole source. The API, the mode bar and the icon map pick it up automatically.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | status, embedding model, active models, capabilities |
| GET | `/api/v1/modes` | the mode catalogue, for building a UI |
| GET | `/api/v1/models` | pool state, breaker state and upstream availability per model |
| POST | `/api/v1/auth/request-code` | `{email, display_name?}`, emails a six-digit code |
| POST | `/api/v1/auth/verify` | `{email, code}`, returns a session token; the `X-Guest-Id` sent along is merged into the account |
| GET | `/api/v1/auth/me` | who the caller is |
| POST | `/api/v1/auth/logout` | end the session |
| DELETE | `/api/v1/auth/account` | delete the account and everything it owns |
| DELETE | `/api/v1/auth/guest` | delete everything the guest id owns, now |
| POST | `/api/v1/sources` | `{url}`, starts ingestion, 202 |
| POST | `/api/v1/sources/upload` | multipart file upload, 202 |
| GET | `/api/v1/sources` | recent sources |
| GET | `/api/v1/sources/{id}` | one source, poll until `ready` |
| POST | `/api/v1/sources/{id}/reingest` | re-read a source |
| DELETE | `/api/v1/sources/{id}` | drop your handle; the shared document goes once nobody holds it |
| POST | `/api/v1/collections` | `{title, source_ids}` |
| GET/PATCH/DELETE | `/api/v1/collections/{id}` | read, rename, change members, delete |
| POST/DELETE | `/api/v1/collections/{id}/sources/{sid}` | add or remove one member |
| POST | `/api/v1/conversations` | `{source_id}` or `{collection_id}` |
| GET | `/api/v1/conversations/{id}/messages` | history |
| POST | `/api/v1/conversations/{id}/messages` | `{content, mode, spoken?}`, streams SSE; `spoken` asks for an answer written to be read aloud |
| POST | `/api/v1/conversations/{id}/voice` | multipart `audio` (+ `mode`), a spoken question; streams `transcript` first, then the same events |

Every endpoint below `/auth/me` needs an identity: `Authorization: Bearer <token>` for a user or `X-Guest-Id: gst_...` for a guest; health, modes and models are public.

The message stream emits `citations`, `progress`, `model`, `token`, then `done` or `error`; a voice turn starts with `transcript`. `done` carries the detected language of the question so the client can pick a voice.

Stopping an answer aborts the request. The server notices the client has gone
and abandons the generation, so nothing further is spent and nothing is saved:
a stopped answer stays on screen marked incomplete, with a Retry button, but
disappears on reload because it was never persisted. The client also stops
consuming events the moment it aborts, since cancelling a stream resolves
pending reads rather than raising, and without that check buffered tokens keep
arriving for a moment after the button is pressed. Interactive docs are at http://localhost:8000/docs.

## Configuration

Everything lives in `backend/.env`, documented in `.env.example`: the database URL, identity lifetimes and secret, email provider, chunk size and overlap, retrieval depth, history length, temperature, token caps, request timeouts, breaker thresholds, mode budgets and pacing, upload limits, the embedding model list and CORS origins. The frontend needs only `NEXT_PUBLIC_API_URL`.

### Optional: JavaScript rendering

Single-page apps and many shops serve an empty HTML shell. With the fallback enabled, a page that yields almost no text is re-fetched through a headless browser:

```bash
make browser                    # installs playwright and chromium
# then set BROWSER_FALLBACK_ENABLED=true in backend/.env
```

It costs about 150 MB and a few seconds per page, so it is off by default. Sources that used it are marked in the source panel.

### Optional: local models

Local Ollama is the only provider that needs no account of any kind, and it has
no rate limit, which makes it a useful last resort when free tiers are spent.
Install it from https://ollama.com, pull a model, then enable the provider:

```bash
ollama pull llama3.1:8b          # a few GB on disk
```

```
OLLAMA_ENABLED=true
```

Expect it to be considerably slower than the hosted providers, and note that
an 8B model wants roughly 8 GB of free memory while answering.

### Optional: audio

Audio needs a speech-to-text provider. Groq serves Whisper on the same key as its chat models, so a Groq key enables it with no extra setup. Files up to 24 MB are accepted; longer recordings should be split first. The same provider powers spoken questions in the voice conversation; a spoken question is capped at `MAX_VOICE_BYTES` (6 MB, about three minutes of 16 kHz audio) and the UI stops at ninety seconds.

## Testing

```bash
make test        # backend tests, no network
TEST_DATABASE_URL=postgresql://... make test   # also runs the Postgres adapter suite
make lint        # ruff, eslint, tsc
```

The suite never touches the network. It uses deterministic fake embeddings, scripted fake providers, and real Office files generated by the same libraries that read them. It covers chunking, URL safety, the circuit breaker and failure classification, failover and load spreading, the hybrid index, the streaming protocol, rate-limit pacing, whole-document modes including recursive condensing and rate-limit recovery, owner isolation, shared documents and their cleanup, the sign-in flow, and the full API surface. Set `TEST_DATABASE_URL` to also exercise the real Postgres adapters against a scratch database.

## Notes and limits

- **Voice is a pipeline, not a realtime model.** Speech is transcribed, answered and spoken as three steps, so a reply starts about two to three seconds after you stop talking. A sub-second, interruptible-mid-word experience needs a paid speech-to-speech model. The browser's voices are clear but not human-sounding. Whisper on poor audio repeats fragments or invents stock phrases, so transcripts are cleaned (immediate repeats collapsed, known hallucinations treated as silence) before they become a question.
- **Guests are a browser, not a person.** Clearing site data, a private window or another device is a new guest with nothing in it, and the old guest's sources vanish after a day. Signing in is what makes a library follow a person.
- **Single process.** Rate-limit counters, circuit breakers and the "already ingesting" set live in memory, so run one backend instance. The database is shared, so a second instance would work but double-count nothing and double-ingest occasionally.
- **YouTube blocks caption requests from many data-centre IP ranges.** Run the backend from a home network for YouTube sources.
- **Answers inherit transcription errors.** The chain is only as accurate as the captions or the speech recognition beneath it. Testing Arabic audio, Whisper heard "one hundred thousand" where the speaker said "two hundred thousand", and the answer faithfully repeated the transcript. That is the system working as designed, staying grounded rather than guessing, but numbers and proper nouns in non-English speech deserve a check against the source.
- **Podcast pages are not supported directly.** Spotify and Apple pages do not expose an audio URL. Use a direct audio link or upload the file.
- **The index is per source**, on disk under `backend/data/vectors`. Changing the embedding model invalidates existing indexes: re-submitting a source rebuilds it automatically, and an open conversation reports that a Re-read is needed rather than quietly finding nothing.
- **Two multilingual models fastembed lists will not load**, `google/embeddinggemma-300m` and `intfloat/multilingual-e5-large`. Both store weights as external ONNX data and the runtime rejects the cache layout. The prefix handling they need is implemented and tested, so they work the moment that is fixed upstream.
- **Circuit breaker and pacing state is in-process.** Several backend replicas would each keep their own view of model health.
- **Ingestion runs as an in-process background task.** That suits one machine; a queue would be needed to scale out.
- **Extract mode often returns a table without `[n]` markers**, so those answers can show no citations even though they are grounded.
