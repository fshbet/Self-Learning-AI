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
