# -*- coding: utf-8 -*-
"""
Sewa Setu core: the scheme rules and every write to the applications tables.

The web portal (app.py) and the assistant MCP servers (mcp/) both import this module,
so no channel can admit, withdraw or decide an application another channel would refuse.
Functions that change data take a cursor and leave the commit to the caller, except the
`*_tx` helpers, which own a whole transaction.
"""

import os
import re
import hmac
import time
import random
import hashlib
import logging
import configparser
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import psycopg2

log = logging.getLogger("sewasetu.core")

IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(os.environ.get("SEWASETU_CONFIG") or os.path.join(BASE_DIR, "config", "app.ini"))

SMS_GATEWAY_URL = config.get("app", "sms_gateway_url")
OTP_VALIDITY_SECONDS = config.getint("app", "otp_validity_seconds")
UPLOAD_DIR = config.get("app", "upload_dir")
SCHEME_DEADLINE = datetime.strptime(config.get("pension", "scheme_deadline"), "%Y-%m-%d %H:%M")
MIN_AGE = config.getint("pension", "min_age")
SLA_DAYS = config.getint("pension", "sla_days")

BLOCKS = ["Sonari", "Rajapara", "Dhemaji Pathar", "Borgaon", "Namti", "Khelua"]
# An applicant may hold at most one application in these states at a time.
# WITHDRAWN and REJECTED applications do not block a fresh application.
ACTIVE_STATUSES = ("PENDING", "APPROVED", "DEEMED_APPROVED")
DOC_EXTENSIONS = ("pdf", "jpg", "jpeg", "png")
DOC_MAX_BYTES = 5 * 1024 * 1024
MIN_REASON_CHARS = 10


def now_ist():
    """The portal's clock is Indian Standard Time regardless of where the server runs."""
    return datetime.now(IST).replace(tzinfo=None)


def to_ist(naive_server_time):
    """Timestamps are stored with the server's local clock (datetime.now()); show them in IST."""
    if naive_server_time is None:
        return None
    return naive_server_time.astimezone(IST).replace(tzinfo=None)


def get_db():
    return psycopg2.connect(
        host=config.get("database", "host"),
        port=config.get("database", "port"),
        dbname=config.get("database", "name"),
        user=config.get("database", "user"),
        password=config.get("database", "password"),
    )


def sanitize(value, maxlen=100):
    # Trim whitespace and cap at the column width, counting characters (not
    # bytes) so Assamese/Bangla names are stored intact.
    if value is None:
        return ""
    return value.strip()[:maxlen]


