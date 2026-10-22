# -*- coding: utf-8 -*-
"""Verifies the portal's OAuth access tokens for a resource server (one per MCP endpoint).

A token is accepted only if it is signed by the portal's key (JWKS), was issued by the portal,
was minted for exactly this endpoint (`aud`), carries this endpoint's scope and role, is
unexpired, and (officers, program tokens) has not been switched off at the portal since.
"""

import os
import asyncio
import logging

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.middleware.auth_context import get_access_token

import core

log = logging.getLogger("sewasetu.mcp.auth")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
PORTAL_INTERNAL_URL = os.environ.get("PORTAL_INTERNAL_URL", "http://app:8000").rstrip("/")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")

_jwks = None


def _jwks_client():
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(PORTAL_INTERNAL_URL + "/.well-known/jwks.json",
                                cache_keys=True, lifespan=600, timeout=5)
    return _jwks


class PortalToken(AccessToken):
    subject: str            # citizen:<mobile> or officer:<username>
    identity: str           # the mobile or the username
    role: str
    jti: str = ""


class PortalTokenVerifier(TokenVerifier):
    def __init__(self, audience, scope):
        self.audience = audience.rstrip("/")
        self.scope = scope

    async def verify_token(self, token):
        return await asyncio.to_thread(self._verify, token)

    def _verify(self, token):
        try:
            key = _jwks_client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=PUBLIC_BASE_URL,
                                options={"verify_aud": False, "require": ["exp", "iss", "sub"]})
        except Exception as e:
            log.info("token rejected: %s", e)
            return None
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if self.audience not in [(a or "").rstrip("/") for a in auds]:
            log.info("token rejected: audience %r is not %s", aud, self.audience)
            return None
        if claims.get("scope") != self.scope or claims.get("role") != self.scope:
            log.info("token rejected: scope/role %r/%r", claims.get("scope"), claims.get("role"))
            return None
        subject = claims["sub"]
        prefix, _, identity = subject.partition(":")
        if prefix != self.scope or not identity:
            return None
        if not self._still_allowed(claims, identity):
            return None
        return PortalToken(token=token, client_id=claims.get("client_id", ""), scopes=[self.scope],
                           expires_at=claims["exp"], resource=self.audience, subject=subject,
                           identity=identity, role=self.scope, jti=claims.get("jti", ""))

    def _still_allowed(self, claims, identity):
        try:
            conn = core.get_db()
            cur = conn.cursor()
            if claims.get("kind") == "program":
                cur.execute("SELECT revoked_at FROM oauth_program_tokens WHERE jti = %s", (claims.get("jti"),))
                row = cur.fetchone()
                if not row or row[0] is not None:
                    return False
            if self.scope == "officer" and identity != ADMIN_USERNAME:
                core.ensure_schema()
                cur.execute("SELECT 1 FROM officers WHERE username = %s AND active", (identity,))
                if not cur.fetchone():
                    return False
            cur.close(); conn.close()
            return True
        except Exception as e:     # fail closed: no database, no access
            log.error("token check failed: %s", e)
            return False


def current():
    """The verified caller of the tool being run."""
    tok = get_access_token()
    if not isinstance(tok, PortalToken):
        raise RuntimeError("no authenticated caller")
    return tok
