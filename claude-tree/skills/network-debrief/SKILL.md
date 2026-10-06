---
name: network-debrief
description: >
  Captures Joe Bookout's real-world CARR activity — meetings, calls, introductions, and
  meaningful touches with his vendor network, clients, and prospects — and logs it into the
  networking brain so the system stays current and the Monday brief reflects reality. Writes to the
  RECORD LAYER through verbs (stamp-touch / log-activity for touches, update-vendor, new-vendor,
  new-lead, set-next-action, link-parties, add-loop) — never by editing vendors.xlsx, lead-registry.xlsx,
  clients-active.md, hunt-ledger.md or open-loops.md, which are all GENERATED renders. Hand-authored
  narrative (the DNA/Clients/prospects/ detail files) is still edited directly (corrected 2026-08-06, loop #142 propagation pass): the old pointers here — 00_Context/prospects.md, the retired Prospects/ folder, DNA/Network/vendors.md (a file that does not exist) — are all dead.
  Use it whenever Joe wants to recap or document what
  happened: after a big day or a single important meeting, at the end of a week, or any time he says
  things like "document my day," "log my day," "log my week," "I had a big day," "recap my meetings,"
  "debrief me," "here's who I met with," "update the network," "I talked to [name] today," "log this
  meeting," "who I saw this week," or "field notes." It runs a short interview, then updates the right
  records (last-touch date, relationship stage, what each contact is now Seeking, new Links/introductions,
  brand-new people, deal-stage changes) and confirms exactly what changed. It also captures the rest of the
  conversation exhaust — market observations and post-worthy material to the content substance bank
  (DNA/Marketing/Social Media/content-inspiration-bank.md), objections heard and pitch language that landed to the
  prospect file (and DNA/templates.md as a candidate rule), and unowned commitments opened via the add-loop verb —
  so also use it when Joe says "I heard something interesting today," mentions a market data point, an objection
  a prospect raised, a story from the field, an event he did with vendors ("we hosted a happy hour"), an intro
  outcome ("those two ended up doing business"), a vendor's niche ("he only does vet loans"), or a new lead from
  any conversation — the routing table inside covers all of it, including the lead registry. This is the CAPTURE / INPUT
  tool that keeps the brain fed — it is what makes the introduction matching get sharper over time.
  Do NOT use it to GENERATE the brief or find introductions — that's the engine (DNA/Network/engine.md;
  "run the network brief"). Do NOT use it to draft outreach emails (DNA/templates.md) or write social
  posts (write-content), and not for the monthly content calendar (social-media-manager).
---

# Network Debrief — keep the brain fed

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

Joe's networking brain is only as good as how current it is. This skill is the capture ritual that keeps it alive: Joe recounts what happened — a meeting, a big day, a whole week — and Claude turns it into precise updates across the system, so the Monday brief and the introduction matching always reflect reality. It's the input side; `engine.md` is the output side.

As of July 6, 2026 this skill is the capture layer for the *whole* system, not just the network: a conversation isn't just a touch — it can also carry a market observation worth a post, an objection worth learning from, and a commitment worth tracking. One debrief routes all of it. (Extension prompted by the "Single Company Brain" audit — rationale in 00_Context/decision-history.md.)

## When to use / when not to

**Sibling mode:** when Joe asks for a "vendor interview" / "quiz me on vendors" — Claude DRIVING through the sheet's pending gaps rather than capturing what Joe reports — run `CARR AI/DNA/Network/enrichment-interview.md` (ability #27). Same routing rules as this skill; different direction.

**Use it** any time Joe wants to log real activity: after a meeting or coffee, at the end of a busy day, or as the weekly recap (the Monday `weekly-routine.md` calls this skill for Part 1). Even a single line — "just met Josh Holden, he wants pediatric clients" — is worth running.

**Don't use it** to generate the brief or hunt for introductions (that's `engine.md` / "run the network brief"), to draft outreach (`DNA/templates.md`), or to write posts (`write-content`).

## The debrief flow (quick, low-friction)

Ask conversationally, **one or two questions at a time — never a wall of six**. It's fine if Joe only answers one; any update beats none. Adapt the scope to what he brings (a single meeting, a day, or a week).

