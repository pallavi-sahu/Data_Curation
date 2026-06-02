"""Extract article body paragraphs from Wikipedia via the MediaWiki API."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
from html import unescape

from curation.config import (
    INDIC_WIKIPEDIA_MAIN_PAGE_TITLES,
    WIKIPEDIA_MAIN_PAGE_TITLES_UNIVERSAL,
)

logger = logging.getLogger(__name__)

_WIKI_HOST_RE = re.compile(r"^([a-z-]+)\.wikipedia\.org$", re.IGNORECASE)
_HTML_CACHE: dict[tuple[str, str], str] = {}
_last_wiki_request_at: float = 0.0
_wiki_request_interval: float = 2.5
_wiki_429_backoff: float = 0.0  # extra pause after rate-limit errors


def configure_wiki_rate_limit(
    *,
    min_interval: float = 2.5,
) -> None:
    """Set minimum spacing between Wikipedia API requests for this process."""
    global _wiki_request_interval
    _wiki_request_interval = max(0.0, min_interval)


def clear_wiki_html_cache() -> None:
    """Clear cached Wikipedia HTML (e.g. between separate pipeline runs)."""
    _HTML_CACHE.clear()


def is_wiki_html_cached(url: str) -> bool:
    try:
        lang, title = _parse_wikipedia_url(url)
    except ValueError:
        return False
    return (lang, title) in _HTML_CACHE


# Boilerplate blocks common on Wikipedia main pages and footers.
_BOILERPLATE_RES = [
    re.compile(r"10,?\d{2},?\d{3}\s+கட்டுரைகளுக்கு\s+மேல்", re.IGNORECASE),
    re.compile(r"இந்த\s+விக்கிப்பீடியா\s+.*?\s+எழுதப்பட்டுள்ளது", re.IGNORECASE),
    re.compile(r"wiki\s*pedia\s*languages?", re.IGNORECASE),
    re.compile(r"^[\s\-·|]+$"),
    re.compile(r"^(English|Deutsch|español|français|italiano)\s*·", re.IGNORECASE),
]


def is_wikipedia_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    return bool(_WIKI_HOST_RE.match(host))


def extract_text_from_wikipedia(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """
    Fetch Wikipedia article paragraphs as plain text.

    Uses the MediaWiki parse API and extracts ``<p>`` tags from the article body,
    skipping navboxes, infoboxes, tables, and language-link footers.
    """
    wiki_lang, title = _parse_wikipedia_url(url)

    html, canonical_title = _fetch_parse_html(wiki_lang, title, timeout=timeout)
    is_main = _is_main_page(canonical_title, wiki_lang) or _html_is_main_page_layout(
        html, wiki_lang=wiki_lang, title=canonical_title
    )
    if is_main:
        logger.info(
            "Wikipedia main page detected (%r). Extracting featured sections "
            "and linked articles from layout tables.",
            canonical_title,
        )

    paragraphs = _paragraphs_from_html(html, is_main_page=is_main)
    paragraphs = [p for p in paragraphs if not _is_boilerplate(p)]

    if not paragraphs:
        logger.warning("No article paragraphs extracted from Wikipedia page: %s", url)

    return "\n\n".join(paragraphs)


def discover_wikipedia_article_links(
    url: str,
    *,
    timeout: int = 30,
    max_links: int = 25,
) -> list[str]:
    """
    Discover internal ``/wiki/`` article links from any Wikipedia page.

    Links are taken from the article body (paragraphs and lists), not from
    site-wide navigation headers. Main/portal pages also scan ``MainPageBG``
    feature tables.
    """
    wiki_lang, title = _parse_wikipedia_url(url)
    html, canonical_title = _fetch_parse_html(wiki_lang, title, timeout=timeout)
    return _discover_links_from_html(
        html,
        wiki_lang=wiki_lang,
        title=canonical_title,
        max_links=max_links,
    )


def crawl_wikipedia_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 2,
    max_pages: int = 150,
    links_per_page: int = 40,
    timeout: int = 30,
) -> list[str]:
    """
    Breadth-first crawl of Wikipedia article URLs starting from seed pages.

    Depth 0 = seeds only. Depth 1 = seeds + links found on seeds. Depth 2 =
    also pages linked from those articles, and so on. Only follows ``/wiki/``
    links in the same language wiki as each page.
    """
    if crawl_depth <= 0:
        return _dedupe_urls(seed_urls)

    unlimited_pages = max_pages <= 0
    ordered: list[str] = []
    seen: set[str] = set()
    current_level = _dedupe_urls(seed_urls)

    for depth in range(crawl_depth + 1):
        next_level: list[str] = []
        for url in current_level:
            if not unlimited_pages and len(ordered) >= max_pages:
                break
            if url not in seen:
                seen.add(url)
                ordered.append(url)

            if depth >= crawl_depth or (
                not unlimited_pages and len(ordered) >= max_pages
            ):
                continue

            try:
                wiki_lang, title = _parse_wikipedia_url(url)
                html, canonical_title = _fetch_parse_html(
                    wiki_lang, title, timeout=timeout
                )
                discovered = _discover_links_from_html(
                    html,
                    wiki_lang=wiki_lang,
                    title=canonical_title,
                    max_links=links_per_page,
                )
            except Exception:
                logger.exception("Failed discovering links at depth %s from %s", depth, url)
                continue

            for link in discovered:
                if link in seen or link in next_level:
                    continue
                if not unlimited_pages and len(ordered) + len(next_level) >= max_pages:
                    break
                next_level.append(link)

            logger.info(
                "Crawl depth %s: found %s links from %s",
                depth,
                len(discovered),
                url,
            )

        if not next_level:
            break
        current_level = next_level

    if unlimited_pages:
        return ordered
    return ordered[:max_pages]


def _discover_links_from_html(
    html: str,
    *,
    wiki_lang: str,
    title: str,
    max_links: int,
) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".mw-parser-output") or soup.select_one(
        "#mw-content-text .mw-parser-output"
    )
    if root is None:
        return []

    links: list[str] = []
    seen: set[str] = set()

    is_main = _is_main_page(title, wiki_lang) or _html_is_main_page_layout(
        html, wiki_lang=wiki_lang, title=title
    )
    if is_main:
        for container in root.select("td.MainPageBG, td#mp-left, td#mp-right, table.mainpage"):
            links.extend(
                _extract_article_links([container], wiki_lang=wiki_lang, seen=seen)
            )
        for block in root.select("p, li, dd, h2, h3, h4"):
            if block.find_parent([".navbox", ".infobox", ".reflist", ".metadata"]):
                continue
            links.extend(
                _extract_article_links([block], wiki_lang=wiki_lang, seen=seen)
            )
    else:
        content_root = BeautifulSoup(str(root), "lxml").select_one(".mw-parser-output") or root
        _strip_non_content(content_root)
        blocks = content_root.select("p, li, dd, h2, h3, h4")
        if blocks:
            for block in blocks:
                if block.find_parent(["table", ".navbox", ".infobox", ".reflist"]):
                    continue
                links.extend(
                    _extract_article_links([block], wiki_lang=wiki_lang, seen=seen)
                )
        else:
            links.extend(
                _extract_article_links([content_root], wiki_lang=wiki_lang, seen=seen)
            )

    if max_links > 0:
        return links[:max_links]
    return links


def _dedupe_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = raw.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _title_from_path(path: str) -> str | None:
    if not path.startswith("/wiki/"):
        return None
    raw = path[len("/wiki/") :]
    title = urllib.parse.unquote(raw).replace("_", " ").strip()
    return title or None


def _parse_wikipedia_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    match = _WIKI_HOST_RE.match(host)
    if not match:
        raise ValueError(f"Not a Wikipedia URL: {url}")
    title = _title_from_path(parsed.path)
    if not title:
        raise ValueError(f"Could not parse Wikipedia page title from: {url}")
    return match.group(1), title


def _throttle_wiki_request() -> None:
    """Enforce minimum interval between API calls; honor post-429 backoff."""
    global _last_wiki_request_at, _wiki_429_backoff

    if _wiki_429_backoff > 0:
        logger.info("Waiting %.1fs after rate limit before next request", _wiki_429_backoff)
        time.sleep(_wiki_429_backoff)
        _wiki_429_backoff = 0.0

    if _wiki_request_interval <= 0:
        return

    elapsed = time.monotonic() - _last_wiki_request_at
    if elapsed < _wiki_request_interval:
        time.sleep(_wiki_request_interval - elapsed)
    _last_wiki_request_at = time.monotonic()


def _fetch_parse_html(lang: str, title: str, *, timeout: int) -> tuple[str, str]:
    cache_key = (lang, _normalize_wiki_title(title))
    if cache_key in _HTML_CACHE:
        return _HTML_CACHE[cache_key], cache_key[1]

    params = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": title,
            "prop": "text",
            "format": "json",
            "disabletoc": "1",
            "redirects": "1",
        }
    )
    api_url = f"https://{lang}.wikipedia.org/w/api.php?{params}"
    request = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": (
                "data-curation-pipeline/1.0 "
                "(https://github.com/karya; research dataset builder)"
            )
        },
    )

    max_attempts = 8
    payload = None
    for attempt in range(max_attempts):
        _throttle_wiki_request()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            retry_after = 0.0
            if exc.headers and exc.headers.get("Retry-After"):
                try:
                    retry_after = float(exc.headers.get("Retry-After", 0))
                except ValueError:
                    retry_after = 0.0
            sleep_s = max(retry_after, min(120.0, 8.0 * (2**attempt)))
            global _wiki_429_backoff
            _wiki_429_backoff = max(_wiki_429_backoff, sleep_s * 0.5)
            if attempt == max_attempts - 1:
                raise
            logger.warning(
                "Wikipedia API throttled (429) for '%s'. Retry %s/%s in %.0fs...",
                title,
                attempt + 1,
                max_attempts,
                sleep_s,
            )
            time.sleep(sleep_s)

    if payload is None:
        raise RuntimeError("Failed to fetch Wikipedia API payload")

    if "error" in payload:
        code = payload["error"].get("code", "unknown")
        info = payload["error"].get("info", "")
        raise RuntimeError(f"Wikipedia API error ({code}): {info}")

    html = payload["parse"]["text"]["*"]
    canonical_title = _normalize_wiki_title(payload["parse"].get("title", title))
    resolved_key = (lang, canonical_title)
    _HTML_CACHE[resolved_key] = html
    requested_key = (lang, _normalize_wiki_title(title))
    if requested_key != resolved_key:
        _HTML_CACHE[requested_key] = html
    return html, canonical_title


def _normalize_wiki_title(title: str) -> str:
    return unicodedata.normalize("NFC", title.strip())


def _titles_match(a: str, b: str) -> bool:
    na = _normalize_wiki_title(a)
    nb = _normalize_wiki_title(b)
    if na == nb:
        return True
    if na.replace(" ", "") == nb.replace(" ", ""):
        return True
    if na.isascii() and nb.isascii() and na.lower() == nb.lower():
        return True
    return False


def _is_main_page(title: str, wiki_lang: str | None = None) -> bool:
    if _titles_match(title, "Main Page"):
        return True
    for universal in WIKIPEDIA_MAIN_PAGE_TITLES_UNIVERSAL:
        if _titles_match(title, universal):
            return True

    if wiki_lang:
        host = wiki_lang.strip().lower()
        for candidate in INDIC_WIKIPEDIA_MAIN_PAGE_TITLES.get(host, ()):
            if _titles_match(title, candidate):
                return True

    for titles in INDIC_WIKIPEDIA_MAIN_PAGE_TITLES.values():
        for candidate in titles:
            if _titles_match(title, candidate):
                return True
    return False


def _html_is_main_page_layout(
    html: str,
    *,
    wiki_lang: str | None = None,
    title: str | None = None,
) -> bool:
    """Detect portal/main-page layout from Wikipedia HTML structure."""
    if title and wiki_lang and _is_main_page(title, wiki_lang):
        return True

    from bs4 import BeautifulSoup

    root = BeautifulSoup(html, "lxml").select_one(".mw-parser-output")
    if root is None:
        return False
    main_cells = root.select("td.MainPageBG, td#mp-left, td#mp-right, table.mainpage")
    if len(main_cells) >= 2:
        return True
    if main_cells:
        return True
    # Some wikis (e.g. Urdu) use plain p/li portals without MainPageBG cells.
    wiki_links = [
        a
        for a in root.select('a[href^="/wiki/"]')
        if a.get("href") and not a["href"].startswith("/wiki/Special:")
    ]
    readable_blocks = root.select("p, li")
    return len(wiki_links) >= 40 and len(readable_blocks) >= 8


def _paragraphs_from_html(html: str, *, is_main_page: bool = False) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".mw-parser-output") or soup.select_one(
        "#mw-content-text .mw-parser-output"
    )
    if root is None:
        return []

    if is_main_page:
        return _paragraphs_from_main_page(root)

    _strip_non_content(root)

    paragraphs: list[str] = []
    for element in root.find_all(["p", "li"], recursive=True):
        if element.find_parent(["table", ".navbox", ".infobox", ".reflist"]):
            continue
        text = _clean_element_text(element)
        if text:
            paragraphs.append(text)

    return _dedupe_paragraphs(paragraphs)


def _paragraphs_from_main_page(root) -> list[str]:
    """
    Main-page body text lives in layout ``<table>`` cells, not plain ``<p>`` tags.

    Extract ``p``/``li`` from MainPage content cells and skip footer/language tables.
    """
    paragraphs: list[str] = []

    for cell in root.select("td.MainPageBG, td#mp-left, td#mp-right, table.mainpage"):
        for element in cell.find_all(["p", "li"], recursive=True):
            text = _clean_element_text(element)
            if text and not _is_boilerplate(text):
                paragraphs.append(text)

    # Fallback: short news / feature blurbs sometimes sit directly in cells.
    for cell in root.select("td.MainPageBG"):
        if cell.find(["p", "li"]):
            continue
        text = _clean_element_text(cell)
        if text and len(text.split()) >= 8 and not _is_boilerplate(text):
            paragraphs.append(text)

    if paragraphs:
        return _dedupe_paragraphs(paragraphs)

    # Portals without classic MainPageBG layout (e.g. Urdu — content inside tables).
    for element in root.find_all(["p", "li"], recursive=True):
        if element.find_parent([".navbox", ".infobox", ".reflist", ".metadata"]):
            continue
        text = _clean_element_text(element)
        if text and not _is_boilerplate(text):
            paragraphs.append(text)

    return _dedupe_paragraphs(paragraphs)


def _strip_non_content(root) -> None:
    for selector in (
        "table",
        ".navbox",
        ".infobox",
        ".metadata",
        ".noprint",
        ".mw-editsection",
        ".reference",
        ".reflist",
        ".thumb",
        ".magnify",
        "style",
        "script",
        ".toc",
        "#toc",
        ".sidebar",
        ".portail",
        ".sistersitebox",
        ".interlanguage-link",
        ".mw-jump-link",
    ):
        for tag in root.select(selector):
            tag.decompose()


def _clean_element_text(element) -> str:
    text = unescape(element.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", text).strip()


def _dedupe_paragraphs(paragraphs: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for paragraph in paragraphs:
        key = paragraph.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(paragraph)
    return unique


def _extract_article_links(
    containers,
    *,
    wiki_lang: str,
    seen: set[str] | None = None,
) -> list[str]:
    links: list[str] = []
    if seen is None:
        seen = set()

    for container in containers:
        for anchor in container.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href.startswith("/wiki/"):
                continue
            title_part = href[len("/wiki/") :]
            if not title_part:
                continue
            title_part = title_part.split("#", 1)[0]
            if not title_part:
                continue
            if ":" in title_part.split("/", 1)[0]:
                # Skip namespaces like Category:, File:, Help:, Template:, etc.
                continue
            absolute = (
                f"https://{wiki_lang}.wikipedia.org/wiki/"
                f"{urllib.parse.quote(urllib.parse.unquote(title_part), safe='()')}"
            )
            if absolute in seen:
                continue
            seen.add(absolute)
            links.append(absolute)
    return links


def _is_boilerplate(text: str) -> bool:
    if len(text) < 20:
        return True
    for pattern in _BOILERPLATE_RES:
        if pattern.search(text):
            return True
    # Language-link lists: many middle dots or script switches.
    if text.count(" · ") >= 5 or text.count("·") >= 8:
        return True
    return False
