"""Private host-to-package credentials; never exposed as public MCP arguments."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class RequestCredentials:
    access_token: str
    developer_token: str | None = None


_credentials: ContextVar[RequestCredentials | None] = ContextVar(
    "google_ads_request_credentials", default=None
)


def current_credentials() -> RequestCredentials | None:
    return _credentials.get()


@contextmanager
def request_credentials(credentials: RequestCredentials):
    """Isolate a trusted host's identity for exactly one invocation."""
    token = _credentials.set(credentials)
    try:
        yield
    finally:
        _credentials.reset(token)
