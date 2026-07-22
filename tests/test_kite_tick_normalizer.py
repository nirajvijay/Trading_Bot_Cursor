from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from kite_tick_normalizer import normalize_kite_tick, to_tick_event
from tick_event import IST

_IST = ZoneInfo(IST)


def _sample_raw() -> dict:
    return {
        "instrument_token": 738561,
        "mode": "full",
        "last_price": 2500.5,
        "volume_traded": 1000,
        "last_traded_quantity": 10,
        "average_traded_price": 2499.0,
        "change": 0.5,
        "exchange_timestamp": datetime(2026, 7, 22, 10, 30, 0),
        "last_trade_time": datetime(2026, 7, 22, 10, 29, 55),
        "ohlc": {
            "open": 2490.0,
            "high": 2510.0,
            "low": 2485.0,
            "close": 2495.0,
        },
    }


class NormalizeKiteTickTests(unittest.TestCase):
    def test_valid_tick(self) -> None:
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        normalized = normalize_kite_tick(_sample_raw(), received_at=received_at)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized.instrument_token, 738561)
        self.assertEqual(normalized.last_price, 2500.5)
        self.assertEqual(normalized.volume_traded, 1000)
        self.assertEqual(normalized.ohlc.high, 2510.0)
        self.assertEqual(normalized.exchange_timestamp.tzinfo, _IST)
        self.assertEqual(normalized.received_at.tzinfo, _IST)

    def test_to_tick_event_assigns_sequence(self) -> None:
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        normalized = normalize_kite_tick(_sample_raw(), received_at=received_at)
        self.assertIsNotNone(normalized)
        event = to_tick_event(normalized, 7)
        self.assertEqual(event.sequence, 7)
        self.assertEqual(event.instrument_token, 738561)

    def test_missing_optional_fields_use_defaults(self) -> None:
        raw = {
            "instrument_token": 1,
            "last_price": 100.0,
        }
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        normalized = normalize_kite_tick(raw, received_at=received_at)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized.volume_traded, 0)
        self.assertEqual(normalized.ohlc.open, 100.0)

    def test_invalid_instrument_token(self) -> None:
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        self.assertIsNone(normalize_kite_tick({"last_price": 1.0}, received_at=received_at))

    def test_invalid_last_price(self) -> None:
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        self.assertIsNone(
            normalize_kite_tick(
                {"instrument_token": 1, "last_price": 0},
                received_at=received_at,
            )
        )

    def test_bad_ohlc_returns_none(self) -> None:
        received_at = datetime(2026, 7, 22, 10, 30, 1, tzinfo=_IST)
        raw = {
            "instrument_token": 1,
            "last_price": 10.0,
            "ohlc": {"open": "bad", "high": 1, "low": 1, "close": 1},
        }
        self.assertIsNone(normalize_kite_tick(raw, received_at=received_at))


if __name__ == "__main__":
    unittest.main()
