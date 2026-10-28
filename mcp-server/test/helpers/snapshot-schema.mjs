import { readFileSync } from 'node:fs';

// Restore event identity state after loading a database snapshot.
export async function restoreEventIdentity(client, snapshot) {
  const identity = snapshot.match(/ALTER TABLE public\.event ALTER COLUMN mutation_order ADD GENERATED ALWAYS AS IDENTITY \([\s\S]*?\n\);/)?.[0];
  if (!identity) throw new Error('current snapshot must declare the event mutation identity');
  await client.query(identity);
}

// Snapshots lag migrations: apply the client portal link migration when the restored snapshot predates it.
export async function applyPendingPortalLink(client) {
  const { rows } = await client.query("select to_regclass('public.v_deal_client_portal') as view");
  if (!rows[0].view)
    await client.query(readFileSync(new URL('../../../migrations/1013_register_client_portal.sql', import.meta.url), 'utf8'));
}

// Snapshots lag migrations: apply Clear the deck (WR577) when the restored snapshot predates it.
export async function applyPendingClearTheDeck(client) {
  const { rows } = await client.query("select to_regclass('public.v_deck_item') as view");
  if (!rows[0].view)
    await client.query(readFileSync(new URL('../../../migrations/1030_clear_the_deck.sql', import.meta.url), 'utf8'));
}
