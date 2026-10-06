-- DoctorCRE V5-F01 — the DOCUMENT half of the derivative-registration rule.
-- A CANDIDATE. NOT A MIGRATION. NOT APPLIED BY ANYTHING IN THIS CHANGE.
--
-- WHAT THIS FILE IS. The additive relation, the private writer, three pure text
-- helpers and the forward-replacement of three existing objects, which together
-- give a derived DOCUMENT VERSION the same provenance edge a parsed proposal
-- already has: registered in the same transaction that completes the derivative,
-- or the derivative does not complete.
--
-- THE THREE FORWARD-REPLACEMENTS, each CREATE OR REPLACE over an object domain.sql
-- installed, none of them a DROP of anything but the writer this file supersedes:
--   ops.f01_guard_direct_dml()          one added alternative in the writer list
--   ops.f01_reserved_derivative_kinds() one added kind, the other kept (section 1.1)
--   ops.f01_record_document()           the four-argument form dropped and replaced
--                                       by the six-argument one (section 5)
--
-- WHAT THIS FILE IS NOT, and the parent must supply separately:
--   * A MIGRATION. There is no ordinal, no ledger entry, no SCAC registration and
--     no frontier reservation here. The file name carries no number on purpose.
--     Security attribution owns 0499/v25 and the F01 successor is v26; this hunk
--     belongs inside whatever the parent binds, and it binds nothing itself.
--   * AN APPLY. Nothing in this change runs it. It has not been executed against
--     any database, disposable or otherwise, and no claim is made here that it
--     does. The local PostgreSQL gate is the parent's to point at it.
--   * POLICY. No retention rule, no producer allow-list, no field owner, no
--     document class, no OneDrive item and no object-storage key is inserted,
--     defaulted or implied. It ships zero rows.
--   * LEGACY CHANGE. public.record_source and public.document are not read,
--     altered, backfilled, renamed, dual-written or referenced.
--   * EXTERNAL EFFECT. No provider call, no byte deletion, no notification, no
--     scheduler, no extension install, no role creation. The only grants it makes
--     are the posture every comparable F01 object already has: SELECT on the new
--     relation and EXECUTE on the new readers and pure text helpers for the four
--     runtime principals, EXECUTE on the replaced writer for the three that held
--     it before, and EXECUTE on the new private helper for nobody at all.
--
-- WHY A FORWARD REPLACEMENT RATHER THAN A NEW WRITER. The settled rule is that a
-- derived record registers its source BEFORE it is considered complete. A second,
-- optional "also register the document's source" writer would leave the ORIGINAL
-- ops.f01_record_document as a path that completes a derived document with no
-- provenance edge — which is the whole thing this exists to prevent. So the
-- four-argument form is DROPPED and replaced by a six-argument form that cannot
-- be called without a provenance statement, exactly as ops.f01_record_proposal's
-- four-argument form was dropped when its third envelope became mandatory. Two
-- overloads would also make the four-argument call ambiguous.
--
-- EVERY OLD GUARD IS PRESERVED. The replaced writer keeps its digest
-- recomputation, its actor derivation, its document advisory lock, its
-- prior-digest compare-and-swap against BOTH the stored current pointer and the
-- record's own claim, its idempotency claim-before-state ordering, and its
-- verified readback. Nothing anywhere in this file is relaxed: every replacement
-- adds a check or a name and removes none.
--
-- THE FOUR THINGS THIS FILE MAKES UNSAYABLE:
--   1. A document version recorded with no statement about where it came from.
--   2. A derived document version recorded without its ops.f01_derivative_link
--      row, or with one that names some other document or some other source.
--   3. A provenance row that claims coverage, claims to be an exhaustive
--      inventory, claims to permit a deletion, or admits that its source artifact
--      was inferred from the document's own bytes or its OneDrive identity.
--   4. A PUBLIC registration of an 'f01_document_version' link. That identity is
--      "<document_id>:<version_no>" — predictable before the document exists — over
--      an append-only unique index with no release path, so a caller could
--      otherwise pre-claim it and make the genuine completion of that version
--      conflict for ever, or repoint an already-completed one. Section 1.1
--      forward-replaces ops.f01_reserved_derivative_kinds() with BOTH internally
--      produced kinds. The private inserter is unaffected, which is exactly the
--      point: this file's own writer still registers the link, and nothing outside
--      the schema can.
--
-- WHAT IT STILL DOES NOT ESTABLISH. Coverage. ops.f01_derivative_coverage answers
-- 'unknown' for every artifact and this file does not change it: registering
-- where one document came from says nothing about whether some other derivative
-- of that artifact exists unregistered. Deletion still fails closed, and
-- ops.f01_stored_derivatives still returns NULL.
--
-- ORDER OF APPLICATION, AND WHAT NOTHING HERE CAN CATCH. This file assumes
-- domain.sql has already been applied in the same database. It must never be
-- applied again afterwards, and — said plainly, because an earlier revision of
-- this header claimed the opposite — NOTHING IN THIS FILE DETECTS IT IF IT IS.
-- Every check in section 6 runs once, while THIS file is applying. A later
-- domain.sql re-apply happens after they have all passed and re-runs only
-- domain.sql's own checks, which do not know this hunk exists. Three things break
-- silently in that order:
--
--   1. domain.sql's grant loop hands ops.f01_insert_document_source_provenance —
--      a private helper — to every runtime role, and its posture check (b) does
--      not notice, because both enumerate the private helpers BY NAME.
--   2. domain.sql's CREATE OR REPLACE restores the OLD ops.f01_guard_direct_dml,
--      whose writer alternation does not name that helper, so every provenance
--      insert is refused as direct DML. This one fails closed and loudly.
--   3. domain.sql's CREATE OR REPLACE restores ops.f01_reserved_derivative_kinds()
--      without 'f01_document_version', reopening the public pre-claim this file
--      closes, and restores the FOUR-ARGUMENT ops.f01_record_document beside the
--      six-argument one — a path that completes a derived document with no
--      provenance edge, which is the whole thing this file exists to prevent.
--
-- So the parent's domain.sql edits are MANDATORY rather than tidy-up, and they are
-- enumerated in section 6. Until they land, the only thing that catches the wrong
-- order is the SQL fixture, which runs after both files and asserts the private
-- helper is unreachable — a detection at test time, not a guard at install time.
--
-- REQUIRES PostgreSQL 13 or later and a UTF8 database, for the same reasons
-- domain.sql does; both are asserted there and not re-asserted here.

\set ON_ERROR_STOP on

-- ===========================================================================
-- 0. Preconditions, or refuse. This file extends a schema; it does not create
--    one, and it will not half-install against a database that lacks the parts
--    it forward-replaces.
-- ===========================================================================

DO $preconditions$
DECLARE
  v_missing text[] := ARRAY[]::text[];
  v_name text;
BEGIN
  IF to_regnamespace('ops') IS NULL THEN
    RAISE EXCEPTION 'f01_document_source_blocked: schema ops does not exist; apply the F01 domain schema first'
      USING ERRCODE = '42704';
  END IF;
  -- The text rules in section 1.2 spell their character classes as U& literals so
  -- this file can stay plain ASCII. PostgreSQL refuses that syntax outright when
  -- standard_conforming_strings is off, and refusing HERE says which setting and
  -- why, instead of failing four hundred lines later on a syntax error.
  IF current_setting('standard_conforming_strings') <> 'on' THEN
    RAISE EXCEPTION 'f01_document_source_blocked: standard_conforming_strings is %, and the Unicode string literals in section 1.2 require it to be on',
      current_setting('standard_conforming_strings') USING ERRCODE = '0A000';
  END IF;
  FOREACH v_name IN ARRAY ARRAY[
    'ops.f01_digest_jsonb(jsonb)',
    'ops.f01_is_digest_ref(text)',
    'ops.f01_is_instant_text(text)',
    'ops.f01_instant(text)',
    'ops.f01_tenant()',
    'ops.f01_context_actor_slug()',
    'ops.f01_claim_idempotency(text,text,text)',
    'ops.f01_settle_idempotency(text,text,jsonb)',
    'ops.f01_verify_envelope(jsonb,text,text,text)',
    'ops.f01_stored_artifact(text)',
    'ops.f01_insert_derivative_link(jsonb,text,text)',
    'ops.f01_derivative_coverage(text)',
    'ops.f01_current_policy_digest()',
    -- Forward-replaced in section 1.1. Replacing a function that is not there
    -- would CREATE one, and a "replacement" that is really a first definition is
    -- how a guard ends up in a schema whose public registration surface never
    -- consults it.
    'ops.f01_reserved_derivative_kinds()',
    'ops.f01_register_derivative_link(jsonb,text,text)',
    'ops.f01_guard_direct_dml()',
    'ops.f01_guard_append_only()',
    'ops.f01_guard_no_truncate()'
  ] LOOP
    IF to_regprocedure(v_name) IS NULL THEN v_missing := v_missing || v_name; END IF;
  END LOOP;
  -- ops.f01_corporate_artifact is named because the new relation carries a FOREIGN
  -- KEY to it: without the table the CREATE below fails on a missing relation
  -- rather than on a missing schema, which reads as a defect in this file instead
  -- of an incomplete install.
  IF to_regclass('ops.f01_document_version') IS NULL
     OR to_regclass('ops.f01_document_current') IS NULL
     OR to_regclass('ops.f01_corporate_artifact') IS NULL
     OR to_regclass('ops.f01_derivative_link') IS NULL THEN
    v_missing := v_missing ||
      'ops.f01_document_version / f01_document_current / f01_corporate_artifact / f01_derivative_link';
  END IF;
  -- The public registration surface must actually CONSULT the reserved-kind list,
  -- or replacing the list closes nothing. Checked against the installed source,
  -- because a schema where somebody inlined the literal would pass every other
  -- check here and silently keep the pre-claim open.
  IF to_regprocedure('ops.f01_register_derivative_link(jsonb,text,text)') IS NOT NULL
     AND pg_get_functiondef('ops.f01_register_derivative_link(jsonb,text,text)'::regprocedure)
           NOT LIKE '%f01_reserved_derivative_kinds()%' THEN
    RAISE EXCEPTION 'f01_document_source_blocked: ops.f01_register_derivative_link does not consult ops.f01_reserved_derivative_kinds(), so reserving a kind here would refuse nothing'
      USING ERRCODE = '42501';
  END IF;
  IF cardinality(v_missing) > 0 THEN
    RAISE EXCEPTION 'f01_document_source_blocked: the F01 domain schema is incomplete here; missing %',
      array_to_string(v_missing, ', ') USING ERRCODE = '42704';
  END IF;
