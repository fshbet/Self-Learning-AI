"""Turn raw HTML/PDF bytes into normalized text + metadata + outgoing links."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import trafilatura
from lxml import html as lxml_html

_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


@dataclass
class NormalizedDoc:
    title: str
    text: str  # markdown-ish main content
    language: str
    published_at: str | None
    content_hash: str
    links: list[str] = field(default_factory=list)
    text_fingerprint: str = ""  # letters/digits only, lower-case: survives punctuation/whitespace/case edits
    canonical_url: str | None = None  # <link rel=canonical> / og:url when the page declares one


def canonicalize_url(url: str, base: str | None = None) -> str:
    """Absolute URL without fragment, trailing-slash normalised, tracking params dropped."""
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return ""
    query = "&".join(
        kv for kv in parts.query.split("&") if kv and not kv.lower().startswith(("utm_", "fbclid", "gclid", "ref="))
    )
    # Keep the path as published: a trailing slash changes how relative links resolve, so it is not
    # a cosmetic difference. Only an empty path is normalised to "/".
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _NL.sub("\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


_ALNUM = re.compile(r"[^0-9a-z]+")


def text_fingerprint(text: str) -> str:
    """Loose identity of a text: lower-case letters and digits only (audit P1.9). Two pages that differ only
    in punctuation, whitespace, casing or markdown decoration share a fingerprint."""
    return "sha256:" + hashlib.sha256(_ALNUM.sub("", (text or "").lower()).encode("utf-8")).hexdigest()


def _canonical_link(raw: bytes, base_url: str) -> str | None:
    """The page's own canonical URL (rel=canonical, then og:url), absolute and canonicalised."""
    try:
        tree = lxml_html.fromstring(raw)
    except Exception:
        return None
    for xpath in ("//link[@rel='canonical']/@href", "//meta[@property='og:url']/@content"):
        for value in tree.xpath(xpath):
            u = canonicalize_url(str(value), base_url)
            if u:
                return u
    return None


def _extract_links(raw: bytes, base_url: str) -> list[str]:
    try:
        tree = lxml_html.fromstring(raw)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for href in tree.xpath("//a/@href"):
        u = canonicalize_url(str(href), base_url)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def normalize_html(raw: bytes, url: str) -> NormalizedDoc:
    downloaded = raw.decode("utf-8", errors="replace")
    md = (
        trafilatura.extract(
            downloaded,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,
            include_comments=False,
            favor_precision=True,
        )
        or ""
    )
    meta = trafilatura.extract_metadata(downloaded, default_url=url)
    title = (meta.title if meta and meta.title else "") or _title_from_html(raw)
    text = normalize_text(md)
    return NormalizedDoc(
        title=title.strip(),
        text=text,
        language=(meta.language if meta and meta.language else "en") or "en",
        published_at=meta.date if meta and meta.date else None,
        content_hash=content_hash(text),
        links=_extract_links(raw, url),
        text_fingerprint=text_fingerprint(text),
        canonical_url=_canonical_link(raw, url),
    )


def _title_from_html(raw: bytes) -> str:
    try:
        tree = lxml_html.fromstring(raw)
        t = tree.findtext(".//title")
        return t or ""
    except Exception:
        return ""


def normalize_pdf(raw: bytes, url: str) -> NormalizedDoc:
    """PDF text extraction. Kept minimal; swap in a richer extractor via this seam."""
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        text = ""
    else:
        import io

        reader = PdfReader(io.BytesIO(raw))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    text = normalize_text(text)
    return NormalizedDoc(
        title=url.rsplit("/", 1)[-1],
        text=text,
        language="en",
        published_at=None,
        content_hash=content_hash(text),
        links=[],
        text_fingerprint=text_fingerprint(text),
    )


def normalize(raw: bytes, url: str, content_type: str) -> NormalizedDoc:
    if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
        return normalize_pdf(raw, url)
    return normalize_html(raw, url)
