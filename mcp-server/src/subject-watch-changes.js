import { parseSubjectWatchChanges } from '../contracts/subject-watch-change.v1.js';
const uuid = { type: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' };
export function subjectWatchChangeTools({ ToolError }) {
  return {
    'read-subject-watch-changes-v1': {
      humanOnly: true,
      humanCallerOnly: true,
      writerConnection: true,
      description: 'Read at most 100 meaningful change receipts for your current watch generation. Verified partner only. Removed or inaccessible watches refuse. Private reply evidence stays in its owning mailbox.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        subject_type: { type: 'string', enum: ['deal','lease'] }, subject_id: uuid, subscription_id: uuid,
        cursor: uuid, limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
      }, required: ['subject_type','subject_id','subscription_id'] },
      handler: async(c,_actor,args) => {
        const result=(await c.query('select ops.read_subject_watch_changes($1,$2::uuid,$3::uuid,$4::uuid,$5) as result',
          [args.subject_type,args.subject_id,args.subscription_id,args.cursor ?? null,args.limit ?? 50])).rows[0]?.result;
        if (!result?.ok) throw new ToolError({ error: result?.error || 'subject_watch_unavailable' });
        return parseSubjectWatchChanges(result);
      },
    },
  };
}
