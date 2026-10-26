# Purchase vs. Lease — CARR's Fill & Interpret Methodology

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Created 2026-07-07 by Claude Code (watch-video skill), from CARR's official tool-walkthrough video (presenter a senior CARR agent; "available on Agent Central"). This is the JUDGMENT layer the other Deal Management docs were missing: `deal-analysis-toolkit.md` says WHAT each tool models and `fill-engine/fill-it-in-workflow.md` says HOW to populate cells mechanically — this says HOW CARR says to choose the inputs and read the outputs. The tool itself is already in the system: `Templates/PurchaseVsLeaseComparison_TEMPLATE.xlsx` (confirmed identical to the portal's current file). Most principles here apply to all four client-facing worksheets, not just Purchase-vs-Lease. Timestamps index the source transcript in the appendix.*

---

## Cross-tool principles (apply to every worksheet)

- **Save first, as `Client Name – City – m-d-yy`.** The templates ship as "Dr. John Smith / Birmingham" placeholders — rename immediately so a stray placeholder never reaches a client.
- **Keep the FIRST (offered) comparison.** Save the terms initially offered, then a second file with the final negotiated terms. At close you show the doctor the before/after — the savings summary is the proof of value.
- **Never touch the Total / function columns** (E & I on this sheet). Only change the RATE columns (C & G). The totals are formulas driven off the rates; overtype one and you kill the function and silently break the model. If a number looks wrong, click the total cell and read its formula rather than editing it.
- **Re-derive the headline numbers before you present them (the reconciliation pass).** Don't trust a total because it reads right. Compute every percentage and "equals X years of rent" figure yourself from both endpoints, and check each number on the one-pager against the cell it came from. The error that burns you is the clean-looking wrong number, not the obvious one. Full procedure in `fill-engine/fill-it-in-workflow.md` step 5. *(added July 12, 2026 · Joe, Cowork)*
- **When unsure what an input means, read the Notes column to the right (col M).** Every input has a plain-English explanation there — use it both to fill correctly and to answer a client's "what's that number?" live.
- **Estimate HIGH on costs/rates, LOW on amortization — under-promise, over-deliver.** Tell the client you're deliberately estimating high, so they come back saying "it was cheaper than you said," never the reverse. Don't be excessive.
- **Send as PDF, never Excel.** A lot of work sits in this sheet; don't hand out the working file. If a client wants to "play with it," offer: "I can't send the file, but tell me what to change and I'll rerun it."
- **Fill EVERYTHING in yourself before calling your MB/RD.** Get the full sheet populated, then bring it to your managing broker for the counter strategy — don't call with a blank sheet.

## Purchase vs. Lease — field-by-field judgment

