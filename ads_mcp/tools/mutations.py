"""Google Ads batch mutations, using the installed SDK's protobuf contract."""

import json
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api import field_behavior_pb2
from google.protobuf import descriptor_pb2, json_format
from mcp.types import ToolAnnotations

from ads_mcp import utils
from ads_mcp.mcp_header_interceptor import MCPHeaderInterceptor

mutations_mcp = FastMCP("mutations")


def _customer_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9]{10}|[0-9]{3}-[0-9]{3}-[0-9]{4})", value):
        raise ValueError(
            "Customer IDs must contain 10 digits (dashes optional)."
        )
    return value.replace("-", "")


@mutations_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_mutation_schema(path: str = "") -> dict[str, Any]:
    """Inspect mutation fields and enums for the installed Google Ads API version.

    With an empty path, lists all supported MutateOperation types. Follow field
    paths for details, e.g. 'campaign_operation', 'campaign_operation.create',
    or 'ad_group_ad_operation.create.ad.responsive_search_ad'. Both snake_case
    and lowerCamelCase field names work. Message fields can be inspected with
    a further path. This is protobuf metadata; Google still validates business
    rules and required combinations. FieldMask values use a comma-separated
    string of lowerCamelCase paths, e.g. 'status,name' (not a paths object).
    """
    descriptor = utils.get_googleads_type("MutateOperation")._pb.DESCRIPTOR
    if path:
        for name in path.split("."):
            field = next(
                (f for f in descriptor.fields if name in (f.name, f.json_name)),
                None,
            )
            if field is None or field.message_type is None:
                raise ValueError(
                    f"Not a message field in mutation path: {name}"
                )
            descriptor = field.message_type

    fields = []
    for field in descriptor.fields:
        item: dict[str, Any] = {
            "name": field.name,
            "json_name": field.json_name,
            "type": descriptor_pb2.FieldDescriptorProto.Type.Name(field.type),
            "repeated": field.is_repeated,
        }
        if field.message_type:
            item["message_type"] = field.message_type.full_name
            if field.message_type.full_name == "google.protobuf.FieldMask":
                item["json_format"] = "comma-separated lowerCamelCase paths"
        if field.enum_type:
            item["enum_values"] = [v.name for v in field.enum_type.values]
        if field.containing_oneof:
            item["oneof"] = field.containing_oneof.name
        behaviors = field.GetOptions().Extensions[
            field_behavior_pb2.field_behavior
        ]
        if behaviors:
            item["behavior"] = [
                field_behavior_pb2.FieldBehavior.Name(value)
                for value in behaviors
            ]
        fields.append(item)
    return {
        "message_type": descriptor.full_name,
        "path": path,
        "fields": fields,
    }


@mutations_mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def mutate(
    customer_id: str,
    operations: list[dict[str, Any]],
    validate_only: bool = True,
    partial_failure: bool = False,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Create, update or remove Google Ads resources in one batch.

    Supports campaigns, budgets, ad groups, ads, keywords, assets and all other
    operation types listed by get_mutation_schema. Each operations item is a
    protobuf-JSON MutateOperation, e.g. {"campaign_operation": {"update":
    {"resource_name": "customers/1234567890/campaigns/42", "status": "PAUSED"},
    "update_mask": "status"}}. Unknown fields and multiple oneof choices fail.

    Defaults to validation only: set validate_only=false to APPLY the batch.
    Changes can spend money or remove resources. Use only the user's authorized
    customer/account and changes. Create campaigns PAUSED unless launching was
    authorized. Customer IDs are 10 digits, optionally dashed. Manager access
    uses login_customer_id; it is not the target customer_id.

    The default partial_failure=false makes the batch atomic. For independent
    operations only, partial_failure=true allows some to succeed; inspect the
    returned partial_failure_error and indexed results before any retry. Use
    negative temporary resource IDs to link new objects, defining parents
    before their children. Update masks use comma-separated lowerCamelCase
    paths. int64 amounts are in micros and may be supplied as decimal strings.

    No automatic retry is made. After an ambiguous timeout, read the account
    before retrying; creates are not idempotent. Validation is not a reservation
    or guarantee that a later apply will succeed.
    """
    customer_id = _customer_id(customer_id)
    if login_customer_id is not None:
        login_customer_id = _customer_id(login_customer_id)
    if not operations or len(operations) > 10000:
        raise ValueError("Provide between 1 and 10000 mutate operations.")

    client = utils._get_googleads_client(login_customer_id=login_customer_id)
    request = client.get_type("MutateGoogleAdsRequest")
    try:
        json_format.ParseDict(
            {
                "customer_id": customer_id,
                "mutate_operations": operations,
                "validate_only": validate_only,
                "partial_failure": partial_failure,
            },
            request._pb,
            ignore_unknown_fields=False,
        )
    except (json_format.ParseError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid mutation payload: {exc}") from exc

    # ParseDict accepts empty messages. Reject those before sending an RPC.
    for index, operation in enumerate(request.mutate_operations):
        selected = operation._pb.WhichOneof("operation")
        if not selected:
            raise ValueError(f"Operation {index} has no operation type.")
        body = getattr(operation, selected)._pb
        if (
            "operation" in body.DESCRIPTOR.oneofs_by_name
            and not body.WhichOneof("operation")
        ):
            raise ValueError(
                f"Operation {index} has no create, update or remove."
            )

    service = client.get_service(
        "GoogleAdsService", interceptors=[MCPHeaderInterceptor()]
    )
    try:
        response = service.mutate(request=request, retry=None, timeout=90)
    except GoogleAdsException as exc:
        error = {
            "request_id": exc.request_id,
            "failure": json_format.MessageToDict(
                exc.failure._pb, preserving_proto_field_name=True
            ),
        }
        raise ToolError(json.dumps(error)) from exc

    result = json_format.MessageToDict(
        response._pb, preserving_proto_field_name=True
    )
    return {
        "customer_id": customer_id,
        "validate_only": validate_only,
        "partial_failure": partial_failure,
        "operation_count": len(operations),
        "response": result,
    }
