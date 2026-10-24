# -*- coding: utf-8 -*-
"""Plays every persona against the live MCP endpoints through a real OAuth-signed-in MCP client,
then checks the portal's database and stores the evidence for /eval-dashboard.

    python -m evals.runner            (needs PORTAL_URL, SMS_URL, CITIZEN_MCP, OFFICER_MCP, OFFICERS_CREDENTIALS)
"""

import os
import sys
import json
import time
import asyncio
import traceback
import subprocess
from datetime import datetime

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import core                                    # noqa: E402
from mcp_client import connect, result_data   # noqa: E402
from personas import PERSONAS, LLM, PDF       # noqa: E402
import claude_mode                            # noqa: E402

PORTAL_URL = os.environ.get("PORTAL_URL", "http://localhost:8000")
SMS_URL = os.environ.get("SMS_URL", "http://localhost:8025")
CITIZEN_MCP = os.environ.get("CITIZEN_MCP", PORTAL_URL.rstrip("/") + "/mcp/citizen")
OFFICER_MCP = os.environ.get("OFFICER_MCP", PORTAL_URL.rstrip("/") + "/mcp/officer")
# "meena:meenapw,ravi:ravipw"
OFFICER_LOGINS = dict(p.split(":", 1) for p in os.environ.get("OFFICER_LOGINS", "meena:meenapw,ravi:ravipw").split(",") if p)
TEST_MOBILE_PREFIX = "91000000"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id SERIAL PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, mode VARCHAR(20),
    target VARCHAR(200), revision VARCHAR(40), passed INTEGER, total INTEGER);
CREATE TABLE IF NOT EXISTS eval_results (
    id SERIAL PRIMARY KEY, run_id INTEGER, persona_id VARCHAR(40), profile JSONB, passed BOOLEAN,
    checks JSONB, transcript JSONB, error TEXT, millis INTEGER);
