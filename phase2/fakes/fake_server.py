#!/usr/bin/env python3
"""Tiny stdio MCP server used to test the harness. Standard library only.

It has one read-only tool, list_events, that calls an HTTP upstream
(UPSTREAM_URL, with bearer token TOKEN). urllib honours HTTP(S)_PROXY, so a
dead proxy really breaks the upstream call, and a wrong TOKEN really gets 401.

Modes (argv[1]) differ only in what they do when the upstream call fails:
  honest        isError=true with a reason
  silent_empty  success with an empty list        <- the bug we look for
  error_text    success whose text says "Error: ..." (isError not set)
  rpc_error     JSON-RPC error instead of an isError result
  hang          never answers
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

MODE = sys.argv[1] if len(sys.argv) > 1 else "honest"
TOOL = {
    "name": "list_events",
    "description": "List calendar events (read-only).",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"readOnlyHint": True},
}


def upstream():
    req = urllib.request.Request(
        os.environ["UPSTREAM_URL"],
        headers={"Authorization": "Bearer " + os.environ.get("TOKEN", "")},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def reply(rid, result=None, error=None):
    m = {"jsonrpc": "2.0", "id": rid}
    m.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()


def call(rid):
    try:
        events = upstream()
        reply(rid, {"content": [{"type": "text", "text": json.dumps(events)}]})
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = f"upstream call failed: {e}"
        if MODE == "honest":
            reply(rid, {"isError": True, "content": [{"type": "text", "text": reason}]})
        elif MODE == "silent_empty":
            reply(rid, {"content": [{"type": "text", "text": "[]"}]})
        elif MODE == "error_text":
            reply(rid, {"content": [{"type": "text", "text": "Error: " + reason}]})
        elif MODE == "rpc_error":
            reply(rid, error={"code": -32603, "message": reason})
        elif MODE == "hang":
            time.sleep(3600)


for line in sys.stdin:
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    if m == "initialize":
        reply(rid, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": f"fake-{MODE}", "version": "0.0.1"}})
    elif m == "tools/list":
        reply(rid, {"tools": [TOOL]})
    elif m == "tools/call":
        call(rid)
