# CoStar Playbook — what's actually in there, and the ten workflows worth building

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Created 2026-07-13 from a live walk of Joe's logged-in CoStar (Tenants → Tenant Locations, Pensacola - FL market). Everything below was SEEN, not assumed. Where the data is missing, this file says so.*

**The legal line, stated once:** CoStar is licensed to Joe as a subscriber. It **cannot be scraped** (that ruling is already in the idea bank as Retired R1). Everything here runs on **exports Joe is entitled to pull**, which we then process and enrich on our side. The derived lead lists are ours. Raw CoStar data is never redistributed, never published, and never handed to Dell's brain as raw CoStar data.

**🖥️ HOW TO GET INTO COSTAR (Joe, 2026-07-31) — the Claude desktop app browser. NEVER Chrome.** CoStar detects and blocks the Chrome extension on the first click; that route is closed, full stop. The desktop app browser runs clean and was verified end to end on 2026-07-31 including a full export. **Move slowly, like a human: one action at a time, no `browser_batch`, and stop immediately if CoStar ever challenges.** Full workflow and the saved export layouts: `DNA/Deal Management/space-search-sop.md`.

---

## THE HEADLINE FINDING — the field exists, the data does not

CoStar's tenant records carry an **Expiration** column, a **Rent/SF/year** column, and a **Rent Type** column. They also carry a filter for "Upcoming Lease Expiration."

**In the Pensacola market, all three are BLANK.** Every row. "Upcoming Lease Expiration" as a filter returns **zero results** against 167 medical tenant locations.

This is the single most important thing to know before building anything on CoStar here, and it reframes the Atlanta agent's system entirely: **he is not being lazy by estimating lease timing from tenure. He is being forced to.** The expiration data does not exist in tertiary markets. CoStar populates it where it has leasing-broker relationships and deal flow — major metros. The Panhandle is not one.

**So: derive, don't look up.** And our derivation is better than his, because of what we built the same day (see "Why ours beats his" below).

## WHAT IS ACTUALLY POPULATED (Pensacola, medical, verified 2026-07-13)

| Field | Status | Use |
|---|---|---|
| **Occupancy Type (Owned / Leased)** | ✅ populated | **The single most valuable field.** Removes owner-occupiers instantly. |
| Commencement | ⚠ partial | The anchor date for deriving an expiration. |
| Moved In | ⚠ partial | Backup anchor. |
| Tenant Name · Address · Suite · Floor · SF Occupied · % of Building | ✅ populated | The universe. |
| Employees · SF/Employee | ✅ populated | Size + a crowding signal (see workflow 6). |
| NAICS · SIC · Industry | ✅ populated | The vertical filter (dentists = 621210). |
| Landlord · Landlord Rep · **Tenant Rep** | ✅ available | **Blank Tenant Rep = unrepresented = a lead by definition.** |
| Best Tenant Contact · Best Tenant Phone · Location Phone · Website | ✅ available | Contact, though the DOH file beats it for doctors. |
| **Expiration** | ❌ **BLANK** | — |
| **Rent/SF/year · Rent Type** | ❌ **BLANK** | — |
| Next Break Date · Next Review Date | ❌ blank | — |

**Coverage reality check:** CoStar shows **167 medical tenant locations / 121 properties** in the whole Pensacola market. Our tax-roll pull found **363 medical-use parcels in Escambia County alone.** CoStar's tenant coverage here is partial — it is a starting list, not the universe. Never treat a CoStar count as the market.

## ⭐ TENANT INSIGHTS — CoStar already built the trigger list

A filter called **Tenant Insights** carries 15 pre-built lead triggers. This is the best thing on the platform and most agents never open it:

1. **Upcoming Lease Expiration** *(useless here — no data)*
2. **Tenant In Non-Contiguous Suites** — split across suites, needs to consolidate. A move waiting to happen.
3. Tenant In Multiple Buildings In Park
4. Tenant In Multiple Locations In Market
5. **Building For Sale** — their landlord is selling. Uncertainty = the moment to get in front of a tenant.
6. **Building Recently Sold** — **new landlord = renegotiation window.** Cross-check against our SDF sale data, which we now hold back to 2009.
7. Building Loan Status
8. **Upcoming Loan Maturity** — the landlord's debt is coming due. Landlord under pressure = tenant leverage.
9. **⭐ Loan Maturity Around Lease Expiration** — the landlord's loan matures near the tenant's expiry. That is *maximum tenant leverage*, and it is a checkbox. This is the most sophisticated signal on the platform.
10. Neighbors Moving In
11. **Neighbors Moving Out** — vacancy next door = leverage, and a space opportunity for a client.
12. Business Park Vacancy
13. Ongoing Building Renovations
14. Recent Building Renovations
15. **New Property Management** — new manager, new rules, relationships reset.

