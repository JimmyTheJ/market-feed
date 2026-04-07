"""Tests for summarizer module."""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from src.models import EnrichedPosition, PortfolioSummary, PositionSummary, ScoredArticle
from src.summarizer import (
    _extractive_summary,
    _parse_structured_response,
    generate_portfolio_summary,
    summarize_for_position,
)


class TestExtractiveSubstitute:
    def test_empty_articles(self):
        result = _extractive_summary([])
        assert "No significant" in result

    def test_creates_bullet_list(self, sample_scored_articles):
        result = _extractive_summary(sample_scored_articles, max_items=2)
        assert result.count("-") >= 1
        assert "via" in result


class TestParseStructuredResponse:
    def test_parses_valid_response(self):
        response = """NET_BIAS: bullish
INTERPRETATION: Markets are moving higher on strong data.
BULLISH: strong earnings, momentum
BEARISH: none
RISKS: rate hike, geopolitics"""
        parsed = _parse_structured_response(response)
        assert parsed["NET_BIAS"] == "bullish"
        assert "strong" in parsed["INTERPRETATION"]
        assert "strong earnings" in parsed["BULLISH"]
        assert parsed["BEARISH"] == "none"
        assert "rate hike" in parsed["RISKS"]

    def test_handles_empty_response(self):
        parsed = _parse_structured_response("")
        assert parsed == {}


class TestSummarizeForPosition:
    def test_generates_position_summary_without_ollama(
        self, sample_enriched_positions, sample_scored_articles
    ):
        result = summarize_for_position(
            sample_enriched_positions[0],
            sample_scored_articles,
            use_ollama=False,
        )
        assert isinstance(result, PositionSummary)
        assert result.ticker == "IBIT"
        assert result.weight == 0.35
        assert result.net_bias == "neutral"  # No ollama = default
        assert len(result.risks) > 0

    def test_returns_key_items_from_relevant_articles(
        self, sample_enriched_positions, sample_scored_articles
    ):
        result = summarize_for_position(
            sample_enriched_positions[0],
            sample_scored_articles,
            use_ollama=False,
        )
        # Should have key items from the bitcoin article
        assert len(result.key_items) > 0


class TestGeneratePortfolioSummary:
    def test_generates_complete_summary(
        self, sample_enriched_positions, sample_scored_articles
    ):
        summary = generate_portfolio_summary(
            run_date=date(2026, 4, 3),
            positions=sample_enriched_positions,
            scored_articles=sample_scored_articles,
            use_ollama=False,
        )
        assert isinstance(summary, PortfolioSummary)
        assert summary.date == date(2026, 4, 3)
        assert len(summary.position_summaries) == 2
        assert len(summary.top_themes) > 0
        assert len(summary.contrarian_views) > 0

    def test_handles_empty_articles(self, sample_enriched_positions):
        summary = generate_portfolio_summary(
            run_date=date(2026, 4, 3),
            positions=sample_enriched_positions,
            scored_articles=[],
            use_ollama=False,
        )
        assert isinstance(summary, PortfolioSummary)
        assert len(summary.position_summaries) == 2
        assert summary.what_matters == ["No high-signal items today."]
