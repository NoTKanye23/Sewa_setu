# -*- coding: utf-8 -*-
"""/upload-proof/<token>: where a person uploads the age-proof file themselves, using a short-lived
link their assistant gave them. The assistant never sees or handles the file."""

from flask import Blueprint, request, render_template_string

import core

uploadlink = Blueprint("uploadlink", __name__)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Upload age proof</title>
<style>body{font:20px/1.5 system-ui,sans-serif;max-width:560px;margin:0 auto;padding:24px 16px;background:#f6f7f9;color:#1d2430}
@media(prefers-color-scheme:dark){body{background:#12161c;color:#e8ecf2}}
.box{background:#fff;border-radius:12px;padding:20px;border:1px solid #dde1e8}@media(prefers-color-scheme:dark){.box{background:#1b212a;border-color:#2c3441}}
input[type=file]{width:100%;margin:16px 0;font-size:18px}button{font-size:22px;padding:14px 24px;border-radius:10px;border:0;background:#14733c;color:#fff;width:100%}
.ok{color:#14733c;font-weight:700}.bad{color:#a4262c;font-weight:700}</style></head><body><div class="box">
<h1>Upload your age proof</h1>
{% if state == 'bad' %}<p class="bad">This link has expired or is not valid. Ask your assistant for a new one.</p>
{% elif state == 'done' %}<p class="ok">Your document was received.</p><p>You can go back to your assistant now and tell them it is done. Nobody has checked the document yet.</p>
{% else %}{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<p>Choose a photo or PDF of the document that shows your date of birth (JPG, PNG or PDF, up to 5 MB).</p>
<form method="post" enctype="multipart/form-data"><input type="file" name="document" accept=".jpg,.jpeg,.png,.pdf,image/*,application/pdf" required>
<button type="submit">Send document</button></form>{% endif %}</div></body></html>"""


@uploadlink.route("/upload-proof/<token>", methods=["GET", "POST"])
def upload_proof(token):
    mobile = core.mobile_from_upload_token(token)
    if not mobile:
        return render_template_string(PAGE, state="bad"), 400
    if request.method == "POST":
        f = request.files.get("document")
        try:
            if f is None or not f.filename:
                raise core.Refused("Please choose a file first")
            path = core.save_document(mobile, f.filename, f.read())
        except core.Refused as r:
            return render_template_string(PAGE, state="form", error=r.problems[0].capitalize() + "."), 400
        core.remember_document_tx(mobile, path)
        return render_template_string(PAGE, state="done")
    return render_template_string(PAGE, state="form")
