"""Rules every channel must share. Needs a Postgres loaded with schema.sql + seed.sql;
point SEWASETU_CONFIG at an app.ini whose [database] reaches it."""
import os
import sys
import time
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import core  # noqa: E402

core.UPLOAD_DIR = tempfile.mkdtemp()
GOOD = {"applicant_name": "Sukhdei Devi", "village": "Rampur", "block": "Khelua",
        "dob": "01/01/1950", "bank_account": "123456789012", "ifsc": "SBIN0003077",
        "gender": "Female", "marital_status": "Widow"}
MOBILE = "9000000001"


def wipe(mobile):
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM audit_log WHERE application_id IN (SELECT id FROM applications WHERE mobile=%s)", (mobile,))
    cur.execute("DELETE FROM applications WHERE mobile=%s", (mobile,))
    cur.execute("DELETE FROM portal_users WHERE mobile=%s", (mobile,))
    conn.commit(); conn.close()


class CoreRules(unittest.TestCase):
    def setUp(self):
        core.ensure_schema()
        wipe(MOBILE)

    tearDown = lambda self: wipe(MOBILE)

    def test_submit_then_duplicate_refused(self):
        core.submit_application_tx(MOBILE, GOOD, "/x/doc.pdf")
        with self.assertRaises(core.Refused) as r:
            core.submit_application_tx(MOBILE, GOOD, "/x/doc.pdf")
        self.assertIn("already exists", str(r.exception))

    def test_underage_and_bad_fields_refused(self):
        bad = dict(GOOD, dob="01/01/1990", ifsc="BAD", bank_account="12ab")
        with self.assertRaises(core.Refused) as r:
            core.submit_application_tx(MOBILE, bad, "/x/doc.pdf")
        self.assertGreaterEqual(len(r.exception.problems), 3)

    def test_missing_document_refused(self):
        with self.assertRaises(core.Refused):
            core.submit_application_tx(MOBILE, GOOD, "")

    def test_withdraw_allows_reapply(self):
        core.submit_application_tx(MOBILE, GOOD, "/x/doc.pdf")
        core.withdraw_application_tx(MOBILE)
        core.submit_application_tx(MOBILE, GOOD, "/x/doc.pdf")

    def test_decision_needs_reason_and_own_block(self):
        app_id, _ = core.submit_application_tx(MOBILE, GOOD, "/x/doc.pdf")
        with self.assertRaises(core.Refused):
            core.decide_application_tx(app_id, "APPROVED", "meena", "")
        with self.assertRaises(core.Refused):
            core.decide_application_tx(app_id, "APPROVED", "meena", "documents fine", officer_block="Sonari")
        core.decide_application_tx(app_id, "REJECTED", "meena", "age proof is not legible", officer_block="Khelua")
        with self.assertRaises(core.Refused):
            core.decide_application_tx(app_id, "APPROVED", "meena", "changed my mind")
        conn = core.get_db(); cur = conn.cursor()
        cur.execute("SELECT decided_by FROM applications WHERE id=%s", (app_id,))
        self.assertEqual(cur.fetchone()[0], "meena")
        cur.execute("SELECT actor, note FROM audit_log WHERE application_id=%s AND action='REJECTED'", (app_id,))
        self.assertEqual(cur.fetchone(), ("officer:meena", "age proof is not legible"))
        conn.close()

    def test_document_rules(self):
        path = core.save_document(MOBILE, "../../etc/Proof.PDF", b"%PDF-1.4")
        self.assertTrue(path.startswith(core.UPLOAD_DIR))
        for name, body in (("x.exe", b"a"), ("x.pdf", b""), ("x.pdf", b"a" * (5 * 1024 * 1024 + 1))):
            with self.assertRaises(core.Refused):
                core.save_document(MOBILE, name, body)


class WindowAndAge(unittest.TestCase):
    """The scheme window and the 60-year rule, on IST dates, with the clock held still."""

    def errors(self, now, dob):
        with mock.patch.object(core, "now_ist", return_value=now):
            errs, _ = core.validate_application(dict(GOOD, dob=dob), "/x/doc.pdf")
        return errs

    def test_window_closed_after_deadline(self):
        after = core.SCHEME_DEADLINE + timedelta(minutes=1)
        self.assertTrue(any("window has closed" in e for e in self.errors(after, "01/01/1950")))

    def test_window_open_just_before_deadline(self):
        before = core.SCHEME_DEADLINE - timedelta(minutes=1)
        self.assertEqual(self.errors(before, "01/01/1950"), [])

    def test_exactly_sixty_today_is_accepted_and_a_day_short_is_not(self):
        today = datetime(2026, 10, 24, 12, 0)
        self.assertEqual(self.errors(today, "24/10/1966"), [])
        self.assertTrue(any("years of age" in e for e in self.errors(today, "25/10/1966")))

    def test_age_counts_on_the_indian_date(self):
        # 00:10 IST on the 25th is still the 24th in UTC; the person turned 60 on the 25th by the Indian calendar
        just_after_midnight_ist = datetime(2026, 10, 25, 0, 10)
        self.assertEqual(self.errors(just_after_midnight_ist, "25/10/1966"), [])

    def test_times_are_shown_in_ist(self):
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"; time.tzset()
        try:
            self.assertEqual(core.to_ist(datetime(2026, 10, 24, 4, 41)), datetime(2026, 10, 24, 10, 11))
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


class OtpLimits(unittest.TestCase):
    MOBILE = "9000000002"

    def clean(self):
        conn = core.get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM otps WHERE mobile=%s", (self.MOBILE,))
        cur.execute("DELETE FROM otp_failures WHERE mobile=%s", (self.MOBILE,))
        conn.commit(); conn.close()

    def send(self, code="123456"):
        conn = core.get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO otps (mobile, code, created_at) VALUES (%s,%s,%s)", (self.MOBILE, code, datetime.now()))
        conn.commit(); conn.close()

    def setUp(self):
        core.ensure_schema(); self.clean()

    tearDown = lambda self: self.clean()

    def test_correct_code_works_once(self):
        self.send()
        self.assertEqual(core.otp_verify(self.MOBILE, "123456"), "ok")
        self.assertEqual(core.otp_verify(self.MOBILE, "123456"), "invalid")

    def test_wrong_guesses_lock_the_number_even_for_the_right_code(self):
        self.send()
        for _ in range(core.OTP_MAX_WRONG):
            self.assertEqual(core.otp_verify(self.MOBILE, "000000"), "invalid")
        self.assertEqual(core.otp_verify(self.MOBILE, "123456"), "locked")

    def test_expired_code_is_reported(self):
        conn = core.get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO otps (mobile, code, created_at) VALUES (%s,%s,%s)",
                    (self.MOBILE, "123456", datetime.now() - timedelta(seconds=core.OTP_VALIDITY_SECONDS + 5)))
        conn.commit(); conn.close()
        self.assertEqual(core.otp_verify(self.MOBILE, "123456"), "expired")

    def test_too_many_requests_are_refused(self):
        for _ in range(core.OTP_MAX_REQUESTS - 1):
            self.send()
        self.assertTrue(core.otp_request_allowed(self.MOBILE))
        self.send()
        self.assertFalse(core.otp_request_allowed(self.MOBILE))


if __name__ == "__main__":
    unittest.main()
