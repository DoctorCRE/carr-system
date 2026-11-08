import { Validator } from '@cfworker/json-schema';

export const subjectWatchPolicySchema = {
  type: 'object', additionalProperties: false,
  properties: {
    change_kinds: { type: 'array', minItems: 1, maxItems: 3, uniqueItems: true,
      items: { type: 'string', enum: ['reply_arrived', 'lease_threshold_crossed', 'listing_status_changed'] } },
    include_self_changes: { type: 'boolean', default: true },
    lease_threshold_days: { type: 'array', minItems: 1, maxItems: 20, uniqueItems: true,
      items: { type: 'integer', minimum: 0, maximum: 3650 } },
  }, required: ['change_kinds'],
};


const uuid = { type: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' };
const watch = {
  type: 'object', additionalProperties: false,
  required: ['id','actor_id','subject_type','subject_id','state','policy','lifecycle_version','baseline','subscribed_at'],
  properties: {
    id: uuid, actor_id: uuid, subject_id: uuid,
    subject_type: { enum: ['deal','lease','listing'] },
    state: { enum: ['watching','paused','removed'] },
    policy: { ...subjectWatchPolicySchema, required: ['change_kinds','include_self_changes'] },
    lifecycle_version: { type: 'integer', minimum: 1 },
    baseline: { type: 'object', additionalProperties: false,
      required: ['schema_version','subject_type','subject_id','version'],
      properties: { schema_version: { const: 'subject-watch-baseline.v1' },
        subject_type: { enum: ['deal','lease','listing'] }, subject_id: uuid, version: { type: 'integer', minimum: 1 } } },
    subscribed_at: { type: 'string', format: 'date-time' },
  },
};
export const subjectWatchV2ResponseSchema = {
  type: 'object', required: ['ok','schema_version'],
  properties: {
    ok: { const: true }, schema_version: { const: 'subject-watch.v2' },
    watch: { anyOf: [watch,{type:'null'}] },
    watches: { type: 'array', maxItems: 100, items: watch },
    rows_changed: { type: 'integer', minimum: 0, maximum: 1 },
    has_more: { type: 'boolean' }, next_cursor: { anyOf: [uuid,{type:'null'}] },
  },
  oneOf: [{required:['watch']},{required:['watches','has_more','next_cursor']}],
};
const policyValidator = new Validator(subjectWatchPolicySchema);
export function validSubjectWatchPolicy(policy) {
  return policyValidator.validate(policy).valid
    && (!policy.change_kinds.includes('lease_threshold_crossed') || Array.isArray(policy.lease_threshold_days));
}
const response = new Validator(subjectWatchV2ResponseSchema);
export function parseSubjectWatchV2(value) {
  if (!response.validate(value).valid) throw new Error('invalid_subject_watch_v2');
  for (const watch of value.watches ?? (value.watch ? [value.watch] : []))
    if (!validSubjectWatchPolicy(watch.policy)) throw new Error('invalid_subject_watch_v2');
  return value;
}
