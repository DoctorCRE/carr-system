# CARR Training — Other Healthcare Reference (Chiropractic + Therapy: build-out, parking, the low-TI angle)

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through a guidance-file pull request (AGENTS.md "Guidance changes") or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

*Captured July 7, 2026 from CARR's internal Agent Training Portal (`training.carr.us`), the "Other Healthcare" dashboard. CARR proprietary agent knowledge — internal use. Source pages at the bottom. Companion to `dental-`, `medical-`, `vet-`, and `vision-vertical-guide.md`.*

**What "Other Healthcare" is:** on the portal's vertical taxonomy this dashboard covers two smaller verticals — **Chiropractic** and **Therapy** (the therapy build-out page is **Physical Therapy**). CARR internally groups these with the specialist branches as the "**Vet, Vision, Therapy**" agent vertical (per the chiropractic page — that's the team to escalate these deals to). Neither page states a SF sizing band and neither has a standalone Pitch-to-Landlord page (the chiropractic page bakes the landlord narrative into its body). Both are **lower-build-out** uses, which is itself the negotiating angle.

**Where this plugs in:** intake (`DNA/Clients/intake/` — Chiropractic and Therapy verticals), due diligence (x-ray/electrical for chiro; imaging-proximity for PT; parking on both), deal analysis (`DNA/Deal Management/` — low-TI leverage), and site selection (referral proximity for PT).

---

## Chiropractic

- **Build-out is simpler than most medical offices.** Reception/waiting + a few exam/treatment **rooms or bays** (some chiropractic treatment areas are **not fully enclosed**). Each exam/treatment room holds an **adjustment table and/or chair** (for leverage on the adjustment).
- **Biggest technical need = X-RAY.** Many chiros x-ray to assess patients and monitor progress. That means **heavier electrical demand** and **lead-lined walls**. (Loop in the MB — many verticals use x-ray, so the requirements are familiar.)
- **The low-TI negotiating angle (semi-internal — set the narrative):** because chiros need **less build-out than other medical uses**, pitch the landlord that they're getting a **solid healthcare tenant who needs less TI and a shorter build-out period** — leverage to ask for a **lower rate or a longer free-rent period.** And a vacated chiro space **re-fills easily** (another chiro, or an easy transition to a new use). Frame the chiro's lighter footprint as an *asset* to the landlord. (If you need help crafting this, go to your MB or a senior agent in the Vet/Vision/Therapy vertical.)
- **Quick visits → parking is about VOLUME, not duration.** An optimal chiropractic visit is **under 15 minutes.** Appointments stack close together, so you need **parking volume** — but each car is there briefly. **Key concession play:** in a crowded strip center where the landlord won't grant designated spaces, **ask for 15-minute parking spaces in front** — it keeps neighboring stores' employees and long-stay users out and reserves those spots for the chiro's fast turnover. Also ask the doctor how many patients will be in the building at once.
- **Sizing:** not specified on the portal — benchmark per deal if it matters.

---

## Physical Therapy (the "Therapy" vertical)

- **Think "a hybrid of a medical office and a gym."** Reception + admin desk up front; behind it, an **open, gym-like floorplan** — the **edges** hold exam/assessment areas, the **middle** holds workout and therapy equipment for the "in-practice" part of the session. Much of the assessment happens **on the PT floor or in a small bay**, not in enclosed exam rooms.
- **Sessions run long, and patients often can't drive themselves** (assessment + supervised practice). That drives **larger waiting rooms** and **more parking**. Plan around **patient mobility** too — restroom location and the number of doors a patient must navigate matter.
- **Build-out cost is lower** than specialized medical (e.g., specialty dentistry) because it's largely open floor — **but** the therapist may still need a **large equipment budget.**
- **Imaging questions to ask up front:** do they need to **read scans/x-rays in their workspace** (computer screens or light boards), or a **separate room** for treatment-plan review? Do they **order follow-up scans** to track progress, and do they **outsource** imaging?
- **Referral proximity is a real site-selection driver:** PTs often want to be **near the surgery center or hospital that sends their referrals**, or near **labs that run x-rays/MRIs.** Nail this in the initial meeting so you search the right locations.
- **Sizing:** not specified on the portal — benchmark per deal if it matters.

---

## Case study on file — "Chiropractic Relocation" (Greystone Chiropractic, Birmingham AL, 2019; agent Richard Tidwell)
A busy Birmingham chiropractor near lease-end weighed **renew-and-expand vs. new rental vs. buy-and-build.** CARR researched all three, introduced **bank lenders and contractors**, and coordinated architects/review committees/developer. The client chose to **build new construction** (now owns practice + land), and **added ~1,200 SF to lease out to a tenant — a new income stream.** Two reusable lessons: (1) the buy-and-build option can win on **location + full customization**, not just rent savings; (2) the value the client felt most was the **concierge coordination** (vendor intros + managing the process) — this is exactly what `DNA/Network/vendor-intro-timeline.md` operationalizes. *Named-client testimonial — internal/marketing, don't repurpose as generic content.*

---

