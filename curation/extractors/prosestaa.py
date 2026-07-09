"""Extract articles from prosestaa.wordpress.com (WordPress category archives + posts)."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST = "prosestaa.wordpress.com"

_CATEGORY_PATH_RE = re.compile(r"/category/([^/?#]+)", re.IGNORECASE)
_DATE_POST_PATH_RE = re.compile(
    r"^/\d{4}/\d{2}/\d{2}/[^/]+/?$",
    re.IGNORECASE,
)

_SKIP_PATH_MARKERS = (
    "/wp-content/",
    "/wp-admin/",
    "/wp-login",
    "/xmlrpc.php",
    "/feed/",
    "/tag/",
    "/author/",
)

_STATIC_SLUGS = frozenset(
    {
        "about",
        "contact",
        "privacy",
        "disclaimer",
        "home",
        "index.php",
    }
)

_JUNK_LINE_RES = [
    re.compile(r"^read\s+more\s*$", re.I),
    re.compile(r"^continue\s+reading", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^share\s*:", re.I),
    re.compile(r"^leave a comment", re.I),
    re.compile(r"^post a comment", re.I),
    re.compile(r"^previous\s*$", re.I),
    re.compile(r"^next\s*$", re.I),
]


def is_prosestaa_url(url: str) -> bool:
    host = urllib_parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host == _HOST or host.endswith("." + _HOST)


def is_category_url(url: str) -> bool:
    if not is_prosestaa_url(url):
        return False
    return bool(_CATEGORY_PATH_RE.search(urllib_parse.urlparse(url).path or ""))


def is_article_url(url: str) -> bool:
    """True for single WordPress posts (date permalinks or story slugs)."""
    if not is_prosestaa_url(url):
        return False
    path = (urllib_parse.urlparse(url.strip()).path or "/").lower()
    if any(marker in path for marker in _SKIP_PATH_MARKERS):
        return False
    if is_category_url(url):
        return False
    if _DATE_POST_PATH_RE.match(path):
        return True
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 1:
        return False
    slug = urllib_parse.unquote(parts[0]).lower()
    if slug in _STATIC_SLUGS:
        return False
    if slug.endswith((".jpg", ".jpeg", ".png", ".gif", ".pdf", ".xml")):
        return False
    return True


def crawl_prosestaa_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Expand prosestaa.wordpress.com seeds to article URLs.

    Category archives paginate and collect post links from listing cards
    (``Continue reading`` / entry titles). ``crawl_depth`` >= 1 is required
    to expand category archives.
    """
    seeds = [u.strip() for u in seed_urls if u.strip() and is_prosestaa_url(u)]
    if not seeds:
        return []

    if crawl_depth <= 0:
        return _dedupe_article_urls(seeds, max_pages=max_pages)

    ordered: list[str] = []
    seen: set[str] = set()

    for seed in seeds:
        if is_category_url(seed):
            targets = discover_category_articles(seed, timeout=timeout)
        elif is_article_url(seed):
            targets = [seed]
        else:
            targets = discover_category_articles(seed, timeout=timeout) or [seed]

        for url in targets:
            key = _normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(url)
            if max_pages > 0 and len(ordered) >= max_pages:
                return ordered

    if crawl_depth <= 1:
        return ordered

    current_level = list(ordered)
    seen = {_normalize_url(u) for u in ordered}

    for depth in range(1, crawl_depth):
        if max_pages > 0 and len(ordered) >= max_pages:
            break
        next_level: list[str] = []
        for page_url in current_level:
            try:
                discovered = discover_post_links(page_url, timeout=timeout)
            except Exception:
                logger.exception(
                    "Failed discovering prosestaa links at depth %s from %s",
                    depth,
                    page_url,
                )
                continue

            if links_per_page > 0:
                discovered = discovered[:links_per_page]

            for link in discovered:
                norm = _normalize_url(link)
                if norm in seen:
                    continue
                seen.add(norm)
                next_level.append(link)
                ordered.append(link)
                if max_pages > 0 and len(ordered) >= max_pages:
                    return ordered

        if not next_level:
            break
        current_level = next_level

    return ordered


