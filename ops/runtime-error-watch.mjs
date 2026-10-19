import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const ENVIRONMENTS = new Set(['local', 'rehearsal', 'staging', 'production']);
const FAILURE_CLASSES = new Set(['http_5xx', 'verb_internal_error', 'actor_unresolved']);
const INCIDENT_REF = /^INC-[0-9]{8}-[0-9]+$/;
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

function key(ref, operation) {
  const hex = createHash('sha256').update(`carr-runtime-error/v1|${ref}|${operation}`).digest('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-8${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

async function callRecord(verb, args) {
  const { stdout } = await promisify(execFile)(fileURLToPath(new URL('../run.sh', import.meta.url)),
    ['call', verb, JSON.stringify(args)], { timeout: 60000, maxBuffer: 8 * 1024 * 1024 });
  const reply = JSON.parse(stdout);
  if (!reply || reply.ok === false || reply.error) throw new Error('record_call_refused');
  return reply;
}

export async function reconcileRuntimeErrors(call = callRecord) {
  const rows = [], cursors = new Set();
  let cursor, expected;
  do {
    const page = await call('unfinished-work', { kinds: 'incident', text: 'carr-mcp', limit: 500,
      ...(cursor ? { cursor } : {}) });
    const coverage = page.coverage?.filter(row => row.kind === 'incident');
    if (page.ok !== true || coverage?.length !== 1 || coverage[0].state !== 'complete' ||
        !Number.isSafeInteger(coverage[0].count_total) || !Array.isArray(page.items))
      throw new Error('runtime_incident_census_incomplete');
    expected ??= coverage[0].count_total;
    if (expected !== coverage[0].count_total) throw new Error('runtime_incident_census_changed');
    rows.push(...page.items);
    cursor = page.next_cursor;
    if (cursor && cursors.has(cursor)) throw new Error('runtime_incident_cursor_repeated');
    cursors.add(cursor);
  } while (cursor);
  if (rows.length !== expected || new Set(rows.map(row => row.id)).size !== expected)
    throw new Error('runtime_incident_census_short');

  let linked = 0;
  for (const row of rows) {
    if (row.kind !== 'incident' || !INCIDENT_REF.test(row.id)) throw new Error('runtime_incident_ref_invalid');
    const card = await call('get-incident', { ref: row.id, fact_limit: 1 });
    const incident = card.incident;
    if (!incident || !Array.isArray(card.links)) throw new Error('runtime_incident_card_incomplete');
    const failureClass = incident.fingerprint?.split('|').at(-1);
    if (incident.source_ref !== 'mcp-server/src/trace.js' || incident.detected_source !== 'carr-mcp-worker' ||
        !ENVIRONMENTS.has(incident.environment) || !FAILURE_CLASSES.has(failureClass) ||
        !incident.fingerprint.startsWith(`carr-mcp|${incident.environment}|`) ||
        ['resolved', 'reviewed'].includes(incident.state) || card.links.some(link => link.kind === 'work_request')) continue;
    if (incident.ref !== row.id) throw new Error('runtime_incident_identity_mismatch');
    const signal = { ref: incident.ref, environment: incident.environment, failure_class: failureClass };
    const finding = await call('record-finding', { idempotency_key: key(signal.ref, 'finding'),
      subject: 'repo:jbookout/carr-system', kind: 'runtime_error', internal: true,
      epistemic_status: 'observed', source: `incident:${signal.ref}`,
      value: { schema: 'carr.runtime-error.v1', ...signal } });
    if (!UUID.test(finding.flag_id)) throw new Error('runtime_finding_receipt_invalid');
    const request = await call('report-problem', { idempotency_key: key(signal.ref, 'work-request'),
      situation: 'DoctorCRE Worker runtime failure requires investigation, repair and regression verification.',
      title: `Worker runtime failure ${signal.ref} (${signal.environment})`,
      desired_outcome: `Address runtime finding ${finding.flag_id} from incident ${signal.ref} for DoctorCRE delivery. ` +
        'Record a verified fix or an evidence-backed disposition in this Work Request. Read the incident for diagnosis.',
      acceptance_criteria: [
        { id: 'AC-CAUSE', text: 'Identify the runtime failure cause and record a fix or an evidence-backed disposition.' },
        { id: 'AC-PROOF', text: 'Verify the affected behavior and a regression test using redacted evidence.' },
      ] });
    if (!/^WR-[0-9]{1,12}$/.test(request.human_ref)) throw new Error('runtime_work_request_receipt_invalid');
    const link = await call('link-incident-work-request', { idempotency_key: key(signal.ref, 'link'),
      incident_ref: signal.ref, work_request: request.human_ref });
    if (link.ok !== true || link.incident_ref !== signal.ref || link.work_request !== request.human_ref)
      throw new Error('runtime_link_receipt_invalid');
    linked += 1;
    if (linked === 10) break;
  }
  return { linked };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(await reconcileRuntimeErrors())); }
  catch { console.error('runtime-error-watch: reconciliation failed; durable incidents remain pending'); process.exitCode = 1; }
}
