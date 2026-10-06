# Vendor Intro Timeline — introducing the client to the right vendor, at the right stage

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Added July 7, 2026 from Joe's "Vendor Intro Timeline." Source PDF filed alongside. This is the **concierge layer**: as a deal moves forward, the tenant rep connects the client to the right vendor category at the right moment. It's one of the concrete ways Joe & Dell add value beyond the lease itself.*

## How this differs from introduction-rules.md
`introduction-rules.md` governs **vendor ↔ vendor** intros (which of the network's vendors can meet each other, for networking — a political minefield). **This file is vendor → client**: which vendor *categories* to put in front of *the client* as their deal progresses. Different axis, different purpose. Both draw candidates from the same `vendors.xlsx`.

## The timeline (deal stage → categories to introduce)

| Deal stage | What's happening | Introduce the client to… | Maps to vendors.xlsx categories |
|---|---|---|---|
| **Initial Call** | first real conversation, feasibility | Lenders, Demographics, Consultants | Banker / Lender · Marketing / Demographics (demographics side) · Practice Broker / Consultant · SBDC Consultant |
| **Post Tour** | touring / selecting space, planning build-out | Attorneys, Equipment Reps, GCs, Architects, Merch Reps | Attorney · Supply / Equipment Rep · General Contractor · Architect / Design |
| **Legal** | LOI / lease or purchase negotiation | Insurance Agents, CPAs, Financial Advisors, Marketing, IT Providers | Insurance · CPA / Financial · Marketing / Demographics (marketing side) · IT Services |
| **Execution / Closing** | signing / closing, opening the doors | Associations, Study Clubs | Association / Study Club |

Two notes on the mapping:
- **Marketing / Demographics is one combined category** in the sheet but spans two stages — the *demographics* people belong at Initial Call (site feasibility) and the *marketing* people at Legal (pre-open marketing). Pick by the vendor's actual `Offers`, not just the category label.
- **Execution / Closing = Associations & Study Clubs** — professional community and growth connections for after the doors open. Now the `Association / Study Club` category (added Jul 7, 2026); it has no records yet, so add association/study-club contacts to `vendors.xlsx` to populate this stage.

## Operating procedure
1. When a prospect/client advances a deal stage, run the timeline for that stage.
2. **Get the candidate vendors:** local Claude Code runs the repo copy against the record layer: `~/carr-system/.venv/bin/python ~/carr-system/shared/vendor_intro_for_stage.py "<CARR_ROOT>" "<stage>" [STATE]` (records mode by default, `--files` for the xlsx read; ORDER 29b repoint, 2026-08-05). Cloud/Dell sessions keep the vault copy's original form: `python3 vendor_intro_for_stage.py <path>/vendors.xlsx "<stage>" [STATE]` — returns the stage's categories with real candidates, **referral-active ones first (★)**, state-matched, excluding "Avoid", "Target — not yet met", and "Prospect (uncontacted)" (never introduce a vendor you or Dell haven't actually built a relationship with — untouched/hunt-found prospects don't qualify until promoted). Run in the cloud workspace against a fresh export of the sheet.
3. **Match on top of that:** honor `introduction-rules.md` — vertical fit and state split (e.g., BofA vet lending Dahl=AL / Navarro=FL), and skip anyone the client already has (from the intake "Key players").
4. **Log every intro** via the record layer — `link-parties` (or `links[]` on the touch's `log-activity`) — so the two-way referral relationship is tracked; deals.md's referral/reciprocity ledger renders from those rows (ORDER 39, 2026-08-01).

## Referral ethics — how to present multiple vendors (founder Q&A, Nov 2024)
*(Added July 7, 2026 — from the Nov 2024 founder Q&A; source in the ingestion ledger.)*
- **Give the client multiple options in a category** (real-estate-law best practice; some states require ≥2 names) — BUT if a vendor **brought you the deal**, don't voluntarily bring in their competitors unless the client asks (fiduciary override).
- **Offer each vendor "a shot at the deal," never demand exclusivity.** "Bring me a lead; it's my job to win it." No vendor resents that.
- **Make the intros WARM and 1:1** — a separate email per vendor, coaching the doctor ("you'll get three emails, each a different person worth a conversation; I'm on their team hoping they win it"). Note that different banks fit different products (e.g., **Live Oak** for a large project with little/no down — `V-BNK-045`).
- **Don't hide behind "CARR/the law makes me give three names."** Lenders resent that framing. Say plainly "Dr. Smith asked me for multiple options," then coach: "if you've got the right product and good communication, your odds of winning are high — I can't steer, but I'll make sure they understand your product."
- **Never split referral fees, and never direct where a bank's referral fee goes** ("it's not yours to direct"). If a vendor is the true referral source, they keep the fee; if you are, you keep it. Merch/equipment reps often aren't even allowed to take referral fees — don't drag them into it. One *won* deal is worth ~10× a referral fee. (INTERNAL — fee territory; never client-facing.)

## How it wires into the rest of the system
- **Intake** (`DNA/Clients/intake/`): the "Key players" section captures which vendors the client *already has* vs. *needs*. This timeline says *when* to fill each gap. A blank slot + the matching deal stage = an intro to make.
- **Deal stages**: the four stages here track the deal's progression (roughly: engagement → touring → LOI/negotiation → closing), sitting under the pipeline status in the operator's own `DNA/Clients/clients-active.md`.
- **Our vendor network**: the actual people come from `vendors.xlsx`; this only sequences the categories.
- **Referral revenue**: every well-timed intro is a two-way referral relationship — the reason the network exists.

## Files
- `vendor_intro_for_stage.py` — the matcher (this folder).
- `CARR_VendorIntroTimeline_source.pdf` — the original one-page timeline.
