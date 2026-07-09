"""Extract posts from hindivyakran.com (Blogger; label pages use Atom JSON feeds)."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

from curation.extractors.crawl import _fetch_html

logger = logging.getLogger(__name__)

_HOST = "hindivyakran.com"
_USER_AGENT = "data-curation-pipeline/1.0 (research use)"
_FEED_PAGE_SIZE = 150

_POST_PATH_RE = re.compile(
    r"/20\d{2}/(?:0?[1-9]|1[0-2])/.+\.html?$",
    re.IGNORECASE,
)
_LABEL_PATH_RE = re.compile(r"/search/label/([^/?#]+)", re.IGNORECASE)

_JUNK_LINE_RES = [
    re.compile(r"^read\s+more\s*:?\s*$", re.I),
    re.compile(r"^tags?\s*:", re.I),
    re.compile(r"^share\s*:", re.I),
    re.compile(r"^advertisement\s*$", re.I),
    re.compile(r"^\*{2,}$"),
]


def is_hindivyakran_url(url: str) -> bool:
    host = urllib.parse.urlparse(url.strip()).netloc.lower().removeprefix("www.")
    return host == _HOST or host.endswith("." + _HOST)


def is_label_page_url(url: str) -> bool:
    if not is_hindivyakran_url(url):
        return False
    return bool(_LABEL_PATH_RE.search(urllib.parse.urlparse(url).path or ""))


def is_post_url(url: str) -> bool:
    if not is_hindivyakran_url(url):
        return False
    path = (urllib.parse.urlparse(url).path or "").lower()
    if "/search/" in path or "/p/" in path or "/feeds/" in path:
        return False
    return bool(_POST_PATH_RE.search(path))


def label_from_url(url: str) -> str | None:
    match = _LABEL_PATH_RE.search(urllib.parse.urlparse(url).path or "")
    if not match:
        return None
    return urllib.parse.unquote(match.group(1))


def crawl_hindivyakran_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 1,
    max_pages: int = 0,
    links_per_page: int = 0,
    timeout: int = 30,
) -> list[str]:
    """
    Resolve hindivyakran seed URLs to post pages to extract.

    Label pages (``/search/label/...``) expand via the Blogger Atom JSON feed
    because the HTML archive is empty until JavaScript runs. ``crawl_depth``
    only controls optional link-following from individual posts.
    """
    seeds = [u.strip() for u in seed_urls if u.strip() and is_hindivyakran_url(u)]
    if not seeds:
        return []

    ordered: list[str] = []
    seen: set[str] = set()

    for seed in seeds:
        if is_label_page_url(seed):
            label = label_from_url(seed)
            if not label:
                continue
            posts = discover_posts_from_label(
                label,
                max_pages=max_pages,
                timeout=timeout,
            )
            for url in posts:
                key = _normalize_url(url)
                if key not in seen:
                    seen.add(key)
                    ordered.append(url)
            continue

        if is_post_url(seed):
            key = _normalize_url(seed)
            if key not in seen:
                seen.add(key)
                ordered.append(seed)

    if crawl_depth <= 0 or not ordered:
        return _cap_urls(ordered, max_pages)

    unlimited = max_pages <= 0
    current_level = list(ordered)
    seen = {_normalize_url(u) for u in ordered}

    for depth in range(crawl_depth):
        if not unlimited and len(ordered) >= max_pages:
            break
        next_level: list[str] = []
        for url in current_level:
            try:
                discovered = discover_post_links(
                    url,
                    timeout=timeout,
                )
            except Exception:
                logger.exception(
                    "Failed discovering links at depth %s from %s", depth, url
                )
                continue

            if links_per_page > 0:
                discovered = discovered[:links_per_page]

            for link in discovered:
                norm = _normalize_url(link)
                if norm in seen:
                    continue
                if not unlimited and len(ordered) + len(next_level) >= max_pages:
                    break
                seen.add(norm)
                next_level.append(link)
                ordered.append(link)

        if not next_level:
            break
        current_level = next_level

    return _cap_urls(ordered, max_pages)


def discover_posts_from_label(
    label: str,
    *,
    max_pages: int = 0,
    timeout: int = 30,
) -> list[str]:
    """List post URLs for a Blogger label via ``/feeds/posts/default/-/LABEL``."""
    unlimited = max_pages <= 0
    ordered: list[str] = []
    seen: set[str] = set()
    start_index = 1

    while True:
        feed_url = (
            "https://www.hindivyakran.com/feeds/posts/default/-/"
            f"{urllib.parse.quote(label)}?alt=json"
            f"&max-results={_FEED_PAGE_SIZE}&start-index={start_index}"
        )
        try:
            entries, total = _fetch_feed_entries(feed_url, timeout=timeout)
        except Exception:
            logger.exception("Failed to fetch label feed for %s", label)
            break

        if not entries:
            break

        for entry in entries:
            post_url = _entry_post_url(entry)
            if not post_url or not is_post_url(post_url):
                continue
            key = _normalize_url(post_url)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(post_url)
            if not unlimited and len(ordered) >= max_pages:
                return ordered

        start_index += len(entries)
        if start_index > total or len(entries) < _FEED_PAGE_SIZE:
            break

    return ordered


def discover_post_links(
    url: str,
    *,
    timeout: int = 30,
) -> list[str]:
    """Discover followable post links inside a hindivyakran article body."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".post-body") or soup.select_one(".entry-content")
    if not root:
        return []

    ordered: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        absolute = urllib.parse.urljoin(url, anchor["href"].strip())
        if not is_post_url(absolute):
            continue
        key = _normalize_url(absolute)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(absolute)
    return ordered


def extract_post_from_url(
    url: str,
    *,
    timeout: int = 30,
) -> str:
    """Download a hindivyakran post and return combined body paragraphs."""
    from bs4 import BeautifulSoup

    if is_label_page_url(url):
        logger.warning(
            "Label page has no static Malayalam body; use feed crawl instead: %s",
            url,
        )
        return ""

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(".post-body") or soup.select_one(".entry-content")
    if not root:
        logger.warning("No post body on hindivyakran page: %s", url)
        return ""

    _trim_trailing_promo(root)

    paragraphs: list[str] = []
    for element in root.find_all(["p", "li"]):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if not text or _is_junk_line(text):
            continue
        if len(text.split()) < 6:
            continue
        paragraphs.append(text)

    return "\n\n".join(paragraphs).strip()


def _fetch_feed_entries(
    feed_url: str,
    *,
    timeout: int,
) -> tuple[list[dict], int]:
    request = urllib.request.Request(feed_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)

    feed = payload.get("feed", {})
    entries = feed.get("entry", [])
    if not isinstance(entries, list):
        entries = [entries] if entries else []

    total_raw = feed.get("openSearch$totalResults", {}).get("$t", "0")
    try:
        total = int(total_raw)
    except ValueError:
        total = len(entries)

    return entries, total


def _entry_post_url(entry: dict) -> str | None:
    for link in entry.get("link", []):
        if link.get("rel") == "alternate" and link.get("type") == "text/html":
            return link.get("href")
    return None


def _trim_trailing_promo(root) -> None:
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
    return any(pattern.search(stripped) for pattern in _JUNK_LINE_RES)


def _cap_urls(urls: list[str], max_pages: int) -> list[str]:
    if max_pages > 0:
        return urls[:max_pages]
    return urls


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )
