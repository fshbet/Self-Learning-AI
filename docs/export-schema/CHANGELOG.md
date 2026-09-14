# Canonical Knowledge Snapshot — export schema changelog

The **export schema version** (`manifest.export_schema_version`, also stamped on every file under `schema/`) is the
contract between the platform and external consumers. It is versioned independently of:

| Field in `manifest.json` | What it versions |
|---|---|
| `export_schema_version` (and legacy `schema_version`) | the shape and meaning of the exported files — this document |
| `platform_version` | the application that produced the snapshot |
| `database_schema_version` | the Alembic revision of the producing database (informational only) |
| `plugin_version` / `plugin_api_version` | the domain plugin and the plugin contract |

**Compatibility rule**

* **Minor** bump (`1.1 → 1.2`): only *adds* optional fields, files or vocabulary values. A consumer written for any
  `1.x` keeps working; unknown fields must be ignored.
* **Major** bump (`1.x → 2.0`): renames or removes fields or files, or changes canonical serialisation, hashing or the
  meaning of an existing vocabulary value. Consumers must be updated; the platform never re-labels a breaking change
  as minor.

The JSON Schema files in this directory are generated with `kp export schema` from the pydantic models in
`core/export/schema.py`; the same files are shipped inside every snapshot under `schema/`, and a test keeps the
committed copy identical to the generated one.

## 1.2 — 2026-09-14

Additive.

* `schema/*.schema.json` (JSON Schema draft 2020-12) and `schema/vocabulary.json` shipped inside every full snapshot
  and hashed like every other file; `verify` also validates the stored files against them.
* `manifest.export_schema_version` (same value as `schema_version`, which stays for 1.0 consumers) and
  `manifest.database_schema_version`.
* `ai/knowledge.jsonl` records are now a formal type (`AIKnowledgeRecord`).
* `evidence_type` vocabulary gains `derivation` (the recorded reasoning of a DERIVED/SYNTHESIZED item).
* `knowledge.jsonl` records with `origin` DERIVED/SYNTHESIZED carry `provenance: DERIVED` and `derived_from`
  dependencies; their AI record text ends with a `Derived from` / `Synthesized from` block naming the premises.
* `evidence_type` vocabulary gains `derivation` (the recorded reasoning of a DERIVED/SYNTHESIZED item).
* `knowledge.jsonl` records with `origin` DERIVED/SYNTHESIZED carry `provenance: DERIVED` and `derived_from`
  dependencies; their AI record text ends with a `Derived from` / `Synthesized from` block naming the premises.

## 1.1 — 2026-09-14

Additive.

* Trust state on `knowledge.jsonl` / `negative.jsonl` records: `needs_revalidation`, `revalidation_reason`,
  `needs_review`, `review_kind`, `review_reason`, `review_flagged_at`, `evidence_status`.
* AI Knowledge Source records: `usage` is `caution` for flagged items, `caution_reasons`, the flag fields and
  `evidence_status`; the `text` opens with `Caution:` / `Historical:` where applicable.

## 1.0 — 2026-09-13

Initial contract: `manifest.json`, `knowledge.jsonl`, `evidence.jsonl`, `sources.jsonl`, `relationships.jsonl`,
`examples.jsonl`, `negative.jsonl`, `glossary.json`, `conflicts.json`, `changelog.jsonl`, `ai/knowledge.jsonl`,
`ai/index.json`, `ai/knowledge.md`, `knowledge.html`, `README.md`, optional `ext/`; per-file SHA-256 and the
integrity hash over sorted `path:sha256` lines (manifest excluded).
