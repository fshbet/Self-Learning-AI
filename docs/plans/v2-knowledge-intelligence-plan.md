# V2 plan — Knowledge Intelligence Platform upgrade

Status: **proposal, awaiting approval** · Date: 2026-09-13 · Base commit: `241802f`

This document reconciles the "Update the Modular Self-Updating Knowledge Platform" requirements with the code that exists
today. Nothing here has been implemented yet. Section numbers in *italics* refer to the requirements prompt; `§n` refers to
`modular_self_updating_knowledge_platform.md`.

---

## 1. Current architecture summary

```
domains/<plugin>/            plugin.yaml · sources.yaml · evaluation.yaml · plugin.py (validators, skills)
src/knowledge_platform/
  core/plugins        contract (DomainPlugin) + registry (auto-discovery, api_version check)
  core/collection     Fetcher (robots, Crawl-delay, UA, rate limit, backoff, ETag/If-Modified-Since) · normalize · collector (scoped BFS, SHA-256 change detection)
  core/extraction     heading-aware chunker · Ollama structured-output extraction · verbatim-quote verification (no quote → dropped)
  core/quality        explainable scoring (factors + rule version) · guarded near-dedup (subject + object + lexical + vector)
  core/versioning     status machine EXTRACTED→CANDIDATE→SUPPORTED→VERIFIED (+CONFLICTED/STALE/REJECTED/SUPERSEDED), audited transitions, levels 0–5
  core/verification   contradiction detection (same-doc rule, multi-valued rule, LLM adjudication)
  core/retrieval      pgvector + Postgres FTS with RRF · grounded answers with [n] citations
  core/orchestration  Postgres queue (SKIP LOCKED, idempotency, retries, dead-letter) · worker · scheduler (re-checks crawled sources)
  core/pipeline.py    document → knowledge stage (stale marking, extract, dedup-as-agreement, validators, score, conflicts, embed)
  core/evaluation     EMPTY (package placeholder only)
  adapters/           llm (Ollama only) · embeddings (Ollama only) · storage (local, S3) · search (SearXNG)
  api/                FastAPI: domains, sources (user URLs, keywords), documents, knowledge, review, conflicts, search, ask, runs, jobs, stats, health
frontend/             React/Vite/Tailwind: Dashboard, Knowledge, Search&Ask, Review, Sources, Documents, Pipeline, Domains; docs served at /docs
alembic/              0001 initial · 0002 source origin + domain_keywords
tests/                unit (chunker, quotes, scoring, lifecycle, normalize, fetcher, dedup guards) · contract (every plugin) · integration (pipeline, API)
```

Tables: `domains, domain_keywords, sources, documents, knowledge_items, evidence, status_transitions, conflicts, runs, jobs, llm_calls`.

---

## 2. Requirement map

Legend: ✅ complete · 🟡 partial / needs modification · ❌ missing · ⚪ not recommended / decide

