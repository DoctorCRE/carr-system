import { ToolError } from './tool-error.ts';

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const REASONS = Object.freeze({
  same_email: 'Same email after trimming and case normalization.',
  same_npi: 'Same ten-digit NPI on two person records.',
  same_name_phone: 'Same full name after whitespace and case normalization, with a matching phone or cell number.',
});

export function partyDuplicateCandidateTools() {
  return {
    'party-duplicate-candidates': {
      write: false,
      description: 'Read-only duplicate candidates for contacts and companies with explicit match reasons. Shared identifiers can belong to different identities. Never chooses a survivor or merges records. Pages are ordered by party UUID pair; use next_cursor while has_more is true. Each page reads current records, not a frozen snapshot.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
        cursor: { type: 'object', additionalProperties: false, properties: {
          left_id: { type: 'string', format: 'uuid' }, right_id: { type: 'string', format: 'uuid' },
        }, required: ['left_id','right_id'] },
      } },
      handler: async (c, _actor, args) => {
        if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).some(key => !['limit','cursor'].includes(key)))
          throw new ToolError({ error: 'invalid_duplicate_report_arguments' });
        const limit = args.limit === undefined ? 50 : args.limit;
        const cursor = args.cursor;
        if (!Number.isInteger(limit) || limit < 1 || limit > 100)
          throw new ToolError({ error: 'invalid_duplicate_report_limit' });
        if (cursor !== undefined && (!cursor || typeof cursor !== 'object' || Array.isArray(cursor)
            || Object.keys(cursor).length !== 2 || !UUID.test(cursor.left_id) || !UUID.test(cursor.right_id)
            || cursor.left_id.toLowerCase() >= cursor.right_id.toLowerCase()))
          throw new ToolError({ error: 'invalid_duplicate_report_cursor' });
        const { rows } = await c.query(`with live as (
          select id,ref,name,kind,
            case when btrim(email,E' \\t\\r\\n') ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
              then lower(btrim(email,E' \\t\\r\\n')) end email_key,
            nullif(lower(btrim(regexp_replace(name,'\\s+',' ','g'))),'') name_key,
            case when kind='person' and btrim(npi) ~ '^[0-9]{10}$' then btrim(npi) end npi_key,
            array(select case when length(digits)=11 then substring(digits from 2) else digits end
              from (select raw,regexp_replace(raw,'[^0-9]','','g') digits from unnest(array[phone,cell]) raw) p
              where raw ~ '^[+()0-9 .-]+$' and digits ~ '^1?[0-9]{10}$') phone_keys
          from public.party where merged_into is null and deleted_at is null
        ), keys as (
          select id,kind,'same_email' code,email_key match_key from live where email_key is not null
          union all select id,kind,'same_npi',npi_key from live where npi_key is not null
          union all select id,kind,'same_name_phone',jsonb_build_array(name_key,phone)::text
            from live cross join lateral unnest(phone_keys) phone where name_key is not null
        ), pairs as (
          select a.id left_id,b.id right_id,array_agg(distinct a.code) reason_codes
          from keys a join keys b on a.kind=b.kind and a.code=b.code and a.match_key=b.match_key and a.id<b.id
          where $1::uuid is null or (a.id,b.id)>($1::uuid,$2::uuid)
          group by a.id,b.id order by a.id,b.id limit $3
        ) select pairs.*,
          jsonb_build_object('id',a.id,'ref',a.ref,'name',a.name,'kind',a.kind) as left,
          jsonb_build_object('id',b.id,'ref',b.ref,'name',b.name,'kind',b.kind) as right
          from pairs join live a on a.id=left_id join live b on b.id=right_id
          order by left_id,right_id`, [cursor?.left_id ?? null,cursor?.right_id ?? null,limit+1]);
        const page = rows.slice(0, limit);
        const last = page.at(-1);
        const hasMore = rows.length > limit;
        return { schema_version: 'party-duplicate-candidates.v1', read_only: true,
          caution: 'Shared identifiers can belong to different identities. Review the source records before any identity decision.',
          candidates: page.map(row => ({ left: row.left, right: row.right,
            reasons: Object.entries(REASONS).filter(([code]) => row.reason_codes.includes(code))
              .map(([code, explanation]) => ({ code, explanation })) })),
          has_more: hasMore, next_cursor: hasMore ? { left_id: last.left_id, right_id: last.right_id } : null };
      },
    },
  };
}
