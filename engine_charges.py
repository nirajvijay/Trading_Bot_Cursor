"""Estimated Zerodha equity-intraday (MIS) charges for one round trip.

ARB's day P&L is net of charges, as the backtest's was, so the hard day stop,
the day lock and "was this trade a loser" all see the same number the strategy
was tested on. This is an estimate for those decisions only. The Charges tab
keeps using Kite's own contract-note figures (api/services/trade_charges.py).

The formula is the research one (Reference/arb_strategy_2026-09/code/charges.py),
checked against a Kite contract note: brokerage 0.03% capped at Rs 20 per leg,
STT 0.025% on the sell leg (rounded to the rupee), exchange turnover charge,
SEBI fee Rs 10 per crore, stamp duty 0.003% on the buy leg (rounded to the
rupee), and 18% GST on brokerage + exchange + SEBI.
"""
from __future__ import annotations

BROKERAGE_RATE = 0.0003
BROKERAGE_CAP_RUPEES = 20.0
STT_SELL_RATE = 0.00025
EXCHANGE_RATE = 0.0000307
SEBI_RATE = 10e-7
STAMP_BUY_RATE = 0.00003
GST_RATE = 0.18


def estimate_intraday_charges(buy_value: float, sell_value: float) -> float:
    """Total charges in rupees for a buy leg and a sell leg of these values."""
    buy_value = max(0.0, float(buy_value))
    sell_value = max(0.0, float(sell_value))
    brokerage = min(BROKERAGE_CAP_RUPEES, BROKERAGE_RATE * buy_value) + min(
        BROKERAGE_CAP_RUPEES, BROKERAGE_RATE * sell_value
    )
    stt = round(STT_SELL_RATE * sell_value)
    exchange = EXCHANGE_RATE * (buy_value + sell_value)
    sebi = SEBI_RATE * (buy_value + sell_value)
    stamp = round(STAMP_BUY_RATE * buy_value)
    gst = GST_RATE * (brokerage + exchange + sebi)
    return brokerage + stt + exchange + sebi + stamp + gst


def round_trip_charges(*, direction: str, qty: int, entry: float, exit_price: float) -> float:
    """Charges for opening at ``entry`` and closing at ``exit_price``.

    A long buys at entry and sells at exit; a short sells at entry and buys
    back at exit.
    """
    qty = int(qty or 0)
    if qty <= 0:
        return 0.0
    entry_value = qty * float(entry)
    exit_value = qty * float(exit_price)
    if str(direction).upper() == "UP":
        return estimate_intraday_charges(entry_value, exit_value)
    return estimate_intraday_charges(exit_value, entry_value)
