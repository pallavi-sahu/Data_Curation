"""Extract Gujarati essays from saralgujarati.in (Blogger post pages)."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST = "saralgujarati.in"

_POST_PATH_RE = re.compile(
    r"/20\d{2}/(?:0?[1-9]|1[0-2])/.+\.html?$",
    re.IGNORECASE,
)

_SKIP_PATH_MARKERS = (
    "/p/",
    "/search/",
    "/feeds/",
    "/label/",
    "javascript:",
)

_JUNK_LINE_RES = [
    re.compile(r"^--\s*સમાપ્ત\s*--$"),
    re.compile(r"^\*{2,}$"),
    re.compile(r"^read\s+more\s*:?\s*$", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^share\s*:", re.I),
    re.compile(r"^getting info", re.I),
    re.compile(r"^post a comment", re.I),
    re.compile(r"^copy\s*$", re.I),
    re.compile(r"^join the conversation", re.I),
]

_INDEX_LINK_THRESHOLD = 10


def is_saralgujarati_url(url: str) -> bool:
    host = urllib_parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host == _HOST or host.endswith("." + _HOST)


def is_essay_post_url(url: str) -> bool:
    if not is_saralgujarati_url(url):
        return False
    path = (urllib_parse.urlparse(url.strip()).path or "/").lower()
    if any(marker in path for marker in _SKIP_PATH_MARKERS):
        return False
    return bool(_POST_PATH_RE.search(path))


def discover_essay_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Collect essay post URLs linked from a saralgujarati index/list page."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = (
        soup.select_one(".post-body")
        or soup.select_one(".entry-content")
        or soup.find("article")
        or soup.body
    )
    if not root:
        return []

    seed_key = _normalize_url(url)
    ordered: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_essay_post_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen or key == seed_key:
            continue
        seen.add(key)
        ordered.append(absolute)

    return ordered


def crawl_saralgujarati_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Breadth-first crawl of saralgujarati.in essay post links.

    Index pages such as ``gujarati-essay.html`` list 100+ essay URLs in
    ``.post-body``; those are expanded instead of scraping the index intro only.
    """
    seeds = [
        u.strip()
        for u in seed_urls
        if u.strip() and is_saralgujarati_url(u.strip())
    ]
    if not seeds:
        return []

    if crawl_depth <= 0:
        return _dedupe_essay_urls(seeds, max_pages=max_pages)

    unlimited_pages = max_pages <= 0
    ordered: list[str] = []
    seen: set[str] = set()
    current_level = _initial_crawl_frontier(seeds, timeout=timeout)

    for depth in range(crawl_depth + 1):
        next_level: list[str] = []
        for page_url in current_level:
            if not unlimited_pages and len(ordered) >= max_pages:
                break
            normalized = _normalize_url(page_url)
            if normalized not in seen:
                seen.add(normalized)
                ordered.append(page_url)

            if depth >= crawl_depth or (
                not unlimited_pages and len(ordered) >= max_pages
            ):
                continue

            try:
                discovered = discover_essay_links(page_url, timeout=timeout)
            except Exception:
                logger.exception(
                    "Failed discovering essay links at depth %s from %s",
                    depth,
                    page_url,
                )
                continue

            if links_per_page > 0:
                discovered = discovered[:links_per_page]

            logger.info(
                "saralgujarati depth %s: %s essay link(s) from %s",
                depth,
                len(discovered),
                page_url,
            )

            pending = {_normalize_url(x) for x in next_level}
            for link in discovered:
                norm = _normalize_url(link)
                if norm in seen or norm in pending:
                    continue
                if not unlimited_pages and len(ordered) + len(next_level) >= max_pages:
                    break
                next_level.append(link)
                pending.add(norm)

        if not next_level:
            break
        current_level = _dedupe_essay_urls(next_level, max_pages=0)

    if unlimited_pages:
        return ordered
    return ordered[:max_pages]


def extract_essays_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """Download a post page and return combined body paragraphs."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".post-body") or soup.select_one(".entry-content")
    if not root:
        logger.warning("No post body on saralgujarati page: %s", url)
        return ""

    _trim_trailing_promo(root)

    paragraphs: list[str] = []
    for element in root.find_all(["p", "li"]):
        if element.find_parent(["table", "nav", "footer", "aside"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if not text or _is_junk_line(text):
            continue
        if len(text.split()) < 6:
            continue
        paragraphs.append(text)

    return "\n\n".join(paragraphs).strip() if paragraphs else ""


def _initial_crawl_frontier(
    seeds: list[str],
    *,
    timeout: int,
) -> list[str]:
    frontier: list[str] = []
    for seed in seeds:
        discovered = discover_essay_links(seed, timeout=timeout)
        if len(discovered) >= _INDEX_LINK_THRESHOLD:
            frontier.extend(discovered)
        elif is_essay_post_url(seed):
            frontier.append(seed)
        elif discovered:
            frontier.extend(discovered)
        else:
            frontier.append(seed)
    return _dedupe_essay_urls(frontier, max_pages=0)


def _dedupe_essay_urls(urls: list[str], *, max_pages: int) -> list[str]:
    ordered = filter_essay_urls(urls)
    if max_pages > 0:
        return ordered[:max_pages]
    return ordered


def filter_essay_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if not is_essay_post_url(url):
            continue
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return ordered


def _trim_trailing_promo(root) -> None:
    for heading in root.find_all(["h2", "h3", "h4", "h5", "h6"]):
        label = heading.get_text(" ", strip=True).lower()
        if label.startswith("read more") or label.startswith("popular posts"):
            for sibling in list(heading.find_all_next()):
                sibling.decompose()
            heading.decompose()
            break


def _is_junk_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _JUNK_LINE_RES)


def _normalize_url(url: str) -> str:
    parsed = urllib_parse.urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib_parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )
