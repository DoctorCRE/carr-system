// DoctorCRE v5 slice V5-J103: the governed correspondence store and its verbs.
//
// THE KERNELS DECIDE; THIS MODULE MAKES THE DURABLE HALF REACHABLE. It owns four
// verbs over the relations the J103 store migration installs, and no others:
//
//   correspondence-readiness              read   what is installed, consented,
//                                                read and owed, per partner
//   read-correspondence-thread            read   the provenance-preserving read of
//                                                stored adapter read receipts
//   record-correspondence-adapter-consent human  a partner's consent for one
//                                                adapter to READ their own mailbox
//   revoke-correspondence-adapter-consent human  its withdrawal
//
// THERE IS NO SEND VERB AND NO DRAFT VERB HERE, and the second absence is a
// decision rather than an omission. The draft RELATION and its writer ship in
// the migration, with every no-dispatch property held by CHECK constraints and
// proved by mcp-server/test/governed-correspondence-store-postgres.sql. A draft
// verb, though, must run governed-correspondence.v5.js's draft classification
// (privacy boundary, author seam, participant rules), and that kernel only
// drafts from a read IT produced. Its adapter-receipt lookup is deliberately
// null until an adapter exists; binding it to this store's receipts changes a
// twice-reviewed kernel's access control, so it lands WITH the F10 adapter that
// will write the first receipt, not before. Registering a draft verb now would
// register a write that can never write.
//
// NOTHING HERE IS A CALLER'S WORD. The partner a consent belongs to is the
// verified-partner context the server sets for a humanOnly act — read back from
// the transaction, never an argument — and the record layer checks it again. The
// mailbox account arrives once, is normalized and digested here, and only the
// digest is sent to the database or returned. A thread read is fenced to the
// server-derived sponsor inside the database. Stored digests are recomputed on
// read and a mismatch refuses the whole read rather than skipping a row.
//
// WHICH MAILBOX A PARTNER MAY CONSENT FOR: A POSITIVE ALLOWLIST. The partner is
// derived; the account must then be one V5_J103_PARTNER_MAILBOXES maps to THAT
// partner, and nothing else:
//   joe  -> joe.bookout@carr.us
//   dell -> dell.mccraney@carr.us
// Any other address is refused as account_not_partners_own, here and again in
// the record layer, which holds the same (partner, digest) pairs and checks the
// pair, not the digest alone. So Joe cannot consent for Dell's mailbox, Dell
// cannot consent for Joe's, and a delegated or shared mailbox (another person's
// mailbox a partner's Outlook happens to hold) is refused by construction: it is
// not on the list. Adding an address is a reviewed source change in both places,
// and a parity test keeps the two lists equal. The F10 installation binding
// still owes one more check: its receipt writer must match the installation's
// own account against the consent's account_digest before it records a receipt.
//
// ACTIVATION IS THE HUMAN STEP, AND THIS MODULE CANNOT TAKE IT. Recording consent
// unlocks no read by itself: reads come from receipts, and the receipt writer is
// granted to no runtime role until the F10 adapter and its seat land.
// correspondence-readiness says exactly that, from the database's own grants.

import { createHash } from "node:crypto";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J103_ADAPTER_KINDS,
  V5_J103_ADAPTER_READ_RECEIPT_SEAM,
  V5_J103_RECONCILIATION_ITEM_SEAM,
  V5_J103_SEND_AUTHORITY_HOLDER,
  V5_J103_SEND_AUTHORITY_SEAM,
  governedCorrespondenceGaps,
  v5J103PolicyDigest,
} from "./governed-correspondence.v5.js";
import {
  V5_J103J_INTERNAL_UPDATE_STEP,
  v5J103JourneyPolicyDigest,
} from "./governed-correspondence-journey.v5.js";
import { V5_F10_READ_OPERATIONS, V5_F10_WRITE_OPERATIONS } from "./partner-mail-calendar.v5.js";

export const V5_J103_STORE_SCHEMA_VERSION = "doctorcre-v5-j103-correspondence-store.v1";

/** The verbs this module registers, and the only ones. */
export const V5_J103_STORE_VERBS = Object.freeze([
  "correspondence-readiness",
  "read-correspondence-thread",
  "record-correspondence-adapter-consent",
  "revoke-correspondence-adapter-consent",
]);

