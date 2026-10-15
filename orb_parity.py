"""Post-market ORB parity: did the live detector see what the backtest would?

Read-only. Once a session's 1-minute candles are in the historical DB, this
rebuilds that morning the way the backtest does (research orb.py: opening
range from candles, top three by RVOL, first bar to touch the level) and
compares it with what the live detector recorded from ticks:

* the opening range of every stock: open, high, low, close, volume;
* which stocks were armed, in which direction, at which level;
* when each armed stock broke out, and at what price.

It also answers the two things the live range has to assume about Kite's
candles: that the 09:15 candle's volume leaves out the pre-open auction, and
that its open is the day's official open. Both show up directly in the report.

Nothing here places orders or simulates an account.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from orb_baselines import load_orb_baselines
from orb_config import DEFAULT_ORB_CONFIG, OrbConfig
from orb_rules import MinuteBar, first_breakout_bar, range_from_candles, rank_ranges
from orb_types import DECISION_TRIGGERED, DIRECTION_UP

# A stock is worth listing when either side puts it near the volume line.
NEAR_RVOL = 2.0
PRICE_TOLERANCE = 1e-6


class ParityError(Exception):
    """The comparison cannot be made (yet). The message says why."""


def _connect(path: Path, what: str) -> sqlite3.Connection:
    if not Path(path).exists():
        raise ParityError(f"{what} not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _same(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= PRICE_TOLERANCE


def _hm(stamp: Optional[str]) -> Optional[str]:
    return None if not stamp else str(stamp)[11:16]


def _stats(values: Sequence[float]) -> Optional[Dict[str, float]]:
    if not values:
        return None
    return {
        "min": round(min(values), 4),
        "median": round(float(median(values)), 4),
        "max": round(max(values), 4),
    }


def _load_live(live_db: Path, session_date: str, rule_version: str) -> Dict[str, Any]:
    conn = _connect(live_db, "live db")
    try:
        try:
            ranges = conn.execute(
                "SELECT * FROM live_orb_ranges WHERE session_date = ? AND orb_rule_version = ?",
                (session_date, rule_version),
            ).fetchall()
            arms = conn.execute(
                """
                SELECT a.tradingsymbol, a.direction, a.level, a.rank, a.rvol, a.atr14,
                       d.decision_type, d.reason, d.trigger_exchange_ts, d.trigger_price,
                       d.planned_stop
                FROM live_orb_arms a
                LEFT JOIN live_orb_decisions d
                  ON d.setup_id = a.setup_id AND d.orb_rule_version = a.orb_rule_version
                WHERE a.session_date = ? AND a.orb_rule_version = ?
                ORDER BY a.rank ASC
                """,
                (session_date, rule_version),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ParityError(f"the live db has no ORB tables ({exc})") from exc
    finally:
        conn.close()
    if not ranges:
        raise ParityError(f"the detector recorded no ranking for {session_date}")
    return {"ranges": ranges, "arms": arms}


def _load_bars(historical_db: Path, tokens: Dict[int, str], session_date: str) -> Dict[int, List[MinuteBar]]:
    next_day = (date.fromisoformat(session_date) + timedelta(days=1)).isoformat()
    conn = _connect(historical_db, "historical db")
    out: Dict[int, List[MinuteBar]] = {}
    try:
        for token in tokens:
            try:
                rows = conn.execute(
                    "SELECT candle_time, open, high, low, close, volume FROM candles "
                    "WHERE instrument_token = ? AND candle_time >= ? AND candle_time < ? "
                    "ORDER BY candle_time ASC",
                    (token, session_date, next_day),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise ParityError(f"the historical db has no candles table ({exc})") from exc
            out[token] = [
                MinuteBar(str(r[0])[11:16], float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]))
                for r in rows
            ]
    finally:
        conn.close()
    return out


def compare_day(
    *,
    live_db: Path,
    historical_db: Path,
    session_date: str,
    config: OrbConfig = DEFAULT_ORB_CONFIG,
) -> Dict[str, Any]:
    """The full comparison for one session, as plain data."""
    live = _load_live(live_db, session_date, config.rule_version)
    live_ranges = {str(r["tradingsymbol"]): r for r in live["ranges"]}
    tokens = {int(r["instrument_token"]): str(r["tradingsymbol"]) for r in live["ranges"]}
    bars = _load_bars(historical_db, tokens, session_date)
    with_candles = sum(1 for b in bars.values() if b)
    if with_candles == 0:
        raise ParityError(
            f"no {session_date} candles in the historical db yet: collect history first"
        )
    baselines = load_orb_baselines(historical_db, session_date, tokens, config=config)

    replay_ranges = [
        range_from_candles(
            instrument_token=token,
            tradingsymbol=symbol,
            baseline=baselines.get(token),
            bars=bars.get(token, []),
            config=config,
        )
        for token, symbol in sorted(tokens.items(), key=lambda item: item[1])
    ]
    replay_ranked = rank_ranges(replay_ranges, config)
    replay_by_symbol = {entry.range.tradingsymbol: entry for entry in replay_ranked}

    # ---- every stock's opening range --------------------------------------
    compared = 0
    equal = {"open": 0, "high": 0, "low": 0, "close": 0, "direction": 0, "baseline": 0}
    ratio_net: List[float] = []      # live volume (auction excluded) / candle volume
    ratio_gross: List[float] = []    # live volume with the auction added back / candle volume
    differences: List[Dict[str, Any]] = []
    for symbol, row in sorted(live_ranges.items()):
        entry = replay_by_symbol.get(symbol)
        if entry is None or row["or_volume"] is None or entry.range.or_volume is None:
            continue
        candle = entry.range
        if row["or_open"] is None or candle.or_open is None or not candle.or_volume:
            continue
        compared += 1
        same = {
            "open": _same(row["or_open"], candle.or_open),
            "high": _same(row["or_high"], candle.or_high),
            "low": _same(row["or_low"], candle.or_low),
            "close": _same(row["or_close"], candle.or_close),
            "direction": (row["direction"] or None) == (candle.direction or None),
            "baseline": _same(row["atr14"], candle.atr14)
            and _same(row["avg_or_volume"], candle.avg_or_volume),
        }
        for key, ok in same.items():
            equal[key] += bool(ok)
        net = float(row["or_volume"]) / float(candle.or_volume)
        ratio_net.append(net)
        if row["preopen_volume"] is not None:
            ratio_gross.append(
                (float(row["or_volume"]) + float(row["preopen_volume"])) / float(candle.or_volume)
            )
        live_rvol = row["rvol"]
        near = max(float(live_rvol or 0.0), float(candle.rvol or 0.0)) >= NEAR_RVOL
        if near and not all(same[k] for k in ("high", "low", "direction")):
            differences.append(
                {
                    "symbol": symbol,
                    "live": {"open": row["or_open"], "high": row["or_high"], "low": row["or_low"],
                             "close": row["or_close"], "direction": row["direction"], "rvol": live_rvol},
                    "candles": {"open": candle.or_open, "high": candle.or_high, "low": candle.or_low,
                                "close": candle.or_close, "direction": candle.direction,
                                "rvol": candle.rvol},
                }
            )

    # ---- which stocks were armed ------------------------------------------
    live_arms = [
        {"symbol": str(a["tradingsymbol"]), "direction": str(a["direction"]),
         "level": float(a["level"]), "rvol": float(a["rvol"]), "rank": int(a["rank"])}
        for a in live["arms"]
    ]
    replay_arms = [
        {"symbol": e.range.tradingsymbol, "direction": e.range.direction,
         "level": e.range.level, "rvol": e.range.rvol, "rank": e.rank}
        for e in replay_ranked
        if e.selected
    ]
    live_set = [a["symbol"] for a in live_arms]
    replay_set = [a["symbol"] for a in replay_arms]
    replay_arm_by_symbol = {a["symbol"]: a for a in replay_arms}

    # ---- what happened to each armed stock --------------------------------
    token_of = {symbol: token for token, symbol in tokens.items()}
    breakouts: List[Dict[str, Any]] = []
    for arm_row in live["arms"]:
        symbol = str(arm_row["tradingsymbol"])
        replay_arm = replay_arm_by_symbol.get(symbol)
        item: Dict[str, Any] = {
            "symbol": symbol,
            "live": {
                "decision": arm_row["decision_type"] or "ARMED",
                "reason": arm_row["reason"],
                "minute": _hm(arm_row["trigger_exchange_ts"]),
                "price": arm_row["trigger_price"],
                "level": float(arm_row["level"]),
                "direction": str(arm_row["direction"]),
            },
            "replay": None,
            "same_arm": False,
            "same_outcome": None,
            "minutes_apart": None,
            "price_vs_replay_bps": None,
        }
        if replay_arm is not None:
            item["same_arm"] = replay_arm["direction"] == str(arm_row["direction"]) and _same(
                replay_arm["level"], arm_row["level"]
            )
            hit = first_breakout_bar(
                bars.get(token_of[symbol], []),
                direction=str(replay_arm["direction"]),
                level=float(replay_arm["level"]),
                config=config,
            )
            item["replay"] = {
                "level": replay_arm["level"],
                "direction": replay_arm["direction"],
                "minute": None if hit is None else hit[0].time,
                "price": None if hit is None else hit[1],
            }
            live_triggered = arm_row["decision_type"] == DECISION_TRIGGERED
            item["same_outcome"] = live_triggered == (hit is not None)
            if live_triggered and hit is not None and arm_row["trigger_price"] is not None:
                live_minute = _hm(arm_row["trigger_exchange_ts"])
                if live_minute:
                    item["minutes_apart"] = _minutes(live_minute) - _minutes(hit[0].time)
                sign = 1.0 if replay_arm["direction"] == DIRECTION_UP else -1.0
                # Positive: the live trigger price was worse than the backtest's entry.
                item["price_vs_replay_bps"] = round(
                    (float(arm_row["trigger_price"]) - float(hit[1])) * sign / float(hit[1]) * 1e4, 2
                )
        breakouts.append(item)

    same_selection = live_set == replay_set
    same_arms = same_selection and all(b["same_arm"] for b in breakouts)
    return {
        "session_date": session_date,
        "stocks_compared": compared,
        "stocks_with_candles": with_candles,
        "equal": equal,
        "volume_ratio_live_to_candles": _stats(ratio_net),
        "volume_ratio_with_auction_added_back": _stats(ratio_gross),
        "range_differences_near_the_line": differences,
        "live_arms": live_arms,
        "replay_arms": replay_arms,
        "same_selection": same_selection,
        "same_arms": same_arms,
        "breakouts": breakouts,
        "same_outcomes": all(b["same_outcome"] for b in breakouts if b["same_outcome"] is not None),
    }


def _minutes(hm: str) -> int:
    return int(hm[:2]) * 60 + int(hm[3:5])


def format_report(result: Dict[str, Any]) -> str:
    """The comparison as text for a terminal."""
    n = result["stocks_compared"]
    eq = result["equal"]
    lines = [
        f"ORB parity for {result['session_date']}: {n} stocks compared "
        f"({result['stocks_with_candles']} with candles)",
        "",
        "Opening range, live ticks vs Kite candles (stocks that agree):",
        f"  open {eq['open']}/{n}   high {eq['high']}/{n}   low {eq['low']}/{n}   "
        f"close {eq['close']}/{n}   direction {eq['direction']}/{n}   baseline {eq['baseline']}/{n}",
    ]
    net, gross = result["volume_ratio_live_to_candles"], result["volume_ratio_with_auction_added_back"]
    if net:
        lines.append(
            f"  volume, live / candles: median {net['median']} (min {net['min']}, max {net['max']})"
            "   <- 1.0 means the live range counts volume as Kite's candles do"
        )
    if gross:
        lines.append(
            f"  with the pre-open auction added back: median {gross['median']} "
            f"(min {gross['min']}, max {gross['max']})"
        )
    lines.append("")

    def arms(items: List[Dict[str, Any]]) -> str:
        if not items:
            return "none"
        return ", ".join(
            f"{a['symbol']} {a['direction']} {a['level']:.2f} ({a['rvol']:.2f}x)" for a in items
        )

    lines.append(f"Armed live:    {arms(result['live_arms'])}")
    lines.append(f"Armed replay:  {arms(result['replay_arms'])}")
    lines.append(
        "  -> " + ("same stocks, directions and levels" if result["same_arms"]
                   else "same stocks, different level or direction" if result["same_selection"]
                   else "DIFFERENT stocks")
    )
    lines.append("")
    if result["breakouts"]:
        lines.append("Breakouts:")
    for b in result["breakouts"]:
        live, replay = b["live"], b["replay"]
        left = f"live {live['decision']}"
        if live["minute"]:
            left += f" {live['minute']}"
        if live["price"] is not None:
            left += f" @ {float(live['price']):.2f}"
        if replay is None:
            right = "not armed in the replay"
        elif replay["minute"] is None:
            right = "replay: no breakout"
        else:
            right = f"replay {replay['minute']} @ {float(replay['price']):.2f}"
        extra = ""
        if b["price_vs_replay_bps"] is not None:
            extra = f"   live price {b['price_vs_replay_bps']:+.1f} bps vs the backtest entry"
        flag = "" if b["same_outcome"] in (True, None) and (replay is None or b["same_arm"]) else "   <- differs"
        lines.append(f"  {b['symbol']:<12} {left:<34} {right}{extra}{flag}")
    differences = result["range_differences_near_the_line"]
    if differences:
        lines += ["", "Ranges that differ, near the volume line:"]
        for d in differences:
            live, candles = d["live"], d["candles"]
            lines.append(
                f"  {d['symbol']:<12} live {live['direction']} {live['low']}-{live['high']} "
                f"({(live['rvol'] or 0):.2f}x)   candles {candles['direction']} "
                f"{candles['low']}-{candles['high']} ({(candles['rvol'] or 0):.2f}x)"
            )
    return "\n".join(lines)
