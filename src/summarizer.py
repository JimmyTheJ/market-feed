"""Summarization using Ollama (local LLM) with extractive fallback.

Integrates with an Ollama instance for AI-powered summarization.
Falls back to extractive summaries when Ollama is unavailable.
"""

import logging
import os
import re
from datetime import date

import httpx

from .models import (
    CategorySummary,
    EnrichedPosition,
    MarketSummary,
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


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _call_ollama(
    prompt: str,
    timeout: float = 120.0,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str | None:
    """Call Ollama chat API for text generation.

    Uses /api/chat so the model's chat template is applied correctly.
    Sets think=False as a top-level request field (the correct location per
    Ollama's API) to disable Qwen3 reasoning mode. Falls back to
    _strip_thinking() as a safety net in case a model still emits <think>
    blocks in message.content.
    """
    base_url, resolved_model = _get_ollama_config(model)
    logger.info(f"Calling Ollama: model={resolved_model!r} url={base_url}")
    try:
        response = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": resolved_model,
                "think": False,  # Top-level: disables Qwen3 thinking mode (ignored by other models)
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a concise financial analyst. "
                            "Respond only with the requested structured format. "
                            "Do not include reasoning, preamble, or explanation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        msg = data.get("message", {})
        # When thinking mode is active, Ollama may put the answer in message.content
        # and thinking in message.thinking. Read content; fall back to thinking if empty.
        raw = msg.get("content") or msg.get("thinking") or ""
        if not raw:
            logger.warning(
                f"Ollama returned empty message: keys={list(msg.keys())}, "
                f"done_reason={data.get('done_reason')}"
            )
        result = _strip_thinking(raw)
        if len(raw) != len(result):
            logger.info(f"Stripped thinking block ({len(raw)} raw → {len(result)} chars)")
        logger.info(f"Ollama response received ({len(result)} chars)")
        return result or None
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
    ollama_temperature: float = 0.3,
    ollama_max_tokens: int = 2048,
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

        # Build subject line — use option_label when available so the LLM has full context
        if position.option_label:
            subject = f"{position.ticker} option ({position.option_label})"
            option_note = (
                f"\nNote: this is an options position ({position.option_label}). "
                "Focus your analysis on the underlying equity's price drivers and relevant news."
            )
        else:
            subject = f"{position.ticker} ({position.underlying or position.sector})"
            option_note = ""

        prompt = (
            f"Analyze these market developments for {subject}:{option_note}\n\n"
            f"{article_texts}\n\n"
            "Provide a brief analysis in this exact format:\n"
            "NET_BIAS: [bullish/bearish/mixed/neutral]\n"
            "INTERPRETATION: [2-3 sentence summary]\n"
            "BULLISH: [comma-separated factors, or none]\n"
            "BEARISH: [comma-separated factors, or none]\n"
            "RISKS: [comma-separated risks, or none]"
        )

        result = _call_ollama(
            prompt, model=ollama_model,
            temperature=ollama_temperature, max_tokens=ollama_max_tokens,
        )
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
    ollama_temperature: float = 0.3,
    ollama_max_tokens: int = 2048,
) -> PortfolioSummary:
    """Generate a complete portfolio summary."""
    # Per-position summaries
    position_summaries = [
        summarize_for_position(
            pos, scored_articles, use_ollama, ollama_model,
            ollama_temperature, ollama_max_tokens,
        )
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
            result = _call_ollama(
                prompt, model=ollama_model,
                temperature=ollama_temperature, max_tokens=ollama_max_tokens,
            )
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

    _, resolved_model = _get_ollama_config(ollama_model)

    return PortfolioSummary(
        date=run_date,
        run_label=run_label,
        model_name=resolved_model if (any_llm_used or contrarian_llm_used) else "",
        top_themes=sorted(all_themes)[:10],
        top_signals=top_signals,
        position_summaries=position_summaries,
        contrarian_views=contrarian_views,
        what_matters=what_matters,
        what_is_noise=what_is_noise,
        llm_used=any_llm_used or contrarian_llm_used,
    )


# Category display config for general market digest
CATEGORY_META: dict[str, dict] = {
    "macro": {"emoji": "📊", "label": "Macro & Economy"},
    "equities": {"emoji": "📈", "label": "Equities & Earnings"},
    "fixed_income": {"emoji": "🏦", "label": "Bonds & Rates"},
    "crypto": {"emoji": "₿", "label": "Crypto"},
    "energy": {"emoji": "⚡", "label": "Energy"},
    "commodities": {"emoji": "🪙", "label": "Commodities"},
    "healthcare": {"emoji": "💊", "label": "Healthcare & Pharma"},
    "ev_tesla": {"emoji": "🚗", "label": "EV & Clean Energy"},
    "technology": {"emoji": "💻", "label": "Technology"},
    "international": {"emoji": "🌍", "label": "International"},
    "general": {"emoji": "📰", "label": "General"},
}


def generate_category_summaries(
    scored_articles: list[ScoredArticle],
    use_ollama: bool = True,
    ollama_model: str | None = None,
    ollama_temperature: float = 0.3,
    ollama_max_tokens: int = 2048,
    top_articles_per_category: int = 8,
) -> list[CategorySummary]:
    """Generate per-category summaries for General Market Update mode."""
    from collections import defaultdict

    # Group articles by category
    by_category: dict[str, list[ScoredArticle]] = defaultdict(list)
    for sa in scored_articles:
        cat = sa.article.category or "general"
        by_category[cat].append(sa)

    summaries: list[CategorySummary] = []

    for category, articles in by_category.items():
        # Sort by score within category
        articles.sort(key=lambda x: x.portfolio_score, reverse=True)
        top = articles[:top_articles_per_category]
        top_headlines = [sa.article.title for sa in top[:5]]

        interpretation = ""
        key_points: list[str] = []
        llm_used = False

        if use_ollama and top:
            article_texts = "\n".join(
                f"- {sa.article.title}: {sa.article.content[:150]}" for sa in top
            )
            meta = CATEGORY_META.get(category, {"label": category})
            prompt = (
                f"Summarize these {meta['label']} news items for an investor:\n\n"
                f"{article_texts}\n\n"
                "Respond in this exact format:\n"
                "INTERPRETATION: [2-3 sentence summary of the key trend or development]\n"
                "KEY_POINTS: [3 concise bullet points, comma-separated]"
            )
            result = _call_ollama(
                prompt,
                model=ollama_model,
                temperature=ollama_temperature,
                max_tokens=min(ollama_max_tokens, 512),
            )
            if result:
                parsed = _parse_structured_response(result)
                if parsed.get("INTERPRETATION"):
                    interpretation = parsed["INTERPRETATION"]
                if parsed.get("KEY_POINTS"):
                    key_points = [
                        p.strip() for p in parsed["KEY_POINTS"].split(",") if p.strip()
                    ]
                llm_used = True

        if not interpretation:
            interpretation = "; ".join(top_headlines[:3]) if top_headlines else "No notable developments."

        summaries.append(
            CategorySummary(
                category=category,
                article_count=len(articles),
                top_headlines=top_headlines,
                interpretation=interpretation,
                key_points=key_points,
                llm_used=llm_used,
            )
        )

    # Sort categories by a preferred display order
    order = list(CATEGORY_META.keys())
    summaries.sort(key=lambda s: order.index(s.category) if s.category in order else len(order))
    return summaries


def generate_general_market_summary(
    run_date: date,
    scored_articles: list[ScoredArticle],
    use_ollama: bool = True,
    run_label: str = "",
    ollama_model: str | None = None,
    ollama_temperature: float = 0.3,
    ollama_max_tokens: int = 2048,
) -> MarketSummary:
    """Generate a General Market Update summary."""
    category_summaries = generate_category_summaries(
        scored_articles,
        use_ollama=use_ollama,
        ollama_model=ollama_model,
        ollama_temperature=ollama_temperature,
        ollama_max_tokens=ollama_max_tokens,
    )

    any_llm_used = any(cs.llm_used for cs in category_summaries)

    macro_overview = ""
    key_themes: list[str] = []

    if use_ollama and scored_articles:
        top_titles = "\n".join(
            f"- {sa.article.title}" for sa in scored_articles[:15]
        )
        prompt = (
            f"Based on today's top financial headlines, provide a macro market overview:\n\n"
            f"{top_titles}\n\n"
            "Respond in this exact format:\n"
            "OVERVIEW: [3-4 sentence market overview]\n"
            "THEMES: [5 key market themes, comma-separated]"
        )
        result = _call_ollama(
            prompt,
            model=ollama_model,
            temperature=ollama_temperature,
            max_tokens=min(ollama_max_tokens, 512),
        )
        if result:
            parsed = _parse_structured_response(result)
            if parsed.get("OVERVIEW"):
                macro_overview = parsed["OVERVIEW"]
            if parsed.get("THEMES"):
                key_themes = [t.strip() for t in parsed["THEMES"].split(",") if t.strip()]
            any_llm_used = True

    _, resolved_model = _get_ollama_config(ollama_model)

    return MarketSummary(
        date=run_date,
        run_label=run_label,
        model_name=resolved_model if any_llm_used else "",
        category_summaries=category_summaries,
        macro_overview=macro_overview,
        key_themes=key_themes,
        llm_used=any_llm_used,
    )