END;
$preconditions$;

-- ===========================================================================
-- 1. The direct-DML guard, forward-replaced.
--
-- IDENTICAL TO THE SHIPPED GUARD except for ONE added alternative in the writer
-- alternation: f01_insert_document_source_provenance. Every existing name is
-- preserved verbatim — install_policy, apply_observation, record_artifact,
-- record_proposal, record_document, record_hold, record_deletion_evaluation,
-- register_derivative_link, insert_derivative_link, claim_idempotency and
-- settle_idempotency — so no relation loses the protection it has today.
--
-- CREATE OR REPLACE, NEVER DROP. A DROP would have to cascade through seventeen
-- triggers and would leave every F01 relation momentarily unguarded inside the
-- transaction that reinstalled them. Replacing the body in place changes the
-- check and touches no trigger, and CREATE OR REPLACE also preserves the existing
-- ACL, so the revoke domain.sql performed on this function still stands.
--
-- A LATER domain.sql RE-APPLY REVERTS THIS ONE, and it is the reversion that fails
-- CLOSED: the old alternation does not name insert_document_source_provenance, so
-- every provenance insert is refused as direct DML and every document completion
-- with it. Loud and safe, unlike the other two reversions the head of this file
-- lists. The fix is the same in all three cases — fold this hunk into domain.sql.
-- ===========================================================================

CREATE OR REPLACE FUNCTION ops.f01_guard_direct_dml()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_context text;
BEGIN
  GET DIAGNOSTICS v_context = PG_CONTEXT;
  -- Every frame naming this guard itself is discounted; what must remain is a
  -- frame naming one of the registered writers.
  IF regexp_replace(v_context, 'PL/pgSQL function (ops\.)?f01_guard_direct_dml\(\)[^\n]*', '', 'g')
       !~ 'PL/pgSQL function (ops\.)?f01_(install_policy|apply_observation|record_artifact|record_proposal|record_document|record_hold|record_deletion_evaluation|register_derivative_link|insert_derivative_link|insert_document_source_provenance|claim_idempotency|settle_idempotency)\('
  THEN
    RAISE EXCEPTION 'f01_direct_dml_refused: %.% is written only through the registered ops.f01_* writers',
      TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

-- ===========================================================================
-- 1.1 The reserved-kind list, forward-replaced.
--
-- THE PUBLIC SURFACE MUST REFUSE A KIND THIS SCHEMA'S OWN WRITER PRODUCES, and
-- from this file on that is two kinds rather than one.
--
-- WHAT WAS OPEN. ops.f01_record_document below registers an ops.f01_derivative_link
-- row whose identity is ('f01_document_version', '<document_id>:<version_no>').
-- That identity is PREDICTABLE — it is the document's own name and version, not a
-- digest of anything the server holds — and the identity index is unique per
-- (tenant, kind, id) over an append-only table with no release path. Until this
-- replacement, any principal holding EXECUTE on the public
-- ops.f01_register_derivative_link could:
--
--   * PRE-CLAIM ('f01_document_version', 'some-doc:1') against an artifact of its
--     choosing BEFORE that document version was ever written, after which the
--     genuine completion of that version raises f01_derivative_source_conflict for
--     ever — and a provenance edge asserting a document came from an artifact it
--     did not sits in the record layer meanwhile; or
--   * attempt to REPOINT an already-registered document version at another
--     artifact, which refuses today but for the wrong reason and only because the
--     first link happened to exist first.
--
-- THE LIST IS REPLACED, NOT APPENDED TO, and it must therefore keep every kind it
-- already had. CREATE OR REPLACE substitutes the whole body: dropping
-- 'f01_parsed_proposal' here would reopen exactly the hole that guard was written
-- for. Both kinds are named below and the fixture asserts both.
--
-- THE PRIVATE HALF IS UNTOUCHED, deliberately. ops.f01_insert_derivative_link
-- consults no reserved list, because it is reached only from inside a definer
-- writer in this schema — ops.f01_record_proposal for proposals, and
-- ops.f01_record_document for documents — which is the only place these kinds are
-- legitimately produced. Refusing there would break the very paths that must
-- write them.
--
-- CREATE OR REPLACE preserves the existing ACL, so the grants domain.sql made on
-- this function still stand and this file widens nothing.
-- ===========================================================================

CREATE OR REPLACE FUNCTION ops.f01_reserved_derivative_kinds()
RETURNS text[]
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT ARRAY['f01_parsed_proposal', 'f01_document_version']::text[] $$;

DO $reserved$
BEGIN
  -- Read back rather than assumed, for the same reason every digest here is
  -- recomputed: a replacement that silently dropped a kind would be a hole with a
  -- successful apply in front of it.
  IF NOT ('f01_parsed_proposal' = ANY (ops.f01_reserved_derivative_kinds()))
     OR NOT ('f01_document_version' = ANY (ops.f01_reserved_derivative_kinds())) THEN
    RAISE EXCEPTION 'f01_reserved_kind_regression: the reserved-kind list must name both internally produced kinds, and names %',
      array_to_string(ops.f01_reserved_derivative_kinds(), ', ') USING ERRCODE = '42501';
  END IF;
END;
$reserved$;

-- ===========================================================================
-- 1.2 The text rules, as functions, so there is one copy of each.
--
-- WHY THESE EXIST AT ALL. The Node module refuses malformed free text before it
-- ever builds a record; these are the half that holds whatever calls the writer.
-- They are used BOTH by the CHECK constraints in section 2 and by the named
-- refusals in the private inserter in section 4, so the predicate has exactly one
-- copy and a caller still learns what it did rather than reading a constraint name.
--
-- THE LENGTH BOUND IS COUNTED IN UTF-16 CODE UNITS, NOT CODE POINTS, and that is
-- the whole reason f01_docsource_utf16_length exists rather than a plain length().
-- JavaScript's String.prototype.length counts UTF-16 code units, so the module
-- refuses a basis statement of 512 astral characters — 1024 units. PostgreSQL's
-- length() counts CODE POINTS and would have admitted it: the SQL half would then
-- be SILENTLY WEAKER than the Node half for exactly the text most likely to be
-- pasted in from somewhere else. The arithmetic below is
--
--     utf16 = 2 * code_points - non_astral_code_points
--           = code_points + astral_code_points
--
-- which is the same number String.prototype.length reports, for every string. The
-- Node suite pins both halves of that equality.
--
-- WRITTEN AS UNICODE ESCAPES, NEVER AS LITERAL CHARACTERS. A file that embedded
-- the zero-width and bidirectional characters it exists to refuse would read as
-- binary to file(1), rg and git diff, and the schema that refuses invisible
-- characters in its inputs could not itself be reviewed as text. The classes below
-- are U& literals with UESCAPE '!' rather than backslash escapes, for two reasons:
-- the file stays plain ASCII either way, and '!' cannot be confused with the
-- REGEX's own backslash. A bracket expression written with backslash-u escapes has
-- to be read twice to work out which layer resolves it, and read wrong — one
-- backslash where two were meant, or two where one was — it quietly matches a
-- literal backslash and the letter u instead of the character nobody can see. The
-- string lexer resolves the U& form before the regex engine is handed anything, so
-- the pattern contains exactly the characters named here and nothing else.
--
-- ONE JS RULE IS ABSENT ON PURPOSE. The module refuses unpaired surrogates; a
-- PostgreSQL text value in a UTF8 database cannot contain one, so there is nothing
-- here to check and a check would be theatre.
-- ===========================================================================

