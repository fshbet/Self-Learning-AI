# Knowledge Platform

A modular, self-updating knowledge intelligence platform. It discovers, collects, extracts, verifies, versions and serves
evidence-backed knowledge for any technology or subject, one **domain plugin** at a time. Power BI is the first domain.

The design is described in [modular_self_updating_knowledge_platform.md](modular_self_updating_knowledge_platform.md);
section numbers referenced in the code (`§14`, `§28`, …) point there.

```
Sources ─▶ Discovery ─▶ Collection ─▶ Extraction ─▶ Quality ─▶ Verification ─▶ Versioned repository ─▶ Search / Ask / UI
              │             │              │            │            │
         source registry  robots.txt   verbatim      dedup +     conflicts,
         (authority,      ETag, hash   evidence      explainable  lifecycle,
          permissions)    change det.  required      scoring      human review
```

## Stack (local-first, provider-portable)

| Concern | Default (local) | Swap later via |
|---|---|---|
| Database + vectors + queue + full-text | PostgreSQL 17 + pgvector (Docker) | `KP_DATABASE_URL` |
| LLM (extraction, answers) | Ollama · `qwen3:8b` | `KP_LLM_*` |
| Embeddings | Ollama · `nomic-embed-text` (768-d) | `KP_EMBEDDING_*` |
| Raw evidence store | local filesystem `data/raw` | `KP_OBJECT_STORE=s3` (MinIO / R2 / S3) |
| Web search (discovery) | SearXNG (Docker, optional) | `KP_SEARCH_PROVIDER` |
| API | FastAPI (`/api`, docs at `/api/docs`) | — |
| Frontend | React + Vite + Tailwind, served by the API | — |

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 20+, [Ollama](https://ollama.com).

```powershell
# 1. models
ollama pull qwen3:8b
ollama pull nomic-embed-text

# 2. database
docker compose up -d postgres

# 3. backend
uv sync --extra dev
copy .env.example .env          # adjust if needed
uv run kp db upgrade            # apply migrations
uv run kp domains sync          # load plugin manifests + source catalogs

# 4. frontend (one-off build; the API serves frontend/dist)
cd frontend; npm install; npm run build; cd ..

# 5. run API + embedded worker, then open http://127.0.0.1:8010
uv run kp serve
```

First knowledge: on the dashboard press **Run pipeline** (or `uv run kp run pipeline powerbi --max-pages 10`).
Extraction is the slow stage — roughly 20–30 s per page section on an RTX 3060 Ti with `qwen3:8b` — so start with a small
page budget and let the scheduler grow the repository over time.

Frontend development with hot reload: `cd frontend && npm run dev` (proxies `/api` to port 8010).

## Adding your own URLs and keywords (no files needed)

On the **Sources** page of the UI:

* **Add your own URL** — any documentation site, manual or blog. Set authority (trust, 0–100), page budget and link depth;
  the crawler stays under the path you give it. User-added sources are tagged `user`, survive plugin re-syncs, and can be
  paused, re-crawled or removed. API: `POST /api/sources`.
* **Discovery keywords** — search phrases merged with the plugin's built-in queries when **Discover sources** runs
  (needs SearXNG: `docker compose --profile discovery up -d`). Found sites appear as candidates you approve.
  API: `POST /api/domains/{id}/keywords`.

The in-app **User guide** (`/docs/user-guide.html`) walks through every screen; **Why it works this way**
(`/docs/origin-and-design.html`) explains how the design was derived.

## Adding a topic

```powershell
uv run kp domains new my-topic --name "My Topic"
#   edit domains/my-topic/plugin.yaml   → taxonomy, terminology, extraction hints, risk classes
#   edit domains/my-topic/sources.yaml  → seed URLs with authority + permissions + crawl scope
uv run kp domains sync my-topic
uv run kp run pipeline my-topic
```

No core code changes are needed. See [domains/README.md](domains/README.md) for the plugin contract; `plugin.py` is
optional and only needed for validators and skills (see `domains/powerbi/plugin.py`).

## CLI

| Command | Purpose |
|---|---|
| `kp db upgrade` | apply Alembic migrations |
| `kp domains list / sync / new / check` | manage plugins |
| `kp run pipeline <domain> [--source key] [--max-pages N]` | crawl + extract (runs the worker inline) |
| `kp run extract <domain>` | extract documents that were fetched but not extracted |
| `kp run discover <domain>` | web discovery → candidate sources (needs SearXNG: `docker compose --profile discovery up -d`) |
| `kp serve` | API + UI + embedded worker + scheduler |
| `kp worker` | standalone worker (set `KP_EMBEDDED_WORKER=false` for the API) |
| `kp search <domain> "query"` / `kp ask <domain> "question"` | retrieval from the terminal |

## How quality is enforced

* **No evidence, no knowledge** — every extracted item must carry a quote located verbatim in the document; otherwise it is dropped.
* **Explainable confidence** — the score is computed from stored factors (authority, evidence, agreement, specificity,
  taxonomy match, domain validation) and a scoring-rule version.
* **Lifecycle with audit trail** — `EXTRACTED → CANDIDATE → SUPPORTED → VERIFIED`, plus `CONFLICTED`, `STALE`, `REJECTED`,
  `SUPERSEDED`; every transition records actor and reason; illegal transitions are refused.
* **Deduplication as agreement** — an identical or near-identical statement from a second source becomes extra evidence on the
  existing item (raising its verification level) rather than a duplicate.
* **Contradictions are surfaced, not resolved silently** — same subject and predicate with a different object opens a conflict
  for human review.
* **Change detection** — content hashes + ETag/If-Modified-Since; when a page changes, items whose quotes vanished become `STALE`.
* **Responsible collection** — robots.txt (incl. Crawl-delay), identified User-Agent, per-host rate limits, backoff on 429/5xx.
* **Accounting** — every model call is recorded (model, tokens, latency) so cost per knowledge item is visible on the dashboard.

## Tests

```powershell
uv run pytest                    # unit + contract (+ integration when PostgreSQL is reachable)
uv run ruff check . ; uv run ruff format --check .
cd frontend; npm run typecheck
```

Contract tests load every folder under `domains/` and fail if a plugin breaks the interface.

## Layout

```
src/knowledge_platform/
  adapters/      llm · embeddings · storage · search   (provider implementations behind interfaces)
  core/
    plugins/     contract + registry
    collection/  fetcher (robots, rate limit, conditional GET) · normalize · collector
    extraction/  chunker · prompts · extractor (verbatim-quote verification)
    quality/     scoring · dedup
    verification/ conflicts
    versioning/  lifecycle (status machine, verification levels)
    retrieval/   embeddings · hybrid search (RRF) · grounded answers
    orchestration/ queue · jobs · worker · scheduler
    pipeline.py  document → knowledge stage
  api/           FastAPI routes + schemas
  cli.py
domains/         plugins (powerbi, example template)
alembic/         migrations
frontend/        React UI
docs/            user guide + design-origin HTML (served at /docs), adr/ decision records
tests/           unit · contract · integration (fixtures, fake providers)
```

## Roadmap (from the design document)

Phase 1 collector ✔ · Phase 2 verification (conflicts, stale, review) ✔ basic · Phase 3 hybrid retrieval + grounded answers ✔ ·
Phase 4 executable validators (sandboxed) · Phase 5 scheduler ✔ basic, source reliability history · Phase 6 golden-set
evaluation runner · Phase 7/8 additional domains to prove the plugin architecture.
