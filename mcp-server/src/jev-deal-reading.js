import {cachedSemanticAsk} from "./jev-semantic.js";
// Bounded, read-only Jev advisory for one authenticated Deal Room record.
// The record is fetched by the caller through the existing get-deal-room verb.
// No model answer changes a deal, its ordering, or a partner's next action.
export const JEV_DEAL_EVIDENCE_FLOOR = 200;
const LEVELS = [
  "A client and space requirement are recorded, but no specific property is identified.",
  "Specific properties are being researched, toured, or compared; no offer is recorded.",
  "An offer or proposal was sent; the other side's response is outstanding.",
  "The parties exchanged a counter and are negotiating specific economic terms.",
  "Economic terms are settled; drafting, review, or signature remains.",
];
const WAITING = {
  client: "The tenant or buyer client owes the next decision, reply, approval, or information.",
  counterparty: "The landlord, seller, or their representative owes the next move.",
  carr: "The CARR agent owes the next research, assembly, send, or scheduling step.",
  market: "No acceptable property exists; the transaction awaits inventory.",
  third_party: "A lender, architect, contractor, franchisor, or authority owes the next move.",
  client_timing: "Nothing is blocked; the client's planned time has not arrived.",
  not_recorded: "The record does not establish who owes the next move.",
};

function clip(value, max = 1200) {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

function items(value) {
  return Array.isArray(value) ? value : [];
}

function usefulText(value) {
  const words = clip(value).toLowerCase().match(/[a-z0-9]{3,}/g) || [];
  return new Set(words).size >= 5;
}

// Record deadlines are DATE fields, interpreted on the CARR business calendar.
function calendarDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value ? value : null;
}

