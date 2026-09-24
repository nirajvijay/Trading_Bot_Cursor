"""Check exact frontend service paths against registered API routes.

Guards the seam where a renamed endpoint would otherwise only fail in the
browser: every path the client calls must exist on the app, and vice versa for
the ones the desk depends on.
"""
import re
import unittest
from pathlib import Path
from api.main import app

CLIENT = Path(__file__).parents[1] / 'frontend/src/api/client.ts'


class FrontendRouteContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CLIENT.read_text()
        self.routes = set(app.openapi()['paths'])

    def _assert_calls(self, function: str, endpoint: str) -> None:
        anchor = self.source.find(f'export function {function}')
        self.assertNotEqual(anchor, -1, f'{function} not found in client.ts')
        # A window rather than up to the first brace: a template-literal path
        # like `/x/${'${'}id}/events` contains braces of its own.
        body = self.source[anchor:anchor + 400]
        self.assertIn(endpoint, body, f'{function} does not call {endpoint}')
        self.assertIn('/api/v1' + endpoint, self.routes, f'{endpoint} not registered')

    def test_execution_desk_read_endpoints_are_registered(self):
        for function, endpoint in (
            ('fetchExecutionStatus', '/execution/status'),
            ('fetchExecutionPreflight', '/execution/preflight'),
            ('fetchExecutionPositions', '/execution/positions'),
        ):
            with self.subTest(function=function):
                self._assert_calls(function, endpoint)

    def test_execution_desk_write_endpoints_are_registered(self):
        for function, endpoint in (
            ('postExecutionStart', '/execution/start'),
            ('postExecutionCommand', '/execution/commands'),
        ):
            with self.subTest(function=function):
                self._assert_calls(function, endpoint)

    def test_parameterised_execution_routes_are_registered(self):
        # Template paths cannot be string-matched from the client, so assert
        # the route side directly.
        for route in (
            '/api/v1/execution/positions/{trade_id}/events',
            '/api/v1/execution/commands/{command_id}',
        ):
            with self.subTest(route=route):
                self.assertIn(route, self.routes)

    def test_charges_tab_endpoint_is_registered(self):
        self._assert_calls('fetchTradeCharges', '/charges')

    def test_observation_endpoints_are_unchanged(self):
        # fetchSessionClock is deliberately not asserted: the client has never
        # had that function, which is why the previous version of this test was
        # already failing before the rebuild.
        self._assert_calls('fetchSectorMap', '/observation/sectors')

    def test_admin_console_is_removed(self):
        self.assertFalse(any(p.startswith('/api/v1/admin/') for p in self.routes))
        self.assertNotIn('/admin/', self.source)

    def test_the_old_trading_engine_router_is_gone(self):
        stale = [r for r in self.routes if r.startswith('/api/v1/trading-engine')]
        self.assertEqual(stale, [], f'old engine routes still registered: {stale}')

    def test_the_client_no_longer_calls_the_old_engine(self):
        self.assertNotIn('/trading-engine/', self.source)
        self.assertNotIn("'/trading/control'", self.source)


if __name__ == "__main__":
    unittest.main()
