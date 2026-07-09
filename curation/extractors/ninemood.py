"""Extract Gujarati articles from 9mood.com (listing archive + posts)."""

from __future__ import annotations

import logging
import re
from urllib import parse as urllib_parse

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST = "9mood.com"
_GUJARATI_LISTING_PATH = "/gujarati-articles"

_SKIP_PATH_MARKERS = (
    "/wp-content/",
    "/wp-admin/",
    "/wp-login",
    "/xmlrpc.php",
    "/feed/",
    "/members/",
    "/reaction/",
    "/tag/",
    "/author/",
    "/r.php",
)

_STATIC_SLUGS = frozenset(
    {
        "about-us",
        "introduction",
        "contact",
        "privacy",
        "terms",
        "leaderboard",
        "newsletter",
        "support",
        "advertise-sponsorship",
        "affiliate",
        "changelog",
        "home",
        "index.php",
        "gujarati-articles",
        "community",
        "login",
        "register",
        "signup",
    }
)

_JUNK_LINE_RES = [
    re.compile(r"^share\s*tweet\s*$", re.I),
    re.compile(r"^don't be selfish", re.I),
    re.compile(r"^like loading", re.I),
    re.compile(r"^leave a reply", re.I),
    re.compile(r"^cancel reply", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^posted by\b", re.I),
    re.compile(r"^discover more from", re.I),
    re.compile(r"^subscribe\b", re.I),
    re.compile(r"^type your email", re.I),
    re.compile(r"^previous\s*post", re.I),
    re.compile(r"^next\s*post", re.I),
    re.compile(r"^what's your reaction", re.I),
    re.compile(r"^loading comments", re.I),
]

_GUJARATI_SCRIPT_RE = re.compile(r"[\u0A80-\u0AFF]")


def is_ninemood_url(url: str) -> bool:
    host = urllib_parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host == _HOST or host.endswith("." + _HOST)


def is_gujarati_listing_url(url: str) -> bool:
    if not is_ninemood_url(url):
        return False
    path = (urllib_parse.urlparse(url.strip()).path or "/").rstrip("/").lower()
    if path == _GUJARATI_LISTING_PATH:
        return True
    return path.startswith(_GUJARATI_LISTING_PATH + "/page/")


def is_article_url(url: str) -> bool:
    """True for single 9mood post pages (slug URLs)."""
    if not is_ninemood_url(url):
        return False
    parsed = urllib_parse.urlparse(url.strip())
    if parsed.query:
        return False
    if parsed.fragment:
        return False
    path = (parsed.path or "/").lower()
    if any(marker in path for marker in _SKIP_PATH_MARKERS):
        return False
    if is_gujarati_listing_url(url):
        return False
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 1:
        return False
    slug = urllib_parse.unquote(parts[0]).lower()
    if slug in _STATIC_SLUGS:
        return False
    if slug.endswith((".jpg", ".jpeg", ".png", ".gif", ".pdf", ".xml")):
        return False
    return True


def crawl_ninemood_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Expand 9mood.com seeds to Gujarati article URLs.

    The ``/gujarati-articles/`` archive is paginated and collected when
    ``crawl_depth`` >= 1. Individual post seeds are kept; depth >= 1 also
    walks the Gujarati listing when the seed looks Gujarati or is the listing.
    """
    seeds = [u.strip() for u in seed_urls if u.strip() and is_ninemood_url(u)]
    if not seeds:
        return []

    if crawl_depth <= 0:
        return _dedupe_article_urls(seeds, max_pages=max_pages)

    ordered: list[str] = []
    seen: set[str] = set()

    expand_listing = any(
        is_gujarati_listing_url(seed) or _seed_looks_gujarati(seed) for seed in seeds
    )

    if expand_listing:
        for url in discover_gujarati_articles(timeout=timeout):
            key = _normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(url)
            if max_pages > 0 and len(ordered) >= max_pages:
                return ordered

    for seed in seeds:
        if is_article_url(seed):
            key = _normalize_url(seed)
            if key not in seen:
                seen.add(key)
                ordered.append(seed)
                if max_pages > 0 and len(ordered) >= max_pages:
                    return ordered
        elif is_gujarati_listing_url(seed):
            continue
        elif crawl_depth >= 1:
            for url in discover_gujarati_articles(timeout=timeout):
                key = _normalize_url(url)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(url)
                if max_pages > 0 and len(ordered) >= max_pages:
                    return ordered

    if crawl_depth <= 1 or not ordered:
        return ordered

    current_level = list(ordered)
    seen = {_normalize_url(u) for u in ordered}

    for depth in range(1, crawl_depth):
        if max_pages > 0 and len(ordered) >= max_pages:
            break
        next_level: list[str] = []
        for page_url in current_level:
            try:
                discovered = discover_related_gujarati_links(page_url, timeout=timeout)
            except Exception:
                logger.exception(
                    "Failed discovering 9mood links at depth %s from %s",
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


def discover_gujarati_articles(
    *,
    timeout: int = 30,
) -> list[str]:
    """Walk all pages of ``/gujarati-articles/`` and collect post URLs."""
    articles: list[str] = []
    seen_articles: set[str] = set()
    page_num = 1

    while True:
        if page_num == 1:
            page_url = f"https://www.{_HOST}{_GUJARATI_LISTING_PATH}/"
        else:
            page_url = (
                f"https://www.{_HOST}{_GUJARATI_LISTING_PATH}/page/{page_num}/"
            )

        html = _fetch_html(page_url, timeout=timeout)
        if not html:
            break

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        page_articles = _article_links_from_listing(soup, page_url)
        if not page_articles:
            break

        new_on_page = 0
        for post_url in page_articles:
            key = _normalize_url(post_url)
            if key in seen_articles:
                continue
            seen_articles.add(key)
            articles.append(post_url)
            new_on_page += 1

        logger.info(
            "9mood gujarati-articles page %s: %s new, %s total",
            page_num,
            new_on_page,
            len(articles),
        )

        if new_on_page == 0:
            break
        page_num += 1

    return articles


def discover_related_gujarati_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Discover Gujarati post links from an article's related-post blocks."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    ordered: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select("a[rel='bookmark'][href], a[rel='category'][href]"):
        absolute = urllib_parse.urljoin(url, anchor["href"].strip())
        if not is_article_url(absolute):
            continue
        if not _seed_looks_gujarati(absolute):
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
    """Download a 9mood post and return combined body paragraphs."""
    from bs4 import BeautifulSoup

    if is_gujarati_listing_url(url):
        logger.warning(
            "Gujarati listing has excerpts only; crawl with depth >= 1: %s",
            url,
        )
        return ""

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = (
        soup.select_one("article .entry-content")
        or soup.select_one("article .mvp-post-content")
        or soup.select_one("article")
    )
    if not root:
        logger.warning("No article content on 9mood page: %s", url)
        return ""

    _trim_trailing_promo(root)

    paragraphs: list[str] = []
    for element in root.find_all(["p", "li"]):
        if element.find_parent(["table", "nav", "footer", "aside", "form"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if not text or _is_junk_line(text):
            continue
        if len(text.split()) < 6:
            continue
        paragraphs.append(text)

    return "\n\n".join(paragraphs).strip() if paragraphs else ""


def _article_links_from_listing(soup, page_url: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for block in soup.find_all("article"):
        anchor = block.select_one(
            "h2 a[href], h3 a[href], .mvp-post-title a[href], .jeg_post_title a[href]"
        )
        if not anchor or not anchor.get("href"):
            continue
        absolute = urllib_parse.urljoin(page_url, anchor["href"].strip())
        if not is_article_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)

    return ordered


def _seed_looks_gujarati(url: str) -> bool:
    if is_gujarati_listing_url(url):
        return True
    path = urllib_parse.urlparse(url).path or ""
    if _GUJARATI_SCRIPT_RE.search(urllib_parse.unquote(path)):
        return True
    lower = path.lower()
    return any(
        token in lower
        for token in (
            "gujarati",
            "bodhvarta",
            "varta",
            "vartao",
            "akbar-birbal",
            "chanakya",
        )
    )


def _dedupe_article_urls(urls: list[str], *, max_pages: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if not is_article_url(url) and not is_gujarati_listing_url(url):
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
        ".mvp-post-add-story",
        ".reaction-buttons",
        ".wp-block-jetpack-subscriptions",
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
        (parsed.scheme.lower(), netloc, path, "", "", "")
    )
