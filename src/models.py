"""Data models for the market pipeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


DEFAULT_CURRENCIES = ["USD", "CAD", "BTC"]


class Position(BaseModel):
    """A single portfolio position from positions.yaml.

    Supports equities and options. Options have additional fields for type
    (CALL/PUT), direction (LONG/SHORT), strike price, and expiration date.
    Short options are stored with negative shares.
    A price_override can be set to manually specify cost per share when
    live price lookup is unavailable or undesired.
    """

    ticker: str
    shares: float
    currency: str = "USD"
    price_override: Optional[float] = None
    position_type: str = "equity"  # "equity" or "option"
    option_type: Optional[str] = None  # "CALL" or "PUT"
    option_direction: Optional[str] = None  # "LONG" or "SHORT"
    strike: Optional[float] = None
    expiration: Optional[str] = None  # ISO date string, e.g. "2026-06-20"

    @field_validator("ticker")
    @classmethod
    def ticker_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ticker must be non-empty")
        return v.strip().upper()

    @field_validator("shares")
    @classmethod
    def shares_valid(cls, v: float) -> float:
        if v == 0:
            raise ValueError("shares must be non-zero")
        return float(v)

    @field_validator("currency")
    @classmethod
    def currency_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("currency must be non-empty")
        return v.strip().upper()

    @field_validator("position_type")
    @classmethod
    def valid_position_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("equity", "option"):
            raise ValueError("position_type must be 'equity' or 'option'")
        return v

    @field_validator("option_type")
    @classmethod
    def valid_option_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper().strip()
        if v not in ("CALL", "PUT"):
            raise ValueError("option_type must be 'CALL' or 'PUT'")
        return v

    @field_validator("option_direction")
    @classmethod
    def valid_option_direction(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper().strip()
        if v not in ("LONG", "SHORT"):
            raise ValueError("option_direction must be 'LONG' or 'SHORT'")
        return v

    @field_validator("price_override")
    @classmethod
    def price_override_positive(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("price_override must be non-negative")
        return v


class PositionsFile(BaseModel):
    """The full positions.yaml structure."""

    currencies: list[str] = Field(default_factory=lambda: list(DEFAULT_CURRENCIES))
    positions: list[Position]

    @field_validator("positions")
    @classmethod
    def no_duplicate_positions(cls, v: list[Position]) -> list[Position]:
        """Ensure no exact duplicate positions.

        For equities, duplicate means same ticker.
        For options, duplicate means same ticker + option_type + strike + expiration.
        """
        seen: set[tuple] = set()
        for p in v:
            if p.position_type == "option":
                key = (p.ticker, p.option_type, p.strike, p.expiration)
            else:
                key = (p.ticker, "equity", None, None)
            if key in seen:
                raise ValueError(f"Duplicate position: {p.ticker}")
            seen.add(key)
        return v


class EnrichedPosition(BaseModel):
    """A position enriched with metadata."""

    ticker: str
    weight: float
    instrument_type: str = "unknown"
    asset_class: str = "unknown"
    sector: str = "unknown"
    subsector: str = ""
    underlying: str = ""
    themes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    macro_sensitivities: list[str] = Field(default_factory=list)
    related_terms: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    region: str = "us"
    currency: str = "usd"
    notes: str = ""


class DailyPositionsSnapshot(BaseModel):
    """The daily enriched positions snapshot."""

    date: date
    generated_from: str = "config/positions.yaml"
    positions: list[EnrichedPosition]


class NormalizedArticle(BaseModel):
    """A normalized article from any source."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    title: str
    url: str = ""
    published_at: Optional[datetime] = None
    content: str = ""
    summary: Optional[str] = None
    tokens: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    category: str = "general"


class ScoredArticle(BaseModel):
    """An article with relevance scores."""

    article: NormalizedArticle
    position_scores: dict[str, float] = Field(default_factory=dict)
    portfolio_score: float = 0.0
    top_position: str = ""
    scoring_details: dict = Field(default_factory=dict)


class PositionSummary(BaseModel):
    """Summary for a single position."""

    ticker: str
    weight: float
    underlying: str = ""
    net_bias: str = "neutral"
    key_items: list[str] = Field(default_factory=list)
    interpretation: str = ""
    risks: list[str] = Field(default_factory=list)
    bullish_factors: list[str] = Field(default_factory=list)
    bearish_factors: list[str] = Field(default_factory=list)


class PortfolioSummary(BaseModel):
    """Summary for the whole portfolio."""

    date: date
    top_themes: list[str] = Field(default_factory=list)
    top_signals: list[dict] = Field(default_factory=list)
    position_summaries: list[PositionSummary] = Field(default_factory=list)
    contrarian_views: list[str] = Field(default_factory=list)
    what_matters: list[str] = Field(default_factory=list)
    what_is_noise: list[str] = Field(default_factory=list)
