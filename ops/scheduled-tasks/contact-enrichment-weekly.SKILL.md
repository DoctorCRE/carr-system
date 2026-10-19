---
name: contact-enrichment-weekly
description: Weekly slice of open-source contact enrichment using the deterministic queue planner until the book is covered, then it reports done and stops finding work
---

Weekly contact-enrichment slice for Joe Bookout's CARR system. LOCAL Claude Code session with the CARR record-layer MCP connector.

Run `.venv/bin/python tools/db-tap.py run tools/enrichment-queue.py contact` once.
Research only the returned `selected` subjects, in order, then stop. If the command fails,
report the failure and stop. Report its `remaining` count and `non_contact_expired` subjects
alongside the research results. Read subject details through the record layer by exact ID.

FOR EACH RECORD, run the deep-dive research the standing rule requires (it is in compiled-rules-shared.md — read it first). Gather: full legal and trade name, title, current company, practice website, address/city/county, specialty, NPI/licence where applicable, other practitioners at the practice, hours, entity filings, and SOCIAL MEDIA ACCOUNTS with direct links so Joe can follow them.

RESEARCH SOURCES — THE INDEX FIRST, THEN THE OPEN WEB (Joe, 2026-10-07). Before searching, call `list-research-sites` with this run's topics (`provider-directory`, `provider-verification`, `entity-filings`, `npi`; call it with no topic to see every tag in use) and start from the sources it returns. The index is a starting point, never a limit: ALSO search the open internet for sources it does not list — a plain read-only fetch may reach any public host, spelled `curl -q ...` or `wget --no-config --no-netrc ...` with no variable assignment in front and no `cd` anywhere in the command; any other spelling is refused for an unlisted host. To save a download, write it only with `curl -q -g -o <path>` or `wget --no-config --no-netrc -O <path>`, where `<path>` is a new file named by a literal absolute path under the system temp directory (`/tmp` on Linux; no glob, `~`, `$`, `#` or `..`, no symlink, never inside a repository); `curl -O`, a `>` redirect to a file and `tee` to a file are refused. When a new source proves useful, add it for the next run with `add-research-site` (host, topics, an optional start url, a one-line note, a fresh idempotency_key); retire a dead or misleading one with `remove-research-site` and a reason. Both verbs are open to this run and need no approval. Index sources, never people: no page that carries a client's or patient's details goes in.

CONTACT INFO — GET THE REACHABLE ONES. Joe: "you need to get contact info where it is available too (email and cell phone, not office phone)." A direct email and a CELL number are what let him actually reach someone; an office line reaches a front desk and is close to useless for this business. Record office numbers only when nothing better exists, and LABEL them as office so nobody mistakes one for a direct line. If a cell or personal email cannot be found from open sources, say so plainly rather than filling the field with the switchboard.

HARD RULES:
- NEVER edit an identity field on research alone. Propose corrections with evidence; Joe applies them. This system merged the wrong Castillo once — an import welded Jenna Castillo to Jeff Castillo DMD, two different people.
- A near-match on a similar name is CONTAMINATION, not confirmation. When two candidates are plausible, report both and pick neither.
- Vendor category: if the right type is not already in `vendor_category`, propose a NEW category rather than forcing a fit. Joe: "vendor type should never be misc. if they are a rare type they deserve their own new category so we can recall that data in the future without missing anyone." There is deliberately no catch-all.
- Record WHEN each field was verified. Title and company change (promotions, job moves), so an old verification is unverified, not fact.
- **STAMP `expires_on` ON EVERY VOLATILE FINDING** (added 2026-08-06, loop #212): title, company, email, cell, office_phone, and any `verified` pass covering them carry `expires_on` = observed date + 180 days. An unstamped volatile fact quietly becomes permanent "truth" — the 2026-08-06 run stamped zero of its 40 rows, which is why `v_expired_verification` reports unstamped-volatile rows as due. Address and entity-filing facts may carry 365 days or none.
- Draft only. No email, no outreach, nothing external ever fires.

WRITE-UP: findings land through `record-finding` — one row per fact, source
required, `expires_on` per the stamping rule, discrepancies as
`proposes_correction`. NEVER a markdown report file: the previous instruction
here (append to `out/enrichment/enrichment-<date>.md`) violated the
findings-go-to-the-database rule, which names this task explicitly; corrected
2026-08-06 under loop #142/#212. Then post a short chat summary: how many
enriched, how many needed a new vendor category, how many had discrepancies,
how many re-verifies cleared, and how many records remain in the queue.

WHEN THE QUEUE IS EMPTY: say so plainly and recommend disabling this task. Do not invent work to fill the slice.
