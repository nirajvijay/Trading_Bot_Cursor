"""Fresh quote fixture for historical lifecycle regressions.

These suites predate signal freshness and intentionally use historical/invalid
signal timestamps. They exercise order lifecycle, not entry admission. WP18's
integration suite uses the real cycle and strict admission without this fixture.
"""
from trading_engine_cycle import TradingEngineCycle as RealCycle
from trading_engine_quotes import EntryLimitDecision, TouchQuote
from trading_engine_broker import FakeBroker


class TradingEngineCycle(RealCycle):
    def enforce_daily_loss(self):
        # Historical unit fixtures do not supply liquidation quotes / cost profiles.
        # WP110 exercises complete daily-loss accounting with the real cycle.
        return None

    def _revalidate_postfill(self, trade):
        # Older execution-accounting regressions intentionally inject overfills.
        # The breach response is independently exercised with the real cycle in WP19.
        return None

    def _entry_limit_decision(self, trade):
        price = trade.entry_estimate
        if isinstance(self.broker, FakeBroker):
            price = self.broker.last_prices.get(trade.symbol, price)
            self.broker.touch_quotes[trade.symbol] = TouchQuote(price, price, self._now_ist().isoformat())
        return EntryLimitDecision(price, None, 0, 0)
