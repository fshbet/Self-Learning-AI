# Domains

Every sub-directory with a `plugin.yaml` is a domain plugin. The core discovers them at
start-up (and via `POST /api/domains/reload`). A plugin never touches the database; it only
describes *what* to collect and *how* to validate it.

```
domains/<slug>/
├── plugin.yaml      # manifest: taxonomy, terminology, knowledge types, risk classes, hints
├── sources.yaml     # seed source catalog: URL, authority, permissions, crawl rules
├── evaluation.yaml  # golden questions (optional)
└── plugin.py        # optional: `class Plugin(DomainPlugin)` with validators() / skills()
```

Add a topic:

```bash
kp domains new my-topic --name "My Topic"
# edit domains/my-topic/plugin.yaml and sources.yaml
kp domains sync my-topic
kp run pipeline my-topic
```

Validate a plugin directory against the contract: `kp domains check domains/my-topic`.
Extra plugin directories can be added with `KP_DOMAINS_PATH=./domains,/path/to/more`.

## Knowledge types carry their semantics

The core never assumes what a type *means*; `plugin.yaml` says so. A plain name gets the conventional defaults
(`fact`/`definition` are foundations, `example` is an example, `limitation`/`warning`/`anti_pattern` are negative
dependents); an object form declares everything explicitly, which is how a domain with its own vocabulary — a
robotics `hazard`, a `safety_rule` — gets polarity, dependency propagation, export headings and AI-text prefixes
without touching core code:

```yaml
knowledge_types:
  - {name: spec, role: foundation, label: Specifications}          # others depend on it
  - {name: safety_rule, role: dependent, label: Safety rules}       # flagged when its foundation changes
  - {name: hazard, role: dependent, polarity: negative, label: Hazards, prefix: Hazard}
  - {name: demo, role: example, label: Demonstrations}              # structured example (code, expected result)
  - note                                                            # neutral: no dependency rules
sample_questions:                                                   # shown on the Search & Ask page
  - "What is the payload limit of the arm?"
language: de                                                        # drives lexical search: de -> 'german'
retrieval:
  text_search_config: german                                        # optional explicit PostgreSQL config
  synonyms:                                                         # lexical variants the corpus uses (ADR 0006)
    payload: [lifting capacity, load rating]                        #   query "lifting capacity" also searches "payload"
  intent_cues:                                                      # extra regex cues per core intent, tried first
    limitation: ['\bhazards?\b']                                  #   "which hazard ..." is a limitation question here
  intent_types:                                                     # optional: which types an intent prefers
    limitation: [hazard, safety_rule]                               #   (default: derived from roles / polarity)
discovery:                                                          # new-source discovery (ADR 0005)
  queries: ["robot arm payload specification"]                      # `discovery_queries` still works as an alias
  prefer_hosts: ["docs.vendor.example"]                             # +25 relevance, still needs approval
  deny_hosts: ["content-farm.example"]                              # never registered
  min_relevance: 20                                                 # floor for registering a candidate
  max_candidates: 25                                                # per discovery run
```

`role`: `foundation` | `dependent` | `example` | `neutral`; `polarity`: `positive` | `negative`. The declaration is
exported in every snapshot's `glossary.json` (`knowledge_type_specs`), so consumers read the same semantics.

**Retrieval behaviour (ADR 0006).** Query analysis, ranking, expansion and the answer plan are domain-independent;
everything a domain knows about its own vocabulary comes from `retrieval.*`: `synonyms` (declared lexical
variants — the core never guesses them), `intent_cues` (regexes added to the core's intent classifier, tried
before the core defaults so a domain can say "which hazard" is a limitation question), `intent_types` (override of
the knowledge types an intent prefers; the default is derived from the declared roles and polarity). Entities are
the query n-grams filed as subjects in the domain's live knowledge plus identifier-looking tokens; a question that
names a declared knowledge type ("which hazard", "limitations of") prefers items of that type. Every ranking
contribution is recorded per item and shown on the Search & Ask page ("why did this rank here?").

**Language / search behaviour.** The core does not assume English. Lexical retrieval uses the PostgreSQL
text-search configuration the plugin declares (`retrieval.text_search_config`) or, when absent, the one derived
from `language` (`en` → `english`, `de` → `german`, `fr` → `french`, … see `LANGUAGE_TEXT_SEARCH_CONFIGS`); any
other language, a multilingual corpus (`language: mul`) or a script PostgreSQL has no stemmer for uses `simple`
(plain tokenisation, no stemming — always correct, less forgiving). Vector retrieval is language-neutral. A
configuration PostgreSQL does not have falls back to `simple` with a warning; the English expression index created
by the initial migration only serves English domains — add a per-domain expression index if a large non-English
domain needs one.

## Sources that republish another source

If a catalog source syndicates or mirrors another one, say so — its pages then count as the primary's for source
independence instead of looking like a second confirmation:

```yaml
sources:
  - key: vendor-docs
    url: https://docs.vendor.example/
    source_class: official
  - key: vendor-docs-mirror
    url: https://mirror.example/vendor-docs/
    source_class: external
    mirror_of: vendor-docs
```

## Validators and the isolation boundary

A validator declares `kind = "static"` (default) or `kind = "executing"`. Static validators are pure functions of
the item payload (no I/O, no subprocesses, nothing executed) and run inside the worker; executing validators —
anything that evaluates DAX, Python, SQL, simulations or calculations — are refused in-process and only ever run
through the isolated validator runner described in `docs/adr/0003-validator-isolation.md`. Until that runner
exists they are skipped and the skip is recorded on the item (`validator_versions`), so an item is never scored by
a validator that did not run.