def hash_password(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def send_sms(mobile, text):
    try:
        requests.post(SMS_GATEWAY_URL + "/api/send", json={"to": mobile, "text": text}, timeout=5)
    except Exception as e:
        log.error("sms gateway error: %s", e)


def deadline_remaining():
    delta = SCHEME_DEADLINE - now_ist()
    if delta.total_seconds() <= 0:
        return None
    return int(delta.total_seconds() // 3600)


def new_application_no():
    return "SSP" + datetime.now().strftime("%y") + str(random.randint(100000, 999999))


def write_audit(cur, app_id, action, actor, note=""):
    cur.execute("INSERT INTO audit_log (application_id, action, actor, note, at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (app_id, action, actor, note, datetime.now()))


# ---------------------------------------------------------------------------
# Tables the assistant adds. Created on demand so an existing database needs no migration.
# ---------------------------------------------------------------------------

_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS officers (
    username      VARCHAR(50) PRIMARY KEY,
    display_name  VARCHAR(100),
    password_hash VARCHAR(80),
    block         VARCHAR(50),            -- NULL = all blocks
    active        BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS otp_failures (          -- wrong OTP guesses, for lockout
    mobile VARCHAR(15),
    at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_otp_failures_mobile ON otp_failures(mobile, at);
CREATE TABLE IF NOT EXISTS assistant_documents (   -- age proof received but not yet submitted
    mobile      VARCHAR(15) PRIMARY KEY,
    doc_path    VARCHAR(200),
    uploaded_at TIMESTAMP
);
"""


_schema_ready = False


def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(_EXTRA_SCHEMA)
    conn.commit()
    cur.close(); conn.close()
    _schema_ready = True


# ---------------------------------------------------------------------------
# Scheme rules: the single source of truth for what an application must satisfy.
# ---------------------------------------------------------------------------

def parse_dob(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def age_on(dob, on):
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def validate_application(data, doc_path):
    """Validate a complete application. Returns (errors, cleaned).

    `errors` is a list of plain-language problems (empty when valid);
    `cleaned` holds the normalised values ready for create_application().
    """
    errors = []
    cleaned = {
        "applicant_name": sanitize(data.get("applicant_name")),
        "village": (data.get("village") or "").strip(),
        "block": (data.get("block") or "").strip(),
        "gender": data.get("gender") or "",
        "marital_status": data.get("marital_status") or "",
        "husband_name": (data.get("husband_name") or "").strip(),
        "husband_employer": "",  # no longer collected (not a scheme requirement)
        "bank_account": re.sub(r"[\s-]", "", data.get("bank_account") or ""),
        "ifsc": re.sub(r"[\s-]", "", (data.get("ifsc") or "")).upper(),
        "doc_path": doc_path or "",
        "dob": None,
    }
    for label, key in (("full name", "applicant_name"), ("village", "village"),
                       ("block", "block"), ("bank account", "bank_account")):
        if not cleaned[key]:
            errors.append("%s is required" % label)
    if cleaned["block"] and cleaned["block"] not in BLOCKS:
        errors.append("block must be one of: " + ", ".join(BLOCKS))
    if cleaned["bank_account"] and not re.fullmatch(r"\d{9,18}", cleaned["bank_account"]):
        errors.append("bank account number must be 9 to 18 digits")
    if cleaned["ifsc"] and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", cleaned["ifsc"]):
        errors.append("IFSC must be 11 characters, e.g. SBIN0003077")
    dob = parse_dob(data.get("dob"))
    if dob is None:
        errors.append("date of birth must be given as DD/MM/YYYY")
    else:
        cleaned["dob"] = dob
        if age_on(dob, now_ist().date()) < MIN_AGE:
            errors.append("applicant must be %d years of age or above" % MIN_AGE)
    if not cleaned["doc_path"]:
        errors.append("age proof document is required")
    if now_ist() > SCHEME_DEADLINE:
        errors.append("the application window has closed")
    return errors, cleaned


def active_application(cur, mobile):
    """The applicant's current live application, if any."""
    cur.execute("SELECT id, application_no, status FROM applications "
                "WHERE mobile = %s AND status IN %s "
                "ORDER BY submitted_at DESC LIMIT 1", (mobile, ACTIVE_STATUSES))
    return cur.fetchone()


def create_application(cur, mobile, cleaned):
    """Insert a validated application; ensure the status-portal account exists.

    The caller commits. Returns (id, application_no).
    """
    app_no = new_application_no()
    cur.execute(
        """INSERT INTO applications
           (application_no, applicant_name, mobile, dob, gender, marital_status,
            husband_name, husband_employer, village, block, bank_account, ifsc,
            doc_path, status, submitted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
           RETURNING id""",
        (app_no, cleaned["applicant_name"], mobile, cleaned["dob"], cleaned["gender"],
         cleaned["marital_status"], cleaned["husband_name"], cleaned["husband_employer"],
         cleaned["village"], cleaned["block"], cleaned["bank_account"], cleaned["ifsc"],
         cleaned["doc_path"], datetime.now()))
    new_id = cur.fetchone()[0]

    # status portal account; password is DOB as DDMMYYYY per dept. circular
    portal_pass = cleaned["dob"].strftime("%d%m%Y")
    cur.execute("SELECT count(*) FROM portal_users WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES (%s,%s)",
                    (mobile, hash_password(portal_pass)))
    write_audit(cur, new_id, "SUBMIT", "applicant:%s" % mobile)
    return new_id, app_no


class Refused(Exception):
    """A rule stopped the action. `.problems` are plain-language reasons safe to show a citizen."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def submit_application_tx(mobile, data, doc_path):
    """The one way to create an application: validate, refuse a second active one, insert, audit.

    `mobile` must already be verified by the caller (OTP on the web form, OAuth sign-in for
    the assistant). Returns (id, application_no) or raises Refused.
    """
    errors, cleaned = validate_application(data, doc_path)
    if errors:
        raise Refused(errors)
    ensure_schema()
    conn = get_db()
    try:
        cur = conn.cursor()
        # two simultaneous submissions for one mobile must not both pass the duplicate check
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("apply:" + mobile,))
        existing = active_application(cur, mobile)
        if existing:
            raise Refused("application %s (%s) already exists for this mobile number; a pending "
                          "application can be withdrawn first if a fresh one is needed"
                          % (existing[1], existing[2]))
        new_id, app_no = create_application(cur, mobile, cleaned)
        cur.execute("DELETE FROM assistant_documents WHERE mobile = %s", (mobile,))
        conn.commit()
        return new_id, app_no
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def withdraw_application_tx(mobile, app_id=None):
    """Applicant withdraws their own PENDING application. Returns application_no or raises Refused."""
    conn = get_db()
    try:
        cur = conn.cursor()
        if app_id is None:
            cur.execute("SELECT id FROM applications WHERE mobile = %s AND status = 'PENDING' "
                        "ORDER BY submitted_at DESC LIMIT 1", (mobile,))
            row = cur.fetchone()
            app_id = row[0] if row else None
        row = None
        if app_id is not None:
            cur.execute("UPDATE applications SET status = 'WITHDRAWN', decided_at = %s, "
                        "decided_by = 'APPLICANT' WHERE id = %s AND mobile = %s "
                        "AND status = 'PENDING' RETURNING application_no",
                        (datetime.now(), app_id, mobile))
            row = cur.fetchone()
        if not row:
            raise Refused("only a pending application can be withdrawn")
        write_audit(cur, app_id, "WITHDRAW", "applicant:%s" % mobile)
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def decide_application_tx(app_id, new_status, officer, reason, officer_block=None):
    """An officer approves or rejects a PENDING application. The reason is mandatory and the
    decision is recorded against the officer's own username. Returns application_no or raises Refused."""
    if new_status not in ("APPROVED", "REJECTED"):
        raise Refused("decision must be APPROVED or REJECTED")
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON_CHARS:
        raise Refused("a reason of at least %d characters is required to %s an application"
                      % (MIN_REASON_CHARS, "approve" if new_status == "APPROVED" else "reject"))
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT block, status FROM applications WHERE id = %s FOR UPDATE", (app_id,))
        row = cur.fetchone()
        if not row:
            raise Refused("no such application")
        if officer_block and row[0] != officer_block:
            raise Refused("this application belongs to another block")
        if row[1] != "PENDING":
            raise Refused("only a pending application can be decided (this one is %s)" % row[1])
        cur.execute("UPDATE applications SET status = %s, decided_at = %s, decided_by = %s "
                    "WHERE id = %s RETURNING application_no, mobile",
                    (new_status, datetime.now(), officer, app_id))
        app_no, mobile = cur.fetchone()
        write_audit(cur, app_id, new_status, "officer:%s" % officer, reason[:500])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    send_sms(mobile, "Sewa Setu: your pension application %s has been %s."
             % (app_no, new_status.lower()))
    return app_no


# ---------------------------------------------------------------------------
# Age-proof documents
# ---------------------------------------------------------------------------

def save_document(mobile, filename, content):
    """Store an age-proof upload under the same rules as the web form. Returns the path or raises Refused.
    The file is stored as received: nothing here checks that it really proves anyone's age."""
    name = os.path.basename((filename or "").replace("\\", "/")).lower()
    if not name:
        raise Refused("please choose a file to upload (JPG, PNG or PDF, up to 5 MB)")
    if name.rsplit(".", 1)[-1] not in DOC_EXTENSIONS:
        raise Refused("please upload a JPG, PNG or PDF file")
    if len(content) > DOC_MAX_BYTES:
        raise Refused("file is larger than 5 MB; please upload a smaller file")
    if not content:
        raise Refused("the file is empty")
    name = re.sub(r"[^a-z0-9._-]", "_", name)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, "%s_%s" % (mobile, name))
    with open(path, "wb") as out:
        out.write(content)
    return path


def remember_document_tx(mobile, path):
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO assistant_documents (mobile, doc_path, uploaded_at) VALUES (%s,%s,%s) "
                "ON CONFLICT (mobile) DO UPDATE SET doc_path = EXCLUDED.doc_path, "
                "uploaded_at = EXCLUDED.uploaded_at", (mobile, path, datetime.now()))
    conn.commit()
    cur.close(); conn.close()


def pending_document(mobile):
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doc_path FROM assistant_documents WHERE mobile = %s", (mobile,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row[0] if row else ""


UPLOAD_LINK_TTL = 20 * 60

# OTP abuse limits, shared by the web form and the assistants' sign-in
OTP_MAX_WRONG = 5            # wrong guesses allowed ...
OTP_LOCK_SECONDS = 600       # ... within this window, then the number is locked until it passes
OTP_MAX_REQUESTS = 10        # OTPs that may be sent to one number within the same window


def otp_request_allowed(mobile):
    """False when too many OTPs were already sent to this number recently."""
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM otps WHERE mobile = %s AND created_at > now() - make_interval(secs => %s)",
                (mobile, OTP_LOCK_SECONDS))
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n < OTP_MAX_REQUESTS


def otp_verify(mobile, code):
    """Check an OTP. Returns 'ok', 'invalid', 'expired' or 'locked'. A correct code works once; too many
    wrong guesses lock the number for a while, even for the right code."""
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM otp_failures WHERE mobile = %s AND at > now() - make_interval(secs => %s)",
                    (mobile, OTP_LOCK_SECONDS))
        if cur.fetchone()[0] >= OTP_MAX_WRONG:
            return "locked"
        cur.execute("SELECT code, created_at FROM otps WHERE mobile = %s ORDER BY id DESC LIMIT 1", (mobile,))
        row = cur.fetchone()
        if row and hmac.compare_digest(row[0] or "", code or ""):
            if (datetime.now() - row[1]).total_seconds() > OTP_VALIDITY_SECONDS:
                return "expired"
            cur.execute("DELETE FROM otps WHERE mobile = %s", (mobile,))          # single use
            cur.execute("DELETE FROM otp_failures WHERE mobile = %s", (mobile,))
            return "ok"
        cur.execute("INSERT INTO otp_failures (mobile, at) VALUES (%s, now())", (mobile,))
        return "invalid"
    finally:
        conn.commit()
        cur.close(); conn.close()


def _upload_secret():
    return (os.environ.get("UPLOAD_LINK_SECRET") or config.get("app", "secret_key")).encode()


def make_upload_token(mobile, ttl=UPLOAD_LINK_TTL):
    """A bearer token that lets whoever holds it upload one age-proof file for this mobile, for a short while."""
    body = "%s.%d" % (mobile, int(time.time()) + ttl)
    sig = hmac.new(_upload_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return body + "." + sig


def mobile_from_upload_token(token):
    """The mobile a valid, unexpired upload token was issued for, else None."""
    try:
        mobile, exp, sig = token.split(".")
        good = hmac.new(_upload_secret(), ("%s.%s" % (mobile, exp)).encode(), hashlib.sha256).hexdigest()[:32]
        if hmac.compare_digest(sig, good) and int(exp) > time.time() and mobile.isdigit():
            return mobile
    except (ValueError, AttributeError):
        pass
    return None


def pending_document_info(mobile):
    """(file name, uploaded_at) of the age proof waiting for this mobile, or None."""
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doc_path, uploaded_at FROM assistant_documents WHERE mobile = %s", (mobile,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return (os.path.basename(row[0]), row[1]) if row else None


# ---------------------------------------------------------------------------
# Officers
# ---------------------------------------------------------------------------

def officer_for_login(username, password):
    """Return the officer row (username, display_name, block) if the credentials match, else None."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, display_name, block FROM officers "
                "WHERE username = %s AND password_hash = %s AND active", (username, hash_password(password)))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row


def officer_block(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT block FROM officers WHERE username = %s AND active", (username,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row[0] if row else None


def seed_officers_from_env():
    """OFFICERS="name:password:Block,name2:password2" (block optional) creates or updates officers."""
    spec = os.environ.get("OFFICERS", "").strip()
    if not spec:
        return
    ensure_schema()
    conn = get_db()
    cur = conn.cursor()
    for item in spec.split(","):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        block = parts[2] if len(parts) > 2 and parts[2] in BLOCKS else None
        cur.execute("INSERT INTO officers (username, display_name, password_hash, block) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (username) DO UPDATE SET "
                    "password_hash = EXCLUDED.password_hash, block = EXCLUDED.block, active = TRUE",
                    (parts[0], parts[0], hash_password(parts[1]), block))
    conn.commit()
    cur.close(); conn.close()