## Cross-cutting (Chiropractic vs. PT)
- **Both are lower-build-out** than dentistry/procedural medical → both give you a **low-TI / faster-occupancy** story to trade for rate or free rent.
- **Parking, two different shapes:** chiro = **high volume, short dwell** (15-min spaces are the clever ask); PT = **more spaces + longer dwell + mobility/driver considerations.**
- **Imaging:** chiro often has **in-house x-ray** (electrical + lead-lining); PT usually **reads or outsources** imaging (proximity to imaging/hospital matters more than in-house build).

---

## MEP specs — HVAC & electrical (merged 2026-07-31 from a sourced research pass; every LOI term is negotiable — these are defensible most-common asks, not policy)

**Chiropractic**
- HVAC: **standard office ratio (1 ton per 350–400 SF)** — adjustment bays add no meaningful load.
- Electrical: **200A single-phase adequate; the x-ray is the whole story.** Modern chiropractic **digital x-ray runs on single-phase 208–240V — 3-phase is NOT required** (generators run on either; Maven Imaging / PatientImage). Installer convention: a **dedicated 100–150A circuit** to the x-ray room with a disconnect ~60" AFF near the console — an allowance, not an NEC figure; size to the actual generator sheet. **Fallback with leverage:** a stored-energy (capacitor-discharge) generator runs on a **standard 110V/20A outlet** — a real option when the existing service is thin, and a counter to any over-scoped electrical demand.
- LOI row shape (with x-ray, ~1,500–3,000 SF): standard-office HVAC; 200A single-phase service with a dedicated 100A x-ray circuit + disconnect sized per the selected manufacturer's spec.

**Physical / occupational therapy**
- HVAC: treatment/exam side = standard office; **the gym floor wants ≈1 ton per 300–350 SF** (occupant exertion; no published PT-specific standard exists — extrapolated between office and the health-club 200–300 band, say so if challenged). **The code-backed number is ventilation: ASHRAE 62.1 / IMC give exercise/gym occupancy 20 cfm/person outdoor air — 4× the office rate.** Gulf Coast: exertion adds latent load; target ≤60% RH, don't rely on a sensible-sized package unit alone.
- Electrical: **200A single-phase adequate.** Real dedicated-circuit drivers: **in-house laundry** (washer 20A/120V + electric dryer 240V/30A, NEC-driven — most PT clinics run laundry), **hydrotherapy** (240V dedicated GFCI, ~50A class), e-stim/ultrasound are wall-plug loads.
- **Salt / halotherapy room (live C-112 lesson):** a **tenant-side build item, never a landlord HVAC delivery ask** — it wants its own isolated mini-split or dedicated exhaust, NO shared return air (salt aerosol corrodes coils/ducts), supply-only inside the room, ~4 air-changes post-session, ≤50% RH, salt-resistant fixtures (Salt Room Builders / Salt Chamber / Salt Therapy Association). Landlord delivers base capacity + chase access; tenant installs the isolation.
- LOI row shape (~1,500–3,000 SF): HVAC minimum 1 ton per 350 SF with ASHRAE 62.1 exercise-rate ventilation for the designated gym area; 200A single-phase 120/240V service.

**Counseling / mental health / office-like medical (baseline):** standard office everything — 1 ton per 350–400 SF, 200A single-phase. (NYS-OMH's specialized psych-facility HVAC guide applies to licensed inpatient settings only; don't import it into an outpatient LOI.)

**Fitness / small-group wellness (heaviest HVAC in this family):** HIIT/cardio formats spec toward **1 ton per 200 SF** (Orangetheory's published build spec); moderate studios **~250–300 SF/ton**; same 20 cfm/person code ventilation. **The point to force with a landlord's engineer on the Gulf Coast: fitness loads run sensible-heat-factor 0.25–0.45 — a standard office RTU will hit temperature and stay clammy** unless the unit or supplemental dehumidification is selected for high-latent load (Henderson Engineers). Electrical 200A single-phase unless sauna/cold-plunge (240V dedicated, 30–50A) or treadmill banks stack.

*[stamp: Joe's brain, Claude Code (Fable seat), 2026-07-31 — MEP research merge; key sources named inline]*

## Content potential (handle with care)
Good owner-useful education fuel: the chiropractic **"visits are under 15 minutes, so parking is about turnover"** insight (and the 15-minute-space trick), and the PT **"medical office meets gym"** layout concept. **NOT content:** the internal **low-TI landlord-narrative** framing (that's negotiation posture) and the **named case-study testimonial** (client-specific/marketing). No proprietary floorplans were attached to these two pages.

## Sources (training.carr.us — internal portal, login-gated; captured Jul 7, 2026)
- /sessions/chiropractic/ · /sessions/physical-therapy/ · /sessions/case-study-alabama-chiropractic/ (case study)
- Dashboard: /dashboard/otherhealthcare (tiles render client-side; the underlying pages were pulled via the portal's own `wp-json/wp/v2/sessions?verticals=` API — see the memory note on portal structure).
- Portal vertical taxonomy (full branch map, Jul 7 2026): All Healthcare 247 · Dental 102 · Medical 89 · Veterinary 56 · Vision Care 47 · Chiropractic 12 · Therapy 7. Dental/Medical/Vet/Vision/Chiro/Therapy all now captured in DNA/Reference/.
