# ADR 0003 — Validator isolation boundary

Status: **proposed (design only — awaiting approval before implementation)** · Date: 2026-09-14 · P2.6

## Context

Domain validators (`DomainPlugin.validators()`) test a knowledge item rather than judge it: they run
`applies_to(item)` and `validate(item)` inside `core/pipeline.run_validators`, i.e. **in the API/worker process**,
with the process's identity, filesystem, network and memory. Today every shipped validator is *static* (regex and
bracket balancing over the item's `code` — `dax-syntax`, `m-syntax`), so the exposure is limited to a buggy
validator crashing or hanging the worker. The design allows for validators that **execute** extracted content:
DAX against a semantic model, Python snippets, SQL, simulations, numeric calculations. Extracted content is
untrusted by construction — it was crawled from the web and shaped by a model — and must never execute inside the
main process.

This ADR fixes the trust boundary and the runtime contract before any executing validator exists, so that the
sandbox is a property of the platform and not of each plugin author's diligence.

## Trust boundary

| Zone | Trust | Contents |
|---|---|---|
| **Core process** (API + embedded worker) | trusted | database session, object store, model credentials, settings, plugin code |
| **Validator runner** (separate process, one per validation) | *untrusted by default* | the validator's code **and** the item payload it receives |
| **Item payload** | untrusted input | statement, code, details — crawled + model-generated text |

Rules that follow from the table:

1. A validator never receives a database session, the object store, settings or credentials. It receives a
   JSON document and returns a JSON document — nothing else crosses the boundary.
2. The runner's verdict is *evidence* (`evidence_type=validator`), never an action: it cannot change an item's status,
   flags or relations directly; the core scores the result exactly as today (`run_validators` → `validator_versions`
   → scoring factor).
3. A validator that cannot be run under the boundary (no runner configured, resource limits unavailable on the host)
   is **skipped and recorded as skipped** — an item is never verified by a validator that did not run.

## Validator classes

| `Validator.kind` | Runs | Allowed today |
|---|---|---|
| `static` | in-process; pure function of the payload; no I/O, no imports at call time, no subprocesses | yes (current behaviour) |
| `executing` | only through the isolated runner described below | **no** — skipped with a warning until the runner exists |

`kind` is declared by the validator class (default `static`). The core refuses to run an `executing` validator
in-process even if the plugin asks for it. This declaration is the one piece implemented now (see "Status").

## Allowed inputs

* The item payload as produced by `pipeline.item_as_dict` (statement, subject/predicate/object, knowledge type, code,
  details, product version, topic, tags) — nothing about the database, sources or other items.
* An optional, plugin-declared **fixture set** (files under the plugin directory, e.g. a small semantic model or test
  data) mounted read-only.
* The validator's own version string, for reproducibility (`validator@version` is stored on every result).

Not allowed: environment variables of the host, credentials, other items, the raw crawled documents, the network.

## Allowed outputs

* Exactly one JSON `ValidationResult` (`validator`, `version`, `passed`, `details`, `message`) ≤ 64 KiB.
* `stdout`/`stderr` captured and truncated (≤ 64 KiB each) into `details.stdout` / `details.stderr` for diagnosis.
* An exit code; anything but a clean exit with a parseable result is recorded as `passed=false,
  details.error="runner: …"` — a broken validator is a failed validation, never a crashed worker.

Outputs are never executed, rendered as HTML, or interpreted as instructions by the core.

## Resource limits (per validation)

| Limit | Default | Enforced by |
|---|---|---|
| Wall-clock timeout | 30 s (`KP_VALIDATOR_TIMEOUT_SECONDS`) | runner kills the process group |
| CPU time | 20 s (`RLIMIT_CPU` on POSIX; Job Object `PerProcessUserTimeLimit` on Windows) | OS |
| Memory | 512 MiB (`RLIMIT_AS` / Job Object `ProcessMemoryLimit`) | OS |
| Processes / threads | 1 process, no forking (`RLIMIT_NPROC=1` where available; Job Object active-process limit 1) | OS |
| Output size | 64 KiB per stream | runner |
| Filesystem | fresh temporary working directory (deleted afterwards); plugin fixtures mounted read-only; no other paths | runner (cwd + env) → container/namespace in tier 2 |
| Network | none: no outbound sockets (tier 1: `KP_FETCH_ALLOW_PRIVATE`-style netguard cannot help here — the runner strips proxy variables and, in tier 2, runs with networking disabled) | tier 1 best effort, tier 2 enforced |
| Environment | minimal (`PATH`, `LANG`, `TMPDIR`); everything else stripped | runner |
| Concurrency | one validation at a time per worker; a queue of its own is not needed while extraction is the bottleneck | worker |

