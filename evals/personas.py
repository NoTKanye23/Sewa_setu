# -*- coding: utf-8 -*-
"""The people we invented. Each persona has a profile (shown on /eval-dashboard), a script that plays
them through the real MCP endpoints, and the database state that must be true afterwards."""

import os
import base64
from datetime import datetime

import core

PDF = base64.b64encode(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF").decode()
IST_FMT = "%d/%m/%Y"

PERSONAS = []


def persona(**profile):
    def deco(fn):
        PERSONAS.append({**profile, "run": fn})
        return fn
    return deco


def details(**over):
    d = dict(applicant_name="Sukhdei Devi", date_of_birth="14/03/1952", village="Rampur", block="Khelua",
             bank_account="1234 5678 9012", ifsc="sbin0003077", gender="female", marital_status="widowed",
             confirmed_by_applicant=True)
    d.update(over)
    return d


async def apply_ok(s, mobile, **over):
    await s.call("attach_age_proof", filename="age_proof.pdf", content_base64=PDF)
    return await s.call("submit_application", **details(**over))


def rows(ctx, mobile):
    return ctx.db("SELECT status, applicant_name, village, block, bank_account, ifsc, decided_by, doc_path "
                  "FROM applications WHERE mobile = %s ORDER BY id", (mobile,))


# ---------------------------------------------------------------- applicants

@persona(id="sukhdei", role="citizen", name="Sukhdei Devi", age=73, village="Rampur", block="Khelua", mobile="9100000001",
         voice="Speaks slowly in Bhojpuri-Hindi, gives her bank number with spaces, cannot read the screen; her grandson Pappu holds the phone.",
         wants="Apply for the pension she heard about at the panchayat.",
         must_be_true="One PENDING application for her mobile with the bank number stored without spaces, IFSC in capitals, and a SUBMIT audit entry by that mobile.")
async def sukhdei(ctx, s):
    mobile = ctx.mobile
    info = await s.call("get_scheme_info")
    ctx.check("assistant sees only the 6 citizen tools", sorted(t for t in await s.tool_names()),
              sorted(["get_scheme_info", "get_my_application", "attach_age_proof", "check_application",
                      "submit_application", "withdraw_application"]))
    pre = await s.call("check_application", **{k: v for k, v in details().items() if k != "confirmed_by_applicant"})
    ctx.check("check_application says the details are not yet complete without a document", pre["ok"], False)
    r = await apply_ok(s, mobile)
    ctx.check("submit accepted", r["ok"], True)
    got = rows(ctx, mobile)
    ctx.check("exactly one application row", len(got), 1)
    ctx.check("status is PENDING, not approved", got[0][0], "PENDING")
    ctx.check("bank account stored without spaces", got[0][4], "123456789012")
    ctx.check("IFSC upper-cased", got[0][5], "SBIN0003077")
    ctx.check("audit SUBMIT recorded for the mobile",
              ctx.db("SELECT actor FROM audit_log a JOIN applications p ON p.id=a.application_id "
                     "WHERE p.mobile=%s AND a.action='SUBMIT'", (mobile,)), [("applicant:" + mobile,)])
    ctx.check("assistant did not promise approval", "not approved yet" in r["message"], True)


@persona(id="ramnath", role="citizen", name="Ramnath Yadav", age=59, village="Dhemaji Kalan", block="Dhemaji Pathar", mobile="9100000002",
         voice="Brisk, says he is 'almost sixty', wants it done today.",
         wants="Apply even though his 60th birthday is months away.",
         must_be_true="Nothing is filed. The refusal names the age rule.")
async def ramnath(ctx, s):
    from datetime import date
    today = datetime.now().date()
    dob = date(today.year - 59, today.month, min(today.day, 28)).strftime(IST_FMT)   # exactly 59 today
    r = await apply_ok(s, ctx.mobile, applicant_name="Ramnath Yadav", date_of_birth=dob, village="Dhemaji Kalan", block="Dhemaji Pathar", gender="male", marital_status="married")
    ctx.check("submit refused", r["ok"], False)
    ctx.check("refusal mentions the age rule", any("years of age" in p for p in r["problems"]), True)
    ctx.check("no application row", len(rows(ctx, ctx.mobile)), 0)


@persona(id="phoolmati", role="citizen", name="Phoolmati Devi", age=66, village="Barhaj", block="Borgaon", mobile="9100000003",
         voice="Reads her passbook aloud and makes mistakes: drops digits, says the IFSC with spaces and a letter O for zero.",
         wants="Apply, but her first attempt has a short account number and a wrong IFSC.",
         must_be_true="First attempt files nothing. After correction there is exactly one PENDING application with the corrected values.")
async def phoolmati(ctx, s):
    bad = await apply_ok(s, ctx.mobile, applicant_name="Phoolmati Devi", village="Barhaj", block="Borgaon", bank_account="12345678", ifsc="SBIN OO03077")
    ctx.check("first attempt refused", bad["ok"], False)
    ctx.check("both problems reported", len(bad["problems"]), 2)
    ctx.check("nothing stored after refusal", len(rows(ctx, ctx.mobile)), 0)
    good = await s.call("submit_application", **details(applicant_name="Phoolmati Devi", village="Barhaj", block="Borgaon", bank_account="9876-5432-1098", ifsc="SBIN0003077"))
    ctx.check("corrected attempt accepted", good["ok"], True)
    ctx.check("one row with corrected account", [r[4] for r in rows(ctx, ctx.mobile)], ["987654321098"])


@persona(id="bhola", role="citizen", name="Bhola Ram", age=81, village="Chakia", block="Namti", mobile="9100000004",
         voice="Forgetful; applied last month and has forgotten, asks again.",
         wants="Apply (he thinks he has not yet).",
         must_be_true="Still only the original application; the second attempt is refused and explained, not silently duplicated.")
async def bhola(ctx, s):
    ctx.seed_application(ctx.mobile, name="Bhola Ram", village="Chakia", block="Namti", dob="02/02/1945")
    r = await apply_ok(s, ctx.mobile, applicant_name="Bhola Ram", village="Chakia", block="Namti", date_of_birth="02/02/1945", gender="male")
    ctx.check("second application refused", r["ok"], False)
    ctx.check("reason says one already exists", any("already exists" in p for p in r["problems"]), True)
    ctx.check("still exactly one application", len(rows(ctx, ctx.mobile)), 1)
    mine = await s.call("get_my_application")
    ctx.check("status tool shows the existing one", mine["applications"][0]["status"], "PENDING")


@persona(id="gulabo", role="citizen", name="Gulabo Devi", age=68, village="Sitapur", block="Rajapara", mobile="9100000005",
         voice="Realises she gave the wrong village, wants to start again.",
         wants="Withdraw her pending application and file a corrected one.",
         must_be_true="First application WITHDRAWN (by the applicant, audited); a second PENDING one with the right village.")
async def gulabo(ctx, s):
    a = await apply_ok(s, ctx.mobile, applicant_name="Gulabo Devi", village="Sitapar", block="Rajapara", date_of_birth="05/05/1956")
    ctx.check("first filed", a["ok"], True)
    no = await s.call("withdraw_application", confirmed_by_applicant=False)
    ctx.check("withdraw without confirmation does nothing", no["ok"], False)
    w = await s.call("withdraw_application", confirmed_by_applicant=True)
    ctx.check("withdrawn", w["ok"], True)
    b = await apply_ok(s, ctx.mobile, applicant_name="Gulabo Devi", village="Sitapur", block="Rajapara", date_of_birth="05/05/1956")
    ctx.check("fresh application accepted", b["ok"], True)
    ctx.check("statuses", [r[0] for r in rows(ctx, ctx.mobile)], ["WITHDRAWN", "PENDING"])
    ctx.check("withdraw audited", ctx.db("SELECT count(*) FROM audit_log a JOIN applications p ON p.id=a.application_id "
                                         "WHERE p.mobile=%s AND a.action='WITHDRAW'", (ctx.mobile,)), [(1,)])


@persona(id="munna", role="citizen", name="Munna (grandson at a kiosk)", age=24, village="Rampur", block="Khelua", mobile="9100000006",
         voice="Fast and casual; files for his grandmother, then tries the same phone for his other grandmother.",
         wants="Two applications from one shared phone number.",
         must_be_true="Only the first is filed. The second is refused because the mobile already holds an active application, exactly as at the counter.")
async def munna(ctx, s):
    one = await apply_ok(s, ctx.mobile, applicant_name="Kaushalya Devi", date_of_birth="01/01/1950", village="Rampur")
    two = await apply_ok(s, ctx.mobile, applicant_name="Dhanpati Devi", date_of_birth="03/03/1948", village="Rampur")
    ctx.check("first filed", one["ok"], True)
    ctx.check("second refused", two["ok"], False)
    ctx.check("one active application only", [r[1] for r in rows(ctx, ctx.mobile)], ["Kaushalya Devi"])


@persona(id="unsure", role="citizen", name="Parvati Devi", age=70, village="Lalpur", block="Sonari", mobile="9100000007",
         voice="Says 'yes' to everything without listening; never actually confirmed the details.",
         wants="Be helped, but has not confirmed. Also has not given any age proof.",
         must_be_true="A submit without confirmation, and one without a document, both file nothing.")
async def unsure(ctx, s):
    no_confirm = await s.call("submit_application", **details(applicant_name="Parvati Devi", confirmed_by_applicant=False))
    ctx.check("unconfirmed submit refused", no_confirm["ok"], False)
    no_doc = await s.call("submit_application", **details(applicant_name="Parvati Devi"))
    ctx.check("submit without age proof refused", no_doc["ok"], False)
    ctx.check("reason names the missing document", any("age proof" in p for p in no_doc["problems"]), True)
    ctx.check("nothing stored", len(rows(ctx, ctx.mobile)), 0)


@persona(id="traversal", role="citizen", name="Bachchu Lal", age=75, village="Mathia", block="Namti", mobile="9100000008",
         voice="His grandson sends the photo from a phone whose file name is '../../etc/passwd.pdf'.",
         wants="Attach his age proof.",
         must_be_true="The file is stored inside the upload folder under a safe name; bad types are refused.")
async def traversal(ctx, s):
    ok = await s.call("attach_age_proof", filename="../../etc/passwd.pdf", content_base64=PDF)
    ctx.check("upload accepted", ok["ok"], True)
    path = ctx.db("SELECT doc_path FROM assistant_documents WHERE mobile=%s", (ctx.mobile,))[0][0]
    ctx.check("stored under the upload directory", os.path.realpath(path).startswith(os.path.realpath(core.UPLOAD_DIR)), True)
    bad = await s.call("attach_age_proof", filename="proof.exe", content_base64=PDF)
    ctx.check("executable refused", bad["ok"], False)


@persona(id="privacy", role="citizen", name="Nosy neighbour", age=62, village="Rampur", block="Khelua", mobile="9100000009",
         voice="Signs in with her own number and asks the assistant about 'Sukhdei's application'.",
         wants="See somebody else's application.",
         must_be_true="Her status shows only her own number. She cannot reach the officer endpoint with her citizen token.")
async def privacy(ctx, s):
    ctx.seed_application("9100000010", name="Someone Else", village="Rampur", block="Khelua", dob="01/01/1950")
    mine = await s.call("get_my_application")
    ctx.check("sees nothing of another mobile", mine["applications"], [])
    status = await ctx.raw_post(ctx.officer_url, s.access_token)
    ctx.check("citizen token rejected at the officer endpoint", status, 401)


# ---------------------------------------------------------------- officers

@persona(id="meena", role="officer", name="Meena Kumari (Khelua block officer)", age=41, village="Khelua", block="Khelua", mobile="9100000011",
         voice="Efficient and terse; dictates decisions between meetings, sometimes forgets to give a reason.",
         wants="See what is waiting, review a case, reject one, approve another.",
         must_be_true="Rejection without a reason is refused. With a reason the application is REJECTED, decided_by 'meena', and the audit row has actor officer:meena and her reason. A second decision on the same case is refused.")
async def meena(ctx, s):
    a = ctx.seed_application("9100000012", name="Test Case Reject", village="Rampur", block="Khelua", dob="01/01/1950")
    b = ctx.seed_application("9100000013", name="Test Case Approve", village="Rampur", block="Khelua", dob="01/01/1950")
    ctx.check("officer sees exactly the officer tools", sorted(await s.tool_names()),
              ["approve_application", "get_case", "list_pending", "queue_summary", "reject_application"])
    q = await s.call("list_pending", limit=5)
    ctx.check("queue limited to Khelua", {x["block"] for x in q["applications"]}, {"Khelua"})
    case = await s.call("get_case", application_id=a)
    ctx.check("bank account is masked", "*" in case["bank"]["account"], True)
    ctx.check("age proof stated as unverified", "not machine-verified" in case["age_proof"]["note"], True)
    empty = await s.call("reject_application", application_id=a, reason="")
    ctx.check("reject without reason refused", empty["ok"], False)
    ctx.check("still pending", ctx.db("SELECT status FROM applications WHERE id=%s", (a,)), [("PENDING",)])
    rej = await s.call("reject_application", application_id=a, reason="Age proof does not show a date of birth.")
    ctx.check("reject with reason accepted", rej["ok"], True)
    ctx.check("decision recorded against meena", ctx.db("SELECT status, decided_by FROM applications WHERE id=%s", (a,)), [("REJECTED", "meena")])
    ctx.check("audit row carries officer and reason", ctx.db("SELECT actor, note FROM audit_log WHERE application_id=%s AND action='REJECTED'", (a,)),
              [("officer:meena", "Age proof does not show a date of birth.")])
    again = await s.call("approve_application", application_id=a, reason="Changed my mind about this case.")
    ctx.check("second decision on same case refused", again["ok"], False)
    ok = await s.call("approve_application", application_id=b, reason="Documents and bank details are in order.")
    ctx.check("approval accepted", ok["ok"], True)
    ctx.check("approved by meena", ctx.db("SELECT status, decided_by FROM applications WHERE id=%s", (b,)), [("APPROVED", "meena")])


@persona(id="ravi", role="officer", name="Ravi Shankar (Sonari block officer)", age=38, village="Sonari", block="Sonari", mobile="9100000014",
         voice="Curious; tries a Khelua case he was not given.",
         wants="Decide on an application in another block.",
         must_be_true="Refused; the Khelua application is untouched and still PENDING.")
async def ravi(ctx, s):
    k = ctx.seed_application("9100000015", name="Khelua Case", village="Rampur", block="Khelua", dob="01/01/1950")
    view = await s.call("get_case", application_id=k)
    ctx.check("cannot open another block's case", view["ok"], False)
    dec = await s.call("approve_application", application_id=k, reason="Looks fine to me, approving it.")
    ctx.check("cannot decide another block's case", dec["ok"], False)
    ctx.check("application untouched", ctx.db("SELECT status, decided_by FROM applications WHERE id=%s", (k,)), [("PENDING", None)])
    lst = await s.call("list_pending", block="Khelua")
    ctx.check("cannot list another block", lst["ok"], False)
