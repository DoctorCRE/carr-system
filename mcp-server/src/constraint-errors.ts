import { ToolError } from "./tool-error.ts";

// DEFECT 2, HALF (b) (found 2026-08-13, decision 7026246b): a write whose bad
// input reaches the database raw (an enum this file never learned to validate,
// a foreign key nobody checked first) throws a driver error, not a ToolError —
// and mcp.js's top-level catch used to flatten EVERY non-ToolError into a bare
// {"error":"internal error"} with no field, no constraint, no allowed values.
// Half (a) closes the specific gap (marker, domain, above); this is the
// backstop for every OTHER enum/FK this file has not yet learned to check —
// Postgres SQLSTATE class 23 (integrity_constraint_violation) always names the
// constraint and usually the column, so that much can be surfaced honestly
// without ever touching the connection string (which lives in env bindings,
// never in a query or its error — nothing here reads or forwards env).
// Deliberately narrow: anything outside class 23 (a real connection or driver
// fault) returns null and falls through to the generic handler unchanged,
// because this is a translator for bad input, not a catch-all.
const PG_VIOLATION_KIND: Readonly<Record<string, string>> = Object.freeze({
  "23514": "check_violation", // e.g. marker/kind fails its CHECK
  "23503": "foreign_key_violation", // e.g. domain names no row in loop_domain
  "23505": "unique_violation",
  "23502": "not_null_violation",
});
const CONNSTR_RE = /\b\w+:\/\/[^\s'"]+/gi; // defense in depth; pg errors don't carry one today
function redact(s: unknown): unknown {
  return typeof s === "string" ? s.replace(CONNSTR_RE, "[redacted]") : s;
}

// THE TWO CLASSES THAT COST THE MOST ON 2026-08-21, neither of which is a
// constraint violation, so neither was translated and both reached the caller
// as a bare "internal error" naming nothing:
//
//   42501 insufficient_privilege — complete-capability-project took a row lock
//   on an append-only table the serving role holds only insert/select on. Row
//   locks need UPDATE. Every completion since the verb shipped died here, which
//   is why the AI Engineering Suite read 0 complete of 51 while six of its
//   projects were finished. Hours went into it because the error named nothing.
//
//   22P02 invalid_text_representation — a short id or a "#213"-style reference
//   reaching a uuid column. Hit twice in one session, on close-loop's successor
//   field and on update-decision, both of which document the human-readable
//   form in their own hints. Two defects filed under one class.
//
// A permission or a type mismatch is not less actionable than a CHECK
// violation, and it is far more likely to be a bug in the verb rather than in
// the caller's arguments — which is exactly why hiding it is expensive.
const PG_FAULT_KIND: Readonly<Record<string, string>> = Object.freeze({
  "42501": "insufficient_privilege",
  "22P02": "invalid_text_representation",
  "42703": "undefined_column",
  "42P01": "undefined_table",
  "42883": "undefined_function",
});
const PG_FAULT_HINT: Readonly<Record<string, string>> = Object.freeze({
  "42501":
    "the database refused this to the role the server connects as — the grant is missing, " +
    "or the statement takes a row lock (select ... for update) on a table this role may only " +
    "read. Read the handler's SQL before assuming a server fault; do not widen the grant " +
    "without checking whether the lock is needed at all.",
  "22P02":
    "a value did not parse as its column's type — most often a short id, a '#123' reference " +
    "or a name where a uuid is required. Check which form this field actually accepts; the " +
    "verb's own description may name a friendlier form than the code accepts.",
  "42703": "the statement names a column that does not exist — a schema change and this code have drifted.",
  "42P01": "the statement names a table that does not exist — a migration is missing on this database.",
  "42883": "the statement calls a function that does not exist — a migration is missing on this database.",
});

// A CHECK THAT SPANS MORE THAN ONE COLUMN REPORTS `column: null`, and 219 of
// this database's constraints do. Postgres is not being unhelpful -- it cannot
// name one column for a rule about several -- but the caller is then told that
// a rule broke without being told which of their inputs broke it, which is the
// difference between a refusal they can act on and a dead end. Measured
// 2026-09-18: 219 multi-column constraints, of which 86 leave the field
// completely unidentifiable and 212 leave the required fix unknowable.
//
// The catalog knows the answer. pg_constraint.conkey holds exactly the columns
// the rule is about, and pg_get_constraintdef prints the rule itself, so one
// lookup keyed on the constraint name the error already carries turns
// `column: null` into the list of fields involved plus the condition they must
// satisfy. That is a read of the system catalogs only -- no row of anyone's
// data is touched -- and it runs on the connection the failed statement was
// already using, after its rollback, so it costs no new connection.
const CONSTRAINT_COLUMNS_SQL = `
  select t.relname as table_name,
         pg_get_constraintdef(c.oid) as definition,
         coalesce(array_agg(a.attname order by a.attname)
                    filter (where a.attname is not null), '{}') as columns
    from pg_constraint c
    join pg_class t on t.oid = c.conrelid
    left join pg_attribute a
      on a.attrelid = c.conrelid and a.attnum = any(c.conkey) and not a.attisdropped
   where c.conname = $1
   group by t.relname, c.oid
   limit 1`;

interface ConstraintCatalogRow {
  table_name?: unknown;
  definition?: unknown;
  columns?: unknown;
}

export interface ConstraintCatalogClient {
  query(sql: string, values: string[]): Promise<{ rows?: readonly ConstraintCatalogRow[] } | null | undefined>;
}

/** Add the columns and the rule text to a constraint refusal, when we can.
 *
 * Deliberately best-effort: the refusal is already correct and already useful
 * without this, so a catalog lookup that fails must NOT replace a precise
 * refusal with a database error about the lookup. Any failure returns the
 * refusal untouched.
 */
export async function describeConstraint<T extends ToolError | null | undefined>(
  client: ConstraintCatalogClient | null | undefined,
  refusal: T,
): Promise<T> {
  const name = refusal?.payload?.constraint;
  if (!client || !refusal || typeof name !== "string" || !name) return refusal;
  let row: ConstraintCatalogRow | undefined;
  try {
    const out = await client.query(CONSTRAINT_COLUMNS_SQL, [name]);
    row = out?.rows?.[0];
  } catch {
    return refusal; // see the doc comment: never trade a good refusal for this
  }
  if (!row) return refusal;
  const columns: unknown[] = Array.isArray(row.columns) ? row.columns : [];
  if (columns.length === 0) return refusal;
  refusal.payload.table = refusal.payload.table || row.table_name || null;
  refusal.payload.columns = columns;
  refusal.payload.rule = redact(row.definition) || null;
  // Only overwrite the hint when the original was the unhelpful case: a rule
  // broke and no field was named. A single-column violation already told the
  // caller which field, and that hint is better than this one.
  if (!refusal.payload.column) {
    refusal.payload.hint =
      `this rule is about ${columns.length} fields together (${columns.join(", ")}), which is why ` +
      `no single field is named: the database cannot attribute a multi-column rule to one column. ` +
      `\`rule\` is the exact condition your values must satisfy. Check the combination, not each ` +
      `field on its own -- each may be individually valid.`;
  }
  return refusal;
}

export function pgConstraintError(e: unknown): ToolError | null {
  if (!isErrorFields(e)) return null;
  const code = e.code;
  if (typeof code !== "string") return null;
  if (PG_VIOLATION_KIND[code]) {
    return new ToolError({
      error: "invalid_field_value",
      violation: PG_VIOLATION_KIND[code],
      constraint: e.constraint || null,
      table: e.table || null,
      column: e.column || null,
      detail: redact(e.detail) || null,
      hint:
        "a value failed a database constraint — check it against this verb's documented " +
        "enum or required fields; this is the constraint the value actually violated, not a stack trace",
    });
  }
  if (PG_FAULT_KIND[code]) {
    return new ToolError({
      error: "database_refused_the_statement",
      fault: PG_FAULT_KIND[code],
      sqlstate: code,
      table: e.table || null,
      column: e.column || null,
      message: redact(e.message) || null,
      detail: redact(e.detail) || null,
      hint: PG_FAULT_HINT[code],
    });
  }
  return null;
}

function isErrorFields(value: unknown): value is Record<string, unknown> {
  return value !== null && (typeof value === "object" || typeof value === "function");
}
