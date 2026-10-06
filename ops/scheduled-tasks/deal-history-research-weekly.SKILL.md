---
name: deal-history-research-weekly
description: Weekly staggered slice of open-source research on counterparties from the Salesforce deal-history import (25 records/run) until the book is covered, then it reports done and stops
---

Weekly deal-history research slice for Joe Bookout's CARR system. LOCAL Claude Code session with the CARR record-layer MCP connector.

WHY THIS IS A SLICE, NOT A SWEEP. Joe, 2026-08-02: "lets do the same staggered enrichment strategy as with the vendors so we dont burn all the tokens in one week." Research EXACTLY 25 records this run, then stop and report. Do not continue because there is more to do — there always will be until it is finished. Running long is the failure mode this task exists to prevent.

TOKEN-BUDGET INTERACTION, READ THIS FIRST. `contact-enrichment-weekly` runs Thursdays and takes 40 vendor/lead records. This task runs Friday. If that Thursday run completed a full 40 slice this week, TRIM THIS RUN TO 15 records and say so in the report. Two full enrichment passes in one week is exactly the burn Joe is guarding against. Check whether the Thursday run happened via the record layer (corrected 2026-08-06 — the old check read `out/enrichment/`, whose writer was removed when findings moved to the database): count this week's enrichment findings with a read-only query, e.g. `select count(distinct subject_id) from v_record_flag_subject where observed_at >= date_trunc('week', now())` (or ask the connector's read verbs). 30+ subjects this week = a full Thursday slice ran = TRIM TO 15.

WHAT THIS COVERS, and why it is separate from contact-enrichment-weekly: that task enriches vendors and leads already in the book. This one researches COUNTERPARTIES AND CLIENTS arriving from the Salesforce deal-history import — Dell's historical deals and the deals found in the 2026-08-02 audit that the record layer had never seen. The standing shared rule requires it: every client, party and deal already in the system gets the same open-source verification a new client gets, once, and the verification runs ON INTAKE for anything arriving in bulk, explicitly including Dell's Salesforce history import, where every counterparty he has done a deal with gets researched as it lands, not later.

PICK THE 25, in this priority order (read-only queries first):
1. Clients on deals imported from Salesforce that carry NO record_flag row of kind 'verified'. Newest-imported first — intake research is worth most while the deal is live.
2. Clients whose status is active_deal or engaged and who have never been verified.
3. Counterparties (party rows linked through deal_participant) with no verified flag.
4. Any client whose identity fields look wrong: a person's name sitting in the vertical or specialty column, a NULL status, a missing roster_ref. The 2026-08-02 audit found 16 such rows from the 7/31 import.
Skip anything whose party has contact_state 'do_not_contact'.

FOR EACH RECORD, run the deep open-source research the standing shared rule requires — practice website, NPPES/NPI, state corporate registry (Sunbiz for FL), licensing boards, Healthgrades and specialty directories, and SOCIAL MEDIA ACCOUNTS with direct links. Verify practice name (legal and trade), address, phone, specialty, other practitioners, and hours against what the record says.

WRITE FINDINGS TO THE DATABASE, NOT TO MARKDOWN. This is a hard shared rule (Joe, 2026-08-02: "we dont write to markdown in the new system only the database"). Use the `record-finding` verb for every result:
- A completed identity pass → kind 'verified', value listing what was checked, source naming every source used, expires_on about a year out.
- A found fact → kind 'email' / 'cell' / 'social' / 'npi' / 'website' / 'entity_filing' / 'address', with the source.
- SEARCHED AND FOUND NOTHING → pass found:false. This matters as much as a hit: it makes a record nobody searched distinguishable from one that was searched and came up dry, and it stops the next run re-burning effort on the same dead trail.
- A disagreement with the record → kind 'discrepancy' plus proposes_correction {field, current, proposed}. It is RECORDED ONLY. Never edit an identity field on research alone.

HARD RULES:
- NEVER edit an identity field on research alone. Propose with evidence; the owning partner applies it. This system once welded Jenna Castillo to Jeff Castillo DMD — two different people.
- A near-match on a similar name is CONTAMINATION, not confirmation. When two candidates are plausible, record both and pick neither.
- PLACEHOLDER CONTACT DETAILS ARE NOT DATA. Any CARR agent's own contact info, a CARR office line, or a carr.us address sitting in a client contact field is a placeholder — treat it as NULL and record the field as genuinely unknown. Known values: the phone (205) 643-6555 and the email dell.mccraney@carr.us.
- Title and company CHANGE over time. Record when each was verified; an old verification is unverified, not fact.
- Draft only. No email, no outreach, nothing external ever fires.

REPORT in chat when done: how many researched, how many verified clean, how many discrepancies proposed, how many came up empty, how many records remain in the queue, and whether you trimmed the slice for the token-budget interaction above. If the queue is empty, say so plainly and recommend disabling this task. Do not invent work to fill the slice.