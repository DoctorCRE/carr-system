// Define the system health audit workflow.
export const meta = {
  name: 'health-audit',
  description: 'Monthly system health audit: 12 report-card categories graded in parallel, fresh-context verified, merged',
  whenToUse: 'Monthly audit (the 4th) or an on-demand re-score. Local Claude Code sessions only. Source of truth for the grading rubric stays 00_Context/system-report-card-2026-07-07.md; this graph runs its v3 prompt wider, not differently.',
  phases: [
    { title: 'Measure', detail: 'FINAL-baseline metrics re-measured (T1 Haiku)', model: 'haiku' },
    { title: 'Grade', detail: 'one grader per category, independent, no anchoring (T2 Sonnet)', model: 'sonnet' },
    { title: 'Verify', detail: 'fresh-context skeptic per grade — refute or confirm (top seat)' },
    { title: 'Synthesize', detail: 'merge, overall score, top-5 corrective actions' },
  ],
}

const ARGS = (() => { try { return typeof args === 'string' ? JSON.parse(args) : (args || {}) } catch { return {} } })()

// Graph-engineering build 2026-07-25. Design rules honored here:
//  - graders never see each other or prior scores (the no-anchoring rule, now structural)
//  - verifiers get FRESH context and judge evidence, not the grader's claim (fan-out hygiene rule 1)
//  - scoped: 12 + 12 + 2 agents, known cost shape, no unbounded loops (fan-out hygiene rule 2)
//  - the workflow WRITES NOTHING. Score-history columns, decision-history, open-loops are
//    top-seat writes the invoking session performs after reading this result (model-tiering hard floor).
// args: { vault: string (CARR AI absolute path), today: 'YYYY-MM-DD' } — timestamps come in via args.

// PHASE 4 GATE (2026-08-22). This workflow reads the legacy Drive vault and has
// no canonical mode. It used to fall back to a hardcoded Drive root when no
// vault argument arrived, which is the ambient Drive selection Phase 4 removes
// -- and it meant a caller who forgot the argument got a silent read of Joe's
// real vault instead of an error. The root is now required and never guessed.
const VAULT = (ARGS && ARGS.vault)
if (!VAULT) {
  throw new Error(
    'MISSING_CANONICAL_SEAM: record-native system report card. This workflow reads ' +
    'the legacy Drive vault and has no canonical mode, so it will not guess a root. ' +
    'Pass args.vault with the absolute vault path to run it as an acknowledged ' +
    'recovery exercise.')
}
const TODAY = (ARGS && ARGS.today) || 'UNDATED-pass-args.today'
const CARD = `${VAULT}/00_Context/system-report-card-2026-07-07.md`

const CATEGORIES = [
  'Knowledge capture & ingestion', 'Architecture & information design',
  'Automation reliability & safety', 'Governance & self-maintenance',
  'Token efficiency', 'Content & marketing system', 'Deal execution capability',
  'Vendor/referral network', 'Team scalability (Dell)', 'Data quality',
  'Pipeline & lead generation', 'Proven business outcomes',
]

const GRADE = { type: 'object', required: ['category', 'score', 'evidence', 'gaps'], properties: {
  category: { type: 'string' }, score: { type: 'number', minimum: 0, maximum: 100 },
  evidence: { type: 'array', items: { type: 'string' }, description: 'concrete observations with file paths / counts backing the score' },
  gaps: { type: 'array', items: { type: 'string' } } } }

const VERDICT = { type: 'object', required: ['upheld', 'adjustedScore', 'reason'], properties: {
  upheld: { type: 'boolean' },
  adjustedScore: { type: 'number', minimum: 0, maximum: 100, description: 'YOUR final 0-100 score for the category as a number IN THIS FIELD — equal to the grader score if upheld. Never deliver your score only inside the reason text.' },
  reason: { type: 'string' } } }

