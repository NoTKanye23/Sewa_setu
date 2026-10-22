# -*- coding: utf-8 -*-
"""Sewa Setu assistant for applicants: /mcp/citizen (scope `citizen`).

The caller is whoever signed in on the portal with mobile + OTP. Every tool acts on that
mobile number only, and every write goes through core.py, the same code the web form uses.
"""

import os
import base64
import binascii

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings

import core
from auth import PortalTokenVerifier, PUBLIC_BASE_URL, current
from common import ist, ist_date, mask_account, deemed_on, clean_choice, serve

RESOURCE_URL = os.environ.get("RESOURCE_URL") or PUBLIC_BASE_URL + "/mcp/citizen"

INSTRUCTIONS = """\
You help one citizen apply for the Old Age Pension (Rs 250 a month) in Purvanchal and follow up \
on the application. The person you are talking to signed in to the portal themselves; every tool \
acts only for that signed-in mobile number.

How to behave:
- Speak simply and kindly, in the person's language, in short sentences. Many applicants are old \
and are using a shared phone or a kiosk. Ask one thing at a time.
- The portal's rules are the only rules. Never promise, hint at, or imply that someone will get \
the pension. Only an officer approves, or the law deems it after the waiting period. Say what \
the record says, nothing more.
- Do not guess any detail. If the person has not told you a value, ask. Dates of birth must be \
a real calendar date (DD/MM/YYYY); if someone gives an age or a festival or an Indian calendar \
date, ask for the date of birth on the age proof document instead.
- Before submitting, read every detail back and get a clear yes. Only then call submit_application \
with confirmed_by_applicant=true.
- The age proof is stored as received. You cannot check it, so never say it is verified or valid.
- If a tool says something is not allowed (too young, window closed, already applied), explain it \
plainly and kindly. Do not look for a way around it.
- All times are Indian Standard Time.
"""

mcp = FastMCP(
    "Sewa Setu - applicant assistant",
    instructions=INSTRUCTIONS,
    token_verifier=PortalTokenVerifier(RESOURCE_URL, "citizen"),
    auth=AuthSettings(issuer_url=PUBLIC_BASE_URL, resource_server_url=RESOURCE_URL,
                      required_scopes=["citizen"]),
    host="0.0.0.0", port=int(os.environ.get("PORT", "8001")),
    streamable_http_path="/mcp/citizen", stateless_http=True, json_response=True,
)


def _describe(row):
    """A row of (id, application_no, status, submitted_at, decided_at, decided_by, block)."""
    app_id, app_no, status, submitted, decided, decided_by, block = row
    out = {"application_id": app_id, "application_no": app_no, "status": status,
           "block": block, "submitted_at": ist(submitted)}
    if status == "PENDING":
        out["meaning"] = ("Received and waiting for the block officer. If no one decides it by %s "
                          "it is treated as approved under the Right to Public Services Act."
                          % deemed_on(submitted))
        out["can_withdraw"] = True
    elif status == "APPROVED":
        out["meaning"] = "Approved by a block officer on %s." % ist_date(decided)
    elif status == "DEEMED_APPROVED":
        out["meaning"] = ("Treated as approved on %s because it was not decided within %d days."
                          % (ist_date(decided), core.SLA_DAYS))
    elif status == "REJECTED":
        out["meaning"] = ("Rejected on %s. The person can apply again if they are eligible; the "
                          "reason is with the block office." % ist_date(decided))
    elif status == "WITHDRAWN":
        out["meaning"] = "Withdrawn by the applicant on %s." % ist_date(decided)
    return out


@mcp.tool()
def get_scheme_info() -> dict:
    """Who can get the Old Age Pension, what is needed to apply, the blocks served and how long
    the application window stays open. Call this first when someone asks whether they can apply."""
    hours = core.deadline_remaining()
    return {
        "scheme": "Old Age Pension, Government of Purvanchal, Department of Social Welfare",
        "amount": "Rs 250 per month",
        "who_can_apply": "People aged %d or above, living in Purvanchal. One application per mobile "
                         "number while an earlier one is pending, approved or deemed approved."
                         % core.MIN_AGE,
        "blocks": core.BLOCKS,
        "needed": ["full name", "date of birth (DD/MM/YYYY)", "village", "block",
                   "bank account number (9 to 18 digits)", "IFSC code of the bank branch",
                   "a photo or PDF of an age proof document (JPG, PNG or PDF, up to 5 MB)"],
        "optional": ["gender", "marital status", "late husband's name (if widowed)"],
        "window_closes": core.SCHEME_DEADLINE.strftime("%d/%m/%Y %I:%M %p IST"),
        "window_open": hours is not None,
        "hours_left": hours,
        "waiting_period": "An application not decided within %d days is treated as approved." % core.SLA_DAYS,
        "signed_in_mobile": current().identity,
    }


