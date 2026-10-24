# Loom outline (3 minutes, face + screen). Say it in your own words; these are prompts, not a script.

**0:00 – 0:25  What I built.** Two MCP servers over the existing portal: citizen and officer, separate endpoints, one set of rules.
Show the portal URL, then Claude Code connecting to `/mcp/citizen` and signing in with mobile + OTP.

**0:25 – 1:10  Design decisions across the two MCPs.** Show `core.py`: one rulebook for the web form and both servers (the duplicate
check that lived only in the web handler). Show that the citizen tools take no mobile number: identity is the signed-in token.
Show the officer side: own login, mandatory reason, recorded under their name, own block only. Show the upload link instead of
the model sending file bytes.

**1:10 – 2:15  Personas and evals.** Open `/eval-dashboard`. Walk through three people: Sukhdei (the plain success), Munna at the
kiosk (a shared phone, second application refused), Meena the officer (no reason given, the assistant will not invent one).
Mention the harder ones: the instruction hidden in an applicant's name, the "Collector override", the age that does not match the date of birth.
Say the two kinds of runs: scripted (deterministic) and Claude Code with Haiku as the assistant.

**2:15 – 2:45  What the evals found and what I changed.**
- The base64 age-proof design invited a model to invent a document, so I moved to an upload link.
- A model refused a clear officer instruction because of a "possible duplicate" flag, so flags now inform and never override.
- My own personas were too short: a person has to say yes after the read-back.
- Results vary: show the 13/14 run next to the 14/14 run on the same code.

**2:45 – 3:00  What I learned / what I'd do next.** Fixed scripts cannot answer back, so a second model should play the citizen; and
this runs on a laptop behind a tunnel, which I would move to a proper VM.
