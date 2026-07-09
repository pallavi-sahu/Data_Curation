"""Extract standalone news articles from crawled news-site URLs."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

import trafilatura

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

# Typical News18 / Local18 article paths: /news/<section>/<slug>.html
_ARTICLE_PATH_RE = re.compile(
    r"/news/[^/?#]+/[^/?#]+(?:\.html?)?/?$",
    re.IGNORECASE,
)

_NON_ARTICLE_PATH_MARKERS = (
    "/photogallery/",
    "/videos/",
    "/short-videos/",
    "/short-news/",
    "/topic/",
    "/live-tv",
    "/web-stories/",
)

_METADATA_LINE_RES = [
    re.compile(r"^published by\s*:", re.I),
    re.compile(r"^last updated\s*:", re.I),
    re.compile(r"^updated\s*:", re.I),
    re.compile(r"^news18[-\s]", re.I),
    re.compile(r"^advertisement\s*$", re.I),
    re.compile(r"^read more\s*$", re.I),
    re.compile(r"^also read\s*:", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^share\s*:", re.I),
    re.compile(r"^follow us", re.I),
    re.compile(r"^copyright", re.I),
    re.compile(r"^all rights reserved", re.I),
    re.compile(r"^ಕನ್ನಡ edition", re.I),
]

_HEADING_LINE_RE = re.compile(
    r"^(?:pm modi|breaking|live updates|photo gallery|ವೀಡಿಯೋ)\b",
    re.I,
)


def is_article_url(url: str) -> bool:
    """True for URLs that look like a single news story (not hub/video/gallery)."""
    parsed = urllib_parse.urlparse(url)
    path = (parsed.path or "/").lower()
    if any(marker in path for marker in _NON_ARTICLE_PATH_MARKERS):
        return False
    if not _ARTICLE_PATH_RE.search(path):
        return False
    # Listing pages: /news/ or /news/section/ only
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 3:
        return False
    if parts[0] != "news":
        return False
    return True


def filter_article_urls(urls: list[str]) -> list[str]:
    """Keep article-like URLs in stable order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if not is_article_url(url):
            continue
        key = urllib_parse.urlunparse(
            urllib_parse.urlparse(url)._replace(fragment="", query="")
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return ordered


def extract_article_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """
    Download a news article URL and return body text (headings/metadata stripped).

    Uses Trafilatura, then removes bylines, nav crumbs, and heading-like lines.
    Paragraphs are joined with blank lines to preserve context within the article.
    """
    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        include_images=False,
        include_links=False,
        favor_precision=True,
        url=url,
    )
    if not text or not text.strip():
        text = _extract_from_article_container(html)
    if not text:
        return ""

    return _clean_article_text(text)


def _extract_from_article_container(html: str) -> str:
    """Fallback when Trafilatura returns little (rare on News18)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for selector in (
        "nav",
        "header",
        "footer",
        "aside",
        "script",
        "style",
        "noscript",
        ".nav",
        ".menu",
        ".footer",
        ".header",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    ):
        for element in soup.select(selector):
            element.decompose()

    root = (
        soup.select_one("[class*='pge_contnr']")
        or soup.select_one("article")
        or soup.find("main")
        or soup.body
    )
    if not root:
        return ""

    chunks: list[str] = []
    for element in root.find_all(["p", "li"]):
        text = element.get_text(" ", strip=True)
        if text and len(text.split()) >= 6:
            chunks.append(text)

    return "\n\n".join(chunks)


_INLINE_META_SPLIT_RE = re.compile(
    r"\s+(?:Last Updated|Published by|Updated)\s*:\s*",
    re.IGNORECASE,
)


def _clean_article_text(raw: str) -> str:
    """Drop metadata/headings; keep article paragraphs."""
    raw = _INLINE_META_SPLIT_RE.split(raw.strip(), maxsplit=1)[-1].strip()

    paragraphs: list[str] = []
    blocks = re.split(r"\n\s*\n+", raw.strip())

    for block in blocks:
        block = re.sub(r"\s+", " ", block).strip()
        if not block:
            continue
        block = _strip_leading_headline_prefix(block)
        if not block:
            continue
        if _is_metadata_or_heading_line(block):
            continue
        if len(block.split()) < 8:
            continue
        paragraphs.append(block)

    if not paragraphs and raw.strip():
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        for line in lines:
            line = _strip_leading_headline_prefix(line)
            if not line or _is_metadata_or_heading_line(line):
                continue
            if len(line.split()) < 8:
                continue
            paragraphs.append(line)

    if len(paragraphs) >= 2 and len(paragraphs[0].split()) < 25:
        first, second = paragraphs[0], paragraphs[1]
        if first in second or second.startswith(first[:40]):
            paragraphs = paragraphs[1:]

    return "\n\n".join(paragraphs).strip()


def _strip_leading_headline_prefix(text: str) -> str:
    """Remove short 'Label: headline' prefix before article body."""
    match = re.match(r"^[^:]+:\s*(.+)$", text)
    if not match:
        return text
    rest = match.group(1).strip()
    if len(rest.split()) < 12:
        return text
    if _INLINE_META_SPLIT_RE.search(rest):
        return _INLINE_META_SPLIT_RE.split(rest, maxsplit=1)[-1].strip()
    return rest


def _is_metadata_or_heading_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < 60 and stripped.endswith(":"):
        return True
    for pattern in _METADATA_LINE_RES:
        if pattern.search(stripped):
            return True
    if _HEADING_LINE_RE.match(stripped) and len(stripped.split()) < 20:
        return True
    # Single-line breadcrumb / category tags
    if stripped.count("|") >= 2:
        return True
    return False
