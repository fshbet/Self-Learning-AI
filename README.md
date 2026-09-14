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

**Start at logon (optional):** `uv run kp autostart install` registers a Windows Task Scheduler task (systemd user
unit on Linux, launchd agent on macOS) that starts the Postgres container if Docker is available and then `kp serve`
with its embedded worker and scheduler — so the platform keeps re-checking sources, evaluating and exporting without
an open terminal. `kp autostart status` / `kp autostart uninstall`; logs in `data/logs/kp-serve.log`.

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
├── documents.jsonl      every fetched document the evidence cites: url, version, content hash + hashing recipe,
│                        raw-bytes hash, fetch/change times, mirror link — provenance and integrity, never the text
├── sources.jsonl · relationships.jsonl · examples.jsonl · negative.jsonl · glossary.json · conflicts.json · changelog.jsonl
├── ai/knowledge.jsonl   AI Knowledge Source: one self-contained text record per item with citations (index this)
├── ai/index.json        navigation index: taxonomy → ids, subjects → ids, counts, recommended filters
├── ai/knowledge.md      the same knowledge grouped by taxonomy (long-context ingestion / reading)
├── knowledge.html · README.md
├── schema/              the export contract: JSON Schema (draft 2020-12) per file + vocabulary.json
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
(`cite` / `caution` / `historical`), `verification_level`, `polarity`
(negative records say what does *not* work), `validated_by`, `dependencies` and `citations` (documents with
url/title/section/excerpt, or the person/organisation that provided the knowledge). The snapshot README spells out
the consumption steps; `ai/index.json` lets an agent navigate by taxonomy or subject without reading everything.

**The export contract is formal and versioned independently** (`manifest.export_schema_version`, currently 1.5;
`platform_version`, `database_schema_version` and `plugin_version` are separate fields). Every snapshot ships the
JSON Schemas it satisfies under `schema/` and is validated against them at build time and on `verify`; the same
files are committed under [`docs/export-schema/`](docs/export-schema/) with a
[changelog](docs/export-schema/CHANGELOG.md) — minor versions only add, major versions break — and `kp export schema`
regenerates them (a test keeps the copy in sync).

**Trust state travels with the export** (schema 1.1): every knowledge record carries `needs_review` / `review_kind` /
`review_reason`, `needs_revalidation` / `revalidation_reason` and an `evidence_status` summary (verified, unverified,
contradicting). An item flagged for review, awaiting revalidation, CONFLICTED, STALE or carrying contradicting
evidence is exported with `usage: caution`, an explicit `caution_reasons` list and a `Caution:` first line in its
`text`, so no consumer can mistake it for an ordinary trusted citation; superseded items are `historical`.

**The evidence model is self-contained** (schema 1.5): `documents.jsonl` describes every fetched document the
evidence cites — url, final and canonical url, version, `content_hash` plus the `normalizer_version` that produced
it, `previous_content_hash`, `raw_sha256` of the fetched bytes, fetch/change/publication times, HTTP validators and
mirror links — so a consumer can check without the database that every `evidence.document_id` is a known fetch of
a known source and that `evidence.document_hash` is that document's current (verified) or previous (stale) content
hash. The text is not exported: source terms may not allow redistribution, and the excerpt in the evidence record
is the quote. The gate refuses a snapshot whose evidence cites an unexported document.

**Delta snapshots** (`Export delta` in the UI, `kp export delta powerbi [--base <id>]`) describe what changed between
two full snapshots. A fresh full snapshot is built as the head, then diffed record-by-record against the base using
the stored canonical files, so a delta is reproducible and never depends on live database state:

```
powerbi-knowledge-v7/            kind: delta · base v5 → head v6 (ids and integrity hashes in the manifest)
├── delta.json                   added / modified (+ changed_fields) / superseded / removed / status_changes /
│                                rescored_only for knowledge; added / modified / removed for relationships, sources,
│                                evidence, examples, negatives — every modified one with changes[id] = changed_fields
│                                + before/after (e.g. an evidence `verified` flag flipping after a page changed)
├── knowledge.jsonl              head records of every item whose stored record differs (added, modified, superseded,
│                                and `refreshed` = only scores / freshness stamps moved)
├── removed.jsonl                ids (and last status) of base items that dropped out (rejected or excluded)
├── evidence.jsonl · relationships.jsonl · sources.jsonl · examples.jsonl · negative.jsonl   head records of every
│                                record that differs from the base
├── conflicts.json · changelog.jsonl   conflicts that changed (opened/resolved) and transitions since the base
└── ai/knowledge.jsonl · README.md     every AI Knowledge Source record that differs; how to apply the delta
```

