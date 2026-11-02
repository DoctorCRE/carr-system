import { dealExtractionTools } from '../src/deal-extraction.ts';
import type { Actor, Db, VerbInputs } from '../src/verb-contract.js';

declare const db: Db;
declare const actor: Actor;
declare const record: VerbInputs['record-deal-extraction'];
const tools = dealExtractionTools();
const source = {
  idempotency_key: '00000000-0000-4000-8000-000000000001',
  entity_ref: '00000000-0000-4000-8000-000000000002',
  document_ref: '00000000-0000-4000-8000-000000000003',
  document_type: 'loi', model: 'haiku', extractor_version: 'deal-text.v1',
} satisfies VerbInputs['read-deal-extraction-source'];

tools['read-deal-extraction-source'].handler(db, actor, source);
tools['record-deal-extraction'].handler(db, actor, record);

// @ts-expect-error The source contract requires a stored document reference.
tools['read-deal-extraction-source'].handler(db, actor, { idempotency_key: source.idempotency_key });
// @ts-expect-error Extraction accepts only registered models.
tools['read-deal-extraction-source'].handler(db, actor, { ...source, model: 'unregistered' });
// @ts-expect-error Recording requires a receipt and observations.
tools['record-deal-extraction'].handler(db, actor, source);
// @ts-expect-error Extraction receipts cannot claim canonical promotion.
tools['record-deal-extraction'].handler(db, actor, { ...record, receipt: { ...record.receipt, canonical: true } });
// @ts-expect-error Extraction pass receipts require both pass digests.
tools['record-deal-extraction'].handler(db, actor, { ...record, receipt: { ...record.receipt, passes: [] } });
// @ts-expect-error The database dependency must implement query.
tools['read-deal-extraction-source'].handler({}, actor, source);
// @ts-expect-error An authenticated actor requires an id.
tools['read-deal-extraction-source'].handler(db, {}, source);
