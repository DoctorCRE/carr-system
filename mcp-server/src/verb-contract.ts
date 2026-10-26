// The types every verb module shares with tools.js: the database seam, the
// authenticated actor, the envelope and event writers tools.js hands to tool
// factories, and each verb's validated input. Types only; nothing here runs.
import type { ClientBase } from "@neondatabase/serverless";
import type { VerbInputs } from "../.generated/verb-inputs.js";

export type { VerbInputs };

/** The one database method verb modules use; test fakes satisfy it. */
export type Db = Pick<ClientBase, "query">;

/** The authenticated actor tools.js resolves before any handler runs. */
export type Actor = { id: string; [field: string]: unknown };

/** tools.js withEnvelope: idempotency, audit and error mapping around one write. */
export type WithEnvelope = <T>(c: Db, actor: Actor, verb: string, args: object,
  fn: () => Promise<T>) => Promise<T>;

/** tools.js lockDealField: row-locks one deal field (and what it supersedes) in lock order. */
export type LockDealField = (c: Db, dealId: string, field: string) => Promise<unknown>;

/** Another registered verb's handler, called in-process with already-checked arguments. */
export type DelegatedHandler = (c: Db, actor: Actor, args: Record<string, unknown>) => Promise<any>;

/** tools.js writeEvent: one append-only event row for a changed subject. */
export type WriteEvent = (c: Db, actor: Actor, verb: string, subjectType: string,
  subjectId: string, fields?: Record<string, unknown>) => Promise<unknown>;
