# -*- coding: utf-8 -*-
import os
import re

import core

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def ist(dt):
    return core.to_ist(dt).strftime("%d/%m/%Y %I:%M %p IST") if dt else None


def ist_date(dt):
    return core.to_ist(dt).strftime("%d/%m/%Y") if dt else None


def mask_account(acct):
    acct = acct or ""
    return ("*" * max(len(acct) - 4, 0)) + acct[-4:]


def mask_mobile(m):
    m = m or ""
    return m[:2] + "******" + m[-2:] if len(m) == 10 else m


def deemed_on(submitted_at):
    from datetime import timedelta
    return ist_date(submitted_at + timedelta(days=core.SLA_DAYS))


def clean_choice(value, allowed, label):
    """Match a free-text answer to the form's own options; None means not given."""
    v = (value or "").strip()
    if not v:
        return None
    for a in allowed:
        if a.lower() == v.lower():
            return a
    raise core.Refused("%s must be one of: %s" % (label, ", ".join(allowed)))


def serve(server):
    """Run one MCP endpoint. FastMCP's metadata route prints the issuer through a URL type that
    appends a slash; the portal's own metadata has none, and clients compare them exactly."""
    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from mcp.server.auth.routes import build_resource_metadata_url

    auth = server.settings.auth
    asgi = server.streamable_http_app()
    meta_path = build_resource_metadata_url(auth.resource_server_url).path
    doc = {"resource": str(auth.resource_server_url).rstrip("/"),
           "authorization_servers": [str(auth.issuer_url).rstrip("/")],
           "scopes_supported": auth.required_scopes, "bearer_methods_supported": ["header"]}

    async def metadata(request):
        return JSONResponse(doc, headers={"Access-Control-Allow-Origin": "*"})

    asgi.router.routes.insert(0, Route(meta_path, metadata, methods=["GET", "OPTIONS"]))
    uvicorn.run(asgi, host=server.settings.host, port=server.settings.port, log_level="info")
