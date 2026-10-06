---
name: contact-enrichment-weekly
description: Weekly slice of open-source contact enrichment (40 records/run) until the book is covered, then it reports done and stops finding work
---

Weekly contact-enrichment slice for Joe Bookout's CARR system. LOCAL Claude Code session with the CARR record-layer MCP connector.

WHY THIS IS A SLICE, NOT A SWEEP. Joe, 2026-08-02: "i would only do a certain percentage once per week until its completed bc i dont want to wipe out tokens next week before the week even starts." Enrich EXACTLY 40 records this run, then stop and report. Do not continue because there is more to do — there always will be until it is finished. Running long is the failure mode this task exists to prevent.

PICK THE 40, in this priority order (read-only queries first):
1. `select * from v_vendor_needs_type` — active vendors with no real category. 63 at creation. These are the most valuable: 41 of them were filed as "Target (not yet met)", a stage stored in a type field, so their actual profession was never recorded. Targets and deepest relationships sort first.
2. Vendors missing location: `party.city` and `party.county` are 0/290 populated, `state` 108/290. A territory business with no vendor cities.
3. Vendors missing `verticals` (10/290 populated).
4. Leads and clients missing title, org, email or phone.
Skip anything whose party has `contact_state` of `do_not_contact`.

FOR EACH RECORD, run the deep-dive research the standing rule requires (it is in compiled-rules-shared.md — read it first). Gather: full legal and trade name, title, current company, practice website, address/city/county, specialty, NPI/licence where applicable, other practitioners at the practice, hours, entity filings, and SOCIAL MEDIA ACCOUNTS with direct links so Joe can follow them.

CONTACT INFO — GET THE REACHABLE ONES. Joe: "you need to get contact info where it is available too (email and cell phone, not office phone)." A direct email and a CELL number are what let him actually reach someone; an office line reaches a front desk and is close to useless for this business. Record office numbers only when nothing better exists, and LABEL them as office so nobody mistakes one for a direct line. If a cell or personal email cannot be found from open sources, say so plainly rather than filling the field with the switchboard.

HARD RULES:
- NEVER edit an identity field on research alone. Propose corrections with evidence; Joe applies them. This system merged the wrong Castillo once — an import welded Jenna Castillo to Jeff Castillo DMD, two different people.
- A near-match on a similar name is CONTAMINATION, not confirmation. When two candidates are plausible, report both and pick neither.
- Vendor category: if the right type is not already in `vendor_category`, propose a NEW category rather than forcing a fit. Joe: "vendor type should never be misc. if they are a rare type they deserve their own new category so we can recall that data in the future without missing anyone." There is deliberately no catch-all.
- Record WHEN each field was verified. Title and company change (promotions, job moves), so an old verification is unverified, not fact.
- **STAMP `expires_on` ON EVERY VOLATILE FINDING** (added 2026-08-06, loop #212): title, company, email, cell, office_phone, and any `verified` pass covering them carry `expires_on` = observed date + 180 days. An unstamped volatile fact quietly becomes permanent "truth" — the 2026-08-06 run stamped zero of its 40 rows, which is why `v_expired_verification` reports unstamped-volatile rows as due. Address and entity-filing facts may carry 365 days or none.
- Draft only. No email, no outreach, nothing external ever fires.

QUEUE SOURCE ADDITION (2026-08-06, loop #212): before step 1 above, read
`v_expired_verification` — expired and unstamped-volatile re-verifies are this
task's FIRST queue, ahead of never-verified records, because a stale "verified"
stamp misleads where a blank at least looks unknown.

SCOPE THAT VIEW TO CONTACTS (added 2026-08-20). `v_expired_verification` returns
every expired finding in the system, and `record-finding` has taken non-contact
subjects since 0066 (campaign, platform, pillar, format) and 0101 (repo, commit).
Filter on `subject_type in ('party','vendor','lead','client')`. On 2026-08-20 the
view held exactly one row and it was a `commit` subject — a code finding
(`control_plane_production_readiness`, expired 2026-08-17) that this task can
neither research nor enrich. An unfiltered read makes the first queue look
non-empty when it holds nothing this task can act on. If a non-contact row is
expired, do not enrich it and do not silently skip it: name it in the chat
summary so the lane that owns it can pick it up.

WRITE-UP: findings land through `record-finding` — one row per fact, source
required, `expires_on` per the stamping rule, discrepancies as
`proposes_correction`. NEVER a markdown report file: the previous instruction
here (append to `out/enrichment/enrichment-<date>.md`) violated the
findings-go-to-the-database rule, which names this task explicitly; corrected
2026-08-06 under loop #142/#212. Then post a short chat summary: how many
enriched, how many needed a new vendor category, how many had discrepancies,
how many re-verifies cleared, and how many records remain in the queue.

WHEN THE QUEUE IS EMPTY: say so plainly and recommend disabling this task. Do not invent work to fill the slice.