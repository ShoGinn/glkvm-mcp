# Security and operational boundaries

This server can operate the connected machine with the KVM account's privileges.
Use it only on authorized targets, restrict access to the MCP host, and confirm
the connected target before input. `human_activity` is a heuristic, not a lock
or an access-control mechanism. The default server transport is stdio; no remote
MCP hosting or authentication deployment is provided here.

## Current limitations

- The driver disables TLS certificate verification for its device HTTP client.
  HTTPS therefore does not authenticate the device against an attacker. Use a
  trusted, restricted network; do not expose the device to an untrusted network.
  Verified TLS configuration is not currently implemented.
- Authentication tokens are stored in `~/.cache/glkvm-mcp/<GLKVM_HOST>.token.json`
  and sent in request query parameters and headers. Cache files use the process
  umask rather than enforced private permissions. Protect the account and cache
  directory, use a restrictive umask, and avoid logging authenticated URLs.
  The cache is keyed by host, not the full base URL or user; use separate server
  home directories when isolating multiple identities for the same host.
- `GLKVM_ALLOW_MSD` disables the named MSD wrappers by default. It does not block
  raw API access: `kvmd_api` permits POSTs to `/api` paths, including MSD endpoints.
  Treat access to the server as device control, not a restricted read-only role.
- OCR runs locally, but text results and screenshots are delivered to the MCP
  client and may enter its logs or model context. `echo_text=false` removes text
  only from the typing result; arguments can still be recorded.
- Saved frames remain on the server host without automatic expiration. Protect
  and remove them according to your data-retention requirements. A frame hash
  and local receipt time do not verify physical target identity or device time.
- Sanitized errors apply to observation capture/authentication and refresh-auth
  responses; raw API responses and legacy MSD errors can contain device data.
  MSD streaming uses separate code paths from the shared request deadline and
  retry logic, including authentication retries. Do not assume the shared
  request limits apply to file transfers.

## Reporting a vulnerability

Do not include secrets, tokens, authenticated URLs, screenshots, or customer data
in a public issue. Use the fork's private vulnerability reporting facility if
GitHub offers it, or an established private channel with the fork maintainer.
Fork issues are currently disabled. If no private route is available, do not
publish vulnerability details in a pull request; first establish private contact
with the maintainer. There is no published response-time guarantee or
supported-version matrix. For an upstream issue, coordinate disclosure with
upstream before publishing sensitive details.
