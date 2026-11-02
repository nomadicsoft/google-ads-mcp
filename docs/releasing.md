# Releasing the NomadicSoft fork

The distribution is `nomadicsoft-google-ads-mcp`; the import namespace stays
`ads_mcp`. The first fork release is `0.1.0`. This package is independent of
Google's `google-ads-mcp` PyPI project. Keep the upstream Apache-2.0 license and
attribution when merging upstream changes.

## One-time setup

Before tagging a release, an owner must configure a [pending Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
on PyPI. The intended project name must be available; this repository does not
reserve it or claim that a release has already been published.

| Publisher field | Value |
| --- | --- |
| PyPI project | `nomadicsoft-google-ads-mcp` |
| GitHub owner | `nomadicsoft` |
| Repository | `google-ads-mcp` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

Create the `pypi` GitHub environment with those exact values. A reviewer and a
`v*` tag restriction are recommended. No long-lived PyPI token is required.
For rehearsals, register a separate pending publisher on TestPyPI and create a
`testpypi` GitHub environment; use the same fields except environment `testpypi`.
PyPI and TestPyPI do not share accounts or publisher settings.

## First release and later updates

1. Merge the packaging/bridge PR and verify CI.
2. Optionally run **Release** manually on the default branch. This publishes
   `0.1.0.dev<run-id>` to TestPyPI and never to production PyPI.
3. For production, ensure `project.version` is the intended unused version, then
   create and push an **annotated** tag matching it, initially `v0.1.0`:

   ```sh
   git tag -a v0.1.0 -m 'NomadicSoft Google Ads MCP 0.1.0'
   git push origin v0.1.0
   ```

4. The release workflow verifies the tag belongs to main, builds and checks the
   wheel/sdist, inspects an installed wheel and its bridge entry point, then
   publishes through OIDC. Approve the environment if protection requires it.
5. Install the published version in a clean environment and verify:

   ```sh
   python -m pip install 'nomadicsoft-google-ads-mcp==0.1.0'
   nomadicsoft-google-ads-bridge --version
   ```

6. Deploy AtmosCompute with its matching `requirements/google-ads-mcp.txt` pin.
   Future upstream merges get a new fork version and a separate AtmosCompute PR
   updating that pin. Do not make production follow a Git branch or `latest`.

The old `google-ads-mcp` command remains as a compatibility alias. The fork's
explicit commands are `nomadicsoft-google-ads-mcp` (standalone MCP server) and
`nomadicsoft-google-ads-bridge` (private one-shot host integration). Avoid
installing both distributions into the same Python environment because both
provide the `ads_mcp` namespace.
