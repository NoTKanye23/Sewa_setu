# -*- coding: utf-8 -*-
"""Sewa Setu assistant for block officers: /mcp/officer (scope `officer`).

Separate endpoint, separate audience, separate tools. Every decision is recorded against the
signed-in officer's own username, with the reason they give. An officer assigned to a block
sees and decides only that block's applications.
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings

import core
from auth import PortalTokenVerifier, PUBLIC_BASE_URL, current
from common import ist, mask_account, mask_mobile, deemed_on, serve

RESOURCE_URL = os.environ.get("RESOURCE_URL") or PUBLIC_BASE_URL + "/mcp/officer"

INSTRUCTIONS = """\
You help a block officer of the Old Age Pension scheme work through applications waiting for a \
decision. The officer signed in on the portal; every decision is recorded under their own name.

How to behave:
- Show what the record says; do not invent facts about an applicant. If something is missing or \
unclear, say so.
- You cannot verify age proof documents. They are stored as received; tell the officer to open \
the document themselves when it matters.
- Approve or reject only when the officer clearly tells you which application and what the \
reason is. A reason is mandatory and is stored against the officer's name: never make one up, and \
never reuse an earlier reason for a different case.
- Applications that nobody decides within the waiting period are treated as approved \
automatically, so older cases deserve attention first.
- Applicants' personal data is private. Share it only with the officer in this conversation.
- All times are Indian Standard Time.
"""

mcp = FastMCP(
    "Sewa Setu - officer assistant",
    instructions=INSTRUCTIONS,
    token_verifier=PortalTokenVerifier(RESOURCE_URL, "officer"),
    auth=AuthSettings(issuer_url=PUBLIC_BASE_URL, resource_server_url=RESOURCE_URL,
                      required_scopes=["officer"]),
    host="0.0.0.0", port=int(os.environ.get("PORT", "8002")),
    streamable_http_path="/mcp/officer", stateless_http=True, json_response=True,
)


def _my_block():
    """The block this officer is limited to, or None for all blocks."""
    core.ensure_schema()
    return core.officer_block(current().identity)


@mcp.tool()
def queue_summary() -> dict:
    """How many applications are waiting for a decision, by block, and how many have waited
    longer than the waiting period. Start here when an officer asks what is waiting."""
    block = _my_block()
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("SELECT block, count(*), min(submitted_at) FROM applications WHERE status = 'PENDING' "
                "AND (%s IS NULL OR block = %s) GROUP BY block ORDER BY count(*) DESC", (block, block))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"officer": current().identity, "limited_to_block": block,
            "waiting_total": sum(r[1] for r in rows),
            "by_block": [{"block": r[0], "waiting": r[1], "oldest_submitted": ist(r[2]),
                          "oldest_becomes_deemed_approved_on": deemed_on(r[2])} for r in rows]}


@mcp.tool()
def list_pending(block: str = "", limit: int = 10, offset: int = 0) -> dict:
    """Applications waiting for a decision, oldest first (they are the closest to being treated as
    approved automatically). Optionally filter by block. Returns at most 25 per call."""
    mine = _my_block()
    if mine and block and block != mine:
        return {"ok": False, "problems": ["you are assigned to %s and cannot list other blocks" % mine]}
    block = mine or block.strip() or None
    if block and block not in core.BLOCKS:
        return {"ok": False, "problems": ["block must be one of: " + ", ".join(core.BLOCKS)]}
    limit = max(1, min(int(limit), 25))
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("SELECT id, application_no, applicant_name, village, block, submitted_at "
                "FROM applications WHERE status = 'PENDING' AND (%s IS NULL OR block = %s) "
                "ORDER BY submitted_at ASC LIMIT %s OFFSET %s", (block, block, limit, max(int(offset), 0)))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"ok": True, "block": block,
            "applications": [{"application_id": r[0], "application_no": r[1], "name": r[2],
                              "village": r[3], "block": r[4], "submitted_at": ist(r[5]),
                              "becomes_deemed_approved_on": deemed_on(r[5])} for r in rows]}


@mcp.tool()
def get_case(application_id: int) -> dict:
    """Everything the portal holds on one application: details, age-proof file received, history of
    actions, and other applications that share the mobile number or bank account (possible
    duplicates). The bank account number is shown masked."""
    mine = _my_block()
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, gender, marital_status, husband_name, "
                "village, block, bank_account, ifsc, doc_path, status, submitted_at, decided_at, decided_by "
                "FROM applications WHERE id = %s", (application_id,))
    r = cur.fetchone()
    if not r or (mine and r[8] != mine):
        cur.close(); conn.close()
        return {"ok": False, "problems": ["no such application in your block"]}
    cur.execute("SELECT at, action, actor, note FROM audit_log WHERE application_id = %s ORDER BY at ASC",
                (application_id,))
    audit = [{"at": ist(a[0]), "action": a[1], "by": a[2], "note": a[3]} for a in cur.fetchall()]
    cur.execute("SELECT id, application_no, status, mobile FROM applications WHERE id <> %s AND "
                "(mobile = %s OR bank_account = %s) ORDER BY submitted_at DESC LIMIT 10",
                (application_id, r[2], r[9]))
    related = [{"application_id": x[0], "application_no": x[1], "status": x[2],
                "shares": "mobile" if x[3] == r[2] else "bank account"} for x in cur.fetchall()]
    cur.close(); conn.close()
    age = core.age_on(r[3], core.now_ist().date()) if r[3] else None
    return {"ok": True, "application_id": application_id, "application_no": r[0], "status": r[12],
            "applicant": {"name": r[1], "mobile": mask_mobile(r[2]), "date_of_birth": r[3].strftime("%d/%m/%Y") if r[3] else None,
                          "age_today": age, "gender": r[4], "marital_status": r[5], "husband_name": r[6],
                          "village": r[7], "block": r[8]},
            "bank": {"account": mask_account(r[9]), "ifsc": r[10]},
            "age_proof": {"file_received": bool(r[11]), "file": os.path.basename(r[11] or "") or None,
                          "note": "Stored as received; not machine-verified. Open it to check it."},
            "submitted_at": ist(r[13]), "becomes_deemed_approved_on": deemed_on(r[13]) if r[12] == "PENDING" else None,
            "decided_at": ist(r[14]), "decided_by": r[15],
            "history": audit, "possible_duplicates": related}


def _decide(application_id, decision, reason):
    me = current().identity
    try:
        app_no = core.decide_application_tx(application_id, decision, me, reason, officer_block=_my_block())
    except core.Refused as r:
        return {"ok": False, "problems": r.problems}
    return {"ok": True, "application_no": app_no, "status": decision, "recorded_against": me,
            "reason": reason.strip()}


@mcp.tool()
def approve_application(application_id: int, reason: str) -> dict:
    """Approve one PENDING application. `reason` (at least 10 characters) is mandatory, comes from
    the officer, and is recorded against their name. The applicant is told by SMS."""
    return _decide(application_id, "APPROVED", reason)


@mcp.tool()
def reject_application(application_id: int, reason: str) -> dict:
    """Reject one PENDING application. `reason` (at least 10 characters) is mandatory, comes from
    the officer, and is recorded against their name. The applicant is told by SMS."""
    return _decide(application_id, "REJECTED", reason)


if __name__ == "__main__":
    core.ensure_schema()
    serve(mcp)