| Req. | Requirement | Status | Existing implementation / gap |
|---|---|---|---|
| *1* | Core ↔ plugin separation, domain-agnostic core | ✅ | Plugin contract + registry; contract tests; core has no Power BI code |
| *2* | Preserve existing functionality | ✅ | All changes below are additive; migrations 0003+ only add columns/tables |
| *3–5* | Canonical Knowledge Snapshot with manifest, integrity hash | ❌ | No export of any kind |
| *6* | Full and delta exports | ✅ (P1/P4) | full snapshots + delta diff engine, UI base selector, CLI |
| *7* | Knowledge item model: provenance, versions, dependencies | 🟡 | Has: version, previous/superseded ids, product_version, valid_until, first_discovered, last_verified, extraction metadata, quality factors. Missing: **origin**, **provenance level**, **effective_date**, **dependencies** |
| *8* | Knowledge origin DIRECT/DERIVED/SYNTHESIZED/EXPERIMENTALLY_VALIDATED | ❌ | Everything is implicitly DIRECT; extractor rejects quote-less items (see decision D1) |
| *9* | Provenance levels OFFICIAL/EXTERNAL/COMMUNITY/USER/ORGANIZATION/DERIVED | 🟡 | Sources carry `origin` (plugin/user/discovered) and `authority`; items do not carry a provenance level; no way to enter USER/ORGANIZATION knowledge |
| *10* | Evidence model | 🟡 | Has: URL, excerpt, locator (heading path, offsets), document hash, verified flag, type (extraction/validator/human). Missing on the evidence record itself: title, publisher, publication date, retrieval timestamp, source version (all *derivable* via document/source joins — the **export** must denormalise them) |
| *11* | Source independence | 🟡 | Independence = distinct `source_id`. No detection of syndicated/copied documents |
| *12* | Active contradiction / falsification | 🟡 | Internal contradiction detection with adjudication exists; no *search for contradicting evidence* on the web |
| *13* | Lifecycle states | ✅ | All states except `DISCOVERED` (that state applies to *sources* in this model; items start at `EXTRACTED`) |
| *14* | Explainable verification levels & confidence | ✅ | Levels 0–5; factors stored per item; missing factors: **freshness**, **version match**, **contradiction status**, **independence** (see D5) |
| *15* | Automated self-evaluation | ❌ | Golden set exists (9 Power BI questions) and is loaded by the plugin; no runner, no metrics, no history |
| *16* | Self-correction loop | 🟡 | Human review + audit trail exist; no failure-analysis output; rule "no automatic knowledge change" already true |
| *17* | Dependency graph + revalidation | ❌ | No relations table; no `needs_revalidation` |
| *18* | Examples as first-class knowledge | ✅ (P2/P6) | `details` (expected_behavior/expected_result/common_mistake/validation_method), validators, `example_of` relations, `examples.jsonl`, entry form |
| *19* | Negative knowledge | ✅ (P2/P6) | `polarity`, `anti_pattern` type, `details.condition/workaround`, `negative.jsonl`, answer-context labels, `negative_coverage` eval metric |
| *20* | User URLs/keywords survive sync | ✅ | `origin` column; sync only touches `plugin` rows; API tests cover it |
| *21* | Source registry | ✅ (P2/P7) | `source_class`, `relevance`, approval state, per-source interval/class/authority/scope editable in the Sources UI; `reliability_history` still unused |
| *22* | Collection strategy | ✅/🟡 | Scoped, ranked by authority, dedup, relevance filter (heuristic). Deep reference following limited to same-host scope — by design |
| *23* | Media handling | ⚪ | No media collected at all (compliant with "do not store everything"); OCR/transcripts not implemented — recommend defer |
| *24* | Hot/cold storage | ✅ | Postgres (hot) + object store (raw HTML/PDF) |
| *25* | Delta processing | ✅ (P7) | ETag/Last-Modified/doc hash + section-level chunk hashes (`documents.chunk_hashes`): unchanged sections skip the model |
| *26* | Plugin export contract | ✅ (P1) | `DomainPlugin.export_extensions()` → `ext/` files in the snapshot |
| *27* | AI Knowledge Source export | ✅ (P1/P5) | `ai/knowledge.jsonl` self-contained records + usage hints, `ai/index.json`, `ai/knowledge.md`, README consumption guide |
| *28* | Human-readable export | ✅ (P1) | `ai/knowledge.md` + `knowledge.html` + README (PDF not provided, per D8) |
| *30* | Versioning of everything | ✅ (P1–P3) | plugin, schema, extractor/prompt, chunker, scoring rule, render, embedding identity, item, document, **snapshot**, **validator versions on item**, relationships |
| *31* | Freshness/staleness | ✅ (P2/P3) | source-change staleness, time-based freshness factor in `score@2.0`, dependency-based revalidation |
| *32–33* | Power BI plugin; future domains | ✅ | Taxonomy covers all listed areas; core untouched by domain |
| *35* | Security | ✅ (P0b/P7) | content-as-data prompting, static validators only, **SSRF guard** on every outbound fetch, API keys masked and opt-in in DB |
| *36* | Observability | ✅ (P0/P7) | evaluation scores + regression on dashboard; `ops` block: queue depth/age, retries, dead letters, per-job durations, model p95 latency, storage, schedule; `kp ops` |
| *37* | Scheduler | ✅ (P0/P7) | per-source interval (UI-editable), eval scheduling, snapshot scheduling (interval / after pipeline), revalidation; auto-start: `kp autostart` |
| *38* | Regression protection | ✅ (P0) | run-over-run comparison on same dataset version, threshold, dashboard banner, `--fail-on-regression` |
| *39* | Quality gates | ✅ (P0/P1) | knowledge gate + export gate (schema → provenance → consistency → integrity) |
| *40–41* | Terminology / architecture | ✅ | Will be reflected in docs and export README |
| **User** | Local/API model selection in UI with model listing | ✅ (P0b) | Settings page: provider, base URL, key, model lists via probe, per-purpose models, re-embed |

---

## 3. What is already implemented (do not rebuild)

Discovery (keyword search → candidates → approval), responsible collection, change detection, extraction with verbatim evidence,
explainable scoring, guarded dedup-as-agreement, lifecycle with audit, contradiction detection with adjudication, hybrid retrieval,
grounded answers, queue/worker/scheduler with retries and dead-letter, user URLs and keywords with origin tracking, static DAX/M
validators, plugin contract + contract tests, UI for all of the above, in-app docs.

## 4. What is missing (build)

Evaluation runner + history + regression detection · Canonical snapshot (full, delta, AI-oriented, human-readable) with manifest
and integrity hash · knowledge origin + provenance level · dependency graph + revalidation · structured examples + negative
knowledge polarity · multi-provider model adapters + settings UI · SSRF guard · source-independence detection · section-level
hashes · per-source interval UI · auto-start · export/eval scheduling · extended metrics.