**Reconstruction promise (delta@1.2).** Base records + the delta's records − the `removed` ids reproduce the head
snapshot's files *record for record* — the delta ships every record whose stored form differs, volatile fields
included (a rescore, a freshness stamp, a citation's document version). `delta.json` keeps the meaning apart from the
bytes: `added`, `modified` (+ `changed_fields` / `changes[id]` with before/after), `superseded`, `removed`,
`status_changes`, and `rescored_only` / `refreshed` for records that moved without changing meaning.
`tests/integration/test_incremental_updates.py` proves the promise across two consecutive deltas.

**`kp export apply <base> <delta> --out DIR`** is the formal integrity test of that contract: it rebuilds the head's
record files from the base and the delta (snapshot ids, unzipped directories or zips — no database needed) and
checks every one of them against the head hashes the delta manifest recorded (`head_files`); `reconstruction.json`
lists each file's hash and verdict, and the command exits non-zero when any promised file differs or the delta was
built against a different base. The derived renderings (`ai/index.json`, `ai/knowledge.md`, `knowledge.html`,
`README.md`) are not reconstructed — they carry no information of their own. Record files follow a canonical order
(by id; evidence by item then id; changelog by time then id) so reconstruction is byte-exact.

## Knowledge origin, provenance and polarity

Every item records **how** it was obtained and **where** it came from (req. 8–9, 19):

| Field | Values | Meaning |
|---|---|---|
| `origin` | `DIRECT`, `EXPERIMENTALLY_VALIDATED`, `DERIVED`, `SYNTHESIZED` | stated verbatim by a source · plus passed a domain validator · derived/combined from other items (evidence chain via `derived_from` relations) |
| `provenance` | `OFFICIAL`, `EXTERNAL`, `COMMUNITY`, `USER`, `ORGANIZATION`, `DERIVED` | from the **declared class** of the most authoritative source (`source_class` in `sources.yaml` or the Sources editor — authority never implies *official*; undeclared sources are `external`, discovered ones `community`); `USER`/`ORGANIZATION` only for knowledge a person entered; `DERIVED` for derived/synthesized items. Curating a class re-derives the provenance of the items it evidences on the next `kp domains sync` |
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

**Derived and synthesized knowledge** (`origin` DERIVED / SYNTHESIZED) is entered the same way (Knowledge → Add
knowledge → Origin) but must name its premises — existing items picked from search — and a rationale. No quote is
fabricated: the chain is `derived_from` relations plus a `derivation` evidence record holding the reasoning, the
provenance is `DERIVED`, and the item follows its premises: confidence = 0.9 × the weakest premise, `SUPPORTED` at most
until a reviewer approves it, demoted to `STALE` and flagged for revalidation as soon as a premise stops being live.
API: `POST /api/knowledge` with `origin`, `derived_from`, `rationale`.

**Derived and synthesized knowledge** (`origin` DERIVED / SYNTHESIZED) is entered the same way (Knowledge → Add
knowledge → Origin) but must name its premises — existing items picked from search — and a rationale. No quote is
fabricated: the chain is `derived_from` relations plus a `derivation` evidence record holding the reasoning, the
provenance is `DERIVED`, and the item follows its premises: confidence = 0.9 × the weakest premise, `SUPPORTED` at most
until a reviewer approves it, demoted to `STALE` and flagged for revalidation as soon as a premise stops being live.
API: `POST /api/knowledge` with `origin`, `derived_from`, `rationale`.
Confidence (`score@2.0`) is explainable: authority, verified evidence, agreement, specificity, taxonomy match, domain
validation, **freshness**, **contradiction status**, **version known** — every factor is stored on the item.

Three timestamps with distinct meanings keep freshness honest: `last_verified_at` is a **verification event** (the
item reached VERIFIED through scoring or a reviewer) and is never touched by a crawl; `last_source_checked_at` is
stamped when a source holding the item's verified quote is re-fetched and **still contains it** (an unchanged page
confirms the claim, it does not re-verify it); `last_content_changed_at` records that the source changed while the
quote persisted. The freshness factor uses the last confirmation, so a stable authoritative page keeps its
knowledge fresh without pretending it was validated again.

