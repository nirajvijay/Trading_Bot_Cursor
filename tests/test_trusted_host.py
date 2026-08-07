"""TrustedHostMiddleware allowlist coverage."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.main import ALLOWED_HOSTS, app
from tests.auth_test_helpers import TEST_BASE_URL, make_test_client


class TrustedHostTests(unittest.TestCase):
    def test_allowlist_contents(self) -> None:
        self.assertEqual(
            ALLOWED_HOSTS,
            [
                "njtrading.website",
                "www.njtrading.website",
                "127.0.0.1",
                "localhost",
            ],
        )
        self.assertEqual(TEST_BASE_URL, "http://127.0.0.1")

    def test_allowed_hosts_return_health_200(self) -> None:
        for host in (
            "njtrading.website",
            "www.njtrading.website",
            "127.0.0.1",
            "localhost",
        ):
            with self.subTest(host=host):
                client = TestClient(app, base_url=f"http://{host}")
                res = client.get("/api/v1/health")
                self.assertEqual(res.status_code, 200, res.text)

    def test_evil_host_rejected(self) -> None:
        client = TestClient(app, base_url="http://evil.example")
        res = client.get("/api/v1/health")
        self.assertEqual(res.status_code, 400)

    def test_default_testserver_host_rejected(self) -> None:
        # Starlette TestClient defaults to Host: testserver when base_url is unset.
        client = TestClient(app)
        res = client.get("/api/v1/health")
        self.assertEqual(res.status_code, 400)

    def test_make_test_client_uses_loopback(self) -> None:
        client = make_test_client()
        res = client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