## 5. What needs modification (existing code touched)

| Component | Change |
|---|---|
| `models.KnowledgeItem` | add `origin`, `provenance`, `effective_date`, `polarity`, `details` (JSON), `needs_revalidation`, `validator_versions` (JSON) |
| `models.Evidence` | add `retrieved_at`, `source_version` (document version at the time) — export needs them without joins |
| `models.Document` | add `canonical_document_id` (syndication/duplicate detection), `chunk_hashes` (JSON) |
| `core/quality/scoring.py` | new factors: freshness, version match, contradiction status, independence; rule version → `score@2.0` (old scores are recomputed lazily on next rescore; stored rule version keeps history explainable) |
| `core/pipeline.py` | set origin/provenance on new items; use chunk hashes to skip unchanged chunks; create relations; flag dependents |
| `core/plugins/base.py` | add export hooks (`export_extensions()`, `render_readme()` optional) — defaults in core so existing plugins need no change |
| `adapters/` | `get_llm()`/`get_embedder()` read runtime settings (DB) before env; new OpenAI-compatible and Anthropic adapters |
| `api/schemas.py`, routes | additive fields; new routers `routes_eval.py`, `routes_snapshots.py`, `routes_settings.py` |
| Frontend | new pages **Evaluation**, **Snapshots**, **Settings**; extensions to Knowledge drawer (origin, provenance, dependencies, example structure) and Sources (interval editing) |

Nothing is removed. Existing API responses gain fields; no field is renamed.

---

## 6. Proposed database/schema changes (migration 0003, all additive)

```sql
-- knowledge_items
ALTER TABLE knowledge_items ADD COLUMN origin      VARCHAR(32) NOT NULL DEFAULT 'DIRECT';        -- DIRECT|DERIVED|SYNTHESIZED|EXPERIMENTALLY_VALIDATED
ALTER TABLE knowledge_items ADD COLUMN provenance  VARCHAR(32) NOT NULL DEFAULT 'EXTERNAL';      -- OFFICIAL|EXTERNAL|COMMUNITY|USER|ORGANIZATION|DERIVED
ALTER TABLE knowledge_items ADD COLUMN polarity    VARCHAR(16) NOT NULL DEFAULT 'positive';      -- positive|negative  (negative = limitation/warning/anti_pattern/common_mistake)
ALTER TABLE knowledge_items ADD COLUMN effective_date VARCHAR(40);
ALTER TABLE knowledge_items ADD COLUMN details     JSON NOT NULL DEFAULT '{}';                   -- structured example: expected_behavior, expected_result, common_mistake, validation_method
ALTER TABLE knowledge_items ADD COLUMN needs_revalidation BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE knowledge_items ADD COLUMN revalidation_reason TEXT;
ALTER TABLE knowledge_items ADD COLUMN validator_versions JSON NOT NULL DEFAULT '{}';            -- {"dax-syntax": "1.0"}

-- evidence
ALTER TABLE evidence ADD COLUMN retrieved_at TIMESTAMPTZ;        -- backfilled from documents.fetched_at
ALTER TABLE evidence ADD COLUMN source_version INTEGER;          -- documents.version at the time
ALTER TABLE evidence ADD COLUMN relation VARCHAR(20) NOT NULL DEFAULT 'supports';  -- supports|contradicts|validates|approves

-- documents
ALTER TABLE documents ADD COLUMN canonical_document_id UUID REFERENCES documents(id);  -- set when this doc is a copy/syndication of another
ALTER TABLE documents ADD COLUMN chunk_hashes JSON NOT NULL DEFAULT '[]';              -- [{index, heading_path, sha256}] for section-level delta

-- sources
ALTER TABLE sources ADD COLUMN source_class VARCHAR(20) NOT NULL DEFAULT 'external';    -- official|external|community|organization → drives provenance
ALTER TABLE sources ADD COLUMN relevance INTEGER NOT NULL DEFAULT 50;

-- new tables
knowledge_relations (id, domain_id, from_item_id, to_item_id, relation_type, origin, weight, details, created_at,
                     UNIQUE(from_item_id, to_item_id, relation_type))
    relation_type: depends_on | example_of | contradicts | supersedes | related_to | derived_from
    origin:        extractor | derived | user | system

evaluation_runs     (id, domain_id, dataset_version, status, config JSON, metrics JSON, baseline_run_id, regression BOOLEAN,
                     regression_details JSON, triggered_by, started_at, finished_at, run_id FK runs)
evaluation_results  (id, evaluation_run_id, question_id, question, expected_answer, answer, retrieved JSON, citations JSON,
                     checks JSON, judge JSON, passed BOOLEAN, latency_ms, tokens, created_at)

snapshots           (id, domain_id, version INTEGER, kind full|delta, base_snapshot_id, manifest JSON, integrity_hash,
                     object_prefix, size_bytes, status building|ready|failed, created_by, created_at,
                     UNIQUE(domain_id, version))

settings            (key VARCHAR PK, value JSON, updated_at)   -- runtime overrides: llm provider/models, embedding, eval schedule, export schedule
```

