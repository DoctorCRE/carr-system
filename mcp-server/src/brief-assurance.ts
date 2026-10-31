// Read assurance items for the brief.
import type { ClientBase } from "@neondatabase/serverless";

export async function readBriefAssurance(c: Pick<ClientBase, "query">, sponsor: string) {
  if (sponsor !== 'joe' && sponsor !== 'dell') throw new Error('assurance_partner_required');
  const recipient = (await c.query(
    "select public.retrieval_visibility_actor_id($1) as id", [sponsor])).rows[0];
  if (!recipient?.id) throw new Error('assurance_active_partner_required');
  return (await c.query(`
    select n.id as notification_id, n.reason, n.severity, n.subject_type, n.subject_ref,
           n.deep_link, n.created_at, s.signal_kind as reason_id
      from ops.notification n
      join public.signal_event s on s.id=n.event_ref and n.event_source='signal_event'
      left join ops.notification_read r
        on r.notification_id=n.id and r.recipient_actor=n.recipient_actor
     where n.recipient_actor=$1 and s.producer='v5-a05-delivery-cadence'
       and r.notification_id is null
       and ops.notification_visible(n.id)
     order by case n.severity when 'failure' then 0 when 'action_required' then 1 else 2 end,
              n.created_at, n.id`, [recipient.id])).rows;
}
