export const meta = {
  name: 'lead-sweep',
  description: 'Multi-modal lead-sourcing sweep: parallel finders per signal lane, dedupe barrier, fresh-context verify, ALL leads presented',
  whenToUse: 'On-demand widening of lead sourcing beyond the coded feeds (Sunbiz/NPI/renewal radar stay T0 in the weekly run). Web-research lanes only. Local Claude Code sessions.',
  phases: [
    { title: 'Sweep', detail: 'one finder per lane x region, each blind to the others (T2 Sonnet)', model: 'sonnet' },
    { title: 'Dedupe', detail: 'cross-lane merge in plain code — an edge, not an agent' },
    { title: 'Verify', detail: 'fresh-context source check on each candidate (top seat)' },
    { title: 'Present', detail: 'score + tier every lead; nothing withheld' },
  ],
}

const ARGS = (() => { try { return typeof args === 'string' ? JSON.parse(args) : (args || {}) } catch { return {} } })()

// Graph-engineering build 2026-07-25. Standing rules honored:
//  - NEVER pre-qualify (Joe, Jul 16): every candidate that survives dedupe is PRESENTED with a
//    score/confidence tier. Verification here checks the lead EXISTS at its source, it does not
//    judge whether the lead is worth Joe's time. Weak-but-plausible => low score, still on the list.
//  - Fan-out hygiene: verifiers are fresh-context, judging the cited source; run starts SCOPED
//    (default 6 finder agents), widen only after a clean run.
//  - A lone name never merges records: see the Dedupe edge below.
//  - The workflow writes nothing. The invoking session (top seat) routes results to the
//    CARR Leads Inbox / registry intake per DNA/Leads/lead-system.md, stamps, and logs the run.
// args: { today: 'YYYY-MM-DD', lanes?: string[], regions?: string[], cap?: number }

const TODAY = (ARGS && ARGS.today) || 'UNDATED-pass-args.today'
const CAP = Math.min((ARGS && ARGS.cap) || 6, 20)

const LANES = (ARGS && ARGS.lanes) || [
  'local news + business journals: practice openings, expansions, relocations, new physician/dentist/vet arrivals announced in the last 60 days',
  'building permits + planning-commission agendas: healthcare build-outs, medical office construction filings',
  'hiring signals: job postings for office managers / associates / front desk at NEW or EXPANDING private practices (Indeed, practice sites, association job boards)',
  'practice-for-sale and transition listings: brokers, DSO acquisition news, retirement announcements (sellers create buyer-side and lease-event opportunities nearby)',
  'association + society announcements: new members, board changes, CE event hosts among private-practice owners',
  'university + residency pipelines: graduating residents/associates announcing moves into private practice in the territory',
]
const REGIONS = (ARGS && ARGS.regions) || [
  'Mobile and Baldwin County AL; Dothan AL',
  'Pensacola / Escambia + Santa Rosa County FL; Fort Walton / Destin / Okaloosa + Walton County FL',
  'Panama City / Bay County FL; Tallahassee / Leon County FL',
]

const FINDINGS = { type: 'object', required: ['leads'], properties: { leads: { type: 'array', items: {
  type: 'object', required: ['name', 'signal', 'sourceUrl', 'vertical', 'confidence'], properties: {
    name: { type: 'string', description: 'person or practice name, exactly as the source states it' },
    practice: { type: 'string' }, location: { type: 'string' },
    vertical: { type: 'string', description: 'dental/medical/vet/chiro/vision/therapy/fitness-wellness/other' },
    signal: { type: 'string', description: 'what was observed and why it implies a coming real-estate decision' },
    sourceUrl: { type: 'string' }, sourceDate: { type: 'string' },
    confidence: { type: 'string', enum: ['HIGH', 'MEDIUM', 'LOW'] } } } } } }

const CHECKS = { type: 'object', required: ['checks'], properties: { checks: { type: 'array', items: {
  type: 'object', required: ['item', 'name', 'exists', 'note'], properties: {
    item: { type: 'integer', description: 'the claim number from the numbered list' },
    name: { type: 'string', description: 'the lead name EXACTLY as given in the numbered list' },
    exists: { type: 'boolean', description: 'the cited source really says what the finder claims' },
    note: { type: 'string' },
    corroboration: { type: 'string', description: 'any second independent field found (address, license, entity filing)' } } } } } }

// scoped: pair lanes with regions round-robin up to CAP finder agents this run
const jobs = []
for (let i = 0; i < LANES.length && jobs.length < CAP; i++)
  for (let r = 0; r < REGIONS.length && jobs.length < CAP; r++)
    if ((i + r) % Math.max(1, Math.ceil((LANES.length * REGIONS.length) / CAP)) === 0 || LANES.length * REGIONS.length <= CAP)
      jobs.push({ lane: LANES[i], region: REGIONS[r] })

