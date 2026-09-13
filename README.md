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
| LLM (extraction, answers) | Ollama · `qwen3:8b` | **Settings page** (Ollama / OpenAI-compatible / Anthropic, live model list) or `KP_LLM_*` |
| Embeddings | Ollama · `nomic-embed-text` (768-d) | Settings page (Ollama / OpenAI-compatible) or `KP_EMBEDDING_*`; switching triggers re-embedding |
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

## Choosing models (local or API)

**Settings** in the UI lets you pick the provider per role — *triage*, *extraction*, *reasoning & answers* — and the
embedding model:

* **Ollama (local)** — lists the models installed on this machine.
* **OpenAI-compatible API** — OpenAI, Gemini's OpenAI endpoint, Groq, OpenRouter, Mistral, LM Studio, vLLM… (base URL + key).
* **Anthropic API** — Claude models via the Messages API (structured output through tool use).

*Load models* queries the provider's model list; *Test* sends a one-line structured-output prompt and reports latency.
Choices are stored in the local `settings` table and override `.env`; API keys are masked in every response and never
logged (`KP_SETTINGS_ALLOW_KEYS=false` forces env-only keys). Changing the embedding model marks existing vectors as not
searchable until **Re-embed** runs (a dimension change rebuilds the vector column and index). Every call is metered
regardless of provider, so cost per knowledge item stays visible.

## Canonical Knowledge Snapshots (export)

**Snapshots** in the UI (or `kp export snapshot powerbi --out powerbi.zip`) builds a reproducible, versioned export
of a domain's curated knowledge. The snapshot is *not* the source of truth — every record cites evidence that points
back to the original source — it is the canonical representation of what the platform currently believes.

```
powerbi-knowledge-v5/
├── manifest.json        identity, counts, generation (extractor/judge/render/scoring versions, models, embedding),
│                        per-file SHA-256, integrity hash, export-gate results
├── knowledge.jsonl      canonical knowledge records (origin, provenance, polarity, lifecycle, evidence ids, dependencies)
├── evidence.jsonl       source, document, version, hash, section, verbatim excerpt, retrieval time
├── sources.jsonl · relationships.jsonl · examples.jsonl · negative.jsonl · glossary.json · conflicts.json · changelog.jsonl
├── ai/knowledge.jsonl   AI Knowledge Source: one self-contained text record per item with citations (index this)
├── ai/index.json        navigation index: taxonomy → ids, subjects → ids, counts, recommended filters
├── ai/knowledge.md      the same knowledge grouped by taxonomy (long-context ingestion / reading)
├── knowledge.html · README.md
└── ext/                 optional plugin-provided files (DomainPlugin.export_extensions)
```

Before anything is written an **export gate** runs: schema validation of every record, provenance (every current
DIRECT item has verified evidence; DERIVED items have a `derived_from` chain), consistency (no dangling references),
then integrity hashing. Canonical serialisation (sorted keys, ordered records, UTC timestamps, no volatile fields, no
identity in rendered files) makes unchanged knowledge hash identically across builds; `Verify integrity` recomputes
every hash. Included: VERIFIED, SUPPORTED, CONFLICTED, STALE (flagged) and SUPERSEDED (`historical: true`).

**AI Knowledge Source** (`ai/`): each `ai/knowledge.jsonl` record carries a `text` block that stands on its own —
statement, explanation, code, structured details (expected result, common mistake, how to validate), scope
(product version, effective dates) and numbered sources — plus metadata an AI system can filter on: `usage`
(`cite` / `caution` for CONFLICTED–STALE / `historical` for superseded), `verification_level`, `polarity`
(negative records say what does *not* work), `validated_by`, `dependencies` and `citations` (documents with
url/title/section/excerpt, or the person/organisation that provided the knowledge). The snapshot README spells out
the consumption steps; `ai/index.json` lets an agent navigate by taxonomy or subject without reading everything.

**Delta snapshots** (`Export delta` in the UI, `kp export delta powerbi [--base <id>]`) describe what changed between
two full snapshots. A fresh full snapshot is built as the head, then diffed record-by-record against the base using
the stored canonical files, so a delta is reproducible and never depends on live database state:

```
powerbi-knowledge-v7/            kind: delta · base v5 → head v6 (ids and integrity hashes in the manifest)
├── delta.json                   added / modified (+ changed_fields) / superseded / removed / status_changes /
│                                rescored_only, plus relationship, source, evidence, example, negative and conflict diffs
├── knowledge.jsonl              full head records of added, modified and superseded items
├── removed.jsonl                base records that dropped out (rejected or excluded)
├── evidence.jsonl · relationships.jsonl · sources.jsonl · examples.jsonl · negative.jsonl   changed records only
├── conflicts.json · changelog.jsonl   conflicts opened/resolved and transitions since the base
└── ai/knowledge.jsonl · README.md     AI Knowledge Source for the changed items; how to apply the delta
```

Apply a delta on top of its base by upserting the records it carries, dropping the `removed` ids and treating
`superseded`/`status_changes` as lifecycle updates. Volatile fields (`confidence`, `last_verified_at`, …) do not
count as modifications; items whose only change is a rescore are listed under `rescored_only`.

## Knowledge origin, provenance and polarity

Every item records **how** it was obtained and **where** it came from (req. 8–9, 19):

