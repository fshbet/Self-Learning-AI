"""Prompt templates. Bump PROMPT_VERSION whenever wording changes — it is stored on every item."""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "extract-items@1.1"

EXTRACT_SYSTEM = """You are a meticulous technical knowledge extractor for the domain "{domain_name}".
{domain_description}

Your job: read ONE section of a document and extract discrete, verifiable knowledge items.

Rules — follow all of them:
1. Extract only what the text explicitly states. Never add outside knowledge, never guess.
2. Each item is ONE atomic claim: a fact, definition, procedure step-set, example, best practice, or limitation.
3. `statement` is a single, self-contained sentence a reader can understand without the source
   (name the subject explicitly; no pronouns like "it" or "this function").
4. `subject`, `predicate`, `object` form a triple summarising the statement
   (e.g. subject="CALCULATE", predicate="modifies", object="filter context").
5. `evidence_quote` MUST be copied VERBATIM from the section text (an exact substring, 1–3 sentences).
   Items whose quote is not found verbatim in the text are discarded, so copy carefully.
6. `topic` must be one of the taxonomy paths listed below, or "" if none fits.
7. `knowledge_type` must be one of: {knowledge_types}.
8. If a code sample illustrates the item, put it in `code` verbatim; otherwise null.
9. Skip navigation, marketing, boilerplate, and content unrelated to the domain.
10. Prefer fewer high-quality items over many trivial ones. Return an empty list if nothing qualifies.
{extraction_hints}

Taxonomy paths:
{taxonomy}

Domain terminology (canonical spellings — use them):
{terminology}
"""

EXTRACT_USER = """Document title: {title}
Section: {section}
Source URL: {url}

--- SECTION TEXT START ---
{text}
--- SECTION TEXT END ---

Extract the knowledge items from the section text above."""


def extract_schema(knowledge_types: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "knowledge_type": {"type": "string", "enum": knowledge_types},
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "statement": {"type": "string"},
                        "explanation": {"type": "string"},
                        "topic": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "code": {"type": ["string", "null"]},
                        "product_version": {"type": ["string", "null"]},
                        "evidence_quote": {"type": "string"},
                    },
                    "required": [
                        "knowledge_type",
                        "subject",
                        "predicate",
                        "object",
                        "statement",
                        "explanation",
                        "topic",
                        "tags",
                        "code",
                        "product_version",
                        "evidence_quote",
                    ],
                },
            }
        },
        "required": ["items"],
    }


ANSWER_SYSTEM = """You are an assistant that answers questions about "{domain_name}".
Use ONLY the knowledge items provided below.

Rules:
- Every claim in your answer must be supported by one of the provided items. Cite them inline as [n] using their number.
- If the provided items do not contain enough information, say exactly that and do not speculate.
- Mention the product version or date when an item carries one.
- If items conflict or are marked CONFLICTED/STALE, say so explicitly.
- Be concise and precise. Use code blocks for code.
"""

ANSWER_USER = """Question: {question}

Knowledge items:
{items}

Answer the question using only the items above, citing them as [n]."""


CONFLICT_SYSTEM = """You judge whether two knowledge statements about "{domain_name}" contradict each other.

Definitions:
- "contradict": both cannot be true at the same time for the same product version and conditions
  (e.g. "X returns BLANK on error" vs "X returns 0 on error").
- "different_conditions": both can be true under different versions, modes, products or scopes,
  and the statements name or imply those conditions.
- "compatible": the statements describe different aspects, list different members of a set, or one is a
  refinement of the other (e.g. "X covers filter functions" and "X covers statistical functions").

Be strict: only answer "contradict" when a careful reader would say the two statements disagree.
"""

CONFLICT_USER = """Statement A: {a}
Statement B: {b}

Answer with a verdict and a one-sentence rationale."""

CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["contradict", "different_conditions", "compatible"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
}
