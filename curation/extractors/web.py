"""Extract main body text from web URLs (navigation, sidebars, captions excluded)."""

from __future__ import annotations

import logging

import trafilatura

from curation.extractors.bhashan_marathi import (
    extract_essays_from_url,
    is_bhashan_marathi_url,
)
from curation.extractors.hindivyakran import (
    extract_post_from_url,
    is_hindivyakran_url,
)
from curation.extractors.saralgujarati import (
    extract_essays_from_url as extract_saralgujarati_from_url,
    is_saralgujarati_url,
)
from curation.extractors.punjabi_kavita import (
    extract_story_from_url as extract_punjabi_kavita_from_url,
    is_punjabi_kavita_url,
)
from curation.extractors.punjabilibrary import (
    extract_article_from_url as extract_punjabilibrary_from_url,
    is_punjabilibrary_url,
)
from curation.extractors.ninemood import (
    extract_article_from_url as extract_ninemood_from_url,
    is_ninemood_url,
)
from curation.extractors.prosestaa import (
    extract_article_from_url as extract_prosestaa_from_url,
    is_prosestaa_url,
)
from curation.extractors.townparle import (
    extract_article_from_url as extract_townparle_from_url,
    is_townparle_url,
)
from curation.extractors.html import extract_text_from_html_url, is_html_url
from curation.extractors.wikipedia import extract_text_from_wikipedia, is_wikipedia_url

logger = logging.getLogger(__name__)


def extract_text_from_url(
    url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
) -> str:
    """
    Download a URL and return main article/body text only.

    Wikipedia URLs use the MediaWiki API for accurate article-body extraction.
    URLs ending in ``.html`` / ``.htm`` use the HTML extractor (Trafilatura).
    Other URLs use Trafilatura (menus, sidebars, and image blocks excluded).
    """
    if is_wikipedia_url(url):
        logger.info("Using Wikipedia API extractor for %s", url)
        return extract_text_from_wikipedia(url, timeout=timeout)

    if is_bhashan_marathi_url(url):
        logger.info("Using bhashanmarathi essay extractor for %s", url)
        return extract_essays_from_url(url, timeout=timeout)

    if is_hindivyakran_url(url):
        logger.info("Using hindivyakran post extractor for %s", url)
        return extract_post_from_url(url, timeout=timeout)

    if is_saralgujarati_url(url):
        logger.info("Using saralgujarati essay extractor for %s", url)
        return extract_saralgujarati_from_url(url, timeout=timeout)

    if is_townparle_url(url):
        logger.info("Using townparle article extractor for %s", url)
        return extract_townparle_from_url(url, timeout=timeout)

    if is_prosestaa_url(url):
        logger.info("Using prosestaa article extractor for %s", url)
        return extract_prosestaa_from_url(url, timeout=timeout)

    if is_ninemood_url(url):
        logger.info("Using 9mood article extractor for %s", url)
        return extract_ninemood_from_url(url, timeout=timeout)

    if is_punjabilibrary_url(url):
        logger.info("Using punjabilibrary article extractor for %s", url)
        return extract_punjabilibrary_from_url(url, timeout=timeout)

    if is_punjabi_kavita_url(url):
        logger.info("Using punjabi-kavita story extractor for %s", url)
        return extract_punjabi_kavita_from_url(url, timeout=timeout)

    if is_html_url(url):
        logger.info("Using HTML extractor for %s", url)
        return extract_text_from_html_url(url, timeout=timeout)

    downloaded = trafilatura.fetch_url(
        url,
        no_ssl=False,
        config=_build_config(timeout=timeout, user_agent=user_agent),
    )
    if not downloaded:
        logger.warning("Failed to download URL: %s", url)
        return ""

    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        include_images=False,
        include_links=False,
        favor_recall=True,
        url=url,
    )
    return (text or "").strip()


def _build_config(timeout: int, user_agent: str | None):
    """Build a trafilatura settings object for fetch timeouts."""
    from trafilatura.settings import use_config

    config = use_config()
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(timeout))
    if user_agent:
        config.set("DEFAULT", "USER_AGENT", user_agent)
    return config
