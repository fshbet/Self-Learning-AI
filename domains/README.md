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
```

`role`: `foundation` | `dependent` | `example` | `neutral`; `polarity`: `positive` | `negative`. The declaration is
exported in every snapshot's `glossary.json` (`knowledge_type_specs`), so consumers read the same semantics.

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