## Dependency graph and revalidation

`knowledge_relations` holds edges between items (`depends_on`, `example_of`, `derived_from`, `related_to`,
`supersedes`, `contradicts`). Structural edges are derived automatically — examples, procedures, best practices and
limitations depend on the definitions/facts about the same subject — and can be added by hand (`POST
/api/knowledge/{id}/relations`). When an item becomes stale, superseded, rejected or conflicted, every dependent —
direct or **transitive** (A → B → C → D; diamonds and cycles are safe, expansion is deterministic, the reason records
the root and the number of hops) — is flagged `needs_revalidation`; the `revalidate_item` job re-runs validators, scoring and conflict
detection and clears the flag only when all dependencies are live again. Flagged items appear on the Review page
("Needs revalidation") and are re-queued by the scheduler — but only when the item or one of its dependencies has
changed since the last revalidation; a wait that only a reviewer can end (a CONFLICTED dependency, say) is not
re-checked every tick. "Revalidate all" on the Review page forces a pass. Snapshots export the edges as
`relationships.jsonl`.

### Supersede-by-new-version

A claim that *changes* on its source page is the next version of the item it replaces, not an unrelated new fact.
When a page changes, the item whose quote vanished goes STALE; if the same page now yields exactly one new item about
the same subject with the same core predicate (and polarity), the two are linked: `old.superseded_by_id` /
`new.previous_version_id`, `new.version = old.version + 1`, a `supersedes` relation, and the old item moves to
SUPERSEDED. Ambiguous pairings are left STALE for a reviewer, who links versions explicitly
(`POST /api/knowledge/{id}/review {"action": "supersede", "superseded_by": …}`). The old version is never deleted:
it keeps its evidence and history, is excluded from retrieval, and is exported as `historical` with the successor's
id in its text. Propagating relations that pointed at the old version are carried to the new one and the dependents
are flagged; `revalidate_item` settles an ordinary dependent on the live successor, while a DERIVED / SYNTHESIZED
conclusion stays flagged until a reviewer re-derives it (approve clears the flag) — its premise no longer reads the
same. Deltas list the change under `superseded`.

## Self-evaluation and regression protection

Every domain plugin ships a golden question set (`evaluation.yaml`). The runner answers each question from the current
knowledge base, applies mechanical checks (required concepts, citation validity, abstention, stale/conflict flagging,
version, validators, negative-knowledge coverage, retrieval precision/recall) and an LLM judge (correctness, evidence support, hallucinated claims);
a question passes only when both agree.

Every result also carries one **primary failure class**, so a low score can be read correctly:

