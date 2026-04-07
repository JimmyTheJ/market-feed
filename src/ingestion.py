"""Content ingestion from RSS feeds and APIs."""

import logging
from pathlib import Path

import feedparser
import httpx
import yaml
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_SOURCES_PATH = "config/sources.yaml"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "MarketPipeline/1.0 (+https://github.com/market-pipeline)"


def load_sources(path: str | Path = DEFAULT_SOURCES_PATH) -> dict:
    """Load source configuration."""
    path = Path(path)
    if not path.exists():
        logger.warning(f"Sources config not found: {path}")
        return {}

    with open(path) as f:
        data = yaml.safe_load(f)

    return data or {}


def fetch_rss_feed(url: str, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch and parse an RSS feed, returning raw article dicts."""
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)

        articles = []
        for entry in feed.entries:
            published = _parse_published(entry)

            content = ""
            if hasattr(entry, "summary"):
                content = entry.summary
            elif hasattr(entry, "content") and entry.content:
                content = entry.content[0].get("value", "")

            articles.append(
                {
                    "title": getattr(entry, "title", ""),
                    "url": getattr(entry, "link", ""),
                    "published_at": published,
                    "content": content,
                    "source_url": url,
                }
            )

        logger.info(f"Fetched {len(articles)} articles from {url}")
        return articles

    except Exception as e:
        logger.error(f"Failed to fetch RSS feed {url}: {e}")
        return []


def _parse_published(entry) -> datetime | None:
    """Parse the published date from a feed entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_all_sources(sources: dict, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Fetch content from all configured sources."""
    all_articles = []

    for feed_config in sources.get("rss", []):
        if not feed_config.get("enabled", True):
            continue

        url = feed_config.get("url", "")
        if not url:
            continue

        articles = fetch_rss_feed(url, timeout=timeout)
        for article in articles:
            article["source_name"] = feed_config.get("name", "unknown")
            article["category"] = feed_config.get("category", "general")
            article["priority"] = feed_config.get("priority", 5)

        all_articles.extend(articles)

    logger.info(f"Total articles fetched: {len(all_articles)}")
    return all_articles
