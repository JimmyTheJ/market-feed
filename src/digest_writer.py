"""Generate the daily Markdown digest."""

import logging

from .models import DailyPositionsSnapshot, PortfolioSummary

logger = logging.getLogger(__name__)


def generate_digest(
    summary: PortfolioSummary,
    snapshot: DailyPositionsSnapshot,
) -> str:
    """Generate the Markdown digest content."""
    d = summary.date.isoformat()
    lines: list[str] = []

    # Header
    lines.append(f"# Market Digest - {d}")
    lines.append("")
    lines.append(f"Generated from: output/{d}-analysis/daily-positions-{d}.yaml")
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
    total_weight = sum(p.weight for p in snapshot.positions)
    lines.append(f"- **Total weight:** {total_weight:.0%}")
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
