"""Heading-aware chunking of normalized markdown text.

Chunk boundaries follow document structure so that evidence locators
(heading path + character offsets) stay meaningful. Chunking parameters are
versioned because they materially affect recall (§22).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHUNKER_VERSION = "headings@1.1"

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    index: int
    heading_path: list[str]
    text: str
    start: int  # char offset into the full document text
    end: int

    @property
    def title(self) -> str:
        return " > ".join(self.heading_path) if self.heading_path else ""


@dataclass
class _Section:
    heading_path: list[str]
    lines: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0


def _split_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    path: list[str] = []
    current = _Section(heading_path=[], start=0)
    offset = 0
    for line in text.split("\n"):
        m = _HEADING.match(line)
        line_len = len(line) + 1
        if m:
            current.end = offset
            if current.lines:
                sections.append(current)
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            # the section body starts after the heading line
            current = _Section(heading_path=list(path), start=offset + line_len)
        else:
            current.lines.append(line)
        offset += line_len
    current.end = offset
    if current.lines:
        sections.append(current)
    return sections


def _split_long_paragraphs(paragraphs: list[str], max_chars: int) -> list[str]:
    """Paragraphs longer than ``max_chars`` are split at sentence boundaries (hard-split as a last resort)."""
    out: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            out.append(para)
            continue
        buf = ""
        for sentence in _SENTENCE_END.split(para):
            while len(sentence) > max_chars:  # pathological: no sentence boundaries at all
                out.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if buf and len(buf) + 1 + len(sentence) > max_chars:
                out.append(buf)
                buf = sentence
            else:
                buf = f"{buf} {sentence}".strip()
        if buf:
            out.append(buf)
    return out


def chunk_text(text: str, *, max_chars: int = 6000, min_chars: int = 200) -> list[Chunk]:
    """Split by headings, merge tiny sections into their neighbour, split oversized ones by paragraph."""
    sections = _split_sections(text)
    chunks: list[Chunk] = []

    def emit(path: list[str], body: str, start: int, end: int) -> None:
        lead = len(body) - len(body.lstrip("\n"))
        body = body.strip("\n")
        start += lead
        if not body.strip():
            return
        if chunks and len(chunks[-1].text) < min_chars and chunks[-1].heading_path[: len(path) - 1] == path[:-1]:
            prev = chunks[-1]
            prev.text = prev.text + "\n\n" + body
            prev.end = end
            return
        chunks.append(Chunk(index=len(chunks), heading_path=path, text=body, start=start, end=end))

    for sec in sections:
        body = "\n".join(sec.lines)
        if len(body) <= max_chars:
            emit(sec.heading_path, body, sec.start, sec.end)
            continue
        # Oversized: pack paragraphs (and sentence pieces of very long paragraphs) up to max_chars.
        # Pieces are located in the body so offsets stay exact and chunk text keeps original separators.
        cursor = 0
        buf_start: int | None = None
        buf_end = 0
        for piece in _split_long_paragraphs(body.split("\n\n"), max_chars):
            idx = body.find(piece, cursor)
            if idx < 0:
                idx = cursor
            p_end = idx + len(piece)
            cursor = p_end
            if buf_start is not None and p_end - buf_start > max_chars:
                emit(sec.heading_path, body[buf_start:buf_end], sec.start + buf_start, sec.start + buf_end)
                buf_start = None
            if buf_start is None:
                buf_start = idx
            buf_end = p_end
        if buf_start is not None:
            emit(sec.heading_path, body[buf_start:buf_end], sec.start + buf_start, sec.start + buf_end)

    for i, c in enumerate(chunks):
        c.index = i
    return [c for c in chunks if len(c.text.strip()) >= min(min_chars, 40)]
