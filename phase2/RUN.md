# Phase 2: run on your machine

Needs: Node 22.21+ (or 24+), Python 3.10+, internet access. Standard library only for the harness.

```
cd phase2
./setup.sh                      # pins and pre-installs every server (needs network, once)
cp .env.example .env            # fill in TEST-account credentials only; never commit .env
python3 -m pytest -q .          # optional: 32 harness tests, should pass
python3 harness.py servers.json # runs healthy / network_down / bad_creds per server
```

Run one server: `python3 harness.py servers.json --server notion`.
Servers with missing env vars are skipped, not failed.
Raw JSON-RPC for every run lands in `../evidence/<server>/<UTC timestamp>/<scenario>.json`.
Send that folder back. Secret values are redacted in it; skim it before sharing anyway.

## Before you run
- Test accounts only. Prefer read-only scopes. The harness calls exactly one read-only tool per server
  (named in servers.json) and refuses any tool marked readOnlyHint=false or with a write-like name.
- Microsoft 365: use a test tenant, not a work mailbox. Do not run the Microsoft baselines while
  Microsoft reports an active incident for the service; a failed baseline is INCONCLUSIVE, not a finding.
- `network_down` sets HTTP(S)_PROXY/ALL_PROXY to a dead local port (and NODE_USE_ENV_PROXY=1 for Node
  servers). If a server ignores that, its response matches the baseline and is graded INCONCLUSIVE.
  Never read an INCONCLUSIVE as a pass.

## Grades
PASS: isError=true with a message. WARN: error but vague, or sent as JSON-RPC error, or error text in a
success result. FAIL: success with empty content. HANG: no reply in 30 s (or process died mid-call).
INCONCLUSIVE: baseline unusable, fault had no effect, server never reached the call, or the output
needs a human look. Every FAIL and every non-trivial PASS gets read by a person before it is reported.

## Not in batch 1
GitHub official (Go binary), Azure MCP (`@azure/mcp`), Playwright MCP (no upstream credential, so
the faults do not apply), plus further popular servers once their package names and popularity are verified.