Backfill in the migration: `origin='DIRECT'`; `provenance` from source: `plugin & authority≥80 → OFFICIAL`, `plugin → EXTERNAL`,
`user → USER`, `discovered → COMMUNITY`; `polarity='negative'` where `knowledge_type IN ('limitation','warning')`;
`evidence.retrieved_at` from the linked document; `source_class` from authority.

Embedding model changes (Settings UI) trigger a planned `reembed` job; vectors from different models never mix (identity stored).

---

## 7. Proposed API changes (additive)

| Endpoint | Purpose |
|---|---|
| `POST /api/evaluations` `{domain}` · `GET /api/evaluations?domain=` · `GET /api/evaluations/{id}` (results) · `GET /api/evaluations/{id}/compare/{other}` | run, list, inspect, diff evaluation runs |
| `POST /api/snapshots` `{domain, kind: full\|delta, base?}` · `GET /api/snapshots?domain=` · `GET /api/snapshots/{id}` · `GET /api/snapshots/{id}/download` (zip) · `GET /api/snapshots/{id}/files/{path}` · `POST /api/snapshots/{id}/verify` (re-hash) | canonical snapshot lifecycle |
| `GET/PUT /api/settings` · `GET /api/providers/{provider}/models` | runtime model configuration and model listing (Ollama `/api/tags`, OpenAI-compatible `/v1/models`, Anthropic `/v1/models`) |
| `POST /api/knowledge` (user/organization knowledge with evidence text/URL) | USER/ORGANIZATION provenance entry point (see D3) |
| `GET /api/knowledge/{id}/relations` · `POST /api/knowledge/{id}/relations` · `DELETE …` | dependency graph |
| `POST /api/knowledge/{id}/revalidate` · `GET /api/knowledge?needs_revalidation=true` | dependency-triggered revalidation queue |
| `PATCH /api/sources/{id}` gains `crawl_frequency_hours`, `relevance`, `source_class` (already partially there) | per-source scheduling |
| `GET /api/stats` gains: latest evaluation metrics, regression flag, retries, dead-letter count, storage bytes, p95 job latency | observability |
| `POST /api/system/autostart` (Windows scheduled task create/remove) — CLI `kp autostart install/remove` primarily | continuous operation |

Job types added: `evaluate`, `snapshot`, `revalidate_item`, `reembed`, `falsify` (optional, see D4).

---

## 8. Proposed UI changes

* **Settings** (new): provider cards — *Local (Ollama)* and *API (OpenAI-compatible / Anthropic)*; "Test connection"; model lists fetched live;
  per-purpose selection (triage / extract / reason+answer / embeddings); embedding change warns and offers re-embed; eval & export schedules.
* **Evaluation** (new): run history with score trend, per-question results (pass/fail, checks, judge rationale, retrieved vs cited),
  regression banner, "Run now", compare two runs.
* **Snapshots** (new): per domain list of versions; "Export full", "Export delta vs vN"; manifest viewer; file list; download zip; integrity status.
* **Knowledge drawer**: origin + provenance chips, dependencies (depends on / depended by), structured example fields, negative-knowledge badge, "needs revalidation" flag with reason.
* **Sources**: edit re-check interval and source class inline.
* **Review**: new tab "Needs revalidation".
* **Dashboard**: latest eval score + regression indicator; retry/dead-letter/storage tiles.
* **Domains**: "Export knowledge" button → Snapshots page.

---

## 9. Proposed export format (Canonical Knowledge Snapshot)

Stored under the object store at `snapshots/<domain>/<snapshot_id>/`, also downloadable as a zip:

```
<domain>-knowledge-v<version>/
├── manifest.json          identity, counts, versions, config, per-file sha256, integrity_hash, base_snapshot (delta)
├── knowledge.jsonl        one canonical record per item (all fields of §7 + origin, provenance, polarity, details, dependencies ids)
├── relationships.jsonl    knowledge_relations
├── sources.jsonl          registry rows (URL, class, origin, authority, license, permissions, last checked/changed)
├── evidence.jsonl         denormalised: item id, source url/title/publisher, publication date, document version, content hash,
│                          retrieved_at, section (heading path), excerpt, offsets, type, relation, verified
├── examples.jsonl         items with knowledge_type=example (+ details) and their supporting item ids
├── negative.jsonl         polarity=negative items (limitations, warnings, anti-patterns)
├── glossary.json          plugin terminology + taxonomy tree
├── conflicts.json         open and resolved conflicts with both sides and resolution
├── changelog.jsonl        status_transitions since base snapshot (full: since beginning, capped) 
├── ai/
│   ├── knowledge.jsonl    AI-optimised records: text block (statement, explanation, code, version, citations rendered) + metadata
│   └── knowledge.md       grouped by taxonomy → concepts, definitions, procedures, examples, negative knowledge, conflicts; each with [source] citations
├── README.md              human-readable: domain, statistics, how to consume, terminology (Source of Truth vs Evidence vs Curated vs Snapshot)
└── knowledge.html         human-readable rendering (same content as ai/knowledge.md + stats)   [PDF: not proposed]
```

