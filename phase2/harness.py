#!/usr/bin/env python3
"""Phase 2 throwaway harness (NOT failcheck).

Runs ONE read-only tool of an MCP stdio server three ways (healthy, network
down, bad credentials), saves the raw JSON-RPC traffic, and grades each run.
Standard library only. It speaks raw JSON-RPC so the evidence is the exact
bytes the server sent.

Usage:
    python harness.py servers.json [--server NAME] [--scenarios a,b,c]
"""
import argparse
import datetime
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time

PROTOCOL_VERSION = "2025-11-25"
CALL_TIMEOUT = 30.0     # seconds; no reply to tools/call within this => HANG
SETUP_TIMEOUT = 90.0    # initialize / tools/list (cold starts are slow)
SCENARIOS = ("healthy", "network_down", "bad_creds")
INVALID_CREDENTIAL = "failcheck-invalid-credential-0000"

# Tool names that look like they change state. We refuse to call these unless
# the server explicitly says readOnlyHint=true. Hints are untrusted (MCP spec),
# so the name check is a second layer, and the config picks the tool by hand.
WRITE_NAME = re.compile(
    r"(create|update|delete|remove|write|send|post|put|patch|push|merge|edit|"
    r"move|rename|insert|drop|run|exec|set_|add_|assign|close|reopen|approve|"
    r"cancel|upload|publish|deploy|start|stop|restart|invite|reply|forward)",
    re.I,
)

ERROR_TEXT = re.compile(
    r"\b(error|errors|failed|failure|unauthori[sz]ed|unauthenticated|forbidden|"
    r"invalid (api )?(token|credentials?|key)|bad credentials|401|403|502|503|504|"
    r"econnrefused|enotfound|connection (refused|failed|error)|timed? ?out|"
    r"unreachable|could not connect|cannot connect|network)\b",
    re.I,
)
GENERIC_ERROR = {"error", "failed", "failure", "unknown error", "undefined", "null", "none"}
PROSE_EMPTY = re.compile(
    r"^\s*(no|zero|0)\s+[\w\s-]{0,40}\b(found|results?|items?|matches|issues|"
    r"events|meetings|messages|emails?|files?|projects?|resources?|subscriptions?)\b"
    r"[\w\s.,!-]{0,40}$",
    re.I,
)


# --------------------------------------------------------------------------
# Grading (pure functions, unit tested)
# --------------------------------------------------------------------------

def _json_empty(v):
    """True if a parsed JSON value carries no data."""
    if v is None or v is False or v == 0 or v == "":
        return True
    if isinstance(v, (list, tuple)):
        return all(_json_empty(x) for x in v)
    if isinstance(v, dict):
        return all(_json_empty(x) for x in v.values())
    return False


def result_texts(result):
    return [b.get("text", "") for b in result.get("content") or []
            if isinstance(b, dict) and b.get("type") == "text"]


def empty_kind(result):
    """Return None if the result carries data, else a short label saying how it is empty."""
    sc = result.get("structuredContent")
    if sc is not None and not _json_empty(sc):
        return None
    blocks = result.get("content") or []
    if not blocks:
        return "no_content_blocks" if sc is None else "empty_structured_content"
    non_text = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "text")]
    if non_text:
        return None  # image/resource/etc: treat as data
    texts = [t for t in result_texts(result)]
    joined = "\n".join(texts).strip()
    if not joined:
        return "blank_text"
    try:
        parsed = json.loads(joined)
    except ValueError:
        return "prose_empty" if PROSE_EMPTY.match(joined) else None
    return "json_empty" if _json_empty(parsed) else None


def normalize(result):
    return json.dumps(
        {"c": result.get("content"), "s": result.get("structuredContent")},
        sort_keys=True,
    )


def baseline_ok(outcome):
    """A baseline is usable only if it is a non-error, non-empty success."""
    if outcome.get("kind") != "result":
        return False, f"baseline outcome was {outcome.get('kind')}"
    r = outcome["result"]
    if r.get("isError"):
        return False, "baseline returned isError=true"
    ek = empty_kind(r)
    if ek:
        return False, f"baseline was empty ({ek})"
    return True, ""


