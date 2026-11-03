import type { Actor, Db, WithEnvelope } from './verb-contract.ts';
import { ToolError } from './tool-error.ts';
import { organizationTenantForActor } from './identity.js';

export type WaitingIdentity = { kind: 'party'; id: string } | { kind: 'actor'; id: string };
type WaitingCommand = {
  idempotency_key: string;
  item_id: string;
  item_version: string;
  base_version: number;
  reason: string;
  evidence: string[];
};
type SetWaiting = WaitingCommand & { target: WaitingIdentity };
const uuid = { type: 'string', format: 'uuid' };
const properties = {
  idempotency_key: uuid,
  item_id: { type: 'string', minLength: 1, maxLength: 1000 },
  item_version: { type: 'string', pattern: '^[a-f0-9]{32}$' },
  base_version: { type: 'integer', minimum: 0, maximum: 2147483646 },
  reason: { type: 'string', minLength: 1, maxLength: 2000 },
  evidence: { type: 'array', minItems: 1, maxItems: 20,
    items: { type: 'string', minLength: 1, maxLength: 2000 } },
};
export function homeWaitingTools({ withEnvelope }: { withEnvelope: WithEnvelope }) {
  function command(name: string, setting: boolean) {
    return {
      write: true,
      writerConnection: true,
      description: `${setting ? 'Set or replace' : 'Clear'} the explicit person who owes a response on one Home card. Requires current item_version and waiting base_version. Reason and evidence are required. Never infers a person or changes task ownership. Exact retries replay the audited receipt.`,
      inputSchema: { type: 'object', additionalProperties: false,
        properties: { ...properties, ...(setting ? { target: {
          type: 'object', additionalProperties: false,
          properties: { kind: { type: 'string', enum: ['party', 'actor'] }, id: uuid },
          required: ['kind', 'id'],
        } } : {}) }, required: [...Object.keys(properties), ...(setting ? ['target'] : [])] },
      handler: async (c: Db, actor: Actor, args: WaitingCommand | SetWaiting) =>
        withEnvelope(c, actor, name, args, async () => {
          const response = await c.query('select ops.change_home_waiting($1,$2::uuid,$3::jsonb) result',
            [organizationTenantForActor(actor), args.idempotency_key,
              JSON.stringify({ ...args, idempotency_key: undefined, command: setting ? 'set' : 'clear' })]);
          const result = response.rows[0]?.result;
          if (result?.ok !== true) throw new ToolError({ error: result?.error || 'home_waiting_unavailable',
            current_version: result?.current_version });
          return result;
        }),
    };
  }
  return { 'set-home-waiting': command('set-home-waiting', true),
    'clear-home-waiting': command('clear-home-waiting', false) };
}
