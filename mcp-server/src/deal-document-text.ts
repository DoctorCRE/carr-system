import { authorizationClassForActor } from './identity.js';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs } from './verb-contract.js';

const uuid = { type: 'string', format: 'uuid' };
const hash = { type: 'string', pattern: '^[0-9a-f]{64}$' };
const object = <T extends Record<string, unknown>>(properties: T, required = Object.keys(properties)) =>
  ({ type: 'object', additionalProperties: false, properties, required });
function authenticated(actor: Actor) {
  if (!actor?.id) throw new ToolError({ error: 'unauthenticated' });
}
function jobOnly(actor: Actor) {
  authenticated(actor);
  if (['verified_partner', 'probe_agent', 'review_agent'].includes(authorizationClassForActor(actor)))
    throw new ToolError({ error: 'not_in_profile' });
}
type TextRow = { original_hash: string; content_hash: string | null; text_hash: string;
  status: 'produced' | 'no_text' | 'unavailable'; pages: string[] };

export function dealDocumentTextTools() {
  return {
    'record-deal-document-text': {
      write: true,
      description: 'Automation-only immutable deterministic page-text retention. Binds current undeleted deal attachment and original SHA256. Same attachment/hash/format replays; different text refuses. No model calls.',
      inputSchema: object({ document_ref: uuid, entity_ref: uuid, content_hash: hash,
        text_format_version: { const: 'page-text.v1' }, status: { enum: ['produced', 'no_text', 'unavailable'] },
        pages: { type: 'array', maxItems: 1000, items: { type: 'string', maxLength: 500000 } } }),
      handler: async (c: Db, actor: Actor, args: VerbInputs['record-deal-document-text']) => {
        jobOnly(actor);
        const result = (await c.query<{ r: { ok: boolean; error?: string } }>(
          'select ops.record_deal_document_text($1,$2::jsonb) r', [actor.id, JSON.stringify(args)])).rows[0].r;
        if (!result.ok) throw new ToolError({ error: result.error ?? 'retention_failed' });
        return result;
      },
    },
    'list-deal-document-text-sources': {
      write: false,
      description: 'Automation-only bounded keyset scan of undeleted deal originals for deterministic text backfill. Includes retained flag for current hash/format. No model call or writes.',
      inputSchema: object({ after: { anyOf: [uuid, { type: 'null' }] }, limit: { type: 'integer', minimum: 1, maximum: 100 } }, ['limit']),
      handler: async (c: Db, actor: Actor, args: VerbInputs['list-deal-document-text-sources']) => {
        jobOnly(actor);
        const documents = (await c.query(`select a.id document_ref,a.subject_id entity_ref,a.sha256 content_hash,
          a.mime,a.bytes::integer,a.r2_key,exists(select 1 from ops.deal_document_text t where
            t.document_ref=a.id and t.content_hash=a.sha256 and t.text_format_version='page-text.v1') retained
          from public.attachment a where a.subject_type='deal' and a.deleted_at is null
            and a.bytes between 1 and 26214400 and ($1::uuid is null or a.id>$1::uuid)
          order by a.id limit $2`, [args.after ?? null, args.limit])).rows;
        return { ok: true, documents };
      },
    },
    'read-deal-document-text': {
      write: false,
      description: 'deal-document-text.v1. Read retained page-text.v1 for an undeleted attachment in a visible deal. Original hash pins pagination and refuses superseded content. Explicit no_text/unavailable. At most 16000 Unicode characters and 200 line fragments. Stable 1-based page/line and 0-based character offsets. No model, parser, object keys or original bytes.',
      inputSchema: object({ deal_id: uuid, attachment_id: uuid, content_hash: hash,
        page: { type: 'integer', minimum: 1, maximum: 1000 }, line: { type: 'integer', minimum: 1, maximum: 500001 },
        offset: { type: 'integer', minimum: 0, maximum: 500000 },
        max_characters: { type: 'integer', minimum: 1, maximum: 16000 } }, ['deal_id', 'attachment_id']),
      handler: async (c: Db, actor: Actor, args: VerbInputs['read-deal-document-text']) => {
        authenticated(actor);
        const doc = (await c.query<TextRow>(`select d.content_hash original_hash,
          t.content_hash,t.text_hash,t.status,t.pages from public.v_deal_documents d
          left join ops.deal_document_text t on t.document_ref=d.id and t.entity_ref=d.deal_id
            and t.content_hash=d.content_hash and t.text_format_version='page-text.v1'
          where d.deal_id=$1 and d.id=$2`, [args.deal_id, args.attachment_id])).rows[0];
        if (!doc) throw new ToolError({ error: 'document_not_found' });
        if (args.content_hash && args.content_hash !== doc.original_hash) throw new ToolError({ error: 'source_changed' });
        if ((args.page || args.line || args.offset) && !args.content_hash) throw new ToolError({ error: 'content_hash_required' });
        const identity = { ok: true, schema_version: 'deal-document-text.v1', text_format_version: 'page-text.v1',
          deal_id: args.deal_id.toLowerCase(), attachment_id: args.attachment_id.toLowerCase(), content_hash: doc.original_hash };
        if (!doc.content_hash) return { ...identity, status: 'unavailable', reason: 'not_produced', text_hash: null, page_count: null, lines: [], next: null };
        if (doc.status !== 'produced') return { ...identity, status: doc.status, reason: doc.status === 'no_text' ? 'no_text_layer' : 'unsupported_or_limits',
          text_hash: doc.text_hash, page_count: doc.pages.length, lines: [], next: null };
        let page = args.page ?? 1, line = args.line ?? 1, offset = args.offset ?? 0;
        if (page > doc.pages.length || line > doc.pages[page - 1].split('\n').length
          || offset > Array.from(doc.pages[page - 1].split('\n')[line - 1]).length) throw new ToolError({ error: 'invalid_position' });
        let budget = args.max_characters ?? 16000;
        const lines: { page: number; line: number; offset: number; text: string; end_of_line: boolean }[] = [];
        while (page <= doc.pages.length && budget > 0 && lines.length < 200) {
          const pageLines = doc.pages[page - 1].split('\n');
          const chars = Array.from(pageLines[line - 1]);
          const text = chars.slice(offset, offset + budget).join('');
          const size = Array.from(text).length;
          const end_of_line = offset + size === chars.length;
          lines.push({ page, line, offset, text, end_of_line });
          budget -= size;
          if (end_of_line) {
            offset = 0; line++;
            if (line > pageLines.length) { page++; line = 1; }
          } else offset += size;
        }
        return { ...identity, status: 'produced', text_hash: doc.text_hash, page_count: doc.pages.length, lines,
          next: page <= doc.pages.length ? { content_hash: doc.original_hash, page, line, offset } : null };
      },
    },
  };
}
