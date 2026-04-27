"""Summarization using Ollama (local LLM) with extractive fallback.

Integrates with an Ollama instance for AI-powered summarization.
Falls back to extractive summaries when Ollama is unavailable.
"""

import logging
import os
from datetime import date

import httpx

from .models import (
    EnrichedPosition,
    PortfolioSummary,
    PositionSummary,
    ScoredArticle,
)

logger = logging.getLogger(__name__)


def _get_ollama_config(model: str | None = None) -> tuple[str, str]:
    """Get Ollama configuration from environment, with optional model override."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    default_model = os.getenv("OLLAMA_MODEL", "llama3.2")
    return base_url, model or default_model


def _call_ollama(
    prompt: str, timeout: float = 120.0, model: str | None = None
) -> str | None:
    """Call Ollama API for text generation."""
    base_url, resolved_model = _get_ollama_config(model)
    logger.info(f"Calling Ollama: model={resolved_model!r} url={base_url}")
    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": resolved_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 500},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        logger.info(f"Ollama response received ({len(result)} chars)")
        return result
    except Exception as e:
        logger.warning(f"Ollama call failed (model={resolved_model!r}): {type(e).__name__}: {e}")
        return None


def _extractive_summary(articles: list[ScoredArticle], max_items: int = 3) -> str:
    """Create a simple extractive summary from top articles."""
    if not articles:
        return "No significant developments identified."

    lines = []
    for sa in articles[:max_items]:
        title = sa.article.title
        source = sa.article.source
        lines.append(f"- {title} (via {source})")

    return "\n".join(lines)


def _parse_structured_response(result: str) -> dict:
    """Parse a structured LLM response into fields."""
    parsed: dict = {}
    for line in result.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().upper().replace(" ", "_")
            value = value.strip()
            parsed[key] = value
    return parsed


def summarize_for_position(
    position: EnrichedPosition,
    scored_articles: list[ScoredArticle],
    use_ollama: bool = True,
    ollama_model: str | None = None,
) -> PositionSummary:
    """Generate a summary for a single position."""
    # Filter and rank articles relevant to this position
    relevant = [
        sa
        for sa in scored_articles
        if sa.position_scores.get(position.ticker, 0) > 0
    ]
    relevant.sort(
        key=lambda x: x.position_scores.get(position.ticker, 0), reverse=True
    )
    top_articles = relevant[:5]

    key_items = [sa.article.title for sa in top_articles[:3]]

    interpretation = ""
    net_bias = "neutral"
    risks: list[str] = []
    bullish_factors: list[str] = []
    bearish_factors: list[str] = []
    llm_used = False

    if use_ollama and top_articles:
        article_texts = "\n".join(
            [
                f"- {sa.article.title}: {sa.article.content[:200]}"
                for sa in top_articles
            ]
        )

        prompt = (
            f"Analyze these market developments for {position.ticker} "
            f"({position.underlying or position.sector}):\n\n"
            f"{article_texts}\n\n"
            "Provide a brief analysis in this exact format:\n"
            "NET_BIAS: [bullish/bearish/mixed/neutral]\n"
            "INTERPRETATION: [2-3 sentence summary]\n"
            "BULLISH: [comma-separated factors, or none]\n"
            "BEARISH: [comma-separated factors, or none]\n"
            "RISKS: [comma-separated risks, or none]"
        )

        result = _call_ollama(prompt, model=ollama_model)
        if result:
            parsed = _parse_structured_response(result)
            bias = parsed.get("NET_BIAS", "neutral").lower()
            llm_used = True

            if bias in ("bullish", "bearish", "mixed", "neutral"):
                net_bias = bias

            if parsed.get("INTERPRETATION"):
                interpretation = parsed["INTERPRETATION"]

            if parsed.get("BULLISH") and parsed["BULLISH"].lower() != "none":
                bullish_factors = [
                    f.strip() for f in parsed["BULLISH"].split(",") if f.strip()
                ]

            if parsed.get("BEARISH") and parsed["BEARISH"].lower() != "none":
                bearish_factors = [
                    f.strip() for f in parsed["BEARISH"].split(",") if f.strip()
                ]

            if parsed.get("RISKS") and parsed["RISKS"].lower() != "none":
                risks = [
                    r.strip() for r in parsed["RISKS"].split(",") if r.strip()
                ]

    if not interpretation:
        interpretation = (
            _extractive_summary(top_articles)
            if top_articles
            else "No significant developments."
        )

    if not risks:
        risks = ["Market volatility", "Unexpected policy changes"]

    return PositionSummary(
        ticker=position.ticker,
        weight=position.weight,
        underlying=position.underlying,
        net_bias=net_bias,
        key_items=key_items,
        interpretation=interpretation,
        risks=risks,
        bullish_factors=bullish_factors,
        bearish_factors=bearish_factors,
        llm_used=llm_used,
    )


def generate_portfolio_summary(
    run_date: date,
    positions: list[EnrichedPosition],
    scored_articles: list[ScoredArticle],
    use_ollama: bool = True,
    run_label: str = "",
    ollama_model: str | None = None,
) -> PortfolioSummary:
    """Generate a complete portfolio summary."""
    # Per-position summaries
    position_summaries = [
        summarize_for_position(pos, scored_articles, use_ollama, ollama_model)
        for pos in positions
    ]

    # Track if any LLM call succeeded
    any_llm_used = any(ps.llm_used for ps in position_summaries)

    # Collect all themes
    all_themes: set[str] = set()
    for pos in positions:
        all_themes.update(pos.themes)

    # Top signals
    top_signals = [
        {
            "title": sa.article.title,
            "source": sa.article.source,
            "score": sa.portfolio_score,
            "top_position": sa.top_position,
        }
        for sa in scored_articles[:5]
    ]

    # Contrarian views
    contrarian_views: list[str] = []
    contrarian_llm_used = False
    if use_ollama and position_summaries:
        bullish_tickers = [
            ps.ticker for ps in position_summaries if ps.net_bias == "bullish"
        ]
        if bullish_tickers:
            tickers_str = ", ".join(bullish_tickers[:3])
            prompt = (
                f"Briefly state one strong bearish counterargument for holding "
                f"{tickers_str} today. Keep it to 1-2 sentences."
            )
            result = _call_ollama(prompt, model=ollama_model)
            if result:
                contrarian_llm_used = True
                contrarian_views.append(result)

    if not contrarian_views:
        contrarian_views = [
            "Always consider that consensus positioning can unwind rapidly."
        ]

    # What matters vs noise
    what_matters = (
        [sa.article.title for sa in scored_articles[:3]]
        if scored_articles
        else ["No high-signal items today."]
    )
    what_is_noise = (
        [sa.article.title for sa in scored_articles[-3:]]
        if len(scored_articles) > 5
        else ["Low article volume today."]
    )

    return PortfolioSummary(
        date=run_date,
        run_label=run_label,
        top_themes=sorted(all_themes)[:10],
        top_signals=top_signals,
        position_summaries=position_summaries,
        contrarian_views=contrarian_views,
        what_matters=what_matters,
        what_is_noise=what_is_noise,
        llm_used=any_llm_used or contrarian_llm_used,
    )
