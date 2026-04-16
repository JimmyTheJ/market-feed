"""Tests for normalization module."""

import pytest

from src.normalization import (
    extract_entities,
    extract_tokens,
    generate_article_id,
    normalize_all,
    normalize_article,
    strip_html,
)


class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_entities(self):
        assert strip_html("AT&amp;T &amp; others") == "AT&T & others"

    def test_collapses_whitespace(self):
        assert strip_html("  too   many   spaces  ") == "too many spaces"

    def test_empty_string(self):
        assert strip_html("") == ""


class TestExtractTokens:
    def test_basic_extraction(self):
        tokens = extract_tokens("Bitcoin ETF inflows surged today")
        assert "bitcoin" in tokens
        assert "etf" in tokens
        assert "inflows" in tokens
        assert "surged" in tokens

    def test_stops_removed(self):
        tokens = extract_tokens("The price of gold is rising")
        assert "the" not in tokens
        assert "of" not in tokens
        assert "is" not in tokens
        assert "gold" in tokens

    def test_financial_short_words_kept(self):
        tokens = extract_tokens("The fed raised the rate on oil and gas")
        assert "fed" in tokens
        assert "oil" in tokens
        assert "gas" in tokens
        assert "rate" in tokens


class TestExtractEntities:
    def test_finds_known_entities(self):
        entities = extract_entities("The Federal Reserve raised rates today")
        assert "Federal Reserve" in entities or "Fed" in entities

    def test_finds_capitalized_phrases(self):
        entities = extract_entities("BlackRock CEO Larry Fink spoke today")
        assert "Larry Fink" in entities

    def test_finds_financial_entities(self):
        entities = extract_entities("Bitcoin and Ethereum dropped as SEC announced review")
        assert "Bitcoin" in entities
        assert "SEC" in entities


class TestGenerateArticleId:
    def test_deterministic(self):
        id1 = generate_article_id("src", "title", "url")
        id2 = generate_article_id("src", "title", "url")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        id1 = generate_article_id("src1", "title", "url")
        id2 = generate_article_id("src2", "title", "url")
        assert id1 != id2

    def test_length(self):
        result = generate_article_id("s", "t", "u")
        assert len(result) == 16


class TestNormalizeArticle:
    def test_normalizes_raw_article(self):
        raw = {
            "title": "<b>Bitcoin</b> surges 5%",
            "content": "<p>Price <em>jumped</em> on ETF news</p>",
            "url": "https://example.com/btc",
            "source_name": "test_source",
            "category": "crypto",
            "published_at": None,
        }
        article = normalize_article(raw)
        assert article.title == "Bitcoin surges 5%"
        assert "jumped" in article.content
        assert article.source == "test_source"
        assert article.category == "crypto"
        assert len(article.id) == 16
        assert len(article.tokens) > 0


class TestNormalizeAll:
    def test_normalizes_and_deduplicates(self):
        raw_articles = [
            {"title": "Article 1", "content": "Content 1", "url": "url1",
             "source_name": "s1", "category": "general"},
            {"title": "Article 1", "content": "Content 1", "url": "url1",
             "source_name": "s1", "category": "general"},
            {"title": "Article 2", "content": "Content 2", "url": "url2",
             "source_name": "s2", "category": "general"},
        ]
        result = normalize_all(raw_articles)
        assert len(result) == 2  # Deduped from 3

    def test_handles_empty_list(self):
        assert normalize_all([]) == []

    def test_handles_malformed_articles_gracefully(self):
        raw_articles = [
            {"title": "Good article", "content": "Content", "url": "url",
             "source_name": "s", "category": "g"},
        ]
        result = normalize_all(raw_articles)
        assert len(result) == 1

    def test_fuzzy_dedup_similar_titles(self):
        """Articles with near-identical titles from different sources should be deduped."""
        raw_articles = [
            {"title": "Fed raises interest rates by 25 basis points",
             "content": "Content A", "url": "url1",
             "source_name": "reuters", "category": "macro"},
            {"title": "Fed raises interest rates by 25 basis points today",
             "content": "Content B", "url": "url2",
             "source_name": "bloomberg", "category": "macro"},
        ]
        result = normalize_all(raw_articles)
        assert len(result) == 1

    def test_fuzzy_dedup_different_titles_kept(self):
        """Articles with genuinely different titles should be kept."""
        raw_articles = [
            {"title": "Bitcoin surges past $100k on ETF news update",
             "content": "Content A", "url": "url1",
             "source_name": "coindesk", "category": "crypto"},
            {"title": "Oil prices rise 3% on OPEC+ production cuts agreement",
             "content": "Content B", "url": "url2",
             "source_name": "reuters", "category": "energy"},
        ]
        result = normalize_all(raw_articles)
        assert len(result) == 2

    def test_fuzzy_dedup_skips_short_titles(self):
        """Short titles should not trigger fuzzy matching."""
        raw_articles = [
            {"title": "Rate hike", "content": "C1", "url": "u1",
             "source_name": "s1", "category": "g"},
            {"title": "Rate cut", "content": "C2", "url": "u2",
             "source_name": "s2", "category": "g"},
        ]
        result = normalize_all(raw_articles)
        assert len(result) == 2
