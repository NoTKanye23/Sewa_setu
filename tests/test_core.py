"""Rules every channel must share. Needs a Postgres loaded with schema.sql + seed.sql;
point SEWASETU_CONFIG at an app.ini whose [database] reaches it."""
import os
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
