# -*- coding: utf-8 -*-
"""
Sewa Setu - Old Age Pension Portal
Government of Purvanchal, Department of Social Welfare

Developed by: Netlink Infosolutions Pvt Ltd (2023)
Maintained in-house since 02/2026.
"""

import os
import io
import random
from datetime import datetime, timedelta

import requests
from flask import (Flask, request, session, redirect, url_for, render_template,
                   flash, send_file, abort)
from fpdf import FPDF

import core
# The scheme rules and every write to the applications tables live in core.py, shared with
# the assistant MCP servers. They are re-exported here under their original names.
from core import (IST, now_ist, get_db, sanitize, hash_password, send_sms, deadline_remaining,
                  new_application_no, write_audit, parse_dob, age_on, validate_application,
                  active_application, create_application, Refused, ACTIVE_STATUSES, BLOCKS,
                  SMS_GATEWAY_URL, OTP_VALIDITY_SECONDS, UPLOAD_DIR, SCHEME_DEADLINE, MIN_AGE,
                  SLA_DAYS, config, BASE_DIR)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or config.get("app", "secret_key")
# Two hours: elderly applicants fill the form slowly; the draft lives in the session.
app.config["PERMANENT_SESSION_LIFETIME"] = 7200

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

import logging
_logdir = "/var/log/sewasetu"
try:
    os.makedirs(_logdir, exist_ok=True)
    _fh = logging.FileHandler(os.path.join(_logdir, "sewasetu-app.log"))
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s in app: %(message)s"))
    app.logger.addHandler(_fh)
    app.logger.setLevel(logging.INFO)
except Exception:
    pass

@app.template_filter("ist")
def _ist_filter(value):
    """Times are stored on the server's clock; people read Indian Standard Time."""
    return core.to_ist(value).strftime("%d/%m/%Y %I:%M %p IST") if isinstance(value, datetime) else value


try:
    core.ensure_schema()
    core.seed_officers_from_env()
except Exception as e:   # database not up yet; the first request that needs it will retry
    app.logger.warning("schema bootstrap deferred: %s" % e)


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", hours_left=deadline_remaining())


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/__gateway/", defaults={"subpath": ""})
@app.route("/__gateway/<path:subpath>")
def gateway_proxy(subpath):
    # Convenience proxy to the internal SMS gateway, so the OTP inbox is reachable
    # on the main site without opening a second port on the host. The gateway
    # itself runs only on the internal network.
    try:
        r = requests.get(SMS_GATEWAY_URL + "/" + subpath,
                         params=request.args, timeout=5)
        return (r.content, r.status_code,
                {"Content-Type": r.headers.get("Content-Type", "text/html")})
    except Exception as e:
        return ("SMS gateway unreachable: %s" % e, 502)


# ---------------------------------------------------------------------------
# Application flow: mobile -> OTP -> form steps -> upload -> declaration
# ---------------------------------------------------------------------------

@app.route("/apply", methods=["GET", "POST"])
def apply():
    if deadline_remaining() is None:
        flash("The application window for this scheme has closed.")
        return redirect(url_for("index"))
    if request.method == "POST":
        mobile = request.form.get("mobile", "").strip()
        captcha = request.form.get("captcha", "")
        try:
            captcha_ok = int(captcha) == session.get("captcha_answer")
        except (ValueError, TypeError):
            captcha_ok = False
        if not captcha_ok:
            flash("Security check answer is incorrect. Please try again.")
            return render_template("apply.html", captcha_q=make_captcha())
        if len(mobile) != 10 or not mobile.isdigit():
            flash("Please enter a valid 10-digit mobile number.")
            return render_template("apply.html", captcha_q=make_captcha())
        if not core.otp_request_allowed(mobile):
            flash("Too many OTP requests for this number. Please wait a few minutes and try again.")
            return render_template("apply.html", captcha_q=make_captcha())
        code = str(random.randint(100000, 999999))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO otps (mobile, code, created_at) VALUES (%s, %s, %s)",
                    (mobile, code, datetime.now()))
        conn.commit()
        cur.close(); conn.close()
        send_sms(mobile, "Your Sewa Setu OTP is %s. Valid for 5 minutes." % code)
        session.permanent = True
        session["apply_mobile"] = mobile
        return redirect(url_for("verify"))
    return render_template("apply.html", captcha_q=make_captcha())


