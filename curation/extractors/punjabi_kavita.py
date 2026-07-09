"""Extract Punjabi stories from punjabi-kavita.com / punjabikahani.punjabi-kavita.com."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST_SUFFIXES = (
    "punjabi-kavita.com",
    "punjabikahani.punjabi-kavita.com",
)

# Site navigation / hub pages — not story text.
_SKIP_PHP = frozenset(
    {
        "punjabi-stories.php",
        "punjabistories.php",
        "childrenstories.php",
        "lokkahanian.php",
        "punjabinovels.php",
        "punjabiplays.php",
        "punjabialochana.php",
        "pkcontactus.php",
        "index.php",
        "parikahanian.php",
        "alif-laila.php",
        "panchtantra.php",
        "aesop-fables.php",
        "indianlanguagesstories.php",
    }
)

_LISTING_HUB_PHP = frozenset(
    {
        "childrenstories.php",
        "lokkahanian.php",
        "punjabinovels.php",
        "aesop-fables.php",
        "alif-laila.php",
        "parikahanian.php",
        "panchtantra.php",
    }
)

_JUNK_LINE_RES = [
    re.compile(r"^contact us", re.I),
    re.compile(r"^home\s*$", re.I),
    re.compile(r"^punjabi stories\s*$", re.I),
]


def is_punjabi_kavita_url(url: str) -> bool:
    host = urllib_parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return any(host == suffix or host.endswith("." + suffix) for suffix in _HOST_SUFFIXES)


def is_story_index_url(url: str) -> bool:
    if not is_punjabi_kavita_url(url):
        return False
    name = _php_name(url)
    return name in {"punjabi-stories.php", "punjabistories.php"}


def is_story_url(url: str) -> bool:
    if not is_punjabi_kavita_url(url):
        return False
    name = _php_name(url)
    if not name:
        return False
    return name not in _SKIP_PHP


def crawl_punjabi_kavita_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 2,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Expand punjabi-kavita story hub pages to individual story URLs.

    ``Punjabi-Stories.php`` lists ~300 author/category ``.php`` links. Each
    author page lists story ``.php`` links. Generic web crawl only follows the
    first 40 links per page and misses most content.

    - ``crawl_depth`` 0: seed URL(s) only.
    - ``crawl_depth`` 1: index -> author/category pages (no stories yet).
    - ``crawl_depth`` 2+ (default): index -> authors -> all story pages.
    """
    seeds = [u.strip() for u in seed_urls if u.strip() and is_punjabi_kavita_url(u)]
    if not seeds:
        return []

    if crawl_depth <= 0:
        return _cap(_dedupe([s for s in seeds if is_story_url(s)]), max_pages)

    listing_pages: list[str] = []
    story_pages: list[str] = []
    seen: set[str] = set()

    for seed in seeds:
        if is_story_index_url(seed) or _is_listing_hub(seed):
            for link in discover_listing_links(seed, timeout=timeout):
                key = _normalize_url(link)
                if key not in seen:
                    seen.add(key)
                    listing_pages.append(link)
        elif is_story_url(seed):
            key = _normalize_url(seed)
            if key not in seen:
                seen.add(key)
                story_pages.append(seed)
        else:
            key = _normalize_url(seed)
            if key not in seen:
                seen.add(key)
                listing_pages.append(seed)

    if crawl_depth < 2 and not story_pages:
        return _cap(_dedupe(listing_pages), max_pages)

    targets: list[str] = list(story_pages)
    seen_stories = {_normalize_url(u) for u in story_pages}

    for listing in listing_pages:
        stories = discover_story_links(
            listing,
            timeout=timeout,
            max_links=links_per_page,
        )
        logger.info(
            "punjabi-kavita: %s -> %s story link(s)",
            _php_name(listing),
            len(stories),
        )
        for story in stories:
            key = _normalize_url(story)
            if key in seen_stories:
                continue
            seen_stories.add(key)
            targets.append(story)
            if max_pages > 0 and len(targets) >= max_pages:
                return targets

    return _cap(_dedupe(targets), max_pages)


def discover_listing_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Author/category ``.php`` links from ``Punjabi-Stories.php`` or similar hubs."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = _content_root(soup)
    ordered: list[str] = []
    seen: set[str] = set()

    for anchor in root.find_all("a", href=True):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_story_url(absolute):
            continue
        if is_story_index_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)

    return ordered


def discover_story_links(
    url: str,
    *,
    timeout: int = 30,
    max_links: int = 0,
) -> list[str]:
    """Story ``.php`` links from an author or category listing page."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = _content_root(soup)
    current = _normalize_url(url)
    ordered: list[str] = []
    seen: set[str] = set()

    for anchor in root.find_all("a", href=True):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_story_url(absolute):
            continue
        if is_story_index_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key == current or key in seen:
            continue
        label = anchor.get_text(" ", strip=True)
        if not label and not _looks_like_story_slug(_php_name(absolute)):
            continue
        if label.lower() in {"home", "punjabi stories", "contact us..."}:
            continue
        seen.add(key)
        ordered.append(absolute)
        if max_links > 0 and len(ordered) >= max_links:
            break

    return ordered


def extract_story_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """Download a story page and return body paragraphs from ``div.content``."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = _content_root(soup)
    if not root:
        logger.warning("No content root on punjabi-kavita page: %s", url)
        return ""

    paragraphs: list[str] = []
    for element in root.find_all(["p", "blockquote"]):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if not text or _is_junk_line(text):
            continue
        if len(text.split()) < 4:
            continue
        paragraphs.append(text)

    if paragraphs:
        return "\n\n".join(paragraphs).strip()

    raw = re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()
    if not raw:
        return ""

    blocks = re.split(r"\n\s*\n+", raw)
    kept = [b.strip() for b in blocks if len(b.split()) >= 8 and not _is_junk_line(b)]
    return "\n\n".join(kept).strip()


def _content_root(soup):
    return (
        soup.select_one("div.contentt")
        or soup.select_one("div.content")
        or soup.select_one("div.full")
        or soup.body
    )


def _is_listing_hub(url: str) -> bool:
    name = _php_name(url)
    return name in _LISTING_HUB_PHP


def _looks_like_story_slug(name: str) -> bool:
    if not name:
        return False
    stem = name.removesuffix(".php")
    return "-" in stem and len(stem) > 12


def _php_name(url: str) -> str:
    path = urllib_parse.urlparse(url.strip()).path or ""
    if not path.lower().endswith(".php"):
        return ""
    return path.rsplit("/", 1)[-1].lower()


def _is_junk_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _JUNK_LINE_RES)


def _dedupe(urls: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return ordered


def _cap(urls: list[str], max_pages: int) -> list[str]:
    if max_pages > 0:
        return urls[:max_pages]
    return urls


def _normalize_url(url: str) -> str:
    parsed = urllib_parse.urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib_parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )
