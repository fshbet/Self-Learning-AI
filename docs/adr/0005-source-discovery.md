# ADR 0005 — Source monitoring vs new-source discovery

Status: accepted for the controlled part (implemented); auto-approval **proposed, not implemented** · Date: 2026-09-14 · P2.8

## Context

The scheduler re-checks *known* sources when their `crawl_frequency_hours` elapse (**source monitoring**). It never
looks for sources it does not know, so the knowledge base grows only when a person adds a source or runs
`kp run discover`. Discovery existed as a one-off job: run the plugin's `discovery_queries` (plus user keywords)
through the search provider and register every unknown host as a `CANDIDATE` source with `authority = 30`,
`enabled = false`. Nothing scored the candidates, nothing filtered them, and a person had to approve each one.

The two activities have different risk profiles and must stay distinct:

| | Source monitoring | New-source discovery |
|---|---|---|
| Input | the approved source catalogue | search results for domain queries |
| Trust | the source's declared class / authority | **none** — a hit is a URL someone put on the web |
| Failure mode | stale knowledge | corpus poisoning, spam, duplicates, irrelevant sources |
| Automation | fully automatic (scheduler) | automatic *candidate* collection; **approval is a decision** |

## Flow

```
Discovery (scheduled or manual)      search provider hits for plugin queries + user keywords
        ↓
Candidate source                     Source(status=CANDIDATE, enabled=false, origin=discovered,
                                     source_class=community, authority=30, relevance=score)
        ↓
Source scoring                       relevance 0–100 from cheap, explainable signals (below); reasons stored in notes
        ↓
Approval / policy                    a person approves (PATCH status=ACTIVE, enabled=true, sets class/authority);
                                     a policy may only *reject or park* automatically, never approve
        ↓
Collection                           the approved source joins monitoring; first crawl is explicit (Run pipeline)
```

A discovered source **never becomes trusted automatically**: it enters with `source_class = community` and
`authority = 30`, its knowledge therefore scores as community knowledge and can never reach `OFFICIAL`; only a
person raises authority or class, and only a person makes it `ACTIVE`.

## Candidate scoring (implemented)

`relevance` (0–100) is the sum of explainable signals, each recorded in `notes` so the reviewer sees *why*:

| Signal | Points | Rationale |
|---|---|---|
| hit by several distinct discovery queries | +15 per extra query, max +45 | a site that answers many domain queries is about the domain |
| taxonomy / terminology terms in title + snippet | +5 per distinct term, max +30 | relevance to the plugin's vocabulary, not to the search engine's ranking |
| rank position in its best query | +10 (top 3) / +5 (top 10) | weak prior from the engine |
| host declared in the plugin's `discovery.prefer_hosts` | +25 | the plugin author expects this publisher |
| host under a `discovery.deny_hosts` entry or the global deny list | **not registered** | spam / content farms / known mirrors |
| host already known (any status) or url canonical to a known document | **not registered** (counted as duplicate) | duplication |
| relevance below `discovery.min_relevance` (default 20) | **not registered**, logged | irrelevance |

No model call is involved in scoring; discovery must stay cheap and deterministic. A judged relevance (model reads
the landing page) is a possible later refinement, run only on candidates a person opened.

## Protections

* **Spam / low quality**: deny lists (plugin + global `KP_DISCOVERY_DENY_HOSTS`), minimum relevance, `authority 30`.
* **Poisoned content**: candidates are community-class; the extraction path treats their claims as community
  evidence; contradictions against official sources open conflicts rather than overwriting; falsification and review
  flags apply unchanged. Nothing discovered is exported as anything but `COMMUNITY` provenance.
* **Duplication**: host-level dedup against every known source (any status) and canonical-document matching against
  already-fetched pages (`documents.canonical_url`), plus the existing mirror detection at crawl time.
* **Irrelevance**: vocabulary-based relevance with a floor; a reviewer sees the reasons before approving.
* **Volume**: per run at most `discovery.max_candidates` (default 25) new candidates per domain; recurring discovery
  is **off by default** (`KP_DISCOVERY_INTERVAL_HOURS=0`) and, when on, runs at most once per interval per domain.

## Recurring discovery (implemented, opt-in)

`schedule_due_discovery` enqueues one `discover` job per domain whose last discovery run is older than
`discovery.interval_hours` (settings table override or `KP_DISCOVERY_INTERVAL_HOURS`). The job only adds
candidates; the Sources page lists them under "Candidates" with their score and reasons. Monitoring of approved
sources is untouched.

## Auto-approval policy (proposed, not implemented)

A policy could approve candidates automatically when `relevance ≥ N` **and** the host is in `prefer_hosts`, with
`authority ≤ 50` and `source_class = community` fixed. The cost is that a poisoned page on a preferred host would be
crawled without a person looking at it; the benefit is unattended growth. This is a product decision, not an
engineering one, so it is left for approval. Everything above works without it.

## Consequences

* Discovery can run unattended and the candidate list grows with reasons attached; the corpus grows only through
  approvals.
* The plugin contract gains `discovery: {queries, prefer_hosts, deny_hosts, min_relevance, max_candidates}`;
  `discovery_queries` keeps working as an alias.
