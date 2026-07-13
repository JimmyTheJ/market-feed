"""Portfolio P&L computation engine.

Supports signed positions: buy-to-open is positive, sell-to-open is negative.
Short option/equity lots are tracked separately from long lots.

Cost-basis methods:
  - fifo          – matches closes against oldest lots first (supports shorts)
  - average_cost  – long-only weighted average; shorts use signed FIFO
  - specific_lot  – lot_id matching with FIFO fallback; shorts use signed FIFO
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
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
    quantity: float  # always positive within the lot
    cost_per_unit: float  # per share (equity) or per-share-premium (option)
    currency: str


@dataclass
class _PositionBook:
    """Signed position state for one instrument."""

    long_lots: deque[_Lot] = field(default_factory=deque)
    short_lots: deque[_Lot] = field(default_factory=deque)

    @property
    def long_qty(self) -> float:
        return sum(lot.quantity for lot in self.long_lots)

    @property
    def short_qty(self) -> float:
        return sum(lot.quantity for lot in self.short_lots)

    @property
    def net_signed_qty(self) -> float:
        return self.long_qty - self.short_qty


# ── Instrument helpers ────────────────────────────────────────────────────────


def _instrument_key(tx: TransactionRecord) -> str:
    """Stable key uniquely identifying a traded instrument."""
    if tx.position_type == "option":
        return f"{tx.ticker}__{tx.option_type}_{tx.strike}_{tx.expiration}"
    return f"{tx.ticker}__equity"


def _option_label(
    tx: TransactionRecord | None = None,
    *,
    ticker: str = "",
    option_type: str | None = None,
    strike: float | None = None,
    expiration: str | None = None,
    direction: str | None = None,
) -> str:
    """Human-readable label for an option contract."""
    if tx is not None:
        ticker = tx.ticker
        option_type = tx.option_type
        strike = tx.strike
        expiration = tx.expiration
        direction = tx.option_direction
    parts = [ticker]
    if strike is not None:
        parts.append(f"${strike:.0f}")
    if option_type:
        parts.append(option_type)
    if expiration:
        parts.append(f"exp {expiration}")
    if direction:
        parts.append(f"({direction.upper()})")
    return " ".join(parts)


def _multiplier(position_type: str) -> int:
    return CONTRACT_MULTIPLIER if position_type == "option" else 1


def _cost_per_unit(tx: TransactionRecord) -> float:
    mult = _multiplier(tx.position_type)
    comm_per_share = tx.commission / (tx.quantity * mult) if tx.quantity else 0.0
    return tx.price + comm_per_share


# ── Lot matching helpers ──────────────────────────────────────────────────────


def _close_long_lots(
    lots: deque[_Lot], qty: float, close_price: float, mult: int
) -> tuple[float, float]:
    """Close long lots FIFO. Returns (realized_pnl, unmatched_qty)."""
    realized = 0.0
    remaining = qty
    while remaining > 1e-9 and lots:
        lot = lots[0]
        match_qty = min(remaining, lot.quantity)
        realized += (close_price - lot.cost_per_unit) * match_qty * mult
        lot.quantity -= match_qty
        remaining -= match_qty
        if lot.quantity <= 1e-9:
            lots.popleft()
    return realized, remaining


def _close_short_lots(
    lots: deque[_Lot], qty: float, close_price: float, mult: int
) -> tuple[float, float]:
    """Close short lots FIFO. Returns (realized_pnl, unmatched_qty)."""
    realized = 0.0
    remaining = qty
    while remaining > 1e-9 and lots:
        lot = lots[0]
        match_qty = min(remaining, lot.quantity)
        # Short P&L: credit at open minus cost to close
        realized += (lot.cost_per_unit - close_price) * match_qty * mult
        lot.quantity -= match_qty
        remaining -= match_qty
        if lot.quantity <= 1e-9:
            lots.popleft()
    return realized, remaining


def _open_long_lot(book: _PositionBook, tx: TransactionRecord) -> None:
    book.long_lots.append(
        _Lot(
            transaction_id=tx.id,
            date=tx.date,
            quantity=tx.quantity,
            cost_per_unit=_cost_per_unit(tx),
            currency=tx.currency,
        )
    )


def _open_short_lot(book: _PositionBook, tx: TransactionRecord, qty: float) -> None:
    mult = _multiplier(tx.position_type)
    comm_per_share = tx.commission * (qty / tx.quantity) / (qty * mult) if qty else 0.0
    credit_per_unit = tx.price + comm_per_share
    book.short_lots.append(
        _Lot(
            transaction_id=tx.id,
            date=tx.date,
            quantity=qty,
            cost_per_unit=credit_per_unit,
            currency=tx.currency,
        )
    )


def _process_transaction(book: _PositionBook, tx: TransactionRecord) -> float:
    """Apply one transaction to a position book. Returns realized P&L."""
    mult = _multiplier(tx.position_type)
    realized = 0.0

    if tx.position_type == "option":
        direction = (tx.option_direction or "LONG").upper()
        if tx.action == "buy":
            if direction == "SHORT":
                r, unmatched = _close_short_lots(book.short_lots, tx.quantity, tx.price, mult)
                realized += r
                if unmatched > 1e-9:
                    logger.warning(
                        f"Buy-to-close {tx.quantity} {tx.ticker} option on {tx.date}: "
                        f"{unmatched:.4f} contracts unmatched to short lots."
                    )
            else:
                _open_long_lot(book, tx)
        else:  # sell
            if direction == "SHORT":
                _open_short_lot(book, tx, tx.quantity)
            else:
                r, unmatched = _close_long_lots(book.long_lots, tx.quantity, tx.price, mult)
                realized += r
                if unmatched > 1e-9:
                    logger.warning(
                        f"Sell-to-close {tx.quantity} {tx.ticker} option on {tx.date}: "
                        f"{unmatched:.4f} contracts exceed long lots."
                    )
    else:
        # Equity: buy covers shorts first; sell closes longs then may open short
        if tx.action == "buy":
            remaining = tx.quantity
            if book.short_qty > 1e-9:
                r, remaining = _close_short_lots(book.short_lots, remaining, tx.price, mult)
                realized += r
            if remaining > 1e-9:
                long_tx = tx.model_copy(update={"quantity": remaining})
                _open_long_lot(book, long_tx)
        else:
            r, remaining = _close_long_lots(book.long_lots, tx.quantity, tx.price, mult)
            realized += r
            if remaining > 1e-9:
                _open_short_lot(book, tx, remaining)

    return realized


def build_position_books(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, _PositionBook], dict[str, float]]:
    """Replay transactions into signed position books (signed FIFO)."""
    books: dict[str, _PositionBook] = {}
    realized_by_key: dict[str, float] = {}

    for tx in sorted(transactions, key=lambda t: (t.date, t.id)):
        key = _instrument_key(tx)
        if key not in books:
            books[key] = _PositionBook()
        r = _process_transaction(books[key], tx)
        if r:
            realized_by_key[key] = realized_by_key.get(key, 0.0) + r

    return books, realized_by_key


# ── Legacy average-cost / specific-lot (long-only fallback) ───────────────────


def _books_to_long_lots(books: dict[str, _PositionBook]) -> dict[str, deque[_Lot]]:
    """Expose only long lots for backward-compatible consumers."""
    return {key: book.long_lots for key, book in books.items() if book.long_qty > 1e-9}


def _compute_average_cost(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, _PositionBook], dict[str, float]]:
    has_short_intent = any(
        (t.position_type == "option" and (t.option_direction or "").upper() == "SHORT")
        or (t.position_type == "equity" and t.action == "sell")
        for t in transactions
    )
    if has_short_intent:
        logger.debug("average_cost: using signed FIFO because short positions are present")
        return build_position_books(transactions)

    buckets: dict[str, list[float]] = {}
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

    books: dict[str, _PositionBook] = {}
    for key, (qty, cost) in buckets.items():
        if qty > 1e-9:
            pos_type = (
                "option"
                if "__" in key and key.count("__") == 1 and "_" in key.split("__")[1]
                else "equity"
            )
            mult = _multiplier(pos_type)
            avg_cost_per_unit = cost / (qty * mult) if qty > 0 else 0.0
            books[key] = _PositionBook(
                long_lots=deque(
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
            )

    return books, realized_by_key


def _compute_specific_lot(
    transactions: list[TransactionRecord],
) -> tuple[dict[str, _PositionBook], dict[str, float]]:
    has_short_intent = any(
        t.position_type == "option" and (t.option_direction or "").upper() == "SHORT"
        for t in transactions
    )
    if has_short_intent:
        logger.debug("specific_lot: using signed FIFO because short options are present")
        return build_position_books(transactions)

    books: dict[str, _PositionBook] = {}
    lots_by_id: dict[str, _Lot] = {}
    realized_by_key: dict[str, float] = {}

    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in books:
            books[key] = _PositionBook()

        if tx.action == "buy":
            lot = _Lot(
                transaction_id=tx.id,
                date=tx.date,
                quantity=tx.quantity,
                cost_per_unit=_cost_per_unit(tx),
                currency=tx.currency,
            )
            books[key].long_lots.append(lot)
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
                    r, _ = _close_long_lots(books[key].long_lots, remainder, tx.price, mult)
                    realized_by_key[key] += r
            else:
                r, _ = _close_long_lots(books[key].long_lots, tx.quantity, tx.price, mult)
                realized_by_key[key] = realized_by_key.get(key, 0.0) + r

    return books, realized_by_key


def _compute_lots_and_realized(
    transactions: list[TransactionRecord], method: str
) -> tuple[dict[str, _PositionBook], dict[str, float]]:
    if method == "average_cost":
        return _compute_average_cost(transactions)
    if method == "specific_lot":
        return _compute_specific_lot(transactions)
    return build_position_books(transactions)


# ── Public API ────────────────────────────────────────────────────────────────


def derive_positions_from_transactions(
    transactions: list[TransactionRecord],
    currencies: list[str] | None = None,
) -> PositionsFile:
    """Derive open positions with signed quantity (negative = short)."""
    books, _ = build_position_books(transactions)

    meta: dict[str, TransactionRecord] = {}
    for tx in sorted(transactions, key=lambda t: t.date):
        key = _instrument_key(tx)
        if key not in meta:
            meta[key] = tx

    positions: list[Position] = []
    for key, book in books.items():
        net = book.net_signed_qty
        if abs(net) <= 1e-9:
            continue
        tx_meta = meta[key]
        is_short = net < 0
        if tx_meta.position_type == "option":
            positions.append(
                Position(
                    ticker=tx_meta.ticker,
                    shares=round(net, 6),
                    currency=tx_meta.currency,
                    position_type="option",
                    option_type=tx_meta.option_type,
                    option_direction="SHORT" if is_short else "LONG",
                    strike=tx_meta.strike,
                    expiration=tx_meta.expiration,
                )
            )
        else:
            positions.append(
                Position(
                    ticker=tx_meta.ticker,
                    shares=round(net, 6),
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
    option_prices: dict[str, float] | None = None,
) -> PortfolioPnL:
    """Compute full portfolio P&L including short positions."""
    if method not in VALID_METHODS:
        logger.warning(f"Unknown cost basis method '{method}', falling back to FIFO")
        method = "fifo"

    books, realized_by_key = _compute_lots_and_realized(transactions, method)
    option_prices = option_prices or {}

    currency_by_key: dict[str, str] = {}
    pos_type_by_key: dict[str, str] = {}
    opt_meta_by_key: dict[str, TransactionRecord] = {}
    for tx in transactions:
        key = _instrument_key(tx)
        if key not in currency_by_key:
            currency_by_key[key] = tx.currency
            pos_type_by_key[key] = tx.position_type
            if tx.position_type == "option":
                opt_meta_by_key[key] = tx

    all_keys = sorted(set(realized_by_key) | set(books))

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

        book = books.get(key, _PositionBook())
        net_qty = book.net_signed_qty
        is_short = net_qty < -1e-9
        is_long = net_qty > 1e-9
        open_qty = net_qty  # signed

        # Cost basis / credit at open
        if is_long:
            total_lot_cost = sum(lot.cost_per_unit * lot.quantity for lot in book.long_lots)
            avg_cost = total_lot_cost / book.long_qty
            total_cost_native = avg_cost * book.long_qty * mult
        elif is_short:
            total_credit = sum(lot.cost_per_unit * lot.quantity for lot in book.short_lots)
            avg_cost = total_credit / book.short_qty
            total_cost_native = total_credit * mult  # premium received (positive)
        else:
            avg_cost = 0.0
            total_cost_native = 0.0

        # Current price lookup
        current_price: float | None = None
        if pos_type == "option":
            opt_tx = opt_meta_by_key.get(key)
            if opt_tx and opt_tx.option_type and opt_tx.strike and opt_tx.expiration:
                opt_key = f"{ticker}_{opt_tx.option_type}_{opt_tx.strike}_{opt_tx.expiration}"
                current_price = option_prices.get(opt_key)
        else:
            current_price = prices.get(ticker)

        current_value_native: Optional[float] = None
        unrealized_native: Optional[float] = None
        unrealized_pct: Optional[float] = None

        if current_price is not None and abs(net_qty) > 1e-9:
            if is_long:
                current_value_native = current_price * book.long_qty * mult
                unrealized_native = current_value_native - total_cost_native
            elif is_short:
                liability = current_price * book.short_qty * mult
                current_value_native = -liability
                unrealized_native = total_cost_native - liability
            if total_cost_native != 0:
                unrealized_pct = (unrealized_native / abs(total_cost_native)) * 100.0

        realized_native = realized_by_key.get(key, 0.0)

        total_pl_native: Optional[float] = None
        if unrealized_native is not None:
            total_pl_native = realized_native + unrealized_native
        elif abs(net_qty) <= 1e-9:
            total_pl_native = realized_native

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

        total_realized_disp += realized_disp
        if unrealized_disp is not None and total_unrealized_disp is not None:
            total_unrealized_disp += unrealized_disp
        else:
            total_unrealized_disp = None
        total_cost_basis_disp += cost_basis_disp
        if current_value_disp is not None and total_market_value_disp is not None:
            total_market_value_disp += current_value_disp
        else:
            total_market_value_disp = None

        def _r2(v: float | None) -> float | None:
            return round(v, 2) if v is not None else None

        opt_tx = opt_meta_by_key.get(key)
        direction = None
        if pos_type == "option":
            direction = "SHORT" if is_short else "LONG"
        label = ""
        if opt_tx:
            label = _option_label(opt_tx, direction=direction)

        position_list.append(
            PositionPnL(
                instrument_key=key,
                ticker=ticker,
                position_type=pos_type,
                option_label=label,
                option_direction=direction,
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
