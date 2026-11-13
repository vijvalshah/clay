"""Failure-path observation: run each configured tool with a dead proxy and dummy
credentials. No baseline exists, so these are NOT Phase 2 grades."""
import json, os, sys, datetime
sys.path.insert(0, "/home/user/clay/phase2")
import harness
cfgs = json.load(open("/home/user/clay/phase2/servers.json"))["servers"]
dummy = {"NOTION_TOKEN":"ntn_dummy0000000000","SENTRY_AUTH_TOKEN":"sntryu_dummy000000","CLARITY_API_TOKEN":"dummy-clarity-0000",
         "ADO_ORG":"failcheck-dummy-org","ADO_MCP_AUTH_TOKEN":"dummy-ado-000000","MS365_MCP_OAUTH_TOKEN":"dummy.graph.token",
         "TENANT_ID":"00000000-0000-0000-0000-000000000000","CLIENT_ID":"00000000-0000-0000-0000-000000000000","CLIENT_SECRET":"dummy-secret-0000"}
out = {}
for cfg in cfgs:
    cfg = dict(cfg)
    cfg["command"] = [a.replace("${KIT}/vendor/node_modules", "/tmp/claude-0/vendor/node_modules").replace("${KIT}/vendor/venv", "/tmp/claude-0/venv") for a in cfg["command"]]
    res = harness.run_scenario(cfg, "network_down", dummy, None)
    out[cfg["name"]] = res
    o = res["outcome"]
    print(f"## {cfg['name']}: kind={o.get('kind')}")
    print("   ", json.dumps(o)[:400])
    if o.get("kind") in ("setup_failed","exit","timeout"):
        print("    stderr:", res["stderr_tail"][-300:].replace("\n"," | "))
json.dump(out, open("/tmp/claude-0/scratch/failpath.json","w"), indent=2)
