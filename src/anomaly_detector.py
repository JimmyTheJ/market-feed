"""Ledger anomaly detection.

Scans transaction histories for inconsistencies such as:
  - Selling more shares/contracts than held (oversell)
  - Selling without any prior buy (possible short sale or missing leg)
  - Open options past their expiration date (worthless expiry not recorded)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .models import Anomaly, AnomalyScanResultWithFixes, SuggestedFix, TransactionRecord
from .portfolio_ledger import CONTRACT_MULTIPLIER, _instrument_key, _Lot

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class _InstrumentState:
    """Running position state while replaying transactions."""

    lots: deque[_Lot]
    net_qty: float = 0.0
    position_type: str = "equity"
    option_type: Optional[str] = None
    option_direction: Optional[str] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None
    currency: str = "USD"


def _multiplier(position_type: str) -> int:
    return CONTRACT_MULTIPLIER if position_type == "option" else 1


def _option_label(ticker: str, option_type: str | None, strike: float | None, expiration: str | None) -> str:
    parts = [ticker]
    if strike is not None:
        parts.append(f"${strike:.0f}")
    if option_type:
        parts.append(option_type)
    if expiration:
        parts.append(f"exp {expiration}")
    return " ".join(parts)


def _replay_instrument(
    transactions: list[TransactionRecord],
    account_id: str | None = None,
    account_name: str | None = None,
) -> tuple[list[Anomaly], dict[str, _InstrumentState]]:
    """Replay transactions chronologically and collect per-instrument anomalies."""
    anomalies: list[Anomaly] = []
    states: dict[str, _InstrumentState] = {}

    for tx in sorted(transactions, key=lambda t: (t.date, t.id)):
        key = _instrument_key(tx)
        if key not in states:
            states[key] = _InstrumentState(
                lots=deque(),
                position_type=tx.position_type,
                option_type=tx.option_type,
                option_direction=tx.option_direction,
                strike=tx.strike,
                expiration=tx.expiration,
                currency=tx.currency,
            )
        state = states[key]
        # Keep latest metadata (direction may change across legs)
        state.option_direction = tx.option_direction or state.option_direction
        state.expiration = tx.expiration or state.expiration

        if tx.action == "buy":
            mult = _multiplier(tx.position_type)
            comm_per_share = tx.commission / (tx.quantity * mult) if tx.quantity else 0.0
            cost_per_unit = tx.price + comm_per_share
            state.lots.append(
                _Lot(
                    transaction_id=tx.id,
                    date=tx.date,
                    quantity=tx.quantity,
                    cost_per_unit=cost_per_unit,
                    currency=tx.currency,
                )
            )
            state.net_qty += tx.quantity
        elif tx.action == "sell":
            available = sum(lot.quantity for lot in state.lots)
            if available <= 1e-9:
                # No long position to sell from
                is_short_open = (
                    tx.position_type == "option"
                    and (tx.option_direction or "").upper() == "SHORT"
                )
                if is_short_open:
                    anomalies.append(
                        Anomaly(
                            severity="info",
                            code="SHORT_OPEN",
                            message=(
                                f"Sell of {tx.quantity} {tx.ticker} on {tx.date} opens a "
                                f"short option position (no prior buy)."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "quantity": tx.quantity,
                                "date": tx.date.isoformat(),
                                "position_type": tx.position_type,
                            },
                        )
                    )
                    state.net_qty -= tx.quantity
                else:
                    anomalies.append(
                        Anomaly(
                            severity="warning",
                            code="SELL_WITHOUT_BUY",
                            message=(
                                f"Sell of {tx.quantity} {tx.ticker} on {tx.date} has no "
                                f"matching buy — missing purchase data or possible short sale."
                            ),
                            instrument_key=key,
                            ticker=tx.ticker,
                            transaction_id=tx.id,
                            account_id=account_id,
                            account_name=account_name,
                            details={
                                "quantity": tx.quantity,
                                "date": tx.date.isoformat(),
                                "position_type": tx.position_type,
                                "may_be_short": tx.position_type == "equity",
                            },
                        )
                    )
                    state.net_qty -= tx.quantity
            elif tx.quantity > available + 1e-9:
                unmatched = tx.quantity - available
                label = tx.ticker
                if tx.position_type == "option":
                    label = _option_label(tx.ticker, tx.option_type, tx.strike, tx.expiration)
                anomalies.append(
                    Anomaly(
                        severity="error",
                        code="OVERSELL",
                        message=(
                            f"Sell of {tx.quantity} {label} on {tx.date} exceeds open "
                            f"position of {available:.4g} by {unmatched:.4g}."
                        ),
                        instrument_key=key,
                        ticker=tx.ticker,
                        transaction_id=tx.id,
                        account_id=account_id,
                        account_name=account_name,
                        details={
                            "sell_quantity": tx.quantity,
                            "available_quantity": available,
                            "unmatched_quantity": unmatched,
                            "date": tx.date.isoformat(),
                            "may_be_short": unmatched,
                        },
                    )
                )
                # Match what we can
                remaining = tx.quantity
                while remaining > 0 and state.lots:
                    lot = state.lots[0]
                    match_qty = min(remaining, lot.quantity)
                    lot.quantity -= match_qty
                    remaining -= match_qty
                    if lot.quantity <= 1e-9:
                        state.lots.popleft()
                state.net_qty -= tx.quantity
            else:
                remaining = tx.quantity
                while remaining > 0 and state.lots:
                    lot = state.lots[0]
                    match_qty = min(remaining, lot.quantity)
                    lot.quantity -= match_qty
                    remaining -= match_qty
                    if lot.quantity <= 1e-9:
                        state.lots.popleft()
                state.net_qty -= tx.quantity

    return anomalies, states


def _detect_expired_options(
    states: dict[str, _InstrumentState],
    as_of: date,
    account_id: str | None = None,
    account_name: str | None = None,
) -> tuple[list[Anomaly], list[SuggestedFix]]:
    """Flag open option positions past expiration and suggest $0 close trades."""
    anomalies: list[Anomaly] = []
    fixes: list[SuggestedFix] = []

    for key, state in states.items():
        if state.position_type != "option":
            continue
        open_qty = sum(lot.quantity for lot in state.lots)
        if open_qty <= 1e-9:
            continue
        if not state.expiration:
            continue

        try:
            exp_date = date.fromisoformat(state.expiration)
        except ValueError:
            continue

        if exp_date >= as_of:
            continue

        ticker = key.split("__")[0]
        label = _option_label(ticker, state.option_type, state.strike, state.expiration)
        total_cost = sum(lot.cost_per_unit * lot.quantity for lot in state.lots)
        mult = _multiplier("option")
        loss_native = total_cost * mult  # premium paid, now worthless

        anomalies.append(
            Anomaly(
                severity="warning",
                code="EXPIRED_OPTION_OPEN",
                message=(
                    f"{open_qty} contract(s) of {label} expired on {state.expiration} "
                    f"but remain open — should be closed at $0 (loss ≈ "
                    f"{loss_native:.2f} {state.currency})."
                ),
                instrument_key=key,
                ticker=ticker,
                account_id=account_id,
                account_name=account_name,
                details={
                    "open_quantity": open_qty,
                    "expiration": state.expiration,
                    "estimated_loss_native": round(loss_native, 2),
                    "currency": state.currency,
                },
            )
        )

        # LONG options: sell at $0 to close. SHORT options: buy at $0 to close.
        direction = (state.option_direction or "LONG").upper()
        if direction == "SHORT":
            fix = TransactionRecord(
                date=exp_date,
                ticker=ticker,
                action="buy",
                quantity=open_qty,
                price=0.0,
                currency=state.currency,
                position_type="option",
                option_type=state.option_type,
                option_direction="SHORT",
                strike=state.strike,
                expiration=state.expiration,
                notes="Auto-closed: option expired worthless",
            )
        else:
            fix = TransactionRecord(
                date=exp_date,
                ticker=ticker,
                action="sell",
                quantity=open_qty,
                price=0.0,
                currency=state.currency,
                position_type="option",
                option_type=state.option_type,
                option_direction="LONG",
                strike=state.strike,
                expiration=state.expiration,
                notes="Auto-closed: option expired worthless",
            )
        fixes.append(
            SuggestedFix(
                code="EXPIRED_OPTION_CLOSE",
                account_id=account_id,
                account_name=account_name,
                transaction=fix,
                message=f"Close {open_qty} contract(s) of {label} at $0 on {state.expiration}",
            )
        )

    return anomalies, fixes


def detect_anomalies(
    transactions: list[TransactionRecord],
    *,
    profile: str = "",
    account_id: str | None = None,
    account_name: str | None = None,
    as_of: date | None = None,
) -> AnomalyScanResultWithFixes:
    """Scan a transaction list for ledger anomalies."""
    as_of = as_of or date.today()
    replay_anomalies, states = _replay_instrument(
        transactions, account_id=account_id, account_name=account_name
    )
    expired_anomalies, suggested_fixes = _detect_expired_options(
        states, as_of, account_id=account_id, account_name=account_name
    )

    all_anomalies = replay_anomalies + expired_anomalies
    all_anomalies.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), a.code, a.ticker or ""))

    error_count = sum(1 for a in all_anomalies if a.severity == "error")
    warning_count = sum(1 for a in all_anomalies if a.severity == "warning")
    info_count = sum(1 for a in all_anomalies if a.severity == "info")

    return AnomalyScanResultWithFixes(
        scanned_at=datetime.now(),
        profile=profile,
        account_id=account_id,
        scope="account" if account_id else "profile",
        anomaly_count=len(all_anomalies),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        anomalies=all_anomalies,
        suggested_fixes=suggested_fixes,
    )


def detect_profile_anomalies(
    profile: str,
    account_transactions: dict[str, list[TransactionRecord]],
    account_names: dict[str, str] | None = None,
    *,
    as_of: date | None = None,
    per_account: bool = True,
) -> AnomalyScanResultWithFixes:
    """Scan all accounts in a profile, optionally per-account and aggregated."""
    account_names = account_names or {}
    as_of = as_of or date.today()
    all_anomalies: list[Anomaly] = []
    all_fixes: list[SuggestedFix] = []

    if per_account:
        for acct_id, txs in account_transactions.items():
            if not txs:
                continue
            result = detect_anomalies(
                txs,
                profile=profile,
                account_id=acct_id,
                account_name=account_names.get(acct_id),
                as_of=as_of,
            )
            all_anomalies.extend(result.anomalies)
            all_fixes.extend(result.suggested_fixes)

    # Also scan aggregated view (cross-account position mismatches)
    combined = []
    for txs in account_transactions.values():
        combined.extend(txs)
    if len(account_transactions) > 1 and combined:
        agg = detect_anomalies(combined, profile=profile, as_of=as_of)
        seen_tx_ids = {a.transaction_id for a in all_anomalies if a.transaction_id}
        for a in agg.anomalies:
            if a.code == "OVERSELL" and a.transaction_id in seen_tx_ids:
                continue
            if a.code == "OVERSELL":
                a.message = f"[All accounts combined] {a.message}"
                a.account_id = None
                a.account_name = "All accounts"
                all_anomalies.append(a)

    # Deduplicate fixes by account + instrument
    seen_fix_keys: set[str] = set()
    unique_fixes: list[SuggestedFix] = []
    for fix in all_fixes:
        tx = fix.transaction
        fix_key = (
            f"{fix.account_id}|{tx.ticker}|{tx.option_type}|{tx.strike}|"
            f"{tx.expiration}|{tx.action}"
        )
        if fix_key not in seen_fix_keys:
            seen_fix_keys.add(fix_key)
            unique_fixes.append(fix)

    all_anomalies.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), a.code, a.ticker or ""))
    error_count = sum(1 for a in all_anomalies if a.severity == "error")
    warning_count = sum(1 for a in all_anomalies if a.severity == "warning")
    info_count = sum(1 for a in all_anomalies if a.severity == "info")

    return AnomalyScanResultWithFixes(
        scanned_at=datetime.now(),
        profile=profile,
        account_id=None,
        scope="profile",
        anomaly_count=len(all_anomalies),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        anomalies=all_anomalies,
        suggested_fixes=unique_fixes,
    )


def preview_import_anomalies(
    new_transactions: list[TransactionRecord],
    existing_transactions: list[TransactionRecord],
) -> list[Anomaly]:
    """Detect anomalies that would appear if new transactions were merged."""
    combined = existing_transactions + new_transactions
    result = detect_anomalies(combined)
    return result.anomalies