CREATE OR REPLACE FUNCTION ops.f01_docsource_utf16_length(p_text text)
RETURNS integer
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
  SELECT 2 * length(p_text)
         - length(regexp_replace(p_text, U&'[^!0001-!ffff]' UESCAPE '!', '', 'g'))
$$;

/**
 * Free text a human may later have to read and weigh: non-empty, bounded in the
 * same units the module bounds it in, canonical, visible and trimmed.
 *
 * The trimming set is spelled out rather than left to btrim's default, which
 * trims the SPACE character alone. JavaScript's trim() removes U+00A0 and the
 * U+2000 block too, so a default-set mirror would accept a statement the module
 * refuses — and a leading no-break space is precisely the invisible difference
 * that makes two statements look identical and hash differently.
 */
CREATE OR REPLACE FUNCTION ops.f01_docsource_is_safe_text(p_text text, p_max_utf16 integer)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
  SELECT length(p_text) > 0
     AND ops.f01_docsource_utf16_length(p_text) <= p_max_utf16
     -- C0 and C1 controls, zero-width and directional formats, the invisible
     -- operators, the bidirectional isolates and the byte-order mark.
     AND p_text !~ U&'[!0001-!001f!007f-!009f!200b-!200f!202a-!202e!2060-!2064!2066-!2069!feff]'
                   UESCAPE '!'
     -- Refused rather than normalized: normalizing would store bytes nobody wrote
     -- and change the digest the record hashes to.
     AND p_text = normalize(p_text, NFC)
     -- The trimming set, spelled out: SPACE, NO-BREAK SPACE, OGHAM SPACE MARK, the
     -- EN QUAD .. HAIR SPACE block, LINE and PARAGRAPH SEPARATOR, NARROW NO-BREAK
     -- SPACE, MEDIUM MATHEMATICAL SPACE and IDEOGRAPHIC SPACE. Every other
     -- character JavaScript's trim() removes is a control character, and those are
     -- refused outright above.
     AND p_text !~ U&'^[!0020!00a0!1680!2000-!200a!2028!2029!202f!205f!3000]' UESCAPE '!'
     AND p_text !~ U&'[!0020!00a0!1680!2000-!200a!2028!2029!202f!205f!3000]$' UESCAPE '!'
$$;

/**
 * The kernel's external-identifier alphabet, character for character. ASCII only,
 * so code points, UTF-16 code units and bytes are the same count and the bound
 * needs no reconciliation.
 */
CREATE OR REPLACE FUNCTION ops.f01_docsource_is_external_ident(p_text text, p_max integer)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
  SELECT length(p_text) BETWEEN 1 AND p_max
     AND p_text ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]*$'
$$;

-- ===========================================================================
-- 2. The relation.
--
-- WHAT A ROW HERE IS. One document VERSION's statement about where that version
-- came from. Exactly one row per stored document version, written in the same
-- transaction as the version itself.
--
-- WHY IT EXISTS AT ALL, given ops.f01_derivative_link. Because an ABSENT link has
-- always meant nothing. A document with no link row is indistinguishable from a
-- document nobody registered, a document produced before this rule existed, and a
-- document that genuinely has no corporate source because somebody wrote it here.
-- Those are three different facts and the record layer has to be able to say
-- which. So the derived case gets a link row AND a provenance row; the other two
-- get a provenance row that says, in bytes that hash, which of them is true.
--
-- WHAT A ROW HERE IS NOT. It is not coverage, not an inventory, not a deletion
-- permission, and never an inference. The four claims a derivative link may not
-- make are refused here too, and two more are added: a row may not say its source
-- artifact was inferred from the document's own bytes, and may not say it was
-- inferred from the OneDrive identity. Both are stored as false and CHECK-bound,
-- so the prohibition is a property of the row rather than a promise in a handler.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ops.f01_document_source_provenance (
  provenance_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  document_id       text NOT NULL,
  version_no        integer NOT NULL,
  document_digest   text NOT NULL REFERENCES ops.f01_document_version (document_digest),
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  provenance_digest text NOT NULL,
  provenance_state  text NOT NULL,
  source_artifact_digest text REFERENCES ops.f01_corporate_artifact (artifact_digest),
  derivative_link_digest text REFERENCES ops.f01_derivative_link (link_digest),
  derivative_kind   text,
  derivative_id     text,
  producer_workflow text,
  producer_run_ref  text,
  basis_statement   text,
  -- Nullable for the same reason a hold's and a derivative link's are: stating
  -- where one document came from is a fact about two records and does not depend
  -- on a field-authority registry existing.
  policy_digest     text,
  actor_slug        text NOT NULL,
  recorded_at_text  text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL
  -- NOT ONE CHECK CONSTRAINT IS WRITTEN HERE. Every one of them is added by name
  -- below, and the block under the indexes says why.
);

