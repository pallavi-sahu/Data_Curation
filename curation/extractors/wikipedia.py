"""Extract article body paragraphs from Wikipedia via the MediaWiki API."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from html import unescape

logger = logging.getLogger(__name__)

_WIKI_HOST_RE = re.compile(r"^([a-z-]+)\.wikipedia\.org$", re.IGNORECASE)
_MAIN_PAGE_TITLES = frozenset(
    {
        "main page",
        "முதற்பக்கம்",
        "முதற் பக்கம்",
        "మొదటి పేజీ",
        "మొదటిపేజీ",
        "मुखपृष्ठ",
        "पृष्ठ प्रधान",
        "প্রধান পাতা",
        "page d'accueil",
        "hauptseite",
        "pagina principale",
    }
)

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

    html = _fetch_parse_html(wiki_lang, title, timeout=timeout)
    is_main = _is_main_page(title) or _html_is_main_page_layout(html)
    if is_main:
        logger.info(
            "Wikipedia main page detected (%r). Extracting featured sections "
            "and linked articles from layout tables.",
            title,
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
    Discover article links from a Wikipedia page.

    For main pages, links are extracted from ``MainPageBG`` content cells.
    For normal article pages, links are extracted from the article body.
    """
    wiki_lang, title = _parse_wikipedia_url(url)
    html = _fetch_parse_html(wiki_lang, title, timeout=timeout)

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".mw-parser-output") or soup.select_one(
        "#mw-content-text .mw-parser-output"
    )
    if root is None:
        return []

    is_main = _is_main_page(title) or _html_is_main_page_layout(html)
    if is_main:
        containers = root.select("td.MainPageBG, td#mp-left, td#mp-right")
        if not containers:
            containers = root.select("table.mainpage")
    else:
        _strip_non_content(root)
        containers = [root]

    links = _extract_article_links(containers, wiki_lang=wiki_lang)
    if max_links > 0:
        links = links[:max_links]
    return links


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


def _fetch_parse_html(lang: str, title: str, *, timeout: int) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": title,
            "prop": "text",
            "format": "json",
            "disabletoc": "1",
        }
    )
    api_url = f"https://{lang}.wikipedia.org/w/api.php?{params}"
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "data-curation-pipeline/1.0 (research use)"},
    )
    payload = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 2:
                raise
            sleep_s = 2.0 * (attempt + 1)
            logger.warning(
                "Wikipedia API throttled (429) for '%s'. Retrying in %.1fs...",
                title,
                sleep_s,
            )
            time.sleep(sleep_s)
    if payload is None:
        raise RuntimeError("Failed to fetch Wikipedia API payload")

    if "error" in payload:
        code = payload["error"].get("code", "unknown")
        info = payload["error"].get("info", "")
        raise RuntimeError(f"Wikipedia API error ({code}): {info}")

    return payload["parse"]["text"]["*"]


def _is_main_page(title: str) -> bool:
    normalized = title.strip().lower()
    if normalized in _MAIN_PAGE_TITLES:
        return True
    # Match titles that differ only by spaces (e.g. முதற்பக்கம் vs முதற் பக்கம்).
    collapsed = normalized.replace(" ", "")
    return collapsed in {t.replace(" ", "") for t in _MAIN_PAGE_TITLES}


def _html_is_main_page_layout(html: str) -> bool:
    """Detect portal/main-page layout from Wikipedia HTML structure."""
    from bs4 import BeautifulSoup

    root = BeautifulSoup(html, "lxml").select_one(".mw-parser-output")
    if root is None:
        return False
    main_cells = root.select("td.MainPageBG, td#mp-left, td#mp-right")
    return len(main_cells) >= 2


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

    for cell in root.select("td.MainPageBG, td#mp-left, td#mp-right"):
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


def _extract_article_links(containers, *, wiki_lang: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

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