function businessToday(now) {
  return new Intl.DateTimeFormat("en-CA", {timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit"}).format(now);
}

function activeCriticalDates(record, now) {
  const current = now.valueOf();
  const distance = date => {
    const parsed = Date.parse(date?.due_on || "");
    return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : Math.abs(parsed - current);
  };
  return items(record.critical_dates)
    .filter(d => !["completed", "cancelled", "canceled", "satisfied"].includes(d.status))
    .sort((a, b) => distance(a) - distance(b))
    .slice(0, 4);
}

// Keep current operational facts ahead of older narrative. A single long note
// cannot fill the input or establish enough evidence for all three judgments.
function evidenceLines(record, now) {
  const sources = [
    ["next_action", items(record.next_actions).filter(a => a.status === "open" || !a.status)
      .slice(0, 4).map(a => clip(a.description, 500))],
    ["negotiation", items(record.negotiation_rounds).slice(0, 3).map(n => clip(n.note, 500))],
    ["critical_date", activeCriticalDates(record, now).map(d => clip(d.note, 500))],
    ["activity", items(record.activities).slice(0, 6)
      .map(a => [clip(a.summary, 250), clip(a.detail, 350)].filter(Boolean).join(" "))],
    ["note", items(record.thread).slice(0, 8).map(n => clip(n.text, 500))],
  ];
  const lines = [];
  const kinds = new Set();
  for (const [kind, values] of sources) {
    for (const value of values) {
      if (!value || lines.join("\n").length + value.length > 6000) continue;
      lines.push(`${kind}: ${value}`);
      if (usefulText(value)) kinds.add(kind);
    }
  }
  return { lines, kinds };
}

export function dealReadingState(record, now = new Date()) {
  const today = businessToday(now);
  const expired = value => calendarDate(value) === null ? null : value < today;
  const { lines, kinds } = evidenceLines(record, now);
  const nextStep = clip(record.next_step);
  if (usefulText(nextStep)) kinds.add("next_step");
  const evidenceChars = nextStep.length + lines.reduce((sum, line) => sum + line.length, 0);
  const lastTouch = record.last_touch ? new Date(record.last_touch) : null;
  const daysQuiet = lastTouch && !Number.isNaN(lastTouch.valueOf())
    ? Math.max(0, Math.floor((now.valueOf() - lastTouch.valueOf()) / 86400000)) : null;
  const hasTransactionAnchor = items(record.premises).length > 0 ||
    items(record.negotiation_rounds).length > 0 ||
    /\b(offer|counter|proposal|lease|purchase|property|space|tour|site|landlord|seller|tenant|buyer|renewal|loi)\b/i
      .test([nextStep, ...lines].join(" "));
  const hasNextMove = usefulText(nextStep) || kinds.has("next_action") ||
    /\b(waiting|reply|respond|decide|approve|send|schedule|owe|needs? to|must)\b/i
      .test([nextStep, ...lines].join(" "));
  const negotiations = items(record.negotiation_rounds);
  const premises = items(record.premises);
  const criticalDates = activeCriticalDates(record, now);
  const sentDocuments = items(record.documents).filter(d => d.sent_status === "sent").length;
  const narrativeSufficient = evidenceChars >= JEV_DEAL_EVIDENCE_FLOOR &&
    kinds.size >= 2 && hasTransactionAnchor && hasNextMove;
  const structuredSufficient = evidenceChars >= 80 && usefulText(nextStep) &&
    (negotiations.length > 0 || sentDocuments > 0 || (premises.length > 0 && criticalDates.length > 0));
  const sufficient = narrativeSufficient || structuredSufficient;
  return {
    evidenceChars,
    sufficient,
    state: {
      deal: {
        recorded_phase: record.phase || null,
        transaction_type: record.type || null,
        next_step_on_file: nextStep || null,
        history: lines,
        premises_recorded: premises.length,
        negotiation_rounds_recorded: negotiations.length,
        latest_negotiation: negotiations.length ? {
          round_no: Number.isInteger(negotiations[0].round_no) ? negotiations[0].round_no : null,
          side: clip(negotiations[0].side, 40) || null,
          proposed_on: clip(negotiations[0].proposed_on, 24) || null,
          expired: expired(negotiations[0].expires_on),
        } : null,
        active_dates: criticalDates.map(d => ({
          kind: clip(d.kind, 40) || null,
          due_relative_to_today: expired(d.due_on) === null ? "unknown" : expired(d.due_on) ? "past" : "future_or_today",
          status: clip(d.status, 40) || null,
        })),
        sent_documents_recorded: sentDocuments,
        days_since_last_recorded_touch: daysQuiet,
      },
    },
  };
}

export function dealReadingQuestions() {
  return {
    movement: { type: "score", instructions: "Judge how far this commercial real estate transaction has actually moved toward a signed agreement from the recorded evidence. Do not infer movement from the hand-maintained phase label alone.", criteria: LEVELS },
    waiting_on: { type: "choice", instructions: "Who or what owes the very next move according to the recorded evidence? Choose not_recorded if the record does not establish this.", criteria: WAITING },
    silence_is_bad: { type: "noul", instructions: "Given the recorded next step and the days since last touch, is the silence evidence that the transaction is in trouble? An unknown last touch is not evidence of trouble.", criteria: { true: "The wait exceeds what the recorded step normally takes and nothing explains the gap.", false: "The wait fits the recorded step, a future date, or the client's planned timeline." } },
  };
}

function probability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

export async function readDealWithJev(record, { askJev, now = new Date() } = {}) {
  const { evidenceChars, sufficient, state } = dealReadingState(record, now);
  const base = { schema: "carr.jev-deal-reading.v1", advisory_only: true,
    evidence_chars: evidenceChars, evidence_floor: JEV_DEAL_EVIDENCE_FLOOR };
  if (!sufficient)
    return { ...base, judged: false, reason: "insufficient_recorded_evidence" };
  if (typeof askJev !== "function") return { ...base, judged: false, reason: "jev_unavailable" };
  try {
    const result = await cachedSemanticAsk(askJev, { model: "jev-1.13.0", state, questions: dealReadingQuestions() }, "deal-reading-v1");
    const answers = result?.answers;
    const movement = answers?.movement?.score;
    const waiting = answers?.waiting_on?.choice;
    const silence = answers?.silence_is_bad?.noul;
    if (typeof movement !== "number" || !Number.isFinite(movement) || movement < 0 || movement > 4 ||
        !Object.hasOwn(WAITING, waiting) || !probability(silence))
      return { ...base, judged: false, reason: "invalid_jev_answer" };
    const rung = Math.round(movement);
    return { ...base, judged: true, reason: null, model: result.model || "unknown",
      movement_score: movement, movement_rung: rung + 1, movement_rungs: LEVELS.length,
      movement_label: LEVELS[rung], waiting_on: waiting,
      waiting_on_confidence: probability(answers.waiting_on.confidence) ? answers.waiting_on.confidence : null,
      silence_is_bad: silence };
  } catch (error) {
    return { ...base, judged: false, reason: /semantic answer|resolved model/.test(error?.message ?? "") ? "invalid_jev_answer" : "jev_unavailable" };
  }
}
