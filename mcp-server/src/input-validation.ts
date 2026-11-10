// Schema-guided tool input coercion and required-field validation.
import { ToolError, type ToolErrorPayload } from "./tool-error.ts";

interface InputSchema {
  type?: string | string[];
  properties?: Record<string, InputSchema | null>;
  required?: string[];
  anyOf?: InputSchema[];
  oneOf?: InputSchema[];
  items?: InputSchema;
}

function isArgumentBag(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// THE OTHER HALF OF THE SAME DEFECT (found 2026-08-13, loop 353). compareVersion
// above fixed ONE field, base_version, against mistyped arrival. The cause it
// names — "MCP tool-call arguments are never validated against inputSchema
// server-side" — was never field-specific, and leaving it at one field meant the
// next mistyped argument was only a matter of which verb got called.
//
// It got called. `teach` decides a rule's SCOPE with `args.personal ? ... : null`
// (loose truthiness) and echoes it back with `args.personal === true` (strict).
// A boolean that arrived as the STRING "false" is truthy at the first line and
// false at the second, so the verb stored a SHARED rule as PERSONAL while
// reporting personal_requested:false. That is not a cosmetic disagreement: scope
// decides WHO a taught rule binds, the response said the caller got what it
// asked for, and only a hand comparison of two exported files caught it. Twice
// in one session.
//
// Fixing teach alone would have been the same mistake a second time: a sweep
// found 17 sites reading a declared boolean or number with loose truthiness or
// bare arithmetic, SEVEN of which write the wrong value straight to the database
// (drift_critical on add-loop and update-loop, also_listing_side on add-premises,
// found and internal on record-finding, close on score-campaign), and eight more
// that silently skip a dedup or plausibility gate. So the coercion happens ONCE,
// at the choke point every verb passes through, and no handler has to remember.
//
// STRICTLY SCHEMA-DRIVEN, NEVER VALUE-SNIFFING. Only a property whose declared
// type is exactly "boolean", "integer" or "number" is touched. A field declared
// "string" is never inspected, so free text that happens to read "true" (a
// human_quote, a note, a rationale) is untouchable by construction. A union
// (oneOf, anyOf, or type given as an array) is skipped rather than guessed at —
// log-decision's `about` takes string OR array, and patch-deal-field has
// nullable strings.
//
// IT THROWS RATHER THAN GUESSING. "true"/"false" and numeric strings map
// cleanly; anything else in a typed field is a caller error and now fails
// loudly, in the same spirit as invalid_base_version. Silently leaving an
// unmappable value in place is what produced this defect in the first place.
//
// Recursive, because two of the affected flags are not top-level: add-premises
// carries also_listing_side inside ownership[] and force_new one level deeper
// inside ownership[].new_party.
// ── THE MARKUP WRITE DOOR (2026-08-14) ───────────────────────────────────────
// ops/store-markup-scan.py has been catching this AFTER the fact for weeks: a
// caller composes several long fields as one block of text, a field swallows its
// own closing tag, and every parameter after that tag is stored NULL. Six active
// shared rules carried it on 2026-08-13, the oldest four days old, five with a
// partner's verbatim quote absorbed into the rule statement.
//
// A detector cannot un-write a NULL, and this record layer refuses to edit a
// closed row on purpose — "a closed loop is history" — so damage that lands and
// is then closed is permanent. The write door is the only place it can actually
// be stopped, which is here.
//
// CORRUPTION vs MENTION is the same structural test ops/store-markup-scan.py's
// classify() uses, deliberately, because two doors disagreeing about one row is
// worse than either rule alone: the field swallowed ITS OWN closing tag, or it
// carries a bare marker that ate whatever should have followed. Markers quoted
// in backticks on one line are prose ABOUT the defect — the rule documenting it,
// the loops that tracked the cleanup, this comment — and must stay writable.
const TOOL_CALL_MARKERS = ["<parameter", "</parameter", "<invoke", "</invoke"];

export function looksLikeToolCallMarkup(field: unknown, value: unknown): boolean {
  if (typeof value !== "string" || value === "") return false;
  // close_outcome's own closing tag is </outcome>; the scan strips the close_
  // prefix the same way, and the two must not disagree about the same row.
  const ownCloser = `</${String(field).replace(/^close_/, "")}>`;
  if (value.includes(ownCloser)) return true;
  for (const marker of TOOL_CALL_MARKERS) {
    let idx = value.indexOf(marker);
    while (idx !== -1) {
      const before = value.lastIndexOf("`", idx);
      const after = value.indexOf("`", idx);
      const quoted = before !== -1 && after !== -1 && !value.slice(before, after).includes("\n");
      if (!quoted) return true;
      idx = value.indexOf(marker, idx + 1);
    }
  }
  return false;
}

// REQUIRED ARGUMENTS, ENFORCED AT THE DOOR (2026-08-14).
//
// Every verb declares `required` in its inputSchema and, until this existed,
// nothing checked it. mcp.js hands rpc.params.arguments through untouched and
// the local CLI path does the same, so a required field that was misspelled or
// simply absent arrived as undefined and the handler ran regardless.
//
// WHAT THAT PRODUCED, measured live rather than imagined. search-doctrine builds
// websearch_to_tsquery('english', undefined), which Postgres does not treat as
// an error — it matches nothing:
//
//     search-doctrine {"query":"HIPAA"}  ->  ok:true, hits:[], total:0
//     search-doctrine {}                 ->  ok:true, hits:[], total:0
//     search-doctrine {"q":"HIPAA"}      ->  20 hits
//
// A call with NO ARGUMENTS AT ALL came back clean and empty.
//
// AN EMPTY RESULT IS INDISTINGUISHABLE FROM A GENUINE ABSENCE, which is what
// makes this worse than a crash. On 2026-08-14 a session searched doctrine for a
// settled council ruling, got total:0, concluded the ruling did not exist, and
// filed a defect claiming the doctrine read path was broken. The ruling was
// there; the parameter name was wrong. Rule c53beeaa already says an ok:true
// confirms the call PARSED and never that the values landed — this enforces that
// at the boundary instead of hoping each of a hundred handlers remembers.
//
// It sits beside coerceArgsToSchema deliberately, per the same 2026-08-13 ruling
// that put coercion at the choke point rather than in seventeen handlers.
//
// PRESENCE, not truthiness: `false` and `0` are arguments a caller meant. `null`
// and `""` are what an unfilled template produces and carry no instruction.
//
// The near-miss hint is not decoration. The observed failure was a caller who
// HAD the schema and still sent `query` for `q`, so the error names the field it
// wanted and echoes the unrecognised keys that were sent instead.
export function assertRequiredArgs<T>(schema: InputSchema | null | undefined, args: T): T {
  const required = schema && Array.isArray(schema.required) ? schema.required : null;
  if (!required?.length) return args;
  const bag: Record<string, unknown> = isArgumentBag(args) ? args : {};
  // A field the schema DECLARES nullable (type ["string","null"], or an anyOf /
  // oneOf branch of type "null") takes null as a real answer: "no appointment",
  // "no prior route version". Refusing it made such verbs uncallable, since the
  // key is required AND its only honest value was null (append-tour-route-stop,
  // append-tour-route-stop-transition, search-tour-properties, 2026-09-23).
  // Absent keys and "" stay missing for every field.
  const props = schema?.properties || {};
  const nullable = (k: string) => {
    const p = props[k];
    if (!p || typeof p !== "object") return false;
    if (p.type === "null" || (Array.isArray(p.type) && p.type.includes("null"))) return true;
    return [p.anyOf, p.oneOf].some((alts) => Array.isArray(alts) && alts.some((a) => a && a.type === "null"));
  };
  const missing = required.filter((k) => {
    const v = bag[k];
    if (v === null && nullable(k)) return false;
    return v === undefined || v === null || v === "";
  });
  if (!missing.length) return args;
  const known = new Set(Object.keys(schema?.properties || {}));
  const unrecognised = Object.keys(bag).filter((k) => !known.has(k));
  const payload: ToolErrorPayload = {
    error: "missing_required",
    missing,
    hint: `this verb requires ${missing.map((m) => JSON.stringify(m)).join(", ")}`,
  };
  if (unrecognised.length) {
    payload.unrecognised = unrecognised;
    payload.hint +=
      `; it received ${unrecognised.map((u) => JSON.stringify(u)).join(", ")}` +
      `, which it does not accept — check the argument NAME against the schema before` +
      ` concluding the verb is broken or the answer is empty`;
  }
  throw new ToolError(payload);
}

export function coerceArgsToSchema(schema: InputSchema | null | undefined, args: unknown, path = ""): unknown {
  if (!schema || !isArgumentBag(args)) return args;
  const props = schema.properties;
  if (!props) return args;
  for (const [key, spec] of Object.entries(props)) {
    if (!spec || !Object.hasOwn(args, key)) continue;
    const v = args[key];
    if (v === undefined || v === null) continue;
    const where = path ? `${path}.${key}` : key;
    // Refused BEFORE any coercion or type branch: a leaked tag makes the value
    // wrong whatever its declared type, and the fields this lands in (body,
    // source_note, outcome) are plain strings that would otherwise sail through
    // untouched. The error names the field and the fix, because the caller that
    // does this is mid-way through composing several long fields and needs to be
    // told which one ran into the next.
    if (looksLikeToolCallMarkup(key, v)) {
      throw new ToolError({
        error: "tool_call_markup_in_value",
        field: where,
        hint:
          `${where} contains tool-call markup (a leaked </${String(key).replace(/^close_/, "")}> ` +
          `or <parameter …> tag). The field ran into the one after it, and every field ` +
          `after the leak would be stored empty. Pass each field as its own argument ` +
          `rather than composing them as one block of text, then retry. Prose ABOUT ` +
          `this defect is allowed when the markers are quoted in backticks.`,
      });
    }
    // A union is ambiguous by design; coercing one would destroy the other.
    if (spec.oneOf || spec.anyOf || Array.isArray(spec.type)) continue;
    if (spec.type === "boolean") {
      if (typeof v === "boolean") continue;
      if (typeof v === "string") {
        const s = v.trim().toLowerCase();
        if (s === "true") {
          args[key] = true;
          continue;
        }
        if (s === "false") {
          args[key] = false;
          continue;
        }
      }
      throw new ToolError({
        error: "invalid_boolean",
        field: where,
        got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared boolean; pass true or false, not a quoted or numeric value`,
      });
    }
    if (spec.type === "integer" || spec.type === "number") {
      if (typeof v === "number") continue;
      if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v.trim()))) {
        args[key] = Number(v.trim());
        continue;
      }
      throw new ToolError({
        error: "invalid_number",
        field: where,
        got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared ${spec.type}; pass a number`,
      });
    }
    if (spec.type === "object") {
      coerceArgsToSchema(spec, v, where);
      continue;
    }
    // A JSON-STRING ARRAY IS STILL AN ARRAY ARGUMENT, and until 2026-08-15 it fell
    // straight through here uncoerced, because this branch required Array.isArray
    // BEFORE it would look at anything. What that cost, live: doctrine-sections
    // measured `.length` on an ~80-character string and answered
    // "batch_too_large, max 50" for a batch of two, while a single id slipped
    // under the limit and died casting a string to uuid[]. claim-doctrine-sections
    // iterated the same string into characters, so no session could claim a
    // doctrine section and the single-writer write path was down for hours.
    //
    // Parsed HERE rather than in the handlers, per the 2026-08-13 ruling that put
    // coercion at the choke point instead of in seventeen of them. Every verb
    // taking an array gets this, not only the three that happened to be caught.
    let value = v;
    if (spec.type === "array" && typeof value === "string") {
      const text = value.trim();
      if (text.startsWith("[")) {
        try {
          const parsed: unknown = JSON.parse(text);
          if (Array.isArray(parsed)) {
            args[key] = value = parsed;
          }
        } catch {
          /* leave it; the handler's own validation refuses it by name */
        }
      }
    }
    if (spec.type === "array" && Array.isArray(value) && spec.items) {
      value.forEach((item, i) => {
        coerceArgsToSchema(spec.items, item, `${where}[${i}]`);
      });
    }
  }
  return args;
}
