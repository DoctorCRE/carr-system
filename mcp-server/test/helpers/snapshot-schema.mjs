import { installFocusedFutureWorkStore } from './focused-future-work-store.mjs';
import { readFileSync } from 'node:fs';

// Restore event identity state after loading a database snapshot.
export async function restoreEventIdentity(client, snapshot) {
  const identity = snapshot.match(/ALTER TABLE public\.event ALTER COLUMN mutation_order ADD GENERATED ALWAYS AS IDENTITY \([\s\S]*?\n\);/)?.[0];
  if (!identity) throw new Error('current snapshot must declare the event mutation identity');
  await client.query(identity);
}

export async function applyPendingDealRoomReads(client) {
  await applyFutureWorkSchema(client);
  const { rows } = await client.query("select to_regclass('public.v_deal_client_portal') as view");
  if (!rows[0].view)
    await client.query(readFileSync(new URL('../../../migrations/1013_register_client_portal.sql', import.meta.url), 'utf8'));
  await client.query("do $$ begin create role carr_authority; exception when duplicate_object then null; end $$");
  if (!(await client.query("select to_regclass('public.v_requirement_request') as view")).rows[0].view)
    await client.query(readFileSync(new URL('../../../migrations/1105_requirement_requests.sql', import.meta.url), 'utf8'));
  await client.query(readFileSync(new URL('../../../migrations/1064_deal_sent_correspondence.sql', import.meta.url), 'utf8'));
  await client.query(readFileSync(new URL('../../../migrations/1117_preserve_call_plan_projection.sql', import.meta.url), 'utf8'));
  await client.query(readFileSync(new URL('../../../migrations/1065_call_followup_contact.sql', import.meta.url), 'utf8'));
  await client.query(readFileSync(new URL('../../../migrations/1067_post_call_due_provenance.sql', import.meta.url), 'utf8'));
  await client.query(readFileSync(new URL('../../../migrations/1079_single_action_completion.sql', import.meta.url), 'utf8'));
}

export async function applyFutureWorkSchema(client) {
  if (!(await client.query("select to_regclass('ops.work_request') as relation")).rows[0].relation) {
    await installFocusedFutureWorkStore(client);
    return;
  }
  if (!(await client.query("select to_regclass('ops.future_work_link') as relation")).rows[0].relation)
    await client.query(readFileSync(new URL('../../../migrations/1120_future_work_links.sql', import.meta.url), 'utf8'));
}
