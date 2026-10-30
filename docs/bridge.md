# Embedding the package in an existing application

Install `nomadicsoft-google-ads-mcp==0.1.0` in a dedicated virtual environment.
Run `python -m ads_mcp.bridge` for each tool call. This process has no HTTP
listener and exits after returning one JSON response. The application owns
public MCP transport, OAuth, team authorization, encrypted refresh tokens,
write permission and auditing. PHP is a host language, not a package dependency.

AtmosCompute's main repo implements this host at `/mcp/google-ads`, reusing its
existing Google OAuth client. Corcava can call that endpoint or independently
embed the same Python package. Do not point the AI client at this private bridge.

## Protocol 1

Send exactly one UTF-8 JSON object on stdin, then close stdin:

```json
{
  "protocol_version": 1,
  "tool": "customers_list_accessible_customers",
  "arguments": {},
  "credentials": {
    "access_token": "SHORT_LIVED_GOOGLE_ACCESS_TOKEN",
    "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN"
  }
}
```

Credentials belong to the trusted host envelope; they must not appear in public
tool schemas or user-supplied arguments. Never pass refresh tokens, client
secrets or credentials in process arguments. The bridge never falls back to
ambient Google credentials or a global manager ID. Pass `login_customer_id`
explicitly in tool arguments when an account requires manager access.

Supported tool names and arguments are the same as the Python functions:

- `customers_list_accessible_customers`: no arguments.
- `metadata_get_resource_metadata`: `resource_name`, optional `login_customer_id`.
- `search_search`: `customer_id`, `fields`, `resource`, optional `conditions`,
  `orderings`, `limit`, `login_customer_id`. Hosts should set a finite limit.
- `mutations_get_mutation_schema`: optional dotted `path`.
- `mutations_mutate`: `customer_id`, `operations`, optional `validate_only`
  (default true), `partial_failure` (default false), `login_customer_id`.

Success is `{"protocol_version":1,"ok":true,"result":...}`. A tool or protocol
failure is `{"protocol_version":1,"ok":false,"error":{"code":"...","message":"..."}}`.
`result` preserves the native tool result, including partial mutation failures.
Inspect every mutation result; `ok` does not imply all operations succeeded.
No applied mutation is automatically retried. If the process times out or fails
without a result, the write may have succeeded: inspect account state first.

The bridge accepts at most 4 MiB of input and returns at most 16 MiB. Hosts must
also enforce process/output/time limits, discard stderr and avoid logging the
input envelope. Known credentials are redacted from output. PHP-decoded empty
arrays at singular protobuf message fields are restored to empty objects using
the installed SDK descriptor; repeated fields retain their original shape.

`nomadicsoft-google-ads-bridge --version` returns the distribution name, version
and protocol version without contacting Google. This can verify installation
before switching an application release into service.
