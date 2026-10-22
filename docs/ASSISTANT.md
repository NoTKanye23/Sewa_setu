# Sewa Setu assistant layer

Two MCP servers (streamable HTTP, stateless) in front of the unchanged portal:

| Endpoint | Scope | Tools |
|---|---|---|
| `/mcp/citizen` | `citizen` | `get_scheme_info`, `get_my_application`, `attach_age_proof`, `check_application`, `submit_application`, `withdraw_application` |
| `/mcp/officer` | `officer` | `queue_summary`, `list_pending`, `get_case`, `approve_application`, `reject_application` |

Each endpoint is its own process with its own token audience, so a citizen's assistant never lists the officer tools
and a citizen token is refused at the officer endpoint (eval persona `privacy`).

## Decisions

1. **One rulebook.** `app/core.py` owns the scheme rules and every write. The web form calls it too. The duplicate-active-application
   check used to live only in the web handler; it is now part of `submit_application_tx`, under a per-mobile advisory lock.
2. **Identity comes from the portal's OAuth, never from the conversation.** Tools take no mobile number: the verified `sub` of the
   token is the applicant. The MCP servers check signature (JWKS), issuer, exact audience, scope, role and expiry, and fail closed.
3. **Officers sign in under their own name** (`officers` table; the shared admin login remains and is recorded as `officer:admin`).
   Decisions need a reason (10+ characters), are recorded as `decided_by=<officer>` and `audit_log.actor=officer:<officer>`.
   An officer assigned to a block cannot list, open or decide another block's cases.
4. **Confirmation is a tool argument.** `submit_application` and `withdraw_application` refuse unless `confirmed_by_applicant=true`,
   so a model cannot file something in one step from a half-heard sentence.
5. **The age-proof document is stored as received, and the assistant is told never to call it verified.** Officers see it labelled
   "not machine-verified". Stricter than the portal only where the portal was unsafe (filename path traversal).

## Known limits

- No attempt limit on the portal's OTP check (portal behaviour, not changed).
- Revoking a sign-in ends the refresh token; an already-issued access token lives up to 20/30 minutes.
- Shared phones: one mobile can hold one active application, as at the counter; the assistant explains and refuses.
- Stored times are server-local; every time shown to a person is converted to IST.

## Running the evals

```
docker compose run --rm evals          # plays every persona, stores the run, then /eval-dashboard shows it
python -m unittest tests.test_core     # rules, against any Postgres loaded with schema + seed
```
