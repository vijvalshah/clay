# Preliminary failure-path observations (NOT Phase 2 results)

Date: 2026-11-13 (UTC). Environment: Claude Code cloud sandbox, Node and Python versions in `env.txt`.

These runs were made because the sandbox's network policy blocks the servers' real
upstreams, so the Phase 2 protocol (healthy baseline against the real API, then faults)
could not run. What follows observes only how each server's configured read-only tool
responds when its upstream fails. None of it counts toward the Phase 2 decision rule:
there is no real healthy baseline, and the 401/502 runs use a local stub, not the real API.

Server versions: see `phase2/setup.sh` (pinned). Tool and arguments per server: `phase2/servers.json`.

## A. Upstream unreachable (dead local proxy, dummy credentials)

Evidence: `network_down_dead_proxy.json`, script `failpath.py`.

| Server | Tool | Response type | isError | Text (abridged) |
|---|---|---|---|---|
| ms365-calendar (@softeria 0.184.2) | list-calendar-events | result | true | `{"error":"fetch failed"}` |
| ms365-mail (@softeria 0.184.2) | list-mail-messages | result | true | `{"error":"fetch failed"}` |
| sentry (0.49.0) | find_organizations | result | true | "Configuration Error ... Unable to connect to https://sentry.io/api/0/organizations/... Connection refused" |
| context7 (4.7.0) | resolve-library-id | result | true | "Error searching libraries: TypeError: fetch failed" |
| fetch (2026.11.11) | fetch | result | true | "Failed to fetch robots.txt ... due to a connection issue" |
| azure-devops (2.11.0) | core_list_projects | result | true | "Error fetching projects: tunneling socket could not be established, cause=connect ECONNREFUSED" |
| notion (2.6.1) | API-get-users | JSON-RPC error -32603 | n/a | "connect ECONNREFUSED 127.0.0.1:..." |
| clarity (2.0.1) | query-analytics-dashboard | result | not set | "An error occurred while fetching the data." |
| lokka (2.1.2) | Lokka-Microsoft | server exited during startup (MSAL network_error) | n/a | tool never reached |

Azure DevOps was first run with `--authentication env`, which selects the Azure credential
chain instead of `ADO_MCP_AUTH_TOKEN`; that run tested missing credentials, not the network.
The config was corrected to `--authentication envvar` and the row above is the corrected run.

## B. Upstream answers 401 or 502 (local stub via each server's base-URL override)

Evidence: `stub_upstream_401_502.json`, script `stubtest.py`. Every row below is from a
run where the stub logged at least one request from the server, so the fault took effect.
Clarity, Azure DevOps and Lokka have no base-URL override and were not tested this way.

| Server | Upstream 401 | Upstream 502 |
|---|---|---|
| ms365-calendar / ms365-mail | isError=true, "Microsoft Graph API error: 401 Unauthorized - ..." | isError=true, "Microsoft Graph API error: 502 Bad Gateway - ..." |
| sentry | isError=true, "Authorization Expired ... rejected the stored access token" | isError=true, "HTTP 502 server error with the Sentry API" |
| context7 | isError=true, "Unauthorized" | isError=true, "Request failed with status 502" |
| fetch (`--allow-private-ips`) | isError=true, but says the 401 on robots.txt means "autonomous fetching is not allowed" | isError=true, same wording for 502 |
| notion | isError NOT set; text `{"status":"error","error":{...},"message":"Unauthorized",...}` | isError NOT set; text `{"status":"error","data":"<html>...502 Bad Gateway..."}` |

Two first attempts were discarded and rerun: Context7 reads `HTTPS_PROXY` itself and ignores
`NO_PROXY`, so its requests went to the sandbox proxy (stub logged 0 requests); fetch refuses
loopback URLs unless started with `--allow-private-ips`.

## C. Code reading (explains A and B)

- **Clarity** `dist/utils.js`, `tryAsync`: every failure (network error, non-2xx status,
  JSON parse error) is caught and returned as `{content:[{type:"text", text:"An error occurred
  while fetching the data."}]}` with no `isError`; the real cause goes to stderr only. A
  missing token is also returned as a success-shaped message.
- **Notion** `src/openapi-mcp-server/mcp/proxy.ts` lines 222-239: HTTP errors are returned as
  a success with `{"status":"error", ...upstream body}` and no `isError`; any other error
  (e.g. connection refused) is re-thrown and surfaces as a JSON-RPC error.
- **Azure DevOps** `dist/tools/core.js` lines 58-70: catches any error and returns
  `isError: true` with the message; also returns `isError: true` when the project list is null.
- **Context7** `dist/lib/api.js` `searchLibraries`: on failure returns `{results: [], error}`;
  the client receives `isError: true` (observed in A and B; the handler code for this tool
  was not read).

## What this does and does not show

- No server returned the silent-empty pattern (success with empty content) in any run here.
- Two servers report failures in a way the MCP spec advises against: Clarity (error as
  success text with no `isError`, cause dropped) and Notion (HTTP errors as success text,
  network errors as JSON-RPC errors). Under the Phase 2 grading these would be WARN, not FAIL.
  An agent reading the text would probably still see an error; a client branching on
  `isError` would not.
- Not shown: behaviour against the real upstream APIs, behaviour with real but
  wrongly-scoped credentials, other tools on the same servers, or Lokka's tool path.
