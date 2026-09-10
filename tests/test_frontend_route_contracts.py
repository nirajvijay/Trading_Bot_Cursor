"""Check exact frontend service paths against registered API routes."""
import re
import unittest
from pathlib import Path
from api.main import app


class FrontendRouteContracts(unittest.TestCase):
    def test_new_owner_read_endpoints_are_registered(self):
        source = (Path(__file__).parents[1]/'frontend/src/api/client.ts').read_text()
        routes = set(app.openapi()['paths'])
        for function,endpoint in (
            ('fetchTradingControl','/trading-engine/control'),
            ('fetchTradingSetups','/trading-engine/setups'),
            ('fetchSectorMap','/observation/sectors'),
            ('fetchSessionClock','/observation/session-clock'),
        ):
            match = re.search(r'export function '+function+r'\([^)]*\)\s*\{([^\n]+)',source)
            self.assertIsNotNone(match,function)
            self.assertIn("'"+endpoint+"'",match.group(1))
            self.assertIn('/api/v1'+endpoint,routes)
        self.assertIn('/api/v1/trading-engine/trades/{trade_id}/audit',routes)
        self.assertIn('/api/v1/trading-engine/report',routes)