@mcp.tool()
def get_my_application() -> dict:
    """The status of this person's applications (newest first) with a plain-language meaning.
    Use it for 'what happened to my application?' and before offering to withdraw."""
    mobile = current().identity
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("SELECT id, application_no, status, submitted_at, decided_at, decided_by, block "
                "FROM applications WHERE mobile = %s ORDER BY submitted_at DESC LIMIT 5", (mobile,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    if not rows:
        return {"mobile": mobile, "applications": [],
                "message": "No application has been filed from this mobile number."}
    return {"mobile": mobile, "applications": [_describe(r) for r in rows]}


def _fields(applicant_name, date_of_birth, village, block, bank_account, ifsc,
            gender, marital_status, husband_name):
    return {"applicant_name": applicant_name, "dob": date_of_birth, "village": village,
            "block": block, "bank_account": bank_account, "ifsc": ifsc,
            "gender": clean_choice(gender, ["Male", "Female", "Other"], "gender") or "",
            "marital_status": clean_choice(marital_status, ["Married", "Unmarried", "Widowed"],
                                           "marital status") or "",
            "husband_name": husband_name}


@mcp.tool()
def attach_age_proof(filename: str, content_base64: str) -> dict:
    """Store the applicant's age proof document (JPG, PNG or PDF, up to 5 MB) so it can be
    submitted with the application. `content_base64` is the file's bytes, base64 encoded.
    The document is kept exactly as received: this tool does not and cannot check it."""
    mobile = current().identity
    try:
        content = base64.b64decode(content_base64, validate=False)
        if not content and content_base64.strip():
            raise binascii.Error("empty")
        path = core.save_document(mobile, filename, content)
    except (binascii.Error, ValueError):
        return {"ok": False, "problems": ["the file content could not be read"]}
    except core.Refused as r:
        return {"ok": False, "problems": r.problems}
    core.remember_document_tx(mobile, path)
    return {"ok": True, "message": "Document received and attached to the next application. "
                                   "It has been stored as is and has not been checked."}


@mcp.tool()
def check_application(applicant_name: str, date_of_birth: str, village: str, block: str,
                      bank_account: str, ifsc: str = "", gender: str = "",
                      marital_status: str = "", husband_name: str = "") -> dict:
    """Test the details against the portal's rules WITHOUT filing anything. Returns the problems to
    fix, or ok. `date_of_birth` is DD/MM/YYYY. Use it before submit_application."""
    mobile = current().identity
    try:
        data = _fields(applicant_name, date_of_birth, village, block, bank_account, ifsc,
                       gender, marital_status, husband_name)
    except core.Refused as r:
        return {"ok": False, "problems": r.problems}
    doc = core.pending_document(mobile)
    errors, cleaned = core.validate_application(data, doc)
    conn = core.get_db(); cur = conn.cursor()
    existing = core.active_application(cur, mobile)
    cur.close(); conn.close()
    if existing:
        errors.append("application %s (%s) already exists for this mobile number" % (existing[1], existing[2]))
    if errors:
        return {"ok": False, "problems": errors}
    return {"ok": True, "message": "These details meet the portal's rules. Read them back to the "
                                   "person and ask for a clear yes before submitting.",
            "bank_account": mask_account(cleaned["bank_account"]), "age_proof_attached": True}


@mcp.tool()
def submit_application(applicant_name: str, date_of_birth: str, village: str, block: str,
                       bank_account: str, ifsc: str, confirmed_by_applicant: bool,
                       gender: str = "", marital_status: str = "", husband_name: str = "") -> dict:
    """File the pension application for the signed-in mobile number. Set confirmed_by_applicant to
    true ONLY after reading every detail back and hearing a clear yes. It runs the same checks as
    the portal's own form, including the one-application-per-mobile rule, and files nothing if
    any fail. `date_of_birth` is DD/MM/YYYY. Attach the age proof first (attach_age_proof)."""
    if not confirmed_by_applicant:
        return {"ok": False, "problems": ["not filed: read the details back to the person and "
                                          "get a clear yes first"]}
    mobile = current().identity
    try:
        data = _fields(applicant_name, date_of_birth, village, block, bank_account, ifsc,
                       gender, marital_status, husband_name)
        app_id, app_no = core.submit_application_tx(mobile, data, core.pending_document(mobile))
    except core.Refused as r:
        return {"ok": False, "problems": r.problems}
    core.send_sms(mobile, "Sewa Setu: application %s received." % app_no)
    return {"ok": True, "application_no": app_no, "application_id": app_id, "status": "PENDING",
            "message": "Application filed. It now waits for the block officer; it is not approved yet. "
                       "If it is not decided by %s it is treated as approved under the RTPS Act."
                       % deemed_on(core.now_ist())}


@mcp.tool()
def withdraw_application(confirmed_by_applicant: bool) -> dict:
    """Withdraw this person's PENDING application so they can apply afresh. Only call it after
    the person clearly asks and confirms; it cannot be undone. Approved or rejected applications
    cannot be withdrawn."""
    if not confirmed_by_applicant:
        return {"ok": False, "problems": ["not withdrawn: ask the person to confirm first"]}
    try:
        app_no = core.withdraw_application_tx(current().identity)
    except core.Refused as r:
        return {"ok": False, "problems": r.problems}
    return {"ok": True, "message": "Application %s has been withdrawn. The person may apply again." % app_no}


if __name__ == "__main__":
    core.ensure_schema()
    serve(mcp)
