# -*- coding: utf-8 -*-
"""/eval-dashboard: the invented citizens and officers, what was said to the assistant, what was
checked in the database afterwards, and whether it held. Reads what evals/runner.py stored."""

import json
from flask import Blueprint, request, render_template_string

import core

evaldash = Blueprint("evaldash", __name__)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sewa Setu assistant evals</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--mute:#5b6676;--line:#dde1e8;--ok:#14733c;--okbg:#e5f4ea;--bad:#a4262c;--badbg:#fbe9ea}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b212a;--ink:#e8ecf2;--mute:#9aa6b6;--line:#2c3441;--ok:#6fd397;--okbg:#17301f;--bad:#ff8f95;--badbg:#3a1a1d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:0}
.mute{color:var(--mute);font-size:.9rem}.bar{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:140px}
.stat b{display:block;font-size:1.6rem}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:16px}
.head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;flex-wrap:wrap}
.pill{border-radius:99px;padding:2px 12px;font-weight:600;font-size:.85rem}.pass{background:var(--okbg);color:var(--ok)}.fail{background:var(--badbg);color:var(--bad)}
dl{margin:10px 0;display:grid;grid-template-columns:max-content 1fr;gap:2px 12px}dt{color:var(--mute)}dd{margin:0}
table{width:100%;border-collapse:collapse;font-size:.88rem;margin-top:8px}td,th{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top;word-break:break-word}
.x{color:var(--bad);font-weight:600}details{margin-top:8px}summary{cursor:pointer;color:var(--mute)}pre{white-space:pre-wrap;font-size:.8rem;background:var(--bg);padding:8px;border-radius:6px;overflow-x:auto}
a{color:inherit}
</style></head><body><main>
<h1>Sewa Setu assistant: evals</h1>
<p class="mute">Invented citizens and officers are played against the live MCP endpoints through a real client that signs in with the portal's OAuth (a citizen with mobile + OTP, an officer with a departmental login). Afterwards the portal's database is queried and compared with what must be true.</p>
{% if not run %}<div class="card">No eval run has been recorded yet.</div>{% else %}
<div class="bar">
 <div class="stat"><b>{{ run.passed }}/{{ run.total }}</b>personas passed</div>
 <div class="stat"><b>{{ checks_ok }}/{{ checks_all }}</b>database checks</div>
 <div class="stat"><b>{{ run.mode }}</b>who played the assistant</div>
 <div class="stat"><b>{{ run.revision }}</b>code revision</div>
</div>
<p class="mute">Run {{ run.id }} finished {{ run.finished }} IST against {{ run.target }}. <a href="?run={{ run.id }}&format=json">JSON</a></p>
{% for r in results %}
<section class="card" id="{{ r.persona_id }}"><div class="head"><div><h2>{{ r.profile.name }}</h2>
<span class="mute">{{ r.profile.role }}, age {{ r.profile.age }}, {{ r.profile.village }} ({{ r.profile.block }})</span></div>
<span class="pill {{ 'pass' if r.passed else 'fail' }}">{{ 'PASS' if r.passed else 'FAIL' }} {{ r.n_ok }}/{{ r.checks|length }}</span></div>
<dl>{% if r.profile.said %}<dt>What they said</dt><dd>{% for l in r.profile.said %}“{{ l }}”<br>{% endfor %}</dd>{% endif %}<dt>How they talk</dt><dd>{{ r.profile.voice }}</dd><dt>What they want</dt><dd>{{ r.profile.wants }}</dd><dt>Must be true afterwards</dt><dd>{{ r.profile.must_be_true }}</dd></dl>
{% if r.error %}<p class="x">Run error: {{ r.error }}</p>{% endif %}
<table><tr><th>Checked</th><th>Expected</th><th>Found</th><th></th></tr>
{% for c in r.checks %}<tr><td>{{ c.name }}</td><td>{{ c.expected }}</td><td>{{ c.actual }}</td><td class="{{ '' if c.passed else 'x' }}">{{ '✓' if c.passed else '✗' }}</td></tr>{% endfor %}</table>
<details><summary>Conversation with the tools ({{ r.transcript|length }} calls)</summary>
{% for t in r.transcript %}{% if t.say and t.say.startswith('PERSON: ') %}<pre><b>Person:</b> {{ t.say[8:] }}</pre>{% elif t.say %}<pre><b>Assistant:</b> {{ t.say }}</pre>{% else %}<pre>{{ t.tool }}({{ t.args|tojson }})
→ {{ t.result|tojson(indent=1) }}</pre>{% endif %}{% endfor %}</details></section>
{% endfor %}
<h2 style="margin-top:28px">History</h2>
<table><tr><th>Run</th><th>Finished (IST)</th><th>Revision</th><th>Passed</th></tr>
{% for h in history %}<tr><td><a href="?run={{ h.id }}">{{ h.id }}</a></td><td>{{ h.finished }}</td><td>{{ h.revision }}</td><td>{{ h.passed }}/{{ h.total }}</td></tr>{% endfor %}</table>
{% endif %}</main></body></html>"""


def _run_row(r):
    return {"id": r[0], "finished": core.to_ist(r[1]).strftime("%d/%m/%Y %H:%M") if r[1] else "",
            "mode": r[2], "target": r[3], "revision": r[4], "passed": r[5], "total": r[6]}


@evaldash.route("/eval-dashboard")
def dashboard():
    conn = core.get_db(); cur = conn.cursor()
    cur.execute("SELECT to_regclass('eval_runs')")
    if cur.fetchone()[0] is None:
        cur.close(); conn.close()
        return render_template_string(PAGE, run=None)
    want = request.args.get("run", type=int)
    cur.execute("SELECT id, finished_at, mode, target, revision, passed, total FROM eval_runs "
                "WHERE (%s IS NULL OR id = %s) ORDER BY id DESC LIMIT 1", (want, want))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return render_template_string(PAGE, run=None)
    run = _run_row(row)
    cur.execute("SELECT persona_id, profile, passed, checks, transcript, error FROM eval_results "
                "WHERE run_id = %s ORDER BY id", (run["id"],))
    results = []
    for pid, profile, passed, checks, transcript, error in cur.fetchall():
        profile = profile if isinstance(profile, dict) else json.loads(profile)
        checks = checks if isinstance(checks, list) else json.loads(checks)
        transcript = transcript if isinstance(transcript, list) else json.loads(transcript)
        results.append({"persona_id": pid, "profile": profile, "passed": passed, "checks": checks,
                        "transcript": transcript, "error": error, "n_ok": sum(c["passed"] for c in checks)})
    cur.execute("SELECT id, finished_at, mode, target, revision, passed, total FROM eval_runs ORDER BY id DESC LIMIT 15")
    history = [_run_row(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    if request.args.get("format") == "json":
        return {"run": run, "results": results}
    return render_template_string(PAGE, run=run, results=results, history=history,
                                  checks_ok=sum(r["n_ok"] for r in results),
                                  checks_all=sum(len(r["checks"]) for r in results))
