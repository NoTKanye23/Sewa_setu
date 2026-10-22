# -*- coding: utf-8 -*-
"""A real MCP client for the evals: streamable HTTP, full OAuth 2.1 (discovery, dynamic
registration, PKCE), signing in on the portal's own pages the way a person would.
A citizen enters a mobile number and the OTP that arrives by SMS; an officer enters the
departmental username and password."""

import re
import time
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs

import httpx
from pydantic import AnyUrl
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

REDIRECT = "http://localhost:3030/callback"


class MemoryStorage(TokenStorage):
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull):
        self.client_info = client_info


class SignIn:
    """Completes the portal's sign-in pages for one person. `credentials` is
    {"mobile": "98..."} for a citizen or {"username": ..., "password": ...} for an officer."""

    def __init__(self, portal_url, sms_url, credentials, consent=True):
        self.portal, self.sms, self.cred, self.consent = portal_url.rstrip("/"), sms_url.rstrip("/"), credentials, consent
        self.result = None

    async def otp_for(self, mobile, after, timeout=30):
        async with httpx.AsyncClient() as c:
            end = time.time() + timeout
            while time.time() < end:
                msgs = (await c.get(self.sms + "/api/messages", params={"to": mobile})).json()
                msgs = msgs if isinstance(msgs, list) else msgs.get("messages", [])
                for m in reversed(msgs):
                    found = re.search(r"OTP is (\d{6})", m.get("text", "") or "")
                    if found and float(m.get("sent_at", after + 1) or 0) >= after - 1:
                        return found.group(1)
                await asyncio.sleep(0.5)
        raise TimeoutError("no OTP arrived for " + mobile)

    async def redirect_handler(self, auth_url):
        started = time.time()
        async with httpx.AsyncClient(follow_redirects=False) as c:
            r = await c.get(auth_url)
            login = urlparse(r.headers["location"])
            rid = parse_qs(login.query)["rid"][0]
            login_url = self.portal + "/oauth/login"
            if "mobile" in self.cred:
                await c.post(login_url, params={"rid": rid}, data={"mobile": self.cred["mobile"]})
                otp = await self.otp_for(self.cred["mobile"], started)
                r = await c.post(login_url, params={"rid": rid}, data={"otp": otp})
            else:
                r = await c.post(login_url, params={"rid": rid}, data=self.cred)
            if r.status_code != 302:
                raise PermissionError("portal sign-in refused")
            r = await c.post(self.portal + "/oauth/consent", params={"rid": rid},
                             data={"decision": "allow" if self.consent else "deny"})
            q = parse_qs(urlparse(r.headers["location"]).query)
            self.result = (q.get("code", [""])[0], q.get("state", [None])[0], q.get("error", [""])[0])

    async def callback_handler(self):
        if self.result[2]:
            raise PermissionError(self.result[2])
        return self.result[0], self.result[1]


@asynccontextmanager
async def connect(endpoint, scope, portal_url, sms_url, credentials, storage=None):
    """Yields (session, storage): an initialised MCP session signed in as the given person."""
    signin = SignIn(portal_url, sms_url, credentials)
    storage = storage or MemoryStorage()
    auth = OAuthClientProvider(
        server_url=endpoint,
        client_metadata=OAuthClientMetadata(
            client_name="Sewa Setu eval runner", redirect_uris=[AnyUrl(REDIRECT)],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method="none", scope=scope),
        storage=storage, redirect_handler=signin.redirect_handler, callback_handler=signin.callback_handler)
    async with streamablehttp_client(endpoint, auth=auth) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session, storage


def result_data(res):
    """The dict a tool returned (structured content, else the JSON text)."""
    import json
    if getattr(res, "structuredContent", None):
        sc = res.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text) if res.content else {}
