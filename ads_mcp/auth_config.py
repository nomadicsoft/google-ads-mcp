"""Shared transport/authentication mode checks."""

import os


def oauth_configured() -> bool:
    """Fail closed for incomplete or explicitly required hosted OAuth."""
    client_id = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")
    required = os.environ.get("GOOGLE_ADS_MCP_REQUIRE_OAUTH", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if bool(client_id) != bool(client_secret):
        raise ValueError("Both Google OAuth client ID and secret are required.")
    if required and not (client_id and client_secret):
        raise ValueError("This deployment requires Google OAuth configuration.")
    return bool(client_id and client_secret)