Rules:
* **Canonical serialisation**: UTF-8, sorted keys, `separators=(",", ":")`, records sorted by id, timestamps ISO-8601 UTC. Volatile
  fields (`updated_at`, embeddings) are excluded; `manifest.created_at` is excluded from the integrity hash.
* **Integrity**: sha256 per file; `integrity_hash = sha256(concat(sorted "<path>:<sha256>"))`. `POST /snapshots/{id}/verify` recomputes.
* **Inclusion**: `VERIFIED, SUPPORTED, CONFLICTED, STALE` as current knowledge; `SUPERSEDED` included with `historical: true`;
  `EXTRACTED, CANDIDATE, REJECTED` excluded from knowledge.jsonl but visible in changelog (see D6).
* **Export gate** (*39*): schema validation (pydantic record models) → provenance validation (every non-DERIVED item has ≥1 verified
  evidence; DERIVED/SYNTHESIZED have ≥1 `derived_from` relation) → integrity → consistency (no dangling relation/evidence ids) →
  conflict statistics → write → hash. Any failure → `status=failed` with reasons; nothing partial is published.
* **Delta** = diff of canonical records between two full snapshots (by id; modified = hash of canonical record differs):
  `added / modified / superseded / removed_or_rejected` for knowledge; `changed` lists for relationships, sources, evidence, examples;
  `status_changes` list. Delta manifest references `base_snapshot_id` + both integrity hashes.
* Plugin hook: `DomainPlugin.export_extensions(snapshot_ctx) -> dict[path, bytes]` (default `{}`) lets a plugin add domain files
  (e.g. a DAX function index) without the core knowing.

---

## 10. Proposed evaluation architecture (P0)

```
evaluation.yaml (versioned)  ─┐
                              ├─► evaluate job ─► per question: retrieve(k) → answer → checks → judge → result row
current knowledge + config   ─┘                         │
                                                        ▼
                                              metrics → evaluation_runs.metrics
                                                        │
                                              compare with previous run (same dataset_version, same domain)
                                                        │
                                              regression? → flag + dashboard banner + (optional) fail CLI exit code
```

**Question schema additions** (backward compatible): `expect_abstain: bool`, `expected_version: str`, `must_not_contain: [..]`,
`negative: bool` (question about a limitation).

**Per-question checks** (mechanical, explainable):
| Check | Metric it feeds |
|---|---|
| required_concepts ⊆ answer (case/whitespace-insensitive) | factual correctness (mechanical part) |
| every `[n]` citation resolves to a retrieved item with ≥1 verified evidence | citation correctness, evidence support |
| retrieved items' topic ∩ question topic / authoritative_sources ∩ evidence urls | retrieval precision; recall (when authoritative_sources given) |
| answer contains claims not supported by cited items — judged | hallucination rate |
| expect_abstain questions → answer must abstain; others must not | unanswered rate / honest-abstention rate |
| retrieved/cited items STALE or CONFLICTED → answer must say so | freshness, contradiction handling |
| expected_version mentioned | version correctness |
| cited items with validators: all passed | domain-validator success |

**LLM-as-judge** (same provider-agnostic adapter, JSON schema): `{correct: bool, supported_by_citations: bool, hallucinated_claims: [..],
rationale}` graded against `expected_answer` + `acceptable_alternatives`. Stored verbatim; never the sole signal — a question passes
only if mechanical checks *and* judge agree.

**Run metrics**: accuracy, citation_correctness, evidence_support, retrieval_precision, retrieval_recall, hallucination_rate,
abstention_correctness, freshness_ok, version_ok, validator_success, unanswered_rate, mean latency, tokens. **Regression** if
accuracy or citation_correctness drop > `eval_regression_threshold` (default 0.05) vs the previous run on the same dataset version.

**Config captured per run**: model ids, prompt versions, chunker/scoring versions, embedding identity, plugin version, knowledge
counts — so a regression can be attributed.

**Scheduling**: `kp eval run <domain> [--fail-on-regression]`; automatic after each completed pipeline run for the domain (setting);
periodic (`eval_interval_hours`, default 24). CI: contract test that every plugin's evaluation.yaml validates; a fake-provider smoke
run of the runner in the integration suite.