phase('Sweep')
log(`lead-sweep ${TODAY}: ${jobs.length} finder agents (scoped cap ${CAP}) — widen only after a clean run`)
const swept = await parallel(jobs.map((j, idx) => () => agent(
  `You are ONE lane of a healthcare-practice lead sweep for a commercial real estate advisor (tenant/buyer representation only) covering: ${j.region}. Your lane, and ONLY your lane: ${j.lane}. Search the live web. Target PRIVATE practices (dental, medical, vet, chiro, vision, therapy, fitness/wellness) — never hospitals or corporate/institutional systems. Return every candidate you find with a real source URL; do NOT filter for quality — grade confidence instead (HIGH/MEDIUM/LOW) and include weak-but-plausible finds at LOW. Empty lanes return an empty list honestly.`,
  { label: `sweep:${idx + 1}`, phase: 'Sweep', model: 'sonnet', schema: FINDINGS })))

// Dedupe is an edge, not an agent (WR-AI-008): same normalized name, at least one shared
// corroborating field (practice or whole location), and no corroborating field that differs.
const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const FIELDS = ['practice', 'location']
const groups = []
for (const batch of swept.filter(Boolean))
  for (const l of (batch.leads || [])) {
    const id = { name: norm(l.name) }
    for (const f of FIELDS) id[f] = norm(l[f])
    const g = groups.find(g => g.id.name === id.name &&
      FIELDS.some(f => id[f] && id[f] === g.id[f]) &&
      FIELDS.every(f => !id[f] || !g.id[f] || id[f] === g.id[f]))
    if (!g) { groups.push({ id, lead: l }); continue }
    for (const f of FIELDS) g.id[f] ||= id[f]
    g.lead.signal += ` | ALSO: ${l.signal} (${l.sourceUrl})`
    if (l.confidence === 'HIGH') g.lead.confidence = 'HIGH'
  }
const candidates = groups.map(g => g.lead)
log(`dedupe: ${candidates.length} unique candidates from ${swept.filter(Boolean).length} lanes`)

phase('Verify')
// Jul 25 token-efficiency pass: mechanical existence checks run BATCHED (5 leads per
// agent — one context setup instead of five) and on T2 Sonnet per the verification-
// tiering amendment (00_Context/model-tiering.md): independence comes from fresh
// context and a different instance than the finder, not from model tier. Ambiguity
// is reported honestly in the note, never guessed; judgment stays with Joe.
const BATCH = 5
const batches = []
for (let i = 0; i < candidates.length; i += BATCH) batches.push(candidates.slice(i, i + BATCH))
const checked = await parallel(batches.map((b, bi) => () => agent(
  `Fresh-context source checks — you have not seen any finder's reasoning. For EACH numbered claim below: open the cited source (plus one cheap corroborating search if warranted) and report whether it actually supports the claim. You are checking EXISTENCE and accuracy only — never whether a lead is "good enough"; qualification belongs to Joe on the board. Unreachable or ambiguous source = say so in the note, do not guess. Echo each claim number and name exactly.\n${b.map((l, i) => `${i + 1}. "${l.name}"${l.practice ? ` (${l.practice})` : ''} — ${l.signal.slice(0, 300)} — source: ${l.sourceUrl}`).join('\n')}`,
  { label: `verify:batch${bi + 1}`, phase: 'Verify', model: 'sonnet', schema: CHECKS })))
// Join by batch + claim number, never by name: same-name leads at different locations
// stay separate after Dedupe and must keep their own check (WR-000513).
const byLead = new Map()
checked.forEach((c, bi) => {
  for (const k of (c?.checks || [])) if (batches[bi][k.item - 1]) byLead.set(batches[bi][k.item - 1], k)
})
const verified = candidates.map(l => ({ ...l, check: byLead.get(l) || null }))

phase('Present')
const out = verified.filter(Boolean).map(l => ({
  ...l,
  tier: !l.check?.exists ? 'UNVERIFIED (present anyway, flag)' :
    l.check.corroboration ? 'CORROBORATED' : 'SINGLE-SOURCE',
}))
log(`lead-sweep ${TODAY}: presenting ALL ${out.length} leads (never pre-qualify)`)
return { date: TODAY, lanesRun: jobs, leads: out,
  writeBackReminder: 'Top seat: route rows to the registry intake per DNA/Leads/lead-system.md (Intake Log entry, stamps), then rebuild the board (run.sh lead-board). No lead dropped.' }