## Process isolation tiers

* **Tier 0 (now)** — static validators in-process, executing validators refused. Implemented.
* **Tier 1 — subprocess runner.** `python -I -S -m knowledge_platform.validators.runner` started by the core with the
  limits above (`resource` on POSIX; Windows Job Objects via `pywin32`/ctypes), the payload on stdin, the result on
  stdout, a scratch cwd, stripped environment. Good enough for validators that are *code we wrote* operating on
  untrusted *data* (a DAX parser, a numeric check, an M formatter). **Not** good enough for validators that run the
  untrusted code itself with a general-purpose interpreter, because a subprocess still shares the host's kernel,
  user account and filesystem visibility.
* **Tier 2 — container / micro-VM runner** for validators that execute untrusted code: the same stdin/stdout protocol
  inside `docker run --rm --network none --read-only --memory 512m --cpus 1 --pids-limit 32 --security-opt
  no-new-privileges --cap-drop ALL --tmpfs /tmp` (or a WASI runtime for languages that have one). The image is
  plugin-declared (`validator.runtime_image`) and pinned by digest. Docker is already a dependency of the local
  setup (PostgreSQL), so this adds no new infrastructure category.

The protocol is identical for tiers 1 and 2 (JSON in, JSON out, exit code), so a validator moves between tiers by
declaration, not by rewrite.

## Plugin contract changes (proposed)

```python
class Validator(ABC):
    name: str; version: str
    kind: Literal["static", "executing"] = "static"      # implemented now
    runtime: Literal["subprocess", "container"] = "subprocess"   # tier for executing validators (proposed)
    runtime_image: str | None = None                     # pinned image for tier 2 (proposed)
    fixtures: list[str] = []                             # read-only plugin files the runner mounts (proposed)
    timeout_seconds: int | None = None                   # per-validator override, capped by the platform maximum
```

The manifest summary and the snapshot's `generation.validators` list `name@version(kind/runtime)` so a consumer can
see under what isolation a validation ran.

## Implementation approach (proposed, not started)

1. `core/validators/runner.py`: the protocol (payload → result), the resource limits (POSIX `resource`, Windows
   Job Objects), timeout with process-group kill, output truncation, scratch directory lifecycle. ~200 lines + tests
   that prove: a validator that sleeps is killed and recorded as failed; one that allocates 1 GiB is refused; one that
   writes outside its cwd cannot; one that opens a socket fails; stdout beyond the cap is truncated; the worker
   survives all of it.
2. `run_validators` dispatches by `kind`: static in-process (unchanged), executing → runner; skipped validators are
   recorded in `validator_versions` as `{"skipped": reason}` so scoring cannot mistake "not run" for "passed".
3. Tier 2 runner behind the same interface, enabled by `KP_VALIDATOR_RUNTIME=container`; refused when Docker is not
   reachable (skip + warning), never silently downgraded to tier 1.
4. A synthetic test plugin (P2.10's domain) with one executing validator to prove the dispatch end to end.

**Significant architecture: yes** — a new process boundary, OS-specific limit enforcement on Windows, and a second
runtime (container). This ADR therefore stops at the design; steps 1–4 wait for approval.

## Status of what is already in place

* `Validator.kind` with default `static`; `run_validators` skips `executing` validators with a warning and records
  the skip in `validator_versions` (no item is scored by a validator that did not run). Tests cover the refusal.
* The `Validator` docstring and `domains/README.md` state the boundary.

## Consequences

* Plugin authors get a single rule: static checks stay static; anything that evaluates content declares itself
  `executing` and will only ever run inside the runner.
* Until tier 1/2 exist, executing validators are inert — knowledge that would need them stays at the verification
  level its evidence supports (no `validator_passed` factor), which is the honest outcome.
* Windows enforcement of CPU/memory limits requires Job Objects; if that proves brittle, tier 2 (Docker) is the
  enforced path on Windows and tier 1 stays a best-effort convenience.
