"""Removed console endpoints must remain unavailable, including to the owner."""
import unittest
from tests.auth_test_helpers import AuthTestHarness


class RemovedAdminApiTests(unittest.TestCase):
    def test_admin_routes_are_unavailable(self):
        with AuthTestHarness() as h:
            for authenticated in (False, True):
                if authenticated:
                    h.login()
                for method, path in (
                    ('GET', '/config'), ('PATCH', '/config'),
                    ('POST', '/trading/pause'), ('POST', '/trading/resume'),
                    ('GET', '/audit'), ('POST', '/config/rollback'),
                ):
                    with self.subTest(authenticated=authenticated, method=method, path=path):
                        response = h.client.request(
                            method, '/api/v1/admin' + path,
                            headers=h.csrf_headers() if authenticated else {},
                        )
                        self.assertEqual(response.status_code, 404, response.text)