def make_captcha():
    a, b = random.randint(1, 9), random.randint(1, 9)
    session["captcha_answer"] = a + b
    return "%d + %d" % (a, b)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    mobile = session.get("apply_mobile")
    if not mobile:
        return redirect(url_for("apply"))
    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        result = core.otp_verify(mobile, code)
        if result == "ok":
            session["verified_mobile"] = mobile
            return redirect(url_for("form_step", step=1))
        if result == "expired":
            flash("OTP expired. Please request a new OTP.")
            return redirect(url_for("apply"))
        if result == "locked":
            flash("Too many wrong attempts. Please wait a few minutes before trying again.")
        else:
            flash("Invalid OTP.")
    return render_template("verify.html", mobile=mobile)


@app.route("/form/<int:step>", methods=["GET", "POST"])
def form_step(step):
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if step not in (1, 2, 3):
        abort(404)
    if request.method == "POST":
        data = session.get("form_data", {})
        for k, v in request.form.items():
            data[k] = v
        session["form_data"] = data
        if step < 3:
            return redirect(url_for("form_step", step=step + 1))
        return redirect(url_for("upload"))
    return render_template("form_step%d.html" % step,
                           data=session.get("form_data", {}), blocks=BLOCKS)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        f = request.files.get("document")
        if f is None or f.filename == "":
            flash("Please choose a file to upload (JPG, PNG or PDF, up to 5 MB).")
            return render_template("upload.html")
        try:
            path = core.save_document(session["verified_mobile"], f.filename, f.read())
        except Refused as r:
            flash("%s." % r.problems[0].capitalize())
            return render_template("upload.html")
        session["doc_path"] = path
        return redirect(url_for("declaration"))
    return render_template("upload.html")


@app.route("/declaration", methods=["GET", "POST"])
def declaration():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        return handle_submission()
    # Preview everything before the applicant commits.
    data = session.get("form_data", {})
    errors, _ = validate_application(data, session.get("doc_path", ""))
    return render_template("declaration.html", data=data, errors=errors,
                           doc_name=os.path.basename(session.get("doc_path", "") or ""))


def handle_submission():
    mobile = session.get("verified_mobile")
    data = session.get("form_data", {})
    try:
        new_id, app_no = core.submit_application_tx(mobile, data, session.get("doc_path", ""))
    except Refused as r:
        if any("already exists" in p for p in r.problems):
            flash("%s." % r.problems[0].capitalize())
            return redirect(url_for("index"))
        flash("Please correct the following before submitting: " + "; ".join(r.problems) + ".")
        return redirect(url_for("form_step", step=1))

    session.pop("form_data", None)
    session.pop("doc_path", None)
    session.pop("verified_mobile", None)
    # The applicant just proved ownership of this mobile by OTP, so let them
    # see their application and download the acknowledgment without a second login.
    session["logged_in"] = True
    session["portal_mobile"] = mobile
    send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal "
                     "with mobile no. and password (DOB as DDMMYYYY)." % app_no)
    return render_template("confirmation.html", app_no=app_no, app_id=new_id)


def generate_acknowledgment(cur, app_id):
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, submitted_at, status FROM applications WHERE id = %s",
                (app_id,))
    row = cur.fetchone()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "GOVERNMENT OF PURVANCHAL", ln=1, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "Department of Social Welfare", ln=1, align="C")
    pdf.cell(0, 8, "Old Age Pension Scheme - Acknowledgment", ln=1, align="C")
    pdf.ln(4)
    # letterhead rules
    pdf.line(10, 12, 200, 12)
    pdf.line(10, 282, 200, 282)
    labels = ["Application No", "Applicant Name", "Mobile", "Date of Birth",
              "Village", "Block", "Bank Account", "IFSC", "Submitted At", "Status"]
    pdf.set_font("Helvetica", "", 10)
    for label, val in zip(labels, row):
        try:
            pdf.cell(60, 8, label, border=1)
            pdf.cell(0, 8, str(val), border=1, ln=1)
        except Exception:
            pdf.cell(0, 8, "?", border=1, ln=1)
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, "This is a computer generated acknowledgment. Processing SLA "
                         "as per the Purvanchal Right to Public Services Act applies.")
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Status portal (citizen login)
# ---------------------------------------------------------------------------