| Class | Meaning |
|---|---|
| `expected_abstention` | the question expects a decline and got one (a pass) |
| `coverage_failure` | the knowledge base holds nothing relevant (no live items under the question's topic, authoritative source never crawled, required concepts absent) — nothing to retrieve or cite |
| `retrieval_failure` | relevant knowledge exists but retrieval did not surface it |
| `uncited_answer` | the model answered from its own memory without citing any item; it is still judged for hallucinations and always fails |
| `answer_generation_failure` | relevant items were available yet the answer is wrong, unsupported or non-compliant |
| `citation_failure` / `validation_failure` | citations do not resolve to verified evidence / cited examples failed a validator |

An abstention is recognised by its content (a short decline) or by empty retrieval — never by the mere absence of
citations. Metrics add **coverage** (share of questions with relevant knowledge present) and **uncited rate**; the
evaluator is never weakened to improve a score: missing knowledge counts as a failure, but as the right kind. Metrics and per-question results are stored, each run is compared with the
previous run on the same dataset version, and a drop in accuracy or citation correctness beyond
`KP_EVAL_REGRESSION_THRESHOLD` (default 5 points) flags a **regression** on the dashboard.

* UI: **Evaluation** page (run, history, trend, failure analysis with suggested actions, per-question checks and judge).
* CLI: `kp eval run powerbi [--fail-on-regression]`, `kp eval list`.
* Automatic: after every pipeline run that produced knowledge (`KP_EVAL_AFTER_PIPELINE`) and every
  `KP_EVAL_INTERVAL_HOURS` (default 24) while serving.

## Operations: scheduling and observability

The embedded scheduler (every `KP_SCHEDULER_INTERVAL_SECONDS`, default 600) re-crawls each source when **its own**
interval is due, runs the golden set on the evaluation interval, revalidates flagged items and — when enabled in
**Settings → Export schedule** (`KP_SNAPSHOT_INTERVAL_HOURS`, `KP_SNAPSHOT_AFTER_PIPELINE`) — exports a full
Canonical Knowledge Snapshot periodically or after every pipeline run that added knowledge.

**Source monitoring vs new-source discovery** ([ADR 0005](docs/adr/0005-source-discovery.md)): the scheduler
monitors the *approved* catalogue; it never trusts a site it found on its own. Discovery (`kp run discover`, or
recurring with `KP_DISCOVERY_INTERVAL_HOURS` > 0 — off by default) runs the plugin's `discovery.queries` and your
keywords through the search provider and registers unknown hosts as **candidates** with an explainable relevance
score (queries hit, domain vocabulary in title/snippet, rank, preferred publishers; deny lists, host/canonical
dedup and a relevance floor keep spam, duplicates and irrelevant sites out). A candidate is `community`-class with
authority 30 and disabled until a person approves it on the Sources page; nothing discovered is ever exported as
anything but `COMMUNITY` provenance.

Operational metrics come from the tables the platform already keeps (jobs, runs, model calls, documents,
snapshots): `GET /api/stats` carries an `ops` block, the dashboard's **Operations** card shows it and `kp ops
[domain]` prints it — queue depth and age, jobs awaiting retry, **dead-letter** count by job type, jobs that needed
retries, per-job-type durations (24h), model calls with average and **p95 latency** per purpose and failures (24h),
storage (documents, bytes, mirrors, evidence, embedded items, snapshots) and the schedule (next source check,
overdue sources, next evaluation, next snapshot). Dead-letter jobs are retried from the Pipeline page or
`POST /api/jobs/{id}/retry`.

The runner never edits knowledge; its findings tell you what to crawl, review or tune (req. 16 of the V2 plan).

## Security boundary

This is a single-user, local-first application: the API binds to `KP_API_HOST` (127.0.0.1) and anything that can
run on the machine — a shell, `kp`, a script — is trusted; there is no login. What is *not* trusted is a web page open
in your browser. Two checks keep other sites away from `http://127.0.0.1:8010/api/*`:

* **CORS** is limited to the application's own origins (the served UI on every loopback spelling, the Vite dev server
  on port 5173, plus `KP_ALLOWED_ORIGINS`); preflights from any other origin are refused and foreign scripts cannot
  read responses.
* **Origin guard**: every mutating request (POST/PUT/PATCH/DELETE) that carries a browser `Origin` outside that list —
  or `Sec-Fetch-Site: cross-site` — is answered `403` before it reaches a route. Requests without a browser origin
  (curl, `kp`, server-to-server) are unaffected.

Outbound, the **SSRF guard** (below) keeps the crawler off internal addresses. Validators declare whether they are
`static` (pure checks, run in the worker) or `executing` (evaluate content): executing validators are refused
in-process and only ever run through the isolated runner designed in
[ADR 0003](docs/adr/0003-validator-isolation.md) — until it exists they are skipped and the skip is recorded on the
item. The boundary only holds on loopback: `kp serve` **refuses** to bind to any other address unless you name
what authenticates — `KP_AUTH_MODE=proxy` (an authenticating reverse proxy in front, its origin in
`KP_ALLOWED_ORIGINS`) — or explicitly accept an unauthenticated network API with `KP_INSECURE_EXPOSE=true`.
[ADR 0004](docs/adr/0004-authentication.md) has the threat model for local vs LAN/remote use and the design of the
optional authentication layer (not implemented).

## Active falsification (optional)