-- ONE STATEMENT PER DOCUMENT VERSION, on both of the identities a version has.
-- The digest index makes the statement unique for the exact stored bytes; the
-- (document_id, version_no) index makes it unique for the version identity, so a
-- second statement about version 2 of a document cannot land even if somebody
-- managed to produce different bytes for it.
CREATE UNIQUE INDEX IF NOT EXISTS f01_docsource_digest_uq
  ON ops.f01_document_source_provenance (provenance_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_docsource_document_digest_uq
  ON ops.f01_document_source_provenance (tenant, document_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_docsource_version_uq
  ON ops.f01_document_source_provenance (tenant, document_id, version_no);
-- One document version, one derivative link. The link table's own
-- (tenant, derivative_kind, derivative_id) index already refuses a second link
-- for the same version; this refuses two versions sharing one link.
CREATE UNIQUE INDEX IF NOT EXISTS f01_docsource_link_uq
  ON ops.f01_document_source_provenance (tenant, derivative_link_digest)
  WHERE derivative_link_digest IS NOT NULL;

-- ===========================================================================
-- 2.1 Every CHECK constraint, added BY NAME.
--
-- WHY NOT INSIDE THE CREATE TABLE. Because CREATE TABLE IF NOT EXISTS leaves an
-- already-installed table completely untouched, constraints written inside it
-- reach FRESH DATABASES ONLY. On any database where the table already exists —
-- which, for a relation nothing else creates, means one where an earlier revision
-- of this same file was applied — a predicate that was tightened, corrected or
-- added since would silently not be there, and the apply would still report
-- success. That is the worst of both worlds: a constraint everybody believes is
-- enforced and nothing enforces. DROP CONSTRAINT IF EXISTS followed by ADD
-- CONSTRAINT lands on both, and there is still exactly ONE copy of each predicate
-- in this file.
--
-- THE ADDS ARE VALIDATING, deliberately, and this is the one place that choice
-- could bite. domain.sql uses NOT VALID where it tightens a constraint over rows
-- written before the tightening existed, because aborting a whole schema apply to
-- say something about history helps nobody. Here the calculus is the opposite: any
-- row in this table was written by an earlier revision of the writer three
-- sections below, under this same contract, so a row that fails is a row this
-- file's own writer produced and could produce again. That aborts the apply, by
-- name, which is the correct and loud failure. A future revision that genuinely
-- tightens a predicate against rows it cannot re-derive must make that call then,
-- in that revision, and say so.
--
-- COLUMNS ARE NOT COVERED BY THIS. CREATE TABLE IF NOT EXISTS will not add a
-- column to an existing table either, and no loop below does. This revision adds
-- none; a revision that does must ship an explicit ALTER TABLE ... ADD COLUMN, and
-- the absence of one here is a statement rather than an oversight.
-- ===========================================================================

DO $constraints$
DECLARE c record;
BEGIN
  FOR c IN
    SELECT * FROM (VALUES
      ('f01_docsource_tenant', $c$
         tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'
       $c$),
      ('f01_docsource_kind', $c$
         envelope ->> 'record_kind' = 'stored_document_source_provenance'
       $c$),
      -- The stored bytes name the contract they were written under. Without this a
      -- record from some other schema version could occupy this relation and every
      -- digest check below would still pass, because they only ever ask whether the
      -- bytes hash to their own claim.
      ('f01_docsource_schema_version', $c$
         envelope -> 'record' ->> 'schema_version'
           = 'doctorcre-v5-f01-stored-document-source-provenance.v1'
         AND envelope ->> 'schema_version' = 'doctorcre-v5-f01-stored-record-envelope.v1'
       $c$),
      ('f01_docsource_envelope_digest', $c$
         envelope_digest = ops.f01_digest_jsonb(envelope)
       $c$),
      ('f01_docsource_record_digest', $c$
         provenance_digest = ops.f01_digest_jsonb(envelope -> 'record')
         AND provenance_digest = envelope ->> 'record_digest'
       $c$),
      ('f01_docsource_binding', $c$
         document_id = envelope -> 'record' ->> 'document_id'
         AND version_no = (envelope -> 'record' ->> 'version_no')::integer
         AND document_digest = envelope -> 'record' ->> 'document_digest'
         AND provenance_state = envelope -> 'record' ->> 'provenance_state'
         AND source_artifact_digest
               IS NOT DISTINCT FROM (envelope -> 'record' ->> 'source_artifact_digest')
         AND derivative_link_digest
               IS NOT DISTINCT FROM (envelope -> 'record' ->> 'derivative_link_digest')
         AND derivative_kind IS NOT DISTINCT FROM (envelope -> 'record' ->> 'derivative_kind')
         AND derivative_id IS NOT DISTINCT FROM (envelope -> 'record' ->> 'derivative_id')
         AND producer_workflow
               IS NOT DISTINCT FROM (envelope -> 'record' ->> 'producer_workflow')
         AND producer_run_ref IS NOT DISTINCT FROM (envelope -> 'record' ->> 'producer_run_ref')
         AND basis_statement IS NOT DISTINCT FROM (envelope -> 'record' ->> 'basis_statement')
         AND actor_slug = envelope -> 'record' ->> 'recorded_by'
         AND recorded_at_text = envelope -> 'record' ->> 'recorded_at'
       $c$),
      -- THE THREE HONEST ANSWERS, and no fourth. "Probably derived", "no link
      -- found" and "not recorded yet" are all legacy_provenance_unknown, and none
      -- of them is original_first_party.
      ('f01_docsource_state', $c$
         provenance_state IN ('derived_from_stored_artifact',
                              'original_first_party',
                              'legacy_provenance_unknown')
       $c$),
      -- A DERIVED ROW CARRIES ITS WHOLE ORIGIN AND A NON-DERIVED ROW CARRIES NONE
      -- OF IT. There is no half-derived shape: a source artifact with no link, or a
      -- link with no source, would each be a provenance edge that points somewhere
      -- the other half does not agree with. A claim with no stated basis is the one
      -- that gets read as fact by whoever finds it next, so the basis is required
      -- rather than optional for both non-derived answers.
      ('f01_docsource_derived_completeness', $c$
         (provenance_state = 'derived_from_stored_artifact'
          AND source_artifact_digest IS NOT NULL
          AND derivative_link_digest IS NOT NULL
          AND derivative_kind = 'f01_document_version'
          AND derivative_id = document_id || ':' || version_no::text
          AND producer_workflow IS NOT NULL
          AND producer_run_ref IS NOT NULL
          AND basis_statement IS NULL)
         OR (provenance_state <> 'derived_from_stored_artifact'
             AND source_artifact_digest IS NULL
             AND derivative_link_digest IS NULL
             AND derivative_kind IS NULL
             AND derivative_id IS NULL
             AND producer_workflow IS NULL
             AND producer_run_ref IS NULL
             AND basis_statement IS NOT NULL)
       $c$),
      ('f01_docsource_digest_shapes', $c$
         ops.f01_is_digest_ref(document_digest)
         AND (source_artifact_digest IS NULL OR ops.f01_is_digest_ref(source_artifact_digest))
         AND (derivative_link_digest IS NULL OR ops.f01_is_digest_ref(derivative_link_digest))
       $c$),
      -- THE FREE TEXT, held to the same rules the Node module holds it to: bounded
      -- in UTF-16 CODE UNITS rather than code points, canonical, visible, trimmed,
      -- and — for the two producer fields and the document id — inside the kernel's
      -- external-identifier alphabet. Section 1.2 owns the predicates.
      ('f01_docsource_text_shape', $c$
         ops.f01_docsource_is_external_ident(document_id, 128)
         AND (basis_statement IS NULL
              OR ops.f01_docsource_is_safe_text(basis_statement, 512))
         AND (producer_workflow IS NULL
              OR ops.f01_docsource_is_external_ident(producer_workflow, 128))
         AND (producer_run_ref IS NULL
              OR ops.f01_docsource_is_external_ident(producer_run_ref, 255))
         AND (derivative_id IS NULL
              OR ops.f01_docsource_is_external_ident(derivative_id, 255))
       $c$),
      ('f01_docsource_version_no', $c$ version_no >= 1 $c$),
      ('f01_docsource_recorded_at_shape', $c$
         ops.f01_is_instant_text(recorded_at_text)
       $c$),
      ('f01_docsource_idempotency', $c$
         length(idempotency_key) BETWEEN 1 AND 200
       $c$),
      -- THE SIX CLAIMS A PROVENANCE ROW MAY NEVER MAKE, asserted on the stored row
      -- and inside the hashed record both. The first four are the derivative link's
      -- own four. The last two are what "never invent a source artifact" looks like
      -- when it is a property of the database rather than a rule in a handler.
      --
      -- coalesce IS LOAD-BEARING, for the reason domain.sql's own
      -- f01_derivative_claims_nothing spells out: `->>` over an ABSENT key yields
      -- SQL NULL, NULL = 'false' is NULL, and a CHECK fails only on FALSE. Without
      -- it this constraint refuses a record that claims establishes_coverage true
      -- and ADMITS one that simply omits the key — leaving a stored statement with
      -- no self-limiting bytes for a later reader to find. '' is neither 'true' nor
      -- 'false', so silence now fails the same conjunct a contrary claim does.
      ('f01_docsource_claims_nothing', $c$
         coalesce(envelope -> 'record' ->> 'registration_is_provenance', '') = 'true'
         AND coalesce(envelope -> 'record' ->> 'is_exhaustive_inventory', '') = 'false'
         AND coalesce(envelope -> 'record' ->> 'establishes_coverage', '') = 'false'
         AND coalesce(envelope -> 'record' ->> 'permits_deletion', '') = 'false'
         AND coalesce(envelope -> 'record'
                        ->> 'source_artifact_inferred_from_document_bytes', '') = 'false'
         AND coalesce(envelope -> 'record'
                        ->> 'source_artifact_inferred_from_onedrive_identity', '') = 'false'
         AND coalesce(envelope ->> 'is_exhaustive_inventory', '') = 'false'
         AND coalesce(envelope ->> 'establishes_coverage', '') = 'false'
         AND coalesce(envelope ->> 'permits_deletion', '') = 'false'
       $c$),
      -- WHAT THIS ONE MAKES IMPOSSIBLE. A provenance statement whose derivative
      -- identity is not the (document_id, version_no) fold this contract defines —
      -- the one shape that would let two document versions compete for a single row
      -- in ops.f01_derivative_link, or let one version register itself twice under
      -- two ids. The writer refuses the same shape earlier and with a better
      -- message; this is the version that holds even if somebody edits the writer.
      ('f01_docsource_derivative_identity', $c$
         derivative_id IS NULL
         OR (derivative_id = document_id || ':' || version_no::text
             AND position(':' in document_id) = 0)
       $c$)
    ) AS t(name, predicate)
  LOOP
    EXECUTE format('ALTER TABLE ops.f01_document_source_provenance DROP CONSTRAINT IF EXISTS %I',
                   c.name);
    EXECUTE format('ALTER TABLE ops.f01_document_source_provenance ADD CONSTRAINT %I CHECK (%s)',
                   c.name, c.predicate);
  END LOOP;
END;
$constraints$;

-- Read back rather than assumed: a loop that quietly added nothing would leave a
-- table with no constraints and a clean apply in front of it.
DO $constraints_present$
DECLARE v_missing text[] := ARRAY[]::text[]; v_name text;
BEGIN
  FOREACH v_name IN ARRAY ARRAY[
    'f01_docsource_tenant', 'f01_docsource_kind', 'f01_docsource_schema_version',
    'f01_docsource_envelope_digest', 'f01_docsource_record_digest', 'f01_docsource_binding',
    'f01_docsource_state', 'f01_docsource_derived_completeness', 'f01_docsource_digest_shapes',
    'f01_docsource_text_shape', 'f01_docsource_version_no', 'f01_docsource_recorded_at_shape',
    'f01_docsource_idempotency', 'f01_docsource_claims_nothing',
    'f01_docsource_derivative_identity'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'ops.f01_document_source_provenance'::regclass
                      AND conname = v_name AND contype = 'c' AND convalidated) THEN
      v_missing := v_missing || v_name;
    END IF;
  END LOOP;
  IF cardinality(v_missing) > 0 THEN
    RAISE EXCEPTION 'f01_docsource_constraint_missing: % is absent or unvalidated on ops.f01_document_source_provenance',
      array_to_string(v_missing, ', ') USING ERRCODE = '42704';
  END IF;
END;
$constraints_present$;

-- ===========================================================================
-- 3. Guard triggers, on the same terms as every other F01 relation.
--
-- Append-only history: a provenance statement about one document version is made
-- once. A later version of the same document gets its own row; the earlier row is
-- never edited to say something different about what already happened.
-- ===========================================================================

DROP TRIGGER IF EXISTS f01_document_source_provenance_dml_guard
  ON ops.f01_document_source_provenance;
CREATE TRIGGER f01_document_source_provenance_dml_guard
  BEFORE INSERT OR UPDATE OR DELETE ON ops.f01_document_source_provenance
  FOR EACH ROW EXECUTE FUNCTION ops.f01_guard_direct_dml();

DROP TRIGGER IF EXISTS f01_document_source_provenance_truncate_guard
  ON ops.f01_document_source_provenance;
CREATE TRIGGER f01_document_source_provenance_truncate_guard
  BEFORE TRUNCATE ON ops.f01_document_source_provenance
  FOR EACH STATEMENT EXECUTE FUNCTION ops.f01_guard_no_truncate();

DROP TRIGGER IF EXISTS f01_document_source_provenance_append_only
  ON ops.f01_document_source_provenance;
CREATE TRIGGER f01_document_source_provenance_append_only
  BEFORE UPDATE OR DELETE ON ops.f01_document_source_provenance
  FOR EACH ROW EXECUTE FUNCTION ops.f01_guard_append_only();

-- ===========================================================================
-- 4. Verification helpers and the private writer.
-- ===========================================================================

/**
 * The provenance statement for one document VERSION, recomputed. NULL when none
 * is stored.
 *
 * NULL MEANS "NO STATEMENT", NEVER "NO SOURCE". Every caller of this must treat
 * an absent row the way ops.f01_derivative_coverage treats an empty link table:
 * as a question nobody has answered. The Node side refuses to turn it into
 * original_first_party, and this function returns no default that would let it.
 */
CREATE OR REPLACE FUNCTION ops.f01_document_version_source(
  p_document_id text, p_version_no integer)
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_document_source_provenance%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_document_source_provenance
   WHERE tenant = ops.f01_tenant() AND document_id = p_document_id
     AND version_no = p_version_no;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.provenance_digest, 'stored_document_source_provenance');
  IF v_row.provenance_state IS DISTINCT FROM (v_row.envelope -> 'record' ->> 'provenance_state')
     OR v_row.document_digest IS DISTINCT FROM (v_row.envelope -> 'record' ->> 'document_digest')
     OR v_row.source_artifact_digest
          IS DISTINCT FROM (v_row.envelope -> 'record' ->> 'source_artifact_digest') THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: document source provenance columns mismatch'
      USING ERRCODE = '22000';
  END IF;
  RETURN jsonb_build_object(
    'document_id', v_row.document_id,
    'version_no', v_row.version_no,
    'document_digest', v_row.document_digest,
    'provenance_state', v_row.provenance_state,
    'source_artifact_digest', v_row.source_artifact_digest,
    'derivative_link_digest', v_row.derivative_link_digest,
    'provenance_digest', v_row.provenance_digest,
    'record', v_row.envelope -> 'record',
    'envelope', v_row.envelope,
    'envelope_digest', v_row.envelope_digest,
    -- Said on every read, because the whole risk here is that somebody reads a
    -- growing provenance table as a complete picture of what was derived.
    'establishes_coverage', false,
    'is_exhaustive_inventory', false,
    'absent_statement_means_no_source', false,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

/** Every provenance statement for one document, oldest version first. */
CREATE OR REPLACE FUNCTION ops.f01_document_source_history(p_document_id text)
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_out jsonb := '[]'::jsonb;
  r ops.f01_document_source_provenance%ROWTYPE;
BEGIN
  FOR r IN SELECT * FROM ops.f01_document_source_provenance
    WHERE tenant = ops.f01_tenant() AND document_id = p_document_id
    ORDER BY version_no
  LOOP
    v_out := v_out || jsonb_build_array(
      ops.f01_document_version_source(r.document_id, r.version_no));
  END LOOP;
  RETURN v_out;
END;
$$;

/**
 * The PRIVATE half: validate and insert one document-source provenance statement.
 *
 * Private for the same reasons ops.f01_insert_derivative_link is. It is reached
 * only from inside a SECURITY DEFINER writer, where it executes as the owner
 * whatever the caller is, so no runtime EXECUTE grant is needed and any runtime
 * grant would be a hole. It takes no locks; its one caller takes them.
 *
 * BEING PRIVATE IS A NAME IN FOUR LISTS, not a property of this definition. See
 * section 6: domain.sql's grant loop and posture check, the local gate's
 * PRIVATE_HELPERS, and the core SQL fixture's least-privilege block each enumerate
 * the private helpers by hand, and this one is in none of them until the parent
 * adds it.
 *
 * IT WRITES SECOND, ALWAYS. Its foreign keys point at the document version and at
 * the derivative link, so both must already be in the transaction. That ordering
 * is not incidental: it is what makes the provenance row a statement ABOUT things
 * that exist rather than a placeholder waiting for them.
 */
CREATE OR REPLACE FUNCTION ops.f01_insert_document_source_provenance(
  p_envelope jsonb, p_actor text, p_idempotency_key text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_record jsonb;
  v_digest text;
  v_state text;
BEGIN
  IF p_envelope IS NULL THEN
    RAISE EXCEPTION 'f01_document_provenance_required: a document version completes only with a statement about where it came from'
      USING ERRCODE = '22023';
  END IF;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_document_provenance_digest_mismatch: the statement does not hash to its claim'
      USING ERRCODE = '22000';
  END IF;
  IF (v_record ->> 'recorded_by') IS DISTINCT FROM p_actor THEN
    RAISE EXCEPTION 'f01_actor_injection_refused: recorded_by is derived, never supplied'
      USING ERRCODE = '42501';
  END IF;
  v_state := v_record ->> 'provenance_state';
  IF v_state IS NULL OR v_state NOT IN ('derived_from_stored_artifact',
                                        'original_first_party',
                                        'legacy_provenance_unknown') THEN
    RAISE EXCEPTION 'f01_unknown_document_provenance_state: %', coalesce(v_state, 'none')
      USING ERRCODE = '22023';
  END IF;

  -- THE FREE TEXT IS HELD TO THE MODULE'S OWN RULES, by name. The CHECK constraint
  -- in section 2.1 enforces the same predicate — literally the same function — and
  -- would refuse this row three statements later with a constraint name. A caller
  -- deserves to learn WHICH field it got wrong, and the two halves cannot drift
  -- because there is one copy of each rule in section 1.2.
  IF (v_record ->> 'basis_statement') IS NOT NULL
     AND NOT ops.f01_docsource_is_safe_text(v_record ->> 'basis_statement', 512) THEN
    RAISE EXCEPTION 'f01_document_provenance_text_refused: basis_statement must be non-empty, canonical (NFC), free of control, invisible and bidirectional characters, free of leading and trailing whitespace, and at most 512 UTF-16 code units'
      USING ERRCODE = '22023';
  END IF;
  IF NOT ops.f01_docsource_is_external_ident(v_record ->> 'document_id', 128)
     OR ((v_record ->> 'producer_workflow') IS NOT NULL
         AND NOT ops.f01_docsource_is_external_ident(v_record ->> 'producer_workflow', 128))
     OR ((v_record ->> 'producer_run_ref') IS NOT NULL
         AND NOT ops.f01_docsource_is_external_ident(v_record ->> 'producer_run_ref', 255)) THEN
    RAISE EXCEPTION 'f01_document_provenance_text_refused: document_id, producer_workflow and producer_run_ref are external identifiers of at most 128, 128 and 255 characters'
      USING ERRCODE = '22023';
  END IF;
  -- THE SOURCE IS LOADED, exactly as it is for a derivative link. Provenance
  -- pointing at an artifact nobody stored is not provenance, and naming a digest
  -- never brings one into existence. The foreign key is the structural half of
  -- this; the named refusal is so a caller learns what it did.
  IF v_state = 'derived_from_stored_artifact'
     AND ops.f01_stored_artifact(v_record ->> 'source_artifact_digest') IS NULL THEN
    RAISE EXCEPTION 'f01_unknown_artifact: a derived document names a stored artifact'
      USING ERRCODE = '23503';
  END IF;

  INSERT INTO ops.f01_document_source_provenance
    (tenant, document_id, version_no, document_digest, envelope, envelope_digest,
     provenance_digest, provenance_state, source_artifact_digest, derivative_link_digest,
     derivative_kind, derivative_id, producer_workflow, producer_run_ref, basis_statement,
     policy_digest, actor_slug, recorded_at_text, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), v_record ->> 'document_id',
          (v_record ->> 'version_no')::integer, v_record ->> 'document_digest',
          p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest, v_state,
          v_record ->> 'source_artifact_digest', v_record ->> 'derivative_link_digest',
          v_record ->> 'derivative_kind', v_record ->> 'derivative_id',
          v_record ->> 'producer_workflow', v_record ->> 'producer_run_ref',
          v_record ->> 'basis_statement',
          ops.f01_current_policy_digest(), p_actor,
          v_record ->> 'recorded_at', ops.f01_instant(v_record ->> 'recorded_at'),
          p_idempotency_key);

  RETURN jsonb_build_object(
    'provenance_digest', v_digest,
    'provenance_state', v_state,
    'establishes_coverage', false,
    'is_exhaustive_inventory', false,
    'permits_deletion', false,
    'source_artifact_inferred_from_document_bytes', false,
    'source_artifact_inferred_from_onedrive_identity', false,
    'readback', ops.f01_document_version_source(
      v_record ->> 'document_id', (v_record ->> 'version_no')::integer));
END;
$$;

-- ===========================================================================
-- 5. record-document-identity, forward-replaced.
--
-- THE FOUR-ARGUMENT FORM IS DROPPED, not left beside this one. Two overloads
-- would make the four-argument call ambiguous AND would leave a path that
-- completes a derived document with no provenance edge, which is the whole thing
-- this exists to prevent. This is the same move ops.f01_record_proposal made when
-- its derivative-link envelope became mandatory.
--
-- THE LOCK ORDER, and it is an AMENDMENT the parent must re-review against the
-- hierarchy stated in domain.sql section 9. That note says no writer takes two
-- tier-3 keys. This one does, when the document is derived:
--
--   tier 1   f01:request:<tenant>:<key>                     (the idempotency claim)
--   tier 3a  f01:artifact-retention:<tenant>:<source digest> (derived documents only)
--   tier 3b  f01:document:<tenant>:<document_id>
--   tier 4   f01:derivative:<tenant>:<kind>:<id>            (derived documents only)
--
-- WHY THAT IS STILL ACYCLIC. The two tier-3 keys are taken in a FIXED order —
-- retention before document, never the reverse — and no other writer takes
-- f01:document: at all, so no other writer can hold it while waiting for
-- f01:artifact-retention:. Every writer that takes f01:artifact-retention:
-- (f01_record_hold, f01_record_deletion_evaluation, f01_register_derivative_link,
-- f01_record_proposal and this one) acquires it FIRST among tier 3 and above. The
-- observed acquisition orders therefore remain a strict order with no back edge.
--
-- WHY THE RETENTION KEY AT ALL. For the same reason f01_record_proposal takes it:
-- this writer registers a derivative link, and a link must not land underneath a
-- deletion evaluation that has already read the artifact's coverage and bound its
-- record to the digest of it.
--
-- EVERY OLD CHECK IS KEPT: the record-digest recomputation, the derived actor,
-- the document advisory lock, the two-sided compare-and-swap against the stored
-- current pointer AND the record's own prior claim, the current-pointer upsert
-- and the verified readback.
--
-- WHICH REFUSAL ARRIVES FIRST, said plainly because the fixture asserts it and a
-- reader would otherwise expect the other one. A second completion of a document
-- version that already exists cannot reach ops.f01_insert_derivative_link's
-- f01_derivative_source_conflict, because the document version is inserted first
-- and ops.f01_document_version_identity_uq — unique on (tenant, document_id,
-- version_no) — refuses it there. The link identity and the document identity are
-- the same identity by construction: derivative_id IS document_id || ':' ||
-- version_no. So through THIS writer the (kind, id) conflict is unreachable for
-- documents, and that is a strengthening rather than a gap: there is no order in
-- which a second, contrary statement about a stored version lands. The conflict
-- refusal still guards every other producer's kinds, through the public surface
-- and through ops.f01_record_proposal, and the fixture proves it there rather than
-- pretending to prove it here.
-- ===========================================================================

DROP FUNCTION IF EXISTS ops.f01_record_document(jsonb, text, text, text);

CREATE OR REPLACE FUNCTION ops.f01_record_document(
  p_envelope jsonb, p_provenance jsonb, p_derivative_link jsonb,
  p_expected_prior_document_digest text,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_record jsonb;
  v_provenance jsonb;
  v_derivative jsonb;
  v_digest text;
  v_document_id text;
  v_version_no integer;
  v_state text;
  v_prior text;
  v_version_id bigint;
  v_row ops.f01_document_version%ROWTYPE;
  v_registration jsonb;
  v_statement jsonb;
  v_result jsonb;
BEGIN
  -- A read-only principal is not a producer workflow, named here as well as by
  -- the absent EXECUTE grant, because either alone is a single point of failure.
  -- Same check ops.f01_register_derivative_link makes, for the same reason: this
  -- writer now registers derivative links too.
  IF session_user = 'carr_reader' THEN
    RAISE EXCEPTION 'f01_producer_principal_refused: record-document-identity completes a derived record and is written by producer workflows, not by a read-only principal'
      USING ERRCODE = '42501';
  END IF;

  v_replay := ops.f01_claim_idempotency('record-document-identity', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;

  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_document_digest_mismatch' USING ERRCODE = '22000';
  END IF;
  v_document_id := v_record -> 'neon_identity' ->> 'document_id';
  v_version_no := (v_record -> 'neon_identity' ->> 'version_no')::integer;

  -- THE PROVENANCE STATEMENT IS MANDATORY, and that is the settled producer rule
  -- made structural for documents. A caller cannot skip it by passing NULL, and
  -- cannot forge it, because it must name THIS document version and THIS digest.
  IF p_provenance IS NULL THEN
    RAISE EXCEPTION 'f01_document_provenance_required: a document version completes only with a statement about where it came from'
      USING ERRCODE = '22023';
  END IF;
  v_provenance := p_provenance -> 'record';
  IF (p_provenance ->> 'record_digest') IS DISTINCT FROM ops.f01_digest_jsonb(v_provenance) THEN
    RAISE EXCEPTION 'f01_document_provenance_digest_mismatch: the statement does not hash to its claim'
      USING ERRCODE = '22000';
  END IF;
  IF (v_provenance ->> 'document_id') IS DISTINCT FROM v_document_id
     OR (v_provenance ->> 'version_no')::integer IS DISTINCT FROM v_version_no
     OR (v_provenance ->> 'document_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_document_provenance_not_bound_to_document: the statement must name this document version and its exact stored bytes'
      USING ERRCODE = '22000';
  END IF;
  v_state := v_provenance ->> 'provenance_state';

  -- NEVER INVENTED FROM THE DOCUMENT'S OWN BYTES OR ITS ONEDRIVE IDENTITY. The
  -- three digests below are the document, not its origin; a source artifact that
  -- equals one of them is an inference dressed as a fact. The Node side refuses
  -- the same shape earlier and with a better message; this is the half that holds
  -- whatever calls the function.
  IF v_state = 'derived_from_stored_artifact'
     AND (v_provenance ->> 'source_artifact_digest') IN (
           coalesce(v_record -> 'neon_identity' ->> 'content_digest', ''),
           coalesce(v_record -> 'object_storage_identity' ->> 'content_digest', ''),
           coalesce(v_record -> 'onedrive_identity' ->> 'content_digest', '')) THEN
    RAISE EXCEPTION 'f01_document_bytes_are_not_a_source_artifact: a document''s own content digest is not the artifact it came from'
      USING ERRCODE = '22000';
  END IF;

  -- THE PROVENANCE EDGE IS CHECKED BEFORE ANYTHING IS WRITTEN, and it must be
  -- about THIS document version and the source the statement names. A link naming
  -- some other derivative, or some other source, would satisfy "a link was
  -- supplied" while registering the provenance of something else entirely.
  IF v_state = 'derived_from_stored_artifact' THEN
    IF p_derivative_link IS NULL THEN
      RAISE EXCEPTION 'f01_derivative_link_required: a derived document version completes only with its source registration'
        USING ERRCODE = '22023';
    END IF;
    v_derivative := p_derivative_link -> 'record';
    IF (v_derivative ->> 'derivative_kind') IS DISTINCT FROM 'f01_document_version'
       OR (v_derivative ->> 'derivative_id')
            IS DISTINCT FROM (v_document_id || ':' || v_version_no::text)
       OR (v_derivative ->> 'derivative_content_digest') IS DISTINCT FROM v_digest
       OR (v_derivative ->> 'source_artifact_digest')
            IS DISTINCT FROM (v_provenance ->> 'source_artifact_digest') THEN
      RAISE EXCEPTION 'f01_derivative_link_not_bound_to_document: the registration must name this document version and the source the statement names'
        USING ERRCODE = '22000';
    END IF;
  ELSE
    -- A link row for a document with no source would be provenance pointing at
    -- nothing, and it is the shape somebody reaches for when they want an
    -- original to look registered.
    IF p_derivative_link IS NOT NULL THEN
      RAISE EXCEPTION 'f01_derivative_link_on_non_derived_document: % declares no source artifact, so it registers no derivative link',
        v_document_id USING ERRCODE = '22023';
    END IF;
    IF (v_provenance ->> 'source_artifact_digest') IS NOT NULL THEN
      RAISE EXCEPTION 'f01_source_named_by_non_derived_document: % declares state % and still names a source artifact',
        v_document_id, v_state USING ERRCODE = '22023';
    END IF;
  END IF;

  -- tier 3a, then 3b, then 4. See the lock note in this section's header.
  IF v_state = 'derived_from_stored_artifact' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended(
      'f01:artifact-retention:' || ops.f01_tenant() || ':' ||
      (v_provenance ->> 'source_artifact_digest'), 0));
  END IF;
  PERFORM pg_advisory_xact_lock(
    hashtextextended('f01:document:' || ops.f01_tenant() || ':' || v_document_id, 0));
  IF v_state = 'derived_from_stored_artifact' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended(
      'f01:derivative:' || ops.f01_tenant() || ':' ||
      (v_derivative ->> 'derivative_kind') || ':' || (v_derivative ->> 'derivative_id'), 0));
  END IF;

  SELECT document_digest INTO v_prior FROM ops.f01_document_current
   WHERE tenant = ops.f01_tenant() AND document_id = v_document_id;
  IF v_prior IS DISTINCT FROM p_expected_prior_document_digest
     OR v_prior IS DISTINCT FROM (v_record ->> 'prior_document_digest') THEN
    RAISE EXCEPTION 'f01_stale_document_digest: current is %, the caller decided against %',
      coalesce(v_prior, 'none'), coalesce(p_expected_prior_document_digest, 'none')
      USING ERRCODE = '40001';
  END IF;

  INSERT INTO ops.f01_document_version
    (tenant, document_id, version_no, envelope, envelope_digest, document_digest,
     prior_document_digest, document_class, content_digest,
     preparation_state, delivery_state, signature_state, validity_state, version_state,
     object_key, object_sealed, onedrive_drive_id, onedrive_item_id, onedrive_filing_state,
     official_filing_state, policy_digest, actor_slug, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), v_document_id, v_version_no,
          p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
          v_record ->> 'prior_document_digest', v_record ->> 'document_class',
          v_record -> 'neon_identity' ->> 'content_digest',
          v_record ->> 'preparation_state', v_record ->> 'delivery_state',
          v_record ->> 'signature_state', v_record ->> 'validity_state',
          v_record ->> 'version_state',
          v_record -> 'object_storage_identity' ->> 'object_key',
          (v_record -> 'object_storage_identity' ->> 'sealed')::boolean,
          v_record -> 'onedrive_identity' ->> 'drive_id',
          v_record -> 'onedrive_identity' ->> 'item_id',
          v_record -> 'onedrive_identity' ->> 'filing_state',
          v_record ->> 'official_filing_state',
          ops.f01_current_policy_digest(), v_actor, now(), p_idempotency_key)
  RETURNING document_version_id INTO v_version_id;

  INSERT INTO ops.f01_document_current
    (tenant, document_id, document_version_id, document_digest, updated_by, updated_at)
  VALUES (ops.f01_tenant(), v_document_id, v_version_id, v_digest, v_actor, now())
  ON CONFLICT (tenant, document_id) DO UPDATE
    SET document_version_id = EXCLUDED.document_version_id,
        document_digest = EXCLUDED.document_digest,
        updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;

  -- Same transaction, same idempotency key: either the document version, its
  -- provenance statement and — when it is derived — its source registration all
  -- land, or none of them does.
  IF v_state = 'derived_from_stored_artifact' THEN
    v_registration := ops.f01_insert_derivative_link(p_derivative_link, v_actor, p_idempotency_key);
    -- A link that was ALREADY registered for this (kind, id) under different
    -- bytes cannot be the link this statement names, and the statement's foreign
    -- key would fail three statements later with a constraint name instead of a
    -- reason. Refuse here, by name.
    IF (v_registration ->> 'link_digest')
         IS DISTINCT FROM (v_provenance ->> 'derivative_link_digest') THEN
      RAISE EXCEPTION 'f01_derivative_link_already_bound_to_document: % already carries link %, not %',
        v_derivative ->> 'derivative_id', v_registration ->> 'link_digest',
        coalesce(v_provenance ->> 'derivative_link_digest', 'none')
        USING ERRCODE = '23505';
    END IF;
  ELSIF (v_provenance ->> 'derivative_link_digest') IS NOT NULL THEN
    RAISE EXCEPTION 'f01_document_provenance_names_absent_link: a non-derived statement may not name a derivative link'
      USING ERRCODE = '22000';
  END IF;

  v_statement := ops.f01_insert_document_source_provenance(
    p_provenance, v_actor, p_idempotency_key);

  SELECT * INTO v_row FROM ops.f01_document_version WHERE document_version_id = v_version_id;
  v_result := jsonb_build_object(
    'operation', 'record-document-identity', 'outcome', 'recorded',
    'actor_slug', v_actor, 'document_digest', v_digest,
    'official_filing_state', v_row.official_filing_state,
    'object_storage_success_implies_official_filing', false,
    'neon_success_implies_official_filing', false,
    'provenance_state', v_state,
    'provenance_digest', v_statement ->> 'provenance_digest',
    'derivative_link_digest', v_registration ->> 'link_digest',
    'derivative_registration_bound', (v_state = 'derived_from_stored_artifact'),
    -- Said on every answer, because the whole risk here is that somebody reads a
    -- growing set of registered documents as a complete picture of what was
    -- derived from an artifact. The coverage readback beside it is the proof
    -- rather than the promise: it still reads 'unknown'.
    'establishes_coverage', false,
    'is_exhaustive_inventory', false,
    'permits_deletion', false,
    'absent_statement_means_no_source', false,
    'derivative_coverage', CASE WHEN v_state = 'derived_from_stored_artifact'
      THEN ops.f01_derivative_coverage(v_provenance ->> 'source_artifact_digest') END,
    'readback', ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                        v_row.document_digest, 'stored_document_version'),
    'provenance_readback', v_statement -> 'readback',
    'external_effects', false);
  RETURN ops.f01_settle_idempotency('record-document-identity', p_idempotency_key, v_result);
END;
$$;

-- ===========================================================================
-- 6. Grants, and the posture readback.
--
-- THIS FILE GRANTS NOBODY ANYTHING THEY DID NOT ALREADY HAVE. The replaced
-- ops.f01_record_document lost its ACL with the DROP, so its previous grants are
-- restored exactly: carr_writer and the two authority logins may execute it,
-- carr_reader may not. The two new read functions and the three pure text helpers
-- follow the read posture every other F01 reader has — they decide nothing, hold
-- nothing and answer about their argument alone, and the local gate expects every
-- ops.f01_* function that is not a private helper or a guard to be reachable by
-- all four principals. The new private helper receives EXECUTE from nobody, ever,
-- exactly like ops.f01_insert_derivative_link.
--
-- ops.f01_reserved_derivative_kinds is REPLACED rather than created, so it keeps
-- the ACL domain.sql gave it. It appears in the loops below anyway: a function this
-- file changed the meaning of should have its posture re-asserted here rather than
-- assumed from another file.
--
-- PARENT WIRING, and it is a real obligation rather than a note. Each of these is
-- a place a name is enumerated BY HAND, so none of them knows about this hunk
-- until somebody adds it:
--
--   domain.sql section 10 grant loop      the private-helper skip list
--       f01_claim_idempotency, f01_settle_idempotency, f01_insert_derivative_link
--       + f01_insert_document_source_provenance
--   domain.sql section 11 check (b)       the same three names again, + the same
--                                         addition
--   domain.sql section 9.4.1              ops.f01_reserved_derivative_kinds() must
--                                         name 'f01_document_version' as well as
--                                         'f01_parsed_proposal' (section 1.1 here)
--   domain.sql section 1 guard            f01_guard_direct_dml's writer alternation
--                                         must name insert_document_source_provenance
--   domain.sql section 9.5                ops.f01_record_document must BE the
--                                         six-argument form, not be re-created as
--                                         the four-argument one beside it
--   ops/record-source-authority-local-pg-gate.py
--       PRIVATE_HELPERS                   add f01_insert_document_source_provenance.
--                                         THIS IS THE MIRROR THAT MATTERS: the gate
--                                         expects EXECUTE=true for every ops.f01_*
--                                         function that is not a private helper, a
--                                         guard, or on a role exclusion list, so a
--                                         correctly private helper that is missing
--                                         from PRIVATE_HELPERS makes the gate FAIL a
--                                         correct schema. READER_FORBIDDEN is NOT the
--                                         list for it — that one is about the eight
--                                         writers plus f01_replay_outcome and
--                                         f01_require_authority_principal, and adding
--                                         a private helper there would still leave
--                                         carr_writer expected to hold EXECUTE on it.
--       REQUIRED_FUNCTIONS                optional, and worth it: naming the new
--                                         helper and the two readers there is what
--                                         stops a schema that shipped the table but
--                                         not the writer from passing vacuously.
--   mcp-server/test/record-source-authority-postgres.sql
--                                         the least-privilege block's own by-name
--                                         private-helper list.
--
-- NOTHING BELOW CATCHES A LATER domain.sql RE-APPLY. The readback at the foot of
-- this section runs once, while this file applies, and it is a hard stop for what
-- it can see THEN: PUBLIC holding EXECUTE, the helper being runtime-reachable, a
-- runtime DML grant, a reader that can complete a document, a surviving
-- four-argument writer. A domain.sql applied afterwards re-runs only ITS checks,
-- which do not know these objects exist. See the ORDER OF APPLICATION note at the
-- head of this file for the three things that silently revert.
-- ===========================================================================

DO $grants$
DECLARE f record; r text;
BEGIN
  -- PUBLIC IS REVOKED UNCONDITIONALLY, before any role is considered, on every
  -- function this file created or replaced.
  FOR f IN SELECT p.oid::regprocedure AS signature, p.proname FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'ops'
      AND p.proname IN ('f01_record_document', 'f01_guard_direct_dml',
                        'f01_insert_document_source_provenance',
                        'f01_document_version_source', 'f01_document_source_history',
                        'f01_reserved_derivative_kinds', 'f01_docsource_utf16_length',
                        'f01_docsource_is_safe_text', 'f01_docsource_is_external_ident')
  LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.signature);
  END LOOP;
  EXECUTE 'REVOKE ALL ON TABLE ops.f01_document_source_provenance FROM PUBLIC';

  FOREACH r IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN CONTINUE; END IF;

    EXECUTE format('REVOKE ALL ON TABLE ops.f01_document_source_provenance FROM %I', r);
    EXECUTE format('GRANT SELECT ON TABLE ops.f01_document_source_provenance TO %I', r);

    FOR f IN SELECT p.oid::regprocedure AS signature, p.proname FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'ops'
        AND p.proname IN ('f01_record_document', 'f01_guard_direct_dml',
                          'f01_insert_document_source_provenance',
                          'f01_document_version_source', 'f01_document_source_history',
                          'f01_reserved_derivative_kinds', 'f01_docsource_utf16_length',
                          'f01_docsource_is_safe_text', 'f01_docsource_is_external_ident')
    LOOP
      EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f.signature, r);
      -- The private helper and the trigger guard are reached only from inside a
      -- definer writer or as a trigger, where they execute as the owner. Nothing
      -- needs a runtime grant and any runtime grant would be a hole.
      IF f.proname IN ('f01_insert_document_source_provenance', 'f01_guard_direct_dml') THEN
        CONTINUE;
      END IF;
      -- A read-only principal gets no write-shaped surface. It keeps both
      -- readers; it does not get the writer.
      IF r = 'carr_reader' AND f.proname = 'f01_record_document' THEN CONTINUE; END IF;
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', f.signature, r);
    END LOOP;
  END LOOP;
