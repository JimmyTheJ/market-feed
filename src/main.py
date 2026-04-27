"""Main pipeline orchestrator.

Coordinates the complete daily market intelligence pipeline:
1. Load positions
2. Enrich with metadata
3. Fetch market content
4. Normalize articles
5. Score and rank by relevance
6. Summarize
7. Write dated digest and artifacts
"""

import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from .config_loader import resolve_config_path, resolve_data_path
from .date_utils import today_date
from .digest_writer import generate_digest
from .forex_service import get_rates_to
from .ingestion import fetch_all_sources, load_sources
from .metadata_lookup import enrich_all_positions
from .models import DailyPositionsSnapshot
from .normalization import normalize_all
from .positions_loader import load_positions
from .price_service import get_option_price, get_prices
from .scoring import score_and_rank
from .storage import (
    ensure_output_dir,
    write_daily_positions,
    write_digest,
    write_ranked_articles,
    write_raw_articles,
    write_run_log,
    write_summary_payload,
)
from .summarizer import generate_portfolio_summary

load_dotenv()

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the pipeline.

    Always writes to stdout. Also writes to logs/market-pipeline.log when the
    logs/ directory exists (it is volume-mounted in Docker).
    """
    from logging.handlers import RotatingFileHandler

    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    if log_dir.exists():
        log_file = log_dir / "market-pipeline.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        handlers.append(file_handler)

    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def run_pipeline(
    run_date: date | None = None,
    positions_path: str | None = None,
    sources_path: str | None = None,
    metadata_path: str | None = None,
    output_base: str = "output",
    use_ollama: bool = True,
    ollama_model: str | None = None,
    ollama_temperature: float = 0.3,
    ollama_max_tokens: int = 2048,
    profile: str | None = None,
    run_label: str = "",
) -> dict:
    """Execute the complete market pipeline.

    Paths default to the config_loader resolution (user override → default).
    If profile is provided, config files are resolved from the profile directory.
    run_label (e.g. "AM", "PM") differentiates multiple runs per day.
    Returns a dict with run statistics.
    """
    start_time = time.time()
    run_date = run_date or today_date()
    log_lines: list[str] = []

    def log(msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        log_lines.append(entry)
        logger.info(msg)

    label_display = f" ({run_label})" if run_label else ""
    log(f"=== Market Pipeline Run: {run_date.isoformat()}{label_display} ===")

    # Resolve paths via config_loader if not explicitly provided
    if not positions_path:
        positions_path = str(resolve_config_path("positions.yaml", profile=profile))
    if not sources_path:
        sources_path = str(resolve_config_path("sources.yaml", profile=profile))
    if not metadata_path:
        metadata_path = str(resolve_data_path("metadata/ticker_metadata.yaml"))

    # Scope output directory to profile if provided
    if profile:
        output_base = str(Path(output_base) / profile)

    # Step 1: Create output directory
    output_dir = ensure_output_dir(output_base, run_date, run_label)
    log(f"Output directory: {output_dir}")

    # Step 2: Load positions
    log("Loading positions...")
    positions_file = load_positions(positions_path)
    log(f"Loaded {len(positions_file.positions)} positions")

    # Step 2b: Compute portfolio weights from live prices
    log("Fetching live prices for weight computation...")
    tickers = [p.ticker for p in positions_file.positions if p.position_type != "cash"]
    prices = get_prices(tickers)
    native_currencies = list({p.currency for p in positions_file.positions})
    forex = get_rates_to("USD", native_currencies)

    total_value = 0.0
    position_values: list[float] = []
    for p in positions_file.positions:
        if p.position_type == "cash":
            price = 1.0
        elif p.price_override is not None:
            price = p.price_override
        elif p.position_type == "option" and p.option_type and p.strike and p.expiration:
            price = get_option_price(p.ticker, p.expiration, p.option_type, p.strike)
        else:
            price = prices.get(p.ticker)
        fx = forex.get(p.currency, 1.0)
        multiplier = 100 if p.position_type == "option" else 1
        val = (abs(p.shares) * price * multiplier * fx) if price is not None else 0.0
        position_values.append(val)
        total_value += val

    # Build weight-annotated positions for the enrichment step
    weighted_positions = []
    for p, val in zip(positions_file.positions, position_values):
        weight = val / total_value if total_value > 0 else 0.0
        weighted_positions.append(
            type("_WPos", (), {"ticker": p.ticker, "weight": weight})()
        )
    log(f"Computed portfolio weights (total ~${total_value:,.0f} USD)")

    # Step 3: Enrich positions
    log("Enriching positions with metadata...")
    enriched = enrich_all_positions(positions_file.positions, metadata_path)
    # Overwrite enriched weights with live-price-computed weights
    for ep, wp in zip(enriched, weighted_positions):
        ep.weight = wp.weight
    log(f"Enriched {len(enriched)} positions")

    # Step 4: Write daily positions snapshot
    snapshot = DailyPositionsSnapshot(date=run_date, positions=enriched)
    write_daily_positions(output_dir, snapshot, run_label)
    log("Wrote daily positions snapshot")

    # Step 5: Load and fetch sources
    log("Loading source configuration...")
    sources = load_sources(sources_path)

    log("Fetching content from sources...")
    raw_articles = fetch_all_sources(sources)
    log(f"Fetched {len(raw_articles)} raw articles")

    # Step 6: Normalize articles
    log("Normalizing articles...")
    normalized = normalize_all(raw_articles)
    log(f"Normalized {len(normalized)} articles")

    write_raw_articles(output_dir, run_date, normalized, run_label)

    # Step 7: Score and rank
    log("Scoring and ranking articles...")
    source_priorities = {}
    for feed in sources.get("rss", []):
        source_priorities[feed.get("name", "")] = feed.get("priority", 5)

    scored = score_and_rank(normalized, enriched, source_priorities)
    log(f"Scored {len(scored)} articles")

    write_ranked_articles(output_dir, run_date, scored, run_label)

    # Step 8: Summarize
    log("Generating portfolio summary...")
    summary = generate_portfolio_summary(
        run_date, enriched, scored, use_ollama, run_label=run_label,
        ollama_model=ollama_model,
        ollama_temperature=ollama_temperature,
        ollama_max_tokens=ollama_max_tokens,
    )
    log("Generated portfolio summary")

    write_summary_payload(output_dir, run_date, summary.model_dump(mode="json"), run_label)

    # Step 9: Write digest
    log("Writing market digest...")
    digest_content = generate_digest(summary, snapshot)
    write_digest(output_dir, run_date, digest_content, run_label)
    log("Wrote market digest")

    elapsed = time.time() - start_time
    log(f"=== Pipeline complete in {elapsed:.1f}s ===")

    write_run_log(output_dir, run_date, log_lines, run_label)

    return {
        "date": run_date.isoformat(),
        "run_label": run_label,
        "output_dir": str(output_dir),
        "positions_count": len(enriched),
        "articles_fetched": len(raw_articles),
        "articles_normalized": len(normalized),
        "articles_scored": len(scored),
        "elapsed_seconds": round(elapsed, 1),
        "llm_used": summary.llm_used,
    }


def main():
    """CLI entry point."""
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    use_ollama = os.getenv("USE_OLLAMA", "true").lower() == "true"
    output_base = os.getenv("OUTPUT_BASE_PATH", "output")

    try:
        result = run_pipeline(output_base=output_base, use_ollama=use_ollama)
        logger.info(f"Pipeline result: {result}")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