**Hands-free / voice mode (added July 31, 2026 — the drive-time debrief, Joe's idea, idea-bank #43):** when the session is voice (phone, truck Bluetooth), reshape the same flow for ears: ONE short spoken question at a time, never a list; confirm each capture back in one sentence ("logged — Chris intro'd you to the Provide lender, edge written") so Joe never needs the screen; no tap-through question boxes (those are screen furniture — take the verdict verbally); park anything needing eyes ("I'll put the two candidates in front of you when you're parked") rather than spelling records aloud. Same routing, same rules, same edge-first intro capture — only the surface changes. Never let the conversation pull toward patient specifics (HIPAA): the day's recap is people, deals, and market color.

**Tap-through where the answer is a verdict (added July 12, 2026):** when a question has a small fixed set of answers — the new-vendor PURSUE/TABLE verdict below, a stage change, "worth a follow-up or not" — use the structured question tool (AskUserQuestion) so Joe taps an option instead of typing, and reserve free-text for the open questions (what happened, what they said, what he heard). Cuts the friction of a debrief roughly in half on a multi-meeting day; open storytelling questions stay conversational, never multiple-choice.

1. **Who did you meet or talk with?** For each: how did it go, and did anything change — relationship warmer or cooler, a next step, a new name that came up?
2. **Did any of them say what they're looking for** — more clients, referrals, or a specific person they want to meet? *(This fills `Seeking` — the biggest lever on introduction quality.)*
3. **Did you introduce anyone, or get introduced?** Who ↔ who?
4. **Anyone new** to add to the network or the pipeline?
5. **Any deals move** — LOI out, lease signed, went cold?
6. **Anything you owe** — a promised follow-up, or a lead to send back to someone who's helped you?
7. **Hear or see anything worth keeping beyond the people?** — a market data point, a rate or deal term, an objection or question a prospect raised, something surprising from the field. *(This feeds the content substance bank and the outreach playbook — the two hungriest parts of the system.)*

**Comp capture (added July 12, 2026 — the Comps Engine):** if the answer to #7 (or any answer) contains REAL deal terms — a rate, TI, free rent, escalations, term, concessions someone actually executed, quoted, or reported — ALSO file a comp row per the routing below. One conversation can feed both the content bank and the comps book; the comp row is the one that compounds.

**New-vendor verdict (added Jul 7, 2026):** whenever Joe mentions meeting a NEW vendor (or a first real meeting with a known one), ask ONE extra question: "worth active follow-up, or table them?" PURSUE → Stage `Building (working on it)` + a concrete Next Step. TABLE → Stage `Tabled (parked — drip only)` + Next Step `Monthly vendor newsletter`; if Joe says there's real referral upside, add `[tabled+potential]` to Notes (quarter-month check-in drafts pick it up). System definition: `CARR AI/DNA/Network/README.md` → Tabled vendors.

## Turning answers into updates (the important part)

Apply each answer to the right record, following the existing schema and rules:

- **Vendor touches → the RECORD, via verbs.** `stamp-touch` (or `log-activity` for anything with detail) for the contact itself — that is what moves Last Touch and lifts capture coverage; `update-vendor` for stage/seeking/offers/territory; `new-vendor` to create one (it mints `V-<CAT>-###`); `link-parties` for who-knows-whom. **`DNA/Network/vendors.xlsx` is a GENERATED export — never edit it.** The prose notes below still describe the fields you are setting: update `Stage` (Fully aligned / Important / Building / Decent / Follow-up needed / Avoid), add anything they're `Seeking`, add `Links` (who they know / want to meet), append a dated `Notes` line. New vendors get a new record with an ID (`V-<CAT>-###`), owner-tagged **Joe / Dell / Shared** and `Originated`.
- **Introductions → the intro-graph EDGE FIRST, prose second (ORDER 34, 2026-07-31 — fixes the 272-empty-Links starvation):** the moment a debrief records "A introduced me to B" / "I introduced A to B", write the `party_link` edge THEN via the record layer — `link-parties`, or the `links[]` array on the `log-activity` call you are already making for the touch. REFS ONLY (resolve via `find`; kinds: `knows`, `intro`, `intro_received`, `can_introduce`, `works_with`, `referral`). If either person is ambiguous or has no record yet, ASK Joe which record (or create it first) — never guess a name match, and never park the edge as prose alone. THEN the prose: put the who↔who color in the `log-activity` note (deals.md's reciprocity ledger renders from those rows as of ORDER 39, 2026-08-01), and a `Links` entry on both people — as color; the edge is the record.
- **Client / prospect activity → the RECORD, via verbs.** `stamp-touch` / `log-activity` for the touch, `set-next-action` for whose ball it is and by when, `new-client` to create one (mints the `C-###`), `update-deal` for deal fields and `set-lead` for deal ownership. **`DNA/Clients/clients-active.md`, `client-roster.xlsx` and the `dossier-*.md` set are GENERATED — never edit them; log analysis with `log-activity` kind:`analysis` and re-export.** `00_Context/prospects.md` and `Prospects/<name>.md` are still hand-authored narrative, so they remain fair to edit (status, last touch, next step). New prospects get a `C-###` id in `Prospects/prospects-roster.md` and, if active, their own detail file. Keep prospects.md short.
- **Deal-stage changes → the `update-deal` verb** (LOI / CA / Lease) and a dated note in the client's detail file — deals.md is unguarded markdown a write to which the system never sees (corrected 2026-08-06, loop #142 propagation pass).
- **Market observations, stories, objections-as-content → `DNA/Marketing/Social Media/content-inspiration-bank.md` (Section 2, Real substance bank):** use that file's entry format (date, input, usable-for, anonymization needed, angles used). Mark anonymization honestly — while the pipeline is small, real situations are identifiable even anonymized (write-content guardrail), so record them for later or generalize hard. Never patient data of any kind (HIPAA).
- **Objections heard + pitch language that landed or flopped → the prospect's `DNA/Clients/prospects/<name>.md` file** (dated note — the old Prospects/ folder is retired). If the same objection or win shows up more than once, or Joe confirms it generalizes, propose it as a rule/hook change in `DNA/templates.md` — don't silently edit approved voice rules without his yes.
- **Commitments and follow-ups without a single home record — or anything blocking system work → the `add-loop` verb** (owner, what it unblocks; `marker:'bell'` for actionable this week, `'dated'`+`due_on`, `'decision'` for an open question, `'none'` for backlog). **`00_Context/open-loops.md`, `open-loops-backlog.md`, `DNA/Team/action-required.md` and `team-loops.md` are GENERATED renders of those loop rows — a hand-edit there is a lost entry.** A settled ruling is not a loop: that is `log-decision`. A promised follow-up to a specific vendor/prospect goes on their record as the next step instead; open-loops is for the rest.

- **Verticals → the vendor's record:** if a conversation reveals a vendor's sub-specialty ("he only does veterinary loans"), set the `Vertical` field — it drives matching and the politics rules.
- **Joint events (happy hours, panels, lunch-and-learns) → `log-activity` on the co-host vendors, with `links[]` between the parties:** who co-hosted (V-IDs), cost split if mentioned, any leads it produced (L-### refs). deals.md's Joint Events log is a RENDER of these rows now (ORDER 39, 2026-08-01) — never hand-write it. Past events Joe recalls casually get backfilled the same way.
- **Joint projects (co-marketing, planned panels, shared booths) → `log-activity` on the vendors involved + an `add-loop` row carrying the next step and owner** — the deals.md Joint Projects log is superseded by the record (corrected 2026-08-06, loop #142 propagation pass).
- **Intro outcomes ("those two I connected actually did a deal") → `link-parties` (kind introduced/referred) + `log-activity` on both vendors** — the party_link edges are what quality-weighted reciprocity actually reads; the deals.md Intro Outcomes log is superseded (corrected 2026-08-06, loop #142 propagation pass). **When an intro RESOLVES, also set its Delivery grade (A/B/C/F — did the vendor perform, deals.md rubric); use the tap-through (AskUserQuestion) for the grade. That grade feeds the Vendor Delivery Scoreboard, which steers who gets the next client (introduction-rules.md).**
- **New leads from any conversation → the `new-lead` verb** (mints the L-ref atomically; set `lead_stage`, owner, and the source/vendor attribution — attribution stays mandatory). **`DNA/Leads/lead-registry.xlsx` is a GENERATED export and must never be hand-edited**; it renders from the lead records. Add a detail file if the lead is immediately active. The record is Claude's to maintain; Joe never needs to open the spreadsheet.
- **Deal terms observed (rate/TI/free rent/escalations/term/concessions, executed·quoted·reported) → `DNA/Deal Management/comps.xlsx`** (added July 12, 2026): new CMP-### row (max+1), Confidence honestly set (Executed/Quoted/Reported — NEVER estimates; the Config tab integrity rule governs), Owner-tag = who captured, Evidence Pointer to the conversation/debrief. If the term implies a lease expiration, also set the registry row's `Est-Lease-Event` columns (Radar lane 3).
- **Pairing-rule learnings ("never put those two in a room") → the `teach` verb, scope `intro_politics`**, in his verbatim words; propose activation for his yes. `DNA/Network/introduction-rules.md` is the GENERATED render — never edit it.
- **Vendor-candidate verdicts ("yes connect with that CPA" / "pass") → `update-vendor` (stage) plus a `log-activity` note carrying the verdict.** The hunt ledger (`DNA/Network/hunt-ledger.md`) and `deals-reciprocity.generated.md` are GENERATED — they render from those rows, so never edit them directly.

Rules to honor: keep the **two brains separate** (vendors vs. clients), linked only by ID + deals.md. Preserve **Owner / Originated** tags. Run any vendor recommendation or intro that surfaces mid-debrief through **`DNA/Network/introduction-rules.md`** first. **Never scrape Dell's SharePoint sheet** to reconcile — if a re-sync is needed, Joe exports the .xlsx to a connected folder.

## Confirm what changed

After updating, tell Joe **exactly what was written** — each record touched and the field changed — so he can catch anything wrong. Then, if this was the weekly run, hand off to `engine.md` for the brief; otherwise just note that the updates will feed the next brief.

## Why this exists

Every relationship CRM dies when nobody feeds it. This skill turns feeding it into a two-minute conversation instead of data entry — and every answer quietly fills the `Seeking`, `Links`, and stage fields that make the introduction engine smarter each week. And because Joe's conversations are this system's equivalent of a big company's call transcripts, the same two minutes now also feeds the content substance bank and the outreach playbook — the raw material every other skill is starved for.
