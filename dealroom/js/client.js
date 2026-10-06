/**
 * Deal Room client interface (WO-1 contract).
 *
 * Both FixtureClient and LiveClient implement this shape.
 * Live mode never runs network in this work order; it documents the wire
 * and throws until a base URL is configured by a later deploy step.
 *
 * @typedef {'joe'|'dell'|string} Actor
 * @typedef {'phase'|'owner'|'attention'|'next_date'|'next_step'|'operating_state'} DealField
 *
 * @typedef {Object} PipelineEvent
 * @property {string} id
 * @property {string} recorded_at ISO-8601
 * @property {Actor} actor
 * @property {string} verb
 * @property {string} subject_type
 * @property {string} subject_id
 * @property {string|null} field
 * @property {string|boolean|null} old_value
 * @property {string|boolean|null} new_value
 *
 * @typedef {Object} PresenceLease
 * @property {Actor} actor
 * @property {string} deal_id
 * @property {string} field
 * @property {string} expires_at ISO-8601
 *
 * @typedef {Object} ChangesResponse
 * @property {PipelineEvent[]} events
 * @property {PresenceLease[]} presence
 * @property {string} cursor opaque keyset cursor
 *
 * @typedef {Object} BoardDeal
 * @property {string} id
 * @property {string} name
 * @property {string} type
 * @property {string} phase
 * @property {Actor|null} owner
 * @property {boolean} attention
 * @property {string|null} last_touch
 * @property {string} next_step
 * @property {string|null} next_date
 * @property {string|null} segment
 * @property {string|null} market
 * @property {'active'|'parked'} operating_state whether this Salesforce-linked row is current work
 * @property {'prospect_never_active'|'client_paused'|'other'|null} parking_reason
 * @property {string|null} parking_note
 *
 * @typedef {Object} ThreadEntry
 * @property {string} id
 * @property {'note'|'archived_step'} kind
 * @property {Actor} actor
 * @property {string} text
 * @property {string} recorded_at
 *
 * @typedef {Object} HistoryEntry
 * @property {string} id
 * @property {Actor} actor
 * @property {string} summary
 * @property {string} recorded_at
 *
 * @typedef {Object} DealDetail
 * @property {BoardDeal} deal
 * @property {ThreadEntry[]} thread newest-first
 * @property {{label:string,date:string|null}[]} critical_dates
 * @property {HistoryEntry[]} history newest-first
 *
 * @typedef {Object} ConflictPayload
 * @property {string} conflict_id
 * @property {string} deal
 * @property {string} field
 * @property {{actor:Actor,value:*,event_id:string}} a
 * @property {{actor:Actor,value:*,event_id:string}} b
 *
 * @typedef {Object} WriteResult
 * @property {'ok'|'conflict'} status
 * @property {PipelineEvent} [event]
 * @property {ConflictPayload} [conflict]
 *
 * @typedef {Object} DealRoomClient
 * @property {'fixture'|'live'} mode
 * @property {Actor} selfActor
 * @property {() => Promise<{deals:BoardDeal[], as_of:string, last_call_at:string}>} getBoard
 * @property {(dealId:string) => Promise<DealDetail>} getDeal
 * @property {(cursor:string|null) => Promise<ChangesResponse>} getChanges
 * @property {(args:{deal:string, field:string, idempotency_key:string}) => Promise<{ok:true}>} presenceLease
 * @property {(args:{deal:string, field:string, value:*, base_event_id:string|null, idempotency_key:string}) => Promise<WriteResult>} patchDealField
 * @property {(args:{conflict_id:string, winner:'a'|'b', idempotency_key:string}) => Promise<WriteResult>} resolveConflict
 * @property {(args:{deal:string, text:string, idempotency_key:string}) => Promise<WriteResult>} addDealNote
 * @property {(args:{deal:string, text:string, next_date?:string|null, idempotency_key:string}) => Promise<WriteResult>} setNextStep
 * @property {(args:{name:string, idempotency_key:string}) => Promise<WriteResult>} createDeal
 * @property {() => Promise<{proposals:ConfirmProposal[]}>} [getPendingConfirms]
 * @property {(args:{deal_ids:string[]}) => Promise<{deals:Object[]}>} [getCallContext] exact active agenda records and participants
 * @property {(args:{proposal_id:string, accept:boolean, idempotency_key:string}) => Promise<WriteResult>} [resolveConfirm]
 * @property {(args:{candidate_id:string, accept:boolean, idempotency_key:string}) => Promise<WriteResult>} [resolvePostCallCandidate]
 * @property {() => Promise<Object>} [getHealth] the truthful health projection (V5-A01), once deployed
 * @property {(dealId:string) => Promise<Object>} [getRecordHome] the canonical record-home read (V5-F01), once deployed
 * @property {() => Promise<void>} [simulatePartnerCall] fixture-only demo of presence + distill
 *
 * @typedef {Object} ConfirmProposal
 * @property {string} id
 * @property {string} label HTML-safe plain text description
 * @property {string} deal_id
 * @property {string} verb
 * @property {Object} args
 */

export const PHASES = [
  'On Deck',
  'Research',
  'Negotiation',
  'Legal',
  'Diligence',
  'Closing',
  'Closed',
];

export const PHICON = {
  'On Deck': '🔥',
  Research: '🔍',
  Negotiation: '🤝',
  Legal: '⚖️',
  Diligence: '📋',
  Closing: '🔑',
  Closed: '✅',
};

export const ACTOR_LABEL = { joe: 'Joe', dell: 'Dell' };

/**
 * @param {'fixture'|'live'} mode
 * @param {Object} [opts]
 * @returns {Promise<import('./client.js').DealRoomClient>}
 */
export async function createClient(mode = 'fixture', opts = {}) {
  if (mode === 'live') {
    const { createLiveClient } = await import('./live-client.js');
    return createLiveClient(opts);
  }
  const { createFixtureClient } = await import('./fixture-client.js');
  return createFixtureClient(opts);
}
