"""Hosted sessions must never inherit the deployment's local Google identity."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ads_mcp.auth_config import oauth_configured
from ads_mcp import utils

OAUTH = {
    "GOOGLE_ADS_MCP_OAUTH_CLIENT_ID": "test-client",
    "GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET": "test-secret",
}


class HostedAuthTest(unittest.TestCase):
    def test_http_discovery_and_unauthenticated_request(self):
        from fastmcp import FastMCP
        from fastmcp.server.auth.providers.google import GoogleProvider
        from key_value.aio.stores.memory import MemoryStore
        from starlette.testclient import TestClient

        auth = GoogleProvider(
            client_id="test-client",
            client_secret="test-secret",
            base_url="https://ads.example.com",
            jwt_signing_key="x" * 64,
            client_storage=MemoryStore(),
            required_scopes=[utils._ADS_SCOPE],
        )
        server = FastMCP("test", auth=auth)
        with TestClient(
            server.http_app(), base_url="https://ads.example.com"
        ) as client:
            discovery = client.get("/.well-known/oauth-authorization-server")
            self.assertEqual(discovery.status_code, 200)
            self.assertEqual(
                discovery.json()["issuer"], "https://ads.example.com/"
            )
            resource = client.get("/.well-known/oauth-protected-resource/mcp")
            self.assertEqual(resource.status_code, 200)
            response = client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            self.assertEqual(response.status_code, 401)
            self.assertIn(
                "resource_metadata", response.headers["www-authenticate"]
            )

    def test_incomplete_or_missing_required_config_fails_closed(self):
        for env in (
            {"GOOGLE_ADS_MCP_OAUTH_CLIENT_ID": "id"},
            {"GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET": "secret"},
            {"GOOGLE_ADS_MCP_REQUIRE_OAUTH": "true"},
        ):
            with (
                self.subTest(env=env),
                patch.dict("os.environ", env, clear=True),
            ):
                with self.assertRaises(ValueError):
                    oauth_configured()

    @patch("google.auth.default")
    @patch("fastmcp.server.dependencies.get_access_token", return_value=None)
    def test_missing_request_token_cannot_use_adc(self, token, adc):
        with (
            patch.dict("os.environ", OAUTH, clear=True),
            self.assertRaises(RuntimeError),
        ):
            utils._create_credentials()
        adc.assert_not_called()

    @patch("google.auth.default")
    @patch("fastmcp.server.dependencies.get_access_token")
    def test_each_request_uses_its_own_google_token(self, token, adc):
        token.side_effect = [
            SimpleNamespace(token="user-one"),
            SimpleNamespace(token="user-two"),
        ]
        with patch.dict("os.environ", OAUTH, clear=True):
            first = utils._create_credentials()
            second = utils._create_credentials()
        self.assertEqual(first.token, "user-one")
        self.assertEqual(second.token, "user-two")
        adc.assert_not_called()

    @patch("google.auth.default", return_value=("local-credentials", None))
    @patch("fastmcp.server.dependencies.get_access_token", return_value=None)
    def test_stdio_keeps_local_credentials(self, token, adc):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(utils._create_credentials(), "local-credentials")
        adc.assert_called_once()