Also filterable: **Future Move** + **Future Move Type** (tenants already known to be moving), **Company Growth**, **Total Employees**.

---

## THE TEN WORKFLOWS

### 1. Renewal radar — the Atlanta play, done right
Seed: Tenants → Tenant Locations → NAICS by vertical → territory → export.
Then **our** steps, which he cannot do:
- **Subtract owner-occupiers.** Two independent sources agree: CoStar's `Occupancy Type = Owned`, and our tax roll's owner-name match (168 owner-occupied medical parcels across the six counties). A doctor who owns his building will never sign another lease. In his model they sit in Tier 1 forever.
- **Anchor the tenancy with real records**, not a guess: `Commencement` where CoStar has it, plus the **build-out permit** at that address, plus the **deed history** from our SDF.
- **Calibrate the lease term with our own comps** as the book fills, instead of assuming a national average.
- **Enrich with the DOH email** so the outreach is deliverable.
- Tier, write the estimate into registry cols 24-26 (`Est-Lease-Event` / `Event-Source=inferred` / `Event-Confidence`), and let the radar surface them.
**Feeds the scorecard line we fail worst: renewal mix, 0% against a ≥30% bar.**

### 2. The unrepresented list
Filter for a **blank Tenant Representative**. A healthcare tenant with no broker is a lead by definition — it is the entire CARR pitch, pre-qualified. Cross it with #1 and you have the call list.

### 3. Landlord-pressure plays (Tenant Insights 5-9)
**Building For Sale · Recently Sold · Upcoming Loan Maturity · Loan Maturity Around Lease Expiration.** Each one is a moment when a tenant's position changes and they don't know it. Nobody is telling them. We would be.

### 4. Consolidation plays (Tenant Insights 2-4)
A practice in **non-contiguous suites** is a practice that has outgrown its layout. That is a relocation lead with no expiry needed.

### 5. Space inventory / matchmaking
Properties + Availabilities: every medical-suitable space on the market, standing. When a Pellegrino or a Brown appears, we open a list instead of starting a search. This activates idea-bank #16 (territory property-inventory matcher), which has been parked for want of a data source.

### 6. The crowding signal
**SF/Employee.** A practice well below the medical-office norm is squeezed — an expansion lead with no trigger event required. Pair with **Company Growth**.

### 7. Lease comps — the one real gap CoStar could fill and doesn't (here)
Rent data is blank in this market, so **CoStar will not fill our lease-comps hole.** Our comps book has 61 *sale* comps and almost no *lease* comps, and lease comps are what you actually negotiate with. **They have to come from our own deals, the fill-engine, and debriefs.** That makes the Comps Engine more important, not less. Honest conclusion: the moat has to be built by hand, which is exactly why it is a moat.

### 8. Vendor discovery from construction
Properties → new construction / renovations. Every medical project names a **developer, GC, and architect** — three of our thinnest vendor categories. A property feed doubles as a vendor feed.

### 9. Broker intel
Landlord Rep and Tenant Rep fields tell us who is working what. Feeds `DNA/Network/brokers.xlsx` (76 rows, almost no relationship data).

### 10. Buy-vs-lease matchmaking
Sales listings + our NAL (which doctors already own property) + the SDF (what things actually trade for) = a targeted list of doctors who *should* own their building and don't. This is the Pellegrino conversation at 4600 Harbor Ln, generalized.

---

## WHY OURS BEATS HIS — the honest summary
He has: a tenant list and an assumed lease length.
We have: a tenant list · **an owner-occupancy subtraction from two independent sources** · real deed and sale history back to 2009 · a medical-property inventory of 1,541 parcels · every doctor's email from the state · entity filings that tell us who is going independent · and 15 prebuilt CoStar triggers he probably has not opened.

**The edge is not the CoStar export. Everyone has that. The edge is everything we join to it.**

## Run notes
- Export cap is 500 rows per pull — slice by county or NAICS to stay under it.
- "Clear All Filters" also clears the LOCATION. Re-enter the market after clearing.
- Space Use = Medical is the fast filter; NAICS is the precise one (621210 dental, 621111 physicians, etc.).
- The market is "Pensacola - FL (USA)" as a **CoStar Market**, not the city.
