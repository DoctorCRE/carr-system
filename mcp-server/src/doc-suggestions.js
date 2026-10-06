// B08: versioned Doc suggestions over the canonical conversation and loop stores.
// All actor, source body, contributor and source time facts are read in SQL.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const valid = value => UUID.test(String(value || ''));

export function docSuggestionTools({ withEnvelope, writeEvent, ToolError }) {
  const result = (value, fallback) => {
    if (!value?.ok) throw new ToolError({ error: value?.reason_id || fallback, current: value?.current || null });
    return value;
  };
  return {
    'complete-doc-suggestion-scan': {
      write: true, authorityOnly: true,
      description: 'Record that the Doc producer examined every turn through the current conversation head. A later turn makes that coverage unknown until scanned again.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' }, conversation_id: { type: 'string' },
        through_sequence: { type: 'integer' },
      }, required: ['idempotency_key','conversation_id','through_sequence'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'complete-doc-suggestion-scan', args, async () => {
        if (!valid(args.idempotency_key) || !valid(args.conversation_id)
          || !Number.isInteger(args.through_sequence) || args.through_sequence < -1)
          throw new ToolError({ error: 'doc_suggestion_scan_input_invalid' });
        const found = await c.query('select ops.complete_doc_suggestion_scan($1::uuid,$2::integer,$3::uuid) as result',
          [args.conversation_id,args.through_sequence,args.idempotency_key]);
        const value = result(found.rows[0]?.result, 'doc_suggestion_scan_refused');
        if (!value.deduplicated) await writeEvent(c, actor, 'complete-doc-suggestion-scan',
          'doc_conversation', args.conversation_id,
          { new: { through_sequence: value.through_sequence }, idempotency_key: args.idempotency_key });
        return value;
      }),
    },
    'suggest-doc-work': {
      write: true, authorityOnly: true,
      description: 'Suggest one obligation from a recorded Doc turn. Exact matching material facts preserve a dismissal; changed facts reopen it. Each contribution keeps its original words and source time.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' }, conversation_id: { type: 'string' },
        source_sequence: { type: 'integer' }, obligation_key: { type: 'string' },
        polished_text: { type: 'string' }, uncertainty: { type: 'string' },
        material_facts: { type: 'object' },
      }, required: ['idempotency_key','conversation_id','source_sequence','obligation_key','polished_text','material_facts'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'suggest-doc-work', args, async () => {
        if (!valid(args.idempotency_key) || !valid(args.conversation_id) || !Number.isInteger(args.source_sequence)
          || args.source_sequence < 0 || !String(args.obligation_key || '').trim()
          || !String(args.polished_text || '').trim() || typeof args.material_facts !== 'object'
          || !args.material_facts || Array.isArray(args.material_facts))
          throw new ToolError({ error: 'doc_suggestion_input_invalid' });
        const found = await c.query('select ops.suggest_doc_work($1::uuid,$2::integer,$3::text,$4::text,$5::text,$6::jsonb,$7::uuid) as result',
          [args.conversation_id,args.source_sequence,args.obligation_key,args.polished_text,args.uncertainty || null,JSON.stringify(args.material_facts),args.idempotency_key]);
        const value = result(found.rows[0]?.result, 'doc_suggestion_refused');
        if (!value.deduplicated) await writeEvent(c, actor, 'suggest-doc-work', 'doc_suggestion', value.suggestion_id,
          { new: { obligation_key: args.obligation_key, material_version: value.material_version }, idempotency_key: args.idempotency_key });
        return value;
      }),
    },
    'list-doc-suggestions': {
      writerConnection: true,
      description: 'Read Doc suggestions visible to the signed-in partner, with each original contribution, current version, and producer coverage state. No actor field is accepted.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        conversation_id: { type: 'string' }, include_parked: { type: 'boolean' },
      } },
      handler: async (c, _actor, args) => {
        if (args.conversation_id !== undefined && !valid(args.conversation_id))
          throw new ToolError({ error: 'doc_conversation_id_invalid' });
        const found = await c.query('select ops.list_doc_suggestions($1::uuid,$2::boolean) as result',
          [args.conversation_id || null,args.include_parked === true]);
        return result(found.rows[0]?.result, 'doc_suggestions_unavailable');
      },
    },
    'decide-doc-suggestion': {
      write: true, writerConnection: true, humanOnly: true,
      description: 'Choose Act, Discuss, Snooze or Dismiss for one version of one Doc suggestion. Act requires a canonical work reference created by the existing work door.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' }, suggestion_id: { type: 'string' },
        base_version: { type: 'integer' }, choice: { type: 'string', enum: ['act','discuss','snooze','dismiss'] },
        snoozed_until: { type: 'string' }, work_ref: { type: 'string' },
      }, required: ['idempotency_key','suggestion_id','base_version','choice'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'decide-doc-suggestion', args, async () => {
        if (!valid(args.idempotency_key) || !valid(args.suggestion_id) || !Number.isInteger(args.base_version)
          || !['act','discuss','snooze','dismiss'].includes(args.choice))
          throw new ToolError({ error: 'doc_suggestion_decision_invalid' });
        const found = await c.query('select ops.decide_doc_suggestion($1::uuid,$2::integer,$3::text,$4::date,$5::text,$6::uuid) as result',
          [args.suggestion_id,args.base_version,args.choice,args.snoozed_until || null,args.work_ref || null,args.idempotency_key]);
        const value = result(found.rows[0]?.result, 'doc_suggestion_decision_refused');
        if (!value.deduplicated) await writeEvent(c, actor, 'decide-doc-suggestion', 'doc_suggestion', args.suggestion_id,
          { new: { choice: args.choice, work_ref: value.work_ref || null }, idempotency_key: args.idempotency_key });
        return value;
      }),
    },
    'propose-doc-correction': {
      write: true, writerConnection: true, humanOnly: true,
      description: 'Propose a typed correction to one suggestion at its displayed version. It preserves the draft and source; it never rewrites the canonical suggestion, task, entity or a global rule.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' }, suggestion_id: { type: 'string' },
        base_version: { type: 'integer' }, proposed_text: { type: 'string' },
        source_conversation_id: { type: 'string' }, source_sequence: { type: 'integer' },
      }, required: ['idempotency_key','suggestion_id','base_version','proposed_text','source_conversation_id','source_sequence'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'propose-doc-correction', args, async () => {
        if (!valid(args.idempotency_key) || !valid(args.suggestion_id) || !valid(args.source_conversation_id)
          || !Number.isInteger(args.base_version) || !Number.isInteger(args.source_sequence)
          || !String(args.proposed_text || '').trim())
          throw new ToolError({ error: 'doc_correction_input_invalid' });
        const found = await c.query('select ops.propose_doc_correction($1::uuid,$2::integer,$3::text,$4::uuid,$5::integer,$6::uuid) as result',
          [args.suggestion_id,args.base_version,args.proposed_text,args.source_conversation_id,args.source_sequence,args.idempotency_key]);
        const value = result(found.rows[0]?.result, 'doc_correction_refused');
        if (!value.deduplicated) await writeEvent(c, actor, 'propose-doc-correction', 'doc_suggestion', args.suggestion_id,
          { new: { proposal_id: value.proposal_id, base_version: args.base_version }, idempotency_key: args.idempotency_key });
        return value;
      }),
    },
  };
}
