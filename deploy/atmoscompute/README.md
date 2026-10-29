# Host Google Ads MCP alongside AtmosCompute

This is a standalone Python service, using Google's SDK and upstream Google
OAuth flow. AtmosCompute provides its setup/catalog page; ChatGPT, Corcava and
other MCP clients connect directly to this service. It does not proxy through
Laravel or share the AtmosCompute Passport token. Each client signs in with
Google and receives access to the accounts that Google user can access.

`https://google-ads-mcp.atmoscompute.com/mcp` is a **proposed URL**, not a
provisioned service. This change does not create DNS, deploy a container, or
register an OAuth application.

## Deploy

1. Provision a dedicated HTTPS hostname on the AtmosCompute host. A separate
   origin keeps Google's MCP OAuth discovery separate from Laravel Passport's
   `/.well-known/oauth-authorization-server`.
2. Enable the Google Ads API in the Google Cloud project. Configure a Google
   OAuth **Web application** client and consent screen with the Ads scope
   `https://www.googleapis.com/auth/adwords` plus `openid`, `email`, `profile`.
   Use the exact authorized redirect URI
   `https://YOUR_MCP_HOST/auth/callback`. Configure test users while the consent
   app is in testing; complete Google's applicable verification before wider use.
3. Copy `.env.example` to `.env` in this directory. Set the public **origin**
   (without `/mcp`), OAuth credentials and two different persistent secrets.
   Generate each secret with `openssl rand -hex 32`; store them in the deployment
   secret manager and restrict `.env` permissions with `chmod 600 .env`. Set
   `GOOGLE_ADS_DEVELOPER_TOKEN` when required by the project's Ads API setup.
   Do not mount ADC/service-account credentials or configure a shared default
   manager customer ID. Clients supply their manager ID when necessary.
4. From this directory run `docker compose up -d --build`. The service binds
   to loopback port 18080. Add `nginx.conf.example` to the hostname's TLS virtual
   host, test Nginx configuration and reload. Proxy **all paths** on this origin,
   including discovery, registration, authorization, token and callback routes.
   Preserve Authorization, MCP session/version headers, streaming and redirects.
5. Set `GOOGLE_ADS_MCP_URL=https://YOUR_MCP_HOST/mcp` in the AtmosCompute Laravel
   deployment and rebuild its cached configuration. Its Google Ads setup page
   is shown only on the English deployment when this URL is configured.
6. Add that same `/mcp` URL as an OAuth MCP connection in the consuming client.
   Authenticate with Google; no AtmosCompute API key is needed. Select writable
   tools explicitly in clients that use a tool allowlist. Reuse the endpoint
   from Corcava instead of running another copy of this server.

## Verify before announcing availability

- OAuth authorization-server and protected-resource discovery are reachable.
  An unauthenticated POST to `/mcp` must return 401, never expose tools or ADC.
- Sign in from the intended client; list accessible customers and query a known
  account. Supply `login_customer_id` for manager-account access as needed.
- Call `mutations_mutate` with `validate_only=true` against a Google Ads test
  account, then apply an explicitly authorized PAUSED test campaign. Inspect
  the account and resource names returned. No live campaign is created by this
  repository's automated tests.
- Test a second Google user and a token refresh/reconnect after a container
  restart. Confirm each user sees only their own Google-granted accounts.

## Operations

This Compose deployment runs one instance. Preserve the `oauth-data` volume
and both secrets across updates; back up encrypted storage and keys separately.
Do not run `docker compose down -v` during upgrades. Rotating/deleting signing
or encryption keys invalidates sessions and can require users to reconnect.
For multiple replicas, use upstream's shared Redis/Firestore storage, identical
keys and a routing strategy compatible with stateful MCP sessions; do not scale
this local-volume Compose deployment horizontally.

Mutation tools are enabled in this fork. For an intentionally read-only
deployment, mount a tools config with `namespaces.mutations: false` and set
`GOOGLE_ADS_MCP_TOOLS_CONFIG` to its path. Google OAuth itself has no read-only
Ads scope. Google remains the authority for account permissions.

Updates: build a reviewed commit, preserve the volume/secrets, restart, and run
the checks above. Roll back to the previous image if needed. Mutation calls do
not retry automatically; after a timeout inspect the account before repeating
creates. Keep error responses/request IDs for troubleshooting, but never log
OAuth tokens or callback query strings.
