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
start into `data/embedding-models`, which is on the persistent volume, so it is
fetched once and reused. On a host with an ephemeral filesystem it would
re-download on every restart, which is the main reason to attach real storage.
To avoid the slow first boot entirely, add a build step that imports the model
so it lands in the image instead.

The backend writes to a named volume, `linkchat_backend-data`, holding the
vector indexes, the SQLite database and uploaded files. Any host whose
filesystem resets on restart will lose your sources, so attach real persistent
storage.

### What is stored where

Everything lives under `backend/data`, which is the only directory a deployment
has to persist:

| Path | Holds |
|---|---|
| `linkchat.sqlite3` | records: sources, collections, conversations, messages with their citations |
| `vectors/` | two files per source: a `.npy` embedding matrix and a `.json` of chunk text |
| `uploads/` | uploaded files, content-addressed so the same file twice is stored once |
| `embedding-models/` | the embedding model, downloaded once on first start |

SQLite is used for the records because the job is small relational rows with
transactions: a conversation must not half-save. It needs no server process, no
port, no credentials and no container, and its driver is in Python's standard
library, so it adds nothing to install. The whole database is one file, which
makes a backup a copy.

Adding more *embedding* models would not spread load, which is worth saying
because it is a natural assumption. They run on your own CPU with no quota to
distribute, so a second model would consume more memory for the same
throughput, and indexes built by different models cannot be compared anyway.
The embedding bottleneck is CPU, and the fix is the ingestion queue above, or
more cores.

Vectors are deliberately **not** in it. They are a dense float matrix that is
read whole and multiplied, which is what numpy does well and what SQL does
badly.

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

### Capacity, and why there is no database server

SQLite and flat files are a deliberate choice here, not a shortcut, because
**retrieval is always scoped to one source or one collection**. There is never
a search across everything, so there is nothing for a vector database to do
that brute-force cosine over a few hundred vectors does not already do in
microseconds.

Measured on 2026-10-17. Per-source index, which is what a search touches:

| Chunks in one source | Index on disk | Search |
|---|---|---|
| 200, a long article | 0.8 MB | 0.2 ms |
| 1,000 | 4 MB | 0.4 ms |
| 20,000, roughly a 1,500-page book | 79 MB | 9 ms |

And the database. Measured cost per record as actually stored, a source being
metadata only and an exchange carrying six citations:

| | |
|---|---|
| One source | 424 bytes |
| One exchange, question and answer | 8.4 KB |
| Write throughput, single writer | 7,000 messages/second |
| 16 threads writing at once, one process | 8,200 rows/second, no errors |
| Look up a source by id or url | 0.03 ms |
| List the 50 newest sources | 0.31 ms |
| Load a 20-message thread | 0.43 ms |

Which projects to:

| Usage | Database size |
|---|---|
| 100 sources, 1,000 exchanges | 10 MB |
| 2,000 sources, 20,000 exchanges | 0.2 GB |
| 50,000 sources, 500,000 exchanges | 4.2 GB |
| 1,000,000 sources, 10,000,000 exchanges | 85 GB |

SQLite's own ceiling is in the hundreds of terabytes, so size is never what
runs out first.

So size is not the reason to change either one. The reasons that would are:

- **more than one backend replica.** This is the real ceiling, and it is
  measured rather than assumed: a second process writing the same file gets
  `database is locked` immediately. Within one process it is fine, because the
  repositories serialise writes behind a lock and sixteen concurrent threads
  sustained 8,200 rows a second with no errors. Reads are never blocked at all,
  thanks to write-ahead logging. So one backend process of any size is fine and
  two are not, and the circuit-breaker and pacing state is per-process anyway.
- **searching across all sources at once**, rather than within one. That is the
  feature that would justify a real vector index.

Each of those is a repository swap behind the existing ports, not a rewrite.

### Deploying

The frontend is a standalone Next.js build and fits a free static or Node host.
The backend needs an always-on process with a persistent volume. Provider free
tiers are tied to your account rather than your IP, so the same keys keep
working from a server.

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
    services/       ingestion, retrieval, chat, whole-document modes, pacing, prompts, collections
    storage/        SQLite repositories with migrations, local hybrid vector store
  config/models.yaml
  tests/            96 tests, no network: fake embeddings, fake providers, real Office files
frontend/
  src/app/          routes: / (home), /s/[id] (source), /c/[id] (collection)
  src/components/   chat, sources, collections, models, layout, ui
  src/hooks/        useSources, useSource, useCollections, useConversation, useChat, useCatalogue, useModelPool
  src/lib/api/      typed client, SSE reader, types
