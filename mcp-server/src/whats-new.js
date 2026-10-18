import { personalScopeForActor } from './identity.js';

const SECTIONS = ['deal_changes', 'lead_changes', 'next_actions', 'critical_dates',
  'partner_activity', 'new_leads', 'doc_suggestions', 'shipped_releases'];
const sentence = value => String(value ?? '').replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').trim()
  .replace(/[.!?]+$/, '').replace(/[.!?]+(?=\s)/g, ';');

export function whatsNewTools({ withEnvelope, executeRegisteredTool, ToolError }) {
  return {
    'whats-new': {
      write: true, writerConnection: true, destructiveHint: false,
      description: "Everything changed since the authenticated partner last marked a what's-new answer seen, grouped by deal and newest first. First use covers 24 hours. Reading never moves the watermark. Only mark_seen:true with an idempotency_key acknowledges this response through its high_water; reuse the key after a lost response to recover the same answer. Unavailable sections are explicit and prevent acknowledgement.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        mark_seen: { type: 'boolean', default: false }, idempotency_key: { type: 'string' },
      } },
      handler: async (c, actor, args = {}) => {
        const scope = personalScopeForActor(actor);
        if (scope.status !== 'personal') throw new ToolError({ error: 'whats_new_requires_partner_scope' });
        if (Object.keys(args).some(k => !['mark_seen', 'idempotency_key'].includes(k))
          || (args.mark_seen !== undefined && typeof args.mark_seen !== 'boolean'))
          throw new ToolError({ error: 'whats_new_input_invalid' });
        if (args.mark_seen === true && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(args.idempotency_key || ''))
          throw new ToolError({ error: 'missing_idempotency_key' });
        const load = async () => {
          const context = (await c.query('select ops.whats_new_context($1::boolean) as result',
            [args.mark_seen === true])).rows[0]?.result;
          if (!context?.ok) throw new ToolError({ error: 'whats_new_watermark_unavailable' });
          const sections = {};
          for (const name of SECTIONS) {
            // Recover a failed SQL source before proceeding in this transaction.
            await c.query('savepoint whats_new_source');
            try {
              let section;
              if (name === 'shipped_releases') {
                // Read the complete release catalog, then apply the snapshot window.
                // A late completion can carry a timestamp before the last watermark.
                const releases = await executeRegisteredTool(c, actor, 'list-shipped-releases', {});
                if (!releases?.ok || !Array.isArray(releases.releases)) throw new Error('source shape');
                section = (await c.query('select ops.whats_new_section($1::text,$2::timestamptz,$3::timestamptz,$4::pg_snapshot) as result',
                  [name, context.since, context.high_water, context.previous_snapshot])).rows[0]?.result;
                if (!section || !Array.isArray(section.items)) throw new Error('source shape');
                const catalog = new Map(releases.releases.map(r => [`release:${r.release_key}`,r]));
                if (section.items.some(i => !catalog.has(i.ref))) throw new Error('source mismatch');
              } else {
                section = (await c.query('select ops.whats_new_section($1::text,$2::timestamptz,$3::timestamptz,$4::pg_snapshot) as result',
                  [name, context.since, context.high_water, context.previous_snapshot])).rows[0]?.result;
              }
              if (!section || !['ready', 'empty', 'unavailable'].includes(section.state)
                || !Array.isArray(section.items)
                || (section.state === 'ready' && !section.items.length)
                || (section.state === 'empty' && section.items.length)) throw new Error('source shape');
              // Stage the complete rendered source before publishing any of it.
              // Invalid items share the SQL source's rollback/failure boundary.
              const items = section.items.map(item => {
                if (typeof item?.ref !== 'string' || !item.ref.trim()
                  || typeof item.at !== 'string' || !Number.isFinite(Date.parse(item.at))
                  || typeof item.text !== 'string' || !sentence(item.text)
                  || (item.group_ref != null && (typeof item.group_ref !== 'string' || !item.group_ref.trim()))
                  || (item.group_name != null && (typeof item.group_name !== 'string' || !item.group_name.trim())))
                  throw new Error('source item');
                return { section: name, ref: item.ref, at: item.at,
                  sentence: `${sentence(item.text)} (${item.ref}).`,
                  group_ref: item.group_ref || 'other', group_name: item.group_name || 'Other changes' };
              });
              sections[name] = { ...section, items };
            } catch {
              await c.query('rollback to savepoint whats_new_source');
              sections[name] = { state: 'unavailable', reason: 'source_unavailable', items: [] };
            } finally {
              await c.query('release savepoint whats_new_source');
            }
          }
          const groups = new Map();
          for (const [section, value] of Object.entries(sections)) {
            const items = [];
            for (const item of value.items) {
              const ref = item.group_ref;
              if (!groups.has(ref)) groups.set(ref, { ref, name: item.group_name, items: [] });
              const { group_ref, group_name, ...rendered } = item;
              groups.get(ref).items.push(rendered);
              items.push(rendered);
            }
            value.items = items;
          }
          const byTime = (a, b) => Date.parse(b.at)-Date.parse(a.at) || a.ref.localeCompare(b.ref);
          for (const group of groups.values()) group.items.sort(byTime);
          const state = Object.values(sections).some(s => s.state === 'unavailable') ? 'unavailable'
            : groups.size ? 'ready' : 'empty';
          let markedSeen = false;
          if (args.mark_seen === true && state !== 'unavailable') {
            const marked = (await c.query('select ops.mark_whats_new_seen($1::timestamptz,$2::pg_snapshot) as result',
              [context.high_water, context.snapshot])).rows[0]?.result;
            if (!marked?.ok) throw new ToolError({ error: 'whats_new_acknowledgement_failed' });
            markedSeen = true;
          }
          return { state, since: context.since, high_water: context.high_water,
            first_call: context.first_call, marked_seen: markedSeen, sections,
            groups: [...groups.values()].sort((a, b) => byTime(a.items[0], b.items[0])) };
        };
        return args.mark_seen === true ? withEnvelope(c, actor, 'whats-new', args, load) : load();
      },
    },
  };
}
