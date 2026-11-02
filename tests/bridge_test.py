"""Private host bridge contract; no Google network calls or live mutations."""

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp import bridge, utils
from ads_mcp.execution_context import current_credentials


def envelope(
    tool="customers_list_accessible_customers",
    arguments=None,
    token="access-secret",
):
    return {
        "protocol_version": 1,
        "tool": tool,
        "arguments": arguments or {},
        "credentials": {
            "access_token": token,
            "developer_token": "developer-secret",
        },
    }


class BridgeTest(unittest.TestCase):
    def test_rejects_missing_credentials_and_untrusted_envelope_fields(self):
        for payload in (
            None,
            {},
            {**envelope(), "protocol_version": 2},
            {**envelope(), "team_id": 2},
            {**envelope(), "credentials": {}},
            {**envelope(), "credentials": {"access_token": " "}},
        ):
            self.assertFalse(bridge.execute(payload)["ok"])
        self.assertEqual(
            "UNKNOWN_TOOL", bridge.execute(envelope("shell"))["error"]["code"]
        )

    def test_rejects_credentials_in_public_tool_arguments(self):
        result = bridge.execute(envelope(arguments={"access_token": "other"}))
        self.assertEqual("INVALID_ARGUMENT", result["error"]["code"])
        self.assertIsNone(current_credentials())

    def test_host_credentials_override_ambient_settings_without_adc(self):
        def check():
            self.assertEqual("access-secret", utils._create_credentials().token)
            self.assertEqual("developer-secret", utils._get_developer_token())
            self.assertIsNone(utils._get_login_customer_id(None))
            return ["1234567890"]

        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_ADS_DEVELOPER_TOKEN": "ambient",
                    "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "9999999999",
                },
            ),
            patch(
                "google.auth.default", side_effect=AssertionError("ADC used")
            ),
            patch.object(
                bridge,
                "_tools",
                return_value={"customers_list_accessible_customers": check},
            ),
        ):
            self.assertEqual(
                ["1234567890"], bridge.execute(envelope())["result"]
            )
        self.assertIsNone(current_credentials())

    def test_missing_developer_token_does_not_fall_back_to_host_environment(
        self,
    ):
        def check():
            self.assertIsNone(utils._get_developer_token())
            return []

        request = envelope()
        del request["credentials"]["developer_token"]
        with (
            patch.dict(os.environ, {"GOOGLE_ADS_DEVELOPER_TOKEN": "ambient"}),
            patch.object(
                bridge,
                "_tools",
                return_value={"customers_list_accessible_customers": check},
            ),
        ):
            self.assertTrue(bridge.execute(request)["ok"])

    def test_concurrent_identities_do_not_cross_and_context_is_reset(self):
        barrier = threading.Barrier(2)

        def check():
            token = current_credentials().access_token
            barrier.wait(timeout=5)
            self.assertEqual(token, current_credentials().access_token)
            return [token[-1]]

        with (
            patch.object(
                bridge,
                "_tools",
                return_value={"customers_list_accessible_customers": check},
            ),
            concurrent.futures.ThreadPoolExecutor(2) as pool,
        ):
            results = list(
                pool.map(
                    lambda token: bridge.execute(envelope(token=token)),
                    ["secret-A", "secret-B"],
                )
            )
        self.assertEqual(
            [["A"], ["B"]], [result["result"] for result in results]
        )
        self.assertIsNone(current_credentials())

    def test_errors_are_redacted_and_unknown_exceptions_are_sanitized(self):
        for exception in (
            ToolError("access-secret developer-secret"),
            RuntimeError("Authorization: access-secret developer-secret"),
        ):

            def fail():
                raise exception

            with patch.object(
                bridge,
                "_tools",
                return_value={"customers_list_accessible_customers": fail},
            ):
                result = bridge.execute(envelope())
            self.assertFalse(result["ok"])
            self.assertNotIn("access-secret", json.dumps(result))
            self.assertNotIn("developer-secret", json.dumps(result))
            self.assertIsNone(current_credentials())

    def test_result_limit_is_explicit(self):
        def large():
            return ["x" * 1000]

        with (
            patch.object(bridge, "MAX_OUTPUT_BYTES", 200),
            patch.object(
                bridge,
                "_tools",
                return_value={"customers_list_accessible_customers": large},
            ),
        ):
            self.assertEqual(
                "RESULT_TOO_LARGE", bridge.execute(envelope())["error"]["code"]
            )

    def test_php_empty_message_normalization_and_atomic_validation_default(
        self,
    ):
        sdk = GoogleAdsClient(
            credentials=AnonymousCredentials(), use_proto_plus=True
        )
        service = MagicMock()
        service.mutate.return_value = sdk.get_type("MutateGoogleAdsResponse")
        sdk.get_service = MagicMock(return_value=service)
        operation = {
            "campaign_operation": {
                "create": {
                    "name": "Paused campaign",
                    "status": "PAUSED",
                    "advertising_channel_type": "SEARCH",
                    "manual_cpc": [],
                }
            }
        }
        with patch("ads_mcp.utils._get_googleads_client", return_value=sdk):
            result = bridge.execute(
                envelope(
                    "mutations_mutate",
                    {
                        "customer_id": "1234567890",
                        "operations": [operation],
                    },
                )
            )
        self.assertTrue(result["ok"], result)
        request = service.mutate.call_args.kwargs["request"]
        self.assertTrue(request.validate_only)
        self.assertFalse(request.partial_failure)
        self.assertTrue(
            request.mutate_operations[0].campaign_operation.create._pb.HasField(
                "manual_cpc"
            )
        )
        self.assertIsNone(service.mutate.call_args.kwargs["retry"])

    def test_string_false_is_not_an_apply_request(self):
        result = bridge.execute(
            envelope(
                "mutations_mutate",
                {
                    "customer_id": "1234567890",
                    "operations": [{}],
                    "validate_only": "false",
                },
            )
        )
        self.assertEqual("INVALID_ARGUMENT", result["error"]["code"])

    def test_cli_emits_exactly_one_json_response_without_oauth_bootstrap(self):
        environment = {
            **os.environ,
            "GOOGLE_CLIENT_ID": "host-client",
            "GOOGLE_CLIENT_SECRET": "host-secret",
            "FASTMCP_CHECK_FOR_UPDATES": "off",
        }
        result = subprocess.run(
            [sys.executable, "-m", "ads_mcp.bridge"],
            input=json.dumps(envelope("mutations_get_mutation_schema")),
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)
        response = json.loads(result.stdout)
        self.assertTrue(response["ok"], response)
        self.assertNotIn("secret", result.stdout)

    def test_cli_rejects_invalid_json_and_identifies_distribution(self):
        result = subprocess.run(
            [sys.executable, "-m", "ads_mcp.bridge"],
            input="not-json",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            "INVALID_REQUEST", json.loads(result.stdout)["error"]["code"]
        )
        version = subprocess.run(
            [sys.executable, "-m", "ads_mcp.bridge", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(bridge.PACKAGE, json.loads(version.stdout)["package"])
        self.assertEqual(1, json.loads(version.stdout)["protocol_version"])


if __name__ == "__main__":
    unittest.main()