Beyond waiting for a source to change, the platform can *try to disprove* what it believes. `Try to falsify` on an
item (drawer), `Try to falsify 5` on the Review page, `POST /api/knowledge/{id}/falsify` or `kp falsify powerbi
--limit 5` search the web (SearXNG: `docker compose --profile discovery up -d`) for the statement and its negation,
fetch the top public pages (SSRF-guarded), and ask the judge model whether a passage **contradicts** the statement.
A contradiction is stored as unverified `falsification` evidence with the quote and URL and raises a **review flag**
(`needs_review`, kind `falsification`) — the platform never rewrites knowledge on the strength of an unregistered
web page. Review flags are separate from dependency revalidation: the scheduler never clears them; only a reviewer's
decision (approve, reject, mark stale or dismiss on the Review page) resolves one, and every resolution is kept in the
item's `review_log`.
Supporting passages are kept as unverified web support and do not raise the score.

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
| `kp export snapshot <domain> [--out file.zip]` / `kp export delta <domain> [--base id]` / `kp export list` / `kp export verify <id>` / `kp export apply <base> <delta> --out DIR` | Canonical Knowledge Snapshots (full and delta) |
| `kp ops [domain]` | queue health, dead letters, model latency, storage, schedule |
| `kp falsify <domain> [--limit 5] [--item id]` | active falsification with the open web (needs SearXNG) |
| `kp autostart install/status/uninstall` | start the platform at logon (Task Scheduler / systemd / launchd) |
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
* **Contradictions are surfaced, not resolved silently** — items about the same subject whose predicates share a core
  ("supports" / "does not support": negation and negative polarity are recognised) and disagree are adjudicated by the
  model; a real contradiction opens a conflict for human review, while `different_versions` / `different_scopes` /
  `different_conditions` verdicts are kept structurally as `compatible_under` relations with the distinguishing
  condition (recorded version mismatches are stored the same way without a model call).
* **Change detection** — content hashes + ETag/If-Modified-Since; when a page changes, items whose quotes vanished become `STALE`.
  **Section-level delta**: every chunk's hash (heading path + text, plus the extractor prompt version) is stored on the
  document; on re-extraction only changed sections go to the model — unchanged sections keep their items and evidence.
  A section the model could not process (timeout, malformed output) is recorded as **failed**, never as extracted: the
  document stays partially extracted (`FETCHED` + error), a delayed retry sends only that section (three attempts with
  backoff), and the next crawl re-enqueues it — no section is silently lost.
* **Source independence** — a copy is not a second confirmation. A document is linked to the earliest copy
  (`documents.canonical_document_id`) when its normalized text is identical, when its loose fingerprint (letters and
  digits only) matches, or when the page declares a known document as its `rel=canonical`; a source can be declared a
  republisher of another (`mirror_of` in `sources.yaml`, `mirror_of_source_id` in the Sources API); and a source that
  only repeats verbatim excerpts another source already supplied is not counted either. Only genuinely independent
  sources raise the agreement factor. Not attempted (by decision): fuzzy similarity of substantially edited copies.
* **SSRF guard** — every outbound fetch (plugin sources, user URLs, discovered pages, robots.txt and every redirect hop)
  must resolve to a public address; loopback, RFC 1918, link-local, CGNAT, multicast, reserved ranges and internal
  hostnames are refused, as are non-http(s) schemes and URLs carrying credentials. IP literals are canonicalised in every
  spelling before checking (`127.1`, `2130706433`, `0x7f000001`, `0177.0.0.1`, IPv4-mapped/6to4 IPv6, zone ids); DNS
  verdicts are cached for 60 s, not forever; and the crawler's transport **resolves once and connects to the validated
  address** (Host header and TLS name keep the hostname), so a rebinding DNS answer cannot move the socket
  (`KP_FETCH_ALLOW_PRIVATE=true` for intranet crawls).
* **Language independence** — lexical retrieval uses the text-search configuration the domain plugin declares or
  the one derived from its `language` (`simple` for anything PostgreSQL cannot stem, incl. multilingual corpora);
  the core assumes no language. See `domains/README.md`.
* **Retrieval policy** — answers are built from VERIFIED / SUPPORTED items; STALE and CONFLICTED items are retrieved
  but labelled for the answer model (and the evaluator checks that the answer says so); items flagged for review or
  awaiting revalidation carry the same labels. CANDIDATE items are **excluded** from search and answers unless a caller
  asks (`include_candidates`, "include unverified candidates" on the Search page) and are then labelled UNVERIFIED —
  they never rank silently next to trusted knowledge. They stay in the database for future validation; SUPERSEDED
  knowledge is historical (exported, never answered).
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
