"""Extract Marathi essays from bhashanmarathi.com (Blogger post pages)."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST = "bhashanmarathi.com"

# Blog post URLs: /2020/10/slug.html (year/month, no day segment)
_ESSAY_POST_RE = re.compile(
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
    re.compile(r"^--\s*समाप्त\s*--$"),
    re.compile(r"^\*{2,}$"),
    re.compile(r"^read\s+more\s*:?\s*$", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^share\s*:", re.I),
    re.compile(r"^facebook$", re.I),
    re.compile(r"^twitter$", re.I),
    re.compile(r"^advertisement\s*$", re.I),
    re.compile(r"^comments?\s*$", re.I),
    re.compile(r"^टिप्पणी\s", re.I),
    re.compile(r"^\d{1,2}\s+\w+,\s+20\d{2}\s+रोजी", re.I),
]


def is_bhashan_marathi_url(url: str) -> bool:
    """True for http(s) URLs on bhashanmarathi.com."""
    host = urllib_parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host == _HOST or host.endswith("." + _HOST)


def is_essay_post_url(url: str) -> bool:
    """True for individual Blogger essay posts (not index/about/search pages)."""
    if not is_bhashan_marathi_url(url):
        return False
    parsed = urllib_parse.urlparse(url.strip())
    path = (parsed.path or "/").lower()
    if any(marker in path for marker in _SKIP_PATH_MARKERS):
        return False
    return bool(_ESSAY_POST_RE.search(path))


def is_essay_index_url(url: str) -> bool:
    """True for list/index pages that link out to essay posts."""
    if not is_bhashan_marathi_url(url):
        return False
    return not is_essay_post_url(url)


def discover_essay_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Collect essay post URLs linked from a bhashanmarathi index/list page."""
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

    ordered: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_essay_post_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)

    return ordered


_INDEX_LINK_THRESHOLD = 10


def crawl_bhashan_essay_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Breadth-first crawl of bhashanmarathi.com essay post links.

    ``crawl_depth`` controls how many link hops to follow (same idea as
    ``--wiki-crawl-depth``):

    - ``0``: seed URL(s) only (no link following).
    - ``1``: essay links on the seed page (index -> all listed essays).
    - ``2+``: also follow essay links found on each essay page (e.g. READ MORE).

    Index pages with many outbound links expand to those essay URLs; the index
    itself is omitted from the crawl list. Text from different pages is never
    merged — each URL is extracted separately downstream.
    """
    seeds = [
        u.strip()
        for u in seed_urls
        if u.strip() and is_bhashan_marathi_url(u.strip())
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
        for url in current_level:
            if not unlimited_pages and len(ordered) >= max_pages:
                break
            normalized = _normalize_url(url)
            if normalized not in seen:
                seen.add(normalized)
                ordered.append(url)

            if depth >= crawl_depth or (
                not unlimited_pages and len(ordered) >= max_pages
            ):
                continue

            try:
                discovered = discover_essay_links(
                    url,
                    timeout=timeout,
                )
            except Exception:
                logger.exception(
                    "Failed discovering essay links at depth %s from %s",
                    depth,
                    url,
                )
                continue

            if links_per_page > 0:
                discovered = discovered[:links_per_page]

            logger.info(
                "bhashanmarathi depth %s: %s essay link(s) from %s",
                depth,
                len(discovered),
                url,
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


def _initial_crawl_frontier(
    seeds: list[str],
    *,
    timeout: int,
) -> list[str]:
    """Starting URLs for depth >= 1 (expand index pages to their essay links)."""
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


def resolve_essay_targets(
    seed: str,
    *,
    timeout: int = 30,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
) -> list[str]:
    """Return essay post URLs to extract for a seed (depth-1 crawl shorthand)."""
    return crawl_bhashan_essay_urls(
        [seed],
        crawl_depth=crawl_depth,
        max_pages=max_pages,
        links_per_page=links_per_page,
        timeout=timeout,
    )


def filter_essay_urls(urls: list[str]) -> list[str]:
    """Keep essay post URLs in stable order."""
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


def extract_essays_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """
    Download an essay page and return combined body text.

    Pages often contain several essays on the same topic (300/400-word versions,
    10-line variants, etc.). All essay paragraphs on the page are combined into
    one body, separated by blank lines.
    """
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".post-body") or soup.select_one(".entry-content")
    if not root:
        logger.warning("No post body on bhashanmarathi page: %s", url)
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

    if not paragraphs:
        return ""

    return "\n\n".join(paragraphs).strip()


def _trim_trailing_promo(root) -> None:
    """Drop READ MORE blocks and related-article link lists at the end."""
    for heading in root.find_all(["h2", "h3", "h4", "h5", "h6"]):
        label = heading.get_text(" ", strip=True).lower()
        if label.startswith("read more"):
            for sibling in list(heading.find_all_next()):
                sibling.decompose()
            heading.decompose()
            break


def _is_junk_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _JUNK_LINE_RES:
        if pattern.search(stripped):
            return True
    return False


def _normalize_url(url: str) -> str:
    parsed = urllib_parse.urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib_parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )
