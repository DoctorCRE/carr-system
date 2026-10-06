# CARR Mapping & Demographics — What's Available and How to Request

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Created 2026-07-07 by Claude Code, from CARR's Nov 2024 team Q&A (source in the ingestion ledger). Reference for the map/demographic reports CARR's mapping team produces for clients. Complements `DNA/Research/costar-how-to.md` (some of this — demographics — is also pullable in CoStar). Turnaround ~4–7 days (the team does hundreds/month), so set a ~1-week expectation with the client.*

## How to request
- **In the deal in Salesforce** → upper-right dropdown → **Request Map**. All options and inputs are there.
- **Signed/ETL clients only.** These reports cost the team real time/money, so they're for people who've committed (signed the engagement), not prospects.
- **Give a tight area — 1–3 zip codes max**, not a whole metro ("show me every dentist in LA" gets declined). It's an analysis, not a data dump.
- Agent Central → **Resources → Mapping & Demographics** has videos, explanations, and sample outputs of each type.

## The map types
| Type | What it shows | Use it for |
|---|---|---|
| **Competition map** | Practices of a given type in an area (NAICS/SIC pull + a manual Google cross-check by the mapping team) | Site selection; density of competitors. **Not 100%** — offices change hands/names; treat as a tool, and have the client verify locally. |
| **Insurance mapping** | Which insurances are accepted by area/zip (Medicaid, Medicare, private — Cigna, BCBS, Principal, etc.) | Payer-mix / positioning for a new or relocating practice |
| **Demographic / population report** | Population, growth, income, education, density | Feasibility; also pullable in **CoStar** if you can't wait |
| **Patient-based heat map** | Plots an existing practice's actual patient base (1k–5k patients) by where they live | Deciding where to **relocate or add a 2nd office** — see below |
| **Layering map** | Combines layers — e.g. income + population density + competitors — on one map | Pinpointing the best zip for a startup (keep it to 1–2 zips or it's too many data points) |

## ⚠️ Patient-based heat map — HIPAA note (CONFIRMED COMPLIANT)
The patient heat map plots real patient location data, which is exactly the kind of work CLAUDE.md flags as needing HIPAA compliance. **Joe confirmed (July 2026): CARR's patient-heat-map process IS HIPAA-compliant** — it follows specific required steps for handling the data. So it's usable, but **follow the documented process exactly** (Agent Central, or reach out to the mapping team for the steps); don't improvise the data handling. This is the one map type with compliance steps — the others use public/aggregate data.

### The heat-map process, as actually run (added 2026-07-31 — observed on Brandvold C-153)

This section used to say "reach out to the mapping team for the steps." Here are the steps, captured from a live run: Dell → Mike Brandvold, 2026-07-21, client confirmed signed and sent the same week.

1. **CARR sends the HIPAA compliance form to the doctor for e-signature.** Nothing moves before it comes back.
2. **The doctor prepares the file themselves.** Excel, columns **`ADDRESS | CITY | STATE | ZIP`** in that order. PO boxes removed. Duplicate records removed. **No names, no other personally identifiable data** — addresses only.
3. **The doctor emails the file DIRECTLY to the mapping department.** It does not route through the agent. Contact: **Stephanie Leeson — stephanie.leeson@carr.us**.
4. **The doctor tells the agent once it's sent**, so the agent can watch for the finished map.

**The agent never handles the patient data.** That is the design, and it is most of why the process is compliant — the file goes doctor → mapping department, and the two people with a copy are the ones who need one. Do not offer to collect, clean, or forward it as a convenience.

**Turnaround expectation:** the ~4–7 day figure at the top of this file is CARR's internal norm. Dell quoted the client **two weeks** on this run. Quote the longer number to clients and let it land early.

## Caveats & alternatives
- **Nothing is 100%** — offices change hands, new ones open. Present any map as a *tool*, paired with local market knowledge and the client's own due diligence.
- **Third-party alternatives** the client could also use: EOS Marketing, Dentographics, Reelscore (typically ~2-week turnaround — CARR's in-house is faster).

## USAFacts.org — free public market data (added 2026-07-13)

*Corporate recommendation from Ryan Gillespie (Regional Director), team email July 13, 2026. He relied on it as an agent/broker and still uses it. Paraphrased per the knowledge policy; merged same day by Joe's brain.*

- **What it is:** a free public site compiling objective data from federal (including the U.S. Census Bureau), state, and local government agencies. No login, no cost, no signed client required.
- **Most useful for CRE:** the population dashboards, https://usafacts.org/population/ (drills to state and county level).
- **Data available:** population trends (state and county), employment statistics, income levels, business activity, government spending, housing statistics.
- **When to use it:** strategic conversations with clients and vendor partners, property tours, and any recommendation that should rest on objective data rather than opinion. Ryan's suggested habit: keep it bookmarked for client research days.
- **Where it fits the stack:** instant and self-serve, so reach for it when a CARR mapping-team report (signed clients only, ~1 week) or a CoStar pull is more than the moment needs. For Panhandle work, county-level population and income here pair well with CoStar's demographics layer (`costar-how-to.md`).

## Reading the data critically — two distortions to correct for (added 2026-07-29)

*Our own field observation, developed in conversation with a multi-practice group client (C-156) weighing coastal sites. This is interpretation, so it stays on our side of the line described below.*

### 1. Coastal population data undercounts the real patient pool
**In beach and resort markets, census-derived population and income figures systematically understate demand.** A meaningful share of housing is owned by people holding second and third homes who **do not homestead** the property. They are not counted as residents, they don't appear in the population base, and they don't move the income figures. But they are physically here for large parts of the year and they consume healthcare while they're here — often paying **cash**, because their insurance network is somewhere else.

**The case that proved it:** a doctor we helped with a startup in a different vertical called about 18 months in to say he had **roughly 1,000 out-of-state patients, most of them paying cash**, and that none of it was in his business plan. It took a while to work out where they'd come from. That's the mechanism.

**How to use it:** when a demographic report on a coastal zip looks thin, don't accept it at face value. Ask what the seasonal and second-home picture is before writing the market off. Conversely, don't let a client build a pro forma that *assumes* this windfall — it's an upside case, not a base case.

### 2. Competitor counts overstate competition
A competition map tells you how many practices sit inside a radius. It does not tell you how many are actually competing. Before treating a market as saturated, screen the incumbents on **quality of operation**: are they buying Google ads, is the website current and interactive, how many Google reviews do they have and how recent. A practice can occupy a pin on the map and be functionally dormant.

Two related field observations:
- **The gas station theory.** Two stations on opposite corners, one dead and one slammed. Same location, same traffic, completely different outcomes. Position on a map explains less than operators expect.
- **Dentists cluster, and clustering is not automatically fatal.** They congregate in the same corridors and effectively create competition for themselves, yet patients treat the cluster as "where you go for a dentist" and multiple practices in one small center routinely all do fine. Nobody walks down the hall into the wrong office.

We have seen doctors plant on sites that looked alarming on paper and then perform very well. The takeaway isn't that site selection doesn't matter — it's that the map is a starting point, and the legwork behind it is what decides.

> **Line to hold:** everything above is *interpretation*. Per §Caveats, we provide data and market knowledge; the client owns the site decision. Raise these as questions to pressure-test their thinking, not as a verdict we're issuing. What works for a cosmetic-focused practice would sink a Medicaid-focused one.

[stamp: Joe's brain, Claude Code, 2026-07-29]

## Related
- `DNA/Research/costar-how-to.md` — CoStar covers the demographic layer yourself (§5 Layers) if you don't want to wait on a report.
