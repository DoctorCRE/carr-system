// Define the market research workflow.
export const meta = {
  name: 'market-research',
  description: 'Cited market-research graph: decompose question, parallel source sweep, adversarial verify, synthesize a DNA/Research-format report',
  whenToUse: 'Market pulls for the territory (vacancy, rents, construction, healthcare-sector moves) or any research question feeding prospecting/content. Local Claude Code sessions.',
  phases: [
    { title: 'Scope', detail: 'decompose the question into distinct search angles' },
    { title: 'Sweep', detail: 'one researcher per angle, blind to the others (T2 Sonnet)', model: 'sonnet' },
    { title: 'Verify', detail: 'adversarial fresh-context check on every load-bearing claim (top seat)' },
    { title: 'Synthesize', detail: 'DNA/Research house format: findings + So-what + re-engagement ammunition' },
  ],
}

const ARGS = (() => { try { return typeof args === 'string' ? JSON.parse(args) : (args || {}) } catch { return {} } })()

// Graph-engineering build 2026-07-25. Rules honored:
//  - verifiers get fresh context and try to REFUTE (fan-out hygiene rule 1)
//  - scoped: angles capped at 6, claims verified with single skeptics; widen only on Joe's ask
//  - output follows the house format of DNA/Research/2026-07-06-panhandle-healthcare-cre-snapshot.md:
//    national context -> per-area cited findings -> "So what" per section -> re-engagement ammunition
//    -> next-pull candidates. The workflow returns markdown; the INVOKING session (top seat) writes
//    the dated file into DNA/Research/ (shared tier), cross-refs prospect files, and stamps.
//  - writing surfaces obey DNA/writing-rules.md; the synthesize prompt says so explicitly.
// args: { question: string (required), today: 'YYYY-MM-DD', maxAngles?: number }

const TODAY = (ARGS && ARGS.today) || 'UNDATED-pass-args.today'
const QUESTION = ARGS && ARGS.question
if (!QUESTION) throw new Error('pass args.question — e.g. {question: "medical office vacancy and rent trend, Pensacola metro", today: "2026-07-25"}')
const MAX_ANGLES = Math.min((ARGS && ARGS.maxAngles) || 5, 6)

const ANGLES = { type: 'object', required: ['territory', 'angles'], properties: {
  territory: { type: 'string', description: 'exact current market list from carr-profile#our-territory' },
  angles: { type: 'array', maxItems: 6, items: {
  type: 'object', required: ['name', 'searchBrief'], properties: {
    name: { type: 'string' }, searchBrief: { type: 'string', description: 'self-contained research brief for one agent: what to find, where to look, what counts as a source' } } } } } }

const FINDINGS = { type: 'object', required: ['findings'], properties: { findings: { type: 'array', items: {
  type: 'object', required: ['claim', 'sourceUrl', 'sourceName'], properties: {
    claim: { type: 'string' }, detail: { type: 'string' },
    sourceUrl: { type: 'string' }, sourceName: { type: 'string' }, sourceDate: { type: 'string' },
    loadBearing: { type: 'boolean', description: 'true if the report leans on this claim (numbers, trends, named moves)' } } } } } }

const VERDICTS = { type: 'object', required: ['verdicts'], properties: { verdicts: { type: 'array', items: {
  type: 'object', required: ['claim', 'survives', 'reason'], properties: {
    claim: { type: 'string', description: 'the claim EXACTLY as given in the numbered list (verbatim echo — used to match results back)' },
    survives: { type: 'boolean' }, reason: { type: 'string' },
    correction: { type: 'string', description: 'the accurate version, if the claim is off' } } } } } }

phase('Scope')
const scoped = await agent(
  `Call read-doctrine with {document: "carr-profile"}. Select the current section whose key is "our-territory" and copy its market list exactly into territory. Then decompose this market-research question into at most ${MAX_ANGLES} DISTINCT search angles that together cover it without overlap: "${QUESTION}". Context: healthcare commercial real estate, tenant/buyer side. Typical angle families: hard market data (vacancy/rents/absorption), construction + permits, healthcare-sector moves (openings, M&A, DSO activity), macro (rates, healthcare economics), local news. Each angle gets a self-contained brief.`,
  { label: 'scope:angles', schema: ANGLES })