/** The seams this store names as owed. Each is a precondition of a real read. */
export const V5_J103_STORE_OWED_STEPS = Object.freeze([
  Object.freeze({
    step: "human:partner-local-mailbox-consent",
    owner: "the partner whose mailbox it is",
    what: "the partner records consent here for their OWN carr.us mailbox (joe: joe.bookout@carr.us; dell: dell.mccraney@carr.us), naming only F10 read operations. The adapter reads that partner's local stores on the partner's own Mac: New Outlook's HxStore or Apple Mail for mail, and Apple Calendar through EventKit",
  }),
  Object.freeze({
    step: V5_J103_ADAPTER_READ_RECEIPT_SEAM,
    owner: "V5-F10 provider client and its registered seat",
    what: "the adapter that reads a consented mailbox and writes ops.correspondence_record_read_receipt; that writer is granted to no runtime role until a reviewed forward migration grants it to the adapter seat",
  }),
  Object.freeze({
    step: "step:j103-kernel-receipt-binding",
    owner: "V5-J103 (governed-correspondence.v5.js issuedAdapterReadReceipt)",
    what: "bind the kernel's adapter-receipt lookup to this store's receipts, which is what lets the kernel produce reads, drafts and proposed facts and what a draft verb waits on",
  }),
  Object.freeze({
    step: V5_J103_RECONCILIATION_ITEM_SEAM,
    owner: "V5-F01 store (record-source-authority-store.v5.js)",
    what: "F01's store of issued reconciliation items and a mailbox field-authority registry, which the source-conflict queue reads",
  }),
  Object.freeze({
    step: V5_J103J_INTERNAL_UPDATE_STEP,
    owner: "F01/F06/J103 owners and an independent acceptance reviewer",
    what: "the amendment's gate for any automatic internal update; absent, every c4..c8 judgment remains a proposal",
  }),
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const SOURCE_SYSTEM = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$/;
const NATIVE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// The partner's own mailbox account: the single address J103 admits, as an
// identity to read FROM (the #983 kernel's exemption), digested on arrival.
const OWN_ACCOUNT = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/;
const UNSAFE_TEXT =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;

export class V5J103StoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J103StoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new V5J103StoreError(code, message, detail);
}

/**
 * The ONLY mailbox each partner may consent for. One server-side source; the
 * record layer (ops.correspondence_partner_owns_account in the store migration)
 * holds the same pairs as digests, and a parity test keeps them equal.
 */
export const V5_J103_PARTNER_MAILBOXES = Object.freeze({
  joe: Object.freeze(["joe.bookout@carr.us"]),
  dell: Object.freeze(["dell.mccraney@carr.us"]),
});

/** True only when `account` is on `partner`'s own list. */
export function partnerOwnsMailbox(partner, account) {
  const list = Object.hasOwn(V5_J103_PARTNER_MAILBOXES, partner) ? V5_J103_PARTNER_MAILBOXES[partner] : [];
  const digest = correspondenceAccountDigest(account);
  return list.some(own => correspondenceAccountDigest(own) === digest);
}

/** Normalized then digested; the address itself is never stored or returned. */
export function correspondenceAccountDigest(account) {
  if (typeof account !== "string") refuse("invalid_account", "account must be a string");
  const normalized = account.trim().toLowerCase();
  if (!OWN_ACCOUNT.test(normalized)) refuse("invalid_account", "account is not a mailbox address");
  return `sha256:${createHash("sha256").update(normalized, "utf8").digest("hex")}`;
}

function assertUuid(value, path) {
  if (typeof value !== "string" || !UUID.test(value)) refuse("invalid_uuid", `${path} must be a UUID`, { path });
}

function assertQuote(value, path) {
  if (typeof value !== "string" || value.trim().length === 0 || value.length > 2000 ||
      (typeof value.isWellFormed === "function" && !value.isWellFormed()) || UNSAFE_TEXT.test(value)) {
    refuse("invalid_human_quote", `${path} must be the partner's own words, 1..2000 characters, no control characters`, { path });
  }
}

function assertClosedArgs(args, allowed) {
  if (args === null || typeof args !== "object" || Array.isArray(args)) refuse("invalid_shape", "arguments must be an object");
  for (const key of Object.keys(args)) {
    if (!allowed.includes(key)) refuse("unregistered_field", `"${key}" is not a field of this verb`, { key });
  }
}

/**
 * Validate one stored receipt as the record layer returned it. Fails closed: a
 * receipt whose stored digest is not the one its own bytes produce refuses the
 * whole read, because a read with a row quietly dropped is not the read.
 */
export function revalidateReceipt(row, path = "receipt") {
  const keys = ["read_receipt_id", "partner_slug", "adapter_kind", "account_digest", "native_identity",
    "thread_metadata", "metadata_digest", "recomputed_digest", "consent_in_force", "recorded_at"];
  if (row === null || typeof row !== "object") refuse("receipt_unreadable", `${path} is not an object`);
  for (const key of keys) if (!(key in row)) refuse("receipt_incomplete", `${path}.${key} is missing`, { path, key });
  if (!UUID.test(row.read_receipt_id)) refuse("receipt_incomplete", `${path}.read_receipt_id is not a UUID`);
  if (!SHA256_REF.test(row.account_digest)) refuse("receipt_provenance_lost", `${path}.account_digest is not a digest`);
  if (!V5_J103_ADAPTER_KINDS.includes(row.adapter_kind)) refuse("receipt_provenance_lost", `${path}.adapter_kind is not registered`);
  const n = row.native_identity;
  if (!n || !SOURCE_SYSTEM.test(n.source_system) || !NATIVE_ID.test(n.native_id) || !Number.isSafeInteger(n.native_id_epoch)) {
    refuse("receipt_provenance_lost", `${path}.native_identity is not a complete native identity`);
  }
  if (row.metadata_digest !== row.recomputed_digest) {
    refuse("receipt_digest_mismatch", `${path} no longer produces its stored digest; the read is refused whole`,
      { path, read_receipt_id: row.read_receipt_id });
  }
  return Object.freeze({
    read_receipt_id: row.read_receipt_id,
    partner_slug: row.partner_slug,
    adapter_kind: row.adapter_kind,
    account_digest: row.account_digest,
    native_identity: Object.freeze({ source_system: n.source_system, native_id: n.native_id, native_id_epoch: n.native_id_epoch }),
    thread_metadata: row.thread_metadata,
    metadata_digest: row.metadata_digest,
    consent_in_force: row.consent_in_force === true,
    recorded_at: row.recorded_at,
  });
}

const CEILING = Object.freeze({
  dispatchable: false,
  provider_operation: null,
  send_authority_holder: V5_J103_SEND_AUTHORITY_HOLDER,
  send_authority_seam: V5_J103_SEND_AUTHORITY_SEAM,
  automatic_internal_update: false,
});

export function governedCorrespondenceStoreTools({ withEnvelope, writeEvent, ToolError }) {
  const toolRefuse = (error, detail) => { throw new ToolError({ error, ...detail }); };
  const check = (fn) => {
    try { return fn(); } catch (error) {
      if (error instanceof V5J103StoreError) {
        toolRefuse(error.code, { message: error.message, ...(error.detail !== undefined ? { detail: error.detail } : {}) });
      }
      throw error;
    }
  };

  /** The partner the server verified for THIS humanOnly act, read back from the transaction. */
  const verifiedPartner = async (c) => {
    const slug = (await c.query(
      "select nullif(current_setting('carr.verified_human_actor_slug', true), '') as slug")).rows[0]?.slug ?? null;
    if (slug !== "joe" && slug !== "dell") {
      toolRefuse("verified_partner_required",
        { message: "consent is the partner's own act for their own mailbox; this transaction carries no verified partner" });
    }
    return slug;
  };

  return {
    "correspondence-readiness": {
      write: false,
      description: "What DoctorCRE's governed correspondence (V5-J103) can do right now, from the record layer: per partner, how many mailbox consents are in force or revoked, how many adapter read receipts and CARR-written drafts exist, whether any runtime role may write read receipts, and every step still owed before a mailbox is read — the partner's own consent, the F10 adapter and its seat, the kernel's receipt binding, F01's reconciliation store, and the independent receipt the 2026-09-20 amendment requires before any automatic internal update. Counts only; no correspondence content. There is no send tool anywhere in CARR: drafts are for a human to send.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, _actor, args) => {
        check(() => assertClosedArgs(args ?? {}, []));
        const readiness = (await c.query("select ops.correspondence_readiness() as r")).rows[0]?.r;
        if (!readiness || !Array.isArray(readiness.partners)) {
          toolRefuse("correspondence_store_unavailable", { message: "the record layer returned no readiness; the J103 store migration may not be applied" });
        }
        const receiptsPossible = readiness.read_receipt_writer_granted_to_runtime === true;
        return {
          ok: true,
          schema_version: V5_J103_STORE_SCHEMA_VERSION,
          readiness,
          mailbox_reads_possible: receiptsPossible,
          activation: {
            human_step: V5_J103_STORE_OWED_STEPS[0],
            status: readiness.partners.some(p => p.consents_in_force > 0) ? "consent_recorded" : "consent_not_recorded",
            note: "consent alone reads nothing; reads need adapter read receipts, which no runtime role can write yet",
          },
          owed: V5_J103_STORE_OWED_STEPS,
          kernel_gaps: governedCorrespondenceGaps(),
          policy: { correspondence: v5J103PolicyDigest(), journey: v5J103JourneyPolicyDigest() },
          consentable_operations: [...V5_F10_READ_OPERATIONS],
          never_consentable_operations: [...V5_F10_WRITE_OPERATIONS],
          ...CEILING,
          effects: V5_NO_EFFECTS,
        };
      },
    },

    "read-correspondence-thread": {
      write: false,
      description: "The provenance-preserving read of one correspondence thread (V5-J103): every adapter read receipt stored for one native identity in YOUR sponsor's own mailbox, each carrying the partner, the adapter, the mailbox account DIGEST and the full (source_system, native_id, native_id_epoch) identity, with typed thread metadata only — never a subject, body or attachment bytes. Every stored digest is recomputed on read and a mismatch refuses the whole read. Answers `unavailable` naming the owed seam when no receipt exists, which today is always: no adapter can write receipts yet.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          source_system: { type: "string" },
          native_id: { type: "string" },
          native_id_epoch: { type: "integer", minimum: 0 },
        },
        required: ["source_system", "native_id", "native_id_epoch"],
      },
      handler: async (c, _actor, args) => {
        check(() => {
          assertClosedArgs(args ?? {}, ["source_system", "native_id", "native_id_epoch"]);
          if (!SOURCE_SYSTEM.test(args.source_system ?? "")) refuse("invalid_identifier", "source_system is not a permitted identifier");
          if (!NATIVE_ID.test(args.native_id ?? "")) refuse("invalid_identifier", "native_id is not a permitted identifier");
          if (!Number.isSafeInteger(args.native_id_epoch) || args.native_id_epoch < 0) refuse("invalid_identifier", "native_id_epoch must be a non-negative integer");
        });
        const rows = (await c.query(
          "select ops.correspondence_thread_readback($1::text,$2::text,$3::integer) as rows",
          [args.source_system, args.native_id, args.native_id_epoch])).rows[0]?.rows;
        if (!Array.isArray(rows)) toolRefuse("correspondence_store_unavailable", { message: "the record layer returned no readback" });
        const native_identity = { source_system: args.source_system, native_id: args.native_id, native_id_epoch: args.native_id_epoch };
        if (rows.length === 0) {
          return {
            ok: true, decision: "unavailable", reason_id: "j103.store.no_read_receipt",
            owed_seam: V5_J103_ADAPTER_READ_RECEIPT_SEAM, native_identity, receipts: [],
            ...CEILING, effects: V5_NO_EFFECTS,
          };
        }
        const receipts = check(() => rows.map((row, i) => revalidateReceipt(row, `receipts[${i}]`)));
        for (const r of receipts) {
          if (r.native_identity.source_system !== args.source_system || r.native_identity.native_id !== args.native_id ||
              r.native_identity.native_id_epoch !== args.native_id_epoch) {
            toolRefuse("receipt_provenance_lost", { message: "the record layer returned a receipt for a different native identity" });
          }
        }
        return {
          ok: true, decision: "receipts_found", reason_id: "j103.store.receipts_with_provenance",
          native_identity, receipts, ...CEILING, effects: V5_NO_EFFECTS,
        };
      },
    },

    "record-correspondence-adapter-consent": {
      write: true, humanOnly: true,
      description: "HUMAN-ONLY. Record YOUR consent for one authorized adapter to READ your OWN mailbox (V5-J103 activation step). The partner is the verified partner the server establishes for this act (you, or an agent you sponsor acting on your quoted words under the 2026-08-26 humanOnly ruling), never an argument. `account` must be YOUR OWN carr.us mailbox and nothing else: joe may consent only for joe.bookout@carr.us, dell only for dell.mccraney@carr.us. Any other address, including the other partner's mailbox and any delegated or shared mailbox your Mail app holds, is refused (account_not_partners_own) here and by the record layer. The account is normalized and digested on arrival and only the digest is stored or returned. The adapter reads your local stores on your own Mac: New Outlook HxStore or Apple Mail for mail, and Apple Calendar through EventKit. `read_operations` may name only the adapter's READ operations (list and read mail/calendar metadata); a send, move, delete or any other write operation is refused by the database, so no consent here can ever authorise sending. `human_quote` is your literal words granting it. Consent alone reads nothing: reads need the adapter to write read receipts, and no runtime role can yet. Revoke with revoke-correspondence-adapter-consent.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          adapter_kind: { type: "string", enum: [...V5_J103_ADAPTER_KINDS] },
          account: { type: "string", description: "your own carr.us mailbox (joe: joe.bookout@carr.us; dell: dell.mccraney@carr.us); digested on arrival, never stored" },
          read_operations: { type: "array", minItems: 1, maxItems: 4, uniqueItems: true,
            items: { type: "string", enum: [...V5_F10_READ_OPERATIONS] } },
          human_quote: { type: "string" },
        },
        required: ["idempotency_key", "adapter_kind", "account", "read_operations", "human_quote"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-correspondence-adapter-consent", args, async () => {
        const account_digest = check(() => {
          assertClosedArgs(args ?? {}, ["idempotency_key", "adapter_kind", "account", "read_operations", "human_quote"]);
          assertUuid(args.idempotency_key, "idempotency_key");
          if (!V5_J103_ADAPTER_KINDS.includes(args.adapter_kind)) refuse("unregistered_adapter", "adapter_kind is not a registered J103 adapter");
          if (!Array.isArray(args.read_operations) || args.read_operations.length === 0 || args.read_operations.length > 4 ||
              new Set(args.read_operations).size !== args.read_operations.length) {
            refuse("invalid_read_operations", "read_operations must be 1..4 distinct operations");
          }
          for (const op of args.read_operations) {
            if (!V5_F10_READ_OPERATIONS.includes(op)) {
              refuse("write_operation_refused", `"${String(op)}" is not a read operation; consent can never name a send or any other write`, { operation: String(op) });
            }
          }
          assertQuote(args.human_quote, "human_quote");
          return correspondenceAccountDigest(args.account);
        });
        const partner = await verifiedPartner(c);
        // Positive allowlist: the account must be one mapped to THIS partner.
        // (The record layer holds the same pairs and refuses the same cases.)
        if (!partnerOwnsMailbox(partner, args.account)) {
          toolRefuse("account_not_partners_own", {
            message: "a partner may consent only for their own carr.us mailbox; any other address, including the other partner's and any delegated or shared mailbox, is refused",
          });
        }
        const consentId = (await c.query(
          "select ops.correspondence_record_adapter_consent($1::text,$2::text,$3::text,$4::text[],$5::text,$6::uuid) as id",
          [partner, args.adapter_kind, account_digest, [...args.read_operations].sort(), args.human_quote, args.idempotency_key],
        )).rows[0].id;
        await writeEvent(c, actor, "record-correspondence-adapter-consent", "correspondence_adapter_consent", consentId,
          { field: "consent_recorded",
            new: { partner_slug: partner, adapter_kind: args.adapter_kind, account_digest, read_operations: [...args.read_operations].sort() },
            human_quote: args.human_quote, idempotency_key: args.idempotency_key });
        return {
          ok: true, consent_id: consentId, partner_slug: partner, adapter_kind: args.adapter_kind,
          account_digest, read_operations: [...args.read_operations].sort(),
          reads_enabled: false,
          reads_enabled_reason: "consent is recorded; a mailbox is read only once the adapter writes read receipts, which no runtime role can do yet",
          ...CEILING, effects: { ...V5_NO_EFFECTS, database_writes: 1 },
        };
      }),
    },

    "revoke-correspondence-adapter-consent": {
      write: true, humanOnly: true,
      description: "HUMAN-ONLY. Withdraw a mailbox consent recorded with record-correspondence-adapter-consent. Only the partner whose mailbox it is may revoke it. Revocation is append-only and final for that consent: the record layer then refuses every new read receipt and every new draft against it. Existing receipts and drafts are kept as history. Record a new consent to resume.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          consent_id: { type: "string" },
          human_quote: { type: "string" },
        },
        required: ["idempotency_key", "consent_id", "human_quote"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "revoke-correspondence-adapter-consent", args, async () => {
        check(() => {
          assertClosedArgs(args ?? {}, ["idempotency_key", "consent_id", "human_quote"]);
          assertUuid(args.idempotency_key, "idempotency_key");
          assertUuid(args.consent_id, "consent_id");
          assertQuote(args.human_quote, "human_quote");
        });
        await verifiedPartner(c);
        const revocationId = (await c.query(
          "select ops.correspondence_revoke_adapter_consent($1::uuid,$2::text,$3::uuid) as id",
          [args.consent_id, args.human_quote, args.idempotency_key])).rows[0].id;
        await writeEvent(c, actor, "revoke-correspondence-adapter-consent", "correspondence_adapter_consent", args.consent_id,
          { field: "consent_revoked", new: { revocation_id: revocationId }, human_quote: args.human_quote, idempotency_key: args.idempotency_key });
        return {
          ok: true, consent_id: args.consent_id, revocation_id: revocationId, consent_in_force: false,
          ...CEILING, effects: { ...V5_NO_EFFECTS, database_writes: 1 },
        };
      }),
    },
  };
}
