"""One-shot JSON bridge for hosts that already manage authentication.

This is a private subprocess protocol, not a network server or public MCP tool.
The host authorizes the caller and passes one request through stdin, then reads
one JSON response from stdout. No credential arguments or ambient ADC are used.
"""

import argparse
import contextlib
import importlib.metadata
import io
import json
import logging
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic import validate_call

from ads_mcp.execution_context import RequestCredentials, request_credentials

PROTOCOL_VERSION = 1
PACKAGE = "nomadicsoft-google-ads-mcp"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class CredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    access_token: SecretStr = Field(min_length=1, max_length=16384)
    developer_token: SecretStr | None = Field(default=None, max_length=4096)


class BridgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]
    tool: str
    arguments: dict[str, Any]
    credentials: CredentialsInput


def _tools():
    # Do not import coordinator/server: a host can have its own Google OAuth
    # environment, but the private bridge must never start that OAuth server.
    from ads_mcp.tools.core import list_accessible_customers
    from ads_mcp.tools.get_resource_metadata import get_resource_metadata
    from ads_mcp.tools.mutations import get_mutation_schema, mutate
    from ads_mcp.tools.search import search

    return {
        "customers_list_accessible_customers": list_accessible_customers,
        "metadata_get_resource_metadata": get_resource_metadata,
        "mutations_get_mutation_schema": get_mutation_schema,
        "mutations_mutate": mutate,
        "search_search": search,
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _restore_empty_messages(value: Any, descriptor) -> None:
    """PHP associative JSON decoding loses the distinction between {} and [].

    Restore only singular protobuf message fields, using the installed SDK's
    schema. Repeated fields and scalar values retain their original types.
    """
    if not isinstance(value, dict):
        return
    for field in descriptor.fields:
        if field.message_type is None:
            continue
        key = field.name if field.name in value else field.json_name
        if key not in value:
            continue
        child = value[key]
        if field.is_repeated:
            if isinstance(child, list):
                for item in child:
                    _restore_empty_messages(item, field.message_type)
        else:
            if child == []:
                value[key] = child = {}
            _restore_empty_messages(child, field.message_type)


def execute(payload: Any) -> dict[str, Any]:
    """Validate the host envelope before any credential or SDK resolution."""
    try:
        request = BridgeRequest.model_validate(payload)
    except ValidationError:
        # Never render validation errors for the envelope: input may be secret.
        return _error(
            "INVALID_REQUEST", "Invalid bridge request or credentials."
        )

    tools = _tools()
    if request.tool not in tools:
        return _error("UNKNOWN_TOOL", "Unsupported Google Ads tool.")
    access_token = request.credentials.access_token.get_secret_value()
    developer_token = (
        request.credentials.developer_token.get_secret_value()
        if request.credentials.developer_token
        else None
    )
    if not access_token.strip():
        return _error("INVALID_REQUEST", "A Google access token is required.")

    # Credential fields are accepted only in the trusted host envelope. Strict
    # function validation rejects unexpected public arguments, including auth.
    function = validate_call(config=ConfigDict(strict=True))(
        tools[request.tool]
    )
    from fastmcp.exceptions import ToolError
    from google.ads.googleads.errors import GoogleAdsException

    try:
        with request_credentials(
            RequestCredentials(access_token, developer_token)
        ):
            if request.tool == "mutations_mutate":
                from ads_mcp.utils import get_googleads_type

                operation = get_googleads_type("MutateOperation")
                descriptor = type(operation).pb().DESCRIPTOR
                operations = request.arguments.get("operations")
                if isinstance(operations, list):
                    for item in operations:
                        _restore_empty_messages(item, descriptor)
            result = function(**request.arguments)
        response = {"protocol_version": 1, "ok": True, "result": result}
    except ValidationError:
        response = _error(
            "INVALID_ARGUMENT", "Arguments do not match the tool schema."
        )
    except (ToolError, ValueError) as exc:
        response = _error("TOOL_ERROR", str(exc))
    except GoogleAdsException as exc:
        response = _error(
            "GOOGLE_ADS_ERROR", f"Google Ads request {exc.request_id} failed."
        )
    except Exception:
        # Network/client exceptions can contain Authorization headers. Do not
        # emit tracebacks, requests, tokens or raw SDK errors from this bridge.
        response = _error(
            "EXECUTION_FAILED",
            "Google Ads request failed. For a write, inspect account state before retrying; the outcome may be unknown.",
        )

    # Apply redaction to the serialized response too (including SDK ToolErrors).
    encoded = json.dumps(response, ensure_ascii=True)
    for secret in (access_token, developer_token):
        if secret:
            encoded = encoded.replace(json.dumps(secret)[1:-1], "[redacted]")
    if len(encoded.encode()) > MAX_OUTPUT_BYTES:
        return _error(
            "RESULT_TOO_LARGE",
            "Result exceeded the bridge limit. Reduce the query limit; do not retry an applied mutation blindly.",
        )
    return json.loads(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        print(
            json.dumps(
                {
                    "package": PACKAGE,
                    "version": importlib.metadata.version(PACKAGE),
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
        )
        return

    # The one-shot process owns its logging configuration. The host must never
    # receive a debug traceback containing tokens or an echoed input envelope.
    logging.disable(logging.CRITICAL)
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        response = _error("INVALID_REQUEST", "Bridge request is too large.")
    else:
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            response = _error(
                "INVALID_REQUEST", "Bridge input must be one JSON object."
            )
        else:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                try:
                    response = execute(payload)
                except Exception:
                    response = _error(
                        "EXECUTION_FAILED", "Google Ads bridge failed."
                    )
    print(json.dumps(response, ensure_ascii=True))


if __name__ == "__main__":
    main()