| Field | Values | Meaning |
|---|---|---|
| `origin` | `DIRECT`, `EXPERIMENTALLY_VALIDATED`, `DERIVED`, `SYNTHESIZED` | stated verbatim by a source · plus passed a domain validator · derived/combined from other items (evidence chain via `derived_from` relations) |
| `provenance` | `OFFICIAL`, `EXTERNAL`, `COMMUNITY`, `USER`, `ORGANIZATION`, `DERIVED` | from the *class* of the most authoritative source; `USER`/`ORGANIZATION` only for knowledge a person entered (Knowledge → **Add knowledge**) — a URL a user adds is still official/external content |
| `polarity` | `positive`, `negative` | negative = what does **not** work (`limitation`, `warning`, `anti_pattern`); searchable, exported as `negative.jsonl`, shown to the answer model as LIMITATION/WARNING blocks so limitations are stated, not inferred |
| `details` | structured fields | examples: `expected_behavior`, `expected_result`, `common_mistake`, `validation_method`; negatives: `condition`, `workaround` |

**Examples and negative knowledge are first-class** (req. 18–19): examples keep their code, expected result, common
mistake and validation method, run through the domain validators (results exported in `examples.jsonl`) and are linked
`example_of` the definitions/facts they illustrate. Negative knowledge is extracted with its condition and workaround,
filtered on the Knowledge page (polarity), rendered with a `Limitation:` / `Warning:` prefix in the AI Knowledge Source,
and evaluated: golden questions marked `negative: true` only pass the **negative-knowledge coverage** check when the
answer cites a negative item — otherwise the finding `limitation_not_surfaced` tells you which limitation to extract.

Human-authored items carry a `human` evidence record with the author, declared authority and what it is based on; they go
through the same dedup, validators, scoring, conflicts and export as extracted knowledge and stay distinguishable.
Confidence (`score@2.0`) is explainable: authority, verified evidence, agreement, specificity, taxonomy match, domain
validation, **freshness**, **contradiction status**, **version known** — every factor is stored on the item.

## Dependency graph and revalidation

`knowledge_relations` holds edges between items (`depends_on`, `example_of`, `derived_from`, `related_to`,
`supersedes`, `contradicts`). Structural edges are derived automatically — examples, procedures, best practices and
limitations depend on the definitions/facts about the same subject — and can be added by hand (`POST
/api/knowledge/{id}/relations`). When an item becomes stale, superseded, rejected or conflicted, every dependent is
flagged `needs_revalidation` with the reason; the `revalidate_item` job re-runs validators, scoring and conflict
detection and clears the flag only when all dependencies are live again. Flagged items appear on the Review page
("Needs revalidation") and are re-queued by the scheduler; snapshots export the edges as `relationships.jsonl`.

## Self-evaluation and regression protection

Every domain plugin ships a golden question set (`evaluation.yaml`). The runner answers each question from the current
knowledge base, applies mechanical checks (required concepts, citation validity, abstention, stale/conflict flagging,
version, validators, negative-knowledge coverage, retrieval precision/recall) and an LLM judge (correctness, evidence support, hallucinated claims);
a question passes only when both agree. Metrics and per-question results are stored, each run is compared with the
previous run on the same dataset version, and a drop in accuracy or citation correctness beyond
`KP_EVAL_REGRESSION_THRESHOLD` (default 5 points) flags a **regression** on the dashboard.

* UI: **Evaluation** page (run, history, trend, failure analysis with suggested actions, per-question checks and judge).
* CLI: `kp eval run powerbi [--fail-on-regression]`, `kp eval list`.
* Automatic: after every pipeline run that produced knowledge (`KP_EVAL_AFTER_PIPELINE`) and every
  `KP_EVAL_INTERVAL_HOURS` (default 24) while serving.

The runner never edits knowledge; its findings tell you what to crawl, review or tune (req. 16 of the V2 plan).

## Adding your own URLs and keywords (no files needed)

On the **Sources** page of the UI:

* **Add your own URL** — any documentation site, manual or blog. Set authority (trust, 0–100), page budget and link depth;
  the crawler stays under the path you give it. User-added sources are tagged `user`, survive plugin re-syncs, and can be
  paused, re-crawled or removed. API: `POST /api/sources`. Internal addresses (localhost, private ranges, cloud
  metadata endpoints, `*.local`) are refused — see *SSRF guard* below.
* **Edit a source** (pencil) — source class (official / external / community / organization), authority, relevance,
  re-check interval, link depth and page budget are per source; the scheduler re-crawls each source when its own
  interval is due. API: `PATCH /api/sources/{id}`.
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
| `kp eval run <domain> [--fail-on-regression]` / `kp eval list` | golden-set evaluation, regression detection |
| `kp export snapshot <domain> [--out file.zip]` / `kp export delta <domain> [--base id]` / `kp export list` / `kp export verify <id>` | Canonical Knowledge Snapshots (full and delta) |
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
  **Section-level delta**: every chunk's hash (heading path + text, plus the extractor prompt version) is stored on the
  document; on re-extraction only changed sections go to the model — unchanged sections keep their items and evidence.
* **Source independence** — the same text fetched from a second source (a mirror, a syndicated copy) is linked to the
  earliest copy (`documents.canonical_document_id`) and counts **once** in confidence scoring; only genuinely independent
  sources raise the agreement factor.
* **SSRF guard** — every outbound fetch (plugin sources, user URLs, discovered pages and every redirect hop) must resolve
  to a public address; loopback, RFC 1918, link-local, CGNAT, multicast, reserved ranges and internal hostnames are
  refused, as are non-http(s) schemes and URLs carrying credentials (`KP_FETCH_ALLOW_PRIVATE=true` for intranet crawls).
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
    evaluation/  checks · runner (golden set, judge, metrics, regression, findings)
    export/      schema · canonical serialisation · snapshot builder + gate · renderers (AI + human)
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
Phase 4 executable validators (sandboxed) · Phase 5 scheduler ✔ basic, source reliability history · Phase 6 golden-set evaluation runner ✔ · Phase 7/8 additional domains to prove the plugin architecture.
