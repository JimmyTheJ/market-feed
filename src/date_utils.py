"""Date utility functions for the market pipeline."""

from datetime import date


def today_str() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return date.today().isoformat()


def today_date() -> date:
    """Return today's date."""
    return date.today()


def analysis_dir_name(d: date | str) -> str:
    """Return the analysis directory name for a given date."""
    if isinstance(d, date):
        d = d.isoformat()
    return f"{d}-analysis"


def dated_filename(prefix: str, d: date | str, ext: str) -> str:
    """Return a dated filename like 'prefix-YYYY-MM-DD.ext'."""
    if isinstance(d, date):
        d = d.isoformat()
    return f"{prefix}-{d}.{ext}"
