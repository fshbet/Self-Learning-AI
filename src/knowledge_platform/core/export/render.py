"""Human-readable (README.md, knowledge.md, knowledge.html) and AI-oriented (ai/knowledge.jsonl, ai/knowledge.md)
representations of a snapshot (req. 27, 28)."""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

from .schema import ConflictRecord, EvidenceRecord, KnowledgeRecord, Manifest

RENDER_VERSION = "render@1.0"  # bump when README/markdown/html output changes: it alters file hashes

TYPE_ORDER = ["definition", "fact", "procedure", "example", "best_practice", "limitation", "warning"]
TYPE_LABEL = {
    "definition": "Definitions",
    "fact": "Facts",
    "procedure": "Procedures",
    "example": "Examples",
    "best_practice": "Best practices",
    "limitation": "Limitations",
    "warning": "Warnings",
}


def _cite_lines(ev: list[EvidenceRecord]) -> list[str]:
    seen: set[str] = set()
    out = []
    for e in ev:
        if e.evidence_type != "extraction" or not e.document_url or e.document_url in seen:
            continue
        seen.add(e.document_url)
        title = e.document_title or e.source_name or e.document_url
        out.append(f"{title} — {e.document_url}" + (f" (v{e.document_version})" if e.document_version else ""))
    return out


# ----------------------------------------------------------------------------- AI knowledge source


def ai_record(k: KnowledgeRecord, ev: list[EvidenceRecord], related: list[str]) -> dict[str, Any]:
    """One self-contained record an AI/RAG system can index: text block + provenance + citations."""
    parts = [k.statement]
    if k.explanation:
        parts.append(k.explanation)
    if k.code:
        parts.append("```\n" + k.code + "\n```")
    if k.product_version:
        parts.append(f"Applies to: {k.product_version}")
    citations = [
        {
            "url": e.document_url,
            "title": e.document_title,
            "publisher": e.publisher,
            "excerpt": e.excerpt,
            "section": " > ".join(e.section) if e.section else None,
            "retrieved_at": e.retrieved_at,
        }
        for e in ev
        if e.evidence_type == "extraction" and e.verified
    ]
    return {
        "id": k.id,
        "domain": k.domain,
        "type": k.knowledge_type,
        "polarity": k.polarity,
        "origin": k.origin,
        "provenance": k.provenance,
        "topic": k.topic,
        "subject": k.subject,
        "text": "\n\n".join(parts),
        "statement": k.statement,
        "status": k.status,
        "historical": k.historical,
        "confidence": k.confidence,
        "verification_level": k.verification_level,
        "product_version": k.product_version,
        "publication_date": k.publication_date,
        "last_verified_at": k.last_verified_at,
        "citations": citations,
        "related_ids": related,
        "tags": k.tags,
    }


def ai_markdown(
    manifest: Manifest,
    items: list[KnowledgeRecord],
    evidence_by_item: dict[str, list[EvidenceRecord]],
    conflicts: list[ConflictRecord],
    glossary: dict[str, Any],
) -> str:
    by_topic: dict[str, list[KnowledgeRecord]] = defaultdict(list)
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
        for t in TYPE_ORDER + sorted({k.knowledge_type for k in group} - set(TYPE_ORDER)):
            sub = [k for k in group if k.knowledge_type == t]
            if not sub:
                continue
            lines += [f"### {TYPE_LABEL.get(t, t.replace('_', ' ').title())}", ""]
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
            f"- Schema: {manifest.schema_version} · id, version, timestamp and integrity hash: see `manifest.json`",
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
            "| `relationships.jsonl` | dependency graph edges between items |",
            "| `examples.jsonl` | structured examples with supporting items and validator results |",
            "| `negative.jsonl` | limitations, warnings, anti-patterns |",
            "| `glossary.json` | domain terminology and taxonomy |",
            "| `conflicts.json` | open and resolved contradictions |",
            "| `changelog.jsonl` | status transitions (audit trail) |",
            "| `ai/knowledge.jsonl` | AI Knowledge Source: one self-contained text record per item with citations |",
            "| `ai/knowledge.md` | the same knowledge grouped by taxonomy for reading or long-context ingestion |",
            "| `knowledge.html` | human-readable rendering |",
            "",
            "## Consuming it",
            "",
            "Index `ai/knowledge.jsonl` (field `text`, keep `citations`, `status`, `verification_level`, "
            "`product_version` as metadata). Prefer VERIFIED over SUPPORTED, surface CONFLICTED and STALE "
            "with a warning, and always show "
            "citations. Verify integrity by recomputing SHA-256 of each file and comparing with `manifest.json`.",
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
