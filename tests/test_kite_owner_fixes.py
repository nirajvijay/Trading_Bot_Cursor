"""Owner Kite setup: scoped password confirmation and durable token status."""
import sqlite3
import unittest
from unittest.mock import patch
import pyotp

from api.auth.web_auth_store import get_web_auth_store
from api.services import token_check_cache as cache
from session_quality import load_session_minutes
from tests.auth_test_helpers import AuthTestHarness


class KiteOwnerFixTests(unittest.TestCase):
    @patch('api.routers.auth.build_authorize_url_with_state', return_value='https://kite.zerodha.com/connect/login')
    def test_password_only_kite_does_not_grant_admin_step_up(self, url):
        with AuthTestHarness() as h:
            store = get_web_auth_store()
            secret = pyotp.random_base32()
            store.set_mfa_pending(secret)
            store.confirm_mfa(secret)
            h.login(totp=pyotp.TOTP(secret).now())
            result = h.client.post('/api/v1/auth/kite/start', json={'password': h.password}, headers=h.csrf_headers())
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(h.client.get('/api/v1/account/me').json()['step_up_active'])
            # General step-up must still demand MFA, even after Kite confirmation.
            general = h.client.post('/api/v1/account/step-up', json={'password': h.password}, headers=h.csrf_headers())
            self.assertEqual(general.status_code, 401)

    @patch('api.routers.auth.build_authorize_url_with_state')
    def test_kite_password_csrf_and_rate_limit(self, url):
        with AuthTestHarness() as h:
            h.login()
            missing_csrf = h.client.post('/api/v1/auth/kite/start', json={'password': h.password})
            self.assertEqual(missing_csrf.status_code, 403)
            for _ in range(5):
                bad = h.client.post('/api/v1/auth/kite/start', json={'password': 'wrong'}, headers=h.csrf_headers())
                self.assertEqual(bad.status_code, 403)
            limited = h.client.post('/api/v1/auth/kite/start', json={'password': h.password}, headers=h.csrf_headers())
            self.assertEqual(limited.status_code, 429)
            url.assert_not_called()

    @patch('api.routers.auth.check_access_token_details', return_value=(True, 'ok', 'AB1234'))
    def test_status_survives_reads_but_not_token_change_or_day_change(self, check):
        with AuthTestHarness() as h, patch.object(cache, 'token_identity', return_value='token-A') as identity, patch('api.routers.auth.token_identity', return_value='token-A'):
            h.login()
            self.assertEqual(h.client.post('/api/v1/auth/check-token', headers=h.csrf_headers()).status_code, 200)
            for _ in range(2):
                status = h.client.get('/api/v1/auth/status').json()
                self.assertTrue(status['token_valid'])
                self.assertIsNotNone(status['token_checked_at'])
                self.assertNotIn('token_identity', status)
            identity.return_value = 'token-B'
            self.assertIsNone(h.client.get('/api/v1/auth/status').json()['token_valid'])
            identity.return_value = 'token-A'
            with patch.object(cache, '_today_ist', return_value='2099-01-01'):
                self.assertIsNone(cache.current_token_check())
            cache.write_token_check(valid=False, identity='token-A')
            self.assertFalse(h.client.get('/api/v1/auth/status').json()['token_valid'])

    def test_session_minutes_range_is_indexed_and_preserves_day(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE candles(instrument_token INTEGER, candle_time TEXT, PRIMARY KEY(instrument_token,candle_time))')
        conn.executemany('INSERT INTO candles VALUES(?,?)', [
            (1, '2026-09-08T09:15:00+05:30'), (1, '2026-09-09 09:15:00+05:30'),
            (1, '2026-09-09T15:00:00+05:30'), (1, '2026-09-10T09:15:00+05:30')])
        self.assertEqual(set(load_session_minutes(conn, 1, '2026-09-09')), {555, 900})
        plan = str(conn.execute('EXPLAIN QUERY PLAN SELECT candle_time FROM candles WHERE instrument_token=? AND candle_time>=? AND candle_time<?', (1,'2026-09-09','2026-09-09~')).fetchall())
        self.assertIn('candle_time>?', plan)


if __name__ == '__main__':
    unittest.main()
