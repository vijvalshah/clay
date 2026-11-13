"""Tests for the Phase 2 harness: grader unit tests plus end-to-end runs against
tiny fake servers (one per behaviour). Run: python -m pytest -q phase2"""
import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import harness  # noqa: E402

FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_server.py")
EVENTS = [{"title": "standup"}, {"title": "review"}]


def text(t, **kw):
    return {"content": [{"type": "text", "text": t}], **kw}


GOOD = {"kind": "result", "result": text(json.dumps(EVENTS))}


# ---- grader ---------------------------------------------------------------

@pytest.mark.parametrize("outcome,expected", [
    ({"kind": "timeout"}, "HANG"),
    ({"kind": "exit"}, "HANG"),
    ({"kind": "rpc_error", "error": {"code": -1, "message": "x"}}, "WARN"),
    ({"kind": "result", "result": text("401 unauthorized: bad token", isError=True)}, "PASS"),
    ({"kind": "result", "result": text("", isError=True)}, "WARN"),
    ({"kind": "result", "result": text("Error", isError=True)}, "WARN"),
    ({"kind": "result", "result": {"content": []}}, "FAIL"),
    ({"kind": "result", "result": text("[]")}, "FAIL"),
    ({"kind": "result", "result": text("")}, "FAIL"),
    ({"kind": "result", "result": text('{"results": []}')}, "FAIL"),
    ({"kind": "result", "result": text('{"total_count": 0, "items": []}')}, "FAIL"),
    ({"kind": "result", "result": text("No meetings found.")}, "FAIL"),
    ({"kind": "result", "result": {"content": [], "structuredContent": {"results": []}}}, "FAIL"),
    ({"kind": "result", "result": text("Error: 401 Unauthorized")}, "WARN"),
    ({"kind": "result", "result": GOOD["result"]}, "INCONCLUSIVE"),  # identical to baseline
    ({"kind": "result", "result": text('[{"title": "other"}]')}, "INCONCLUSIVE"),
    ({"kind": "setup_failed", "detail": "exited at startup"}, "INCONCLUSIVE"),
])
def test_grade_fault_run(outcome, expected):
    assert harness.grade("network_down", outcome, GOOD)[0] == expected


@pytest.mark.parametrize("bad_baseline", [
    None,
    {"kind": "timeout"},
    {"kind": "result", "result": text("[]")},
    {"kind": "result", "result": text("boom", isError=True)},
])
def test_unusable_baseline_is_never_a_verdict(bad_baseline):
    v, _ = harness.grade("bad_creds", {"kind": "result", "result": text("[]")}, bad_baseline)
    assert v == "INCONCLUSIVE"


def test_empty_kind_nonempty_data():
    assert harness.empty_kind(text('[{"a": 1}]')) is None
    assert harness.empty_kind({"content": [{"type": "image", "data": "x", "mimeType": "image/png"}]}) is None


# ---- safety guard ---------------------------------------------------------

def test_refuses_write_looking_tool_without_hint():
    ok, why, _ = harness.check_tool_is_safe("delete_issue", [{"name": "delete_issue"}])
    assert not ok


def test_refuses_explicit_non_readonly_hint():
    ok, _, _ = harness.check_tool_is_safe("list_x", [{"name": "list_x", "annotations": {"readOnlyHint": False}}])
    assert not ok


def test_allows_readonly_hint_and_plain_read_name():
    assert harness.check_tool_is_safe("list_x", [{"name": "list_x", "annotations": {"readOnlyHint": True}}])[0]
    assert harness.check_tool_is_safe("get_me", [{"name": "get_me"}])[0]


# ---- end to end against fakes --------------------------------------------

class Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("Authorization") == "Bearer good-token"
        body = json.dumps(EVENTS if ok else {"message": "bad credentials"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def upstream_url():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/events"
    srv.shutdown()


def cfg(mode, url):
    return {"name": f"fake-{mode}", "command": [sys.executable, FAKE, mode], "tool": "list_events",
            "env": {"UPSTREAM_URL": url, "NO_PROXY": "127.0.0.1"}, "secret_env": ["FAKE_TOKEN_NAME"]}


def run_all(mode, url, monkeypatch):
    c = cfg(mode, url)
    c["env"]["TOKEN"] = "good-token"
    c["secret_env"] = []  # token set via env above; bad_creds is exercised below
    secrets = {}
    base = harness.run_scenario(c, "healthy", secrets, None)
    assert base["verdict"] == "BASELINE_OK", base["reason"]
    net = harness.run_scenario(c, "network_down", secrets, base["outcome"])
    # bad credentials: make TOKEN the secret
    c2 = dict(c, secret_env=["TOKEN"])
    bad = harness.run_scenario(c2, "bad_creds", {"TOKEN": "good-token"}, base["outcome"])
    return net, bad


@pytest.mark.parametrize("mode,expected", [
    ("honest", "PASS"),
    ("silent_empty", "FAIL"),
    ("error_text", "WARN"),
    ("rpc_error", "WARN"),
])
def test_fake_servers_graded(mode, expected, upstream_url, monkeypatch):
    net, bad = run_all(mode, upstream_url, monkeypatch)
    assert net["verdict"] == expected, net
    assert bad["verdict"] == expected, bad


def test_hang_server_is_graded_hang(upstream_url, monkeypatch):
    monkeypatch.setattr(harness, "CALL_TIMEOUT", 2.0)
    c = cfg("hang", upstream_url)
    c["env"]["TOKEN"] = "good-token"
    c["secret_env"] = []
    base = harness.run_scenario(c, "healthy", {}, None)
    assert base["verdict"] == "BASELINE_OK"
    net = harness.run_scenario(c, "network_down", {}, base["outcome"])
    assert net["verdict"] == "HANG"


def test_secret_never_written_to_evidence(upstream_url):
    c = cfg("honest", upstream_url)
    c["env"]["TOKEN"] = "good-token"
    c["secret_env"] = ["TOKEN"]
    secret = "good-token"
    base = harness.run_scenario(c, "healthy", {"TOKEN": secret}, None)
    assert secret not in json.dumps(base)
