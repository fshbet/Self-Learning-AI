"""Human-readable (README.md, knowledge.md, knowledge.html) and AI-oriented (ai/knowledge.jsonl, ai/knowledge.md)
representations of a snapshot (req. 27, 28)."""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

from .schema import ConflictRecord, EvidenceRecord, KnowledgeRecord, Manifest

RENDER_VERSION = "render@1.3"  # bump when output changes (alters file hashes); 1.3: historical text names successor


def type_specs(glossary: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """name -> spec from the snapshot glossary (the plugin's declaration, audit P1.5); empty when absent."""
    return {t["name"]: t for t in (glossary or {}).get("knowledge_type_specs", [])}


def type_heading(name: str, specs: dict[str, dict[str, Any]]) -> str:
    spec = specs.get(name, {})
    return spec.get("label") or name.replace("_", " ").capitalize() + "s"


def type_prefix(name: str, specs: dict[str, dict[str, Any]]) -> str:
    spec = specs.get(name, {})
    return spec.get("prefix") or (spec.get("label") or name.replace("_", " ").capitalize()).rstrip("s")


def _cite_lines(ev: list[EvidenceRecord]) -> list[str]:
    seen: set[str] = set()
    out = []
    for e in ev:
        if e.evidence_type == "human" and e.verified:
            d = e.details or {}
            out.append(f"provided by {d.get('provided_by') or 'unknown'} ({(d.get('provenance') or 'USER').lower()})")
            continue
        if e.evidence_type != "extraction" or not e.document_url or e.document_url in seen:
            continue
        seen.add(e.document_url)
        title = e.document_title or e.source_name or e.document_url
        out.append(f"{title} — {e.document_url}" + (f" (v{e.document_version})" if e.document_version else ""))
    return out


# ----------------------------------------------------------------------------- AI knowledge source


DETAIL_LABEL = {
    "expected_behavior": "Expected behaviour",
    "expected_result": "Expected result",
    "common_mistake": "Common mistake",
    "validation_method": "How to validate",
    "conditions": "Conditions",
}


def caution_reasons(k: KnowledgeRecord) -> list[str]:
    """Why a consumer must not treat this record as an ordinary trusted citation (audit P0.3). Empty = none."""
    reasons: list[str] = []
    if k.needs_review:
        reasons.append(f"flagged for review ({k.review_kind}): {k.review_reason or 'no reason given'}")
    if k.needs_revalidation:
        reasons.append(f"awaiting revalidation: {k.revalidation_reason or 'a dependency changed'}")
    if k.status == "CONFLICTED":
        reasons.append("status CONFLICTED: another item contradicts it and the conflict is unresolved")
    if k.status == "STALE":
        reasons.append("status STALE: its evidence no longer appears in the current source")
    if (k.evidence_status or {}).get("contradicting"):
        reasons.append(f"{k.evidence_status['contradicting']} contradicting evidence record(s) attached")
    return reasons


def usage_hint(k: KnowledgeRecord) -> str:
    """How an AI consumer should treat the record: cite | caution | historical.

    historical  superseded item kept for provenance — do not answer with it
    caution     flagged for review, awaiting revalidation, CONFLICTED/STALE or carrying contradicting evidence
    cite        an ordinary current item
    """
    if k.historical:
        return "historical"
    if caution_reasons(k):
        return "caution"
    return "cite"


def ai_citations(ev: list[EvidenceRecord]) -> list[dict[str, Any]]:
    """Citations for the AI Knowledge Source: extracted evidence (documents) and human-provided evidence."""
    out: list[dict[str, Any]] = []
    for e in ev:
        if not e.verified or e.relation == "contradicts":
            continue
        if e.evidence_type == "extraction":
            out.append(
                {
                    "kind": "document",
                    "url": e.document_url,
                    "title": e.document_title,
                    "publisher": e.publisher,
                    "excerpt": e.excerpt,
                    "section": " > ".join(e.section) if e.section else None,
                    "document_version": e.document_version,
                    "publication_date": e.publication_date,
                    "retrieved_at": e.retrieved_at,
                }
            )
        elif e.evidence_type == "human":
            d = e.details or {}
            out.append(
                {
                    "kind": "human",
                    "provided_by": d.get("provided_by"),
                    "provenance": d.get("provenance"),
                    "authority": d.get("authority"),
                    "excerpt": e.excerpt,
                    "retrieved_at": e.retrieved_at,
                }
            )
    return out


def _cite_label(c: dict[str, Any]) -> str:
    if c["kind"] == "human":
        who = c.get("provided_by") or "unknown"
        return f"{who} ({(c.get('provenance') or 'USER').lower()}-provided)"
    title = c.get("title") or c.get("url") or "source"
    label = f"{title} — {c.get('url')}"
    if c.get("section"):
        label += f", section: {c['section']}"
    return label


def ai_text(
    k: KnowledgeRecord,
    citations: list[dict[str, Any]],
    premises: list[str] | None = None,
    specs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Self-contained text block: statement, explanation, code, structured details, scope, numbered sources."""
    head = k.statement
    if k.polarity == "negative":
        head = f"{type_prefix(k.knowledge_type, specs or {})}: {k.statement}"
    parts = [head]
    # trust state first, in the text itself: a consumer that only reads `text` must still see it (audit P0.3)
    if k.historical:
        by = f" by {k.superseded_by_id}" if k.superseded_by_id else ""
        parts.insert(0, f"Historical: this item was superseded{by} and is kept for provenance only.")
    else:
        reasons = caution_reasons(k)
        if reasons:
            parts.insert(0, "Caution: " + "; ".join(reasons) + ".")
    if k.explanation and k.explanation.strip().lower() != k.statement.strip().lower():
        parts.append(k.explanation)
    if k.code:
        parts.append("```\n" + k.code + "\n```")
    for key, label in DETAIL_LABEL.items():
        v = k.details.get(key)
        if v:
            parts.append(f"{label}: {v}")
    scope = []
    if k.product_version:
        scope.append(f"Applies to: {k.product_version}")
    if k.effective_date:
        scope.append(f"Effective from: {k.effective_date}")
    if k.valid_until:
        scope.append(f"Valid until: {k.valid_until}")
    if scope:
        parts.append(" · ".join(scope))
    if citations:
        parts.append("Sources:\n" + "\n".join(f"[{i}] {_cite_label(c)}" for i, c in enumerate(citations, 1)))
    if k.origin in ("DERIVED", "SYNTHESIZED"):
        label = "Derived from" if k.origin == "DERIVED" else "Synthesized from"
        lines = [f"- {p}" for p in (premises or [])] or [
            f"- {d['item_id']}" for d in k.dependencies if d.get("relation") == "derived_from"
        ]
        parts.append(f"{label} ({k.origin.lower()} knowledge, no verbatim source of its own):\n" + "\n".join(lines))
    return "\n\n".join(parts)


def ai_record(
    k: KnowledgeRecord,
    ev: list[EvidenceRecord],
    related: list[str],
    premises: list[str] | None = None,
    specs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One self-contained record an AI/RAG system can index: text block + provenance + citations."""
    citations = ai_citations(ev)
    validators = sorted(
        {
            str((e.details or {}).get("validator") or e.source_name or "validator")
            for e in ev
            if e.evidence_type == "validator" and (e.details or {}).get("passed")
        }
    )
    return {
        "id": k.id,
        "domain": k.domain,
        "type": k.knowledge_type,
        "polarity": k.polarity,
        "origin": k.origin,
        "provenance": k.provenance,
        "topic": k.topic,
        "subject": k.subject,
        "text": ai_text(k, citations, premises, specs),
        "statement": k.statement,
        "code": k.code,
        "details": k.details,
        "status": k.status,
        "historical": k.historical,
        "superseded_by_id": k.superseded_by_id,
        "usage": usage_hint(k),
        "caution_reasons": caution_reasons(k),
        "needs_review": k.needs_review,
        "review_kind": k.review_kind,
        "review_reason": k.review_reason,
        "needs_revalidation": k.needs_revalidation,
        "revalidation_reason": k.revalidation_reason,
        "evidence_status": k.evidence_status,
        "confidence": k.confidence,
        "verification_level": k.verification_level,
        "validated_by": validators,
        "product_version": k.product_version,
        "publication_date": k.publication_date,
        "effective_date": k.effective_date,
        "last_verified_at": k.last_verified_at,
        "last_source_checked_at": k.last_source_checked_at,
        "citations": citations,
        "dependencies": k.dependencies,
        "related_ids": related,
        "tags": k.tags,
    }


def ai_index(items: list[KnowledgeRecord], glossary: dict[str, Any]) -> dict[str, Any]:
    """Navigation index for agents: taxonomy → item ids, counts per type/status/polarity, filter guidance."""
    topics: dict[str, dict[str, Any]] = {}
    by_type: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_polarity: dict[str, int] = defaultdict(int)
    by_usage: dict[str, int] = defaultdict(int)
    flagged: dict[str, int] = defaultdict(int)
    subjects: dict[str, list[str]] = defaultdict(list)
    for k in items:
        t = topics.setdefault(k.topic or "(unclassified)", {"count": 0, "types": defaultdict(int), "ids": []})
        t["count"] += 1
        t["types"][k.knowledge_type] += 1
        t["ids"].append(k.id)
        by_type[k.knowledge_type] += 1
        by_status["SUPERSEDED" if k.historical else k.status] += 1
        by_usage[usage_hint(k)] += 1
        if k.needs_review:
            flagged["needs_review"] += 1
        if k.needs_revalidation:
            flagged["needs_revalidation"] += 1
        by_polarity[k.polarity] += 1
        subjects[k.subject.strip().lower()].append(k.id)
    for t in topics.values():
        t["types"] = dict(sorted(t["types"].items()))
        t["ids"].sort()
    return {
        "record_file": "ai/knowledge.jsonl",
        "record_fields": {
            "text": "self-contained text to embed/index "
            "(statement, explanation, code, details, scope, numbered sources)",
            "usage": "cite = an ordinary current item; caution = flagged for review, awaiting revalidation, "
            "CONFLICTED/STALE or carrying contradicting evidence (see caution_reasons; the text starts with "
            "'Caution:'); historical = superseded, do not answer with",
            "caution_reasons": "why usage is caution (empty when cite); needs_review/review_kind/review_reason, "
            "needs_revalidation/revalidation_reason and evidence_status carry the underlying state",
            "citations": "documents (url, title, section, excerpt) or human-provided evidence; cite them in answers",
            "dependencies": "items this record relies on (relation + item_id); a change there may invalidate it",
        },
        "recommended_filters": {
            "answering": {"usage": ["cite"], "verification_level_min": 2},
            "with_warning": {"usage": ["caution"]},
            "exclude": {"usage": ["historical"]},
        },
        "topics": dict(sorted(topics.items())),
        "types": dict(sorted(by_type.items())),
        "statuses": dict(sorted(by_status.items())),
        "usage": dict(sorted(by_usage.items())),
        "flagged": dict(sorted(flagged.items())),
        "polarity": dict(sorted(by_polarity.items())),
        "subjects": {s: sorted(ids) for s, ids in sorted(subjects.items())},
        "taxonomy": glossary.get("taxonomy", []),
        "terminology": glossary.get("terminology", {}),
    }


def ai_markdown(
    manifest: Manifest,
    items: list[KnowledgeRecord],
    evidence_by_item: dict[str, list[EvidenceRecord]],
    conflicts: list[ConflictRecord],
    glossary: dict[str, Any],
) -> str:
    by_topic: dict[str, list[KnowledgeRecord]] = defaultdict(list)
    specs = type_specs(glossary)
    for k in items:
        if not k.historical:
            by_topic[k.topic or "(unclassified)"].append(k)
    # No snapshot id / version / timestamp here: identical knowledge must render to identical bytes so the
    # integrity hash is reproducible. Identity lives in manifest.json.
    lines = [
        f"# {manifest.plugin_name} — Canonical Knowledge Snapshot",
        "",
        f"Domain `{manifest.domain}` · plugin {manifest.plugin_version} · schema {manifest.schema_version} · "
        "identity and integrity hash: see manifest.json",
        "",
        "This document is generated from evidence-backed knowledge items. Each item carries a status "
        "(VERIFIED / SUPPORTED / CONFLICTED / STALE), a verification level (L0–L5) and citations to the original "
        "sources, which remain the source of truth. Treat CONFLICTED and STALE items with caution.",
        "",
    ]
    for topic in sorted(by_topic):
        lines += [f"## {topic}", ""]
        group = by_topic[topic]
        declared = list(specs)  # the plugin's order; undeclared types follow alphabetically
        for t in declared + sorted({k.knowledge_type for k in group} - set(declared)):
            sub = [k for k in group if k.knowledge_type == t]
            if not sub:
                continue
            lines += [f"### {type_heading(t, specs)}", ""]
            for k in sorted(sub, key=lambda x: (x.subject.lower(), x.id)):
                flags = f"{k.status} · L{k.verification_level} · {k.confidence:.0%}"
                if k.polarity == "negative":
                    flags += " · NEGATIVE"
                if k.product_version:
                    flags += f" · {k.product_version}"
                lines.append(f"- **{k.subject}** — {k.statement} *({flags})*")
                if k.explanation and k.explanation.strip().lower() != k.statement.strip().lower():
                    lines.append(f"  {k.explanation}")
                if k.code:
                    lines += ["", "  ```", *("  " + ln for ln in k.code.splitlines()), "  ```", ""]
                for key, label in DETAIL_LABEL.items():
                    if k.details.get(key):
                        lines.append(f"  - {label}: {k.details[key]}")
                for c in _cite_lines(evidence_by_item.get(k.id, [])):
                    lines.append(f"  - source: {c}")
            lines.append("")
    negatives = [k for k in items if k.polarity == "negative" and not k.historical]
    if negatives:
        lines += ["## Negative knowledge (what does not work)", ""]
        for k in sorted(negatives, key=lambda x: x.subject.lower()):
            lines.append(f"- **{k.subject}** — {k.statement} *({k.status})*")
        lines.append("")
    open_conflicts = [c for c in conflicts if c.status == "OPEN"]
    if open_conflicts:
        lines += [
            "## Open conflicts",
            "",
            "Sources disagree on these items; both sides are retained until resolved.",
            "",
        ]
        for c in open_conflicts:
            lines.append(f"- `{c.item_a_id}` vs `{c.item_b_id}`: {c.reason}")
        lines.append("")
    if glossary.get("terminology"):
        lines += ["## Glossary", ""]
        for term, definition in sorted(glossary["terminology"].items()):
            lines.append(f"- **{term}**: {definition}")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- README + HTML


def readme(manifest: Manifest) -> str:
    c = manifest.counts
    s = manifest.status_counts
    return "\n".join(
        [
            f"# {manifest.plugin_name} — Canonical Knowledge Snapshot",
            "",
            f"- Domain: `{manifest.domain}` · plugin {manifest.plugin_version} · platform {manifest.platform_version}",
            f"- Export schema: {manifest.export_schema_version} (`schema/`; minor = additive, major = breaking; "
            "independent of the platform, database and plugin versions) · id, version, timestamp and integrity "
            "hash: see `manifest.json`",
            "",
            "## What this is",
            "",
            "A reproducible export of the platform's *curated knowledge* for this domain. It is **not** the source of "
            "truth: every knowledge item cites evidence that points back to the original authoritative source.",
            "",
            "| Term | Meaning |",
            "|---|---|",
            *(f"| {k.replace('_', ' ')} | {v} |" for k, v in manifest.terminology.items()),
            "",
            "## Contents",
            "",
            f"- knowledge items: {c.get('knowledge', 0)} (verified {s.get('VERIFIED', 0)}, "
            f"supported {s.get('SUPPORTED', 0)}, "
            f"conflicted {s.get('CONFLICTED', 0)}, stale {s.get('STALE', 0)}, superseded {s.get('SUPERSEDED', 0)})",
            f"- evidence records: {c.get('evidence', 0)} · sources: {c.get('sources', 0)} · relationships: "
            f"{c.get('relationships', 0)} · examples: {c.get('examples', 0)} · "
            f"negative knowledge: {c.get('negative', 0)} · "
            f"conflicts: {c.get('conflicts', 0)} · changelog entries: {c.get('changelog', 0)}",
            "",
            "| File | Purpose |",
            "|---|---|",
            "| `manifest.json` | identity, counts, versions, per-file SHA-256, integrity hash |",
            "| `knowledge.jsonl` | one canonical record per knowledge item "
            "(provenance, lifecycle, evidence ids, dependencies) |",
            "| `evidence.jsonl` | denormalised evidence: source, document, version, hash, section, verbatim excerpt |",
            "| `sources.jsonl` | the source registry (authority, permissions, origin, cadence) |",
            "| `documents.jsonl` | every fetched document the evidence cites: url, version, content hash "
            "(+ hashing recipe), raw-bytes hash, fetch time — provenance and integrity, never the text |",
            "| `relationships.jsonl` | dependency graph edges between items |",
            "| `examples.jsonl` | structured examples with supporting items and validator results |",
            "| `negative.jsonl` | limitations, warnings, anti-patterns |",
            "| `glossary.json` | domain terminology and taxonomy |",
            "| `conflicts.json` | open and resolved contradictions |",
            "| `changelog.jsonl` | status transitions (audit trail) |",
            "| `ai/knowledge.jsonl` | AI Knowledge Source: one self-contained text record per item with citations |",
            "| `ai/index.json` | navigation index: taxonomy → item ids, counts, subjects, recommended filters |",
            "| `ai/knowledge.md` | the same knowledge grouped by taxonomy for reading or long-context ingestion |",
            "| `knowledge.html` | human-readable rendering |",
            "| `schema/*.schema.json`, `schema/vocabulary.json` | the export contract: JSON Schema (draft 2020-12) "
            "for every file above and the meaning of every controlled value; validate against these |",
            "",
            "## Consuming it (AI systems)",
            "",
            "1. Verify: recompute SHA-256 of each file and compare with `manifest.json`; the integrity hash "
            "identifies this exact knowledge state.",
            "2. Index `ai/knowledge.jsonl`: embed or full-text index the `text` field one record per chunk "
            "(each is self-contained: statement, explanation, code, details, scope and numbered sources). Keep "
            "`id`, `topic`, `type`, `polarity`, `status`, `usage`, `verification_level`, `product_version` and "
            "`citations` as metadata.",
            "3. Filter by `usage`: `cite` records can be answered with; `caution` records are flagged for "
            "review, awaiting revalidation, CONFLICTED / STALE or carry contradicting evidence — `caution_reasons` "
            "says why and the `text` starts with 'Caution:' — surface them only with that warning; `historical` "
            "(superseded) should not be answered with. Prefer `verification_level` ≥ 2 and VERIFIED over SUPPORTED.",
            "4. Answer with citations: every record carries `citations` (document url/title/section/excerpt or the "
            "person/organisation that provided it). Negative records (`polarity: negative`) say what does *not* "
            "work — use them to avoid recommending unsupported behaviour.",
            "5. Navigate with `ai/index.json` (taxonomy → ids, subjects → ids, counts) and `glossary.json`; "
            "`ai/knowledge.md` is the same knowledge as one long-context document.",
            "6. Stay current: apply delta snapshots on top of this one (see the platform's delta README) or "
            "replace it with a newer full snapshot; `dependencies` tell you which records may be affected "
            "when another one changes.",
            "",
        ]
    )


_HTML_CSS = """
body{font-family:Inter,system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 20px;
line-height:1.55;color:#161b25}
h1{letter-spacing:-.02em}h2{margin-top:36px;border-top:1px solid #d9dde5;padding-top:10px}h3{color:#4f5a6b}
code,pre{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}pre{background:#0b0e14;color:#e6ebf2;padding:12px;border-radius:10px;overflow:auto}
li{margin:4px 0}em{color:#667184}.meta{color:#667184;font-size:13px}
"""


def markdown_to_html(md: str, title: str) -> str:
    """Minimal, dependency-free Markdown → HTML for the snapshot document (headings, lists, code, paragraphs)."""
    out: list[str] = []
    in_code = False
    in_list = False
    para: list[str] = []
    code_lines: list[str] = []

    def flush_code() -> None:
        nonlocal code_lines
        out.append("<pre><code>" + "\n".join(html.escape(c) for c in code_lines) + "</code></pre>")
        code_lines = []

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            close_list()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if stripped.startswith("#"):
            flush_para()
            close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"<h{level}>{_inline(stripped[level:].strip())}</h{level}>")
        elif stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
        elif stripped.startswith("|"):
            flush_para()
            close_list()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            tag = "th" if not out or not out[-1].startswith("<tr>") and not out[-1].startswith("<table") else "td"
            if tag == "th":
                out.append("<table>")
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
        elif not stripped:
            flush_para()
            close_list()
            if out and out[-1].startswith("<tr>"):
                out.append("</table>")
        else:
            para.append(stripped)
    flush_para()
    close_list()
    if in_code:
        flush_code()
    if out and out[-1].startswith("<tr>"):
        out.append("</table>")
    body = "\n".join(out)
    head = f"<meta charset='utf-8'><title>{html.escape(title)}</title><style>{_HTML_CSS}</style>"
    return f"<!doctype html><html lang='en'><head>{head}</head><body>{body}</body></html>"


def _inline(text: str) -> str:
    text = html.escape(text)
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"(https?://[^\s)]+)", r'<a href="\1">\1</a>', text)
    return text
