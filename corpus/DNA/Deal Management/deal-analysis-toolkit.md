# Deal-Analysis Toolkit — Reference

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*How Joe & Dell present deal economics to clients. Added July 7, 2026. Reference only — this documents the standard worksheets so any session understands how deals are framed for clients and can populate them for a real prospect on request. Master files live in `DNA/Deal Management/Templates/`.*

---

## The firewall (read first)

**The Commission Comparison / Base Rent Calculator is INTERNAL ONLY.** It computes CARR's commission and goes to the *listing agent* with the invoice — never to a prospect or client, and never referenced in any client-facing document, email, proposal, or social post. We never bring up our commission to the people we represent (representation is free to the tenant/buyer; the landlord/seller pays). Treat this sheet like back-office accounting. The other four are client-facing deal-analysis tools.

---

## The four client-facing tools

### 1. Lease Comparison (10-Year) — the workhorse
Compares up to **4 properties side by side**, each as **Initial Offer vs. Recommended Counter**. Models the full economics per property: base rent with escalations, operating expenses, months of free rent (rent + OpEx), landlord TI allowance vs. estimated build-out cost, the tenant's TI contribution and the interest to finance it, total gross rent, and **Total Cost of Deal**. Below the economics it lays out the qualitative terms that make or break a deal — exclusivity, building/monument signage, renewal options, reserved parking, utilities, purchase options. This is how a tenant sees, on one page, that the cheapest headline rate isn't always the cheapest deal.

*Key modeling defaults:* 3% annual escalation (or +$0.50/SF/yr on some columns), OpEx flat in year 1 with a footnote that it climbs, TI financed via CUMIPMT at the assumed rate (7.5% in the sample), 10-yr amortization.

### 2. 5-Year vs. 10-Year Lease Comparison — the "cost of going short" tool
Shows a client the true penalty of a shorter term. A 10-year commitment typically earns a better rate, a larger TI allowance, and more free rent; the 5-year gives those up. The sheet nets it all out into **"Additional Cost of a 5-Year Lease"** and then translates that into **how many extra years of rent** the penalty equals — a number a doctor feels immediately.

### 3. Purchase Comparison — three ownership options
Compares up to **three purchase scenarios** through the standard structure where the practice pays rent to the client's own real-estate entity. Models total initial cost (land, shell, interior build-out, A&E, soft costs), down payment and financing, annual P&I, operating expenses, the **tax deductions available to the business** (base rent paid to the entity, OpEx, build-out depreciation), the **after-tax annual/monthly cost**, mortgage principal reduction, and **market/equity value after 10 years**.

### 4. Purchase vs. Lease Comparison — the big-decision tool
The most complete model: **owning vs. leasing side by side**, on an after-tax basis. Beyond the cost comparison it does two things most clients have never seen — it computes the **cash difference** between the two paths and the **future value of investing that difference** (so leasing isn't automatically "throwing money away"), and it shows **net equity after 10 years** on the ownership side. Ends with a plain-language verdict pointing to whichever path wins on cash value.

> **How CARR actually fills & presents this → `purchase-vs-lease-fill-guide.md`.** Field-by-field input judgment (estimate high on costs/low on amortization, zero-out tenant income, median lease rate, the landlord-TI-in-misc trick), how to read each output for a client, the key "only models the practice's deductions, not the RE entity's" caveat, and the Goal-Seek "when does buying make sense" move. Most of its cross-tool principles apply to the other three worksheets too.

---

## Placeholder values to set at fill time

The templates ship with placeholder values. These are intentional starting points, customized with the client's real numbers each time a template is filled (see `fill-engine/` and its workflow SOP). The ones most worth setting deliberately per deal, because they move the outcome and go stale:

- **Interest rates are baked in** (5.5% purchase, 6–7.5% on lease-TI financing). These go stale fast — reset to current before sending.
- **Appreciation** defaults to 1%/yr in the Purchase Comparison's equity formula (conservative; the sheet's own note cites a historic average near 3%). Set it to whatever's defensible for the specific deal.
- **Escalations default to 3%/yr** (or +$0.50/SF on some columns). Reasonable, but should match the actual LOI/lease language.
- **Tax bracket defaults to 35%** and build-out depreciation to 15 years — confirm with the client's CPA framing; every sheet already carries the "consult your CPA / not tax advice" disclaimer, which must stay.

---

## How these feed the Comps Engine (added July 12, 2026)
Every real-terms run of these tools ends by filing a comp row in `comps.xlsx` (fill-it-in-workflow step 7). The comps book is the compounding asset: our own median rates eventually replace market estimates in these very templates (scale-vision §1). Internal only, same firewall discipline as the commission sheet.

## How these feed content (see DNA/Marketing/Social Media/content-inspiration-bank.md, §1C)
Every deal point these tools quantify is a potential educational post — free rent, the TI-vs-build-out gap, compounding escalations, the 5-vs-10 penalty, own-vs-lease, the real-estate-entity tax structure. The worksheets are a substance source for the content engine. The commission sheet is **never** content.

## The 70/30 owner-user building play (added July 22, 2026 — distilled from Dell's live prospect analysis; Claude, stamped)

*Source: Dell's 7/22/26 pitch on a Panama City Beach medical building (built with his brain, forwarded to Joe). Per Joe (same day): two and possibly three banks offer this product — Bank of America is one, and the others need to be identified and confirmed. Treat the lender as a per-deal shopping decision, not a single-bank product; competing term sheets are leverage. The prospect record stays Dell's; what's banked here is the reusable analysis pattern.*

**The product:** 100 percent financing (zero cash into the purchase) for a healthcare provider who buys a building, occupies roughly 30 percent, and leases the remaining ~70 percent to other healthcare tenants. The tenants' rent alone does not carry the debt at full financing — the buyer's OWN practice rent (money already being paid to some landlord today) closes the gap. That reframe is the heart of the pitch: your current rent becomes your equity engine.

**The analysis template (five moves, in Dell's order — it works, keep the order):**
1. **Market scarcity hook** — one growth stat plus the supply gap ("second fastest-growing metro, very little quality medical space").
2. **Carry math** — owner rent (30%) + tenant rent (70%) = building income; net after vacancy/management allowance vs. the loan payment; show annual cushion at 2-3 purchase-price scenarios (the price-negotiation lever previewed inside the math).
3. **Equity trajectory** — loan paydown + modest appreciation (3%/yr) at year 10 and year 15, "all built from zero down."
4. **Ground-up comparison (objection preempt)** — land + site work + core-and-shell per SF; leased-half income covers only a fraction of the payment; 18-24 months of waiting plus construction-loan cash. Kill the build-new dream with its own numbers, then note the clinical buildout costs the same either way, so the real variables are land, shell, income, and time.
5. **Motivation + fiduciary close** — days-on-market and seller motivation make the ask negotiable ("the real conversation starts below that"); CARR represents the buyer only, at no cost.

**Benchmark figures from the live example (Panhandle, mid-2026 — refresh per deal, never quote stale):** medical lease rate $32/SF on a growth corridor; ground-up core-and-shell ~$375/SF; site work $500-750K; clinical buildout ~$225/SF; ground-up all-in $575-725/SF before interior; existing 2021 building trading around $356/SF. The own-vs-lease fill guide and comps book supply the deal-specific numbers.
