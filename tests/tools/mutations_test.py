"""Exercise real SDK protobufs with the Google Ads network call mocked."""

import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.auth.credentials import AnonymousCredentials
from google.protobuf import json_format

from ads_mcp.tools import mutations


class MutationsTest(unittest.TestCase):
    def setUp(self):
        self.sdk = GoogleAdsClient(
            credentials=AnonymousCredentials(), use_proto_plus=True
        )
        self.service = MagicMock()
        self.service.mutate.return_value = self.sdk.get_type(
            "MutateGoogleAdsResponse"
        )
        self.sdk.get_service = MagicMock(return_value=self.service)
        self.client_patch = patch(
            "ads_mcp.utils._get_googleads_client", return_value=self.sdk
        )
        self.get_client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def update(self):
        return [
            {
                "campaign_operation": {
                    "update": {
                        "resource_name": "customers/1234567890/campaigns/42",
                        "status": "PAUSED",
                    },
                    "update_mask": "status",
                }
            }
        ]

    def test_default_validates_and_apply_preserves_mask_and_manager(self):
        result = mutations.mutate("123-456-7890", self.update())
        self.assertTrue(result["validate_only"])
        request = self.service.mutate.call_args.kwargs["request"]
        self.assertTrue(request.validate_only)
        self.assertFalse(request.partial_failure)
        self.assertEqual("1234567890", request.customer_id)
        self.assertEqual(
            ["status"],
            list(
                request.mutate_operations[
                    0
                ].campaign_operation.update_mask.paths
            ),
        )
        mutations.mutate(
            "1234567890",
            self.update(),
            validate_only=False,
            login_customer_id="999-888-7777",
        )
        self.get_client.assert_called_with(login_customer_id="9998887777")
        self.assertFalse(
            self.service.mutate.call_args.kwargs["request"].validate_only
        )
        self.assertIsNone(self.service.mutate.call_args.kwargs["retry"])

    def test_linked_campaign_adgroup_ad_and_keyword(self):
        prefix = "customers/1234567890"
        operations = [
            {
                "campaign_budget_operation": {
                    "create": {
                        "resource_name": f"{prefix}/campaignBudgets/-1",
                        "name": "Test budget",
                        "amount_micros": "10000000",
                        "delivery_method": "STANDARD",
                        "explicitly_shared": False,
                    }
                }
            },
            {
                "campaign_operation": {
                    "create": {
                        "resource_name": f"{prefix}/campaigns/-2",
                        "name": "Test search",
                        "status": "PAUSED",
                        "advertising_channel_type": "SEARCH",
                        "campaign_budget": f"{prefix}/campaignBudgets/-1",
                        "manual_cpc": {},
                        "contains_eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
                    }
                }
            },
            {
                "ad_group_operation": {
                    "create": {
                        "resource_name": f"{prefix}/adGroups/-3",
                        "name": "Test group",
                        "campaign": f"{prefix}/campaigns/-2",
                        "type": "SEARCH_STANDARD",
                        "status": "ENABLED",
                        "cpc_bid_micros": "1000000",
                    }
                }
            },
            {
                "ad_group_ad_operation": {
                    "create": {
                        "ad_group": f"{prefix}/adGroups/-3",
                        "status": "PAUSED",
                        "ad": {
                            "final_urls": ["https://example.com/"],
                            "responsive_search_ad": {
                                "headlines": [
                                    {"text": s}
                                    for s in [
                                        "Example one",
                                        "Example two",
                                        "Example three",
                                    ]
                                ],
                                "descriptions": [
                                    {"text": s}
                                    for s in [
                                        "First example description",
                                        "Second example description",
                                    ]
                                ],
                            },
                        },
                    }
                }
            },
            {
                "ad_group_criterion_operation": {
                    "create": {
                        "ad_group": f"{prefix}/adGroups/-3",
                        "status": "ENABLED",
                        "keyword": {"text": "example", "match_type": "EXACT"},
                    }
                }
            },
        ]
        mutations.mutate("1234567890", operations)
        request = self.service.mutate.call_args.kwargs["request"]
        self.assertEqual(len(request.mutate_operations), 5)
        self.assertEqual(
            request.mutate_operations[2].ad_group_operation.create.campaign,
            f"{prefix}/campaigns/-2",
        )
        self.assertEqual(
            request.mutate_operations[
                0
            ].campaign_budget_operation.create.amount_micros,
            10000000,
        )

    def test_remove_and_camel_case(self):
        mutations.mutate(
            "1234567890",
            [
                {
                    "campaignOperation": {
                        "remove": "customers/1234567890/campaigns/42"
                    }
                }
            ],
            validate_only=False,
        )
        request = self.service.mutate.call_args.kwargs["request"]
        self.assertEqual(
            request.mutate_operations[0].campaign_operation.remove,
            "customers/1234567890/campaigns/42",
        )

    def test_invalid_payloads_never_call_google(self):
        for operations in (
            [],
            [{}],
            [{"campaign_operation": {}}],
            [{"typo_operation": {}}],
            [{"campaign_operation": {"create": {"typo": True}}}],
            [{"campaign_operation": {"update": {"status": "NOT_A_STATUS"}}}],
            [
                {
                    "campaign_operation": {
                        "create": {},
                        "remove": "customers/1234567890/campaigns/42",
                    }
                }
            ],
        ):
            with (
                self.subTest(operations=operations),
                self.assertRaises(ValueError),
            ):
                mutations.mutate("1234567890", operations)
        self.service.mutate.assert_not_called()

    def test_invalid_account_ids_never_resolve_credentials(self):
        for customer_id in (
            "",
            "123",
            "oops1234567890",
            "customers/1234567890",
            "１２３４５６７８９０",
        ):
            with (
                self.subTest(customer_id=customer_id),
                self.assertRaises(ValueError),
            ):
                mutations.mutate(customer_id, self.update())
        self.get_client.assert_not_called()

    def test_partial_failure_preserves_positions_and_error_details(self):
        response = self.sdk.get_type("MutateGoogleAdsResponse")
        failure = self.sdk.get_type("GoogleAdsFailure")
        json_format.ParseDict(
            {
                "errors": [
                    {
                        "error_code": {
                            "campaign_error": "DUPLICATE_CAMPAIGN_NAME"
                        },
                        "message": "Duplicate",
                        "location": {
                            "field_path_elements": [
                                {"field_name": "mutate_operations", "index": 1}
                            ]
                        },
                    }
                ]
            },
            failure._pb,
        )
        response.partial_failure_error.code = 3
        response.partial_failure_error.message = "One failed operation"
        from google.protobuf.any_pb2 import Any

        detail = Any()
        detail.Pack(failure._pb)
        response.partial_failure_error.details.append(detail)
        response._pb.mutate_operation_responses.add().campaign_result.resource_name = (
            "customers/1234567890/campaigns/42"
        )
        response._pb.mutate_operation_responses.add()
        self.service.mutate.return_value = response
        result = mutations.mutate(
            "1234567890",
            self.update() * 2,
            partial_failure=True,
            validate_only=False,
        )
        self.assertTrue(
            self.service.mutate.call_args.kwargs["request"].partial_failure
        )
        self.assertEqual(
            result["response"]["mutate_operation_responses"][1], {}
        )
        errors = result["response"]["partial_failure_error"]["details"][0][
            "errors"
        ]
        self.assertEqual(
            errors[0]["location"]["field_path_elements"][0]["index"], 1
        )

    def test_google_error_is_mcp_error_with_request_id(self):
        failure = self.sdk.get_type("GoogleAdsFailure")
        failure._pb.errors.add().message = "Not authorized"
        self.service.mutate.side_effect = GoogleAdsException(
            MagicMock(), MagicMock(), failure, "request-123"
        )
        with self.assertRaisesRegex(ToolError, "request-123"):
            mutations.mutate("1234567890", self.update(), validate_only=False)
        self.service.mutate.assert_called_once()

    def test_schema_follows_nested_messages_and_enums(self):
        root = mutations.get_mutation_schema()
        self.assertIn("campaign_operation", [f["name"] for f in root["fields"]])
        schema = mutations.get_mutation_schema("campaignOperation.create")
        status = next(f for f in schema["fields"] if f["name"] == "status")
        self.assertIn("PAUSED", status["enum_values"])
        self.assertIn(".resources.Campaign", schema["message_type"])
        with self.assertRaises(ValueError):
            mutations.get_mutation_schema("campaign_operation.create.status")


class MutationMcpTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery_and_error_protocol(self):
        async with Client(mutations.mutations_mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            self.assertFalse(tools["mutate"].annotations.readOnlyHint)
            self.assertTrue(tools["mutate"].annotations.destructiveHint)
            self.assertFalse(tools["mutate"].annotations.idempotentHint)
            self.assertTrue(
                tools["get_mutation_schema"].annotations.readOnlyHint
            )
            response = await client.call_tool(
                "mutate",
                {"customer_id": "bad", "operations": []},
                raise_on_error=False,
            )
            self.assertTrue(response.is_error)
