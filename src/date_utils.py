"""Date utility functions for the market pipeline."""

from datetime import date


def today_str() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return date.today().isoformat()


def today_date() -> date:
    """Return today's date."""
    return date.today()


def analysis_dir_name(d: date | str, label: str = "") -> str:
    """Return the analysis directory name for a given date and optional label."""
    if isinstance(d, date):
        d = d.isoformat()
    suffix = f"-{label.lower()}" if label else ""
    return f"{d}-analysis{suffix}"


def dated_filename(prefix: str, d: date | str, ext: str, label: str = "") -> str:
    """Return a dated filename like 'prefix-YYYY-MM-DD[-label].ext'."""
    if isinstance(d, date):
        d = d.isoformat()
    suffix = f"-{label.lower()}" if label else ""
    return f"{prefix}-{d}{suffix}.{ext}"