def discover_category_articles(
    category_url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Walk all pages of a WordPress category archive and collect post URLs."""
    base_category = _category_base(category_url)
    if not base_category:
        return []

    queue: list[str] = [_normalize_category_page(category_url)]
    visited_pages: set[str] = set()
    articles: list[str] = []
    seen_articles: set[str] = set()

    while queue:
        page_url = queue.pop(0)
        page_key = _normalize_url(page_url)
        if page_key in visited_pages:
            continue
        visited_pages.add(page_key)

        html = _fetch_html(page_url, timeout=timeout)
        if not html:
            continue

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        for post_url in _article_links_from_listing(soup, page_url):
            key = _normalize_url(post_url)
            if key in seen_articles:
                continue
            seen_articles.add(key)
            articles.append(post_url)

        for anchor in soup.find_all("a", href=True):
            absolute = urllib_parse.urljoin(page_url, anchor["href"].strip())
            if not _is_same_category_page(absolute, base_category):
                continue
            norm = _normalize_url(absolute)
            if norm in visited_pages or norm in {_normalize_url(x) for x in queue}:
                continue
            queue.append(absolute)

        logger.info(
            "prosestaa category %s: %s article(s) so far, %s listing page(s) seen",
            base_category,
            len(articles),
            len(visited_pages),
        )

    return articles


def _article_links_from_listing(soup, page_url: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for block in soup.find_all("article"):
        link = _read_more_link(block, page_url)
        if not link:
            continue
        key = _normalize_url(link)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(link)

    if ordered:
        return ordered

    for anchor in soup.select("h2.entry-title a[href], a.more-link[href]"):
        absolute = urllib_parse.urljoin(page_url, anchor["href"].strip())
        if not is_article_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)

    return ordered


def _read_more_link(block, page_url: str) -> str | None:
    for anchor in block.find_all("a", href=True):
        label = anchor.get_text(" ", strip=True).lower()
        if "continue reading" in label or "read more" in label:
            absolute = urllib_parse.urljoin(page_url, anchor["href"].strip())
            if is_article_url(absolute):
                return absolute

    for selector in (".entry-title a[href]", "h2 a[href]", "a.more-link[href]"):
        anchor = block.select_one(selector)
        if not anchor or not anchor.get("href"):
            continue
        absolute = urllib_parse.urljoin(page_url, anchor["href"].strip())
        if is_article_url(absolute):
            return absolute
    return None


def discover_post_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Discover followable post links inside an article body."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".entry-content") or soup.select_one("article")
    if not root:
        return []

    ordered: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_article_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)
    return ordered


def extract_article_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """Download a prosestaa post and return combined body paragraphs."""
    from bs4 import BeautifulSoup

    if is_category_url(url):
        logger.warning(
            "Category archive has excerpts only; crawl with depth >= 1: %s",
            url,
        )
        return ""

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".entry-content") or soup.select_one("article .entry-content")
    if not root:
        logger.warning("No entry content on prosestaa page: %s", url)
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


def _category_base(category_url: str) -> str | None:
    match = _CATEGORY_PATH_RE.search(urllib_parse.urlparse(category_url).path or "")
    if not match:
        return None
    slug = match.group(1).lower()
    return f"/category/{slug}"


def _is_same_category_page(url: str, base_category: str) -> bool:
    if not is_prosestaa_url(url):
        return False
    path = urllib_parse.urlparse(url).path or ""
    normalized = path.rstrip("/") or "/"
    if normalized == base_category.rstrip("/"):
        return True
    return normalized.startswith(base_category.rstrip("/") + "/page/")


def _normalize_category_page(url: str) -> str:
    parsed = urllib_parse.urlparse(url.strip())
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urllib_parse.urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def _dedupe_article_urls(urls: list[str], *, max_pages: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if not is_article_url(url) and not is_category_url(url):
            continue
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
        if max_pages > 0 and len(ordered) >= max_pages:
            break
    return ordered


def _trim_trailing_promo(root) -> None:
    for selector in (
        ".sharedaddy",
        ".jp-relatedposts",
        ".related-posts",
        ".post-navigation",
        ".comments-area",
    ):
        for element in root.select(selector):
            element.decompose()


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