const RETRIEVE_HINT = 'To locate evidence cheaply, FIRST run in Bash: ~/carr-system/run.sh retrieve "<your search terms>" — it scores the vault section index and returns exact file + line-range reads. Open only what it returns; fall back to the rubric pointers on no hits.'

phase('Measure')
const metrics = await agent(
  `Re-measure the FINAL-baseline metrics for the CARR AI monthly audit. Read the "monthly audit prompt" and FINAL metrics table in "${CARD}" and re-measure each metric with the SAME method it names (file sizes, row counts from the named xlsx/md files under "${VAULT}", ledger counts). Measure, don't estimate; report "unmeasurable: <why>" honestly where a method needs something this environment lacks. Return one line per metric: name | method | value.`,
  { label: 'measure:baselines', model: 'haiku', effort: 'low' })

phase('Grade')
const graded0 = await parallel(CATEGORIES.map(cat => () => agent(
  `You are grading ONE category of the CARR AI system report card, independently — you have NO access to prior scores and must not guess them. Category: "${cat}". Read the rubric for this category in "${CARD}" (and ONLY what that rubric points you to under "${VAULT}"). ${RETRIEVE_HINT} Fresh measurements you may trust:\n${metrics}\nGrade 0-100 with concrete evidence.`,
  { label: `grade:${cat}`, phase: 'Grade', model: 'sonnet', schema: GRADE })))

// Selective verification (Jul 25 token-efficiency pass): fresh-context T3 verifiers
// check the EXTREMES — the 3 highest and 3 lowest grades, the ones most likely to be
// wrong and most able to move the verdict. Mid-range grades pass through labeled
// unverified (judgment verification stays T3 per the tiering amendment; the barrier
// here is justified — selection needs the whole grade set).
const graded1 = graded0.filter(Boolean)
const rankedCats = [...graded1].sort((a, b) => b.score - a.score).map(g => g.category)
const toVerify = new Set([...rankedCats.slice(0, 3), ...rankedCats.slice(-3)])
const graded = await parallel(graded1.map(g => () => !toVerify.has(g.category)
  ? Promise.resolve({ ...g, verdict: null })
  : agent(
    `Fresh-context audit verification. A grader scored the CARR AI category "${g.category}" at ${g.score}/100 citing: ${JSON.stringify(g.evidence)}. You have NOT seen its reasoning and owe it nothing. Independently spot-check the citations against the real files under "${VAULT}" (rubric: "${CARD}"). ${RETRIEVE_HINT} Refute the score if evidence is wrong, stale, or cherry-picked; uphold only what checks out. Put your final number in adjustedScore either way.`,
    { label: `verify:${g.category}`, phase: 'Verify', schema: VERDICT }
  ).then(v => ({ ...g, verdict: v }))))

phase('Synthesize')
const clean = graded.filter(Boolean)
const rows = clean.map(g => ({
  category: g.category,
  score: g.verdict && !g.verdict.upheld ? g.verdict.adjustedScore : g.score,
  graderScore: g.score, upheld: g.verdict ? g.verdict.upheld : null,
  verifierReason: g.verdict ? g.verdict.reason : 'mid-range grade — not selected for verification (extremes-only policy, Jul 25)',
  evidence: g.evidence, gaps: g.gaps,
}))
const summary = await agent(
  `Synthesize the ${TODAY} CARR AI health-audit verdict from these verified category grades:\n${JSON.stringify(rows, null, 1)}\nReturn: overall score (judgment aggregate, per the report card's method), the top 5 corrective actions ranked by leverage (each tied to a category gap), and one plain sentence on whether the system is improving or idling. No writes — the invoking session records results.`,
  { label: 'synthesize:verdict' })

log(`health-audit ${TODAY}: ${rows.length}/12 categories graded+verified`)
return { date: TODAY, metrics, rows, summary,
  writeBackReminder: 'Top seat now: append dated column to score-history + FINAL metrics tables, decision-history entry, open 🔔 loops (audit-task.md rules).' }
