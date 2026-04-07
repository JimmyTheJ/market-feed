"""Data models for the market pipeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    """A single portfolio position from positions.yaml."""

    ticker: str
    weight: float

    @field_validator("ticker")
    @classmethod
    def ticker_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ticker must be non-empty")
        return v.strip().upper()

    @field_validator("weight")
    @classmethod
    def weight_is_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("weight must be non-negative")
        return float(v)


class PositionsFile(BaseModel):
    """The full positions.yaml structure."""

    positions: list[Position]

    @field_validator("positions")
    @classmethod
    def no_duplicate_tickers(cls, v: list[Position]) -> list[Position]:
        seen: set[str] = set()
        for p in v:
            if p.ticker in seen:
                raise ValueError(f"Duplicate ticker: {p.ticker}")
            seen.add(p.ticker)
        return v

    def weight_sum(self) -> float:
        return sum(p.weight for p in self.positions)

    def weight_warning(self) -> Optional[str]:
        s = self.weight_sum()
        if abs(s - 1.0) > 0.01:
            return f"Weights sum to {s:.4f}, expected ~1.0"
        return None


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
