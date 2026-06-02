"""Text extraction from PDF files and web URLs."""

from curation.extractors.pdf import extract_text_from_pdf
from curation.extractors.web import extract_text_from_url
from curation.extractors.wikipedia import crawl_wikipedia_urls, discover_wikipedia_article_links

__all__ = [
    "crawl_wikipedia_urls",
    "discover_wikipedia_article_links",
    "extract_text_from_pdf",
    "extract_text_from_url",
]