- **Space Occupied vs. Total Space Size.** If the doctor occupies 3,000 SF of a 5,000 SF building, "Space Occupied by Purchaser" = 3,000 and "Total Space" = 5,000. The extra 2,000 is potential tenant space (see Tenant Income).
- **Building vs. land purchase.** Buying a *building*: zero out the Land and Core-and-Shell rows, put the price in Total Purchase Price (per SF × building SF). Buying *land / ground-up*: fill Land and Core-and-Shell instead.
- **Land Value = for depreciation only.** Land can't be depreciated, so this value is subtracted from the depreciable basis. Lower = better tax treatment, but keep it accurate ($3/SF rural is fine if that's real).
- **Interior Build-Out.** Estimate a touch high (e.g. a $120/SF market → enter $125–130).
- **Architect & Engineering — know your market.** [near 03:44] In design-build markets (Birmingham) most A&E is baked into the build-out number, so it's small; in architect-run markets it can be ~$18/SF. If unsure, ask your MB or other agents.
- **Misc Expenses.** Purchase side covers attorney, loan, appraisal, survey, environmental; lease side covers attorney + loan. Fill realistically per deal (don't leave the placeholder) — often much less than the default. **If the doctor will be a landlord to sub-tenants, bury the TI they must give the sub-tenant here**: e.g. $35/SF × 2,000 SF = $70k ÷ 5,000 total SF = +$14/SF into Misc, and NOTE it in the notes cell so the CPA sees it. That landlord-TI is NOT bank-financed, so it raises the required down payment/cash.
- **Down Payment.** Estimate high; investment real estate (leasing part of the building) pushes the required down payment up. 0% is sometimes available now — but don't model the best case.
- **Interest Rate.** Enter the real rate if known; otherwise estimate high (note: 5.5% was flagged as "very high" in the source era — reset to current market).
- **Amortization Term — UNDERestimate.** Model a shorter am (e.g. 10–20 yr) unless a 25-yr is confirmed. A 25-vs-20-yr am is ~$1,000/mo different; promising a 25-yr they don't qualify for creates a hardship. Same "conservative" logic as estimating costs high.
- **Base Lease Rate = the MEDIAN (mid-term) rate, not the starting rate.** For a 10-yr term use the year-5.5 rate; with 5 years left use year 3. At $20/SF with 3% escalations, use $20 × 1.06 ≈ the year-3 rate. Using the starting rate understates the lease's real cost.
- **Operating Expenses.** Estimate high; tell the client it's an estimate they must verify before closing.
- **Tenant Income (the cell agents most often blow) — default it to ZERO.** A doctor must be able to float the entire purchase on the practice alone; never let sub-tenant income be load-bearing (if a COVID-type event empties that tenant, the practice can't be jeopardized). Best practice: run TWO scenarios — with and without the income — so the doctor sees it as gravy, not the basis. When you DO include it, it's **GROSS**: base rent PLUS the per-SF operating expenses / triple-nets (e.g. $20 base + $4.50 NNN = $24.50/SF × the tenant SF). Estimate the rate LOW (if you can get $25, model $20).

## Reading the outputs (how to walk a client through it)

- **Total Annual/Monthly Pretax Cost** = what the doctor must float on the practice. This is the number they care about most.
- **After-Tax Cost** = adds the annual tax benefit (base rent + OpEx + build-out depreciation × bracket). Roughly a ~$21k/yr swing in the example — a real, quarterly-felt benefit.
- **Mortgage Principal Reduction = net-worth growth, NOT a felt cost.** The doctor's net worth rises ~$42k in year 1 (and MORE each later year as the interest portion shrinks). Emphasize they will not "feel" this as an expense — it's equity accruing until sale/refinance.
- **Purchase Benefit — Net Equity after 10 yrs** = appreciated market value − remaining loan balance. Keep appreciation conservative (1% modeled; historic ~3%).
- **Cash Value — invested difference** = the after-tax annual cost difference between the two paths, invested at a yield (5%), future-valued over 10 yrs. This is why leasing isn't automatically "throwing money away." The sheet auto-labels which side wins (Purchase Benefit vs. Lease Benefit) based on after-tax cost.

## The big caveat to communicate

**This model only computes the PRACTICE's tax deductions — NOT the real-estate entity's.** Because the practice leasing from the client's own RE holding company is a separate taxable event, the sheet can't fully capture ownership's upside: the RE entity ALSO deducts mortgage interest and building-structure depreciation, which substantially reduce its taxable income. So the tool **understates** the true purchase benefit — say so when a client is on the fence toward buying.

## The "when does buying make sense?" move (Goal Seek)
 Data → What-If Analysis → Goal Seek: solve for the **purchase price** at which owning equals leasing (set the after-tax cost cell to match, or run it against the after-principal-reduction number). Powerful for a client who leases and asks "when would it make sense to own?" — and a deal-generation conversation even with clients who aren't actively transacting. (Economic-Aid-era note in the source: distressed non-healthcare landlords were more willing to sell/condo space — verify current conditions before using that angle.)

## How this connects to the rest of Deal Management

- The "estimate high on costs/rates, low on amortization" rule should guide how `fill-engine` placeholder values get set per deal (see `fill-it-in-workflow.md` step 2 — reset stale rates AND apply the conservative-direction rule).
- Content angles from this tool are already captured in `DNA/Marketing/Social Media/content-inspiration-bank.md` §1C (own-vs-lease, the RE-entity double benefit, "renting isn't throwing money away"). No new content entries needed from this walkthrough.
- Commission firewall unchanged: the Commission/Base-Rent calculator is internal-only and never part of any of this.

---

## Source transcript
Verbatim material and timestamps not retained (paraphrase-only policy, July 8, 2026); the source session is identified in the Source Material ingestion ledger.
