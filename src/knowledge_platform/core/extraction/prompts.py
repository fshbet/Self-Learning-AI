"""Prompt templates. Bump PROMPT_VERSION whenever wording changes — it is stored on every item."""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "extract-items@1.3"

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
11. For examples, fill `details` with expected_behavior / expected_result / common_mistake when the text states them.
12. For limitations, warnings and anti-patterns ("do not do X"), set `polarity` to "negative", put the
    condition under which it fails or is unsupported in `details.condition` and, when the text names one,
    the recommended alternative in `details.workaround`.
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
                        "polarity": {"type": "string", "enum": ["positive", "negative"]},
                        "details": {
                            "type": "object",
                            "properties": {
                                "expected_behavior": {"type": ["string", "null"]},
                                "expected_result": {"type": ["string", "null"]},
                                "common_mistake": {"type": ["string", "null"]},
                                "condition": {"type": ["string", "null"]},
                            },
                        },
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
                        "polarity",
                    ],
                },
            }
        },
        "required": ["items"],
    }


# answer prompt + retrieval policy version: recorded in every evaluation's config so a change here is never mistaken
# for a change in knowledge (1.1: caution labels for stale/conflicted/flagged items, CANDIDATE excluded by default)
ANSWER_VERSION = "answer@1.2"  # 1.2: answer plan in the prompt, targeted regeneration (ADR 0006)
ANSWER_SYSTEM = """You are an assistant that answers questions about "{domain_name}".
Use ONLY the knowledge items provided below.

Rules:
- Every claim in your answer must be supported by one of the provided items. Cite them inline as [n] using their number.
- If the provided items do not contain enough information, say exactly that and do not speculate.
- Mention the product version or date when an item carries one.
- If items conflict or are marked CONFLICTED/STALE, say so explicitly.
- An item marked UNVERIFIED CANDIDATE, FLAGGED FOR REVIEW or AWAITING REVALIDATION is not trusted knowledge: if you
  use it, say that it is unverified / under review; prefer verified items when they cover the question.
- Items marked LIMITATION / WARNING / ANTI-PATTERN describe what does NOT work or should be avoided: when one is
  relevant to the question, state the limitation and its condition explicitly rather than inferring behaviour
  from positive statements.
- For examples, include the expected result and the common mistake when the item provides them.
- Be concise and precise. Use code blocks for code.
"""

ANSWER_USER = """Question: {question}

Knowledge items:
{items}

Answer the question using only the items above, citing them as [n]."""

# answer@1.2: the deterministic plan (what the evidence supports and the question needs) precedes the instruction
ANSWER_USER_PLANNED = """Question: {question}

Knowledge items:
{items}

Plan (derived from the question and the items above):
{plan}

Answer the question using only the items above, citing them as [n]. Cover every point in the plan that the
items support; do not add anything the items do not state."""

REGENERATE_USER = """Question: {question}

Knowledge items:
{items}

Your previous answer:
{answer}

It is supported by the items but incomplete. The items also establish the following points, which the answer
does not cover:
{missing}

Rewrite the answer so that it also covers those points, citing the items given for each as [n]. Keep every claim
and citation of the previous answer that is still correct, keep the same [n] numbering, and do not add anything
the items do not state."""


CONFLICT_SYSTEM = """You judge whether two knowledge statements about "{domain_name}" contradict each other.

Definitions:
- "contradict": both cannot be true at the same time for the same product version, scope and conditions
  (e.g. "X returns BLANK on error" vs "X returns 0 on error"; "X supports Y" vs "X does not support Y").
- "different_versions": both can be true because they describe different product versions or releases.
- "different_scopes": both can be true because they describe different products, editions, modes or environments.
- "different_conditions": both can be true under different explicit conditions (settings, inputs, states) that the
  statements name or clearly imply.
- "compatible": the statements describe different aspects, list different members of a set, or one is a
  refinement of the other (e.g. "X covers filter functions" and "X covers statistical functions").

Be strict: only answer "contradict" when a careful reader would say the two statements disagree. Two statements
whose surface wording differs but that carry their own conditions are not a contradiction. When you answer
different_versions / different_scopes / different_conditions, name the distinguishing condition in "conditions".
"""

CONFLICT_USER = """Statement A: {a}
Statement B: {b}

Answer with a verdict, the distinguishing conditions if any, and a one-sentence rationale."""

CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["contradict", "different_versions", "different_scopes", "different_conditions", "compatible"],
        },
        "conditions": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
}


JUDGE_VERSION = "judge@1.0"

JUDGE_SYSTEM = """You grade an assistant's answer to a question about "{domain_name}" against a reference answer.

Grade strictly and only on content:
- "correct": the answer conveys the same facts as the reference (wording may differ; acceptable alternatives count).
- "supported_by_citations": every factual claim in the answer is backed by one of the cited knowledge items shown.
- "hallucinated_claims": list claims in the answer that are NOT present in the cited items (empty if none).
- "missing_points": key points of the reference that the answer omits (empty if none).
Give a one-sentence rationale.
"""

JUDGE_USER = """Question: {question}

Reference answer: {expected}
Acceptable alternatives: {alternatives}

Cited knowledge items available to the assistant:
{items}

Assistant answer:
{answer}
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "supported_by_citations": {"type": "boolean"},
        "hallucinated_claims": {"type": "array", "items": {"type": "string"}},
        "missing_points": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["correct", "supported_by_citations", "hallucinated_claims", "missing_points", "rationale"],
}
