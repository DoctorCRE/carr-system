---
name: npi-sweep-weekly
description: Weekly NPI research, worked recommendations and ranked Hot proposals, pending Joe or Dell's verdict.
---

Read the complete current `npi-sweep-sop` with `read-doctrine` using `document:"npi-sweep-sop"` before doing the sweep. The store governs every lane and wins over this prompt. If it cannot be read, stop and report the failure through the record layer.

The `lead-signals-weekly` code job prepares NPPES signals before this routine. Read `loop-board` with `kind:"team_loop"`, `owner:"claude"`, `status:"any"` and `search:"NPPES preparation YYYY-MM-DD"` for today's America/Chicago date. Read the matching row with `read-loop` if its body is abbreviated. A body with `status:"prepared"` contains the classified proposals, exclusions, possible client/vendor/prospect matches, source evidence and pending duties. A partial run can have candidate rows without that completed summary. Read those with `loop-board`, `kind:"team_loop"`, `status:"any"`, `search:"NPPES proposal:"`; reconcile by NPI and source before refiling. Treat proposed routes as unconfirmed identities. Never attach intel to a possible match without corroborating it.

The code owns public weekly-file parsing, the 14-day enumeration window, territory and secondary-location checks, exact-NPI deduplication, preliminary classification, possible existing-record routing and persisted research handoff. It assigns evidence estimates, not final radar scores. If preparation is absent, incomplete or stale, run the SOP's collection path and state the code lane's actual health. Existing candidate rows remain valid evidence; a missing summary is not evidence that no candidate was filed.

This routine retains every judgment duty. Research the principal, verify independent versus institutional or National Accounts profiles, confirm existing clients and vendors, and file dated intel with `record-finding` on their confirmed records. Complete the five-question address workup, the qualification and contact research, the space-claim read, worked recommendation, network path, sensitivity handling and Winning it commentary. Run the other SOP lanes within their existing first-run gates. Mark incomplete research PENDING with its reason.

Publish each run's digest and completion or partial status through `add-loop`, including quiet weeks. Publish the final ranked Hot output in the same record layer with the SOP's `ranked`, `proposed`, `holds` and `signals` groups. Apply the radar rubric only after research. Candidates await Joe or Dell's touch/watch/skip verdict. Update the existing candidate proposal loops instead of creating duplicate candidates. Use `add-loop` for a new proposal with `kind:"team_loop"`, a single owner and `marker:"decision"`; use only fields registered by its live contract. Close the preparation handoff after the worked output is recorded.

Unattended output is proposals, findings and run records only. Registry, roster and active-index entry waits for Joe or Dell's verdict. Never send or draft outreach. All output goes through record verbs; the former Markdown digests and dashboard generators stay retired.
