# Submission checklist (deadline 31 Oct 2026, 11:59 PM IST; aim to submit by the night of 30 Oct)

## Running (must stay up until the window closes)
- [ ] Laptop on charger, sleep masked, Docker enabled at boot, ngrok running (`~/start-sewasetu.sh` brings everything back)
- [ ] Public URL answers from a phone on mobile data: `https://striking-uncurled-uniformly.ngrok-free.dev/`
- [ ] `/eval-dashboard` shows at least one scripted run and two Claude-driven runs

## Values for the form
- Portal: `https://striking-uncurled-uniformly.ngrok-free.dev`
- Citizen MCP: `https://striking-uncurled-uniformly.ngrok-free.dev/mcp/citizen`
- Officer MCP: `https://striking-uncurled-uniformly.ngrok-free.dev/mcp/officer`
- Eval dashboard: `https://striking-uncurled-uniformly.ngrok-free.dev/eval-dashboard`
- Officer login for reviewers: username `reviewer` + the password you set in `.env` (`OFFICERS=...,reviewer:<pw>`), all blocks
- Note for reviewers: citizens sign in with a mobile number and an OTP; the OTP appears at `/__gateway/` on the portal
  (messages take 1 to 4 seconds). The first browser visit through ngrok may show a "Visit site" page: click through.
- Note: the deployment is a laptop behind an ngrok tunnel, not a cloud VM (say so plainly).

## Repo
- [ ] Create the private GitHub repo `sewasetu-assistant` and invite `harshnisar`
- [ ] Push the full history: `git remote add submit git@github.com:<you>/sewasetu-assistant.git && git push submit claude/awesome-allen-95zwvx:main`
- [ ] Fresh clone from the new repo, then `zip -r sewasetu-assistant.zip sewasetu-assistant` (the zip must include `.git`)
- [ ] Check no real secrets are committed (`.env` is git-ignored; the portal's own default DB password in `app.ini` is the handout's)

## Writing (in your own words)
- [ ] One-page design note (start from `docs/DESIGN_NOTE_DRAFT.md`)
- [ ] 3-minute Loom, face and screen (outline in `docs/LOOM_OUTLINE.md`)
- [ ] Your own answers to the form's questions