phase('Sweep')
const swept = await parallel(scoped.angles.slice(0, MAX_ANGLES).map(a => () => agent(
  `Research ONE angle of: "${QUESTION}". Market territory from carr-profile#our-territory: ${scoped.territory}. Your angle: ${a.name}. Brief: ${a.searchBrief}. Search the live web; prefer primary/named sources (government data, CoStar-adjacent public reporting, business journals, permit portals) over aggregators. Every finding needs a real URL. Mark loadBearing=true on anything with a number, a trend direction, or a named market move. Report honestly if an angle is thin — an empty angle is a finding.`,
  { label: `sweep:${a.name.slice(0, 28)}`, phase: 'Sweep', model: 'sonnet', schema: FINDINGS })))

const all = swept.filter(Boolean).flatMap((s, i) => (s.findings || []).map(f => ({ ...f, angle: scoped.angles[i]?.name || `angle ${i + 1}` })))
const loadBearing = all.filter(f => f.loadBearing)
log(`sweep: ${all.length} findings, ${loadBearing.length} load-bearing -> adversarial verify`)

phase('Verify')
// Jul 25 token-efficiency pass: source-support checks are mechanical existence
// verification — batched 4 claims per agent on T2 Sonnet per the tiering amendment
// (00_Context/model-tiering.md). Fresh context vs the researchers is what buys
// independence; refute-by-default posture unchanged.
const VB = 4
const vbatches = []
for (let i = 0; i < loadBearing.length; i += VB) vbatches.push(loadBearing.slice(i, i + VB))
const vresults = await parallel(vbatches.map((b, bi) => () => agent(
  `Adversarial verification, fresh context — you have not seen the researchers' work and your job is to REFUTE each claim below if you can. For EACH numbered claim: open its cited source; cross-check against one independent source when the number matters. Default to survives=false when the source does not clearly support the claim; give a correction when you find the accurate version. Echo each claim verbatim.\n${b.map((f, i) => `${i + 1}. "${f.claim}" (detail: ${(f.detail || 'none').slice(0, 200)}) — cited to ${f.sourceName} at ${f.sourceUrl}`).join('\n')}`,
  { label: `verify:batch${bi + 1}`, phase: 'Verify', model: 'sonnet', schema: VERDICTS })))
const vmap = new Map()
for (const r of vresults.filter(Boolean))
  for (const v of (r.verdicts || [])) vmap.set((v.claim || '').toLowerCase().trim().slice(0, 80), v)

const kept = []
for (const f of all) {
  const v = vmap.get((f.claim || '').toLowerCase().trim().slice(0, 80))
  if (!f.loadBearing) kept.push({ ...f, verified: 'not-load-bearing' })
  else if (v && v.survives) kept.push({ ...f, verified: 'CONFIRMED' })
  else if (v && v.correction) kept.push({ ...f, claim: v.correction, verified: 'CORRECTED', note: v.reason })
  else if (!v) kept.push({ ...f, verified: 'UNMATCHED-VERDICT (kept, flagged — echo mismatch)' })
  // refuted with no correction: dropped, and the drop is logged, not silent
}
const dropped = loadBearing.length - kept.filter(k => k.verified !== 'not-load-bearing').length
if (dropped > 0) log(`verify: ${dropped} claim(s) refuted and dropped`)

phase('Synthesize')
const report = await agent(
  `Write the ${TODAY} research pull answering: "${QUESTION}", from ONLY these verified findings (never add facts of your own):\n${JSON.stringify(kept, null, 1)}\nHouse format (mirror DNA/Research/2026-07-06-panhandle-healthcare-cre-snapshot.md): brief national/context frame -> per-area or per-theme findings as cited bullets (inline source name + URL) -> a "So what" takeaway per section tying it to prospecting or content for a healthcare-CRE tenant/buyer advisor -> a "Re-engagement ammunition" section (which KINDS of prospects this data supports approaching; no invented names) -> "Next-pull candidates". Style: DNA/writing-rules.md applies — no em-dashes, no AI-tell vocabulary, no stacked parallel sentence skeletons. Return pure markdown ready to save as a dated file.`,
  { label: 'synthesize:report' })

return { date: TODAY, question: QUESTION, territory: scoped.territory, angles: scoped.angles, findingCount: all.length,
  confirmed: kept.filter(k => k.verified === 'CONFIRMED').length, dropped, report,
  writeBackReminder: `Top seat: save as DNA/Research/${TODAY}-<slug>.md, cross-ref any relevant prospect files + clients-active.md, feed content-worthy items to the substance bank, stamp.` }
