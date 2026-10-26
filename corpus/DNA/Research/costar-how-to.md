# CoStar — How-To for CARR Agents (property search, pre-tour, comps, renewal-hunting)

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Created 2026-07-07 by Claude Code (watch-video skill), from a chaptered CoStar screen-share tutorial (`training.carr.us/sessions/costar`, video, 59 min; Southeast/Nashville). Practical, feature-by-feature walkthrough of CARR's main market-research tool. Access CoStar via **Agent Central → top bar → CoStar** (2FA login). **🖥️ ACCESS RULE (rewritten 2026-07-31, Joe's direction — supersedes the 2026-07-24 "browser tools stay off CoStar" gotcha):** **CoStar runs in the CLAUDE DESKTOP APP BROWSER ONLY. NEVER Chrome, never the Chrome extension** — CoStar detects and blocks it on the first click, and that route is closed, full stop. The desktop app browser was verified end to end on 2026-07-31 including a 233-row export with no challenge of any kind. **Drive it slowly, like a human — one action at a time, never `browser_batch`, and stop the moment CoStar challenges anything.** Joe-pulls-and-exports stays valid whenever Claude driving would save nothing. Full workflow and the saved "Sale Export" / "Lease Export" layouts: `DNA/Deal Management/space-search-sop.md`. Paraphrase-only per the Jul 8 policy: no transcript stored; the source session is identified in the capture ledger. NOTE: CoStar's UI changes over time — treat exact button locations as approximate and confirm live; the *methods* are what matter. The **"Finding Renewals" (§8) and vendor warm-intro** parts are prospecting gold and are cross-referenced in `DNA/Leads/lead-system.md` and `DNA/Deal Management/playbooks/renewals.md`.*

---

## 1. The one setting people get wrong first
**Leasing vs. Sales tabs.** Clicking **Sales opens in *Sales Comps* (already-SOLD properties), not active listings** — you must switch to **For Sale** for what's on the market. Leasing opens in For Lease correctly. (Same trap on the lease side: For Lease ≠ Lease Comps — see §7.)

## 2. Property search / building a pre-tour set (For Lease)
- **Goal: cut the list down.** Draw the exact area with **Polygon** (his preferred — excludes the junk a radius would sweep in); Radius and Corridor are the alternatives.
- **Filter by square footage with a buffer** — client wants 1,800–2,200 SF? Search ~1,200–3,000 first so you don't miss a perfect 1,750. **Target 15–20 properties** for a pre-tour. Early in a market, stay BROAD (you don't know it yet); once you tour it 6–7×/yr you'll know it cold.
- **Select vs. Favorite**: use **Select** for "part of THIS search." **Favorite** (heart) persists across ALL your searches — reserve it for a built-out spot that's great for dental/vet you want flagged everywhere, not for a single search.
- **Do NOT eliminate on price/SF yet**: some figures are CoStar *estimates* (not real), landlords may flex, and showing an expensive "dream" space is useful **anchoring** — it teaches the client what the market really costs, and a landlord who loves your use (e.g. "veterinary has the lowest default rate") may get aggressive on rate. Let price eliminate properties in negotiation, not in the search.
- Detriment flags while scrolling: dated buildings, **steps (ADA — look for a ramp)**, mostly-warehouse SF, second-floor/off-the-path. Residential-turned-commercial can be great in some submarkets.
- **Filters:** "Existing" removes not-yet-built; also under-construction / under-renovation; property type (office/retail/healthcare) — only narrow type when you're drowning in 40–50+ results.

## 3. Pre-tour report + why you pre-tour
- **Build a pre-tour report** (Reports → property map overview + ~4 properties/page, map always the first page — reorder by click order). Print it and **drive the market taking notes.**
- **Why pre-tour**: find **unlisted properties not in CoStar** (drive the thoroughfares — "the worst thing on a tour is the client asking 'what about that one?' and you don't have it"). It's also the **best time to cold-call / letter-drop** (Agent Central has videos): the due-diligence itself is the pitch — "this is how much work I do for my clients; it'd be my honor to have you as one."

