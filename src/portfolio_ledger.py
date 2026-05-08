"""Portfolio P&L computation engine.

Supports three cost-basis methods:
  - fifo          – matches sells against oldest lots first
  - average_cost  – tracks weighted average cost; realized P&L = (sell - avg) × qty
  - specific_lot  – sell optionally references a specific buy via lot_id; falls back to FIFO
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .models import (
    DEFAULT_CURRENCIES,
    Position,
    PositionsFile,
    PositionPnL,
    PortfolioPnL,
    TransactionRecord,
)

logger = logging.getLogger(__name__)

VALID_METHODS = frozenset({"fifo", "average_cost", "specific_lot"})
CONTRACT_MULTIPLIER = 100  # one option contract = 100 shares of the underlying


# ── Internal lot representation ──────────────────────────────────────────────


@dataclass
class _Lot:
    transaction_id: str
    date: date
    quantity: float
    cost_per_unit: float  # per share (equity) or per-share-premium (option)
    currency: str


# ── Instrument helpers ────────────────────────────────────────────────────────


def _instrument_key(tx: TransactionRecord) -> str:
    """Stable key uniquely identifying a traded instrument."""
    if tx.position_type == "option":
        return f"{tx.ticker}__{tx.option_type}_{tx.strike}_{tx.expiration}"
    return f"{tx.ticker}__equity"


def _option_label(tx: TransactionRecord) -> str:
    """Human-readable label for an option contract."""
    parts = [tx.ticker]
    if tx.strike is not None:
        parts.append(f"${tx.strike:.0f}")
    if tx.option_type:
        parts.append(tx.option_type)
    if tx.expiration:
        parts.append(f"exp {tx.expiration}")
    return " ".join(parts)


def _multiplier(position_type: str) -> int:
    return CONTRACT_MULTIPLIER if position_type == "option" else 1


# ── FIFO matching ─────────────────────────────────────────────────────────────


def _apply_sell_fifo(
    lots: deque[_Lot],
    sell_qty: float,
    sell_price: float,
    position_type: str,
) -> float:
    """Match a sell against the front of *lots* (FIFO). Returns realized P&L."""
    mult = _multiplier(position_type)
    realized = 0.0
    remaining = sell_qty
    while remaining > 0 and lots:
        lot = lots[0]
        match_qty = min(remaining, lot.quantity)
        realized += (sell_price - lot.cost_per_unit) * match_qty * mult
        lot.quantity -= match_qty
        remaining -= match_qty
        if lot.quantity <= 1e-9:
            lots.popleft()
    if remaining > 1e-9:
        logger.warning(
            f"Sell of {sell_qty} units exceeds known open lots; {remaining:.4f} unmatched."
        )
    return realized


def _compute_fifo(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, deque[_Lot]], dict[str, float]]:
    lots_by_key: dict[str, deque[_Lot]] = {}
    realized_by_key: dict[str, float] = {}

    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in lots_by_key:
            lots_by_key[key] = deque()

        if tx.action == "buy":
            mult = _multiplier(tx.position_type)
            comm_per_share = tx.commission / (tx.quantity * mult) if tx.quantity else 0.0
            cost_per_unit = tx.price + comm_per_share
            lots_by_key[key].append(
                _Lot(
                    transaction_id=tx.id,
                    date=tx.date,
                    quantity=tx.quantity,
                    cost_per_unit=cost_per_unit,
                    currency=tx.currency,
                )
            )
        elif tx.action == "sell":
            realized = _apply_sell_fifo(
                lots_by_key[key], tx.quantity, tx.price, tx.position_type
            )
            realized_by_key[key] = realized_by_key.get(key, 0.0) + realized

    return lots_by_key, realized_by_key


# ── Average cost matching─────────────────────────────────────────────────────


def _compute_average_cost(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, deque[_Lot]], dict[str, float]]:
    # Track (total_shares, total_cost) per key
    buckets: dict[str, list[float]] = {}  # key -> [qty, cost]
    realized_by_key: dict[str, float] = {}

    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in buckets:
            buckets[key] = [0.0, 0.0]
        qty, cost = buckets[key]
        mult = _multiplier(tx.position_type)

        if tx.action == "buy":
            total_cost = tx.price * tx.quantity * mult + tx.commission
            buckets[key] = [qty + tx.quantity, cost + total_cost]
        elif tx.action == "sell":
            if qty > 0:
                avg_cost_per_unit = cost / (qty * mult) if qty > 0 else 0.0
                gain = (tx.price - avg_cost_per_unit) * tx.quantity * mult
                realized_by_key[key] = realized_by_key.get(key, 0.0) + gain
                frac_sold = min(tx.quantity / qty, 1.0)
                remaining_qty = max(qty - tx.quantity, 0.0)
                remaining_cost = cost * (1.0 - frac_sold)
                buckets[key] = [remaining_qty, remaining_cost]
            else:
                logger.warning(f"Sell of {tx.ticker} on {tx.date}: no known lots")

    # Convert buckets to Lot records for the open-position representation
    lots_by_key: dict[str, deque[_Lot]] = {}
    for key, (qty, cost) in buckets.items():
        if qty > 1e-9:
            pos_type = "option" if "__" in key and key.count("__") == 1 and "_" in key.split("__")[1] else "equity"
            mult = _multiplier(pos_type)
            avg_cost_per_unit = cost / (qty * mult) if qty > 0 else 0.0
            lots_by_key[key] = deque(
                [
                    _Lot(
                        transaction_id="avg_cost_bucket",
                        date=date.min,
                        quantity=qty,
                        cost_per_unit=avg_cost_per_unit,
                        currency="",
                    )
                ]
            )

    return lots_by_key, realized_by_key


# ── Specific-lot matching ─────────────────────────────────────────────────────


def _compute_specific_lot(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, deque[_Lot]], dict[str, float]]:
    lots_by_key: dict[str, deque[_Lot]] = {}
    lots_by_id: dict[str, _Lot] = {}
    realized_by_key: dict[str, float] = {}

    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in lots_by_key:
            lots_by_key[key] = deque()

        if tx.action == "buy":
            mult = _multiplier(tx.position_type)
            comm_per_share = tx.commission / (tx.quantity * mult) if tx.quantity else 0.0
            cost_per_unit = tx.price + comm_per_share
            lot = _Lot(
                transaction_id=tx.id,
                date=tx.date,
                quantity=tx.quantity,
                cost_per_unit=cost_per_unit,
                currency=tx.currency,
            )
            lots_by_key[key].append(lot)
            lots_by_id[tx.id] = lot
        elif tx.action == "sell":
            mult = _multiplier(tx.position_type)
            if tx.lot_id and tx.lot_id in lots_by_id:
                lot = lots_by_id[tx.lot_id]
                match_qty = min(tx.quantity, lot.quantity)
                realized = (tx.price - lot.cost_per_unit) * match_qty * mult
                realized_by_key[key] = realized_by_key.get(key, 0.0) + realized
                lot.quantity -= match_qty
                remainder = tx.quantity - match_qty
                if remainder > 1e-9:
                    realized_fifo = _apply_sell_fifo(
                        lots_by_key[key], remainder, tx.price, tx.position_type
                    )
                    realized_by_key[key] += realized_fifo
            else:
                realized = _apply_sell_fifo(
                    lots_by_key[key], tx.quantity, tx.price, tx.position_type
                )
                realized_by_key[key] = realized_by_key.get(key, 0.0) + realized

    return lots_by_key, realized_by_key


# ── Dispatch ──────────────────────────────────────────────────────────────────


def _compute_lots_and_realized(
    transactions: list[TransactionRecord], method: str
) -> tuple[dict[str, deque[_Lot]], dict[str, float]]:
    if method == "average_cost":
        return _compute_average_cost(transactions)
    if method == "specific_lot":
        return _compute_specific_lot(transactions)
    return _compute_fifo(transactions)


# ── Public API ────────────────────────────────────────────────────────────────


def derive_positions_from_transactions(
    transactions: list[TransactionRecord],
    currencies: list[str] | None = None,
) -> PositionsFile:
    """Derive the current open positions from a transaction history.

    Uses FIFO to compute net holdings per instrument.  Returns a
    :class:`PositionsFile` that is a drop-in replacement for
    ``load_positions()`` in the pipeline.
    """
    lots_by_key, _ = _compute_fifo(transactions)

    # Build a lookup of the earliest transaction per instrument for metadata
    meta: dict[str, TransactionRecord] = {}
    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in meta:
            meta[key] = tx

    positions: list[Position] = []
    for key, lots in lots_by_key.items():
        net_qty = sum(lot.quantity for lot in lots)
        if net_qty <= 1e-9:
            continue
        tx_meta = meta[key]
        if tx_meta.position_type == "option":
            positions.append(
                Position(
                    ticker=tx_meta.ticker,
                    shares=round(net_qty, 6),
                    currency=tx_meta.currency,
                    position_type="option",
                    option_type=tx_meta.option_type,
                    option_direction=tx_meta.option_direction,
                    strike=tx_meta.strike,
                    expiration=tx_meta.expiration,
                )
            )
        else:
            positions.append(
                Position(
                    ticker=tx_meta.ticker,
                    shares=round(net_qty, 6),
                    currency=tx_meta.currency,
                    position_type=tx_meta.position_type,
                )
            )

    return PositionsFile(
        currencies=currencies or list(DEFAULT_CURRENCIES),
        positions=positions,
    )


def compute_pnl(
    transactions: list[TransactionRecord],
    prices: dict[str, float],
    forex: dict[str, float],
    method: str = "fifo",
    display_currency: str = "USD",
) -> PortfolioPnL:
    """Compute full portfolio P&L.

    Args:
        transactions: All trade records (order does not matter; sorted internally).
        prices: Current market price keyed by ticker symbol.
        forex: Rates from each native currency to *display_currency*
               (e.g. ``{"USD": 1.0, "CAD": 0.73}``).
        method: One of ``"fifo"``, ``"average_cost"``, ``"specific_lot"``.
        display_currency: Currency for portfolio totals.

    Returns:
        :class:`PortfolioPnL` with per-position and aggregate stats.
    """
    if method not in VALID_METHODS:
        logger.warning(f"Unknown cost basis method '{method}', falling back to FIFO")
        method = "fifo"

    lots_by_key, realized_by_key = _compute_lots_and_realized(transactions, method)

    # Instrument metadata lookups
    currency_by_key: dict[str, str] = {}
    pos_type_by_key: dict[str, str] = {}
    opt_label_by_key: dict[str, str] = {}
    for tx in transactions:
        key = _instrument_key(tx)
        if key not in currency_by_key:
            currency_by_key[key] = tx.currency
            pos_type_by_key[key] = tx.position_type
            if tx.position_type == "option":
                opt_label_by_key[key] = _option_label(tx)

    all_keys = sorted(set(realized_by_key) | set(lots_by_key))

    position_list: list[PositionPnL] = []
    total_realized_disp = 0.0
    total_unrealized_disp: Optional[float] = 0.0
    total_cost_basis_disp = 0.0
    total_market_value_disp: Optional[float] = 0.0

    for key in all_keys:
        currency = currency_by_key.get(key, "USD")
        pos_type = pos_type_by_key.get(key, "equity")
        mult = _multiplier(pos_type)
        fx = forex.get(currency, 1.0)
        ticker = key.split("__")[0]

        lots = lots_by_key.get(key, deque())
        open_qty = sum(lot.quantity for lot in lots)

        # Open position cost basis
        if open_qty > 1e-9 and lots:
            total_lot_cost = sum(lot.cost_per_unit * lot.quantity for lot in lots)
            avg_cost = total_lot_cost / open_qty
            total_cost_native = avg_cost * open_qty * mult
        else:
            avg_cost = 0.0
            total_cost_native = 0.0

        # Current value and unrealized P&L.
        # For options we only have the underlying stock price, not the option
        # premium — using it would yield the notional shares value rather than
        # the option's market value.  Mark as unavailable instead.
        current_price = prices.get(ticker)
        current_value_native: Optional[float] = None
        unrealized_native: Optional[float] = None
        unrealized_pct: Optional[float] = None

        if current_price is not None and open_qty > 1e-9 and pos_type != "option":
            current_value_native = current_price * open_qty * mult
            unrealized_native = current_value_native - total_cost_native
            if total_cost_native != 0:
                unrealized_pct = (unrealized_native / abs(total_cost_native)) * 100.0

        realized_native = realized_by_key.get(key, 0.0)

        total_pl_native: Optional[float] = None
        if unrealized_native is not None:
            total_pl_native = realized_native + unrealized_native
        elif open_qty <= 1e-9:
            total_pl_native = realized_native

        # Convert to display currency
        realized_disp = realized_native * fx
        unrealized_disp: Optional[float] = (
            unrealized_native * fx if unrealized_native is not None else None
        )
        total_pl_disp: Optional[float] = (
            total_pl_native * fx if total_pl_native is not None else None
        )
        cost_basis_disp = total_cost_native * fx
        current_value_disp: Optional[float] = (
            current_value_native * fx if current_value_native is not None else None
        )

        # Accumulate portfolio totals
        total_realized_disp += realized_disp
        if unrealized_disp is not None and total_unrealized_disp is not None:
            total_unrealized_disp += unrealized_disp
        else:
            total_unrealized_disp = None  # partial — some prices unavailable
        total_cost_basis_disp += cost_basis_disp
        if current_value_disp is not None and total_market_value_disp is not None:
            total_market_value_disp += current_value_disp
        else:
            total_market_value_disp = None

        def _r2(v: float | None) -> float | None:
            return round(v, 2) if v is not None else None

        position_list.append(
            PositionPnL(
                instrument_key=key,
                ticker=ticker,
                position_type=pos_type,
                option_label=opt_label_by_key.get(key, ""),
                currency=currency,
                open_quantity=round(open_qty, 6),
                avg_cost_basis=round(avg_cost, 4),
                total_cost_basis_native=_r2(total_cost_native),
                current_price=round(current_price, 4) if current_price is not None else None,
                current_value_native=_r2(current_value_native),
                realized_pl_native=_r2(realized_native),
                unrealized_pl_native=_r2(unrealized_native),
                total_pl_native=_r2(total_pl_native),
                unrealized_pl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
                fx_rate=round(fx, 6),
                total_cost_basis_display=_r2(cost_basis_disp),
                realized_pl_display=_r2(realized_disp),
                unrealized_pl_display=_r2(unrealized_disp),
                total_pl_display=_r2(total_pl_disp),
                current_value_display=_r2(current_value_disp),
                display_currency=display_currency,
            )
        )

    total_pl: Optional[float] = None
    if total_unrealized_disp is not None:
        total_pl = total_realized_disp + total_unrealized_disp
    # else: cannot compute without all prices

    return PortfolioPnL(
        computed_at=datetime.now(),
        cost_basis_method=method,
        display_currency=display_currency,
        positions=position_list,
        total_cost_basis=round(total_cost_basis_disp, 2),
        total_market_value=(
            round(total_market_value_disp, 2) if total_market_value_disp is not None else None
        ),
        total_realized_pl=round(total_realized_disp, 2),
        total_unrealized_pl=(
            round(total_unrealized_disp, 2) if total_unrealized_disp is not None else None
        ),
        total_pl=round(total_pl, 2) if total_pl is not None else None,
    )