def grade(scenario, outcome, baseline):
    """Return (verdict, reason). Never rounds in our favour: anything that
    cannot be shown to be an honest error is not PASS."""
    if scenario == "healthy":
        ok, why = baseline_ok(outcome)
        return ("BASELINE_OK", "") if ok else ("INCONCLUSIVE", why)

    ok, why = baseline_ok(baseline) if baseline else (False, "no baseline run")
    if not ok:
        return "INCONCLUSIVE", f"baseline unusable: {why}"

    kind = outcome.get("kind")
    if kind == "setup_failed":
        return "INCONCLUSIVE", f"server did not reach tools/call: {outcome.get('detail')}"
    if kind == "timeout":
        return "HANG", f"no reply within {CALL_TIMEOUT:.0f}s"
    if kind == "exit":
        return "HANG", "server process exited without answering the call"
    if kind == "rpc_error":
        return "WARN", "failure sent as JSON-RPC error, not isError result: " + json.dumps(outcome["error"])[:200]

    r = outcome["result"]
    texts = " ".join(result_texts(r)).strip()
    if r.get("isError") is True:
        if not texts or texts.lower() in GENERIC_ERROR:
            return "WARN", "isError=true but message empty or generic"
        return "PASS", "isError=true with message (read it: is it accurate and useful?)"

    ek = empty_kind(r)
    if ek:
        return "FAIL", f"success response with empty result ({ek})"
    if normalize(r) == normalize(baseline["result"]):
        return "INCONCLUSIVE", "response identical to healthy baseline: fault did not take effect"
    if ERROR_TEXT.search(texts):
        return "WARN", "error reported as success-shaped text (isError not set)"
    return "INCONCLUSIVE", "success with different non-empty content; needs manual review"


# --------------------------------------------------------------------------
# Raw stdio JSON-RPC client
# --------------------------------------------------------------------------

class RawSession:
    def __init__(self, cmd, env, cwd=None):
        self.t0 = time.monotonic()
        self.log = []           # raw lines, both directions
        self.stderr_lines = []
        self.q = queue.Queue()
        self._id = 0
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        for line in self.proc.stdout:
            self.log.append({"dir": "recv", "t": round(time.monotonic() - self.t0, 3), "raw": line.rstrip("\n")})
            self.q.put(line)
        self.q.put(None)  # EOF

    def _read_err(self):
        for line in self.proc.stderr:
            self.stderr_lines.append(line.rstrip("\n"))

    def send(self, msg):
        line = json.dumps(msg)
        self.log.append({"dir": "send", "t": round(time.monotonic() - self.t0, 3), "raw": line})
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def notify(self, method, params=None):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        self.send(m)

    def request(self, method, params, timeout):
        self._id += 1
        rid = self._id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return {"kind": "timeout"}
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                return {"kind": "timeout"}
            if line is None:
                return {"kind": "exit", "returncode": self.proc.poll()}
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # non-JSON noise on stdout; already in raw log
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # server -> client request
                if msg["method"] == "ping":
                    self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                else:
                    self.send({"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": -32601, "message": "not supported by harness"}})
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    return {"kind": "rpc_error", "error": msg["error"]}
                return {"kind": "result", "result": msg.get("result", {})}

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# --------------------------------------------------------------------------
# Faults
# --------------------------------------------------------------------------

def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


KIT_DIR = os.path.dirname(os.path.abspath(__file__))


def subst(text, lookup):
    """Replace ${NAME} with lookup[NAME] (empty string if unset)."""
    return re.sub(r"\$\{(\w+)\}", lambda m: lookup.get(m.group(1), ""), text)


def missing_env(cfg, secrets):
    """Names the server needs that are not set. Optional secrets never count."""
    need = list(cfg.get("pass_env", []))
    if not cfg.get("secret_optional"):
        need += cfg.get("secret_env", [])
    return [n for n in need if not secrets.get(n)]


def build_env(cfg, scenario, secrets):
    env = dict(os.environ)
    env.update(cfg.get("env", {}))
    for name in cfg.get("pass_env", []):
        if name in secrets:
            env[name] = secrets[name]
    for name in cfg.get("secret_env", []):
        if name in secrets:
            env[name] = secrets[name]
    fault = {}
    if scenario == "network_down":
        p = f"http://127.0.0.1:{dead_port()}"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[k] = p
        for k in ("NO_PROXY", "no_proxy"):
            env.pop(k, None)
        env.update(cfg.get("network_down_env", {}))
        fault = {"proxy": p, "extra_env": cfg.get("network_down_env", {})}
    elif scenario == "bad_creds":
        for name in cfg.get("secret_env", []):
            env[name] = INVALID_CREDENTIAL
        fault = {"replaced_env": cfg.get("secret_env", [])}
    return env, fault


def redact(text, secret_values):
    for v in secret_values:
        if v and len(v) >= 6:
            text = text.replace(v, "***REDACTED***")
    return text


# --------------------------------------------------------------------------
# One scenario run
# --------------------------------------------------------------------------

