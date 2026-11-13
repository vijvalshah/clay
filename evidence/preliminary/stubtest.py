"""Simulated-upstream observation: point servers at a local stub that answers every
request with 401 or 502. Not a Phase 2 grade (no real upstream, no real baseline)."""
import http.server, json, os, sys, threading
sys.path.insert(0, "/home/user/clay/phase2"); import harness
V = "/tmp/claude-0/vendor/node_modules/.bin/"
HITS = []
class Stub(http.server.BaseHTTPRequestHandler):
    status = 401
    def _do(self):
        HITS.append(f"{self.command} {self.path[:80]}")
        if Stub.status == 401:
            body, ctype = json.dumps({"error": {"code": "InvalidAuthenticationToken", "message": "Access token is empty or invalid."}, "message": "Unauthorized", "detail": "Invalid token."}).encode(), "application/json"
        else:
            body, ctype = b"<html><body><h1>502 Bad Gateway</h1></body></html>", "text/html"
        self.send_response(Stub.status); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_GET = do_POST = do_PUT = do_PATCH = _do
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
U = f"http://127.0.0.1:{srv.server_address[1]}"
base = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
servers = [
 {"name":"ms365-calendar","command":[V+"ms-365-mcp-server"],"env":{**base,"MS365_MCP_OAUTH_TOKEN":"dummy.graph.token","MS365_MCP_GRAPH_BASE_URL":U},"tool":"list-calendar-events","arguments":{}},
 {"name":"ms365-mail","command":[V+"ms-365-mcp-server"],"env":{**base,"MS365_MCP_OAUTH_TOKEN":"dummy.graph.token","MS365_MCP_GRAPH_BASE_URL":U},"tool":"list-mail-messages","arguments":{}},
 {"name":"notion","command":[V+"notion-mcp-server"],"env":{**base,"NOTION_TOKEN":"ntn_dummy0000","BASE_URL":U},"tool":"API-get-users","arguments":{}},
 {"name":"context7","command":[V+"context7-mcp"],"env":{**base,"CONTEXT7_API_URL":U},"tool":"resolve-library-id","arguments":{"libraryName":"react","query":"useState hook"}},
 {"name":"sentry","command":[V+"sentry-mcp","mcp"],"env":{**base,"SENTRY_AUTH_TOKEN":"sntryu_dummy0000","SENTRY_URL":U},"tool":"find_organizations","arguments":{}},
 {"name":"fetch","command":["/tmp/claude-0/venv/bin/mcp-server-fetch","--allow-private-ips"],"env":base,"tool":"fetch","arguments":{"url":U+"/page"}},
]
res = {}
for status in (401, 502):
    Stub.status = status
    for cfg in servers:
        HITS.clear()
        r = harness.run_scenario(cfg, "healthy", {}, None)  # scenario name only selects env handling; no fault env added
        o = r["outcome"]; r["stub_status"] = status; r["stub_hits"] = list(HITS)
        res[f"{cfg['name']}@{status}"] = r
        kind = o.get("kind"); summ = ""
        if kind == "result":
            rr = o["result"]; txt = " ".join(harness.result_texts(rr))
            summ = f"isError={rr.get('isError')} empty={harness.empty_kind(rr)} text={txt[:230]!r}"
        elif kind == "rpc_error": summ = json.dumps(o["error"])[:230]
        else: summ = r["stderr_tail"][-200:].replace("\n"," | ")
        print(f"[{status}] {cfg['name']:15} hits={len(HITS):2} kind={kind:10} {summ}")
json.dump(res, open("/tmp/claude-0/scratch/stubtest.json","w"), indent=2)
