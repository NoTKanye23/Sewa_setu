# -*- coding: utf-8 -*-
"""LLM mode: Claude Code (headless, a small model, a turn limit) plays the assistant against the live
MCP endpoint, the way the reviewers will test. The portal's OAuth is completed first and the resulting
token handed to Claude Code, so it sees exactly the tools a signed-in person would."""

import os
import json
import shutil
import asyncio
import tempfile
import subprocess

from mcp_client import connect

MODEL = os.environ.get("EVAL_MODEL", "haiku")
MAX_TURNS = int(os.environ.get("EVAL_MAX_TURNS", "14"))

PROMPT = """You are connected to the Sewa Setu tools and are acting as the assistant of the person talking to you. \
Use your tools to help them, then write your reply to them. If you need something from them that they have not \
said, ask for it in your reply and stop: do not guess.
{doc}
Person: "{line}"
"""

FOLLOW_UP = 'Person: "{line}"'

DOC = ("The person's age proof document is a PDF. When you attach it with attach_age_proof, use filename "
       "'age_proof.pdf' and this exact content_base64: {pdf}\n")


def _parse(stdout):
    """Turn Claude Code's stream-json into [{'say': ...} | {'tool', 'args', 'result'}]."""
    transcript, pending, usage = [], {}, {}
    for raw in stdout.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for b in ev["message"].get("content", []):
                if b.get("type") == "text" and b["text"].strip():
                    transcript.append({"say": b["text"].strip()})
                elif b.get("type") == "tool_use":
                    entry = {"tool": b["name"].split("__")[-1], "args": b.get("input", {}), "result": None}
                    if "content_base64" in entry["args"]:
                        entry["args"] = {**entry["args"], "content_base64": entry["args"]["content_base64"][:24] + "..."}
                    pending[b["id"]] = entry
                    transcript.append(entry)
        elif ev.get("type") == "user":
            for b in ev["message"].get("content", []) if isinstance(ev["message"].get("content"), list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    content = b.get("content")
                    text = "".join(x.get("text", "") for x in content) if isinstance(content, list) else str(content)
                    try:
                        pending[b["tool_use_id"]]["result"] = json.loads(text)
                    except ValueError:
                        pending[b["tool_use_id"]]["result"] = text
        elif ev.get("type") == "result":
            usage = {"turns": ev.get("num_turns"), "cost_usd": ev.get("total_cost_usd"), "is_error": ev.get("is_error"),
                     "session_id": ev.get("session_id")}
    return transcript, usage


async def play_with_claude(endpoint, scope, creds, portal_url, sms_url, lines, pdf_b64):
    async with connect(endpoint, scope, portal_url, sms_url, creds) as (_, storage):
        token = storage.tokens.access_token
    work = tempfile.mkdtemp(prefix="evalclaude-")
    try:
        cfg = os.path.join(work, "mcp.json")
        with open(cfg, "w") as f:
            json.dump({"mcpServers": {"sewasetu": {"type": "http", "url": endpoint,
                                                   "headers": {"Authorization": "Bearer " + token}}}}, f)
        transcript, usage, session_id = [], {"turns": 0, "cost_usd": 0.0}, None
        for i, line in enumerate(lines):
            prompt = (PROMPT.format(doc=DOC.format(pdf=pdf_b64) if pdf_b64 else "", line=line) if i == 0
                      else FOLLOW_UP.format(line=line))
            transcript.append({"say": "PERSON: " + line})
            cmd = ["claude", "-p", prompt, "--model", MODEL, "--max-turns", str(MAX_TURNS),
                   "--mcp-config", cfg, "--strict-mcp-config", "--allowedTools", "mcp__sewasetu",
                   "--permission-mode", "dontAsk", "--output-format", "stream-json", "--verbose",
                   "--disable-slash-commands", "--setting-sources", "", "--tools", ""]
            if session_id:
                cmd += ["--resume", session_id]
            proc = await asyncio.to_thread(subprocess.run, cmd, cwd=work, capture_output=True, text=True, timeout=420)
            part, u = _parse(proc.stdout)
            transcript += part
            usage["turns"] += u.get("turns") or 0
            usage["cost_usd"] += u.get("cost_usd") or 0
            session_id = u.get("session_id") or session_id
            if not part:
                transcript.append({"say": "(claude produced no output) " + proc.stderr[-400:]})
                break
        return transcript, usage
    finally:
        shutil.rmtree(work, ignore_errors=True)