@app.route("/status", methods=["GET", "POST"])
def status_login():
    if request.method == "POST":
        mobile = request.form.get("mobile", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM portal_users WHERE mobile = %s", (mobile,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0] == hash_password(password):
            session["logged_in"] = True
            session["portal_mobile"] = mobile
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM applications WHERE mobile = %s "
                        "ORDER BY submitted_at DESC LIMIT 1", (mobile,))
            r = cur.fetchone()
            cur.close(); conn.close()
            if r:
                return redirect(url_for("view_application", app_id=r[0]))
            flash("No application found for this mobile number.")
            return redirect(url_for("status_login"))
        flash("Mobile number or password is incorrect. The password is your date of "
              "birth as DDMMYYYY.")
    return render_template("status_login.html")


@app.route("/application/<int:app_id>")
def view_application(app_id):
    if not session.get("logged_in"):
        flash("Please login to view application status.")
        return redirect(url_for("status_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, status, submitted_at, decided_at "
                "FROM applications WHERE id = %s AND mobile = %s",
                (app_id, session.get("portal_mobile")))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        abort(404)
    return render_template("application.html", a=row, app_id=app_id)


@app.route("/application/<int:app_id>/withdraw", methods=["POST"])
def withdraw_application(app_id):
    """Applicant withdraws their own PENDING application so they can apply afresh."""
    if not session.get("logged_in") or not session.get("portal_mobile"):
        flash("Please login to manage your application.")
        return redirect(url_for("status_login"))
    mobile = session["portal_mobile"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE applications SET status = 'WITHDRAWN', decided_at = %s, "
                "decided_by = 'APPLICANT' WHERE id = %s AND mobile = %s "
                "AND status = 'PENDING' RETURNING application_no",
                (datetime.now(), app_id, mobile))
    row = cur.fetchone()
    if row:
        write_audit(cur, app_id, "WITHDRAW", "applicant:%s" % mobile)
        conn.commit()
        flash("Application %s has been withdrawn. You may submit a new application." % row[0])
    else:
        flash("Only a pending application can be withdrawn.")
    cur.close(); conn.close()
    return redirect(url_for("view_application", app_id=app_id))


@app.route("/ack/<int:app_id>.pdf")
def ack_pdf(app_id):
    if not session.get("admin"):
        conn0 = get_db(); c0 = conn0.cursor()
        c0.execute("SELECT 1 FROM applications WHERE id = %s AND mobile = %s",
                   (app_id, session.get("portal_mobile")))
        owned = c0.fetchone()
        c0.close(); conn0.close()
        if not owned:
            abort(403)
    path = os.path.join(UPLOAD_DIR, "ack", "%d.pdf" % app_id)
    if not os.path.exists(path):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM applications WHERE id = %s", (app_id,))
        if not cur.fetchone():
            cur.close(); conn.close()
            abort(404)
        data = generate_acknowledgment(cur, app_id)
        cur.close(); conn.close()
        return send_file(io.BytesIO(data), mimetype="application/pdf")
    return send_file(path, mimetype="application/pdf")


@app.route("/status/reset", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        mobile = request.form.get("mobile", "")
        conn = get_db()
        cur = conn.cursor()
        # fetch account for reset
        cur.execute("SELECT mobile FROM portal_users WHERE mobile = %s", (mobile,))
        row = cur.fetchone()
        if row:
            cur.execute("SELECT dob FROM applications WHERE mobile = %s LIMIT 1", (mobile,))
            r2 = cur.fetchone()
            if r2:
                newpass = r2[0].strftime("%d%m%Y")
                cur.execute("UPDATE portal_users SET password_hash = %s WHERE mobile = %s",
                            (hash_password(newpass), row[0]))
                conn.commit()
                send_sms(row[0], "Sewa Setu: your password has been reset to your "
                                 "date of birth (DDMMYYYY).")
        cur.close(); conn.close()
        flash("If the mobile number exists, the password has been reset and sent by SMS.")
    return render_template("reset.html")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (ADMIN_PASSWORD and request.form.get("username") == ADMIN_USERNAME and
                request.form.get("password") == ADMIN_PASSWORD):
            session["logged_in"] = True
            session["admin"] = True
            session["admin_user"] = ADMIN_USERNAME
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM applications GROUP BY status")
    by_status = cur.fetchall()
    cur.execute("SELECT count(*) FROM applications WHERE status = 'PENDING' "
                "AND submitted_at < %s", (datetime.now() - timedelta(days=SLA_DAYS),))
    overdue = cur.fetchone()[0]
    cur.execute("SELECT block, count(*) FROM applications WHERE status = 'PENDING' "
                "GROUP BY block ORDER BY count(*) DESC")
    by_block = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_dashboard.html", by_status=by_status,
                           overdue=overdue, by_block=by_block, sla=SLA_DAYS)


@app.route("/admin/applications")
def admin_list():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    status = request.args.get("status", "PENDING")
    mobile = request.args.get("mobile", "").strip()
    page = int(request.args.get("page", 1))
    conn = get_db()
    cur = conn.cursor()
    if mobile:
        cur.execute("SELECT id, application_no, applicant_name, mobile, block, status, "
                    "submitted_at FROM applications WHERE mobile = %s ORDER BY submitted_at ASC",
                    (mobile,))
        status = "mobile %s" % mobile
    else:
        cur.execute("SELECT id, application_no, applicant_name, mobile, block, status, "
                    "submitted_at FROM applications WHERE status = %s "
                    "ORDER BY submitted_at ASC LIMIT 50 OFFSET %s",
                    (status, (page - 1) * 50))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_list.html", rows=rows, status=status, page=page, mobile=mobile)


@app.route("/admin/application/<int:app_id>")
def admin_view(app_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, gender, "
                "marital_status, village, block, bank_account, ifsc, doc_path, "
                "status, submitted_at, decided_at, decided_by "
                "FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        abort(404)
    cur.execute("SELECT at, action, actor, note FROM audit_log "
                "WHERE application_id = %s ORDER BY at ASC", (app_id,))
    audit = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_view.html", a=row, app_id=app_id, audit=audit)


def _admin_decide(app_id, new_status, note):
    """Approve/reject a PENDING application, recording who decided and when."""
    if not session.get("admin"):
        abort(403)
    actor = session.get("admin_user", ADMIN_USERNAME)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE applications SET status = %s, decided_at = %s, decided_by = %s "
                "WHERE id = %s AND status = 'PENDING' RETURNING application_no",
                (new_status, datetime.now(), actor, app_id))
    row = cur.fetchone()
    if row:
        write_audit(cur, app_id, new_status, "admin:%s" % actor, note)
        conn.commit()
        flash("Application %s %s." % (row[0], new_status.lower()))
    else:
        flash("Only a pending application can be decided.")
    cur.close(); conn.close()
    return redirect(url_for("admin_view", app_id=app_id))


@app.route("/admin/approve/<int:app_id>", methods=["POST"])
def admin_approve(app_id):
    return _admin_decide(app_id, "APPROVED", request.form.get("note", ""))


@app.route("/admin/reject/<int:app_id>", methods=["POST"])
def admin_reject(app_id):
    return _admin_decide(app_id, "REJECTED", request.form.get("note", ""))


from oauth import oauth as _oauth_blueprint   # noqa: E402  (needs the definitions above)
app.register_blueprint(_oauth_blueprint)

from uploadlink import uploadlink as _uploadlink_blueprint   # noqa: E402
app.register_blueprint(_uploadlink_blueprint)

from evaldash import evaldash as _evaldash_blueprint   # noqa: E402
app.register_blueprint(_evaldash_blueprint)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