**Self-correction loop** (*16*): a run's failure analysis is written as structured findings (`no_retrieval`, `wrong_item_retrieved`,
`stale_cited`, `missing_concept`, `judge_incorrect`) with suggested action (add source / re-crawl / review item). Actions are
surfaced in the UI; **no automatic knowledge edits**.

---

## 11. Migration / backward compatibility

* All schema changes additive with defaults; migration 0003 backfills origin/provenance/polarity/retrieved_at/source_class.
* Existing API responses gain fields; TypeScript client updated; nothing renamed.
* `scoring` bumps rule version; old items keep their stored factors/version until next rescore (explainability preserved).
* Plugin contract: new methods have defaults → existing plugins (`powerbi`, `example`) need no change; contract tests extended.
* `evaluation.yaml` new keys optional.
* Settings: DB overrides env; env remains the fallback, so `.env`-only deployments keep working.
* Snapshots are new artefacts; nothing consumes them yet.

---

## 12. Implementation order and estimates

| Order | Scope | Est. |
|---|---|---|
| **P0** | Evaluation runner, tables, checks + judge, metrics, regression detection, CLI, API, Evaluation page, dashboard tile, scheduling hook | 1.5 days |
| **P0b** | Model providers: OpenAI-compatible + Anthropic adapters, model listing, settings table + API + Settings page, re-embed job (user request) | 1 day |
| **P1** | Full snapshot: canonical serialisers, export gate, manifest + hash, object store layout, zip download, Snapshots page, plugin export hook, README/knowledge.md/html renderers | 1.5 days |
| **P2** | Origin/provenance/polarity/details columns, backfill, extractor schema (`polarity`, example `details`), USER/ORGANIZATION knowledge entry, drawer chips, scoring v2 factors | 1 day |
| **P3** | Relations table, automatic `example_of`/`depends_on` derivation, `needs_revalidation` propagation + revalidate job, review tab | 1 day |
| **P4** | Delta snapshots (diff engine + manifest + UI) | 0.5 day |
| **P5** | AI knowledge source (`ai/knowledge.jsonl`, `ai/knowledge.md`) — largely shared with P1 renderers | 0.5 day |
| **P6** | Examples/negative export files, negative-knowledge UI filter, evaluation questions for negatives | 0.5 day |
| **P7** | SSRF guard, source-independence detection (document similarity → canonical_document_id, independence in scoring), section-level chunk hashes, per-source interval UI, extended metrics, export/eval scheduling, `kp autostart` (Windows Task Scheduler), optional `falsify` job | 1.5 days |

Each step ends with: tests (unit + contract + integration), lint, typecheck, live check in the browser, commit.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| LLM-as-judge bias/variance (local 8B model) | Mechanical checks gate the verdict; judge rationale stored; judge model selectable (API model can be used for judging only) |
| Evaluation cost/time (each question = retrieval + answer + judge) | 9 questions ≈ 2–3 min locally; scheduled off-peak; sample subsets allowed |
| Snapshot reproducibility drift | Canonical serialisation + excluded volatile fields + integrity verify endpoint + test that two consecutive exports of unchanged data hash identically |
| Delta correctness depends on stable ids | Ids are UUIDs never reused; superseded versions keep their id; test with fixture changes |
| Embedding model switch invalidates vectors | Identity stored per vector; switch triggers re-embed job; search excludes mismatched vectors |
| API keys entered in UI | Stored in local `settings` table (single-user, local deployment), masked in API responses, never logged; env var remains the recommended path — see D8 |
| Scope creep | Priority order enforced; P7 items individually approvable |
| Extractor changes (polarity/details) alter output quality | Prompt version bump → evaluation run before/after (this is exactly what P0 is for) |

---

## 14. Decisions needed before implementation

* **D1 — Quote-less knowledge.** Keep the hard rule "extractor output without a verbatim quote is dropped" (it is the platform's strongest
  quality control) and introduce DERIVED/SYNTHESIZED only through explicit paths: (a) a *synthesis* stage that combines existing verified
  items and records `derived_from` relations, (b) human/API-authored items. Recommended. Alternative: let the extractor emit quote-less
  items marked DERIVED — not recommended (that is how hallucinations enter).
* **D2 — `DISCOVERED` state for items.** Not applicable in this model (documents are discovered; items begin at EXTRACTED). Keep as is.
* **D3 — USER/ORGANIZATION knowledge entry.** Requires a new "Add knowledge" form/API (statement, explanation, optional URL/evidence text,
  provenance USER or ORGANIZATION). Include in P2? Recommended yes (small).
* **D4 — Active falsification via web search.** Only meaningful with a search provider (SearXNG). Proposal: optional `falsify` job for
  risk classes ≥ `technical`, run when a provider is configured; otherwise internal contradiction detection only. Include in P7.
