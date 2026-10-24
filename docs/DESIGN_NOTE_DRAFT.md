# Design note (DRAFT: rewrite in your own words before submitting)

**Sewa Setu assistant: three decisions that mattered, and one I would change**

## What I built
Two MCP servers (`/mcp/citizen`, `/mcp/officer`) in front of the unchanged portal, behind one front door. The portal stays
the system of record; the assistants are another way to apply and to decide, under the portal's own rules.

## The three decisions that mattered

**1. One rulebook for every channel.** The portal's code says every channel must go through `validate_application()`, but the
"one active application per mobile" check lived only in the web handler, so an assistant calling the validator would
have filed a second application the counter refuses. I moved validation, the duplicate check (under a per-mobile
database lock), creation, withdrawal and officer decisions into one module (`core.py`) that the web form and both MCP servers
call. The rules cannot drift between channels, and an application filed in either place is visible in both.

**2. Identity comes from the portal's sign-in, never from the conversation.** The citizen tools take no mobile number: the
signed-in person's verified token *is* the applicant. Each endpoint accepts only tokens minted for its own address, scope
and role, so a citizen's assistant cannot see or call officer tools (a citizen token is refused at the officer endpoint,
tested). I also gave each officer their own login, because the shared admin login would have made "decided by" mean nothing:
every decision now records the officer's own name and the reason they gave (mandatory), and an officer can act only in their own block.

**3. The age-proof document is the person's, not the model's.** My first design let the assistant pass the file as base64. The
Haiku-driven evals showed what that invites: a small model with no file can invent bytes, which would put a fabricated document
in the database. The assistant now gives the person a short-lived signed link to upload their own file, and can only check
that "a file arrived", never that it is valid. Officers see it labelled "not machine-verified".

## What the evals found (and what I changed)
- The duplicate-application gap above (found by reading the code, then pinned by a persona).
- An assistant refusing a clear officer instruction because of a "possible duplicate" flag: I changed the instructions so flags inform
  the officer's decision and never override it.
- Filing needs a read-back and a clear yes after it; I made confirmation an explicit tool argument rather than trusting the model.
- Scripted evals pass 17/17; with Claude Code (Haiku) as the assistant, 14/14 on the personas that exist, across repeated runs.

## The one thing I would do differently with more time
My citizens are fixed scripts, so they cannot react to what the assistant says. With more time I would play each persona with a second
model that answers the assistant in character, and run every persona many times, so the pass rate measures conversations and not just
rules. I would also move to a proper VM (mine is a laptop behind a tunnel) and add rate limits on the OTP step, which I found but left as portal behaviour.
