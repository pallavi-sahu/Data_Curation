"""Breadth-first crawl of same-site links for non-Wikipedia URLs."""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

from curation.extractors.wikipedia import is_wikipedia_url

logger = logging.getLogger(__name__)

_USER_AGENT = "data-curation-pipeline/1.0 (research use)"

_last_request_at: float = 0.0
_request_interval: float = 0.0

_SKIP_SCHEMES = {"mailto", "tel", "javascript", "data"}
_SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".mp3",
    ".mp4",
    ".xml",
    ".rss",
}
_SKIP_PATH_RE = re.compile(
    r"(?:^|/)(?:login|logout|signup|register|search|cart|checkout|"
    r"wp-admin|wp-login|feed|rss|sitemap|privacy|terms|contact-us|"
    r"subscribe|newsletter|author|tag|tags|category)(?:/|$)",
    re.IGNORECASE,
)
_SKIP_HOST_SUFFIXES = (
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "linkedin.com",
    "pinterest.com",
    "whatsapp.com",
)


def configure_web_crawl_rate_limit(*, min_interval: float = 0.0) -> None:
    """Set minimum spacing between HTTP requests during generic web crawls."""
    global _request_interval
    _request_interval = max(0.0, min_interval)


def crawl_web_urls(
    seed_urls: list[str],
    *,
    crawl_depth: int = 0,
    max_pages: int = 150,
    links_per_page: int = 40,
    timeout: int = 30,
) -> list[str]:
    """
    Breadth-first crawl of same-site HTTP(S) links starting from seed pages.

    Only follows links on the same host as each seed (``www.`` ignored).
    Wikipedia URLs in the seed list are skipped here (handled separately).
    """
    seeds = [
        u.strip()
        for u in seed_urls
        if u.strip() and not is_wikipedia_url(u.strip())
    ]
    if crawl_depth <= 0 or not seeds:
        return _dedupe_urls(seeds)

    unlimited_pages = max_pages <= 0
    ordered: list[str] = []
    seen: set[str] = set()
    current_level = _dedupe_urls(seeds)
    seed_hosts = {_netloc(url) for url in current_level}

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

            host = _netloc(url)
            if host not in seed_hosts:
                continue

            try:
                discovered = discover_page_links(
                    url,
                    allowed_hosts=seed_hosts,
                    max_links=links_per_page,
                    timeout=timeout,
                )
            except Exception:
                logger.exception(
                    "Failed discovering links at depth %s from %s", depth, url
                )
                continue

            logger.info(
                "Web crawl depth %s: found %s links from %s",
                depth,
                len(discovered),
                url,
            )

            pending_norms = {_normalize_url(x) for x in next_level}
            for link in discovered:
                norm = _normalize_url(link)
                if norm in seen or norm in pending_norms:
                    continue
                if not unlimited_pages and len(ordered) + len(next_level) >= max_pages:
                    break
                next_level.append(link)
                pending_norms.add(norm)

        if not next_level:
            break
        current_level = _dedupe_urls(next_level)

    if unlimited_pages:
        return ordered
    return ordered[:max_pages]


def discover_page_links(
    url: str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    max_links: int = 40,
    timeout: int = 30,
) -> list[str]:
    """Discover followable same-site links from a page's main content area."""
    from bs4 import BeautifulSoup

    html = _fetch_html(url, timeout=timeout)
    if not html:
        return []

    if allowed_hosts:
        hosts = set()
        for host in allowed_hosts:
            if "://" in host:
                hosts.add(_netloc(host))
            else:
                hosts.add(host.lower().removeprefix("www."))
    else:
        hosts = {_netloc(url)}
    soup = BeautifulSoup(html, "lxml")

    for selector in (
        "nav",
        "header",
        "footer",
        "aside",
        ".nav",
        ".navbar",
        ".menu",
        ".sidebar",
        ".footer",
        ".header",
        "#header",
        "#footer",
        "#nav",
    ):
        for element in soup.select(selector):
            element.decompose()

    root = (
        soup.find("article")
        or soup.find("main")
        or soup.select_one("[role='main']")
        or soup.body
        or soup
    )

    links: list[str] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        absolute = urllib.parse.urljoin(url, anchor["href"].strip())
        normalized = _normalize_url(absolute)
        if normalized in seen:
            continue
        if not _is_followable(absolute, hosts):
            continue
        seen.add(normalized)
        links.append(absolute)
        if max_links > 0 and len(links) >= max_links:
            break

    return links


def _fetch_html(url: str, *, timeout: int) -> str:
    _throttle_request()
    request_url = _encode_request_url(url)
    request = urllib.request.Request(request_url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        logger.warning("Failed to download page for crawl: %s (%s)", url, exc)
        return ""
    return raw.decode("utf-8", errors="replace")


def _encode_request_url(url: str) -> str:
    """Percent-encode non-ASCII path segments for urllib (HTTP/1.1 requires ASCII)."""
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parsed.path, safe="/%:@")
    query = urllib.parse.quote(parsed.query, safe="=&%:@?")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, query, parsed.fragment)
    )


def _throttle_request() -> None:
    global _last_request_at
    if _request_interval <= 0:
        return
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _request_interval:
        time.sleep(_request_interval - elapsed)
    _last_request_at = time.monotonic()


def _is_followable(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if _netloc(url) not in allowed_hosts:
        return False
    if parsed.scheme in _SKIP_SCHEMES:
        return False

    host = parsed.netloc.lower().removeprefix("www.")
    if any(host == suffix or host.endswith("." + suffix) for suffix in _SKIP_HOST_SUFFIXES):
        return False

    path = parsed.path or "/"
    lower_path = path.lower()
    if any(lower_path.endswith(ext) for ext in _SKIP_EXTENSIONS):
        return False
    if _SKIP_PATH_RE.search(lower_path):
        return False
    if parsed.fragment and not parsed.path:
        return False
    return True


def _netloc(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, "", parsed.query, "")
    )


def _dedupe_urls(urls: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return ordered
