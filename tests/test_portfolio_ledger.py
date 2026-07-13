"""Tests for signed portfolio ledger (long/short positions)."""

from datetime import date

from src.models import TransactionRecord
from src.portfolio_ledger import (
    CONTRACT_MULTIPLIER,
    build_position_books,
    compute_pnl,
    derive_positions_from_transactions,
)


def _equity_buy(ticker: str, qty: float, price: float = 100.0, d: str = "2024-01-01") -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="buy",
        quantity=qty,
        price=price,
        currency="USD",
    )


def _equity_sell(ticker: str, qty: float, price: float = 110.0, d: str = "2024-06-01") -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="sell",
        quantity=qty,
        price=price,
        currency="USD",
    )


def _short_call_open(
    ticker: str,
    qty: float,
    premium: float = 5.0,
    d: str = "2024-03-01",
) -> TransactionRecord:
    return TransactionRecord(
        date=date.fromisoformat(d),
        ticker=ticker,
        action="sell",
        quantity=qty,
        price=premium,
        currency="USD",
        position_type="option",
        option_type="CALL",
        option_direction="SHORT",
        strike=300.0,
        expiration="2026-06-20",
    )


class TestCoveredCall:
    def test_covered_call_registers_short_option(self):
        txs = [
            _equity_buy("TSLA", 100, d="2024-01-01"),
            _short_call_open("TSLA", 1, premium=5.0),
        ]
        positions = derive_positions_from_transactions(txs)
        tickers = {p.ticker: p for p in positions.positions}

        assert "TSLA" in tickers
        equity = next(p for p in positions.positions if p.position_type == "equity")
        assert equity.shares == 100

        short_call = next(p for p in positions.positions if p.position_type == "option")
        assert short_call.shares == -1
        assert short_call.option_direction == "SHORT"
        assert short_call.option_type == "CALL"

    def test_short_call_premium_accounting(self):
        """Sell call $5/contract × 1 = $500 credit, -1 contract."""
        txs = [_short_call_open("TSLA", 1, premium=50.0)]
        books, realized = build_position_books(txs)
        key = "TSLA__CALL_300.0_2026-06-20"
        book = books[key]
        assert book.short_qty == 1
        assert book.net_signed_qty == -1
        assert realized.get(key, 0) == 0  # no realized until close

        pnl = compute_pnl(
            txs,
            prices={"TSLA": 250.0},
            forex={"USD": 1.0},
            option_prices={"TSLA_CALL_300.0_2026-06-20": 30.0},
        )
        pos = next(p for p in pnl.positions if p.position_type == "option")
        assert pos.open_quantity == -1
        assert pos.option_direction == "SHORT"
        # Credit $5000, liability at $30 = $3000, unrealized gain $2000
        assert pos.total_cost_basis_native == 50.0 * 1 * CONTRACT_MULTIPLIER
        assert pos.unrealized_pl_native == (50.0 - 30.0) * CONTRACT_MULTIPLIER

    def test_short_call_expires_worthless(self):
        txs = [
            _short_call_open("TSLA", 1, premium=50.0),
            TransactionRecord(
                date=date.fromisoformat("2026-06-20"),
                ticker="TSLA",
                action="buy",
                quantity=1,
                price=0.0,
                currency="USD",
                position_type="option",
                option_type="CALL",
                option_direction="SHORT",
                strike=300.0,
                expiration="2026-06-20",
            ),
        ]
        books, realized = build_position_books(txs)
        key = "TSLA__CALL_300.0_2026-06-20"
        assert books[key].net_signed_qty == 0
        assert realized[key] == 50.0 * CONTRACT_MULTIPLIER


class TestEquityShort:
    def test_sell_more_than_owned_opens_short(self):
        txs = [
            _equity_buy("TSLA", 5),
            _equity_sell("TSLA", 20),
        ]
        positions = derive_positions_from_transactions(txs)
        tsla = next(p for p in positions.positions if p.ticker == "TSLA")
        assert tsla.shares == -15
