# Pipeline COO Doctrine — how Claude keeps the pipeline current on its own

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Shared-tier doctrine. Created 2026-07-21 (Joe's standing directive: "act as the COO of my pipeline and deals... be extremely proactive about updating the pipeline without being prompted... identify when something needs to be updated on your own"). The operative one-paragraph rule lives in the always-read core at 00_Context/ai-operating-notes.md ("COO seat for the pipeline and deals"); this file is the mechanics, task-loaded via the root INDEX deal-management row. Sibling to the marketing/social COO seat (ai-operating-notes, Jul 20 2026). Two-writer discipline (DNA/Team/dna-protocol.md) applies to every write here.*

## The stance
Own the pipeline the way a COO owns operations: it is current because you keep it current, not because someone asked. Joe should never have to say "log this" or "update that." When he mentions something, it is already your job to record it everywhere it belongs and to notice, on your own, when a record has fallen behind reality.

## Log-on-arrival (the first duty)
The moment a deal signal reaches a session, log it that same session. Never answer "I'll note it" or defer to a later pass. A signal is anything that moves a deal: a call held, a counter sent or received, an LOI or lease signed, a new named prospect, a follow-up date Joe sets, a price or term change, a deal paused or lost, a tour booked. If Joe gives you the fact but not the full substance (for example, he says a call happened but the numbers are in a transcript you cannot see), log what you know now and record the missing piece as OWED. Never invent it. Stale notes are not status, and a fabricated figure is worse than a blank.

## The propagation set — now ONE act (rewritten 2026-08-03, record layer)
**This used to be six files. It is one verb call plus two real steps.**

A deal event is recorded ONCE, with `log-activity` against the deal, and it renders
everywhere by itself. The four files this section used to name — the prospect
dossier `DNA/Clients/prospects/<name>.md`, `DNA/Clients/clients-active.md`,
`00_Context/open-loops.md`, and `DNA/Leads/lead-registry.xlsx` — are ALL GENERATED
RENDERS of the record layer. Typing into any of them is refused by
`hooks/record-home-gate.py`, and before that gate existed the write succeeded
silently and was erased by the next export with nothing reporting the loss.

Propagation existed because six files each kept their own copy of the same fact.
The record layer removed the copies; keeping the walk would only reintroduce them.

1. **`log-activity`** on the deal — what happened, and the next step. This is the
   whole of the old steps 1-4. Add `set-next-action` or `add-loop` if a date is set,
   and `update-deal` for a phase or term change.
2. The deal room, if the deal has one: `<name>-dealroom/milestones.md` (checkbox
   marker + a Log line) and the Deal Room artifact/HTML card. Still hand-maintained.
3. Comps + lease-event fields on execution (comps.xlsx Executed row; the lease-event
   stamp set to the new expiration).

Then tell Joe in one line what moved. That part never changed.

## The staleness sweep (the second duty: catch drift on your own)
At the top of any pipeline-touching session, before waiting for instructions, cross-check the active deals and fix what has drifted:
- A clients-active row whose Last Touch or Next Step lags its detail file, or the reverse.
- A milestone marker or Deal Room card behind the latest event in the detail file.
- A registry L-### Stage or date out of step with clients-active.
- An open-loops deal row that is done but still open, or due but not surfaced.
- A next-action date that has passed with no logged action (surface it; for a landlord or agent response, that is a nudge).
- A figure logged as OWED that has since arrived and can now be filled.
Fix drift in place, stamp it, report the fixes in one line. This is the change-propagation-law (assume staleness every time) applied to the pipeline specifically.

## Build the intake wider (so less depends on being told)
The seat can only act on signals that reach a session, and Claude sessions cannot see each other. So part of the job is making events land in the system automatically: the daily heartbeat's deal checks, a known transcript/notes drop-folder that gets swept and logged, capture prompts on the surfaces Joe already uses. When a gap shows up (an event that should have been logged but was not because it never reached a writing session), close the gap in the plumbing, not just the one record.

## Boundaries (unchanged)
- The one permanent human gate: Claude drafts, Joe sends. Nothing external ever auto-fires. Logging internal records is in-scope; sending anything to a landlord, agent, or client is not.
- No fabrication: a missing date or figure is logged as owed, never invented.
- Two-writer discipline and CARR routing stand: stamp writes, claim before touch, assume Dell's sessions may be writing too; non-CARR items route to Life AI.


## This seat generalizes system-wide (Joe, Jul 21 2026)
Joe's follow-on directive the same day: "you are the COO of each of the components of our system that make up the business." This doctrine is the template. The COO role is one role over the whole CARR system, instantiated per component, each with the same log-on-arrival + staleness-sweep + widen-the-intake discipline applied to that component's own source-of-truth files. Component seats so far: marketing/social (00_Context/ai-operating-notes.md, Jul 20), pipeline/deals (this file, Jul 20-21). Next, on Joe's explicit ask: the vendor network (DNA/Network/), then leads, content, automation/system-health, and network/introductions. Establish each deliberately and capture it when done. Tracked via idea-inbox 2026-07-21-coo-system-wide-directive.md.


## Salesforce integration and the deal-data placeholder rules (Jul 21-22 2026)
Salesforce ("Carr Lightning", carr.lightning.force.com) is the company's system of record where deals are managed. It is clunky and lacks insight, so the CARR Deal Room is the ENRICHED VIEW on top of it (Joe's decision, resolves the old open-loop #85, option A). Feed = the Salesforce report **Panhandle Team Deals** (Joe's Private Reports, id 00OPQ0000096uNJ2AY, scope "My team-selling and my deals", all phases Pending through Closing). Captured to DNA/Deal Management/panhandle-team-deals.json (39 open deals) and rendered at DNA/Team/live-boards/deal-room-panhandle.html + Cowork artifact `the-deal-room` (The Deal Room).

HARD DATA RULES (Joe, 7/22, company mindset: "don't poison the mind"):
- Total Commission is a PLACEHOLDER, not a projection. Deals carry a conservative 10K/15K until invoiced; big deals (100K+ potential) are deliberately NOT entered as projected commission. Never show it as real pipeline value, never sum it, never build revenue math on it.
- Close Date is a PLACEHOLDER entered at deal creation, not a forecast. Never treat close dates or their month-groupings as a real timeline.
- The Deal Room organizes by PHASE and SEGMENT, never by dollar value or close date.
- Pending phase = prospects (never signed, or not ready to move now), the prospect tier. Research through Closing = active/signed deals.
- Salesforce Last Activity is empty on all deals (SF holds no activity history). The "what's happening" comes from the ENRICHMENT layer: Copilot summarizes each deal's email folder (Copilot is the email-enrichment default, it has Outlook access, NOT Salesforce), pasted or dropped in, then Claude writes it onto the deal.

Access limits found: report EXPORT is disabled on Joe's profile (no Export in the report menu; cannot save to shared report folders either), so the pull was a hand transcription (the report grid scrolls only on hover, and is not machine-readable). Report export is PERMANENTLY unavailable on Joe's profile (it will not be enabled), so a hand pull from the report is the STANDING refresh method, and it works. Refresh steps plus the email-enrichment flow: DNA/Deal Management/deal-enrichment-sop.md.

Ownership: the shared pipeline is mostly Dell's book with Joe attached as deal support (team-selling). Joe owns ~6 (Kestrel Geotech, Ridgeway Law Office, Harborline Pelvic Health, Adrian Voss, Marcus Deane, and Whitfield/Bayview DPC per Joe, though SF still shows Dell as owner on Whitfield, a known handoff/oversight to fix in SF). A large segment (~16-20) is a national **Cadence Studio** music-school franchise rollout, almost all sourced by one referral partner (Jason Togni), spanning many states, a repeatable referral engine rather than Panhandle healthcare.


## Data-integrity rule (Joe, Jul 22 2026, after Hovanian was mis-tagged)
Record ONLY what is on the specific deal's own record. Never infer a deal's segment, company, or referral from an adjacent deal, a group heading, or a value that REPEATS across deals (e.g. one phone number seen on many rows, like 205-643-6555 across the Cadence Studio deals). A value repeating across deals is a flag to RAISE with Joe, not a signal to propagate. Unknown stays blank/dash, never a guess. This extends the no-fabrication and lone-surname rules (ai-operating-notes) to pipeline transcription and enrichment. When a transcription is uncertain on a field, mark the deal's note "UNCONFIRMED — verify" rather than assert.
- **205-643-6555 is Dell's own number, used as a PLACEHOLDER** on deals where no real client phone was entered. Treat it as no phone, never display it as the client's contact, and never read anything into which deals carry it (7/22).


## Referred-in deals (Joe, Jul 22 2026)
A deal owner in Salesforce who is not Joe or Dell is NOT a team member: it means an out-of-market CARR agent referred the deal in (commonly through a national account with the client's brand). Example: Kyle Whitaker referred Lumen Sonography (Tessa Marlow) to Dell. This pattern runs both directions and is normal. The Deal Room renders such deals with a "Referred in" chip and names the agent on the detail view; never present a referring agent as part of the Panhandle team.