END;
$grants$;

DO $grant_posture$
DECLARE
  v_bad text;
  v_role text;
BEGIN
  -- (a) PUBLIC holds EXECUTE on nothing this file touched. A NULL proacl is
  -- itself the violation for a function: the built-in default grants PUBLIC
  -- EXECUTE, so a NULL there is a function the revoke loop never reached.
  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops'
     AND p.proname IN ('f01_record_document', 'f01_guard_direct_dml',
                       'f01_insert_document_source_provenance',
                       'f01_document_version_source', 'f01_document_source_history',
                       'f01_reserved_derivative_kinds', 'f01_docsource_utf16_length',
                       'f01_docsource_is_safe_text', 'f01_docsource_is_external_ident')
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'));
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: PUBLIC holds EXECUTE on (or no ACL was ever set for) %', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (b) THE PRIVATE HELPER IS RUNTIME-UNREACHABLE, AS OF NOW. This is a hard stop
  -- for the state of the catalog at the moment this file applies — which catches a
  -- domain.sql that was already applied with a private list naming this helper and
  -- granting it, and catches a grant loop above that skipped its CONTINUE. It does
  -- NOT and cannot catch a domain.sql applied AFTERWARDS: that run happens after
  -- this block has finished and re-runs only domain.sql's own posture check, which
  -- does not know this helper exists. The parent's edit to domain.sql's by-name
  -- private lists is the fix; the fixture's least-privilege block, which runs after
  -- both files, is the detection.
  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops' AND p.proname = 'f01_insert_document_source_provenance'
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.privilege_type = 'EXECUTE'
                        AND a.grantee IS DISTINCT FROM p.proowner));
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: % is runtime-executable. Add f01_insert_document_source_provenance to domain.sql''s private-helper lists in sections 10 and 11, and to PRIVATE_HELPERS in the local PostgreSQL gate, then re-apply in the documented order.',
      v_bad USING ERRCODE = '42501';
  END IF;

  -- (b2) THE RESERVED-KIND LIST STILL NAMES BOTH INTERNALLY PRODUCED KINDS, and
  -- the public registration surface still consults it. Section 1.1 asserted this
  -- for the list it had just written; this is the same assertion made after every
  -- other object in this file exists, so a later statement here that replaced the
  -- function again would be caught before the apply reports success.
  IF NOT ('f01_parsed_proposal' = ANY (ops.f01_reserved_derivative_kinds()))
     OR NOT ('f01_document_version' = ANY (ops.f01_reserved_derivative_kinds()))
     OR pg_get_functiondef('ops.f01_register_derivative_link(jsonb,text,text)'::regprocedure)
          NOT LIKE '%f01_reserved_derivative_kinds()%' THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: the public registration surface no longer refuses both internally produced derivative kinds; a document version''s derivative identity could be pre-claimed'
      USING ERRCODE = '42501';
  END IF;

  -- (c) NO RUNTIME DML on the new relation. The direct-DML trigger is the second
  -- line of this defence; the absent grant is the first.
  SELECT string_agg(DISTINCT coalesce(g.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ')
    INTO v_bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    LEFT JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND c.relname = 'f01_document_source_provenance'
     AND (a.grantee = 0
          OR (a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
              AND a.grantee IS DISTINCT FROM c.relowner));
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: ops.f01_document_source_provenance carries %', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (d) The named exclusion holds: a read-only principal may not complete a
  -- document, because completing one now registers a derivative.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'carr_reader')
     AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'ops' AND p.proname = 'f01_record_document'
                    AND EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                                  JOIN pg_roles g ON g.oid = a.grantee
                                 WHERE g.rolname = 'carr_reader'
                                   AND a.privilege_type = 'EXECUTE')) THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: carr_reader is granted EXECUTE on ops.f01_record_document'
      USING ERRCODE = '42501';
  END IF;

  -- (e) And the positive half, so that a loop which quietly granted NOTHING is a
  -- failure too rather than a clean run.
  FOREACH v_role IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN CONTINUE; END IF;
    IF NOT has_table_privilege(v_role, 'ops.f01_document_source_provenance', 'SELECT') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % cannot read ops.f01_document_source_provenance', v_role
        USING ERRCODE = '42501';
    END IF;
    IF NOT has_function_privilege(v_role, 'ops.f01_document_version_source(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege(v_role, 'ops.f01_document_source_history(text)', 'EXECUTE') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % cannot reach the document-source readers', v_role
        USING ERRCODE = '42501';
    END IF;
    -- The three pure text helpers and the reserved-kind list. They answer about
    -- their own argument and hold nothing, and the local gate expects every
    -- non-private, non-guard ops.f01_* function to be reachable by all four
    -- principals — so a helper this file forgot to grant fails the gate against an
    -- otherwise correct schema.
    IF NOT has_function_privilege(v_role, 'ops.f01_docsource_utf16_length(text)', 'EXECUTE')
       OR NOT has_function_privilege(v_role, 'ops.f01_docsource_is_safe_text(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege(v_role,
             'ops.f01_docsource_is_external_ident(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege(v_role, 'ops.f01_reserved_derivative_kinds()', 'EXECUTE') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % cannot reach the document-source text helpers', v_role
        USING ERRCODE = '42501';
    END IF;
    IF v_role <> 'carr_reader' AND NOT has_function_privilege(v_role,
        'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % lost EXECUTE on the replaced ops.f01_record_document', v_role
        USING ERRCODE = '42501';
    END IF;
  END LOOP;

  -- (f) The four-argument form is GONE, not shadowed. An overload would leave a
  -- path that completes a derived document with no provenance edge.
  IF to_regprocedure('ops.f01_record_document(jsonb,text,text,text)') IS NOT NULL THEN
    RAISE EXCEPTION 'f01_document_writer_overloaded: the four-argument ops.f01_record_document still exists, so a document can still complete with no source statement'
      USING ERRCODE = '42723';
  END IF;
END;
$grant_posture$;