* **D5 — Scoring v2 factors** (freshness, version match, contradiction status, independence). Changes confidence numbers of existing
  items on rescore. Recommended; historical factors remain stored.
* **D6 — Snapshot inclusion.** Include SUPERSEDED (historical flag) and STALE/CONFLICTED (flagged) so consumers see the truth state;
  exclude CANDIDATE/EXTRACTED/REJECTED from knowledge.jsonl. Confirm.
* **D7 — Human-readable formats.** Markdown + HTML. PDF not proposed (adds a rendering dependency for little value). Confirm.
* **D8 — API keys.** Stored in the local settings table (masked) so the UI works standalone, with env vars as the alternative. Confirm,
  or require env-only.
* **D9 — Media (OCR/video).** Defer entirely (no media is collected today; nothing violates the requirement). Confirm.
* **D10 — Auto-start.** `kp autostart install` creating a Windows Task Scheduler entry (at logon: `docker compose up -d postgres` then
  `kp serve`). Optional command, off by default. Confirm.
* **D11 — Order.** P0 → P0b (model settings, your request) → P1 → P2 → P3 → P4 → P5 → P6 → P7, committing after each. Confirm or reorder.

---

## 15. Delivery status (2026-09-14)

All priorities approved on 2026-09-13 are implemented, tested (73 tests: unit, contract, integration against PostgreSQL)
and committed in order:

| Priority | Commit | Delivered |
|---|---|---|
| P0 self-evaluation | earlier | evaluation runner, checks, judge, metrics, history, regression detection, findings, Evaluation page, scheduling |
| P0b model settings | earlier | Settings page: local/API providers, model listing via probe, per-purpose models, masked keys, re-embed |
| P1 canonical snapshot | earlier | export gate, canonical serialisation, manifest + integrity hash, verify, zip, Snapshots page, `kp export` |
| P2 origin/provenance | earlier | origin/provenance/polarity/details, source classes, human knowledge entry, score@2.0 |
| P3 dependency graph | earlier | relations, derived edges, propagation, revalidate job, Review "needs revalidation" |
| P4 delta snapshots | `6bfffe8` | diff engine over stored files, `changed_fields`, delta files, base selector UI, `kp export delta` |
| P5 AI knowledge source | `f59600f` | self-contained `text` with numbered sources, `usage` hint, human citations, `ai/index.json`, consumption guide |
| P6 examples / negative | `af7678d` | `negative_coverage` metric + `limitation_not_surfaced` finding, answer-context labels, `anti_pattern`, entry fields |
| P7a security & delta | `8fbd091` | SSRF guard (every hop), source independence (mirrors), section-level chunk hashes, Sources editor |
| P7b ops | `d918b00` | `ops` metrics (queue, dead letters, p95 latency, storage, schedule), snapshot scheduling, `kp ops` |
| P7c optional | `78d8eeb` | active falsification (flag-only), `kp autostart install` (Task Scheduler / systemd / launchd) |

Deliberately not built (per decisions D9/D10 and the requirement analysis): media handling (images, video,
OCR) and PDF export. `sources.reliability_history` remains unused — the per-source reliability signal is the
evaluation history plus the conflict/stale record, which the dashboard already exposes.

## 16. Post-audit hardening (2026-09-14)

The independent baseline audit (Functional beta; snapshot and plugin architecture "yes with conditions") led to
the following, each with regression tests (139 tests, 82 % coverage):

| Item | Commit | What changed |
|---|---|---|
| P0.1 local API origins | `f582d62` | CORS limited to own origins; Origin guard refuses cross-origin mutations |
| P0.2 review flags | `3a615ca` | `needs_review` separate from `needs_revalidation`; falsification never auto-cleared; explicit dismiss + review log |
| P0.3 trust state in exports | `ddcc1c0` | flags + `evidence_status` exported; `usage: caution` and a `Caution:` first line for flagged items |
| P0.4 delta evidence | `ac63291` | modified records carry `changes[id]` (changed fields, before/after); base + delta reproduces head files |
| P0.5/0.6 evaluation | `73ed868` | uncited answers judged and classed; coverage signal; primary `failure_class`; coverage / uncited metrics |
| P1.1/1.2 export schema | `ea10dc0` | JSON Schema + vocabulary shipped in every snapshot, validated at build/verify; `export_schema_version` + changelog |
| P1.3 derived knowledge | `8a8921f` | DERIVED/SYNTHESIZED entry with premises + rationale; no fabricated quote; premise-bound scoring |
| P1.4 freshness | `a53b06e` | `last_source_checked_at` / `last_content_changed_at`; unchanged crawls confirm, never re-verify |
| P1.5 type semantics | `c88ee63` | plugin-declared polarity/role/labels; core vocabulary assumptions removed |
| P1.6 contradictions | `6b8d247` | polarity-aware pairing; `compatible_under` relations keep qualified verdicts |
| P1.7 propagation | `a38463a` | transitive, cycle-safe, deterministic; chains settle in dependency order |
| P1.8 source classes | `0e1580d` | curated `source_class`; authority never implies official; re-derivation on sync |
| P1.9 independence | `91c3d71` | fingerprint + rel=canonical mirrors, declared `mirror_of`, copied-excerpt rule |
| P1.10 netguard | `4d100e2` | literal canonicalisation, TTL DNS cache, resolve-once/connect-to-validated-IP transport |
| P1.11 tests | `7b50779` | queue/worker/scheduler/CLI behaviour tests |

