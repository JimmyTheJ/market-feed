"""Tests for scoring module."""

import pytest
from datetime import datetime, timezone

from src.models import EnrichedPosition, NormalizedArticle
from src.scoring import (
    score_and_rank,
    score_article,
    score_article_for_position,
)


class TestScoreArticleForPosition:
    def test_direct_ticker_match_scores_high(self, sample_enriched_positions):
        ibit = sample_enriched_positions[0]
        article = NormalizedArticle(
            id="test1",
            source="test",
            title="IBIT sees massive inflows",
            content="The IBIT ETF continued its streak of inflows.",
            tokens=["ibit", "sees", "massive", "inflows", "etf", "continued", "streak"],
        )
        score, details = score_article_for_position(article, ibit)
        assert score > 0
        assert details.get("ticker_match") is True

    def test_keyword_matches_score(self, sample_enriched_positions):
        ibit = sample_enriched_positions[0]
        article = NormalizedArticle(
            id="test2",
            source="test",
            title="Bitcoin spot ETF demand surges",
            content="Spot bitcoin etf inflows from BlackRock led the market.",
            tokens=["bitcoin", "spot", "etf", "demand", "surges", "inflows",
                    "blackrock", "led", "market"],
        )
        score, details = score_article_for_position(article, ibit)
        assert score > 0
        assert "keyword_matches" in details
        assert len(details["keyword_matches"]) > 0

    def test_irrelevant_article_scores_low(self, sample_enriched_positions):
        ibit = sample_enriched_positions[0]
        article = NormalizedArticle(
            id="test3",
            source="test",
            title="New restaurant opens downtown",
            content="A Michelin-starred chef opened a new Italian restaurant.",
            tokens=["restaurant", "opens", "downtown", "michelin", "starred",
                    "chef", "opened", "italian"],
        )
        score, details = score_article_for_position(article, ibit)
        assert score == 0

    def test_weight_factor_applied(self, sample_enriched_positions):
        # Heavier weight = higher score for same article
        heavy = EnrichedPosition(
            ticker="TEST", weight=0.5,
            keywords=["bitcoin"], themes=["crypto"],
        )
        light = EnrichedPosition(
            ticker="TEST", weight=0.05,
            keywords=["bitcoin"], themes=["crypto"],
        )
        article = NormalizedArticle(
            id="test4", source="test",
            title="Bitcoin rallies 10%",
            content="Bitcoin surged today.",
            tokens=["bitcoin", "rallies", "surged", "today"],
        )
        heavy_score, _ = score_article_for_position(article, heavy)
        light_score, _ = score_article_for_position(article, light)
        assert heavy_score > light_score


class TestScoreArticle:
    def test_scores_against_all_positions(self, sample_articles, sample_enriched_positions):
        scored = score_article(sample_articles[0], sample_enriched_positions, 8)
        assert "IBIT" in scored.position_scores
        assert "QQQ" in scored.position_scores
        assert scored.portfolio_score > 0
        assert scored.top_position != ""

    def test_bitcoin_article_favors_ibit(self, sample_articles, sample_enriched_positions):
        scored = score_article(sample_articles[0], sample_enriched_positions, 8)
        assert scored.position_scores["IBIT"] > scored.position_scores["QQQ"]
        assert scored.top_position == "IBIT"

    def test_fed_article_favors_qqq(self, sample_articles, sample_enriched_positions):
        scored = score_article(sample_articles[1], sample_enriched_positions, 8)
        assert scored.position_scores["QQQ"] > 0


class TestScoreAndRank:
    def test_returns_sorted_by_portfolio_score(self, sample_articles, sample_enriched_positions):
        ranked = score_and_rank(sample_articles, sample_enriched_positions)
        for i in range(len(ranked) - 1):
            assert ranked[i].portfolio_score >= ranked[i + 1].portfolio_score

    def test_empty_articles(self, sample_enriched_positions):
        ranked = score_and_rank([], sample_enriched_positions)
        assert ranked == []

    def test_empty_positions(self, sample_articles):
        ranked = score_and_rank(sample_articles, [])
        assert len(ranked) == len(sample_articles)

    def test_relevant_articles_ranked_higher(self, sample_articles, sample_enriched_positions):
        ranked = score_and_rank(sample_articles, sample_enriched_positions)
        # Bitcoin ETF article should rank higher than the social media noise
        btc_idx = next(i for i, s in enumerate(ranked) if s.article.id == "article-btc-1")
        noise_idx = next(i for i, s in enumerate(ranked) if s.article.id == "article-noise-1")
        assert btc_idx < noise_idx