## 4. Tour packet (client-facing)
- **Sort custom:** top option first, then by location (don't zigzag across town). Use the **Classic one-page report**.
- **Open it in Word, not PDF** — so you can change each listing rate to **"NA."** You don't want the client anchoring on or dismissing a space over an asking rate that won't reflect the negotiated deal (you'll be pushing for TI, deliverables, better terms as a strong tenant). Explain up front: "no numbers yet because we haven't negotiated."

## 5. Layers (demographics for the packet)
- Layers → **Demographics → population density + median household income** (his two most-used). **Screenshot with the legend** (population: red = dense; income: green = high — opposite scales) and drop into the Word packet to show the client *why* an area is good (high population + high income = good for the practice).

## 6. Save Search + Alerts (the highest-leverage feature)
- **Save current search → name → folder** (a Clients folder; or Nashville-hot-areas). Shareable with your RD/MB/national-accounts.
- **Enable Alerts** = the biggest payoff. You get emailed the moment a new property matches your filters. In tight markets this wins deals — an agent reached out **5 minutes** after a listing went live and got it. Keep **5–10 saved searches on hot pockets** of town.
- **Updating a saved search**: after removing properties, don't just click the saved-search name (that RELOADS the old full set) — eliminate, then **Save Current Search → "Update existing saved search."**
- Do the same on the **Sales / For Sale** side.

## 7. Comps
**Sales comps** [Sales Comps tab]: aim for **6–10 good comps**. Limit by radius (~1 mi), size (±), and time (≤ ~2 years). **Drop the high and low outliers** (a random $1,440/SF sale isn't a comp) and portfolio/multi-property sales. **Filter to Owner-User** — investment sales (someone buying the lease) aren't a clean comp for an owner-occupant. Use them to argue a listing agent's ask is high; cross-check **For Sale** to show what's currently asking.

**Lease comps** [Lease Comps tab — remember For Lease ≠ Lease Comps]: filter by SF (±) and sign date (~2 yrs). **Comprehensive report, 6 comps/page.**
- **CRITICAL: the report's front page (average asking rent/SF) lies** — it blends triple-net, full-service, single-net, and modified-gross (a $20 NNN + $12 CAM ≠ a $26 full-service). **Save pages 2–8, not page 1** — don't hand a client or agent a false average.
- **Posture when comps are thin**: ask the **listing agent to send comps justifying their price.** Either they do (you share with the client) or they can't — which puts them on their heels: "I'm sending an offer; don't shoot the messenger, we couldn't find anything to justify that price."

## 8. Finding Renewals — the Tenants tab (prospecting engine)
This is the biggest prospecting use. Tenants tab → tenant locations → big radius. It's the **same data as Lease Comps**, fed by agents answering CoStar's calls — so **NEVER answer CoStar's "what rate / when does your lease end?" calls about YOUR deals**, or other agents will poach your clients at renewal. Data isn't 100% accurate but it's enough to start conversations.
- **Filter by tenant industry via NAICS code**: look up "dental" → **621210**; also optometry, veterinary, and **MD → 621111** (NAICS is the same in every market). Paste the codes to filter.
- **Filter by lease expiration ≤ ~2 years** (include month-to-month) → a named list of practices with leases coming up (e.g. "Cambridge Dental, Maps Orthodontics").
- **The warm-intro play (don't cold-drop)**: take the *named* list to your **vendor contacts** (the dental/optometry rep): "Do you work with Davis & Williams? Their lease looks like it's up Jan 2025 — good time to talk." The vendor brings you in as a value-add (a savings letter with the doctor's name). **Naming a specific doctor gets far better results than "got any doctors for me?"** — the rep has too many contacts to think of unprompted.
- **Run this for your market every ~6 months**, and **enable Alerts on the tenants search** to get pinged when a new lease appears in your category.
- Also: **Keyword search** (space features → keyword "dental") catches listings that mention a use.

## 9. Low-inventory / secondary markets
Thin market? Before filtering, go to **List View and see who the listing agents are** — if one agent holds 6–7 listings, call them: "any pocket listings?" They're connected and know what's coming. Judge relevance by what they list (skip a heavy-industrial agent for a medical search). If a local agent surfaces spaces CoStar didn't, that's **market knowledge / pocket listings / a market-specific search engine** — ask your MB/RD whether that market has a better data source.

---

## Cross-references (where the prospecting parts also live)
- `DNA/Leads/lead-system.md` (self-sourced prospecting) — §8 above (Tenants-tab renewal hunting + the vendor warm-intro) is a concrete lead-sourcing method.
- `DNA/Deal Management/playbooks/renewals.md` — the founder's "finding renewal opportunities" chapter; §8 is the CoStar mechanic for it.
- `DNA/Deal Management/deal-analysis-toolkit.md` — comps (§7) feed the lease/purchase comparison worksheets.

---

## Source transcript
Verbatim material and timestamps not retained (paraphrase-only policy, July 8, 2026); the source session is identified in the Source Material ingestion ledger.
