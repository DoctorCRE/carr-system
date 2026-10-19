---
name: deal-history-research-weekly
description: Weekly staggered slice of open-source research on counterparties from the Salesforce deal-history import using the deterministic queue planner until the book is covered, then it reports done and stops
---

Weekly deal-history research slice for Joe Bookout's CARR system. LOCAL Claude Code session with the CARR record-layer MCP connector.

Run `.venv/bin/python tools/db-tap.py run tools/enrichment-queue.py deal-history` once.
Research only the returned `selected` subjects, in order, then stop. If the command fails,
report the failure and stop. Use its `remaining` count and `trimmed` flag in the report.
Read subject details through the record layer by exact ID.

WHAT THIS COVERS, and why it is separate from contact-enrichment-weekly: that task enriches vendors and leads already in the book. This one researches COUNTERPARTIES AND CLIENTS arriving from the Salesforce deal-history import — Dell's historical deals and the deals found in the 2026-08-02 audit that the record layer had never seen. The standing shared rule requires it: every client, party and deal already in the system gets the same open-source verification a new client gets, once, and the verification runs ON INTAKE for anything arriving in bulk, explicitly including Dell's Salesforce history import, where every counterparty he has done a deal with gets researched as it lands, not later.

FOR EACH RECORD, run the deep open-source research the standing shared rule requires — practice website, NPPES/NPI, state corporate registry (Sunbiz for FL), licensing boards, Healthgrades and specialty directories, and SOCIAL MEDIA ACCOUNTS with direct links. Verify practice name (legal and trade), address, phone, specialty, other practitioners, and hours against what the record says.

RESEARCH SOURCES — THE INDEX FIRST, THEN THE OPEN WEB (Joe, 2026-10-07). Before searching, call `list-research-sites` with this run's topics (`provider-verification`, `npi`, `entity-filings`, `provider-directory`; call it with no topic to see every tag in use) and start from the sources it returns. The index is a starting point, never a limit: ALSO search the open internet for sources it does not list — a plain read-only fetch may reach any public host, spelled `curl -q ...` or `wget --no-config --no-netrc ...` with no variable assignment in front and no `cd` anywhere in the command; any other spelling is refused for an unlisted host. To save a download, write it only with `curl -q -g -o <path>` or `wget --no-config --no-netrc -O <path>`, where `<path>` is a new file named by a literal absolute path under the system temp directory (`/tmp` on Linux; no glob, `~`, `$`, `#` or `..`, no symlink, never inside a repository); `curl -O`, a `>` redirect to a file and `tee` to a file are refused. When a new source proves useful, add it for the next run with `add-research-site` (host, topics, an optional start url, a one-line note, a fresh idempotency_key); retire a dead or misleading one with `remove-research-site` and a reason. Both verbs are open to this run and need no approval. Index sources, never people: no page that carries a client's or patient's details goes in.

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

REPORT in chat when done: how many researched, how many verified clean, how many discrepancies proposed, how many came up empty, how many records remain in the queue, and whether you trimmed the slice for the planner's budget decision. If the queue is empty, say so plainly and recommend disabling this task. Do not invent work to fill the slice.