### 16.1 P2 — operating at realistic scale (2026-09-14)

| Item | Commit | What |
|---|---|---|
| P2.0.1 baseline metrics | `59232c9` | `knowledge_metrics()` in ops, `kp ops --json`; small-KB baseline stored under `docs/reports/p2/` |
| P2.0 corpus sources | `06740a1` | six more official Microsoft Learn sections (reports/visuals, Service, developer, support, Fabric, enterprise) |
| P2.0.3 / P2.0.4 | `8e41cf9` | incremental-update + snapshot lifecycle proof through the real pipeline (fixtures) |
| live crawl defect | `8e6b754` | GuardedTransport died on redirect hops (`RequestNotRead`) — found by the P2.0 crawl |
| delta exactness | `94ecb92` | delta@1.2 ships every differing record: base + delta reproduces head |
| P2.1 supersede | `41946c4` | supersede-by-new-version, dependency carry-over, review action, historical export |
| P2.2 retrieval policy | `76225d2`, `2e7bc60` | CANDIDATE excluded unless asked; STALE/CONFLICTED/flagged labelled; `answer_version` in eval config |
| P2.3 documents export | `8c82a33` | `documents.jsonl` (schema 1.5): self-contained evidence integrity, gate + delta + apply cover it |
| P2.4 export apply | `23c13e7` | `kp export apply` rebuilds the head byte for byte and verifies against `head_files` (delta@1.3) |
| P2.5 language | `ac59b22` | per-domain text-search configuration; no English assumption in core |
| P2.6 / P2.7 designs | `0065f47` | ADR 0003 validator isolation (tier 0 implemented), ADR 0004 authentication (exposure guard implemented) |
| P2.8 discovery | `4b016b1` | ADR 0005: scored, filtered candidates; opt-in recurring discovery; never auto-approved |
| P2.10 second domain | `6c80f49` | synthetic `roboticslab` plugin through the whole pipeline |
| P2.0 / P2.9 live run | `f87bbc3`, `82fe99b`, `9b1a4e0` | 186-document crawl (6,100 items), machine-restart recovery, defects found live: silent loss of failed sections, ILIKE subjects, revalidation churn |
| P2.11 report | `docs/reports/p2/final-report.md` | recovery, growth, evaluation (coverage 0.45→1.0, citation 0.55→1.0, accuracy unchanged), stability, snapshot v18 + delta v20 + apply verified |

### 16.2 P3 — retrieval and answer intelligence (ADR 0006, 2026-09-15)

| Item | Commit | What |
|---|---|---|
| diagnosis + offline harness | `9478f8d` | `docs/reports/p3/diagnosis.md`; `kp eval retrieval` (concept recall@K, precision, MRR, authoritative hit) |
| stages 1–2 query analysis | `fa79bf9` | normalisation, hyphen/compound variants, plugin synonyms, lexemes, entities, deterministic intent |
| stages 3–6 retrieval | `f70ea96` | vector + weighted-OR lexical + rare-term + entity channels; explainable ranking; diversity; bounded expansion |
| stages 7–10 answers | `5409510` | 12-item context with source/authority/excerpt, deterministic plan, lexeme-space completeness, one targeted regeneration |
| guards | `1344ef6` | no regeneration on abstention / uncited answers; validator-failed label + penalty; single-scan document frequencies; frozen P2 prompt |
| stage 12 observability | `900e12a` | `/api/search` signals + explanation, `/api/ask` mode/plan/regeneration/timings, Search & Ask page, `kp ask --explain` |
| phase 8 tests | `5ba350f` | 14 DB-backed regression/adversarial cases; roboticslab retrieval from plugin declarations; `type_match` signal; plugin cues first |
| stage 11 + report | `docs/reports/p3/final-report.md` | runs A/B/C/D (`scripts/p3_compare.py`), failure decomposition, performance, second-domain validation |

Awaiting approval (designed, not built): validator runner tiers 1–2 (ADR 0003), proxy/token authentication
(ADR 0004), discovery auto-approval policy (ADR 0005). Still deferred by decision: media handling, PDF export,
fuzzy source similarity, locator refresh after section reorders, quality dashboards.
