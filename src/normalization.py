"""Normalize heterogeneous source data into common article schema."""

import hashlib
import logging
import re
from difflib import SequenceMatcher
from html import unescape

from .models import NormalizedArticle

logger = logging.getLogger(__name__)

# Common financial stop words to keep during tokenization
FINANCIAL_KEEP = {
    "fed", "ecb", "boj", "sec", "etf", "gdp", "cpi", "ppi",
    "fomc", "opec", "lng", "api", "eia", "imf", "oil", "gas",
    "gold", "bond", "rate", "yield", "debt", "tax", "tariff",
}

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "to",
    "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "and", "but",
    "or", "nor", "not", "so", "yet", "both", "either", "neither",
    "each", "every", "all", "any", "few", "more", "most", "other",
    "some", "such", "no", "only", "own", "same", "than", "too",
    "very", "just", "because", "if", "when", "where", "how",
    "what", "which", "who", "whom", "this", "that", "these",
    "those", "it", "its", "he", "she", "they", "them", "their",
    "we", "us", "our", "you", "your", "my", "me", "about",
    "also", "said", "says", "new", "like", "get", "go", "going",
}


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_tokens(text: str) -> list[str]:
    """Extract lowercase word tokens from text, preserving financial terms."""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)
    return [
        w
        for w in words
        if (len(w) > 2 or w in FINANCIAL_KEEP) and w not in STOP_WORDS
    ]


def extract_entities(text: str) -> list[str]:
    """Simple entity extraction based on known financial entities and capitalized phrases."""
    entities: set[str] = set()

    # Match capitalized multi-word phrases
    for match in re.finditer(r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text):
        entities.add(match.group())

    # Known financial entities
    financial_terms = [
        "Federal Reserve", "Fed", "ECB", "BOJ", "PBOC", "BOE",
        "SEC", "CFTC", "Treasury", "Wall Street",
        "S&P", "S&P 500", "Nasdaq", "Dow Jones",
        "Bitcoin", "Ethereum", "Solana",
        "BlackRock", "Vanguard", "Fidelity",
        "OPEC", "EIA", "IMF", "World Bank",
        "Tesla", "Nvidia", "Apple", "Microsoft", "Google", "Amazon", "Meta",
    ]
    text_lower = text.lower()
    for term in financial_terms:
        if term.lower() in text_lower:
            entities.add(term)

    return sorted(entities)


def generate_article_id(source: str, title: str, url: str) -> str:
    """Generate a deterministic article ID."""
    content = f"{source}:{title}:{url}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def normalize_article(raw: dict) -> NormalizedArticle:
    """Normalize a raw article dict into a NormalizedArticle."""
    title = strip_html(raw.get("title", ""))
    content = strip_html(raw.get("content", ""))
    source = raw.get("source_name", "unknown")
    url = raw.get("url", "")

    combined_text = f"{title} {content}"
    tokens = extract_tokens(combined_text)
    entities = extract_entities(raw.get("title", "") + " " + raw.get("content", ""))

    return NormalizedArticle(
        id=generate_article_id(source, title, url),
        source=source,
        title=title,
        url=url,
        published_at=raw.get("published_at"),
        content=content,
        tokens=tokens,
        entities=entities,
        category=raw.get("category", "general"),
    )


def _is_similar_title(title_a: str, title_b: str, threshold: float = 0.85) -> bool:
    """Check if two article titles are similar enough to be duplicates."""
    a = title_a.lower().strip()
    b = title_b.lower().strip()
    if a == b:
        return True
    # Only apply fuzzy matching on titles with enough text to be meaningful
    if len(a) < 20 or len(b) < 20:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def normalize_all(raw_articles: list[dict]) -> list[NormalizedArticle]:
    """Normalize all raw articles, deduplicating by ID and fuzzy title match."""
    normalized = []
    seen_ids: set[str] = set()
    accepted_titles: list[str] = []

    for raw in raw_articles:
        try:
            article = normalize_article(raw)
            if article.id in seen_ids:
                continue
            seen_ids.add(article.id)

            # Fuzzy title dedup: skip if very similar to an already-accepted title
            if any(_is_similar_title(article.title, t) for t in accepted_titles):
                continue

            accepted_titles.append(article.title)
            normalized.append(article)
        except Exception as e:
            logger.warning(f"Failed to normalize article: {e}")

    deduped = len(raw_articles) - len(normalized)
    logger.info(
        f"Normalized {len(normalized)} articles "
        f"(removed {deduped} duplicates from {len(raw_articles)} raw)"
    )
    return normalized