"""


class Session:
    """One signed-in MCP session; records every call for the transcript."""

    def __init__(self, session, storage, transcript):
        self.raw, self.storage, self.transcript = session, storage, transcript

    @property
    def access_token(self):
        return self.storage.tokens.access_token

    async def tool_names(self):
        return [t.name for t in (await self.raw.list_tools()).tools]

    async def call(self, name, **args):
        res = await self.raw.call_tool(name, args)
        data = result_data(res)
        shown = {k: (v[:24] + "..." if k == "content_base64" else v) for k, v in args.items()}
        self.transcript.append({"tool": name, "args": shown, "result": data})
        return data


class Ctx:
    def __init__(self, profile):
        self.profile, self.mobile = profile, profile["mobile"]
        self.checks, self.transcript = [], []
        self.officer_url = OFFICER_MCP

    def check(self, name, actual, expected):
        ok = actual == expected
        self.checks.append({"name": name, "expected": repr(expected), "actual": repr(actual), "passed": ok})

    def db(self, sql, args=()):
        conn = core.get_db(); cur = conn.cursor()
        cur.execute(sql, args)
        out = cur.fetchall() if cur.description else []
        conn.commit(); cur.close(); conn.close()
        return out

    def seed_application(self, mobile, name, village, block, dob):
        core.ensure_schema()
        app_id, _ = core.submit_application_tx(mobile, {
            "applicant_name": name, "village": village, "block": block, "dob": dob,
            "bank_account": mobile[-10:] + "00", "ifsc": "SBIN0003077", "gender": "Female"}, "/seed/age_proof.pdf")
        return app_id

    async def upload_status(self, url, filename, content):
        """What the upload page answers when a person submits a file through the link."""
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.post(url, files={"document": (filename, content)})
        return r.status_code

    async def raw_post(self, url, token):
        async with httpx.AsyncClient() as c:
            r = await c.post(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return r.status_code


async def play_llm(profile):
    """A model plays the assistant; only the database afterwards decides pass or fail."""
    ctx = Ctx(profile)
    started, error, usage, lines = time.time(), None, {}, []
    spec = LLM[profile["id"]]
    try:
        ids = spec["setup"](ctx) if spec["setup"] else {}
        lines = [l.format(**ids) for l in spec["lines"]]
        endpoint = CITIZEN_MCP if profile["role"] == "citizen" else OFFICER_MCP
        creds = ({"mobile": profile["mobile"]} if profile["role"] == "citizen"
                 else {"username": profile.get("officer", profile["id"]), "password": OFFICER_LOGINS[profile.get("officer", profile["id"])]})
        ctx.transcript, usage = await claude_mode.play_with_claude(
            endpoint, profile["role"], creds, PORTAL_URL, SMS_URL, lines, PDF if spec["doc"] else "")
        spec["outcome"](ctx, ids)
    except BaseException as e:
        error = "".join(traceback.format_exception_only(type(e), e)).strip()
    passed = error is None and bool(ctx.checks) and all(c["passed"] for c in ctx.checks)
    profile = {k: v for k, v in profile.items() if k != "run"}
    profile["said"] = lines if error is None else []
    profile["usage"] = usage
    return {"persona_id": profile["id"], "profile": profile, "passed": passed, "checks": ctx.checks,
            "transcript": ctx.transcript, "error": error, "millis": int((time.time() - started) * 1000)}


def cleanup():
    conn = core.get_db(); cur = conn.cursor()
    like = TEST_MOBILE_PREFIX + "%"
    cur.execute("DELETE FROM audit_log WHERE application_id IN (SELECT id FROM applications WHERE mobile LIKE %s)", (like,))
    cur.execute("DELETE FROM applications WHERE mobile LIKE %s", (like,))
    cur.execute("DELETE FROM portal_users WHERE mobile LIKE %s", (like,))
    cur.execute("DELETE FROM assistant_documents WHERE mobile LIKE %s", (like,))
    conn.commit(); cur.close(); conn.close()


async def play(profile):
    ctx = Ctx(profile)
    started, error = time.time(), None
    try:
        if profile["role"] == "citizen":
            cm = connect(CITIZEN_MCP, "citizen", PORTAL_URL, SMS_URL, {"mobile": profile["mobile"]})
        else:
            user = profile.get("officer", profile["id"])
            cm = connect(OFFICER_MCP, "officer", PORTAL_URL, SMS_URL,
                         {"username": user, "password": OFFICER_LOGINS[user]})
        async with cm as (raw, storage):
            await profile["run"](ctx, Session(raw, storage, ctx.transcript))
    except BaseException as e:   # a crash is a failed persona, not a failed run
        error = "".join(traceback.format_exception_only(type(e), e)).strip()
        if getattr(e, "exceptions", None):
            error += " | " + "; ".join(repr(x) for x in e.exceptions)
    passed = error is None and bool(ctx.checks) and all(c["passed"] for c in ctx.checks)
    return {"persona_id": profile["id"], "profile": {k: v for k, v in profile.items() if k != "run"},
            "passed": passed, "checks": ctx.checks, "transcript": ctx.transcript, "error": error,
            "millis": int((time.time() - started) * 1000)}


def revision():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True,
                                       cwd=os.path.dirname(__file__), stderr=subprocess.DEVNULL).strip()
    except Exception:
        return os.environ.get("GIT_REV", "unknown")


async def main(only=None, mode="scripted"):
    conn = core.get_db(); cur = conn.cursor(); cur.execute(_SCHEMA); conn.commit(); cur.close(); conn.close()
    core.ensure_schema()
    cleanup()
    started = datetime.now()
    results = []
    for p in PERSONAS:
        if only and p["id"] not in only:
            continue
        if mode == "claude" and p["id"] not in LLM:
            continue
        r = await (play_llm(p) if mode == "claude" else play(p))
        results.append(r)
        print("%-10s %-4s %d/%d checks%s" % (p["id"], "PASS" if r["passed"] else "FAIL",
              sum(c["passed"] for c in r["checks"]), len(r["checks"]), "  ERROR " + r["error"] if r["error"] else ""))
        for c in r["checks"]:
            if not c["passed"]:
                print("     x %s: expected %s, got %s" % (c["name"], c["expected"], c["actual"]))
    label = "claude:" + claude_mode.MODEL if mode == "claude" else "scripted"
    if only:    # a partial run is for debugging; only full runs reach the dashboard
        for r in results:
            for t in r["transcript"]:
                print("   assistant:", t["say"][:600]) if "say" in t else print("   tool %s(%s) -> %s" % (t["tool"], json.dumps(t["args"])[:200], json.dumps(t["result"])[:300]))
            print("   usage:", r["profile"].get("usage"))
        cleanup()
        print("partial run: not recorded")
        return 0 if all(r["passed"] for r in results) else 1
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO eval_runs (started_at, finished_at, mode, target, revision, passed, total) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (started, datetime.now(), label, PORTAL_URL, revision(), sum(r["passed"] for r in results), len(results)))
    run_id = cur.fetchone()[0]
    for r in results:
        cur.execute("INSERT INTO eval_results (run_id, persona_id, profile, passed, checks, transcript, error, millis) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, r["persona_id"], json.dumps(r["profile"]), r["passed"], json.dumps(r["checks"]),
                     json.dumps(r["transcript"], default=str), r["error"], r["millis"]))
    conn.commit(); cur.close(); conn.close()
    cleanup()
    print("run %d: %d/%d personas passed" % (run_id, sum(r["passed"] for r in results), len(results)))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = "claude" if "--claude" in args else "scripted"
    sys.exit(asyncio.run(main([a for a in args if not a.startswith("--")] or None, mode)))
