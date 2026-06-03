"""Extract main body text from local HTML files and .html URLs."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

import trafilatura

logger = logging.getLogger(__name__)

_HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
_HTML_URL_RE = re.compile(r"\.html?(?:[?#]|$)", re.IGNORECASE)


def is_html_url(url: str) -> bool:
    """True for http(s) URLs whose path looks like an HTML document."""
    stripped = url.strip()
    if not stripped.lower().startswith(("http://", "https://")):
        return False
    return bool(_HTML_URL_RE.search(stripped))


def extract_text_from_html_file(path: str | Path) -> str:
    """Read a local HTML file and return main body text."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"HTML file not found: {file_path}")

    raw = _read_html_bytes(file_path)
    html = raw.decode("utf-8", errors="replace")
    source = str(file_path.resolve())
    text = _extract_from_html(html, source=source)
    if not text:
        logger.warning("No text extracted from HTML file: %s", source)
    return text


def extract_text_from_html_url(url: str, *, timeout: int = 30) -> str:
    """Download an HTML URL and return main body text."""
    url = url.strip()
    request = urllib_request.Request(
        url,
        headers={"User-Agent": "data-curation-pipeline/1.0 (research use)"},
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib_error.URLError as exc:
        logger.warning("Failed to download HTML URL: %s (%s)", url, exc)
        return ""

    html = raw.decode("utf-8", errors="replace")
    text = _extract_from_html(html, source=url)
    if not text:
        logger.warning("No text extracted from HTML URL: %s", url)
    return text


def _read_html_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16").encode("utf-8")
    return data


def _extract_from_html(html: str, *, source: str) -> str:
    trafilatura_text = (
        trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            include_images=False,
            include_links=False,
            favor_recall=True,
            url=source if source.startswith("http") else None,
        )
        or ""
    ).strip()
    fallback_text = _extract_from_html_fallback(html)

    if not trafilatura_text:
        return fallback_text
    if not fallback_text:
        return trafilatura_text

    tf_words = _word_count(trafilatura_text)
    fb_words = _word_count(fallback_text)
    # Trafilatura often stops early on <br>-heavy etexts (e.g. Project Madurai).
    if fb_words >= tf_words * 1.25:
        logger.info(
            "HTML structural fallback selected (%d words vs trafilatura %d): %s",
            fb_words,
            tf_words,
            source,
        )
        return fallback_text
    return trafilatura_text


def _word_count(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def _extract_from_html_fallback(html: str) -> str:
    """Fallback for plain-text etexts in ``<pre>`` or simple ``<p>``/``<li>`` blocks."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    parts: list[str] = []
    for pre in soup.find_all("pre"):
        block = pre.get_text("\n", strip=True)
        if block:
            parts.append(block)

    if parts:
        return "\n\n".join(parts).strip()

    body = soup.body or soup
    chunks: list[str] = []
    for element in body.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        if element.find_parent(["nav", "header", "footer", "aside"]):
            continue
        text = element.get_text("\n", strip=True)
        if text:
            chunks.append(text)

    if chunks:
        return "\n\n".join(chunks).strip()

    return _normalize_body_text(body.get_text("\n"))


def _normalize_body_text(raw: str) -> str:
    """Collapse blank lines; drop lines that are only digits (verse/page numbers)."""
    lines: list[str] = []
    for line in raw.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned:
            continue
        if re.fullmatch(r"\d{1,4}", cleaned):
            continue
        lines.append(cleaned)
    return "\n\n".join(lines).strip()