```

The backend follows ports and adapters: services depend on interfaces in `app/domain/ports.py`, and concrete adapters are wired once in `app/core/container.py`. Swapping SQLite for Postgres, or the local index for a hosted vector database, touches the adapter and the container and nothing else.

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
| POST | `/api/v1/sources` | `{url}`, starts ingestion, 202 |
| POST | `/api/v1/sources/upload` | multipart file upload, 202 |
| GET | `/api/v1/sources` | recent sources |
| GET | `/api/v1/sources/{id}` | one source, poll until `ready` |
| POST | `/api/v1/sources/{id}/reingest` | re-read a source |
| DELETE | `/api/v1/sources/{id}` | delete source, index, upload and conversations |
| POST | `/api/v1/collections` | `{title, source_ids}` |
| GET/PATCH/DELETE | `/api/v1/collections/{id}` | read, rename, change members, delete |
| POST/DELETE | `/api/v1/collections/{id}/sources/{sid}` | add or remove one member |
| POST | `/api/v1/conversations` | `{source_id}` or `{collection_id}` |
| GET | `/api/v1/conversations/{id}/messages` | history |
| POST | `/api/v1/conversations/{id}/messages` | `{content, mode}`, streams SSE |

The message stream emits `citations`, `progress`, `model`, `token`, then `done` or `error`.

Stopping an answer aborts the request. The server notices the client has gone
and abandons the generation, so nothing further is spent and nothing is saved:
a stopped answer stays on screen marked incomplete, with a Retry button, but
disappears on reload because it was never persisted. The client also stops
consuming events the moment it aborts, since cancelling a stream resolves
pending reads rather than raising, and without that check buffered tokens keep
arriving for a moment after the button is pressed. Interactive docs are at http://localhost:8000/docs.

## Configuration

Everything lives in `backend/.env`, documented in `.env.example`: chunk size and overlap, retrieval depth, history length, temperature, token caps, request timeouts, breaker thresholds, mode budgets and pacing, upload limits, the embedding model list and CORS origins. The frontend needs only `NEXT_PUBLIC_API_URL`.

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

Audio needs a speech-to-text provider. Groq serves Whisper on the same key as its chat models, so a Groq key enables it with no extra setup. Files up to 24 MB are accepted; longer recordings should be split first.

## Testing

```bash
make test        # 96 backend tests
make lint        # ruff, eslint, tsc
```

The suite never touches the network. It uses deterministic fake embeddings, scripted fake providers, and real Office files generated by the same libraries that read them. It covers chunking, URL safety, the circuit breaker and failure classification, failover and load spreading, the hybrid index, the streaming protocol, rate-limit pacing, whole-document modes including recursive condensing and rate-limit recovery, database migration from the previous schema, and the full API surface.

## Notes and limits

- **No authentication, and therefore no private history.** Every source and conversation is global. Nothing is stored in the browser except the light or dark preference, so opening the app in a private window, in another browser, or on another device shows the same sources and the same chat history: there is no identity to separate them by. That is fine for a local tool and wrong for anything exposed. Authentication and per-user data would have to come first, and nothing in the schema has a user column yet.
- **YouTube blocks caption requests from many data-centre IP ranges.** Run the backend from a home network for YouTube sources.
- **Answers inherit transcription errors.** The chain is only as accurate as the captions or the speech recognition beneath it. Testing Arabic audio, Whisper heard "one hundred thousand" where the speaker said "two hundred thousand", and the answer faithfully repeated the transcript. That is the system working as designed, staying grounded rather than guessing, but numbers and proper nouns in non-English speech deserve a check against the source.
- **Podcast pages are not supported directly.** Spotify and Apple pages do not expose an audio URL. Use a direct audio link or upload the file.
- **The index is per source**, on disk under `backend/data/vectors`. Changing the embedding model invalidates existing indexes: re-submitting a source rebuilds it automatically, and an open conversation reports that a Re-read is needed rather than quietly finding nothing.
- **Two multilingual models fastembed lists will not load**, `google/embeddinggemma-300m` and `intfloat/multilingual-e5-large`. Both store weights as external ONNX data and the runtime rejects the cache layout. The prefix handling they need is implemented and tested, so they work the moment that is fixed upstream.
- **Circuit breaker and pacing state is in-process.** Several backend replicas would each keep their own view of model health.
- **Ingestion runs as an in-process background task.** That suits one machine; a queue would be needed to scale out.
- **Extract mode often returns a table without `[n]` markers**, so those answers can show no citations even though they are grounded.
