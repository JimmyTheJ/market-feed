"""Relevance scoring engine.

Scores articles against enriched positions using multiple dimensions:
- Direct ticker mention
- Keyword overlap
- Theme overlap
- Macro sensitivity overlap
- Related term overlap
- Source priority bonus
- Recency bonus
- Portfolio weight multiplier
"""

import logging
from datetime import datetime, timezone

from .models import EnrichedPosition, NormalizedArticle, ScoredArticle

logger = logging.getLogger(__name__)

# Default scoring weights
DIRECT_TICKER_BONUS = 10.0
KEYWORD_MATCH_POINTS = 3.0
THEME_MATCH_POINTS = 4.0
MACRO_OVERLAP_POINTS = 2.5
RELATED_TERM_POINTS = 1.5
SOURCE_PRIORITY_FACTOR = 0.1
RECENCY_MAX_BONUS = 2.0
RECENCY_DECAY_HOURS = 48


def score_article_for_position(
    article: NormalizedArticle,
    position: EnrichedPosition,
) -> tuple[float, dict]:
    """Score an article's relevance to a specific position.

    Returns (score, details_dict).
    """
    details: dict = {}
    score = 0.0

    article_text = f"{article.title} {article.content}".lower()
    article_tokens_set = set(article.tokens)

    # 1. Direct ticker mention
    if position.ticker.lower() in article_text:
        score += DIRECT_TICKER_BONUS
        details["ticker_match"] = True

    # 2. Keyword overlap
    keyword_matches = [
        kw for kw in position.keywords if kw.lower() in article_text
    ]
    if keyword_matches:
        score += KEYWORD_MATCH_POINTS * len(keyword_matches)
        details["keyword_matches"] = keyword_matches

    # 3. Theme overlap
    theme_matches = []
    for theme in position.themes:
        theme_words = set(theme.lower().replace("_", " ").split())
        if theme_words & article_tokens_set:
            theme_matches.append(theme)
            score += THEME_MATCH_POINTS
    if theme_matches:
        details["theme_matches"] = theme_matches

    # 4. Macro sensitivity overlap
    macro_matches = []
    for macro in position.macro_sensitivities:
        macro_words = set(macro.lower().replace("_", " ").split())
        if macro_words & article_tokens_set:
            macro_matches.append(macro)
            score += MACRO_OVERLAP_POINTS
    if macro_matches:
        details["macro_matches"] = macro_matches

    # 5. Related term overlap
    related_matches = [
        term for term in position.related_terms if term.lower() in article_text
    ]
    if related_matches:
        score += RELATED_TERM_POINTS * len(related_matches)
        details["related_matches"] = related_matches

    # 6. Underlying asset mention
    if (
        position.underlying
        and position.underlying.lower().replace("_", " ") in article_text
    ):
        score += KEYWORD_MATCH_POINTS * 1.5
        details["underlying_match"] = True

    # Apply portfolio weight multiplier (0.5 + weight, so heavier positions score higher)
    weight_factor = 0.5 + position.weight
    score *= weight_factor
    details["weight_factor"] = round(weight_factor, 3)

    return round(score, 3), details


def score_article(
    article: NormalizedArticle,
    positions: list[EnrichedPosition],
    source_priority: int = 5,
) -> ScoredArticle:
    """Score an article against all positions."""
    position_scores: dict[str, float] = {}
    all_details: dict[str, dict] = {}

    for pos in positions:
        pos_score, details = score_article_for_position(article, pos)
        position_scores[pos.ticker] = pos_score
        if pos_score > 0:
            all_details[pos.ticker] = details

    # Source priority bonus
    priority_bonus = source_priority * SOURCE_PRIORITY_FACTOR

    # Recency bonus
    recency_bonus = 0.0
    if article.published_at:
        now = datetime.now(timezone.utc)
        age_hours = (now - article.published_at).total_seconds() / 3600
        if 0 < age_hours < RECENCY_DECAY_HOURS:
            recency_bonus = RECENCY_MAX_BONUS * (1 - age_hours / RECENCY_DECAY_HOURS)

    # Portfolio-level score
    portfolio_score = sum(position_scores.values()) + priority_bonus + recency_bonus

    top_position = (
        max(position_scores, key=position_scores.get) if position_scores else ""
    )

    return ScoredArticle(
        article=article,
        position_scores=position_scores,
        portfolio_score=round(portfolio_score, 3),
        top_position=top_position,
        scoring_details={
            "position_details": all_details,
            "priority_bonus": round(priority_bonus, 3),
            "recency_bonus": round(recency_bonus, 3),
        },
    )


def score_and_rank(
    articles: list[NormalizedArticle],
    positions: list[EnrichedPosition],
    source_priorities: dict[str, int] | None = None,
    max_per_position: int = 5,
) -> list[ScoredArticle]:
    """Score all articles and return them ranked by portfolio relevance."""
    source_priorities = source_priorities or {}

    scored = [
        score_article(article, positions, source_priorities.get(article.source, 5))
        for article in articles
    ]

    scored.sort(key=lambda x: x.portfolio_score, reverse=True)

    if scored:
        logger.info(
            f"Scored {len(scored)} articles, top score: {scored[0].portfolio_score}"
        )
    else:
        logger.info("No articles to score")

    return scored
