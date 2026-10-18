import { isCalendarDate } from './calendar-date.js';

// One read owns the closed-deal/commission join for Invoices and Home.
// Recording a receipt changes the existing commission only; it moves no money.
export function invoiceTrackerTools({ ToolError, withEnvelope, writeEvent }) {
  return {
    'read-invoice-tracker': {
      write: false,
      description: 'Invoice tracker v1: closed uninvoiced deals and all invoiced commission entries, including received entries. Amounts are CARR gross commissions only, never client benefit or Salesforce placeholders. Blank deal invoiced_on is normal awaiting-invoice state. Due dates are explicit; no payment term is inferred.',
      inputSchema: { type: 'object', properties: {} },
      handler: async (c, actor) => {
        const result = await c.query(`select deal_id, name, owner, phase, lane, outcome,
          to_jsonb(closed_on)#>>'{}' as closed_on, to_jsonb(deal_invoiced_on)#>>'{}' as invoiced_on,
          commission_id, gross_amount, status, base_version,
          to_jsonb(commission_invoiced_on)#>>'{}' as commission_invoiced_on,
          to_jsonb(received_on)#>>'{}' as received_on, to_jsonb(due_on)#>>'{}' as due_on,
          to_jsonb(current_date)#>>'{}' as as_of
          from v_invoice_tracker order by name, commission_invoiced_on nulls last, commission_id`);
        return { schema_version: 'invoice-tracker.v1', actor: actor.slug, entries: result.rows,
          observed_at: new Date().toISOString() };
      },
    },
    'record-commission-receipt': {
      write: true,
      humanOnly: true,
      description: 'Record full receipt of one already invoiced CARR commission with its payment date. Never creates a commission, alters amounts, pays out a broker, contacts anyone or moves money. Fresh base_version required; conflict refuses without rebasing. The event and receipt are atomic and the idempotency key replays the same outcome.',
      inputSchema: { type: 'object', properties: {
        idempotency_key: { type: 'string' }, commission_id: { type: 'string', format: 'uuid' },
        base_version: { type: 'integer', minimum: 1 }, received_on: { type: 'string', format: 'date' },
      }, required: ['idempotency_key', 'commission_id', 'base_version', 'received_on'] },
      handler: async (c, actor, args) => withEnvelope(c, actor, 'record-commission-receipt', args, async () => {
        const day = args.received_on;
        if (!isCalendarDate(day))
          throw new ToolError({ error: 'invalid_received_on' });
        const row = (await c.query(`select id, deal_id, status, version,
          to_jsonb(invoiced_on)#>>'{}' as invoiced_on, to_jsonb(received_on)#>>'{}' as received_on,
          to_jsonb(current_date)#>>'{}' as today from commission where id=$1 for update`, [args.commission_id])).rows[0];
        if (!row) throw new ToolError({ error: 'invoice_not_found' });
        if (!Number.isInteger(args.base_version) || args.base_version !== row.version)
          throw new ToolError({ error: 'version_conflict', current_version: row.version });
        if (row.status !== 'invoiced' || !row.invoiced_on)
          throw new ToolError({ error: 'invoice_not_unpaid' });
        if (day < row.invoiced_on || day > row.today)
          throw new ToolError({ error: 'payment_date_out_of_range' });
        const paid = (await c.query(`update commission set status='received', received_on=$2,
          updated_by=$3 where id=$1 returning id, version as base_version,
          to_jsonb(received_on)#>>'{}' as received_on`, [row.id, day, actor.id])).rows[0];
        await writeEvent(c, actor, 'record-commission-receipt', 'deal', row.deal_id, {
          field: 'commission_receipt', old: { commission_id: row.id, status: row.status, received_on: row.received_on },
          new: { commission_id: row.id, status: 'received', received_on: day }, idempotency_key: args.idempotency_key,
        });
        return { ok: true, ...paid };
      }),
    },
  };
}
