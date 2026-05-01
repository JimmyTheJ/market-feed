"""Generate the daily Markdown digest."""

import logging
import os

from .models import DailyPositionsSnapshot, MarketSummary, PortfolioSummary
from .summarizer import CATEGORY_META

logger = logging.getLogger(__name__)


def generate_digest(
    summary: PortfolioSummary,
    snapshot: DailyPositionsSnapshot,
) -> str:
    """Generate the Markdown digest content."""
    d = summary.date.isoformat()
    label_suffix = f" {summary.run_label}" if summary.run_label else ""
    lines: list[str] = []

    # Header
    lines.append(f"# Daily Digest: {d}{label_suffix}")
    lines.append("")

    # LLM usage indicator
    if summary.llm_used:
        model = summary.model_name or os.getenv("OLLAMA_MODEL", "llama3.2")
        lines.append(f"> 🤖 **Summary method:** AI-generated ({model})")
    else:
        lines.append("> 📋 **Summary method:** Extractive (LLM unavailable)")
    lines.append("")

    lines.append(f"Positions analyzed: {len(snapshot.positions)}")
    if summary.top_themes:
        lines.append(f"Top portfolio themes: {', '.join(summary.top_themes[:6])}")
    lines.append("")

    # Portfolio Overview
    lines.append("## Portfolio Overview")
    lines.append("")
    if summary.top_themes:
        lines.append(f"- **Main themes:** {', '.join(summary.top_themes[:5])}")
    tickers = ", ".join(p.ticker for p in snapshot.positions)
    lines.append(f"- **Positions:** {tickers}")
    lines.append("")

    # Top Portfolio Signals
    lines.append("## Top Portfolio Signals")
    lines.append("")
    if summary.top_signals:
        for i, signal in enumerate(summary.top_signals[:5], 1):
            lines.append(f"### {i}. {signal['title']}")
            lines.append(f"- **Source:** {signal['source']}")
            lines.append(f"- **Relevance score:** {signal['score']}")
            lines.append(f"- **Top position:** {signal['top_position']}")
            lines.append("")
    else:
        lines.append("No high-signal articles identified today.")
        lines.append("")

    # Position Analysis
    lines.append("## Position Analysis")
    lines.append("")

    for ps in summary.position_summaries:
        lines.append(f"### {ps.ticker}")
        lines.append(f"**Weight:** {ps.weight:.0%}")
        if ps.underlying:
            lines.append(f"**Underlying:** {ps.underlying}")
        lines.append(f"**Today's net bias:** {ps.net_bias.capitalize()}")
        llm_tag = "🤖 AI" if ps.llm_used else "📋 Extractive"
        lines.append(f"**Analysis:** {llm_tag}")
        lines.append("")

        if ps.key_items:
            lines.append("#### Key Items")
            for j, item in enumerate(ps.key_items, 1):
                lines.append(f"{j}. {item}")
            lines.append("")

        if ps.interpretation:
            lines.append("#### Interpretation")
            lines.append(ps.interpretation)
            lines.append("")

        if ps.bullish_factors:
            lines.append("#### Bullish Factors")
            for f in ps.bullish_factors:
                lines.append(f"- {f}")
            lines.append("")

        if ps.bearish_factors:
            lines.append("#### Bearish Factors")
            for f in ps.bearish_factors:
                lines.append(f"- {f}")
            lines.append("")

        if ps.risks:
            lines.append("#### Risks")
            for r in ps.risks:
                lines.append(f"- {r}")
            lines.append("")

    # Contrarian View
    lines.append("## Contrarian View")
    lines.append("")
    for cv in summary.contrarian_views:
        lines.append(f"- {cv}")
    lines.append("")

    # What Matters / Noise
    lines.append("## What Likely Matters Today")
    lines.append("")
    for item in summary.what_matters:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## What Is Probably Noise")
    lines.append("")
    for item in summary.what_is_noise:
        lines.append(f"- {item}")
    lines.append("")

    return "\n".join(lines)


def generate_general_digest(summary: MarketSummary) -> str:
    """Generate a General Market Update Markdown digest."""
    d = summary.date.isoformat()
    label_suffix = f" {summary.run_label}" if summary.run_label else ""
    lines: list[str] = []

    lines.append(f"# General Market Update: {d}{label_suffix}")
    lines.append("")

    if summary.llm_used:
        model = summary.model_name or os.getenv("OLLAMA_MODEL", "llama3.2")
        lines.append(f"> 🤖 **Summary method:** AI-generated ({model})")
    else:
        lines.append("> 📋 **Summary method:** Extractive (LLM unavailable)")
    lines.append("")

    total_articles = sum(cs.article_count for cs in summary.category_summaries)
    lines.append(f"Sources processed: {total_articles} articles across {len(summary.category_summaries)} categories")
    lines.append("")

    # Macro overview
    if summary.macro_overview:
        lines.append("## 🌐 Market Overview")
        lines.append("")
        lines.append(summary.macro_overview)
        lines.append("")

    if summary.key_themes:
        lines.append("**Key themes today:** " + " · ".join(f"`{t}`" for t in summary.key_themes))
        lines.append("")

    # Per-category sections
    for cs in summary.category_summaries:
        meta = CATEGORY_META.get(cs.category, {"emoji": "📰", "label": cs.category.title()})
        emoji = meta["emoji"]
        label = meta["label"]

        lines.append(f"## {emoji} {label}")
        lines.append(f"*{cs.article_count} articles*")
        lines.append("")

        if cs.interpretation:
            lines.append(cs.interpretation)
            lines.append("")

        if cs.key_points:
            for point in cs.key_points:
                lines.append(f"- {point}")
            lines.append("")

        if cs.top_headlines:
            lines.append("**Top headlines:**")
            for headline in cs.top_headlines:
                lines.append(f"- {headline}")
            lines.append("")

    return "\n".join(lines)
