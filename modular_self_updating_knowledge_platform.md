# Modular Self-Updating Knowledge Intelligence Platform

| Field | Value |
|---|---|
| Document type | Architecture and design specification |
| Status | Draft |
| Version | 0.2.0 |
| Last updated | 2026-09-13 |
| Owner | TBD |
| Audience | Platform architects, engineers, domain experts, project sponsors |

## Purpose and scope

This document describes the vision, architecture, principles, and delivery plan for a modular, domain-independent platform that continuously collects, verifies, versions, and serves trustworthy knowledge.

It is a design document, not an implementation manual. It defines *what* the platform must do and *why*, and recommends *how* at the level of components and interfaces. Detailed API specifications, database schemas, and operational runbooks are expected to live alongside the code and reference this document.

**In scope:** platform architecture, domain plugin model, knowledge lifecycle, verification strategy, evaluation strategy, governance, and phased delivery.

**Out of scope:** custom model training, UI design, and detailed infrastructure sizing (see [Section 46](#46-what-should-not-be-built-initially)).

## Table of contents

1. [Vision](#1-vision)
2. [What the platform is](#2-what-the-platform-is)
3. [Core architectural principle](#3-core-architectural-principle)
4. [High-level architecture](#4-high-level-architecture)
5. [Non-functional requirements](#5-non-functional-requirements)
6. [Domain plugin architecture](#6-domain-plugin-architecture)
7. [Source discovery](#7-source-discovery)
8. [Source registry](#8-source-registry)
9. [Web discovery method](#9-web-discovery-method)
10. [Relevance-driven crawling](#10-relevance-driven-crawling)
11. [Collection layer](#11-collection-layer)
12. [Responsible collection](#12-responsible-collection)
13. [Raw repository vs knowledge repository](#13-raw-repository-vs-knowledge-repository)
14. [Knowledge item](#14-knowledge-item)
15. [Evidence and provenance](#15-evidence-and-provenance)
16. [Quality scoring](#16-quality-scoring)
17. [Source independence](#17-source-independence)
18. [Contradiction detection](#18-contradiction-detection)
19. [Active falsification](#19-active-falsification)
20. [Domain-specific validation](#20-domain-specific-validation)
21. [Knowledge graph](#21-knowledge-graph)
22. [Vector search](#22-vector-search)
23. [Versioning and freshness](#23-versioning-and-freshness)
24. [Change detection](#24-change-detection)
25. [Paywalls and login-protected information](#25-paywalls-and-login-protected-information)
26. [Images and videos](#26-images-and-videos)
27. [Storage architecture](#27-storage-architecture)
28. [Pipeline reliability](#28-pipeline-reliability)
29. [AI orchestration](#29-ai-orchestration)
30. [Answer generation](#30-answer-generation)
31. ["Perfect answer" strategy](#31-perfect-answer-strategy)
32. [Human review](#32-human-review)
33. [Self-learning](#33-self-learning)
34. [Golden evaluation dataset](#34-golden-evaluation-dataset)
35. [Measuring improvement](#35-measuring-improvement)
36. [Training dataset generation](#36-training-dataset-generation)
37. [Domain skills](#37-domain-skills)
38. [Security and governance](#38-security-and-governance)
39. [Observability](#39-observability)
40. [Cost-control strategy](#40-cost-control-strategy)
41. [Deployment strategy](#41-deployment-strategy)
42. [Recommended first technology stack](#42-recommended-first-technology-stack)
43. [Suggested project structure](#43-suggested-project-structure)
44. [Testing strategy](#44-testing-strategy)
45. [Development phases](#45-development-phases)
46. [What should NOT be built initially](#46-what-should-not-be-built-initially)
47. [Definition of success for the first version](#47-definition-of-success-for-the-first-version)
48. [Risks and mitigations](#48-risks-and-mitigations)
49. [Final conceptual architecture](#49-final-conceptual-architecture)
50. [Core philosophy](#50-core-philosophy)
51. [End goal](#51-end-goal)
- [Appendix A — Glossary](#appendix-a--glossary)
- [Appendix B — Open questions](#appendix-b--open-questions)
- [Appendix C — Document history](#appendix-c--document-history)

---

## 1. Vision

Build a modular platform that continuously discovers, collects, evaluates, structures, verifies, versions, and serves high-quality knowledge from the Internet and authorized data sources.

The first domain will be **Power BI**, but the architecture must remain domain-independent so that additional domains such as Tableau, Python, SQL, robotics, manufacturing equipment, painting machines, or other technologies can be added later without redesigning the core platform.

The central principle is:

> **Build the knowledge infrastructure first; keep the AI model replaceable.**

The platform should not attempt to blindly "train itself." Instead, it should create a trustworthy, versioned knowledge repository that can later support RAG, AI agents, fine-tuning, evaluation datasets, or model training.

---

## 2. What the platform is

The platform has five major responsibilities:

1. **Discover** relevant information.
2. **Collect** information from permitted sources.
3. **Transform** information into structured knowledge.
4. **Verify and maintain** that knowledge over time.
5. **Serve** the knowledge to AI applications and future training pipelines.

Conceptually:

```text
Sources
   ↓
Discovery
   ↓
Collection
   ↓
Extraction
   ↓
Quality filtering
   ↓
Verification
   ↓
Knowledge representation
   ↓
Versioned repository
   ↓
RAG / Agents / Training / Analytics
```

---

## 3. Core architectural principle

Separate the platform into two layers.

### Core platform

The core knows how to:

- discover sources
- crawl and collect permitted content
- extract information
- classify information
- score quality
- detect duplicates
- detect contradictions
- create knowledge items
- maintain evidence and provenance
- version knowledge
- monitor freshness
- build embeddings
- provide retrieval
- evaluate AI answers
- generate training datasets

The core does **not** need to know what Power BI, Tableau, or robotics is.

### Domain plugins

Each domain defines:

- what the domain contains
- important entities
- terminology
- source priorities
- relevant websites
- domain-specific document types
- domain-specific validators
- domain-specific relationships
- domain-specific skills
- domain-specific tests

Examples:

```text
Power BI plugin
Tableau plugin
SQL plugin
Python plugin
Robotics plugin
Industrial Painting Machine plugin
```

---

## 4. High-level architecture

```text
                         KNOWLEDGE PLATFORM
                                  |
        ---------------------------------------------------------
        |             |             |             |             |
        v             v             v             v             v
   Discovery      Collection    Extraction     Validation    Repository
        |             |             |             |             |
        ---------------------------------------------------------
                                  |
                                  v
                         Knowledge Graph
                         + Vector Search
                                  |
                    -----------------------------
                    |             |             |
                    v             v             v
                   RAG          AI Agents     Training
                    |
                    v
                 User Apps
```

---

## 5. Non-functional requirements

The following qualities apply to every component. Initial targets are proposals for the first version and should be confirmed by the project owner before Phase 1 begins.

| Quality | Requirement | Initial target (v1) |
|---|---|---|
| Correctness | Every served knowledge item is traceable to evidence and a source | 100% of `VERIFIED` items have ≥ 1 evidence record |
| Auditability | Every state change to a knowledge item is recorded with actor, timestamp, and reason | Full change history retained |
| Reproducibility | A pipeline run can be replayed from raw evidence to produce the same knowledge items | Raw evidence retained; extractor/model versions recorded |
| Freshness | Stale knowledge is detected within a bounded time after the source changes | Priority sources re-checked at least weekly |
| Availability | Retrieval API is available for consumers | 99% monthly (single-region, v1) |
| Latency | Hybrid retrieval returns results promptly | p95 < 1 s for retrieval; answer generation bounded by model latency |
| Scalability | Storage and pipeline scale horizontally per stage | 10⁵–10⁶ knowledge items per domain without redesign |
| Portability | Components can move between providers without rewriting business logic | No provider-specific code outside adapter modules |
| Compliance | Collection respects legal, contractual, and technical access rules | Zero bypass of access controls; permissions recorded per source |
| Cost | AI spend is bounded and attributable | Per-stage token budgets with alerts |
| Security | Untrusted content cannot direct platform actions; secrets never stored in the repository | See [Section 38](#38-security-and-governance) |

---

## 6. Domain plugin architecture

Every technology should be represented as a domain.

A domain should contain configuration and specialized capabilities rather than duplicate the entire platform.

A domain definition should describe:

- domain name
- description
- taxonomy
- entities
- concepts
- relationships
- terminology
- source catalog
- source priorities
- collection rules
- validation rules
- knowledge types
- specialized skills
- testing methods

For Power BI, the domain might contain:

```text
Power BI
 ├── DAX
 ├── Power Query / M
 ├── Data Modeling
 ├── Visualization
 ├── Power BI Service
 ├── Administration
 ├── Security
 ├── Gateways
 ├── REST API
 ├── Fabric integration
 └── Troubleshooting
```

A robotics domain would have a completely different taxonomy while using the same platform.

### Plugin contract

To keep the core genuinely domain-independent, every plugin should implement one explicit, versioned interface. The core must never call domain code outside this interface.

Conceptually, a plugin provides:

```text
manifest          name, version, description, compatible core version
taxonomy()        hierarchical topics for the domain
entities()        entity types and canonical naming rules
relationships()   allowed relationship types between entities
terminology()     glossary, synonyms, abbreviations
sources()         seed source catalog with priorities and collection rules
classifiers()     optional domain-specific classification hints
validators()      domain validators (see Section 20)
skills()          domain skills (see Section 37)
evaluation_set()  golden questions (see Section 34)
```

Rules:

- Plugins are loaded by manifest; the core discovers capabilities at runtime rather than hard-coding domain names.
- Plugins declare the core interface version they target; incompatible plugins fail to load with a clear error rather than degrading silently.
- Plugins never access the database or object storage directly; they receive and return typed objects through the core.
- Plugin behaviour is covered by contract tests (see [Section 44](#44-testing-strategy)) so that a change in the core cannot silently break a domain.

---

## 7. Source discovery

The system should not simply crawl the entire Internet.

It should use a controlled discovery strategy.

### Source categories

Prioritize:

1. Official documentation
2. Government and regulatory sources
3. Standards organizations
4. Official technical publications
5. Academic sources
6. Manufacturer documentation
7. Open-source repositories
8. Industry publications
9. Expert technical sources
10. Community discussions
11. General blogs

Source priority should depend on the domain.

For Power BI, Microsoft documentation should receive very high priority.

For a robotics domain, manufacturer manuals and official engineering documentation may be more important.

---

## 8. Source registry

Maintain a registry for every source.

Important properties include:

- source identity
- domain
- URL
- publisher
- source type
- authority level
- access type
- license information where known
- collection permission/status
- crawl frequency
- last checked
- last changed
- reliability history
- machine-readable access signals observed (robots.txt rules, opt-out signals, rate-limit responses)
- contact/takedown information where published

The source registry becomes the control system for collection.

---

## 9. Web discovery method

The research agent should work iteratively.

Instead of:

```text
Search → scrape everything
```

use:

```text
Search
  ↓
Rank results
  ↓
Select valuable sources
  ↓
Read source
  ↓
Identify missing information
  ↓
Search for missing information
  ↓
Follow references
  ↓
Search primary sources
  ↓
Stop when additional research has low value
```

The agent should be able to follow:

- hyperlinks
- citations
- references
- referenced regulations
- referenced standards
- related documents
- related entities
- version/release references

This produces deeper research without indiscriminate crawling.

---

## 10. Relevance-driven crawling

Crawl depth should not be the only control.

Every discovered page should receive a relevance assessment.

Factors include:

- relevance to the current domain
- relevance to the current research question
- source authority
- novelty
- evidence value
- relationship to already-known information

High-value links should be explored first.

Low-value branches should be stopped.

This creates a search tree that spends resources where they are most useful.

---

## 11. Collection layer

The collection layer should support multiple source types.

### Web pages

Collect permitted textual content and metadata.

### PDFs

Extract text, tables, headings, references, and metadata.

### APIs

Use official APIs where available.

### Git repositories

Collect permitted documentation, examples, metadata, and relevant code.

### Videos

Prefer:

- metadata
- title
- description
- transcript where legitimately available
- timestamps
- extracted concepts
- code/examples

Do not assume that storing the entire video is necessary.

### Images

Prefer:

- image URL/reference
- surrounding context
- OCR when appropriate
- visual description
- extracted diagram relationships

Only retain the actual image when there is a legitimate reason and the applicable rights/terms permit retention.

---

## 12. Responsible collection

Collection must be a good citizen of the Internet and must be defensible to source owners.

Minimum rules for every collector:

- Honour `robots.txt` (including `Crawl-delay`) and machine-readable opt-out signals such as TDM reservation headers and `noai`/`noindex` directives.
- Identify the crawler with a descriptive `User-Agent` string that includes a contact address.
- Apply per-host rate limits and concurrency limits; back off exponentially on `429` and `5xx` responses and on `Retry-After` headers.
- Use conditional requests (`ETag` / `If-Modified-Since`) so unchanged pages are not re-downloaded (see [Section 24](#24-change-detection)).
- Respect published terms of service; record in the source registry when terms restrict storage, processing, or redistribution.
- Never bypass authentication, paywalls, CAPTCHAs, or technical access controls (see [Section 25](#25-paywalls-and-login-protected-information)).
- Provide a takedown/removal workflow: a source owner's removal request must propagate to raw evidence, derived knowledge items, embeddings, and generated datasets (see [Section 38](#38-security-and-governance)).

Violations of these rules are defects, not tuning parameters.

---

## 13. Raw repository vs knowledge repository

Maintain two conceptual layers.

### Raw evidence store

Contains permitted original or minimally processed material.

Examples:

- HTML snapshots
- PDFs
- authorized exports
- source files
- media where appropriate

This is mainly for auditability and reprocessing.

### Curated knowledge store

Contains:

- facts
- concepts
- definitions
- procedures
- relationships
- examples
- code
- claims
- evidence
- confidence
- versions

The curated layer is what the AI primarily uses.

---

## 14. Knowledge item

Every extracted piece of knowledge should become a versioned knowledge item.

A knowledge item should have:

- unique ID
- domain
- knowledge type
- subject
- predicate/relationship
- object/value
- explanation
- source
- evidence
- confidence
- publication date
- effective date where applicable
- expiration/valid-until date where applicable
- product/version information
- first discovered date
- last verified date
- status
- previous version
- language
- extraction method and extractor version (model, prompt, or rule version that produced the item)
- content hash of the normalized statement (for deduplication)

### Illustrative schema

The exact schema belongs with the code; this example shows the intended shape.

```yaml
id: 01J9KZ6Q3F8V2XW1N4R7T5M9AB        # ULID or UUID
domain: powerbi
knowledge_type: fact
subject: CALCULATE
predicate: modifies
object: filter context
explanation: >
  CALCULATE evaluates an expression in a modified filter context ...
language: en
product_version: "Power BI Desktop 2026-08"
publication_date: 2026-08-14
effective_date: 2026-08-14
valid_until: null
first_discovered_at: 2026-09-01T10:15:00Z
last_verified_at: 2026-09-13T08:00:00Z
status: VERIFIED
confidence: 0.92
verification_level: 4
evidence_ids: [ev_7f3a..., ev_9c21...]
source_ids: [src_ms_docs_dax_calculate]
previous_version_id: 01J9K...
extraction:
  method: llm
  extractor_version: extract-facts@1.4.0
  model: <model id>
content_hash: sha256:3b1f...
```

### Confidence

Confidence is a number in the range `0.0–1.0` derived from the explainable quality factors in [Section 16](#16-quality-scoring). It is always stored alongside the verification level from [Section 31](#31-perfect-answer-strategy); neither replaces the other. A change to confidence must record the reason.

### Status lifecycle

Possible statuses:

```text
DISCOVERED
EXTRACTED
CANDIDATE
SUPPORTED
VERIFIED
CONFLICTED
STALE
REJECTED
SUPERSEDED
```

Allowed transitions:

| From | To | Trigger |
|---|---|---|
| DISCOVERED | EXTRACTED | Extraction produced a structured statement |
| EXTRACTED | CANDIDATE | Passed generic quality filters |
| EXTRACTED | REJECTED | Failed quality filters or is a duplicate |
| CANDIDATE | SUPPORTED | At least one evidence record linked |
| CANDIDATE | REJECTED | No supporting evidence found |
| SUPPORTED | VERIFIED | Required verification level reached |
| SUPPORTED / VERIFIED | CONFLICTED | Contradicting evidence detected |
| CONFLICTED | VERIFIED / SUPERSEDED / REJECTED | Conflict resolved (automatically or by human review) |
| VERIFIED | STALE | Validity period expired or source changed without re-verification |
| STALE | VERIFIED | Re-verified against current sources |
| STALE / VERIFIED | SUPERSEDED | A newer version of the item was verified |
| any | REJECTED | Human reviewer rejection or takedown |

`SUPERSEDED` and `REJECTED` are terminal for that version; a new version may still be created.

---

## 15. Evidence and provenance

Never allow an AI-generated statement to become trusted knowledge without provenance.

Every important claim should point back to evidence.

For example:

```text
Knowledge
   ↓
Claim
   ↓
Evidence
   ↓
Source
   ↓
Original document/page/section/timestamp
```

The repository should answer:

- Where did this fact come from?
- When was it collected?
- When was it verified?
- Which sources support it?
- Which sources contradict it?
- What replaced the previous version?
- Why was its confidence changed?
- Which extractor, model, and prompt version produced it?

Provenance should be modelled along the lines of the W3C PROV data model (entities, activities, agents) so that human reviewers, extraction runs, and models are all first-class actors in the audit trail.

Evidence records should store the exact excerpt (or a stable locator: URL + section + character offsets, or timestamp range for media) plus the content hash of the raw document at collection time, so that evidence remains verifiable even if the live source later changes.

---

## 16. Quality scoring

Source quality and knowledge quality should be separate.

### Source quality

Consider:

- authority
- reputation
- primary vs secondary source
- reliability history
- publication ownership
- transparency
- technical specificity

### Knowledge quality

Consider:

- evidence strength
- source agreement
- source independence
- recency
- consistency
- specificity
- reproducibility
- domain validation

The resulting score should be explainable rather than a mysterious number.

Each score should be stored together with its contributing factors and the scoring-rule version, so that a score can be recomputed when rules change and explained to a reviewer on demand.

---

## 17. Source independence

Multiple websites repeating the same statement should not automatically count as multiple independent confirmations.

The system should attempt to identify:

```text
Original source
    ↓
Article A
    ↓
Article B
    ↓
Article C
```

This is effectively one evidence chain.

Independent sources should receive more weight.

Practical signals for detecting derivative content include near-duplicate text detection (for example MinHash/SimHash), explicit citations, shared publication ownership, and publication-date ordering.

---

## 18. Contradiction detection

The collector should actively look for disagreement.

When two sources disagree:

```text
Claim A
   vs
Claim B
```

the system should create a conflict record rather than silently choosing one.

It should investigate:

- publication date
- product version
- geographic applicability
- operating conditions
- definitions
- source authority
- whether one source supersedes another

Possible outcomes:

```text
A confirmed
B confirmed
A superseded
B superseded
Both valid under different conditions
Unresolved conflict
```

Unresolved conflicts are routed to human review ([Section 32](#32-human-review)); items involved remain in `CONFLICTED` status and are served with an explicit warning until resolved.

---

## 19. Active falsification

The research agent should not only search for evidence supporting a claim.

For important claims it should also search for evidence that could disprove the claim.

Research cycle:

```text
Claim
 ↓
Find supporting evidence
 ↓
Find contradictory evidence
 ↓
Find primary source
 ↓
Compare versions
 ↓
Determine confidence
```

This reduces confirmation bias.

---

## 20. Domain-specific validation

This is one of the most important modular features.

The core validator handles generic checks.

Each domain can add specialized validators.

### Power BI

Potential validation methods:

- DAX syntax validation
- M syntax validation
- example consistency
- documentation cross-checking
- executable/testable examples where an appropriate environment is available
- version compatibility checks

### Python

Potential validation:

- syntax
- execution
- unit tests
- expected output

### SQL

Potential validation:

- SQL parsing
- execution against test data
- expected results

### Robotics

Potential validation:

- engineering calculations
- physical constraints
- simulation
- manufacturer specifications
- safety-rule checks

The domain validator should never override safety or authorization requirements.

### Validator sandboxing

Any validator that executes code or queries (Python, SQL, DAX against a test model, simulations) must run in an isolated sandbox:

- separate process or container with no network access unless explicitly required
- CPU, memory, and wall-clock limits
- read-only access to fixtures; no access to platform credentials or the primary database
- inputs treated as untrusted, since they originate from collected content

Validator results are recorded as evidence with the validator name and version, so a validated claim can be re-validated when the validator changes.

---

## 21. Knowledge graph

Use relationships to connect knowledge.

Example:

```text
CALCULATE
   |
   +-- modifies --> Filter Context
   |
   +-- used with --> Measures
   |
   +-- related to --> Context Transition
   |
   +-- example --> Sales YTD
```

For robotics:

```text
Motor
   |
   +-- drives --> Joint
   |
   +-- requires --> Controller
   |
   +-- specification --> Torque
```

The graph allows the AI to reason across related concepts.

Relationship types are declared by the domain plugin; the core stores and traverses them generically. For the first version the graph can live in PostgreSQL (entity and relationship tables with recursive queries); a dedicated graph database is a later optimisation, not a prerequisite.

---

## 22. Vector search

Use embeddings for semantic retrieval.

The system should be able to retrieve relevant information even when the user's wording differs from the stored wording.

Vector search should complement, not replace:

- keyword search
- structured database queries
- knowledge graph traversal
- source filtering
- version filtering

A hybrid retrieval strategy is preferable.

### Embedding management

- Record the embedding model name, version, and dimension with every vector. Vectors from different models are never compared.
- Changing the embedding model triggers a planned re-embedding job; the old index remains available until the new one is complete.
- Chunking strategy (size, overlap, structural boundaries such as headings) is versioned and recorded, since it materially affects recall.
- Hybrid retrieval combines lexical search (for example PostgreSQL full-text search or BM25), vector similarity, and metadata filters, followed by optional re-ranking. Retrieval results carry the knowledge item ID so answers can cite them.

---

## 23. Versioning and freshness

Knowledge should never be treated as permanently correct.

Each item should have lifecycle information.

For technology domains, maintain:

- product version
- release date
- documentation version
- last verification
- validity period

Example:

```text
Feature X
Version: 2026
Status: VERIFIED
Last verified: 2026-09-13
```

When a new document arrives, compare it against existing knowledge.

Do not overwrite historical knowledge.

Create a new version and mark the old version as superseded if appropriate.

Every knowledge item version is immutable once written; corrections produce a new version linked through `previous_version_id`. Consumers can request "current" or "as of date" views.

---

## 24. Change detection

The collector should avoid reprocessing unchanged content.

For every source, maintain:

- content hash (SHA-256 of the normalized main content, so that layout-only changes do not trigger reprocessing)
- last modified information where available (`Last-Modified`, `ETag`, sitemap `lastmod`)
- retrieval timestamp
- version
- structural/content change indicators

Workflow:

```text
Check source
   ↓
Changed?
 ┌─┴─┐
No  Yes
↓    ↓
Skip  Process
```

For changed documents, identify which sections actually changed, and re-run extraction and verification only for knowledge items whose evidence points into the changed sections. Items whose evidence disappeared from the source are flagged `STALE`.

---

## 25. Paywalls and login-protected information

The system must distinguish between:

```text
Publicly accessible
Authorized subscription access
Licensed API access
Private/authenticated content
Restricted/bypassed access
```

The system should never bypass:

- authentication
- paywalls
- CAPTCHA
- technical access controls

If the organization has legitimate access, the platform can support:

- official APIs
- licensed exports
- permitted integrations
- authorized document ingestion

The source registry should record whether content can be:

- read
- stored
- processed
- used for training
- redistributed

These permissions are not necessarily the same.

For inaccessible material, the research agent should attempt to locate:

- the original primary source
- public government documents
- open research
- official announcements
- alternative authoritative sources

---

## 26. Images and videos

The system should optimize for **knowledge density**, not media volume.

### Images

Instead of storing every image:

```text
Image
 ↓
Analyze
 ↓
Description / OCR / diagram relationships
 ↓
Knowledge item
```

Retain the original image only when useful and permitted.

### Videos

Instead of storing entire videos:

```text
Video
 ↓
Authorized transcript
 ↓
Sections
 ↓
Timestamps
 ↓
Concepts
 ↓
Examples/code
```

Retain a source reference and timestamps where possible.

This prevents the repository from becoming dominated by large media files.

---

## 27. Storage architecture

Separate structured data from large files.

### Database

Store:

- knowledge items
- entities
- relationships
- sources
- evidence
- metadata
- versions
- confidence
- embeddings/references
- evaluation results

### Object storage

Store, where appropriate:

- PDFs
- HTML snapshots
- authorized exports
- images
- large documents

The platform should not require all raw content to remain in the primary database.

### Data management

- Schema changes are applied through versioned migrations; no manual schema edits.
- Object storage keys are content-addressed (hash-based) so duplicates are stored once and evidence references remain stable.
- Retention: raw evidence is retained for as long as any derived knowledge item references it, plus a configurable grace period; retention rules are recorded per source in the registry.
- Backups: the database is backed up on a schedule with tested restores; object storage uses versioning where the provider supports it.

---

## 28. Pipeline reliability

Collection, extraction, and verification are long-running, failure-prone jobs. They should be built on a small number of conventions rather than ad-hoc scripts.

- **Job queue.** Every pipeline stage consumes work from a queue and writes results transactionally. For the first version a PostgreSQL-backed queue is sufficient and avoids an extra database; a dedicated broker can replace it later behind the same interface.
- **Idempotency.** Every job is keyed (for example by source URL + content hash + stage + rule version) so re-running it produces the same outcome and no duplicates.
- **Retries and dead-lettering.** Transient failures retry with exponential backoff; persistent failures move to a dead-letter queue that is visible in monitoring and reviewable by an operator.
- **Checkpointing.** Multi-step research runs record progress so that an interrupted run resumes instead of restarting.
- **Run identity.** Every run has an ID that is stamped on all knowledge items, evidence, logs, and metrics it produces, enabling end-to-end tracing.
- **Backpressure.** Stages that call paid AI models enforce per-run and per-day budgets and pause rather than overspend (see [Section 40](#40-cost-control-strategy)).
- **Scheduling.** A scheduler triggers source checks according to the crawl frequency in the source registry; schedules are data, not code.

---

## 29. AI orchestration

The AI model should act primarily as an orchestrator and reasoning engine.

It should call tools such as:

- search
- crawl
- retrieve knowledge
- retrieve evidence
- compare claims
- inspect documents
- execute domain validators
- query the knowledge graph

The model itself should not be the source of truth.

The repository and evidence system are the source of truth.

### Tool interface

Tools are exposed to the model through a single, typed tool layer with explicit input/output schemas. Exposing them through a standard protocol such as the Model Context Protocol (MCP) is recommended so the same tools can be reused by different models and agent frameworks without rewriting integrations.

### Model abstraction

All model calls go through one provider-agnostic interface that records model ID, prompt version, token usage, latency, and cost. Switching or A/B-testing models must not require changes outside the adapter.

### Prompt-injection defence

All collected content is untrusted input. Web pages, PDFs, transcripts, and repository files may contain text written to manipulate an AI agent.

- Collected content is passed to the model as *data*, clearly delimited, never as instructions.
- Instructions found inside collected content are never executed; at most they are recorded as a signal that the source may be adversarial.
- Tool calls with side effects (writing knowledge, changing source permissions, triggering crawls) require the orchestrator's own decision path and, where impactful, human approval; they can never be triggered by content.
- Agents run with least-privilege tool allow-lists per task type.
- Prompt-injection test cases are part of the evaluation suite ([Section 34](#34-golden-evaluation-dataset)).

---

## 30. Answer generation

For a user question:

```text
User question
   ↓
Identify domain
   ↓
Understand intent
   ↓
Retrieve relevant knowledge
   ↓
Retrieve supporting evidence
   ↓
Check current version
   ↓
Reason
   ↓
Validate where possible
   ↓
Generate answer
```

The answer should expose uncertainty where necessary.

For important answers, provide:

- conclusion
- confidence
- evidence
- source references
- version/date
- limitations
- conflicting information when applicable

If retrieval returns nothing of sufficient verification level for the question's risk class, the platform should say so explicitly rather than generate an unsupported answer. An honest "not enough verified knowledge" is a valid outcome and is tracked as a metric ([Section 35](#35-measuring-improvement)).

---

## 31. "Perfect answer" strategy

The platform should never promise mathematical perfection.

Instead, define measurable confidence and verification levels.

Example:

```text
Level 0 — Unverified
Level 1 — Single source
Level 2 — Authoritative source
Level 3 — Multiple independent sources
Level 4 — Source + domain validation
Level 5 — Source + independent validation + expert approval
```

The required level should depend on risk.

Simple explanatory questions can use lower thresholds.

Critical technical, financial, safety, regulatory, or operational decisions should require much stronger evidence and potentially human review.

Each domain plugin declares its risk classes and the minimum verification level per class; the core enforces them at answer time.

---

## 32. Human review

The system should have a review queue.

Send items to humans when:

- authoritative sources conflict
- confidence is low
- the claim has high impact
- the source is ambiguous
- the AI cannot resolve a contradiction
- a domain-specific validator fails
- new information materially changes existing knowledge

Human decisions should become additional metadata and evaluation data.

Review decisions record the reviewer identity, timestamp, decision, and rationale, and are themselves versioned so that a later reviewer can see and, if necessary, overturn earlier decisions.

---

## 33. Self-learning

Do not initially train the underlying model automatically.

Instead, create a controlled improvement loop:

```text
Knowledge
   ↓
AI answer
   ↓
Evaluation
   ↓
Failure detected
   ↓
Determine cause
   ↓
Improve retrieval / prompts / rules / knowledge
   ↓
Re-evaluate
```

Only later consider fine-tuning or model training.

This prevents the model from learning incorrect information simply because it was collected.

---

## 34. Golden evaluation dataset

Each domain should maintain a test set.

For Power BI, questions could cover:

- DAX
- M
- data modeling
- relationships
- security
- administration
- refresh
- visualization
- APIs
- troubleshooting

Each question should have:

- expected answer
- acceptable alternatives
- required concepts
- authoritative sources
- optional executable tests
- difficulty
- risk level

Every significant system change should run against this dataset.

The dataset should also contain negative and adversarial cases: questions with no correct answer in the repository (to measure honest abstention), questions about superseded versions (to measure version awareness), and documents containing prompt-injection attempts (to measure robustness). The dataset is versioned, and results are always reported against a named dataset version.

---

## 35. Measuring improvement

Never define "better" by intuition alone.

Track:

- factual accuracy
- citation correctness
- source quality
- retrieval recall
- retrieval precision
- contradiction handling
- freshness
- domain validation success
- hallucination rate
- unanswered-question rate
- honest-abstention rate (correctly declining when knowledge is insufficient)
- cost per verified knowledge item and per answer

Example:

```text
Version A
Accuracy: 88%

Version B
Accuracy: 93%

Version C
Accuracy: 95%
```

Only promote a new configuration/model when evaluation shows meaningful improvement.

"Meaningful" should be defined per metric in advance (for example a minimum absolute gain with no regression above a tolerance on any other tracked metric) so that promotion decisions are not renegotiated after seeing the numbers.

---

## 36. Training dataset generation

The repository should eventually produce several datasets.

### Knowledge dataset

Facts and relationships.

### Instruction dataset

Question → answer.

### Reasoning/evidence dataset

Question → evidence → conclusion.

### Domain validation dataset

Input → expected technical behavior.

### Negative dataset

Incorrect claims and why they are incorrect.

These datasets can later support:

- fine-tuning
- evaluation
- model comparison
- domain-specific model development

Dataset generation must honour the per-source permissions recorded in the registry ([Section 25](#25-paywalls-and-login-protected-information)): content not permitted for training is excluded automatically, and every dataset ships with a manifest listing the sources, licenses, and knowledge item versions it was built from.

---

## 37. Domain skills

Each domain can expose capabilities in addition to knowledge.

Power BI:

```text
Explain DAX
Write DAX
Review DAX
Optimize DAX
Explain data models
Troubleshoot refresh
Explain Power Query
```

Robotics:

```text
Explain components
Calculate requirements
Diagnose faults
Create procedures
Check constraints
```

The core AI can discover which skills are available from the selected domain.

---

## 38. Security and governance

The platform should maintain:

- source permissions
- data classification
- access control
- audit logs
- ingestion history
- knowledge change history
- human-review history
- model/evaluation history

Sensitive or private data should be isolated from public knowledge.

### Threat model (summary)

| Threat | Mitigation |
|---|---|
| Prompt injection via collected content | Content-as-data handling, tool allow-lists, human approval for impactful side effects ([Section 29](#29-ai-orchestration)) |
| Malicious code in collected examples | Validator sandboxing ([Section 20](#20-domain-specific-validation)); never execute collected code outside a sandbox |
| Credential leakage | Secrets held in a secret manager or environment, never in the repository, logs, or prompts; least-privilege service accounts |
| Data poisoning (false claims planted in low-authority sources) | Source authority weighting, independence analysis, active falsification, human review for high-impact items |
| Unauthorized access to private/licensed knowledge | Per-item data classification and access control enforced at the retrieval API |
| Supply-chain risk in dependencies | Pinned, reviewed dependencies; automated vulnerability scanning |

### Data governance

- **Personal data.** Collection targets technical documentation, not people. Where personal data is unavoidably collected (for example author names in forum posts), it is minimised, classified, and subject to deletion requests.
- **Deletion and takedown.** Deletion is lineage-aware: removing a source or document cascades to raw evidence, evidence records, dependent knowledge items (which become `REJECTED` or lose support), embeddings, and generated datasets. Completed deletions are logged.
- **Access to the platform.** API consumers authenticate; roles distinguish readers, reviewers, operators, and administrators. All reviewer and administrator actions are audited.
- **Licensing.** Per-source read/store/process/train/redistribute permissions are enforced at ingestion, retrieval, and dataset generation, not merely recorded.

---

## 39. Observability

An unobservable pipeline cannot be trusted or tuned.

### Logs

Structured (JSON) logs with correlation fields: run ID, job ID, source ID, domain, stage. No raw secrets or full collected documents in logs.

### Metrics

At minimum, per domain and per stage:

- sources checked, changed, skipped, failed
- documents collected, deduplicated, rejected
- knowledge items created per status, and status-transition counts
- conflicts opened and resolved
- stale items outstanding
- review-queue depth and age
- validator pass/fail rates
- evaluation scores per dataset version
- model tokens, latency, and cost per stage
- queue depth and dead-letter count

### Tracing

Each pipeline run and each answer request is traceable end-to-end (source → evidence → knowledge item → answer) using the run and request IDs.

### Alerting

Alert on: dead-letter growth, budget thresholds, crawl error rates, evaluation regressions, review-queue age, and sources that have not been successfully checked within their scheduled interval.

---

## 40. Cost-control strategy

The expensive resource will generally be AI processing, not text storage.

Use a staged pipeline:

```text
Many URLs
   ↓
Cheap filtering
   ↓
Deduplication
   ↓
Change detection
   ↓
Relevance scoring
   ↓
Small set of valuable documents
   ↓
AI extraction
   ↓
Verification
```

Do not send every discovered page to an expensive model.

Use the AI only where reasoning is useful.

Additional controls:

- Use smaller/cheaper models for classification and relevance scoring; reserve the most capable model for extraction, contradiction analysis, and answer generation.
- Cache model responses keyed by (prompt version, input hash) so re-runs on unchanged content are free.
- Set per-stage, per-run, and per-day token budgets with alerts and automatic pause.
- Report cost per verified knowledge item and per answer as first-class metrics ([Section 35](#35-measuring-improvement)).

---

## 41. Deployment strategy

The first implementation should be designed so that components can move between providers.

Logical services:

```text
Frontend
API
Collector workers
Scheduler
PostgreSQL
Vector search
Object storage
AI provider
```

Avoid deep dependence on any single cloud provider.

The first version can use free/low-cost infrastructure where practical, while keeping interfaces generic enough to migrate later.

Practical expectations:

- Every service runs from a container image; local development uses the same images (for example with Docker Compose).
- Configuration comes from environment variables or a configuration service; no environment-specific values in code.
- Provider-specific integrations (object storage, secret manager, AI provider) live behind adapter interfaces with at least one local/open implementation for development and testing.
- Environments: at minimum `local`, `staging`, and `production`, with the golden evaluation suite run before promotion to production.

---

## 42. Recommended first technology stack

A practical first implementation can use:

```text
Python
    API (e.g. FastAPI, OpenAPI-documented)
    collectors
    orchestration
    processing

PostgreSQL
    metadata
    knowledge
    relationships
    evaluation
    job queue (v1)
    full-text search (v1 lexical retrieval)

pgvector
    embeddings / semantic retrieval

Object storage (S3-compatible)
    raw documents and large permitted files

Crawl4AI or equivalent
    web collection

STORM/Co-STORM concepts or equivalent
    research workflow

LLM (behind a provider-agnostic adapter)
    extraction
    classification
    reasoning
    verification assistance

Schema migrations (e.g. Alembic)
    versioned database changes

Containers (Docker)
    packaging and local development

HTML/JavaScript
    user interface
```

The exact services can be replaced later.

Selection criteria for any component: open standard or open source where possible, a clear migration path, and no lock-in of the knowledge repository itself.

---

## 43. Suggested project structure

Conceptually:

```text
platform/
│
├── core/
│   ├── discovery
│   ├── collection
│   ├── extraction
│   ├── quality
│   ├── verification
│   ├── versioning
│   ├── retrieval
│   ├── evaluation
│   └── orchestration
│
├── domains/
│   ├── powerbi
│   ├── tableau
│   ├── python
│   └── robotics
│
├── repository/
│   ├── database
│   ├── object-storage
│   └── knowledge-graph
│
├── agents/
│   ├── researcher
│   ├── collector
│   ├── verifier
│   └── answerer
│
├── datasets/
│   ├── evaluation
│   ├── training
│   └── feedback
│
├── adapters/
│   ├── llm
│   ├── storage
│   └── secrets
│
├── tests/
│   ├── unit
│   ├── contract
│   ├── integration
│   └── evaluation
│
├── docs/
│   └── adr/            # architecture decision records
│
└── frontend/
```

The exact implementation can change; the separation of responsibilities should remain.

Significant architectural decisions (choice of queue, graph storage, embedding model, and so on) should be recorded as short Architecture Decision Records under `docs/adr/` so the reasoning survives team changes.

---

## 44. Testing strategy

Quality of the platform itself is verified at several levels; the golden dataset alone is not sufficient.

| Level | Scope | Notes |
|---|---|---|
| Unit | Core modules, scoring rules, state transitions, hashing, chunking | Fast, run on every change |
| Contract | Every domain plugin against the plugin interface | Ensures core changes cannot silently break a domain; a reference "example" plugin is kept in the repository |
| Integration | Pipeline stages against a real PostgreSQL and object store | Uses recorded HTML/PDF fixtures, never the live Internet |
| Collector fixtures | Recorded responses including `robots.txt`, `429`, `ETag` scenarios | Verifies responsible-collection behaviour |
| Evaluation | Golden dataset, adversarial and abstention cases | Run before any promotion; results stored with dataset version |
| Migration | Schema migrations applied forward on a copy of production-shaped data | Prevents destructive migrations |
| Security | Prompt-injection suite, sandbox escape checks, dependency scanning | Part of CI |

Continuous integration runs unit, contract, and security tests on every change; integration and evaluation suites run before merging to the main branch and before every release.

---

## 45. Development phases

Each phase has an exit criterion so that progress is measurable and the next phase does not start on an unstable base.

### Phase 1 — Power BI knowledge collector

Goal:

Collect and structure high-quality Power BI knowledge.

Focus on:

- source registry
- web discovery
- crawling (with responsible-collection rules from day one)
- document extraction
- deduplication
- basic quality scoring
- knowledge items
- evidence
- PostgreSQL repository
- job queue, structured logging, and basic metrics

Do not build model training yet.

Exit criterion: a scheduled run collects the seed source catalog, produces knowledge items with evidence, and can be replayed idempotently.

---

### Phase 2 — Verification

Add:

- source ranking
- cross-source comparison
- contradiction detection
- version tracking
- freshness
- confidence
- human review

Exit criterion: conflicts are detected and routed to review; every `VERIFIED` item explains its verification level.

---

### Phase 3 — Knowledge graph + RAG

Add:

- entities
- relationships
- graph retrieval
- vector retrieval
- hybrid search
- grounded answers

Exit criterion: answers cite knowledge item IDs and evidence; the first golden dataset version produces a baseline score.

---

### Phase 4 — Power BI validators

Add specialized validation for:

- DAX
- Power Query/M
- examples
- version compatibility
- testable technical claims

Exit criterion: validators run in sandboxes and their results appear as evidence.

---

### Phase 5 — Continuous updating

Add:

- scheduled source checks
- change detection
- stale knowledge detection
- automatic revalidation
- source reliability history

Exit criterion: a changed source page leads to updated, versioned knowledge without manual intervention.

---

### Phase 6 — Evaluation and improvement

Add:

- golden dataset
- automated answer evaluation
- regression testing
- feedback
- training dataset generation

Exit criterion: promotion of any configuration change is gated on evaluation results.

---

### Phase 7 — Second domain

Add Tableau.

The purpose is not just to gain Tableau knowledge.

It is to prove that the architecture truly supports plugins.

The core should require minimal or no changes.

Exit criterion: the Tableau plugin passes contract tests and runs end to end with no core code changes (core changes, if any, are documented as ADRs).

---

### Phase 8 — Radically different domain

Add robotics or an industrial machine.

This tests whether:

- the knowledge model is sufficiently generic
- domain validators work as plugins
- source strategies are configurable
- skills can be domain-specific
- the AI can operate across fundamentally different knowledge structures

Exit criterion: same as Phase 7, plus at least one non-software validator (calculation or constraint check) operating as a plugin.

---

## 46. What should NOT be built initially

Avoid:

- training a custom LLM
- collecting the entire Internet
- downloading every video
- downloading every image
- building many databases
- Kubernetes
- complex multi-agent systems everywhere
- automatic model retraining
- unrestricted autonomous browsing
- bypassing access controls

First prove:

```text
Discover → Collect → Verify → Structure → Store → Retrieve
```

with Power BI.

---

## 47. Definition of success for the first version

A successful first version should be able to:

1. Discover relevant Power BI sources.
2. Rank sources by quality.
3. Collect permitted content.
4. Detect duplicate/unchanged sources.
5. Extract useful knowledge.
6. Preserve provenance.
7. Create structured knowledge items.
8. Detect conflicting claims.
9. Track knowledge versions.
10. Mark stale knowledge.
11. Retrieve knowledge using semantic and structured search.
12. Explain why a knowledge item is trusted.
13. Generate grounded answers.
14. Produce an evaluation dataset.
15. Run the same platform with another domain without redesigning the core.

### Measurable acceptance criteria (proposed)

| Capability | Acceptance check |
|---|---|
| Provenance | 100% of `VERIFIED` items link to at least one evidence record with a stable locator |
| Deduplication | Re-running collection on unchanged sources creates zero new knowledge items |
| Conflicts | Seeded contradictory fixtures produce conflict records, not silent overwrites |
| Freshness | A changed fixture page marks affected items `STALE` within one scheduled cycle |
| Retrieval | Golden dataset retrieval recall@10 and answer accuracy reported against a named dataset version, with a baseline recorded |
| Grounding | 100% of generated answers cite knowledge item IDs; unsupported answers are refused |
| Portability | Tableau plugin runs with zero core code changes |
| Compliance | Collector fixtures for `robots.txt`, `429`, and opt-out signals pass |

Numeric thresholds for accuracy and recall are to be set once the Phase 3 baseline exists (see [Appendix B](#appendix-b--open-questions)).

---

## 48. Risks and mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Legal or licensing exposure from collected content | High | Medium | Responsible collection ([Section 12](#12-responsible-collection)), per-source permissions enforced at every stage, takedown workflow |
| Prompt injection or data poisoning through collected content | High | Medium | Content-as-data handling, sandboxing, authority weighting, adversarial evaluation cases |
| AI cost overrun | Medium | High | Staged pipeline, cheaper models for triage, budgets with automatic pause, response caching |
| Low extraction quality producing noisy knowledge | High | Medium | Quality gates before `CANDIDATE`, human review, evaluation gating on promotion |
| Source drift breaking collectors | Medium | High | Change detection, collector fixtures, alerting on crawl failures |
| Core becomes Power BI-specific by accident | High | Medium | Plugin contract, contract tests, Phase 7/8 as explicit architecture tests |
| Vendor lock-in (AI provider, cloud) | Medium | Medium | Adapter interfaces, open formats, provider-agnostic model layer |
| Evaluation gaming (tuning to the golden set) | Medium | Medium | Held-out dataset partitions, periodic refresh of questions, adversarial cases |
| Embedding model change invalidates index | Medium | Low | Versioned embeddings and planned re-embedding ([Section 22](#22-vector-search)) |

---

## 49. Final conceptual architecture

```text
                         ┌─────────────────────┐
                         │       USER          │
                         └──────────┬──────────┘
                                    ↓
                         ┌─────────────────────┐
                         │    AI ORCHESTRATOR  │
                         └──────────┬──────────┘
                                    ↓
              ┌─────────────────────┼─────────────────────┐
              ↓                     ↓                     ↓
        KNOWLEDGE RETRIEVAL     RESEARCH AGENT       DOMAIN SKILLS
              ↓                     ↓                     ↓
       ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
       │ Vector       │      │ Discovery    │      │ Power BI     │
       │ Search       │      │ Crawling     │      │ Tableau      │
       │ Graph        │      │ Extraction   │      │ Robotics     │
       │ SQL          │      │ Verification │      │ etc.         │
       └──────┬───────┘      └──────┬───────┘      └──────────────┘
              │                     │
              └──────────┬──────────┘
                         ↓
                ┌───────────────────┐
                │ KNOWLEDGE ENGINE  │
                │                   │
                │ Facts             │
                │ Concepts          │
                │ Relationships     │
                │ Procedures        │
                │ Evidence          │
                │ Versions          │
                └─────────┬─────────┘
                          ↓
                ┌───────────────────┐
                │ KNOWLEDGE STORE   │
                │                   │
                │ PostgreSQL        │
                │ Vector index      │
                │ Knowledge graph   │
                │ Object storage    │
                └─────────┬─────────┘
                          ↓
                ┌───────────────────┐
                │ EVALUATION ENGINE │
                └─────────┬─────────┘
                          ↓
             ┌────────────┴────────────┐
             ↓                         ↓
       Better retrieval          Training datasets
       Better prompts            Fine-tuning
       Better validators         Future model training
```

---

## 50. Core philosophy

The most important design decisions are:

### Knowledge over model

The model can change. The knowledge repository remains.

### Evidence over confidence

A confidence score without evidence is not enough.

### Verification over assumption

When possible, test the knowledge rather than asking another model to judge it.

### Versioning over overwriting

Knowledge changes. Historical states should remain available.

### Domain plugins over domain-specific platforms

Power BI, Tableau, and robotics should use the same core infrastructure.

### Research over crawling

The goal is not to collect the most information. The goal is to collect the **most useful and trustworthy information**.

### Derived knowledge over unnecessary media

Extract useful knowledge from videos/images rather than automatically accumulating huge media archives.

### Controlled learning over automatic self-training

First build a reliable knowledge and evaluation pipeline. Only then consider fine-tuning or model training.

---

## 51. End goal

The final system should behave less like a scraper and more like a continuously maintained digital research organization:

```text
Discover
   ↓
Research
   ↓
Collect
   ↓
Understand
   ↓
Cross-check
   ↓
Validate
   ↓
Structure
   ↓
Remember
   ↓
Monitor changes
   ↓
Correct outdated knowledge
   ↓
Evaluate
   ↓
Improve
```

Power BI is simply the first domain used to prove the platform.

Once the core architecture is stable, new technologies should be added primarily by creating a new **domain plugin**, not by rebuilding the platform.

---

## Appendix A — Glossary

| Term | Definition |
|---|---|
| **Source** | A registered origin of information (website, API, repository, document feed) with recorded permissions and authority. |
| **Document** | A single collected artefact from a source (page, PDF, transcript, file) at a specific point in time, identified by content hash. |
| **Raw evidence store** | Storage for permitted original or minimally processed documents, kept for audit and reprocessing. |
| **Claim** | A single statement asserted by a document, before it is accepted as knowledge. |
| **Evidence** | A verifiable pointer from a claim or knowledge item to a specific location in a document (excerpt, offsets, timestamps) plus the document's hash. |
| **Knowledge item** | A versioned, structured unit of curated knowledge (subject–predicate–object plus explanation, provenance, confidence, status). |
| **Conflict record** | A record linking two or more knowledge items or claims that disagree, with investigation notes and outcome. |
| **Confidence** | A 0.0–1.0 score derived from explainable quality factors. |
| **Verification level** | A discrete 0–5 level describing how a knowledge item was verified (see Section 31). |
| **Status** | The lifecycle state of a knowledge item version (see Section 14). |
| **Freshness** | Whether a knowledge item has been verified against its sources within its validity period. |
| **Provenance** | The full chain of sources, documents, activities, agents, and decisions that produced a knowledge item. |
| **Domain plugin** | A module implementing the plugin contract that supplies taxonomy, sources, validators, skills, and evaluation data for one technology or subject area. |
| **Domain validator** | A domain-supplied check (syntax, execution, calculation, constraint) that tests a knowledge item rather than judging it. |
| **Domain skill** | A domain-supplied capability (for example "Write DAX") exposed to the orchestrator. |
| **Golden evaluation dataset** | A versioned set of questions with expected answers, sources, and risk levels used to measure the platform. |
| **Hybrid retrieval** | Retrieval combining lexical search, vector similarity, metadata filters, and graph traversal. |
| **Run** | One execution of a pipeline (discovery, collection, verification, evaluation) with a unique ID stamped on everything it produces. |
| **ADR** | Architecture Decision Record: a short document capturing a significant decision, its context, and consequences. |

---

## Appendix B — Open questions

Decisions that this document deliberately leaves open, to be resolved and recorded as ADRs:

1. **Numeric evaluation thresholds.** Accuracy, recall, and hallucination-rate targets for promotion, to be set after the Phase 3 baseline.
2. **Risk classes per domain.** Concrete definition of the Power BI risk classes and their minimum verification levels.
3. **Graph storage.** Whether PostgreSQL remains sufficient for graph traversal at target scale or a dedicated graph store is warranted.
4. **Queue technology.** When (if ever) the PostgreSQL-backed queue should be replaced by a dedicated broker.
5. **Embedding model and chunking defaults.** Initial choice and the re-embedding cadence.
6. **Human review tooling.** Whether the review queue is part of the platform frontend or an external tool.
7. **Hosting for validator sandboxes.** Container-based local execution versus a managed sandbox service.
8. **Document owner.** Who owns this specification and approves changes to it.

---

## Appendix C — Document history

| Version | Date | Author | Summary |
|---|---|---|---|
| 0.1.0 | 2026-09-13 | — | Initial draft. |
| 0.2.0 | 2026-09-13 | — | Normalised heading hierarchy; added metadata, scope, table of contents, non-functional requirements, plugin contract, responsible collection, knowledge item schema and status transitions, validator sandboxing, embedding management, pipeline reliability, prompt-injection defence, threat model and data governance, observability, testing strategy, phase exit criteria, measurable acceptance criteria, risks, glossary, open questions. |