def check_tool_is_safe(tool_name, tools):
    t = next((x for x in tools if x.get("name") == tool_name), None)
    if t is None:
        return False, f"tool {tool_name!r} not in tools/list", None
    ann = t.get("annotations") or {}
    hint = ann.get("readOnlyHint")
    if hint is False:
        return False, "server marks the tool readOnlyHint=false; refusing", ann
    if hint is not True and WRITE_NAME.search(tool_name):
        return False, "no readOnlyHint=true and name looks like a write action; refusing", ann
    return True, "", ann


def run_scenario(cfg, scenario, secrets, baseline):
    env, fault = build_env(cfg, scenario, secrets)
    cmd = cfg["command"]
    lookup = {**env, "KIT": KIT_DIR}
    sess = RawSession([subst(a, lookup) for a in cmd], env, cwd=cfg.get("cwd"))
    meta = {"server_info": None, "tool_annotations": None}
    outcome = None
    try:
        init = sess.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "failcheck-phase2-harness", "version": "0"}}, SETUP_TIMEOUT)
        if init["kind"] != "result":
            outcome = {"kind": "setup_failed", "detail": f"initialize -> {init['kind']}"}
        else:
            meta["server_info"] = init["result"].get("serverInfo")
            sess.notify("notifications/initialized")
            tl = sess.request("tools/list", {}, SETUP_TIMEOUT)
            if tl["kind"] != "result":
                outcome = {"kind": "setup_failed", "detail": f"tools/list -> {tl['kind']}"}
            else:
                ok, why, ann = check_tool_is_safe(cfg["tool"], tl["result"].get("tools", []))
                meta["tool_annotations"] = ann
                if not ok:
                    outcome = {"kind": "setup_failed", "detail": why}
                else:
                    outcome = sess.request(
                        "tools/call", {"name": cfg["tool"], "arguments": cfg.get("arguments", {})},
                        CALL_TIMEOUT)
    finally:
        sess.close()

    verdict, reason = grade(scenario, outcome, baseline)
    secret_values = [secrets.get(n, "") for n in cfg.get("secret_env", [])]
    raw = json.loads(redact(json.dumps(sess.log), secret_values))
    return {
        "server": cfg["name"],
        "package_version": cfg.get("version"),
        "scenario": scenario,
        "fault": fault,
        "command": cmd,
        "env_names_set": sorted(set(cfg.get("env", {})) | set(cfg.get("secret_env", [])) | set(cfg.get("pass_env", []))),
        "tool": cfg["tool"],
        "arguments": cfg.get("arguments", {}),
        "run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        **meta,
        "outcome": json.loads(redact(json.dumps(outcome), secret_values)),
        "verdict": verdict,
        "reason": reason,
        "raw_traffic": raw,
        "stderr_tail": redact("\n".join(sess.stderr_lines)[-4000:], secret_values),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def load_dotenv(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--server")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--evidence", default=os.path.join(os.path.dirname(__file__), "..", "evidence"))
    ap.add_argument("--env-file", default=os.path.join(os.path.dirname(__file__), ".env"))
    args = ap.parse_args(argv)

    configs = json.load(open(args.config, encoding="utf-8"))["servers"]
    secrets = {**load_dotenv(args.env_file), **{k: v for k, v in os.environ.items()}}
    wanted = args.scenarios.split(",")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for cfg in configs:
        if args.server and cfg["name"] != args.server:
            continue
        miss = missing_env(cfg, secrets)
        if miss:
            rows.append((cfg["name"], "-", "SKIPPED", "env not set: " + ", ".join(miss)))
            continue
        outdir = os.path.join(args.evidence, cfg["name"], stamp)
        os.makedirs(outdir, exist_ok=True)
        baseline = None
        for sc in ["healthy"] + [s for s in wanted if s != "healthy"]:
            has_secret = any(secrets.get(n) for n in cfg.get("secret_env", []))
            if sc == "bad_creds" and not has_secret:
                rows.append((cfg["name"], sc, "SKIPPED", "no credential set to break"))
                continue
            res = run_scenario(cfg, sc, secrets, baseline)
            if sc == "healthy":
                baseline = res["outcome"]
            with open(os.path.join(outdir, f"{sc}.json"), "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2)
            rows.append((cfg["name"], sc, res["verdict"], res["reason"]))
    w = max([len(r[0]) for r in rows] + [6])
    print(f"{'SERVER':<{w}}  {'SCENARIO':<13} {'VERDICT':<13} REASON")
    for n, sc, v, why in rows:
        print(f"{n:<{w}}  {sc:<13} {v:<13} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
