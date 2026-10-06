-- DoctorCRE v5 slice V5-F01: authoritative record homes, source authority and
-- document identity. The numbered install of the two reviewed F01 SQL sources.
--
-- WHAT THIS FILE IS. The exact bytes of domain.sql (PR #936 / #939) followed by
-- the exact bytes of ops/document-derivative-registration.candidate.sql, in that
-- order, with ONE line changed: the candidate's psql-only `\set ON_ERROR_STOP`
-- line is replaced by a comment, because tools/migrate.py applies migrations
-- through psycopg and psycopg cannot execute a psql meta-command. Nothing else
-- in either source is edited here. mcp-server/test/record-source-authority-
-- door.v5.test.mjs re-derives this body from the two sources and fails on any
-- byte of drift, so the reviewed files and the installed file cannot part.
--
-- WHY BOTH, AND IN THIS ORDER. The candidate forward-replaces the four-argument
-- ops.f01_record_document with the six-argument writer that requires a
-- provenance statement, and its own posture readback refuses a database where
-- the old writer survives. Applying domain.sql after it would revert that, so
-- the order is structural, not a preference.
--
-- WHAT IT INSTALLS. Additive ops.f01_* relations and security-definer writers
-- only. It inserts ZERO policy rows: no field owner, no write direction, no
-- retention period, no hold, no Salesforce mapping, no OneDrive item and no
-- object-storage key. Every registry row arrives later through the humanOnly
-- plus authorityOnly register-record-source-authority-policy verb, and the
-- kernel refuses every observation until one is installed. It reads, alters and
-- backfills no legacy row (public.record_source and public.document are not
-- referenced). It makes no provider call and activates no connector.
--
-- THREE ADDITIONS, all after the verbatim sources and none widening anything.
-- Tail 1 restates the carr_reader/carr_writer grants the sources make inside DO
-- loops as static statements, so the canonical role-bundle plan can see them
-- (see that section). Tail 2 is the authority group grant. The two sources
-- grant their authority surface to the LOGIN roles carr_authority_joe and
-- carr_authority_dell, skipping a login that does not exist when the file runs.
-- Production has no carr_authority_dell login today (the control-plane contract
-- marks it optional_nonblocking), and a disposable CI database has neither. A
-- login provisioned later would therefore hold no F01 grant at all, and nothing
-- would say so. The tail grants the same authority surface to the NOLOGIN group
-- carr_authority, which 0273 grants to both logins whenever they exist, so the
-- surface follows the membership instead of the moment of install. It grants no
-- private helper, no guard and no DML, and it reads its own posture back.
-- Tail 3 then takes back the direct grants the sources made to whichever login
-- existed at install, so the group is the only holder. That keeps the sealed
-- SCAC catalog identical in every database: the catalog counts grants to every
-- carr_* role connected by membership, production has carr_authority_joe, and
-- a CI database has no login at all, so a direct login grant would make the
-- measured catalog depend on which logins happen to exist.
--
-- The paired SCAC registry successor is 0627 (its version is assigned at merge), applied in the same
-- transaction through tools/migrate.py's atomic group; this file carries no
-- registry change.

-- ===========================================================================
-- Preflight: a fresh install or nothing.
-- ===========================================================================
DO $f01_preflight$
BEGIN
  IF to_regprocedure('ops.f01_install_policy(jsonb,text,text,text)') IS NOT NULL
     OR to_regclass('ops.f01_policy_version') IS NOT NULL THEN
    RAISE EXCEPTION 'f01_already_installed: this database already carries the F01 domain schema; bringing it forward needs its own forward migration with an explicit row decision';
  END IF;
  IF to_regprocedure('ops.authority_actor_slug()') IS NULL THEN
    RAISE EXCEPTION 'f01_authority_boundary_missing: 0161 ops.authority_actor_slug() must be installed before F01';
  END IF;
END;
$f01_preflight$;

-- ===========================================================================
-- Part 1 of 2: domain.sql, verbatim.
-- ===========================================================================
-- DoctorCRE V5-F01 — authoritative record homes, source authority and document
-- identity: the DOMAIN SCHEMA ONLY.
--
-- WHAT THIS FILE IS. The additive `ops` relations and security-definer functions
-- that the F01 handlers write through, plus the structural constraints that make
-- the nine settled decisions properties of the DATABASE rather than promises in
-- a handler. It is domain DDL and nothing else.
--
-- WHAT THIS FILE IS NOT, and the parent must supply separately:
--   * A migration. There is no migration number, no ledger entry, no SCAC
--     registration and no ordinal reservation here. Security attribution owns
--     0499/v25; the F01 successor is v26 and the parent binds it.
--   * Policy. No field owner, write direction, retention period, governing
--     constraint, surviving derivative, hold, Salesforce mapping, OneDrive item
--     or object-storage key is inserted, defaulted or implied. Every registry
--     row arrives through ops.f01_install_policy from a humanOnly plus
--     authorityOnly tool path, and this file ships zero rows of it.
--   * Legacy change. public.record_source and public.document are not read,
--     altered, backfilled, renamed, dual-written or referenced. Nothing here
--     claims a bridge from a legacy row to an F01 identity.
--   * External effect. No provider call, no byte deletion, no notification, no
--     scheduler, no extension install, no role creation.
--
-- THREE STRUCTURAL PROMISES, each enforced rather than documented:
--
--   1. SEPARATION. Current state, append-only events, mutation receipts and
--      reconciliation items are four different relations. ops.f01_apply_observation
--      refuses a shape where one stands in for another.
--   2. RECOMPUTED INTEGRITY. Every stored record carries the exact canonical
--      preimage it hashes to, and the digest column is bound to a CHECK that
--      recomputes it from that preimage inside PostgreSQL. The database never
--      learns a digest from the caller; it agrees with one or refuses.
--   3. NO DIRECT DML. Every F01 relation carries a guard trigger that refuses an
--      INSERT/UPDATE/DELETE that did not arrive through a registered
--      ops.f01_* writer, and every history relation additionally refuses UPDATE,
--      DELETE and TRUNCATE outright.
--
-- REQUIRES PostgreSQL 13 or later: trim_scale() (canonical numbers), the
-- built-in sha256(bytea) (digests) and gen_random_uuid() are all assumed.
--
-- SEARCH PATH. Every function pins `SET search_path = pg_catalog, ops, public`
-- and every object reference below is schema-qualified anyway, so a caller's
-- session search_path can never select a different table or operator.

-- REQUIRES A UTF8 DATABASE. The canonical bytes below are UTF-8 by definition —
-- they have to be, because the Node side hashes UTF-8 — so a database in any
-- other encoding would silently hash different bytes for the same record. That
-- is exactly the drift the readback contract exists to prevent, so it is a hard
-- stop at install time rather than a surprise at comparison time. It is also
-- what lets ops.f01_digest_jsonb be declared IMMUTABLE and used inside CHECK
-- constraints: with the encoding fixed, convert_to is deterministic.
DO $encoding$
BEGIN
  IF (SELECT pg_encoding_to_char(encoding) FROM pg_database
       WHERE datname = current_database()) <> 'UTF8' THEN
    RAISE EXCEPTION 'f01_requires_utf8_database: canonical F01 bytes are UTF-8'
      USING ERRCODE = '0A000';
  END IF;
END;
$encoding$;

CREATE SCHEMA IF NOT EXISTS ops;

-- Nothing in this schema is creatable or writable by an unprivileged runtime
-- principal. The F01 grants at the foot of this file re-open exactly EXECUTE on
-- the definer functions and SELECT on the relations, and never DML.
REVOKE CREATE ON SCHEMA ops FROM PUBLIC;

-- ===========================================================================
-- 1. Canonical bytes.
--
-- These four functions reproduce mcp-server/src/artifact-trust.js canonicalJson
-- and digest EXACTLY, because the whole readback story depends on PostgreSQL and
-- Node agreeing byte for byte on what a record hashes to. The fixtures compare
-- the two directly; the database never learns the expected bytes from itself.
--
-- canonicalJson's rules, restated so the correspondence is checkable:
--   array   [ items joined by "," ]
--   object  { "key":value pairs joined by "," }, keys sorted by JavaScript's
--           default Array.prototype.sort — that is UTF-16 CODE UNIT order, not
--           code point order. The two differ only for astral characters, which
--           is why f01_utf16_sortkey exists rather than a plain ORDER BY key.
--   string  JSON.stringify: escape " and \, the five short escapes, \u00xx in
--           lower-case hex below 0x20, and every other character literally —
--           including U+007F, which JSON.stringify does NOT escape.
--   number  JSON.stringify's shortest round-trip decimal.
-- ===========================================================================

/**
 * A sort key whose byte order equals JavaScript's UTF-16 code unit order.
 *
 * Each code point becomes fixed-width lower-case hex: BMP code points as one
 * four-hex unit, astral code points as the two surrogate units JavaScript
 * actually compares. Fixed width is what makes lexicographic text comparison
 * equal numeric code-unit comparison.
 */
CREATE OR REPLACE FUNCTION ops.f01_utf16_sortkey(p_text text)
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_out text := '';
  v_i integer;
  v_cp integer;
  v_rest integer;
BEGIN
  FOR v_i IN 1 .. length(p_text) LOOP
    v_cp := ascii(substr(p_text, v_i, 1));
    IF v_cp < 65536 THEN
      v_out := v_out || lpad(to_hex(v_cp), 4, '0');
    ELSE
      v_rest := v_cp - 65536;
      v_out := v_out
        || lpad(to_hex(55296 + (v_rest / 1024)), 4, '0')
        || lpad(to_hex(56320 + (v_rest % 1024)), 4, '0');
    END IF;
  END LOOP;
  RETURN v_out;
END;
$$;

/** JSON.stringify for one string, escape for escape. */
CREATE OR REPLACE FUNCTION ops.f01_json_string(p_text text)
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_out text := '"';
  v_i integer;
  v_ch text;
  v_cp integer;
BEGIN
  FOR v_i IN 1 .. length(p_text) LOOP
    v_ch := substr(p_text, v_i, 1);
    v_cp := ascii(v_ch);
    IF v_ch = '"' THEN
      v_out := v_out || E'\\"';
    ELSIF v_ch = E'\\' THEN
      v_out := v_out || E'\\\\';
    ELSIF v_cp = 8 THEN
      v_out := v_out || E'\\b';
    ELSIF v_cp = 9 THEN
      v_out := v_out || E'\\t';
    ELSIF v_cp = 10 THEN
      v_out := v_out || E'\\n';
    ELSIF v_cp = 12 THEN
      v_out := v_out || E'\\f';
    ELSIF v_cp = 13 THEN
      v_out := v_out || E'\\r';
    ELSIF v_cp < 32 THEN
      v_out := v_out || E'\\u' || lpad(to_hex(v_cp), 4, '0');
    ELSE
      v_out := v_out || v_ch;
    END IF;
  END LOOP;
  RETURN v_out || '"';
END;
$$;

/**
 * JSON.stringify for one number.
 *
 * PLAIN DECIMAL, then exponent notation at exactly JavaScript's boundaries: at
 * or above 1e21, and below 1e-6, JavaScript switches to the "1e+21" / "1e-7"
 * form, and the tail below reproduces that form — sign, one leading digit, an
 * optional fraction with trailing zeroes stripped, 'e', an explicit '+' for a
 * non-negative exponent. Every number F01 stores — versions, sequences, byte
 * lengths, confidences, retention days — sits comfortably inside the plain
 * range, but the boundary cases are reproduced rather than refused because a
 * canonical form that is merely nearly right is a digest that is wrong.
 *
 * WHAT IS REFUSED IS PRECISION JAVASCRIPT NEVER HAD. numeric is exact and
 * arbitrary-precision; a JavaScript Number is not. A value that does not survive
 * a round trip through double precision could not have come from the Node
 * serializer this function exists to agree with, so it raises
 * f01_number_not_js_roundtrip rather than being canonicalized into bytes Node
 * could never reproduce.
 */
CREATE OR REPLACE FUNCTION ops.f01_json_number(p_value numeric)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_text text;
  v_digits text;
  v_exponent integer;
  v_sign text := CASE WHEN p_value < 0 THEN '-' ELSE '' END;
BEGIN
  IF p_value = 0 THEN RETURN '0'; END IF;
  IF p_value::text IN ('NaN','Infinity','-Infinity') THEN
    RAISE EXCEPTION 'f01_nonfinite_number' USING ERRCODE = '22003';
  END IF;
  -- jsonb preserves the shortest decimal sent by the JS serializer. Never
  -- silently accept extra precision that a JavaScript Number cannot preserve.
  IF (p_value::double precision)::text::numeric IS DISTINCT FROM p_value THEN
    RAISE EXCEPTION 'f01_number_not_js_roundtrip' USING ERRCODE = '22003';
  END IF;
  v_text := trim_scale(abs(p_value))::text;
  IF abs(p_value) >= 1e-6 AND abs(p_value) < 1e21 THEN
    RETURN v_sign || v_text;
  END IF;
  IF abs(p_value) >= 1 THEN
    v_exponent := length(split_part(v_text, '.', 1)) - 1;
    v_digits := replace(v_text, '.', '');
  ELSE
    v_digits := substring(v_text FROM 3);
    v_exponent := -(length(v_digits) - length(ltrim(v_digits, '0')) + 1);
    v_digits := ltrim(v_digits, '0');
  END IF;
  v_digits := rtrim(v_digits, '0');
  RETURN v_sign || left(v_digits, 1) ||
    CASE WHEN length(v_digits) > 1 THEN '.' || substring(v_digits FROM 2) ELSE '' END ||
    'e' || CASE WHEN v_exponent >= 0 THEN '+' ELSE '' END || v_exponent::text;
END;
$$;

/** canonicalJson(value), for a jsonb value that arrived as canonical JSON. */
CREATE OR REPLACE FUNCTION ops.f01_canonical_json(p_value jsonb)
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_kind text := jsonb_typeof(p_value);
  v_body text;
BEGIN
  IF v_kind = 'null' THEN
    RETURN 'null';
  ELSIF v_kind = 'boolean' THEN
    RETURN CASE WHEN p_value = 'true'::jsonb THEN 'true' ELSE 'false' END;
  ELSIF v_kind = 'number' THEN
    RETURN ops.f01_json_number((p_value #>> '{}')::numeric);
  ELSIF v_kind = 'string' THEN
    RETURN ops.f01_json_string(p_value #>> '{}');
  ELSIF v_kind = 'array' THEN
    SELECT coalesce(string_agg(ops.f01_canonical_json(element), ',' ORDER BY ordinality), '')
      INTO v_body
      FROM jsonb_array_elements(p_value) WITH ORDINALITY AS a(element, ordinality);
    RETURN '[' || v_body || ']';
  ELSE
    -- COLLATE "C" IS PART OF THE DIGEST CONTRACT, not a stylistic choice. The
    -- sort key is fixed-width hex precisely so that TEXT order equals UTF-16
    -- code-unit order — but "text order" is whatever the database collation says
    -- it is, and this function is IMMUTABLE and reachable from a CHECK. Pinning
    -- the comparison to C makes the ordering, and therefore every digest derived
    -- from it, a property of the bytes rather than of the cluster's locale.
    SELECT coalesce(string_agg(
             ops.f01_json_string(pair.key) || ':' || ops.f01_canonical_json(pair.value),
             ',' ORDER BY ops.f01_utf16_sortkey(pair.key) COLLATE "C"), '')
      INTO v_body
      FROM jsonb_each(p_value) AS pair(key, value);
    RETURN '{' || v_body || '}';
  END IF;
END;
$$;

/** digest(value) — the same "sha256:<64 lower-case hex>" reference shape. */
CREATE OR REPLACE FUNCTION ops.f01_digest_jsonb(p_value jsonb)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
  SELECT 'sha256:' || encode(sha256(convert_to(ops.f01_canonical_json(p_value), 'UTF8')), 'hex');
$$;

-- ===========================================================================
-- 2. Shared predicates.
-- ===========================================================================

/** The one server-held tenant. Stated by a record, never selected by a caller. */
CREATE OR REPLACE FUNCTION ops.f01_tenant()
RETURNS text
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT 'carr-internal'::text $$;

CREATE OR REPLACE FUNCTION ops.f01_is_digest_ref(p_text text)
RETURNS boolean
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT p_text ~ '^sha256:[0-9a-f]{64}$' $$;

/**
 * The instant SHAPE, immutable so a CHECK can use it. The CALENDAR is checked
 * separately by ops.f01_instant below, which casts and therefore refuses
 * 2026-02-31 instead of normalizing it into 3 March the way a lenient parser
 * would.
 */
CREATE OR REPLACE FUNCTION ops.f01_is_instant_text(p_text text)
RETURNS boolean
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$
  SELECT p_text ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$'
$$;

CREATE OR REPLACE FUNCTION ops.f01_instant(p_text text)
RETURNS timestamptz
LANGUAGE plpgsql STABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
BEGIN
  IF NOT ops.f01_is_instant_text(p_text) THEN
    RAISE EXCEPTION 'f01_invalid_instant: %', p_text USING ERRCODE = '22007';
  END IF;
  RETURN p_text::timestamptz;
END;
$$;

/**
 * SERVER TIME, and the only time an F01 write may be bound to.
 *
 * now() rather than clock_timestamp(), so every record written inside one
 * transaction shares one instant and a receipt can never appear to precede the
 * event it binds. Three fractional digits, fixed, so the text is stable and the
 * canonical bytes are reproducible.
 */
/**
 * One stored instant, rendered in the exact canonical form ops.f01_now_text uses.
 *
 * ONE COPY OF THE FORMAT, for the same reason there is one copy of every
 * predicate here. The custody instant a retention period starts from is READ BACK
 * from a stored timestamptz column, and it has to render byte-identically to the
 * instants this schema writes, or the digests taken over it would differ between
 * the row that produced it and the row that re-derives it.
 */
-- STABLE, NOT IMMUTABLE, and the distinction is the planner's rather than a
-- preference: to_char(timestamp, text) is itself STABLE, so declaring this
-- immutable would be a promise this function cannot keep. It is never used in a
-- CHECK constraint or an index for exactly that reason; ops.f01_is_instant_text,
-- which is a pure regular expression, is what the constraints use.
CREATE OR REPLACE FUNCTION ops.f01_instant_text(p_at timestamptz)
RETURNS text
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, ops, public
AS $$
  SELECT to_char(p_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
$$;

CREATE OR REPLACE FUNCTION ops.f01_now_text()
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$
  SELECT ops.f01_instant_text(now());
$$;

-- ===========================================================================
-- 3. The authenticated principal.
--
-- DERIVED FROM THE CONNECTION, NEVER ACCEPTED FROM THE CALLER. The actor comes
-- from session_user — the database principal the connection actually
-- authenticated as — and, for the two authority principals, from the existing
-- ops.authority_actor_slug() helper, resolved dynamically so this schema stays
-- additive and never redefines a function it does not own.
--
-- session_user AND NOT current_user, deliberately. SECURITY DEFINER changes
-- current_user and leaves session_user alone, so the definer writers below
-- cannot launder their own caller into an authority: the principal these
-- functions see is the one that opened the connection, whatever frame asks.
--
-- NO GUC CONFERS AUTHORITY. carr.acting_actor_slug is ATTRIBUTION ONLY. It is
-- read for the ordinary writer principal alone, where it names which sponsored
-- agent is acting behind an already-sponsored connection, and it is checked
-- against the same slug shape as every other actor. Setting it — to 'joe' or to
-- anything else — cannot make a session human, cannot make it a verified
-- partner, and cannot reach either authorityOnly surface.
--
-- ops.authority_actor_slug() IS A DEPENDENCY, NOT A FALLBACK. When it is absent
-- the authority path raises f01_authority_actor_slug_missing rather than
-- degrading to session_user, to current_user or to the schema owner. An
-- unbootstrapped database refuses authority work; it does not quietly hand that
-- authority to whoever installed the schema. Without the guard the same
-- situation surfaces as a bare "function does not exist" from PL/pgSQL's
-- deferred name resolution, which reads like a packaging accident rather than
-- the hard stop it is.
--
-- PARENT WIRING (see the file-foot notes): the four role names below are the
-- principals this schema assumes. The parent must confirm them, and must confirm
-- that ops.authority_actor_slug() exists and returns a slug matching the shape
-- asserted here for each authority role.
--
-- WHERE THAT HELPER COMES FROM, and what it obliges the parent to have done.
-- ops.authority_actor_slug() is defined by the canonical control-plane authority
-- boundary — 0161_control_plane_authority_boundary.sql — as a SECURITY DEFINER,
-- STABLE function that CASEs on session_user, returning 'joe' for
-- carr_authority_joe and 'dell' for carr_authority_dell and raising for anyone
-- else. That migration also revokes it from PUBLIC and grants EXECUTE on it to
-- the NOLOGIN group role carr_authority. This file does not redefine it, regrant
-- it, or wrap it; it depends on it.
--
-- TWO CALLERS REACH IT, UNDER TWO DIFFERENT ROLES, AND THAT IS DELIBERATE:
--
--   * ops.f01_context_actor_slug() below is SECURITY DEFINER, so it — and every
--     definer writer that resolves a principal through it — reaches the helper
--     as the SCHEMA OWNER. The owner must therefore own the helper or be a
--     member of carr_authority. The grant loop at the foot of this file cannot
--     supply that and must not try: the helper is not an f01\_% object and does
--     not belong to this capsule.
--   * ops.f01_require_authority_principal() below is CALLER-RIGHTS, so an
--     authority login calling it directly reaches the helper as ITSELF, over the
--     grant carr_authority already holds. That is why the two authority logins
--     must be members of that group role.
--
-- BOTH ANSWER IDENTICALLY, because the helper derives from session_user and
-- SECURITY DEFINER changes only current_user. Adding SECURITY DEFINER to
-- f01_require_authority_principal would change no answer at all; it would only
-- move which role needs the helper grant, and quietly drop the one path that
-- exercises the login's own privilege. The fixture asserts the two derivations
-- agree rather than assuming it.
-- ===========================================================================

CREATE OR REPLACE FUNCTION ops.f01_context_actor_slug()
RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE v_slug text;
BEGIN
  IF session_user IN ('carr_authority_joe', 'carr_authority_dell') THEN
    IF to_regprocedure('ops.authority_actor_slug()') IS NULL THEN
      RAISE EXCEPTION 'f01_authority_actor_slug_missing: ops.authority_actor_slug() is not installed; F01 never falls back to the schema owner'
        USING ERRCODE = '42883';
    END IF;
    v_slug := ops.authority_actor_slug();
  ELSIF session_user = 'carr_writer' THEN
    v_slug := nullif(current_setting('carr.acting_actor_slug', true), '');
  ELSIF session_user = 'carr_reader' THEN
    v_slug := 'carr-reader';
  ELSE
    RAISE EXCEPTION 'f01_principal_refused: %', session_user USING ERRCODE = '42501';
  END IF;
  IF v_slug IS NULL OR v_slug !~ '^[a-z][a-z0-9-]{1,62}$' THEN
    RAISE EXCEPTION 'f01_no_authenticated_actor' USING ERRCODE = '28000';
  END IF;
  RETURN v_slug;
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_principal()
RETURNS jsonb LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$
  SELECT jsonb_build_object(
    'actor_slug', ops.f01_context_actor_slug(),
    'human', session_user IN ('carr_authority_joe', 'carr_authority_dell'),
    'authorization_class', CASE WHEN session_user IN ('carr_authority_joe', 'carr_authority_dell')
      THEN 'verified_partner' ELSE 'sponsored_agent' END,
    'derived_by', 'authenticated_database_principal');
$$;

-- CALLER-RIGHTS ON PURPOSE (see section 3's note). This one resolves the helper
-- as the calling login, which is what makes an authority login's own EXECUTE
-- grant part of the path rather than decoration. session_user — the only thing
-- either function decides on — is identical under both models.
CREATE OR REPLACE FUNCTION ops.f01_require_authority_principal(p_operation text)
RETURNS text LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
BEGIN
  IF session_user NOT IN ('carr_authority_joe', 'carr_authority_dell') THEN
    RAISE EXCEPTION 'f01_authority_principal_refused: %', p_operation USING ERRCODE = '42501';
  END IF;
  -- Same hard stop as f01_context_actor_slug: an absent helper refuses the
  -- authority operation outright rather than resolving it to the owner.
  IF to_regprocedure('ops.authority_actor_slug()') IS NULL THEN
    RAISE EXCEPTION 'f01_authority_actor_slug_missing: ops.authority_actor_slug() is not installed; F01 never falls back to the schema owner'
      USING ERRCODE = '42883';
  END IF;
  RETURN ops.authority_actor_slug();
END;
$$;

-- ===========================================================================
-- 4. The direct-DML and append-only guards.
--
-- WHY A CALL-STACK CHECK RATHER THAN A FLAG. A transaction-local flag is
-- something a caller with DML rights could set for itself, so it would prove
-- nothing. PG_CONTEXT names the actual PL/pgSQL frames beneath the trigger, so
-- the guard can require that the write genuinely arrived through a registered
-- ops.f01_* writer. Combined with REVOKE CREATE ON SCHEMA ops above — nobody
-- else can define a function under this prefix — the check is structural.
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
  --
  -- insert_document_source_provenance IS NAMED HERE EVEN THOUGH THIS FILE DOES
  -- NOT CREATE IT, for the same reason sections 10 and 11 name it in their
  -- private-helper lists: it is the document-source seam's private inserter, it
  -- arrives with that hunk, and this alternation is REPLACED wholesale by a later
  -- re-apply of this file. Without the name here, applying domain.sql after the
  -- hunk reverts the guard to a list that does not know the helper exists, and
  -- every provenance insert — and every document completion with it — is refused
  -- as direct DML. That failure is loud and closed rather than dangerous, but it
  -- is still a schema that cannot write a document, and the name costs nothing in
  -- a database where the helper is not installed yet: a frame can only match a
  -- function that exists, and nothing else may define one under this prefix.
  IF regexp_replace(v_context, 'PL/pgSQL function (ops\.)?f01_guard_direct_dml\(\)[^\n]*', '', 'g')
       !~ 'PL/pgSQL function (ops\.)?f01_(install_policy|apply_observation|record_artifact|record_proposal|record_document|record_hold|record_deletion_evaluation|register_derivative_link|insert_derivative_link|insert_document_source_provenance|claim_idempotency|settle_idempotency)\('
  THEN
    RAISE EXCEPTION 'f01_direct_dml_refused: %.% is written only through the registered ops.f01_* writers',
      TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

/** Append-only history: an UPDATE or DELETE is refused, never quietly applied. */
CREATE OR REPLACE FUNCTION ops.f01_guard_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
BEGIN
  RAISE EXCEPTION 'f01_append_only_violation: %.% admits INSERT only; % refused',
    TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP USING ERRCODE = '42501';
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_guard_no_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
BEGIN
  RAISE EXCEPTION 'f01_truncate_refused: %.% is append-only history',
    TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '42501';
  RETURN NULL;
END;
$$;

-- ===========================================================================
-- 5. The relations.
--
-- EVERY RECORD IS AN ENVELOPE. `envelope` is the exact canonical preimage the
-- record hashes to; `envelope -> 'record'` is the domain record the reviewed
-- pure kernel produced, and `record_digest` is the kernel's own digest for it.
-- Both digests are CHECK-bound to a recomputation inside PostgreSQL, so a
-- readback that agrees with the stored claim has genuinely verified it, and a
-- tampered row cannot be read back as healthy.
--
-- Typed columns exist for keys, ordering and constraints, and each one is
-- CHECK-bound to the same field inside the envelope. An extracted column can
-- therefore never drift from the bytes that were hashed.
-- ===========================================================================

-- --- 5.1 policy versions (immutable, CAS-chained) --------------------------

CREATE TABLE IF NOT EXISTS ops.f01_policy_version (
  policy_seq              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant                  text NOT NULL,
  registry_version        integer NOT NULL,
  envelope                jsonb NOT NULL,
  envelope_digest         text NOT NULL,
  policy_digest           text NOT NULL,
  prior_policy_digest     text,
  field_registry_digest   text NOT NULL,
  retention_registry_digest text NOT NULL,
  domain_policy_digest    text NOT NULL,
  installed_by            text NOT NULL,
  installed_at_text       text NOT NULL,
  installed_at            timestamptz NOT NULL,
  idempotency_key         text NOT NULL,

  CONSTRAINT f01_policy_tenant CHECK (tenant = ops.f01_tenant()),
  CONSTRAINT f01_policy_tenant_bound CHECK (tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_policy_kind CHECK (envelope ->> 'record_kind' = 'stored_policy_version'),
  CONSTRAINT f01_policy_envelope_digest
    CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  -- The kernel's own digest over the policy record, recomputed here rather than
  -- copied from the handler.
  CONSTRAINT f01_policy_record_digest
    CHECK (policy_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND policy_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_policy_digest_shape CHECK (ops.f01_is_digest_ref(policy_digest)),
  CONSTRAINT f01_policy_prior_shape
    CHECK (prior_policy_digest IS NULL OR ops.f01_is_digest_ref(prior_policy_digest)),
  CONSTRAINT f01_policy_prior_bound
    CHECK (prior_policy_digest IS NOT DISTINCT FROM (envelope -> 'record' ->> 'prior_policy_digest')),
  CONSTRAINT f01_policy_prior_is_not_self CHECK (prior_policy_digest IS DISTINCT FROM policy_digest),
  CONSTRAINT f01_policy_registry_digests
    CHECK (ops.f01_is_digest_ref(field_registry_digest)
           AND ops.f01_is_digest_ref(retention_registry_digest)
           AND field_registry_digest = envelope -> 'record' ->> 'field_registry_digest'
           AND retention_registry_digest = envelope -> 'record' ->> 'retention_registry_digest'),
  -- The candidate registries are stored as the exact preimages they hash to, so
  -- the digests above are recomputable from the stored bytes and not merely
  -- asserted alongside them.
  CONSTRAINT f01_policy_field_registry_recomputed
    CHECK (field_registry_digest = ops.f01_digest_jsonb(envelope -> 'record' -> 'field_registry')),
  CONSTRAINT f01_policy_retention_registry_recomputed
    CHECK (retention_registry_digest = ops.f01_digest_jsonb(envelope -> 'record' -> 'retention_registry')),
  CONSTRAINT f01_policy_domain_digest CHECK (ops.f01_is_digest_ref(domain_policy_digest)),
  CONSTRAINT f01_policy_registry_version CHECK (registry_version >= 1
    AND registry_version = (envelope -> 'record' ->> 'registry_version')::integer),
  CONSTRAINT f01_policy_installed_by CHECK (installed_by = envelope -> 'record' ->> 'installed_by'),
  CONSTRAINT f01_policy_installed_at_shape CHECK (ops.f01_is_instant_text(installed_at_text)
    AND installed_at_text = envelope -> 'record' ->> 'installed_at'),
  CONSTRAINT f01_policy_idempotency CHECK (length(idempotency_key) BETWEEN 1 AND 200)
);

-- One version per digest, and one successor per prior digest. The second index
-- IS the compare-and-swap: two concurrent installs naming the same prior cannot
-- both land, so a policy chain can never fork under a race.
CREATE UNIQUE INDEX IF NOT EXISTS f01_policy_version_digest_uq
  ON ops.f01_policy_version (policy_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_policy_version_cas_uq
  ON ops.f01_policy_version (tenant, prior_policy_digest)
  WHERE prior_policy_digest IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS f01_policy_version_genesis_uq
  ON ops.f01_policy_version (tenant)
  WHERE prior_policy_digest IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS f01_policy_version_registry_version_uq
  ON ops.f01_policy_version (tenant, registry_version);

-- The separate CURRENT pointer. Exactly one row per tenant; the history above is
-- never rewritten to move it.
CREATE TABLE IF NOT EXISTS ops.f01_policy_current (
  tenant        text PRIMARY KEY,
  policy_seq    bigint NOT NULL REFERENCES ops.f01_policy_version (policy_seq),
  policy_digest text NOT NULL REFERENCES ops.f01_policy_version (policy_digest),
  updated_by    text NOT NULL,
  updated_at    timestamptz NOT NULL,
  CONSTRAINT f01_policy_current_tenant CHECK (tenant = ops.f01_tenant())
);

-- --- 5.2 observations: four separate relations -----------------------------

CREATE TABLE IF NOT EXISTS ops.f01_field_state (
  tenant            text NOT NULL,
  entity            text NOT NULL,
  field             text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  state_digest      text NOT NULL,
  value_digest      text NOT NULL,
  owner_source      text NOT NULL,
  account           text,
  native_id         text,
  native_id_epoch   text,
  event_seq         bigint NOT NULL,
  last_event_digest text NOT NULL,
  observed_at_text  text NOT NULL,
  observed_at       timestamptz NOT NULL,
  policy_digest     text NOT NULL,
  updated_by        text NOT NULL,
  updated_at        timestamptz NOT NULL,

  PRIMARY KEY (tenant, entity, field),
  CONSTRAINT f01_state_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_state_kind CHECK (envelope ->> 'record_kind' = 'stored_field_state'),
  CONSTRAINT f01_state_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_state_record_digest
    CHECK (state_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND state_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_state_binding
    CHECK (entity = envelope -> 'record' ->> 'entity'
           AND field = envelope -> 'record' ->> 'field'
           AND value_digest = envelope -> 'record' ->> 'value_digest'
           AND owner_source = envelope -> 'record' ->> 'owner_source'
           AND account IS NOT DISTINCT FROM (envelope -> 'record' ->> 'account')
           AND event_seq = (envelope -> 'record' ->> 'event_seq')::bigint
           AND last_event_digest = envelope -> 'record' ->> 'last_event_digest'
           AND observed_at_text = envelope -> 'record' ->> 'observed_at'),
  CONSTRAINT f01_state_digest_shapes
    CHECK (ops.f01_is_digest_ref(value_digest) AND ops.f01_is_digest_ref(last_event_digest)
           AND ops.f01_is_digest_ref(policy_digest)),
  -- An established record has had at least one event; sequence zero would
  -- contradict its own existence, exactly as the kernel refuses.
  CONSTRAINT f01_state_event_seq CHECK (event_seq >= 1),
  CONSTRAINT f01_state_observed_at_shape CHECK (ops.f01_is_instant_text(observed_at_text))
);

CREATE TABLE IF NOT EXISTS ops.f01_field_event (
  event_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  entity            text NOT NULL,
  field             text NOT NULL,
  event_seq         bigint NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  event_digest      text NOT NULL,
  previous_event_digest text,
  event_kind        text NOT NULL,
  source_system     text NOT NULL,
  observed_at_text  text NOT NULL,
  observed_at       timestamptz NOT NULL,
  policy_digest     text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_event_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_event_kind_envelope CHECK (envelope ->> 'record_kind' = 'stored_source_event'),
  CONSTRAINT f01_event_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_event_record_digest
    CHECK (event_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND event_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_event_is_event
    CHECK (envelope -> 'record' ->> 'record_kind' = 'append_only_event'
           AND (envelope -> 'record' ->> 'append_only') = 'true'
           AND (envelope -> 'record' ->> 'rewrites_prior_event') = 'false'),
  CONSTRAINT f01_event_binding
    CHECK (entity = envelope -> 'record' ->> 'entity'
           AND field = envelope -> 'record' ->> 'field'
           AND event_seq = (envelope -> 'record' ->> 'event_seq')::bigint
           AND event_kind = envelope -> 'record' ->> 'event_kind'
           AND source_system = envelope -> 'record' ->> 'source_system'
           AND observed_at_text = envelope -> 'record' ->> 'observed_at'
           AND previous_event_digest IS NOT DISTINCT FROM
               (envelope -> 'record' ->> 'previous_event_digest')),
  CONSTRAINT f01_event_seq CHECK (event_seq >= 1),
  -- The chain is structural: sequence 1 opens it, and every later event names
  -- the digest of the one before.
  CONSTRAINT f01_event_chain
    CHECK ((event_seq = 1 AND previous_event_digest IS NULL)
           OR (event_seq > 1 AND ops.f01_is_digest_ref(previous_event_digest))),
  CONSTRAINT f01_event_observed_at_shape CHECK (ops.f01_is_instant_text(observed_at_text))
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_field_event_seq_uq
  ON ops.f01_field_event (tenant, entity, field, event_seq);
CREATE UNIQUE INDEX IF NOT EXISTS f01_field_event_digest_uq
  ON ops.f01_field_event (event_digest);
-- One successor per link: a second event claiming the same predecessor is a
-- fork, and a fork in append-only history is a rewrite by another name.
CREATE UNIQUE INDEX IF NOT EXISTS f01_field_event_previous_uq
  ON ops.f01_field_event (tenant, entity, field, previous_event_digest)
  WHERE previous_event_digest IS NOT NULL;

CREATE TABLE IF NOT EXISTS ops.f01_state_transition (
  transition_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  entity            text NOT NULL,
  field             text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  transition_digest text NOT NULL,
  from_value_digest text,
  to_value_digest   text NOT NULL,
  event_digest      text NOT NULL,
  policy_digest     text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_transition_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_transition_kind CHECK (envelope ->> 'record_kind' = 'stored_state_transition'),
  CONSTRAINT f01_transition_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_transition_record_digest
    CHECK (transition_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND transition_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_transition_is_transition
    CHECK (envelope -> 'record' ->> 'record_kind' = 'current_state_transition'
           AND (envelope -> 'record' ->> 'alone_sufficient') = 'false'),
  CONSTRAINT f01_transition_binding
    CHECK (entity = envelope -> 'record' ->> 'entity'
           AND field = envelope -> 'record' ->> 'field'
           AND to_value_digest = envelope -> 'record' ->> 'to_value_digest'
           AND from_value_digest IS NOT DISTINCT FROM (envelope -> 'record' ->> 'from_value_digest')),
  -- A transition NEVER names a receipt. The receipt names it. That asymmetry is
  -- what makes "no record substitutes for another" checkable rather than stated.
  CONSTRAINT f01_transition_names_no_receipt
    CHECK (NOT (envelope -> 'record' ? 'mutation_receipt_digest'))
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_state_transition_digest_uq
  ON ops.f01_state_transition (transition_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_state_transition_event_uq
  ON ops.f01_state_transition (event_digest);

CREATE TABLE IF NOT EXISTS ops.f01_mutation_receipt (
  receipt_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  entity            text NOT NULL,
  field             text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  receipt_digest    text NOT NULL,
  transition_digest text NOT NULL REFERENCES ops.f01_state_transition (transition_digest),
  event_digest      text NOT NULL REFERENCES ops.f01_field_event (event_digest),
  policy_digest     text NOT NULL,
  reason_id         text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_receipt_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_receipt_kind CHECK (envelope ->> 'record_kind' = 'stored_mutation_receipt'),
  CONSTRAINT f01_receipt_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_receipt_record_digest
    CHECK (receipt_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND receipt_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_receipt_is_receipt
    CHECK (envelope -> 'record' ->> 'record_kind' = 'mutation_receipt'
           AND (envelope -> 'record' ->> 'alone_sufficient') = 'false'),
  -- The receipt binds BOTH other records by digest. Neither of the other two
  -- binds the receipt, so none of the three can be mistaken for another.
  CONSTRAINT f01_receipt_binding
    CHECK (entity = envelope -> 'record' ->> 'entity'
           AND field = envelope -> 'record' ->> 'field'
           AND reason_id = envelope -> 'record' ->> 'reason_id'
           AND transition_digest = envelope -> 'record' ->> 'current_state_transition_digest'
           AND event_digest = envelope -> 'record' ->> 'event_digest'),
  -- The kernel refuses to accept an actor, and the record says so. A stored
  -- receipt that claims one came from somewhere other than the kernel.
  CONSTRAINT f01_receipt_actor_derived
    CHECK ((envelope -> 'record' ->> 'actor_derived_by') = 'authenticated_handler_context'
           AND jsonb_typeof(envelope -> 'record' -> 'actor') = 'null')
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_mutation_receipt_digest_uq
  ON ops.f01_mutation_receipt (receipt_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_mutation_receipt_pair_uq
  ON ops.f01_mutation_receipt (transition_digest, event_digest);

CREATE TABLE IF NOT EXISTS ops.f01_reconciliation_item (
  item_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  entity            text NOT NULL,
  field             text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  item_digest       text NOT NULL,
  conflict_kind     text NOT NULL,
  human_resolver_class text NOT NULL,
  policy_digest     text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_reconciliation_tenant
    CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_reconciliation_kind CHECK (envelope ->> 'record_kind' = 'stored_reconciliation_item'),
  CONSTRAINT f01_reconciliation_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_reconciliation_record_digest
    CHECK (item_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND item_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_reconciliation_binding
    CHECK (entity = envelope -> 'record' ->> 'entity'
           AND field = envelope -> 'record' ->> 'field'
           AND conflict_kind = envelope -> 'record' ->> 'conflict_kind'
           AND human_resolver_class = envelope -> 'record' ->> 'human_resolver_class'),
  -- Visible, unapplied, and never machine-resolved. A reconciliation item that
  -- claimed otherwise would be a silent last-write-wins wearing a queue's name.
  CONSTRAINT f01_reconciliation_visible
    CHECK ((envelope -> 'record' ->> 'visible') = 'true'
           AND (envelope -> 'record' ->> 'applied') = 'false'
           AND (envelope -> 'record' ->> 'resolved_by_machine') = 'false')
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_reconciliation_item_digest_uq
  ON ops.f01_reconciliation_item (item_digest);

-- --- 5.3 corporate artifacts and parsed proposals --------------------------

CREATE TABLE IF NOT EXISTS ops.f01_corporate_artifact (
  artifact_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  artifact_digest   text NOT NULL,
  source_system     text NOT NULL,
  source_account    text NOT NULL,
  native_id         text NOT NULL,
  native_id_epoch   text NOT NULL,
  native_version    text NOT NULL,
  content_digest    text NOT NULL,
  evidence_class    text NOT NULL,
  observed_at_text  text NOT NULL,
  observed_at       timestamptz NOT NULL,
  policy_digest     text,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_artifact_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_artifact_kind CHECK (envelope ->> 'record_kind' = 'stored_corporate_artifact'),
  CONSTRAINT f01_artifact_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_artifact_record_digest
    CHECK (artifact_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND artifact_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_artifact_binding
    CHECK (source_system = envelope -> 'record' ->> 'source_system'
           AND source_account = envelope -> 'record' ->> 'source_account'
           AND native_id = envelope -> 'record' -> 'native_identity' ->> 'native_id'
           AND native_id_epoch = envelope -> 'record' -> 'native_identity' ->> 'native_id_epoch'
           AND native_version = envelope -> 'record' ->> 'native_version'
           AND content_digest = envelope -> 'record' ->> 'content_digest'
           AND evidence_class = envelope -> 'record' ->> 'evidence_class'
           AND observed_at_text = envelope -> 'record' ->> 'observed_at'),
  -- Source-agnostic on purpose, but Tour machinery can never certify a generic
  -- corporate fact, so the five Tour-only classes are refused BY NAME here as
  -- well as in the kernel.
  CONSTRAINT f01_artifact_not_tour_only
    CHECK (evidence_class NOT IN ('tour_rights_receipt', 'tour_source_evidence',
                                  'tour_field_assertion', 'tour_public_projection',
                                  'tour_route_version')),
  CONSTRAINT f01_artifact_content_digest CHECK (ops.f01_is_digest_ref(content_digest)),
  CONSTRAINT f01_artifact_observed_at_shape CHECK (ops.f01_is_instant_text(observed_at_text))
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_corporate_artifact_digest_uq
  ON ops.f01_corporate_artifact (artifact_digest);
-- IMMUTABILITY, structurally: one identity may name exactly one set of bytes.
-- A second artifact reusing the identity for different content cannot land.
CREATE UNIQUE INDEX IF NOT EXISTS f01_corporate_artifact_identity_uq
  ON ops.f01_corporate_artifact
     (tenant, source_system, source_account, native_id, native_id_epoch, native_version);

CREATE TABLE IF NOT EXISTS ops.f01_parsed_proposal (
  proposal_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  proposal_digest   text NOT NULL,
  artifact_digest   text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  source_system     text NOT NULL,
  source_account    text NOT NULL,
  confidence        numeric NOT NULL,
  observed_at_text  text NOT NULL,
  observed_at       timestamptz NOT NULL,
  policy_digest     text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_proposal_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_proposal_kind CHECK (envelope ->> 'record_kind' = 'stored_parsed_proposal'),
  CONSTRAINT f01_proposal_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_proposal_record_digest
    CHECK (proposal_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND proposal_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_proposal_binding
    CHECK (artifact_digest = envelope -> 'record' ->> 'artifact_digest'
           AND source_system = envelope -> 'record' ->> 'source_system'
           AND source_account = envelope -> 'record' ->> 'source_account'
           AND observed_at_text = envelope -> 'record' ->> 'observed_at'),
  CONSTRAINT f01_proposal_confidence CHECK (confidence >= 0 AND confidence <= 1),
  -- A proposal is reviewable and nothing else. These four are asserted on the
  -- STORED row, so a proposal cannot become a fact by being written down.
  CONSTRAINT f01_proposal_is_not_fact
    CHECK ((envelope ->> 'becomes_fact') = 'false'
           AND (envelope ->> 'advances_state') = 'false'
           AND (envelope ->> 'carries_effect_authority') = 'false'
           AND (envelope ->> 'requires_human_review') = 'true')
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_parsed_proposal_digest_uq
  ON ops.f01_parsed_proposal (proposal_digest);

CREATE TABLE IF NOT EXISTS ops.f01_proposal_link (
  link_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  link_digest       text NOT NULL,
  proposal_digest   text NOT NULL REFERENCES ops.f01_parsed_proposal (proposal_digest),
  artifact_digest   text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  supersedes_link_digest text,
  policy_digest     text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_link_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_link_kind CHECK (envelope ->> 'record_kind' = 'stored_proposal_link'),
  CONSTRAINT f01_link_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_link_record_digest
    CHECK (link_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND link_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_link_binding
    CHECK (artifact_digest = envelope -> 'record' ->> 'artifact_digest'
           AND supersedes_link_digest IS NOT DISTINCT FROM
               (envelope -> 'record' ->> 'supersedes_link_digest')),
  CONSTRAINT f01_link_reversible
    CHECK ((envelope -> 'record' ->> 'reversible') = 'true'
           AND (envelope -> 'record' ->> 'history_preserved') = 'true'),
  CONSTRAINT f01_link_not_self CHECK (supersedes_link_digest IS DISTINCT FROM link_digest)
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_proposal_link_digest_uq
  ON ops.f01_proposal_link (link_digest);
-- Superseding NAMES the link it replaces rather than erasing it, and exactly one
-- link may supersede any given link, so the reversible history stays a chain.
CREATE UNIQUE INDEX IF NOT EXISTS f01_proposal_link_supersedes_uq
  ON ops.f01_proposal_link (tenant, supersedes_link_digest)
  WHERE supersedes_link_digest IS NOT NULL;

ALTER TABLE ops.f01_proposal_link
  DROP CONSTRAINT IF EXISTS f01_link_supersedes_fk;
ALTER TABLE ops.f01_proposal_link
  ADD CONSTRAINT f01_link_supersedes_fk
  FOREIGN KEY (supersedes_link_digest) REFERENCES ops.f01_proposal_link (link_digest);

-- --- 5.3.1 registered derivative-source links -------------------------------
--
-- WHOSE RULE THIS RELATION SERVES, said once here and referred to below rather
-- than repeated. Q129.D1 settles the per-class RETENTION REGISTRY and nothing
-- about provenance registration. The registration rule this table exists for was
-- approved in native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683, user turn
-- 01a08779-6b68-7013-bab1-369cf616254f, and carries no canonical decision id
-- because none was issued for it. Where the comments below say "the approved
-- registration rule" they mean that and only that; nothing here is a new settled
-- decision and nothing downstream should record it as one.
--
-- WHAT A ROW HERE IS. One trusted producer workflow's record that it created one
-- derived record from one stored artifact: when the system makes a lease
-- abstract, the row says which lease produced it. Nobody types it; the producing
-- workflow writes it in the same transaction that produces the derivative, which
-- is what "before the derivative is considered complete" means in practice.
--
-- WHAT A ROW HERE IS NOT, and the CHECK below makes it unsayable. It is not an
-- inventory. The presence of rows tells you what was registered; the ABSENCE of
-- rows tells you nothing at all, because an unregistered derivative — from a
-- workflow written before this rule, from a provider, from a copy somebody made
-- — leaves exactly the same trace as no derivative: none. So every stored link
-- carries registration_is_provenance = true, is_exhaustive_inventory = false,
-- establishes_coverage = false and permits_deletion = false inside the bytes it
-- hashes to, and a row claiming otherwise — or a row SAYING NOTHING EITHER WAY —
-- cannot be inserted at all.
--
-- WHAT A ROW HERE IS ALSO NOT: A CHECKED FACT ABOUT THE DERIVATIVE. Nothing in
-- this schema loads, or could load, the record named by derivative_id /
-- derivative_content_digest. The foreign key binds the SOURCE side only, so a
-- registration is a producer's ATTESTATION that it made something, not the
-- database's knowledge that the something exists. Today that is harmless in the
-- one direction it can go — an attested derivative only ever blocks a deletion
-- harder, and no coverage answer can be established at all — but it stops being
-- harmless the moment coverage could be established, because an attested-only
-- link would then flow through ops.f01_stored_derivatives into a deletion
-- receipt as a named survivor that may never have existed. So it is a standing
-- precondition on that work rather than a footnote: DO NOT BUILD AN INGRESS
-- THAT ESTABLISHES COVERAGE UNTIL PRODUCER OUTPUTS ARE INDEPENDENTLY BOUND AND
-- VERIFIED — the derivative resolvable and its bytes checked against
-- derivative_content_digest by something other than the registering caller.
-- Nothing in this file supplies that, and nothing here pretends to.
--
-- ONE DERIVATIVE HAS ONE ORIGINAL. The identity index is on (kind, id) rather
-- than on (source, kind, id): re-registering the same derivative against a
-- different artifact is not a second provenance edge, it is a rewrite of where
-- the derivative came from, and it refuses.
CREATE TABLE IF NOT EXISTS ops.f01_derivative_link (
  derivative_link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  link_digest       text NOT NULL,
  source_artifact_digest text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  derivative_kind   text NOT NULL,
  derivative_id     text NOT NULL,
  derivative_content_digest text NOT NULL,
  producer_workflow text NOT NULL,
  producer_run_ref  text NOT NULL,
  produced_at_text  text NOT NULL,
  produced_at       timestamptz NOT NULL,
  evidence_ref      text NOT NULL,
  evidence_digest   text NOT NULL,
  -- Nullable for the same reason a hold's is: registering provenance is a
  -- statement about two records and does not depend on a field-authority
  -- registry existing.
  policy_digest     text,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_derivative_tenant
    CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_derivative_kind_envelope
    CHECK (envelope ->> 'record_kind' = 'stored_derivative_link'),
  CONSTRAINT f01_derivative_envelope_digest
    CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_derivative_record_digest
    CHECK (link_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND link_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_derivative_binding
    CHECK (source_artifact_digest = envelope -> 'record' ->> 'source_artifact_digest'
           AND derivative_kind = envelope -> 'record' ->> 'derivative_kind'
           AND derivative_id = envelope -> 'record' ->> 'derivative_id'
           AND derivative_content_digest = envelope -> 'record' ->> 'derivative_content_digest'
           AND producer_workflow = envelope -> 'record' ->> 'producer_workflow'
           AND producer_run_ref = envelope -> 'record' ->> 'producer_run_ref'
           AND produced_at_text = envelope -> 'record' ->> 'produced_at'
           AND evidence_ref = envelope -> 'record' ->> 'evidence_ref'
           AND evidence_digest = envelope -> 'record' ->> 'evidence_digest'
           -- The producer principal is DERIVED by the writer; binding the column
           -- to the hashed record is what stops a stored link being attributed
           -- to a producer that did not register it.
           AND actor_slug = envelope -> 'record' ->> 'registered_by'),
  CONSTRAINT f01_derivative_digest_shapes
    CHECK (ops.f01_is_digest_ref(source_artifact_digest)
           AND ops.f01_is_digest_ref(derivative_content_digest)
           AND ops.f01_is_digest_ref(evidence_digest)),
  CONSTRAINT f01_derivative_produced_at_shape
    CHECK (ops.f01_is_instant_text(produced_at_text)),
  -- A record whose bytes are the source's bytes is the source under a second
  -- name, not something derived from it.
  CONSTRAINT f01_derivative_not_self
    CHECK (derivative_content_digest <> source_artifact_digest)
  -- THE FOUR CLAIMS A LINK MAY NEVER MAKE — registration_is_provenance true,
  -- is_exhaustive_inventory, establishes_coverage and permits_deletion false, on
  -- the stored row and inside the hashed record both — are enforced by
  -- f01_derivative_claims_nothing, which is ADDED IMMEDIATELY BELOW rather than
  -- here. There is exactly one copy of that expression on purpose; see the note.
);

-- THE ONE COPY OF f01_derivative_claims_nothing, added as an ALTER for the same
-- reason section 5.6's coverage bound is: CREATE TABLE IF NOT EXISTS leaves an
-- ALREADY-INSTALLED table untouched, so a constraint written only inside the
-- CREATE would reach fresh databases and nothing else — which is no constraint at
-- all on the databases that already hold rows. Writing it in both places would
-- put two copies of one predicate in one file, and the copy that drifts is always
-- the one nobody is looking at.
--
-- WHAT IT MAKES IMPOSSIBLE. A stored link that claims to be an inventory, to
-- establish coverage or to permit deletion — and, equally, one that SAYS NOTHING
-- EITHER WAY. The second half is why coalesce is here: `->>` over an ABSENT key
-- yields SQL NULL, NULL = 'false' is NULL, and a CHECK fails only on FALSE, so an
-- earlier form of this refused a record asserting establishes_coverage: true and
-- ADMITTED one that simply omitted the key — leaving a stored link with no
-- self-limiting bytes for a later reader to find. '' is neither 'true' nor
-- 'false', so silence now fails the same conjunct a contrary claim does.
--
-- NOT VALID, AND THE COMMENT SAYS WHAT THAT MEANS RATHER THAN IMPLYING MORE. Rows
-- written before this tightening are NOT re-checked and are NOT retro-verified:
-- the constraint binds every row inserted from here on, and says nothing about
-- what is already stored. A validating form would abort the whole apply on the
-- first pre-existing row that omitted a key — which is precisely the row this
-- exists to make impossible in future, and precisely the row an operator needs
-- the schema to still install so they can go and look at it.
ALTER TABLE ops.f01_derivative_link
  DROP CONSTRAINT IF EXISTS f01_derivative_claims_nothing;
ALTER TABLE ops.f01_derivative_link
  ADD CONSTRAINT f01_derivative_claims_nothing
  CHECK (coalesce(envelope -> 'record' ->> 'registration_is_provenance', '') = 'true'
         AND coalesce(envelope -> 'record' ->> 'is_exhaustive_inventory', '') = 'false'
         AND coalesce(envelope -> 'record' ->> 'establishes_coverage', '') = 'false'
         AND coalesce(envelope -> 'record' ->> 'permits_deletion', '') = 'false'
         AND coalesce(envelope ->> 'is_exhaustive_inventory', '') = 'false'
         AND coalesce(envelope ->> 'establishes_coverage', '') = 'false'
         AND coalesce(envelope ->> 'permits_deletion', '') = 'false')
  NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS f01_derivative_link_digest_uq
  ON ops.f01_derivative_link (link_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_derivative_link_identity_uq
  ON ops.f01_derivative_link (tenant, derivative_kind, derivative_id);

-- --- 5.4 document identity -------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.f01_document_version (
  document_version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  document_id       text NOT NULL,
  version_no        integer NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  document_digest   text NOT NULL,
  prior_document_digest text,
  document_class    text NOT NULL,
  content_digest    text NOT NULL,
  preparation_state text NOT NULL,
  delivery_state    text NOT NULL,
  signature_state   text NOT NULL,
  validity_state    text NOT NULL,
  version_state     text NOT NULL,
  object_key        text,
  object_sealed     boolean,
  onedrive_drive_id text,
  onedrive_item_id  text,
  onedrive_filing_state text,
  official_filing_state text NOT NULL,
  policy_digest     text,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_document_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_document_kind CHECK (envelope ->> 'record_kind' = 'stored_document_version'),
  CONSTRAINT f01_document_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_document_record_digest
    CHECK (document_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND document_digest = envelope ->> 'record_digest'),
  -- All five axes are stored INDEPENDENTLY, because collapsing them is how a
  -- document ends up "sent" with nobody able to say whether it was signed.
  CONSTRAINT f01_document_binding
    CHECK (document_id = envelope -> 'record' -> 'neon_identity' ->> 'document_id'
           AND version_no = (envelope -> 'record' -> 'neon_identity' ->> 'version_no')::integer
           AND content_digest = envelope -> 'record' -> 'neon_identity' ->> 'content_digest'
           AND document_class = envelope -> 'record' ->> 'document_class'
           AND preparation_state = envelope -> 'record' ->> 'preparation_state'
           AND delivery_state = envelope -> 'record' ->> 'delivery_state'
           AND signature_state = envelope -> 'record' ->> 'signature_state'
           AND validity_state = envelope -> 'record' ->> 'validity_state'
           AND version_state = envelope -> 'record' ->> 'version_state'),
  CONSTRAINT f01_document_states
    CHECK (preparation_state IN ('not_started', 'drafting', 'ready_for_review', 'approved_for_delivery')
           AND delivery_state IN ('undelivered', 'delivered', 'delivery_failed')
           AND signature_state IN ('unsigned', 'partially_signed', 'fully_executed', 'signature_declined')
           AND validity_state IN ('draft', 'effective', 'expired', 'superseded', 'void')
           AND version_state IN ('current', 'superseded', 'withdrawn')
           AND (onedrive_filing_state IS NULL
                OR onedrive_filing_state IN ('filed', 'pending', 'failed'))),
  -- Q125's one forbidden inference, made structural: full execution without a
  -- FILED OneDrive copy is recorded as visibly incomplete, and no object-storage
  -- or Neon success can make it read otherwise.
  CONSTRAINT f01_document_official_filing
    CHECK (official_filing_state IN ('filed', 'not_required', 'incomplete_official_filing')
           AND (signature_state <> 'fully_executed'
                OR (official_filing_state = 'filed' AND onedrive_filing_state = 'filed')
                OR official_filing_state = 'incomplete_official_filing')),
  CONSTRAINT f01_document_version_no CHECK (version_no >= 1),
  CONSTRAINT f01_document_content_digest CHECK (ops.f01_is_digest_ref(content_digest)),
  CONSTRAINT f01_document_prior_shape
    CHECK (prior_document_digest IS NULL OR ops.f01_is_digest_ref(prior_document_digest)),
  CONSTRAINT f01_document_prior_not_self
    CHECK (prior_document_digest IS DISTINCT FROM document_digest)
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_document_version_digest_uq
  ON ops.f01_document_version (document_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_document_version_identity_uq
  ON ops.f01_document_version (tenant, document_id, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS f01_document_version_cas_uq
  ON ops.f01_document_version (tenant, document_id, prior_document_digest)
  WHERE prior_document_digest IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS f01_document_version_genesis_uq
  ON ops.f01_document_version (tenant, document_id)
  WHERE prior_document_digest IS NULL;

CREATE TABLE IF NOT EXISTS ops.f01_document_current (
  tenant            text NOT NULL,
  document_id       text NOT NULL,
  document_version_id bigint NOT NULL REFERENCES ops.f01_document_version (document_version_id),
  document_digest   text NOT NULL REFERENCES ops.f01_document_version (document_digest),
  updated_by        text NOT NULL,
  updated_at        timestamptz NOT NULL,
  PRIMARY KEY (tenant, document_id),
  CONSTRAINT f01_document_current_tenant CHECK (tenant = ops.f01_tenant())
);

-- --- 5.5 preservation holds ------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.f01_preservation_hold_event (
  hold_event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  hold_id           text NOT NULL,
  hold_seq          integer NOT NULL,
  artifact_digest   text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  hold_digest       text NOT NULL,
  prior_hold_digest text,
  hold_state        text NOT NULL,
  placed_at_text    text NOT NULL,
  released_at_text  text,
  -- Nullable on purpose: a preservation hold is an authority act about ONE
  -- stored artifact and does not depend on a field-authority registry existing.
  -- Recording a placeholder digest here would be a claim about policy nobody
  -- made.
  policy_digest     text,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_hold_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_hold_kind CHECK (envelope ->> 'record_kind' = 'stored_preservation_hold'),
  CONSTRAINT f01_hold_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_hold_record_digest
    CHECK (hold_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND hold_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_hold_binding
    CHECK (hold_id = envelope -> 'record' ->> 'hold_id'
           AND artifact_digest = envelope -> 'record' ->> 'artifact_digest'
           AND hold_state = envelope -> 'record' ->> 'state'
           AND placed_at_text = envelope -> 'record' ->> 'placed_at'
           AND released_at_text IS NOT DISTINCT FROM (envelope -> 'record' ->> 'released_at')
           AND prior_hold_digest IS NOT DISTINCT FROM (envelope -> 'record' ->> 'prior_hold_digest')),
  CONSTRAINT f01_hold_state CHECK (hold_state IN ('active', 'released', 'expired', 'unknown')),
  -- A released or expired hold that names no release moment is a hold whose
  -- release nobody can point to, and the release is the whole reason it stopped
  -- blocking.
  CONSTRAINT f01_hold_release_time
    CHECK ((hold_state = 'active' AND released_at_text IS NULL)
           OR (hold_state = 'released' AND released_at_text IS NOT NULL)
           OR hold_state IN ('expired', 'unknown')),
  CONSTRAINT f01_hold_times_shape
    CHECK (ops.f01_is_instant_text(placed_at_text)
           AND (released_at_text IS NULL OR ops.f01_is_instant_text(released_at_text))),
  CONSTRAINT f01_hold_seq CHECK (hold_seq >= 1),
  CONSTRAINT f01_hold_chain
    CHECK ((hold_seq = 1 AND prior_hold_digest IS NULL)
           OR (hold_seq > 1 AND ops.f01_is_digest_ref(prior_hold_digest))),
  -- A hold NEVER deletes the artifact it protects, and the row says so.
  CONSTRAINT f01_hold_deletes_nothing
    CHECK ((envelope ->> 'deletes_artifact') = 'false')
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_hold_event_digest_uq
  ON ops.f01_preservation_hold_event (hold_digest);
CREATE UNIQUE INDEX IF NOT EXISTS f01_hold_event_seq_uq
  ON ops.f01_preservation_hold_event (tenant, hold_id, hold_seq);
CREATE UNIQUE INDEX IF NOT EXISTS f01_hold_event_cas_uq
  ON ops.f01_preservation_hold_event (tenant, hold_id, prior_hold_digest)
  WHERE prior_hold_digest IS NOT NULL;

CREATE TABLE IF NOT EXISTS ops.f01_preservation_hold_current (
  tenant            text NOT NULL,
  hold_id           text NOT NULL,
  artifact_digest   text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  hold_event_id     bigint NOT NULL REFERENCES ops.f01_preservation_hold_event (hold_event_id),
  hold_digest       text NOT NULL REFERENCES ops.f01_preservation_hold_event (hold_digest),
  hold_state        text NOT NULL,
  hold_seq          integer NOT NULL,
  updated_by        text NOT NULL,
  updated_at        timestamptz NOT NULL,
  PRIMARY KEY (tenant, hold_id),
  CONSTRAINT f01_hold_current_tenant CHECK (tenant = ops.f01_tenant()),
  CONSTRAINT f01_hold_current_state CHECK (hold_state IN ('active', 'released', 'expired', 'unknown'))
);

-- --- 5.6 deletion evaluations ----------------------------------------------

CREATE TABLE IF NOT EXISTS ops.f01_deletion_evaluation (
  evaluation_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant            text NOT NULL,
  artifact_digest   text NOT NULL REFERENCES ops.f01_corporate_artifact (artifact_digest),
  artifact_class    text NOT NULL,
  envelope          jsonb NOT NULL,
  envelope_digest   text NOT NULL,
  evaluation_digest text NOT NULL,
  decision          text NOT NULL,
  reason_id         text NOT NULL,
  receipt_digest    text,
  policy_digest     text NOT NULL,
  hold_inventory_digest text NOT NULL,
  actor_slug        text NOT NULL,
  recorded_at       timestamptz NOT NULL,
  idempotency_key   text NOT NULL,

  CONSTRAINT f01_deletion_tenant CHECK (tenant = ops.f01_tenant() AND tenant = envelope ->> 'tenant'),
  CONSTRAINT f01_deletion_kind CHECK (envelope ->> 'record_kind' = 'stored_deletion_evaluation'),
  CONSTRAINT f01_deletion_envelope_digest CHECK (envelope_digest = ops.f01_digest_jsonb(envelope)),
  CONSTRAINT f01_deletion_record_digest
    CHECK (evaluation_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND evaluation_digest = envelope ->> 'record_digest'),
  CONSTRAINT f01_deletion_binding
    CHECK (artifact_digest = envelope -> 'record' ->> 'artifact_digest'
           AND artifact_class = envelope -> 'record' ->> 'artifact_class'
           AND decision = envelope -> 'record' ->> 'decision'
           AND reason_id = envelope -> 'record' ->> 'reason_id'),
  CONSTRAINT f01_deletion_decision CHECK (decision IN ('allow', 'refuse')),
  -- NO HANDLER DELETES BYTES OR ROWS, and an allow never claims an external
  -- purge happened. Both are asserted on the stored row, not merely in prose.
  CONSTRAINT f01_deletion_performs_nothing
    CHECK ((envelope ->> 'silent_purge') = 'false'
           AND (envelope ->> 'purge_without_proof') = 'false'
           AND (envelope ->> 'bytes_deleted') = 'false'
           AND (envelope ->> 'external_purge_performed') = 'false'),
  CONSTRAINT f01_deletion_receipt_only_on_allow
    CHECK ((decision = 'allow' AND receipt_digest IS NOT NULL)
           OR (decision = 'refuse' AND receipt_digest IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS f01_deletion_evaluation_digest_uq
  ON ops.f01_deletion_evaluation (evaluation_digest);

-- ADDED AS AN ALTER rather than inside the CREATE TABLE above, because
-- CREATE TABLE IF NOT EXISTS leaves an already-installed table untouched and a
-- constraint that only reached fresh databases would be no constraint at all.
--
-- WHAT IT MAKES IMPOSSIBLE. An evaluation that does not say which coverage
-- answer it was taken against, and — the one that matters — an ALLOW recorded
-- while that answer is anything other than "established". The writer refuses the
-- same shape earlier and with a better message; this is the version that holds
-- even if somebody edits the writer.
--
-- THE TWO IS NOT NULL CONJUNCTS ARE THE WHOLE POINT OF THE CURRENT SHAPE, and
-- they lead deliberately. An earlier form opened with `... IN ('unknown',
-- 'established')` alone: over an ABSENT key `->>` yields SQL NULL, NULL IN (...)
-- is NULL, ops.f01_is_digest_ref is non-strict over `~` and answers NULL too, and
-- for decision = 'allow' the last conjunct becomes `false OR NULL` = NULL. A
-- CHECK fails only on FALSE, so an ALLOW carrying NO coverage fields at all was
-- accepted by the very constraint written to hold when the writer is edited —
-- which is exactly the shape an edited or regressed writer produces. FALSE AND
-- NULL is FALSE, so testing presence first is what makes the rest bind.
--
-- NOT VALID, STATED HONESTLY. Rows written before this tightening are NOT
-- re-checked and are NOT retro-verified; they are unproven, and this constraint
-- makes no claim about them. It binds every evaluation inserted from here on.
-- The alternative — a validating ADD CONSTRAINT — would abort the entire apply
-- on the first pre-existing evaluation that predates the coverage fields, taking
-- the schema down with it to say something about history that nobody needs said.
ALTER TABLE ops.f01_deletion_evaluation
  DROP CONSTRAINT IF EXISTS f01_deletion_coverage_bound;
ALTER TABLE ops.f01_deletion_evaluation
  ADD CONSTRAINT f01_deletion_coverage_bound
  CHECK ((envelope -> 'record' ->> 'derivative_coverage_state') IS NOT NULL
         AND (envelope -> 'record' ->> 'derivative_coverage_digest') IS NOT NULL
         AND (envelope -> 'record' ->> 'derivative_coverage_state') IN ('unknown', 'established')
         AND ops.f01_is_digest_ref(envelope -> 'record' ->> 'derivative_coverage_digest')
         AND (decision = 'refuse'
              OR (envelope -> 'record' ->> 'derivative_coverage_state') = 'established'))
  NOT VALID;

-- THE SECOND HALF OF THE SAME DISCIPLINE, for the retention TRIGGER.
--
-- WHAT IT MAKES IMPOSSIBLE. An evaluation that does not say which trigger its
-- retention period was measured from, one that names a trigger kind this contract
-- does not register, one whose start instant is unreadable, and — the one that
-- matters — one whose trigger admits it used the source's own observed instant.
-- The writer refuses each of those earlier and with a better message; this is the
-- version that holds if somebody edits the writer, which is exactly when a
-- retention period measured from a value a source can backdate would come back.
--
-- PRESENCE IS TESTED FIRST, for the reason the coverage constraint above spells
-- out: `->>` over an ABSENT key is SQL NULL, ops.f01_is_instant_text is non-strict
-- over `~` and answers NULL too, and a CHECK fails only on FALSE — so a record
-- carrying no clock at all would satisfy a constraint written the other way round.
--
-- NOT VALID, STATED HONESTLY. Evaluations written before this tightening are NOT
-- re-checked and are NOT retro-verified; they were taken against a period measured
-- from the artifact's observed instant, and this constraint makes no claim about
-- them. It binds every evaluation inserted from here on.
ALTER TABLE ops.f01_deletion_evaluation
  DROP CONSTRAINT IF EXISTS f01_deletion_retention_clock_bound;
ALTER TABLE ops.f01_deletion_evaluation
  ADD CONSTRAINT f01_deletion_retention_clock_bound
  CHECK ((envelope -> 'record' ->> 'retention_clock_digest') IS NOT NULL
         AND (envelope -> 'record' -> 'retention_clock' ->> 'kind') IS NOT NULL
         AND (envelope -> 'record' -> 'retention_clock' ->> 'started_at') IS NOT NULL
         AND ops.f01_is_digest_ref(envelope -> 'record' ->> 'retention_clock_digest')
         AND (envelope -> 'record' -> 'retention_clock' ->> 'kind')
               IN ('server_recorded_custody', 'explicit_retention_clock_event')
         AND ops.f01_is_instant_text(
               envelope -> 'record' -> 'retention_clock' ->> 'started_at')
         AND coalesce(envelope -> 'record' -> 'retention_clock'
                        ->> 'source_observed_at_used', '') = 'false')
  NOT VALID;

-- --- 5.7 idempotency -------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.f01_idempotency (
  tenant            text NOT NULL,
  operation         text NOT NULL,
  idempotency_key   text NOT NULL,
  request_digest    text NOT NULL,
  result            jsonb,
  result_digest     text,
  actor_slug        text NOT NULL,
  claimed_at        timestamptz NOT NULL,
  settled_at        timestamptz,
  PRIMARY KEY (tenant, operation, idempotency_key),
  CONSTRAINT f01_idempotency_tenant CHECK (tenant = ops.f01_tenant()),
  CONSTRAINT f01_idempotency_request_digest CHECK (ops.f01_is_digest_ref(request_digest)),
  CONSTRAINT f01_idempotency_result_digest
    CHECK (result_digest IS NULL OR result_digest = ops.f01_digest_jsonb(result))
);

-- An idempotency key is bound to ONE operation and ONE payload, so a key can
-- never substitute one write for another.
CREATE UNIQUE INDEX IF NOT EXISTS f01_idempotency_key_operation_uq
  ON ops.f01_idempotency (tenant, idempotency_key, operation);

-- ===========================================================================
-- 6. Guard triggers.
--
-- Direct-DML guards on every relation; append-only guards on every history
-- relation. The three CURRENT-state relations and the idempotency ledger admit
-- UPDATE through a registered writer — that is what "current state" means — but
-- still refuse a DELETE, a TRUNCATE and any write that did not arrive through
-- one of the writers.
-- ===========================================================================

DO $guards$
DECLARE
  v_table text;
  v_append_only boolean;
BEGIN
  FOR v_table, v_append_only IN
    SELECT * FROM (VALUES
      ('f01_policy_version', true),
      ('f01_policy_current', false),
      ('f01_field_state', false),
      ('f01_field_event', true),
      ('f01_state_transition', true),
      ('f01_mutation_receipt', true),
      ('f01_reconciliation_item', true),
      ('f01_corporate_artifact', true),
      ('f01_parsed_proposal', true),
      ('f01_proposal_link', true),
      ('f01_derivative_link', true),
      ('f01_document_version', true),
      ('f01_document_current', false),
      ('f01_preservation_hold_event', true),
      ('f01_preservation_hold_current', false),
      ('f01_deletion_evaluation', true),
      ('f01_idempotency', false)
    ) AS t(name, append_only)
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON ops.%I', v_table || '_dml_guard', v_table);
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON ops.%I '
      'FOR EACH ROW EXECUTE FUNCTION ops.f01_guard_direct_dml()',
      v_table || '_dml_guard', v_table);

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON ops.%I', v_table || '_truncate_guard', v_table);
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON ops.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION ops.f01_guard_no_truncate()',
      v_table || '_truncate_guard', v_table);

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON ops.%I', v_table || '_append_only', v_table);
    IF v_append_only THEN
      EXECUTE format(
        'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON ops.%I '
        'FOR EACH ROW EXECUTE FUNCTION ops.f01_guard_append_only()',
        v_table || '_append_only', v_table);
    ELSE
      -- Current-state and ledger relations still refuse DELETE: a current row is
      -- replaced in place, never removed to make a history gap plausible.
      EXECUTE format(
        'CREATE TRIGGER %I BEFORE DELETE ON ops.%I '
        'FOR EACH ROW EXECUTE FUNCTION ops.f01_guard_append_only()',
        v_table || '_append_only', v_table);
    END IF;
  END LOOP;
END;
$guards$;

-- ===========================================================================
-- 7. Verification helpers.
--
-- Every readback and every write runs through these, so "the digest was
-- recomputed" is one implementation rather than nine hopeful copies.
-- ===========================================================================

/**
 * Recompute both digests of one stored envelope and refuse if either disagrees.
 *
 * THERE IS NO FALLBACK. A corrupt newest row raises; it does not quietly resolve
 * to the previous healthy version, because answering from an older row is how a
 * reader ends up confidently wrong about the current state.
 */
CREATE OR REPLACE FUNCTION ops.f01_verify_envelope(
  p_envelope jsonb, p_envelope_digest text, p_record_digest text, p_record_kind text)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_envelope_digest text;
  v_record_digest text;
BEGIN
  IF p_envelope IS NULL THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: % row carries no preimage', p_record_kind
      USING ERRCODE = '22000';
  END IF;
  IF (p_envelope ->> 'record_kind') IS DISTINCT FROM p_record_kind THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: expected record_kind %, stored %',
      p_record_kind, p_envelope ->> 'record_kind' USING ERRCODE = '22000';
  END IF;
  IF (p_envelope ->> 'tenant') IS DISTINCT FROM ops.f01_tenant() THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: % row is not bound to the one tenant', p_record_kind
      USING ERRCODE = '22000';
  END IF;
  v_envelope_digest := ops.f01_digest_jsonb(p_envelope);
  IF v_envelope_digest IS DISTINCT FROM p_envelope_digest THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: % envelope digest recomputed as %, stored %',
      p_record_kind, v_envelope_digest, p_envelope_digest USING ERRCODE = '22000';
  END IF;
  v_record_digest := ops.f01_digest_jsonb(p_envelope -> 'record');
  IF v_record_digest IS DISTINCT FROM p_record_digest
     OR v_record_digest IS DISTINCT FROM (p_envelope ->> 'record_digest') THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: % record digest recomputed as %, stored %',
      p_record_kind, v_record_digest, p_record_digest USING ERRCODE = '22000';
  END IF;
  RETURN jsonb_build_object(
    'record_kind', p_record_kind,
    'envelope', p_envelope,
    'envelope_digest', v_envelope_digest,
    'record', p_envelope -> 'record',
    'record_digest', v_record_digest,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

/** The current stored policy, recomputed. NULL when no version is installed. */
CREATE OR REPLACE FUNCTION ops.f01_current_policy()
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_policy_version%ROWTYPE;
  v_current ops.f01_policy_current%ROWTYPE;
BEGIN
  SELECT * INTO v_current FROM ops.f01_policy_current WHERE tenant = ops.f01_tenant();
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  SELECT * INTO v_row FROM ops.f01_policy_version WHERE policy_seq = v_current.policy_seq;
  IF NOT FOUND OR v_row.policy_digest IS DISTINCT FROM v_current.policy_digest THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: the current policy pointer names no intact version'
      USING ERRCODE = '22000';
  END IF;
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.policy_digest, 'stored_policy_version');
  -- The two registries are recomputed from the stored preimages, not trusted.
  IF ops.f01_digest_jsonb(v_row.envelope -> 'record' -> 'field_registry')
       IS DISTINCT FROM v_row.field_registry_digest
     OR ops.f01_digest_jsonb(v_row.envelope -> 'record' -> 'retention_registry')
       IS DISTINCT FROM v_row.retention_registry_digest THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: a stored registry no longer hashes to its digest'
      USING ERRCODE = '22000';
  END IF;
  RETURN jsonb_build_object(
    'policy_seq', v_row.policy_seq,
    'registry_version', v_row.registry_version,
    'policy_digest', v_row.policy_digest,
    'prior_policy_digest', v_row.prior_policy_digest,
    'field_registry', v_row.envelope -> 'record' -> 'field_registry',
    'field_registry_digest', v_row.field_registry_digest,
    'retention_registry', v_row.envelope -> 'record' -> 'retention_registry',
    'retention_registry_digest', v_row.retention_registry_digest,
    'domain_policy_digest', v_row.domain_policy_digest,
    'installed_by', v_row.installed_by,
    'installed_at', v_row.installed_at_text,
    'envelope', v_row.envelope,
    'envelope_digest', v_row.envelope_digest,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_current_policy_digest()
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT (ops.f01_current_policy()) ->> 'policy_digest' $$;

/** The current state for one field, recomputed. NULL when nothing is established. */
CREATE OR REPLACE FUNCTION ops.f01_current_field_state(p_entity text, p_field text)
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_field_state%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_field_state
   WHERE tenant = ops.f01_tenant() AND entity = p_entity AND field = p_field;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.state_digest, 'stored_field_state');
  RETURN jsonb_build_object(
    'entity', v_row.entity, 'field', v_row.field,
    'state_digest', v_row.state_digest,
    'current_state', v_row.envelope -> 'record',
    'envelope', v_row.envelope,
    'envelope_digest', v_row.envelope_digest,
    'policy_digest', v_row.policy_digest,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

/** One stored artifact, recomputed. The handler's prior-identity evidence. */
CREATE OR REPLACE FUNCTION ops.f01_stored_artifact(p_artifact_digest text)
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_corporate_artifact%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_corporate_artifact
   WHERE tenant = ops.f01_tenant() AND artifact_digest = p_artifact_digest;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.artifact_digest, 'stored_corporate_artifact');
  RETURN jsonb_build_object(
    'artifact_digest', v_row.artifact_digest,
    'artifact', v_row.envelope -> 'record',
    -- THE SOURCE'S OWN INSTANT. `created_at` is the artifact's observed_at: what
    -- the source says it saw, and part of the artifact's identity and provenance.
    -- It is NOT when this record layer took custody of it, and it is not what a
    -- retention period runs from — see ops.f01_retention_clock below.
    'created_at', v_row.observed_at_text,
    'source_observed_at', v_row.observed_at_text,
    -- THE SERVER'S OWN INSTANT, stamped by the writer that stored the row and
    -- selectable by nobody. Returned here so a caller never has to reach for the
    -- source's timestamp to answer a question about custody.
    'recorded_at', ops.f01_instant_text(v_row.recorded_at),
    'envelope', v_row.envelope,
    'envelope_digest', v_row.envelope_digest,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

/** The artifact identity a candidate would collide with, or NULL. */
CREATE OR REPLACE FUNCTION ops.f01_stored_artifact_by_identity(
  p_source_system text, p_source_account text, p_native_id text,
  p_native_id_epoch text, p_native_version text)
RETURNS jsonb
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_corporate_artifact%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_corporate_artifact
   WHERE tenant = ops.f01_tenant()
     AND source_system = p_source_system AND source_account = p_source_account
     AND native_id = p_native_id AND native_id_epoch = p_native_id_epoch
     AND native_version = p_native_version;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.artifact_digest, 'stored_corporate_artifact');
  RETURN jsonb_build_object(
    'artifact_digest', v_row.artifact_digest,
    'artifact', v_row.envelope -> 'record',
    'integrity', 'recomputed_from_committed_row');
END;
$$;

/**
 * The hold inventory for one artifact, LOADED rather than accepted.
 *
 * An absent inventory is not an empty one, so this returns the verified list —
 * possibly empty — and the deletion writer binds the evaluation to its digest.
 * A caller cannot describe a cleaner set of holds than the database holds.
 */
CREATE OR REPLACE FUNCTION ops.f01_hold_inventory(p_artifact_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  c ops.f01_preservation_hold_current%ROWTYPE;
  e ops.f01_preservation_hold_event%ROWTYPE;
  v_holds jsonb := '[]'::jsonb;
BEGIN
  -- ORDER BY hold_id COLLATE "C". This list is DIGESTED — the deletion writer
  -- binds its evaluation to ops.f01_hold_inventory_digest — so its order is part
  -- of a stored, later re-derived value. hold_id is arbitrary caller text, and a
  -- non-C collation reorders text containing punctuation ('hold-b' before or
  -- after 'holda' depending on the locale), so the same rows on two clusters
  -- would otherwise digest differently. C order is byte order everywhere.
  FOR c IN SELECT * FROM ops.f01_preservation_hold_current
    WHERE tenant = ops.f01_tenant() AND artifact_digest = p_artifact_digest
    ORDER BY hold_id COLLATE "C"
  LOOP
    SELECT * INTO e FROM ops.f01_preservation_hold_event WHERE hold_event_id = c.hold_event_id;
    IF NOT FOUND OR e.tenant IS DISTINCT FROM c.tenant
      OR e.hold_id IS DISTINCT FROM c.hold_id OR e.artifact_digest IS DISTINCT FROM c.artifact_digest
      OR e.hold_digest IS DISTINCT FROM c.hold_digest OR e.hold_seq IS DISTINCT FROM c.hold_seq
      OR e.hold_state IS DISTINCT FROM c.hold_state THEN
      RAISE EXCEPTION 'f01_corrupt_stored_record: hold pointer mismatch' USING ERRCODE = '22000';
    END IF;
    PERFORM ops.f01_verify_envelope(e.envelope, e.envelope_digest, e.hold_digest, 'stored_preservation_hold');
    IF e.hold_state IS DISTINCT FROM (e.envelope->'record'->>'state')
      OR e.placed_at_text IS DISTINCT FROM (e.envelope->'record'->>'placed_at')
      OR e.released_at_text IS DISTINCT FROM (e.envelope->'record'->>'released_at') THEN
      RAISE EXCEPTION 'f01_corrupt_stored_record: hold columns mismatch' USING ERRCODE = '22000';
    END IF;
    v_holds := v_holds || jsonb_build_array(jsonb_build_object(
      'hold_id', c.hold_id, 'state', c.hold_state,
      'placed_at', e.placed_at_text, 'released_at', e.released_at_text));
  END LOOP;
  RETURN v_holds;
END;
$$;

/**
 * Every REGISTERED derivative-source link for one artifact, recomputed.
 *
 * This is the ingress the approved registration rule added (section 5.3.1 names
 * the approval): rows written by trusted producer workflows saying which original
 * produced which derived record. Ordered by (derivative_kind, derivative_id) COLLATE "C" for the same
 * reason the hold inventory is: this list feeds a digest that is stored and
 * later re-derived, so its order has to be a property of the bytes rather than
 * of the cluster's locale.
 */
CREATE OR REPLACE FUNCTION ops.f01_derivative_links(p_artifact_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  r ops.f01_derivative_link%ROWTYPE;
  v_links jsonb := '[]'::jsonb;
BEGIN
  FOR r IN SELECT * FROM ops.f01_derivative_link
    WHERE tenant = ops.f01_tenant() AND source_artifact_digest = p_artifact_digest
    ORDER BY derivative_kind COLLATE "C", derivative_id COLLATE "C"
  LOOP
    PERFORM ops.f01_verify_envelope(r.envelope, r.envelope_digest, r.link_digest,
                                    'stored_derivative_link');
    IF r.derivative_kind IS DISTINCT FROM (r.envelope->'record'->>'derivative_kind')
      OR r.derivative_id IS DISTINCT FROM (r.envelope->'record'->>'derivative_id')
      OR r.source_artifact_digest IS DISTINCT FROM (r.envelope->'record'->>'source_artifact_digest')
      OR r.producer_workflow IS DISTINCT FROM (r.envelope->'record'->>'producer_workflow') THEN
      RAISE EXCEPTION 'f01_corrupt_stored_record: derivative link columns mismatch'
        USING ERRCODE = '22000';
    END IF;
    v_links := v_links || jsonb_build_array(jsonb_build_object(
      'link_digest', r.link_digest,
      'derivative_kind', r.derivative_kind,
      'derivative_id', r.derivative_id,
      'derivative_content_digest', r.derivative_content_digest,
      'producer_workflow', r.producer_workflow,
      'producer_run_ref', r.producer_run_ref,
      'produced_at', r.produced_at_text,
      'evidence_ref', r.evidence_ref,
      'evidence_digest', r.evidence_digest,
      'registered_by', r.actor_slug));
  END LOOP;
  RETURN v_links;
END;
$$;

/**
 * WHETHER THE REGISTERED LINKS FOR ONE ARTIFACT ARE THE WHOLE SET.
 *
 * THIS FUNCTION RETURNS 'unknown' FOR EVERY ARTIFACT, ON PURPOSE, AND SAYING SO
 * PLAINLY IS THE POINT OF IT. Registered links prove what WAS registered. To
 * know that they are ALL the derivatives, two further facts would have to be
 * established, and this slice has an ingress for neither:
 *
 *   (a) A CLOSED PRODUCER SET. Which trusted producer workflows may derive from
 *       this artifact's class at all. Without it, "no other links exist" and "no
 *       other producer has run yet" are indistinguishable.
 *   (b) A REGISTERED COMPLETION FROM EVERY ONE OF THEM for this exact artifact,
 *       so that each named producer has positively said it is finished with it
 *       rather than merely having written nothing so far.
 *
 * Neither may be asserted by a caller, and neither is inferable from row counts:
 * an empty ops.f01_derivative_link is exactly what an artifact with no
 * derivatives AND an artifact whose producers never registered anything both
 * look like. So the honest answer is 'unknown', the honest consequence is that
 * ops.f01_stored_derivatives below returns NULL and every deletion evaluation
 * fails closed, and the way to change that answer is to build (a) and (b) — not
 * to flip a flag here.
 *
 *   (c) AND A THIRD, WHICH IS NOT OPTIONAL EITHER. A registered link is its
 *       producer's attestation that a derivative exists; nothing loads the named
 *       derivative or checks its bytes against derivative_content_digest, and the
 *       foreign key binds the source side only. While the state is 'unknown' that
 *       costs nothing — an attested link only ever blocks a deletion harder. The
 *       moment 'established' becomes reachable it stops being free, because the
 *       kinds here flow into ops.f01_stored_derivatives and out into a deletion
 *       receipt as named survivors. So: coverage may not be established until
 *       producer outputs are INDEPENDENTLY BOUND AND VERIFIED, by something other
 *       than the caller that registered them. This is a precondition on that
 *       work, not a caveat about it.
 *
 * The observed links ARE returned alongside, because they are real evidence and
 * a reader should be able to see them; they are simply not a coverage claim.
 */
CREATE OR REPLACE FUNCTION ops.f01_derivative_coverage(p_artifact_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_links jsonb := ops.f01_derivative_links(p_artifact_digest);
  v_kinds jsonb;
BEGIN
  -- COLLATE "C" for the same reason as everywhere else here: this list is inside
  -- a digest that is stored and later re-derived, so its order must be byte
  -- order on every cluster rather than the local locale's idea of it.
  SELECT coalesce(jsonb_agg(to_jsonb(s.kind) ORDER BY s.kind COLLATE "C"), '[]'::jsonb)
    INTO v_kinds
    FROM (SELECT DISTINCT e.link ->> 'derivative_kind' AS kind
            FROM jsonb_array_elements(v_links) AS e(link)) s;
  RETURN jsonb_build_object(
    'artifact_digest', p_artifact_digest,
    'state', 'unknown',
    'reason_id', 'producer_closure_not_established',
    'registered_derivative_kinds', v_kinds,
    'registered_links', v_links,
    'registered_link_count', jsonb_array_length(v_links),
    'is_exhaustive_inventory', false,
    'empty_link_set_means_verified_absence', false,
    'integrity', 'recomputed_from_committed_rows');
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_derivative_coverage_digest(p_artifact_digest text)
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT ops.f01_digest_jsonb(ops.f01_derivative_coverage(p_artifact_digest)) $$;

/**
 * The derivative inventory a deletion may be judged against, or NULL.
 *
 * NULL means UNKNOWN and never "none". The registered kinds are returned only
 * when coverage is established, which — see above — is nowhere in this slice.
 * Returning the observed kinds regardless would hand the deletion evaluator a
 * list that looks exhaustive and is not, which is precisely the substitution the
 * settled decision forbids.
 */
CREATE OR REPLACE FUNCTION ops.f01_stored_derivatives(p_artifact_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_coverage jsonb;
BEGIN
  PERFORM ops.f01_stored_artifact(p_artifact_digest);
  v_coverage := ops.f01_derivative_coverage(p_artifact_digest);
  IF (v_coverage ->> 'state') IS DISTINCT FROM 'established' THEN
    RETURN NULL;
  END IF;
  RETURN v_coverage -> 'registered_derivative_kinds';
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_hold_inventory_digest(p_artifact_digest text)
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT ops.f01_digest_jsonb(ops.f01_hold_inventory(p_artifact_digest)) $$;

/**
 * WHEN ONE ARTIFACT'S RETENTION PERIOD STARTED, derived and never accepted.
 *
 * THE DEFAULT IS CUSTODY, and custody is a SERVER STAMP. `recorded_at` is written
 * by the definer writer that stored the artifact, from now(), inside the
 * transaction that took the bytes into the record layer. No caller supplies it, no
 * source influences it, and no payload can move it.
 *
 * THE SOURCE'S OBSERVED INSTANT IS NOT A CLOCK, and this function will not let it
 * become one. `observed_at` is what a corporate source says it saw and when — it
 * is identity and provenance, it is the value an adapter, an export or a person
 * with access to that source can set to any date they like, and a retention period
 * measured from it can be shortened, or expired outright, by backdating a single
 * field. It is returned below as `source_observed_at` so an operator can see BOTH
 * timestamps and see that they are different facts, and `source_observed_at_used`
 * is false in the bytes this answer hashes to.
 *
 * THIS SLICE PRODUCES EXACTLY ONE KIND. A retention class may register an explicit
 * retention-clock EVENT — a typed event with its own provenance, not a timestamp
 * borrowed from somewhere else — but nothing in this schema produces, approves or
 * authenticates such an event, so no artifact has one and this function never
 * returns one. The kernel refuses a deletion for a class that registers an event
 * clock, by name, rather than quietly measuring from custody instead. Building the
 * event ingress is a separate piece of work; inventing an event here would be the
 * one shortcut that makes every later retention answer unverifiable.
 *
 * NULL FOR AN ARTIFACT NOBODY STORED. A clock for a row that does not exist would
 * be a period started by nothing.
 */
CREATE OR REPLACE FUNCTION ops.f01_retention_clock(p_artifact_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_corporate_artifact%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_corporate_artifact
   WHERE tenant = ops.f01_tenant() AND artifact_digest = p_artifact_digest;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  -- The row is verified before its instant is used, exactly as every other
  -- readback here verifies before it answers: a clock read off a corrupt row is a
  -- retention period measured from bytes nobody can vouch for.
  PERFORM ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                  v_row.artifact_digest, 'stored_corporate_artifact');
  IF v_row.observed_at_text IS DISTINCT FROM (v_row.envelope -> 'record' ->> 'observed_at') THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: artifact observed_at column mismatch'
      USING ERRCODE = '22000';
  END IF;
  RETURN jsonb_build_object(
    'artifact_digest', v_row.artifact_digest,
    'kind', 'server_recorded_custody',
    'event_kind', NULL,
    'started_at', ops.f01_instant_text(v_row.recorded_at),
    'reference', 'ops.f01_corporate_artifact.recorded_at',
    'provenance', 'server_stamped_custody',
    'event_digest', NULL,
    -- Verified because it is this server's own stamp on its own row, which is the
    -- only thing in this slice that can be. An explicit event would have to earn
    -- this flag from something that authenticated it, and nothing does yet.
    'verified', true,
    -- Both halves of the anti-alias statement: the source's instant is reported,
    -- and reported as NOT the thing the period runs from.
    'source_observed_at', v_row.observed_at_text,
    'source_observed_at_used', false,
    'integrity', 'recomputed_from_committed_row');
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_retention_clock_digest(p_artifact_digest text)
RETURNS text
LANGUAGE sql STABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT ops.f01_digest_jsonb(ops.f01_retention_clock(p_artifact_digest)) $$;

-- ===========================================================================
-- 8. Idempotency.
--
-- A key is bound to ONE operation and ONE request payload. A replay of the exact
-- payload returns the stored result; the same key over a different payload
-- refuses rather than substituting one write for another.
-- ===========================================================================

-- A read-only replay door, serialized with the private claim by tenant/key.
-- It accepts no result and never creates or settles a row.
CREATE OR REPLACE FUNCTION ops.f01_replay_outcome(
  p_operation text, p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_idempotency%ROWTYPE;
  v_actor text := ops.f01_context_actor_slug();
BEGIN
  -- The closed write-operation vocabulary. A new writer MUST be added here or it
  -- cannot claim, replay or settle a key at all; nothing here is a wildcard.
  IF p_operation IS NULL OR p_operation NOT IN (
    'register-record-source-authority-policy', 'record-source-observation',
    'record-corporate-artifact', 'record-parsed-proposal', 'register-derivative-source-link',
    'record-document-identity',
    'record-artifact-preservation-hold', 'evaluate-artifact-deletion') THEN
    RAISE EXCEPTION 'f01_unknown_operation' USING ERRCODE = '22023';
  END IF;
  IF p_operation IN ('register-record-source-authority-policy', 'record-artifact-preservation-hold') THEN
    PERFORM ops.f01_require_authority_principal(p_operation);
  END IF;
  IF p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'f01_idempotency_key_required' USING ERRCODE = '22023';
  END IF;
  IF p_request_digest IS NULL OR NOT ops.f01_is_digest_ref(p_request_digest) THEN
    RAISE EXCEPTION 'f01_idempotency_request_digest_malformed' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:request:' || ops.f01_tenant() || ':' || p_idempotency_key, 0));
  IF EXISTS (SELECT 1 FROM ops.f01_idempotency
              WHERE tenant = ops.f01_tenant() AND idempotency_key = p_idempotency_key
                AND operation <> p_operation) THEN
    RAISE EXCEPTION 'f01_idempotency_operation_mismatch' USING ERRCODE = '23505';
  END IF;
  SELECT * INTO v_row FROM ops.f01_idempotency
    WHERE tenant = ops.f01_tenant() AND operation = p_operation
      AND idempotency_key = p_idempotency_key;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF v_row.actor_slug IS DISTINCT FROM v_actor THEN
    RAISE EXCEPTION 'f01_idempotency_actor_mismatch' USING ERRCODE = '42501';
  END IF;
  IF v_row.request_digest IS DISTINCT FROM p_request_digest THEN
    RAISE EXCEPTION 'f01_idempotency_payload_mismatch' USING ERRCODE = '23505';
  END IF;
  IF v_row.result IS NULL OR v_row.settled_at IS NULL THEN
    RAISE EXCEPTION 'f01_unsettled_idempotency' USING ERRCODE = '22000';
  END IF;
  IF ops.f01_digest_jsonb(v_row.result) IS DISTINCT FROM v_row.result_digest
      OR (v_row.result ->> 'operation') IS DISTINCT FROM p_operation
      OR (v_row.result ->> 'actor_slug') IS DISTINCT FROM v_actor THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: idempotency outcome' USING ERRCODE = '22000';
  END IF;
  RETURN v_row.result;
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_claim_idempotency(
  p_operation text, p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_row ops.f01_idempotency%ROWTYPE;
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
BEGIN
  v_replay := ops.f01_replay_outcome(p_operation, p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN RETURN v_replay; END IF;
  IF p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'f01_idempotency_key_required: every F01 write carries one'
      USING ERRCODE = '22023';
  END IF;
  IF NOT ops.f01_is_digest_ref(p_request_digest) THEN
    RAISE EXCEPTION 'f01_idempotency_request_digest_malformed' USING ERRCODE = '22023';
  END IF;
  -- ON CONFLICT DO UPDATE takes the row lock, so a concurrent claim of the same
  -- key BLOCKS here and then reads the committed outcome, rather than racing
  -- past it and writing a second time.
  INSERT INTO ops.f01_idempotency AS i
    (tenant, operation, idempotency_key, request_digest, actor_slug, claimed_at)
  VALUES (ops.f01_tenant(), p_operation, p_idempotency_key, p_request_digest, v_actor, now())
  ON CONFLICT (tenant, operation, idempotency_key)
  DO UPDATE SET claimed_at = i.claimed_at
  RETURNING * INTO v_row;

  IF v_row.request_digest IS DISTINCT FROM p_request_digest THEN
    RAISE EXCEPTION 'f01_idempotency_payload_mismatch: key % already binds a different payload',
      p_idempotency_key USING ERRCODE = '23505';
  END IF;
  IF v_row.actor_slug IS DISTINCT FROM v_actor THEN
    RAISE EXCEPTION 'f01_idempotency_actor_mismatch: key % belongs to another actor',
      p_idempotency_key USING ERRCODE = '42501';
  END IF;
  -- A key already used for a DIFFERENT operation is a substitution attempt.
  IF EXISTS (SELECT 1 FROM ops.f01_idempotency
              WHERE tenant = ops.f01_tenant() AND idempotency_key = p_idempotency_key
                AND operation <> p_operation) THEN
    RAISE EXCEPTION 'f01_idempotency_operation_mismatch: key % already binds another operation',
      p_idempotency_key USING ERRCODE = '23505';
  END IF;
  RETURN v_row.result;
END;
$$;

CREATE OR REPLACE FUNCTION ops.f01_settle_idempotency(
  p_operation text, p_idempotency_key text, p_result jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
BEGIN
  UPDATE ops.f01_idempotency
     SET result = p_result,
         result_digest = ops.f01_digest_jsonb(p_result),
         settled_at = now()
   WHERE tenant = ops.f01_tenant() AND operation = p_operation
     AND idempotency_key = p_idempotency_key;
  RETURN p_result;
END;
$$;

-- ===========================================================================
-- 9. The writers.
--
-- Each one derives its actor, validates the tenant, serializes by natural
-- identity, enforces its compare-and-swap, verifies every digest it is handed by
-- RECOMPUTING it, and returns a readback rebuilt from the committed-shape row in
-- the same transaction. None of them accepts an actor, a tenant, a clock or a
-- prior state from its caller.
--
-- REPLAY BEFORE STATE. Every writer below calls ops.f01_claim_idempotency — and
-- therefore ops.f01_replay_outcome — BEFORE it reads any current pointer or
-- evaluates any compare-and-swap. That ordering is load-bearing rather than
-- incidental: a settled key must return its stored result even though the world
-- has moved on since, and a replay that ran after the CAS would raise
-- f01_stale_* for a request that had already succeeded. Do not hoist a state
-- read, a CAS or a policy comparison above the claim.
--
-- THE LOCK HIERARCHY, and it is acyclic. Every advisory lock any writer takes
-- appears in exactly one tier, and no writer ever takes a lock from a lower tier
-- before one from a higher tier. A new writer, or a new lock in an existing one,
-- MUST slot into this order or the deadlock freedom below stops being true.
--
--   tier 1  f01:request:<tenant>:<key>            (f01_replay_outcome; shared
--           with the private claim, and re-entrant within one transaction, so a
--           caller that probes for a replay and then writes does not self-block)
--   tier 2  f01:policy:<tenant>                   (exclusive in f01_install_policy;
--           SHARED in f01_apply_observation, f01_record_proposal and
--           f01_record_deletion_evaluation, which read the policy but never move it)
--   tier 3  f01:field:<tenant>:<entity>:<field>   (f01_apply_observation)
--           f01:artifact:<identity tuple>         (f01_record_artifact)
--           f01:artifact-retention:<tenant>:<artifact digest>   [tier 3a]
--                                                 (f01_record_hold,
--           f01_record_deletion_evaluation AND f01_register_derivative_link —
--           deliberately the SAME key for all three, so that neither a hold nor
--           a derivative registration can land underneath a deletion evaluation
--           that has already read the inventory and the coverage it will be
--           bound to. f01_record_proposal takes it too, because it registers a
--           derivative link of its own, and so does the document writer once it
--           registers one)
--           f01:document:<tenant>:<document_id>   [tier 3b] (f01_record_document)
--   tier 4  f01:hold:<tenant>:<hold_id>           (f01_record_hold)
--           f01:derivative:<tenant>:<kind>:<id>   (f01_register_derivative_link,
--                                                  f01_record_proposal and
--                                                  f01_record_document)
--
-- TIER 3 IS ORDERED INTERNALLY: RETENTION BEFORE DOCUMENT, and that ordering is
-- what keeps this acyclic now that one writer takes two tier-3 keys. The document
-- writer, when the version it is completing is DERIVED, acquires
-- f01:artifact-retention: (3a) and only then f01:document: (3b) and only then
-- f01:derivative: (4) — for the same reason f01_record_proposal takes the
-- retention key: a link must not land underneath a deletion evaluation that has
-- already read the artifact's coverage and bound its record to the digest of it.
--
-- WHY THE BACK EDGE CANNOT EXIST. No other writer takes f01:document: at all, so
-- no other writer can hold 3b while waiting for 3a, and every writer that takes
-- f01:artifact-retention: takes it FIRST among tier 3 and above. The remaining
-- tier-3 keys — f01:field: and f01:artifact: — are taken by writers that take no
-- second tier-3 key. Tier 4 keys are disjoint from each other and no writer takes
-- two of them. The observed acquisition orders are therefore exactly 1→2, 1→2→3,
-- 1→2→3→4, 1→3, 1→3→4 and 1→3a→3b→4, which is a strict order with no edge from a
-- lower tier — or from a later tier-3 key — back to an earlier one. Hence no
-- cycle, hence no deadlock between any two F01 writers.
--
-- A WRITER THAT TAKES BOTH TIER-3 KEYS MUST TAKE THEM IN THAT ORDER. This is the
-- one place that constraint is written down; a future writer that took the
-- document key before the retention key would reintroduce the cycle this note
-- exists to rule out.
--
-- ONE CLAIM PER TRANSACTION-TIER-1 KEY. f01_record_proposal writes a derivative
-- link through the PRIVATE ops.f01_insert_derivative_link rather than by calling
-- the public writer, precisely so that it does not take a second tier-1 request
-- lock after a tier-2 policy lock. Its own idempotency key already covers both
-- records: they are written in one transaction and replayed as one outcome.
-- ===========================================================================

-- --- 9.1 register-record-source-authority-policy ---------------------------

/**
 * Append one exact field-authority plus retention-registry version.
 *
 * humanOnly plus authorityOnly, checked HERE and not only in the handler, so an
 * ordinary evidence writer that somehow reached this function still refuses.
 * The compare-and-swap is the prior-current digest; the unique index on
 * (tenant, prior_policy_digest) makes a concurrent double install impossible
 * rather than unlikely.
 */
CREATE OR REPLACE FUNCTION ops.f01_install_policy(
  p_envelope jsonb, p_expected_prior_policy_digest text,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text;
  v_replay jsonb;
  v_record jsonb;
  v_digest text;
  v_prior text;
  v_seq bigint;
  v_result jsonb;
BEGIN
  v_actor := ops.f01_require_authority_principal('register-record-source-authority-policy');
  v_replay := ops.f01_claim_idempotency(
    'register-record-source-authority-policy', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;

  -- One installer at a time, so the CAS below is decided rather than raced.
  PERFORM pg_advisory_xact_lock(hashtextextended('f01:policy:' || ops.f01_tenant(), 0));

  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_policy_digest_mismatch: the supplied policy does not hash to its claim'
      USING ERRCODE = '22000';
  END IF;
  IF (v_record ->> 'installed_by') IS DISTINCT FROM v_actor THEN
    RAISE EXCEPTION 'f01_actor_injection_refused: installed_by is derived, never supplied'
      USING ERRCODE = '42501';
  END IF;

  SELECT policy_digest INTO v_prior FROM ops.f01_policy_current WHERE tenant = ops.f01_tenant();
  IF v_prior IS DISTINCT FROM p_expected_prior_policy_digest
     OR v_prior IS DISTINCT FROM (v_record ->> 'prior_policy_digest') THEN
    RAISE EXCEPTION 'f01_stale_policy_digest: current is %, the caller decided against %',
      coalesce(v_prior, 'none'), coalesce(p_expected_prior_policy_digest, 'none')
      USING ERRCODE = '40001';
  END IF;

  INSERT INTO ops.f01_policy_version
    (tenant, registry_version, envelope, envelope_digest, policy_digest, prior_policy_digest,
     field_registry_digest, retention_registry_digest, domain_policy_digest,
     installed_by, installed_at_text, installed_at, idempotency_key)
  VALUES (
    ops.f01_tenant(),
    (v_record ->> 'registry_version')::integer,
    p_envelope,
    ops.f01_digest_jsonb(p_envelope),
    v_digest,
    v_record ->> 'prior_policy_digest',
    v_record ->> 'field_registry_digest',
    v_record ->> 'retention_registry_digest',
    v_record ->> 'domain_policy_digest',
    v_actor,
    v_record ->> 'installed_at',
    ops.f01_instant(v_record ->> 'installed_at'),
    p_idempotency_key)
  RETURNING policy_seq INTO v_seq;

  INSERT INTO ops.f01_policy_current (tenant, policy_seq, policy_digest, updated_by, updated_at)
  VALUES (ops.f01_tenant(), v_seq, v_digest, v_actor, now())
  ON CONFLICT (tenant) DO UPDATE
    SET policy_seq = EXCLUDED.policy_seq, policy_digest = EXCLUDED.policy_digest,
        updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;

  v_result := jsonb_build_object(
    'operation', 'register-record-source-authority-policy',
    'outcome', 'installed',
    'actor_slug', v_actor,
    'prior_policy_digest', v_record ->> 'prior_policy_digest',
    'readback', ops.f01_current_policy(),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency(
    'register-record-source-authority-policy', p_idempotency_key, v_result);
END;
$$;

-- --- 9.2 record-source-observation -----------------------------------------

/**
 * Persist ONE observation outcome, atomically and without substitution.
 *
 * THE FOUR RECORDS ARE FOUR RELATIONS, and this function refuses a shape where
 * one stands in for another: an accepted change writes the current-state
 * transition AND the append-only event AND the mutation receipt AND advances
 * current state, or none of them lands. A conflict writes the visible
 * reconciliation item and nothing else. A refusal writes neither.
 *
 * BOTH COMPARE-AND-SWAPS ARE ENFORCED. The stored policy digest and the stored
 * current-state digest must still be the ones the decision was taken against;
 * either having moved refuses, so a concurrent observation cannot land on a
 * picture of the record that has since changed underneath it.
 */
CREATE OR REPLACE FUNCTION ops.f01_apply_observation(
  p_decision text,
  p_entity text, p_field text,
  p_expected_policy_digest text, p_expected_state_digest text,
  p_state jsonb, p_transition jsonb, p_event jsonb, p_receipt jsonb, p_reconciliation jsonb,
  p_idempotency_key text, p_request_digest text,
  p_diagnostics jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_policy_digest text;
  v_state ops.f01_field_state%ROWTYPE;
  v_found boolean;
  v_prior_state_digest text;
  v_prior_event_digest text;
  v_prior_seq bigint;
  v_prior_value text;
  v_event jsonb;
  v_transition jsonb;
  v_receipt jsonb;
  v_new_state jsonb;
  v_event_digest text;
  v_transition_digest text;
  v_receipt_digest text;
  v_state_digest text;
  v_item_digest text;
  v_result jsonb;
BEGIN
  IF p_decision NOT IN ('accept', 'reconcile', 'refuse', 'no_change',
                        'needs_independent_privacy_route') THEN
    RAISE EXCEPTION 'f01_unknown_observation_decision: %', p_decision USING ERRCODE = '22023';
  END IF;
  v_replay := ops.f01_claim_idempotency('record-source-observation', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('f01:policy:' || ops.f01_tenant(), 0));

  IF jsonb_typeof(p_diagnostics) IS DISTINCT FROM 'object'
     OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_diagnostics) AS k(key)
                 WHERE key NOT IN ('reason_id','version_ordering','conflict_kind')) THEN
    RAISE EXCEPTION 'f01_invalid_observation_diagnostics' USING ERRCODE = '22023';
  END IF;
  IF p_diagnostics ? 'reason_id' AND
      (jsonb_typeof(p_diagnostics->'reason_id') IS DISTINCT FROM 'string'
       OR length(p_diagnostics->>'reason_id') NOT BETWEEN 1 AND 200) THEN
    RAISE EXCEPTION 'f01_invalid_observation_reason' USING ERRCODE = '22023';
  END IF;
  IF p_diagnostics->>'version_ordering' IS NOT NULL AND
      p_diagnostics->>'version_ordering' NOT IN ('older','equal','newer','indeterminate') THEN
    RAISE EXCEPTION 'f01_invalid_version_ordering' USING ERRCODE = '22023';
  END IF;
  IF p_decision = 'accept' AND p_diagnostics ? 'reason_id' AND
      (p_diagnostics->>'reason_id') IS DISTINCT FROM (p_receipt->'record'->>'reason_id') THEN
    RAISE EXCEPTION 'f01_observation_reason_mismatch' USING ERRCODE = '22000';
  END IF;
  IF p_diagnostics ? 'conflict_kind' AND
      (p_diagnostics->>'conflict_kind') IS DISTINCT FROM (p_reconciliation->'record'->>'conflict_kind') THEN
    RAISE EXCEPTION 'f01_observation_conflict_mismatch' USING ERRCODE = '22000';
  END IF;

  -- Serialize by NATURAL IDENTITY, not by table, so two observations of
  -- different fields never block each other and two of the same field never
  -- interleave.
  PERFORM pg_advisory_xact_lock(
    hashtextextended('f01:field:' || ops.f01_tenant() || ':' || p_entity || ':' || p_field, 0));

  v_policy_digest := ops.f01_current_policy_digest();
  IF v_policy_digest IS NULL THEN
    RAISE EXCEPTION 'f01_no_installed_policy: an observation cannot be judged against no registry'
      USING ERRCODE = '42704';
  END IF;
  IF v_policy_digest IS DISTINCT FROM p_expected_policy_digest THEN
    RAISE EXCEPTION 'f01_stale_policy_digest: the registry moved between decision and apply'
      USING ERRCODE = '40001';
  END IF;

  SELECT * INTO v_state FROM ops.f01_field_state
   WHERE tenant = ops.f01_tenant() AND entity = p_entity AND field = p_field
   FOR UPDATE;
  v_found := FOUND;
  IF v_found THEN
    PERFORM ops.f01_verify_envelope(v_state.envelope, v_state.envelope_digest,
                                    v_state.state_digest, 'stored_field_state');
    v_prior_state_digest := v_state.state_digest;
    v_prior_event_digest := v_state.last_event_digest;
    v_prior_seq := v_state.event_seq;
    v_prior_value := v_state.value_digest;
  END IF;
  IF v_prior_state_digest IS DISTINCT FROM p_expected_state_digest THEN
    RAISE EXCEPTION 'f01_stale_current_state: current is %, the caller decided against %',
      coalesce(v_prior_state_digest, 'none'), coalesce(p_expected_state_digest, 'none')
      USING ERRCODE = '40001';
  END IF;

  IF p_decision = 'accept' THEN
    IF p_state IS NULL OR p_transition IS NULL OR p_event IS NULL OR p_receipt IS NULL THEN
      RAISE EXCEPTION 'f01_incomplete_mutation_set: an accepted change writes state, transition, event and receipt'
        USING ERRCODE = '22023';
    END IF;
    IF p_reconciliation IS NOT NULL THEN
      RAISE EXCEPTION 'f01_reconciliation_on_accepted_change: an accepted change is not a conflict'
        USING ERRCODE = '22023';
    END IF;
    v_transition := p_transition -> 'record';
    v_event := p_event -> 'record';
    v_receipt := p_receipt -> 'record';
    v_new_state := p_state -> 'record';
    v_transition_digest := ops.f01_digest_jsonb(v_transition);
    v_event_digest := ops.f01_digest_jsonb(v_event);
    v_receipt_digest := ops.f01_digest_jsonb(v_receipt);
    v_state_digest := ops.f01_digest_jsonb(v_new_state);

    -- The append-only chain is continued, never branched.
    IF (v_event ->> 'event_seq')::bigint IS DISTINCT FROM coalesce(v_prior_seq, 0) + 1 THEN
      RAISE EXCEPTION 'f01_event_sequence_out_of_order: expected %, supplied %',
        coalesce(v_prior_seq, 0) + 1, v_event ->> 'event_seq' USING ERRCODE = '23514';
    END IF;
    IF (v_event ->> 'previous_event_digest') IS DISTINCT FROM v_prior_event_digest THEN
      RAISE EXCEPTION 'f01_event_chain_broken: the event does not extend the stored chain'
        USING ERRCODE = '23514';
    END IF;
    -- The transition must describe the move actually being made.
    IF (v_transition ->> 'from_value_digest') IS DISTINCT FROM v_prior_value THEN
      RAISE EXCEPTION 'f01_transition_from_mismatch: the transition starts from a value that is not stored'
        USING ERRCODE = '23514';
    END IF;
    IF (v_new_state ->> 'value_digest') IS DISTINCT FROM (v_transition ->> 'to_value_digest')
       OR (v_new_state ->> 'last_event_digest') IS DISTINCT FROM v_event_digest
       OR (v_new_state ->> 'event_seq') IS DISTINCT FROM (v_event ->> 'event_seq') THEN
      RAISE EXCEPTION 'f01_state_not_bound_to_transition_and_event' USING ERRCODE = '23514';
    END IF;
    -- The receipt binds both, and nothing binds the receipt.
    IF (v_receipt ->> 'current_state_transition_digest') IS DISTINCT FROM v_transition_digest
       OR (v_receipt ->> 'event_digest') IS DISTINCT FROM v_event_digest THEN
      RAISE EXCEPTION 'f01_receipt_does_not_bind_transition_and_event' USING ERRCODE = '23514';
    END IF;

    INSERT INTO ops.f01_state_transition
      (tenant, entity, field, envelope, envelope_digest, transition_digest,
       from_value_digest, to_value_digest, event_digest, policy_digest,
       actor_slug, recorded_at, idempotency_key)
    VALUES (ops.f01_tenant(), p_entity, p_field, p_transition,
            ops.f01_digest_jsonb(p_transition), v_transition_digest,
            v_transition ->> 'from_value_digest', v_transition ->> 'to_value_digest',
            v_event_digest, v_policy_digest, v_actor, now(), p_idempotency_key);

    INSERT INTO ops.f01_field_event
      (tenant, entity, field, event_seq, envelope, envelope_digest, event_digest,
       previous_event_digest, event_kind, source_system, observed_at_text, observed_at,
       policy_digest, actor_slug, recorded_at, idempotency_key)
    VALUES (ops.f01_tenant(), p_entity, p_field, (v_event ->> 'event_seq')::bigint,
            p_event, ops.f01_digest_jsonb(p_event), v_event_digest,
            v_event ->> 'previous_event_digest', v_event ->> 'event_kind',
            v_event ->> 'source_system', v_event ->> 'observed_at',
            ops.f01_instant(v_event ->> 'observed_at'),
            v_policy_digest, v_actor, now(), p_idempotency_key);

    INSERT INTO ops.f01_mutation_receipt
      (tenant, entity, field, envelope, envelope_digest, receipt_digest,
       transition_digest, event_digest, policy_digest, reason_id,
       actor_slug, recorded_at, idempotency_key)
    VALUES (ops.f01_tenant(), p_entity, p_field, p_receipt,
            ops.f01_digest_jsonb(p_receipt), v_receipt_digest,
            v_transition_digest, v_event_digest, v_policy_digest,
            v_receipt ->> 'reason_id', v_actor, now(), p_idempotency_key);

    INSERT INTO ops.f01_field_state
      (tenant, entity, field, envelope, envelope_digest, state_digest, value_digest,
       owner_source, account, native_id, native_id_epoch, event_seq, last_event_digest,
       observed_at_text, observed_at, policy_digest, updated_by, updated_at)
    VALUES (ops.f01_tenant(), p_entity, p_field, p_state, ops.f01_digest_jsonb(p_state),
            v_state_digest, v_new_state ->> 'value_digest', v_new_state ->> 'owner_source',
            v_new_state ->> 'account',
            v_new_state -> 'native_identity' ->> 'native_id',
            v_new_state -> 'native_identity' ->> 'native_id_epoch',
            (v_new_state ->> 'event_seq')::bigint, v_new_state ->> 'last_event_digest',
            v_new_state ->> 'observed_at', ops.f01_instant(v_new_state ->> 'observed_at'),
            v_policy_digest, v_actor, now())
    ON CONFLICT (tenant, entity, field) DO UPDATE
      SET envelope = EXCLUDED.envelope, envelope_digest = EXCLUDED.envelope_digest,
          state_digest = EXCLUDED.state_digest, value_digest = EXCLUDED.value_digest,
          owner_source = EXCLUDED.owner_source, account = EXCLUDED.account,
          native_id = EXCLUDED.native_id, native_id_epoch = EXCLUDED.native_id_epoch,
          event_seq = EXCLUDED.event_seq, last_event_digest = EXCLUDED.last_event_digest,
          observed_at_text = EXCLUDED.observed_at_text, observed_at = EXCLUDED.observed_at,
          policy_digest = EXCLUDED.policy_digest,
          updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;

    v_result := jsonb_build_object(
      'operation', 'record-source-observation', 'outcome', 'accepted',
      'actor_slug', v_actor, 'policy_digest', v_policy_digest,
      'current_state_transition_digest', v_transition_digest,
      'event_digest', v_event_digest,
      'mutation_receipt_digest', v_receipt_digest,
      'reconciliation_item_digest', NULL,
      'records_written', jsonb_build_array('current_state_transition', 'append_only_event',
                                           'mutation_receipt', 'current_state'),
      'any_one_substitutes_for_another', false,
      'readback', ops.f01_current_field_state(p_entity, p_field),
      'external_effects', false);

  ELSE
    IF p_state IS NOT NULL OR p_transition IS NOT NULL OR p_event IS NOT NULL
       OR p_receipt IS NOT NULL THEN
      RAISE EXCEPTION 'f01_mutation_records_on_unaccepted_observation: % writes no transition, event or receipt',
        p_decision USING ERRCODE = '22023';
    END IF;
    IF p_reconciliation IS NOT NULL THEN
      v_item_digest := ops.f01_digest_jsonb(p_reconciliation -> 'record');
      INSERT INTO ops.f01_reconciliation_item
        (tenant, entity, field, envelope, envelope_digest, item_digest, conflict_kind,
         human_resolver_class, policy_digest, actor_slug, recorded_at, idempotency_key)
      VALUES (ops.f01_tenant(), p_entity, p_field, p_reconciliation,
              ops.f01_digest_jsonb(p_reconciliation), v_item_digest,
              p_reconciliation -> 'record' ->> 'conflict_kind',
              p_reconciliation -> 'record' ->> 'human_resolver_class',
              v_policy_digest, v_actor, now(), p_idempotency_key)
      ON CONFLICT (item_digest) DO NOTHING;
    END IF;
    v_result := jsonb_build_object(
      'operation', 'record-source-observation',
      'outcome', p_decision,
      'actor_slug', v_actor, 'policy_digest', v_policy_digest,
      'current_state_transition_digest', NULL,
      'event_digest', NULL,
      'mutation_receipt_digest', NULL,
      'reconciliation_item_digest', v_item_digest,
      'records_written', CASE WHEN v_item_digest IS NULL
                              THEN '[]'::jsonb ELSE '["reconciliation_item"]'::jsonb END,
      'any_one_substitutes_for_another', false,
      'readback', ops.f01_current_field_state(p_entity, p_field),
      'external_effects', false);
  END IF;

  -- Preserve diagnostics in the original result so later replay does not
  -- re-evaluate them against changed policy, state, or time. Direct 12-argument
  -- fixture calls derive an accepted reason from the receipt; other direct
  -- calls are labelled by their decision rather than inventing a kernel reason.
  v_result := v_result || jsonb_build_object(
    'entity', p_entity, 'field', p_field,
    'reason_id', coalesce(p_diagnostics->>'reason_id', p_receipt->'record'->>'reason_id',
                          'direct_observation_' || p_decision),
    'version_ordering', p_diagnostics->'version_ordering',
    'conflict_kind', p_reconciliation->'record'->'conflict_kind');
  RETURN ops.f01_settle_idempotency('record-source-observation', p_idempotency_key, v_result);
END;
$$;

-- --- 9.3 record-corporate-artifact -----------------------------------------

CREATE OR REPLACE FUNCTION ops.f01_record_artifact(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_record jsonb;
  v_digest text;
  v_existing jsonb;
  v_outcome text := 'recorded';
  v_result jsonb;
BEGIN
  v_replay := ops.f01_claim_idempotency('record-corporate-artifact', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;

  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_artifact_digest_mismatch: the artifact does not hash to its claim'
      USING ERRCODE = '22000';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:artifact:' || (v_record ->> 'source_system') || ':' ||
    (v_record ->> 'source_account') || ':' ||
    (v_record -> 'native_identity' ->> 'native_id') || ':' ||
    (v_record -> 'native_identity' ->> 'native_id_epoch') || ':' ||
    (v_record ->> 'native_version'), 0));

  -- PRIOR IDENTITY IS LOADED, never accepted. An identity already bound to
  -- different bytes refuses; the same bytes are a no-op, not a second artifact.
  v_existing := ops.f01_stored_artifact_by_identity(
    v_record ->> 'source_system', v_record ->> 'source_account',
    v_record -> 'native_identity' ->> 'native_id',
    v_record -> 'native_identity' ->> 'native_id_epoch',
    v_record ->> 'native_version');
  IF v_existing IS NOT NULL THEN
    IF (v_existing -> 'artifact' ->> 'content_digest') IS DISTINCT FROM (v_record ->> 'content_digest') THEN
      RAISE EXCEPTION 'f01_artifact_identity_conflict: identity already binds %, supplied %',
        v_existing -> 'artifact' ->> 'content_digest', v_record ->> 'content_digest'
        USING ERRCODE = '23505';
    END IF;
    v_outcome := 'already_recorded';
    v_digest := v_existing ->> 'artifact_digest';
  ELSE
    INSERT INTO ops.f01_corporate_artifact
      (tenant, envelope, envelope_digest, artifact_digest, source_system, source_account,
       native_id, native_id_epoch, native_version, content_digest, evidence_class,
       observed_at_text, observed_at, policy_digest, actor_slug, recorded_at, idempotency_key)
    VALUES (ops.f01_tenant(), p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
            v_record ->> 'source_system', v_record ->> 'source_account',
            v_record -> 'native_identity' ->> 'native_id',
            v_record -> 'native_identity' ->> 'native_id_epoch',
            v_record ->> 'native_version', v_record ->> 'content_digest',
            v_record ->> 'evidence_class', v_record ->> 'observed_at',
            ops.f01_instant(v_record ->> 'observed_at'),
            ops.f01_current_policy_digest(), v_actor, now(), p_idempotency_key);
  END IF;

  v_result := jsonb_build_object(
    'operation', 'record-corporate-artifact', 'outcome', v_outcome,
    'actor_slug', v_actor, 'artifact_digest', v_digest,
    'is_fact', false, 'makes_field_authoritative', false, 'immutable', true,
    'readback', ops.f01_stored_artifact(v_digest),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency('record-corporate-artifact', p_idempotency_key, v_result);
END;
$$;

-- --- 9.4 record-parsed-proposal --------------------------------------------

/**
 * Persist one reviewable proposal, its reversible link, AND the derivative-source
 * registration that binds the proposal to the artifact it was parsed from.
 *
 * THE THIRD ENVELOPE IS MANDATORY, and that is the approved producer rule made
 * structural — the registration rule named in section 5.3.1, not Q129.D1. A parsed proposal is a record DERIVED from a stored artifact, so
 * this workflow is a producer: it registers which original produced the
 * derivative in the same transaction, or the derivative is not recorded at all.
 * A caller cannot skip it by passing NULL — ops.f01_insert_derivative_link
 * raises f01_derivative_link_required — and cannot forge it, because the link
 * must name this exact proposal and this exact artifact.
 *
 * THE SIGNATURE CHANGED, so the four-argument form is dropped rather than left
 * beside this one. Two overloads would make the four-argument call ambiguous and
 * would leave a path that records a proposal with no provenance edge, which is
 * the whole thing this exists to prevent.
 */
DROP FUNCTION IF EXISTS ops.f01_record_proposal(jsonb, jsonb, text, text);

CREATE OR REPLACE FUNCTION ops.f01_record_proposal(
  p_proposal jsonb, p_link jsonb, p_derivative_link jsonb,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_proposal jsonb := p_proposal -> 'record';
  v_link jsonb := p_link -> 'record';
  v_derivative jsonb := p_derivative_link -> 'record';
  v_proposal_digest text;
  v_link_digest text;
  v_registration jsonb;
  v_result jsonb;
BEGIN
  v_replay := ops.f01_claim_idempotency('record-parsed-proposal', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('f01:policy:' || ops.f01_tenant(), 0));

  v_proposal_digest := ops.f01_digest_jsonb(v_proposal);
  v_link_digest := ops.f01_digest_jsonb(v_link);
  IF (p_proposal ->> 'record_digest') IS DISTINCT FROM v_proposal_digest
     OR (p_link ->> 'record_digest') IS DISTINCT FROM v_link_digest THEN
    RAISE EXCEPTION 'f01_proposal_digest_mismatch' USING ERRCODE = '22000';
  END IF;
  IF ops.f01_current_policy_digest() IS NULL THEN
    RAISE EXCEPTION 'f01_no_installed_policy: a proposal names fields, so it needs a registry'
      USING ERRCODE = '42704';
  END IF;
  -- The artifact is LOADED. A proposal cannot assert an artifact into existence
  -- by naming one, and the foreign key below is the structural half of that.
  IF ops.f01_stored_artifact(v_proposal ->> 'artifact_digest') IS NULL THEN
    RAISE EXCEPTION 'f01_unknown_artifact: a proposal must link to a stored artifact'
      USING ERRCODE = '23503';
  END IF;
  IF (v_link ->> 'supersedes_link_digest') IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM ops.f01_proposal_link
                      WHERE tenant = ops.f01_tenant()
                        AND link_digest = v_link ->> 'supersedes_link_digest') THEN
    RAISE EXCEPTION 'f01_unknown_superseded_link: superseding names the link it replaces'
      USING ERRCODE = '23503';
  END IF;

  -- THE PROVENANCE EDGE IS CHECKED BEFORE ANYTHING IS WRITTEN, and it must be
  -- about THIS proposal and THIS artifact. A link naming some other derivative,
  -- or some other source, would satisfy "a link was supplied" while registering
  -- the provenance of something else entirely.
  IF p_derivative_link IS NULL THEN
    RAISE EXCEPTION 'f01_derivative_link_required: a parsed proposal is a derived record and completes only with its source registration'
      USING ERRCODE = '22023';
  END IF;
  IF (v_derivative ->> 'source_artifact_digest') IS DISTINCT FROM (v_proposal ->> 'artifact_digest')
     OR (v_derivative ->> 'derivative_id') IS DISTINCT FROM v_proposal_digest
     OR (v_derivative ->> 'derivative_content_digest') IS DISTINCT FROM v_proposal_digest
     OR (v_derivative ->> 'derivative_kind') IS DISTINCT FROM 'f01_parsed_proposal' THEN
    RAISE EXCEPTION 'f01_derivative_link_not_bound_to_proposal: the registration must name this proposal and its source artifact'
      USING ERRCODE = '22000';
  END IF;

  -- Tier 3 then tier 4, exactly as the public registration writer takes them, so
  -- a derivative cannot be registered underneath a deletion evaluation that has
  -- already read this artifact's coverage.
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:artifact-retention:' || ops.f01_tenant() || ':' ||
    (v_derivative ->> 'source_artifact_digest'), 0));
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:derivative:' || ops.f01_tenant() || ':' ||
    (v_derivative ->> 'derivative_kind') || ':' || (v_derivative ->> 'derivative_id'), 0));

  INSERT INTO ops.f01_parsed_proposal
    (tenant, envelope, envelope_digest, proposal_digest, artifact_digest, source_system,
     source_account, confidence, observed_at_text, observed_at, policy_digest,
     actor_slug, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), p_proposal, ops.f01_digest_jsonb(p_proposal), v_proposal_digest,
          v_proposal ->> 'artifact_digest', v_proposal ->> 'source_system',
          v_proposal ->> 'source_account', (v_proposal ->> 'confidence')::numeric,
          v_proposal ->> 'observed_at', ops.f01_instant(v_proposal ->> 'observed_at'),
          ops.f01_current_policy_digest(), v_actor, now(), p_idempotency_key)
  ON CONFLICT (proposal_digest) DO NOTHING;

  INSERT INTO ops.f01_proposal_link
    (tenant, envelope, envelope_digest, link_digest, proposal_digest, artifact_digest,
     supersedes_link_digest, policy_digest, actor_slug, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), p_link, ops.f01_digest_jsonb(p_link), v_link_digest,
          v_proposal_digest, v_link ->> 'artifact_digest',
          v_link ->> 'supersedes_link_digest', ops.f01_current_policy_digest(),
          v_actor, now(), p_idempotency_key);

  -- Same transaction, same idempotency key: either the proposal and its
  -- provenance both land, or neither does.
  v_registration := ops.f01_insert_derivative_link(p_derivative_link, v_actor, p_idempotency_key);

  v_result := jsonb_build_object(
    'operation', 'record-parsed-proposal', 'outcome', 'recorded',
    'actor_slug', v_actor,
    'proposal_digest', v_proposal_digest, 'link_digest', v_link_digest,
    'derivative_link_digest', v_registration ->> 'link_digest',
    'derivative_registration_bound', true,
    'derivative_coverage', v_registration -> 'coverage',
    'becomes_fact', false, 'advances_state', false,
    'carries_effect_authority', false, 'requires_human_review', true,
    'readback', (SELECT ops.f01_verify_envelope(envelope, envelope_digest, link_digest,
                                                'stored_proposal_link')
                   FROM ops.f01_proposal_link WHERE link_digest = v_link_digest),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency('record-parsed-proposal', p_idempotency_key, v_result);
END;
$$;

-- --- 9.4.1 register-derivative-source-link ---------------------------------

/**
 * The PRIVATE half: validate and insert one derivative-source link.
 *
 * Private for the same reason the idempotency helpers are. It is reached only
 * from inside a SECURITY DEFINER writer, where it executes as the owner whatever
 * the caller is, so no runtime EXECUTE grant is needed and any runtime grant
 * would be a hole. Two writers use it — the public registration surface and
 * ops.f01_record_proposal — which is why it exists at all: the alternative was
 * for the proposal writer to call the public one, take a SECOND tier-1 request
 * lock after a tier-2 policy lock, and break the acyclic lock order in section 9.
 *
 * IT TAKES NO LOCKS ITSELF. Both callers take the tier-3 artifact-retention and
 * tier-4 derivative locks before calling it, in that order.
 */
CREATE OR REPLACE FUNCTION ops.f01_insert_derivative_link(
  p_envelope jsonb, p_actor text, p_idempotency_key text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_record jsonb;
  v_digest text;
  v_source jsonb;
  v_source_content text;
  v_existing ops.f01_derivative_link%ROWTYPE;
  v_outcome text := 'registered';
BEGIN
  IF p_envelope IS NULL THEN
    RAISE EXCEPTION 'f01_derivative_link_required: a derived record completes only with its source registration'
      USING ERRCODE = '22023';
  END IF;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_derivative_link_digest_mismatch: the link does not hash to its claim'
      USING ERRCODE = '22000';
  END IF;
  IF (v_record ->> 'registered_by') IS DISTINCT FROM p_actor THEN
    RAISE EXCEPTION 'f01_actor_injection_refused: registered_by is derived, never supplied'
      USING ERRCODE = '42501';
  END IF;
  -- The source is LOADED. Provenance pointing at an artifact nobody stored is
  -- not provenance, and naming a digest never brings one into existence.
  v_source := ops.f01_stored_artifact(v_record ->> 'source_artifact_digest');
  IF v_source IS NULL THEN
    RAISE EXCEPTION 'f01_unknown_artifact: a derivative link names a stored artifact'
      USING ERRCODE = '23503';
  END IF;

  -- A DERIVATIVE MAY NOT BE A COPY OF ITS OWN SOURCE, and the comparison is
  -- against the ARTIFACT'S CONTENT DIGEST — the bytes — read from the row this
  -- writer just authenticated.
  --
  -- WHY IT IS HERE AND NOT IN A CHECK. f01_derivative_not_self on the table
  -- compares derivative_content_digest with source_artifact_digest, which is the
  -- artifact's RECORD identity: a real byte-identical copy never trips it, because
  -- a record digest is taken over source system, account, native identity,
  -- provenance and observed instant as well as the content. The comparison that
  -- catches a copy needs the OTHER row's content, and a CHECK constraint cannot
  -- read another row — PostgreSQL forbids a subquery in one, and a constraint that
  -- could would be checked against whatever that row said at some later moment
  -- rather than at the moment the claim was made. So it belongs in the writer,
  -- where the source has already been loaded and verified, and the structural
  -- constraint stays exactly as it is for the claim it does cover.
  v_source_content := v_source -> 'artifact' ->> 'content_digest';
  IF v_source_content IS NULL THEN
    RAISE EXCEPTION 'f01_corrupt_stored_record: the stored source artifact carries no content digest'
      USING ERRCODE = '22000';
  END IF;
  IF (v_record ->> 'derivative_content_digest') = v_source_content THEN
    RAISE EXCEPTION 'f01_derivative_is_its_own_source: % names bytes identical to source artifact % (content %); a copy is the source under a second name, not something derived from it',
      v_record ->> 'derivative_id', v_record ->> 'source_artifact_digest', v_source_content
      USING ERRCODE = '23514';
  END IF;

  IF ops.f01_instant(v_record ->> 'produced_at') > now() THEN
    RAISE EXCEPTION 'f01_derivative_produced_after_now: a production nobody has performed'
      USING ERRCODE = '22007';
  END IF;

  -- ONE DERIVATIVE, ONE ORIGINAL. A re-registration that agrees on every
  -- IMMUTABLE field of the provenance edge is the same fact arriving twice and is
  -- a no-op. One that disagrees on any of them is a rewrite of where a record came
  -- from, or of who made it, and refuses — the unique index makes the same thing
  -- true under a concurrent insert rather than merely likely.
  --
  -- THE COMPARISON IS THE FIVE FIELDS NAMED BELOW, and the list is the honest one
  -- rather than "every respect". Source and content digest say WHICH original and
  -- WHICH bytes; producer_workflow, producer_run_ref and evidence_digest say WHO
  -- produced it, in which run, against which immutable evidence. An earlier form
  -- compared only the first two and returned success — handing back the FIRST
  -- producer's link digest — for a second registration that named a different
  -- workflow, a different run or different evidence. That is a second, contrary
  -- provenance claim answered as agreement.
  --
  -- TWO FIELDS ARE DELIBERATELY NOT COMPARED, because comparing them would refuse
  -- honest repeats rather than catch dishonest ones. `produced_at` is the SERVER
  -- instant of the transaction that registered the link, so it differs on every
  -- re-registration by construction; `evidence_ref` is an external label whose
  -- immutable half — evidence_digest — is compared instead. `registered_by` is
  -- re-derived and column-bound above, so it cannot disagree here without having
  -- already been refused.
  SELECT * INTO v_existing FROM ops.f01_derivative_link
   WHERE tenant = ops.f01_tenant()
     AND derivative_kind = v_record ->> 'derivative_kind'
     AND derivative_id = v_record ->> 'derivative_id';
  IF FOUND THEN
    IF v_existing.source_artifact_digest IS DISTINCT FROM (v_record ->> 'source_artifact_digest')
       OR v_existing.derivative_content_digest
            IS DISTINCT FROM (v_record ->> 'derivative_content_digest')
       OR v_existing.producer_workflow IS DISTINCT FROM (v_record ->> 'producer_workflow')
       OR v_existing.producer_run_ref IS DISTINCT FROM (v_record ->> 'producer_run_ref')
       OR v_existing.evidence_digest IS DISTINCT FROM (v_record ->> 'evidence_digest') THEN
      RAISE EXCEPTION 'f01_derivative_source_conflict: % already names source %, bytes %, producer %/% and evidence %',
        v_record ->> 'derivative_id', v_existing.source_artifact_digest,
        v_existing.derivative_content_digest, v_existing.producer_workflow,
        v_existing.producer_run_ref, v_existing.evidence_digest
        USING ERRCODE = '23505';
    END IF;
    v_outcome := 'already_registered';
    v_digest := v_existing.link_digest;
  ELSE
    INSERT INTO ops.f01_derivative_link
      (tenant, envelope, envelope_digest, link_digest, source_artifact_digest,
       derivative_kind, derivative_id, derivative_content_digest,
       producer_workflow, producer_run_ref, produced_at_text, produced_at,
       evidence_ref, evidence_digest, policy_digest, actor_slug, recorded_at, idempotency_key)
    VALUES (ops.f01_tenant(), p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
            v_record ->> 'source_artifact_digest',
            v_record ->> 'derivative_kind', v_record ->> 'derivative_id',
            v_record ->> 'derivative_content_digest',
            v_record ->> 'producer_workflow', v_record ->> 'producer_run_ref',
            v_record ->> 'produced_at', ops.f01_instant(v_record ->> 'produced_at'),
            v_record ->> 'evidence_ref', v_record ->> 'evidence_digest',
            ops.f01_current_policy_digest(), p_actor, now(), p_idempotency_key);
  END IF;

  RETURN jsonb_build_object(
    'outcome', v_outcome,
    'link_digest', v_digest,
    -- Returned on every registration, so the answer a caller stores says out
    -- loud that nothing about coverage moved. The coverage readback beside it is
    -- the proof rather than the promise: it still reads 'unknown'.
    'establishes_coverage', false,
    'is_exhaustive_inventory', false,
    'permits_deletion', false,
    'readback', (SELECT ops.f01_verify_envelope(envelope, envelope_digest, link_digest,
                                                'stored_derivative_link')
                   FROM ops.f01_derivative_link WHERE link_digest = v_digest),
    'coverage', ops.f01_derivative_coverage(v_record ->> 'source_artifact_digest'));
END;
$$;

/**
 * The derivative kinds only an in-schema writer may register.
 *
 * A LIST, NOT A LITERAL, so the next internal producer kind is covered by adding
 * one element rather than by remembering to repeat a comparison. IMMUTABLE and
 * argument-free: it is policy about this schema's own writers, not about data.
 *
 * THE MIRROR OF V5_F01_RESERVED_DERIVATIVE_KINDS, and it carries BOTH names.
 * 'f01_document_version' is produced by the document writer inside this schema,
 * in the same transaction that completes the version, for the same reason
 * 'f01_parsed_proposal' is produced by the proposal writer: its derivative
 * identity is a value a caller can predict — the (document_id, version_no) fold —
 * and the identity index is unique per (tenant, kind, id) over an append-only
 * table with no release path. A pre-registration pointed at another artifact
 * would make the genuine document write conflict for that version for ever.
 * The name is listed here even in a database where only the four-argument
 * document writer is installed: reserving a kind grants nothing and costs
 * nothing, and a list that only became correct after a later hunk applied would
 * be a hole for exactly as long as that took.
 */
CREATE OR REPLACE FUNCTION ops.f01_reserved_derivative_kinds()
RETURNS text[]
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, ops, public
AS $$ SELECT ARRAY['f01_parsed_proposal', 'f01_document_version']::text[] $$;

/**
 * The PUBLIC half: one trusted producer workflow registers one derivative.
 *
 * TRUSTED PRODUCER MEANS THE AUTHENTICATED PRINCIPAL, and nothing else. There is
 * no producer allow-list a caller can name itself into and no `trusted` flag to
 * set: the identity is the connection's, derived exactly as every other writer
 * here derives it. A read-only principal is refused by name in the body as well
 * as by the absent EXECUTE grant, because either alone is a single point of
 * failure.
 *
 * INTERNALLY PRODUCED KINDS ARE RESERVED, and the refusal is here rather than
 * anywhere later. A kind in ops.f01_reserved_derivative_kinds is written by a
 * writer inside this schema, which builds the whole link itself; the public
 * surface exists for producers OUTSIDE it. Without this guard the kinds overlap
 * in one specific and permanent way: the parsed-proposal derivative identity is
 * the proposal digest, that digest is computed from caller payload plus the
 * installed registry digest and is therefore PREDICTABLE by the caller, and the
 * identity index is unique on (tenant, kind, id) over an append-only table with
 * no release path. Pre-registering ('f01_parsed_proposal', <predicted digest>)
 * against some other artifact would make the genuine ops.f01_record_proposal
 * raise f01_derivative_source_conflict for that proposal for ever, and would
 * leave a provenance edge asserting it came from an artifact it did not. One
 * shared carr_writer makes that self-inflicted today; it is refused anyway,
 * because the shape survives any later split of the writer role.
 */
CREATE OR REPLACE FUNCTION ops.f01_register_derivative_link(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_record jsonb;
  v_registration jsonb;
  v_result jsonb;
BEGIN
  IF session_user = 'carr_reader' THEN
    RAISE EXCEPTION 'f01_producer_principal_refused: register-derivative-source-link is written by producer workflows, not by a read-only principal'
      USING ERRCODE = '42501';
  END IF;
  -- BEFORE THE IDEMPOTENCY CLAIM, deliberately, and beside the principal check
  -- rather than after it. Reading the caller's own payload is not a state read,
  -- so it does not disturb the replay-before-state ordering above; claiming a key
  -- for a registration that can never be accepted would burn that key on a
  -- refusal and make the second attempt fail for a different reason.
  v_record := p_envelope -> 'record';
  IF (v_record ->> 'derivative_kind') = ANY (ops.f01_reserved_derivative_kinds()) THEN
    RAISE EXCEPTION 'f01_reserved_derivative_kind: % is produced by a writer inside this schema and is never registered through the public surface',
      v_record ->> 'derivative_kind'
      USING ERRCODE = '42501';
  END IF;
  v_replay := ops.f01_claim_idempotency(
    'register-derivative-source-link', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:artifact-retention:' || ops.f01_tenant() || ':' ||
    (v_record ->> 'source_artifact_digest'), 0));
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'f01:derivative:' || ops.f01_tenant() || ':' ||
    (v_record ->> 'derivative_kind') || ':' || (v_record ->> 'derivative_id'), 0));

  v_registration := ops.f01_insert_derivative_link(p_envelope, v_actor, p_idempotency_key);
  v_result := jsonb_build_object(
    'operation', 'register-derivative-source-link',
    'actor_slug', v_actor,
    'external_effects', false) || v_registration;
  RETURN ops.f01_settle_idempotency(
    'register-derivative-source-link', p_idempotency_key, v_result);
END;
$$;

-- --- 9.5 record-document-identity ------------------------------------------

CREATE OR REPLACE FUNCTION ops.f01_record_document(
  p_envelope jsonb, p_expected_prior_document_digest text,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_record jsonb;
  v_digest text;
  v_document_id text;
  v_prior text;
  v_version_id bigint;
  v_row ops.f01_document_version%ROWTYPE;
  v_result jsonb;
BEGIN
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
  PERFORM pg_advisory_xact_lock(
    hashtextextended('f01:document:' || ops.f01_tenant() || ':' || v_document_id, 0));

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
  VALUES (ops.f01_tenant(), v_document_id,
          (v_record -> 'neon_identity' ->> 'version_no')::integer,
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

  SELECT * INTO v_row FROM ops.f01_document_version WHERE document_version_id = v_version_id;
  v_result := jsonb_build_object(
    'operation', 'record-document-identity', 'outcome', 'recorded',
    'actor_slug', v_actor, 'document_digest', v_digest,
    'official_filing_state', v_row.official_filing_state,
    'object_storage_success_implies_official_filing', false,
    'neon_success_implies_official_filing', false,
    'readback', ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                        v_row.document_digest, 'stored_document_version'),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency('record-document-identity', p_idempotency_key, v_result);
END;
$$;

-- THE FOUR-ARGUMENT FORM ABOVE MUST NOT SURVIVE A RE-APPLY ONTO A DATABASE THAT
-- ALREADY CARRIES THE SIX-ARGUMENT WRITER. The document-source hunk
-- (ops/document-derivative-registration.candidate.sql) DROPs this overload and
-- replaces it with a six-argument form that cannot be called without a
-- provenance statement. The CREATE OR REPLACE above puts the old one back beside
-- it, and section 10's grant loop — which iterates over whatever ops.f01_%
-- functions EXIST rather than over a written list — would then hand EXECUTE on
-- it to carr_writer and both authority logins. That is a path which completes a
-- DERIVED document with no provenance edge: exactly the hole the replacement
-- exists to close, re-opened by applying this file a second time.
--
-- SO THE OVERLOAD IS DROPPED HERE, CONDITIONALLY, AND BEFORE ANY GRANT IS MADE.
-- The condition is the successor's own existence, checked by EXACT signature —
-- to_regprocedure answers NULL rather than raising for a function that is not
-- there. Where the document hunk has not been applied, this database holds only
-- the four-argument core, the condition is false and this block does nothing, so
-- domain.sql standing alone is unchanged. It grants nobody anything and installs
-- nothing: it removes a function this file itself just re-created, and only when
-- the writer that supersedes it is already present.
DO $document_writer_overload$
BEGIN
  IF to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') IS NOT NULL THEN
    DROP FUNCTION IF EXISTS ops.f01_record_document(jsonb, text, text, text);
  END IF;
END;
$document_writer_overload$;

-- --- 9.6 record-artifact-preservation-hold ---------------------------------

/**
 * Append one hold or release state for one STORED artifact.
 *
 * humanOnly plus authorityOnly, checked here. Append-only: a release is a new
 * row naming the state it replaces, never an edit of the row that placed the
 * hold, and the artifact itself is untouched.
 */
CREATE OR REPLACE FUNCTION ops.f01_record_hold(
  p_envelope jsonb, p_expected_prior_hold_digest text,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text;
  v_replay jsonb;
  v_record jsonb;
  v_digest text;
  v_hold_id text;
  v_prior text;
  v_prior_seq integer;
  v_event_id bigint;
  v_row ops.f01_preservation_hold_event%ROWTYPE;
  v_result jsonb;
BEGIN
  v_actor := ops.f01_require_authority_principal('record-artifact-preservation-hold');
  v_replay := ops.f01_claim_idempotency(
    'record-artifact-preservation-hold', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;

  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_hold_digest_mismatch' USING ERRCODE = '22000';
  END IF;
  IF ops.f01_stored_artifact(v_record ->> 'artifact_digest') IS NULL THEN
    RAISE EXCEPTION 'f01_unknown_artifact: a hold protects a stored artifact'
      USING ERRCODE = '23503';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended('f01:artifact-retention:' || ops.f01_tenant() || ':' || (v_record ->> 'artifact_digest'), 0));
  v_hold_id := v_record ->> 'hold_id';
  PERFORM pg_advisory_xact_lock(
    hashtextextended('f01:hold:' || ops.f01_tenant() || ':' || v_hold_id, 0));

  SELECT hold_digest, hold_seq INTO v_prior, v_prior_seq
    FROM ops.f01_preservation_hold_current
   WHERE tenant = ops.f01_tenant() AND hold_id = v_hold_id;
  IF v_prior IS DISTINCT FROM p_expected_prior_hold_digest
     OR v_prior IS DISTINCT FROM (v_record ->> 'prior_hold_digest') THEN
    RAISE EXCEPTION 'f01_stale_hold_digest: current is %, the caller decided against %',
      coalesce(v_prior, 'none'), coalesce(p_expected_prior_hold_digest, 'none')
      USING ERRCODE = '40001';
  END IF;

  INSERT INTO ops.f01_preservation_hold_event
    (tenant, hold_id, hold_seq, artifact_digest, envelope, envelope_digest, hold_digest,
     prior_hold_digest, hold_state, placed_at_text, released_at_text, policy_digest,
     actor_slug, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), v_hold_id, coalesce(v_prior_seq, 0) + 1,
          v_record ->> 'artifact_digest', p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
          v_record ->> 'prior_hold_digest', v_record ->> 'state',
          v_record ->> 'placed_at', v_record ->> 'released_at',
          ops.f01_current_policy_digest(),
          v_actor, now(), p_idempotency_key)
  RETURNING hold_event_id INTO v_event_id;

  INSERT INTO ops.f01_preservation_hold_current
    (tenant, hold_id, artifact_digest, hold_event_id, hold_digest, hold_state, hold_seq,
     updated_by, updated_at)
  VALUES (ops.f01_tenant(), v_hold_id, v_record ->> 'artifact_digest', v_event_id, v_digest,
          v_record ->> 'state', coalesce(v_prior_seq, 0) + 1, v_actor, now())
  ON CONFLICT (tenant, hold_id) DO UPDATE
    SET hold_event_id = EXCLUDED.hold_event_id, hold_digest = EXCLUDED.hold_digest,
        hold_state = EXCLUDED.hold_state, hold_seq = EXCLUDED.hold_seq,
        artifact_digest = EXCLUDED.artifact_digest,
        updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at;

  SELECT * INTO v_row FROM ops.f01_preservation_hold_event WHERE hold_event_id = v_event_id;
  v_result := jsonb_build_object(
    'operation', 'record-artifact-preservation-hold', 'outcome', 'appended',
    'actor_slug', v_actor, 'hold_digest', v_digest, 'hold_state', v_row.hold_state,
    'hold_seq', v_row.hold_seq, 'deletes_artifact', false,
    'readback', ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                        v_row.hold_digest, 'stored_preservation_hold'),
    'hold_inventory', ops.f01_hold_inventory(v_record ->> 'artifact_digest'),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency(
    'record-artifact-preservation-hold', p_idempotency_key, v_result);
END;
$$;

-- --- 9.7 evaluate-artifact-deletion ----------------------------------------

/**
 * Persist one bounded deletion evaluation. NOTHING IS DELETED HERE.
 *
 * The hold inventory the evaluation was taken against is recomputed from the
 * stored holds and must match the digest the record carries, so a caller cannot
 * evaluate against a cleaner set of holds than the database actually holds.
 */
CREATE OR REPLACE FUNCTION ops.f01_record_deletion_evaluation(
  p_envelope jsonb, p_hold_inventory_digest text,
  p_idempotency_key text, p_request_digest text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
  v_record jsonb;
  v_digest text;
  v_artifact_digest text;
  v_observed text;
  v_coverage jsonb;
  v_clock jsonb;
  v_clock_record jsonb;
  v_row ops.f01_deletion_evaluation%ROWTYPE;
  v_result jsonb;
BEGIN
  v_replay := ops.f01_claim_idempotency('evaluate-artifact-deletion', p_idempotency_key, p_request_digest);
  IF v_replay IS NOT NULL THEN
    RETURN v_replay;
  END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('f01:policy:' || ops.f01_tenant(), 0));

  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  IF (p_envelope ->> 'record_digest') IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION 'f01_deletion_digest_mismatch' USING ERRCODE = '22000';
  END IF;
  v_artifact_digest := v_record ->> 'artifact_digest';
  IF ops.f01_stored_artifact(v_artifact_digest) IS NULL THEN
    RAISE EXCEPTION 'f01_unknown_artifact: a deletion evaluation is about a stored artifact'
      USING ERRCODE = '23503';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended('f01:artifact-retention:' || ops.f01_tenant() || ':' || v_artifact_digest, 0));

  IF (v_record ->> 'retention_registry_digest') IS DISTINCT FROM
       (ops.f01_current_policy() ->> 'retention_registry_digest') THEN
    RAISE EXCEPTION 'f01_stale_retention_policy' USING ERRCODE = '40001';
  END IF;
  -- THE RETENTION CLOCK IS RE-DERIVED UNDER THE LOCK THIS WRITER ALREADY HOLDS,
  -- and a trigger that does not match the one the database derives is refused.
  --
  -- WHAT THIS CLOSES. The evaluation says which trigger its retention period was
  -- measured from. Without re-derivation that would be a value travelling in from
  -- outside — and the one field that decides whether a period has elapsed is
  -- exactly the field a forged or stale evaluation would move. So the clock is
  -- computed here from the stored artifact row, the digest of the DATABASE's own
  -- answer must equal the one the record carries, and the trigger inside the
  -- record must be — WHOLE, field for field — the trigger just derived.
  --
  -- WHOLE, AND NOT A CHOSEN FEW FIELDS. The digest travelling in the record is
  -- taken over the reader's FULL answer, which is a DIFFERENT VALUE from the
  -- object the record embeds: the record carries the projection the decision was
  -- allowed to read. So the digest comparison says nothing whatever about the
  -- embedded object, and a comparison of a few named fields left the rest —
  -- `reference`, `provenance`, `verified`, and any key added or dropped — as
  -- caller text sitting inside a record whose digest checks out. That is a
  -- provenance claim about where a retention instant came from and whether
  -- anything vouched for it, readable by every later reader of the stored
  -- envelope, and it was writable by whoever composed the record. Comparing the
  -- embedded object AS A WHOLE to the projection derived here closes it, and
  -- closes it for every field this projection ever gains.
  --
  -- AND THE ALIAS IS REFUSED BY NAME. A record whose clock admits it used the
  -- source's observed instant refuses outright, whatever else it agrees with:
  -- that is the one substitution the whole mechanism exists to prevent, and it
  -- must not depend on the digest comparison happening to notice.
  v_clock := ops.f01_retention_clock(v_artifact_digest);
  IF v_clock IS NULL THEN
    RAISE EXCEPTION 'f01_retention_clock_unavailable: no custody instant could be derived for %',
      v_artifact_digest USING ERRCODE = '22000';
  END IF;
  -- The SAME PROJECTION the record layer carries, derived HERE from the answer
  -- just read: the reader returns more than a record embeds — the artifact digest,
  -- the source's own observed instant and the integrity note — and those three are
  -- covered by the digest below rather than by this comparison. `->` throughout,
  -- never `->>`: the null and boolean fields must stay null and boolean, because a
  -- record carrying "false" where the reader answers false is a different object.
  v_clock_record := jsonb_build_object(
    'kind', v_clock -> 'kind',
    'event_kind', v_clock -> 'event_kind',
    'started_at', v_clock -> 'started_at',
    'reference', v_clock -> 'reference',
    'provenance', v_clock -> 'provenance',
    'event_digest', v_clock -> 'event_digest',
    'verified', v_clock -> 'verified',
    'source_observed_at_used', v_clock -> 'source_observed_at_used');
  -- ABSENT IS ITS OWN REFUSAL, and it comes first so a record carrying no trigger
  -- at all is not reported as one that admitted the alias. Without this, the
  -- coalesce below reads an absent key as '' and raises the wrong name.
  IF jsonb_typeof(v_record -> 'retention_clock') IS DISTINCT FROM 'object'
     OR (v_record ->> 'retention_clock_digest') IS NULL THEN
    RAISE EXCEPTION 'f01_retention_clock_required: an evaluation names the trigger its retention period was measured from, and the digest of the answer it was taken against'
      USING ERRCODE = '22023';
  END IF;
  IF coalesce(v_record -> 'retention_clock' ->> 'source_observed_at_used', '') <> 'false' THEN
    RAISE EXCEPTION 'f01_retention_clock_uses_source_observed_at: a retention period never runs from the instant a source says it observed the artifact'
      USING ERRCODE = '22000';
  END IF;
  IF (v_record ->> 'retention_clock_digest') IS DISTINCT FROM ops.f01_digest_jsonb(v_clock)
     OR (v_record -> 'retention_clock') IS DISTINCT FROM v_clock_record THEN
    RAISE EXCEPTION 'f01_stale_retention_clock: the evaluation carries a retention trigger (% at %) that is not, field for field, the one the database derives (% at %)',
      coalesce(v_record -> 'retention_clock' ->> 'kind', 'none'),
      coalesce(v_record -> 'retention_clock' ->> 'started_at', 'none'),
      coalesce(v_clock ->> 'kind', 'unreadable'),
      coalesce(v_clock ->> 'started_at', 'unreadable')
      USING ERRCODE = '40001';
  END IF;

  -- THE COVERAGE THE EVALUATION WAS TAKEN AGAINST IS RE-DERIVED, exactly like
  -- the hold inventory below it. A derivative registered between the decision
  -- and this write moves the digest, and the evaluation refuses rather than
  -- being recorded against a picture of the provenance graph that has changed.
  v_coverage := ops.f01_derivative_coverage(v_artifact_digest);
  IF (v_record ->> 'derivative_coverage_digest')
       IS DISTINCT FROM ops.f01_digest_jsonb(v_coverage)
     OR (v_record ->> 'derivative_coverage_state') IS DISTINCT FROM (v_coverage ->> 'state') THEN
    RAISE EXCEPTION 'f01_stale_derivative_coverage: registration moved between decision and apply, or the evaluation states a coverage answer (%) the database does not hold (%)',
      coalesce(v_record ->> 'derivative_coverage_state', 'none'),
      coalesce(v_coverage ->> 'state', 'unreadable')
      USING ERRCODE = '40001';
  END IF;
  -- AND AN ALLOW STILL FAILS CLOSED. Unknown coverage means the inventory is
  -- unavailable, not empty, so no allow can be persisted while it is unknown —
  -- checked here as well as in the kernel, because either alone is one edit away
  -- from being the only thing standing between a caller and a purge.
  IF v_record ->> 'decision' = 'allow'
     AND ((v_coverage ->> 'state') IS DISTINCT FROM 'established'
          OR ops.f01_stored_derivatives(v_artifact_digest) IS NULL) THEN
    RAISE EXCEPTION 'f01_derivative_inventory_unavailable: coverage is %, so absence of derivatives is unknown rather than verified',
      coalesce(v_coverage ->> 'state', 'unreadable') USING ERRCODE = '22000';
  END IF;
  v_observed := ops.f01_hold_inventory_digest(v_artifact_digest);
  IF v_observed IS DISTINCT FROM p_hold_inventory_digest
     OR v_observed IS DISTINCT FROM (v_record ->> 'hold_inventory_digest') THEN
    RAISE EXCEPTION 'f01_stale_hold_inventory: holds moved between decision and apply'
      USING ERRCODE = '40001';
  END IF;

  INSERT INTO ops.f01_deletion_evaluation
    (tenant, artifact_digest, artifact_class, envelope, envelope_digest, evaluation_digest,
     decision, reason_id, receipt_digest, policy_digest, hold_inventory_digest,
     actor_slug, recorded_at, idempotency_key)
  VALUES (ops.f01_tenant(), v_artifact_digest, v_record ->> 'artifact_class',
          p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
          v_record ->> 'decision', v_record ->> 'reason_id',
          -- A JSON null is not a receipt. Digesting one would manufacture a
          -- receipt digest for an allow that carries no receipt, which is the
          -- one shape f01_deletion_receipt_only_on_allow exists to refuse.
          CASE WHEN (v_record ->> 'decision') = 'allow'
                AND jsonb_typeof(v_record -> 'deletion_receipt') = 'object'
               THEN ops.f01_digest_jsonb(v_record -> 'deletion_receipt') END,
          ops.f01_current_policy_digest(), v_observed,
          v_actor, now(), p_idempotency_key)
  RETURNING * INTO v_row;

  v_result := jsonb_build_object(
    'operation', 'evaluate-artifact-deletion', 'outcome', v_row.decision,
    'actor_slug', v_actor, 'evaluation_digest', v_digest,
    'reason_id', v_row.reason_id, 'receipt_digest', v_row.receipt_digest,
    'hold_inventory', ops.f01_hold_inventory(v_artifact_digest),
    -- CLASS POLICY, read back from the installed retention registry: which
    -- derivative kinds this class's policy says survive. Reported beside the
    -- coverage answer, never in place of it, because "the policy allows an
    -- abstract to survive" and "an abstract exists" are different statements.
    'surviving_derivatives', coalesce((SELECT c->'surviving_derivatives'
       FROM jsonb_array_elements(ops.f01_current_policy()->'retention_registry'->'classes') AS c
       WHERE c->>'artifact_class' = v_record->>'artifact_class'), '[]'::jsonb),
    'derivative_coverage', v_coverage,
    -- The trigger this evaluation's period was measured from, returned beside the
    -- coverage answer and the hold inventory because it is the same kind of fact:
    -- what the database held at the moment the decision was recorded.
    'retention_clock', v_clock,
    'bytes_deleted', false, 'rows_deleted', false, 'external_purge_performed', false,
    'readback', ops.f01_verify_envelope(v_row.envelope, v_row.envelope_digest,
                                        v_row.evaluation_digest, 'stored_deletion_evaluation'),
    'external_effects', false);
  RETURN ops.f01_settle_idempotency('evaluate-artifact-deletion', p_idempotency_key, v_result);
END;
$$;

-- --- 9.8 read-record-source-authority --------------------------------------

/**
 * The one read entry point. Every branch RECOMPUTES the integrity of what it
 * returns; a corrupt newest row raises rather than resolving to an older one.
 */
CREATE OR REPLACE FUNCTION ops.f01_read(p_kind text, p_selector jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE
  v_actor text := ops.f01_context_actor_slug();
  v_body jsonb;
BEGIN
  IF p_kind = 'current_policy' THEN
    v_body := ops.f01_current_policy();
  ELSIF p_kind = 'field_state' THEN
    v_body := ops.f01_current_field_state(p_selector ->> 'entity', p_selector ->> 'field');
  ELSIF p_kind = 'field_events' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY seq), '[]'::jsonb) INTO v_body
      FROM (SELECT e.event_seq AS seq,
                   ops.f01_verify_envelope(e.envelope, e.envelope_digest, e.event_digest,
                                           'stored_source_event') AS verified
              FROM ops.f01_field_event e
             WHERE e.tenant = ops.f01_tenant()
               AND e.entity = p_selector ->> 'entity'
               AND e.field = p_selector ->> 'field') s;
  ELSIF p_kind = 'mutation_receipts' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY id), '[]'::jsonb) INTO v_body
      FROM (SELECT r.receipt_id AS id,
                   ops.f01_verify_envelope(r.envelope, r.envelope_digest, r.receipt_digest,
                                           'stored_mutation_receipt') AS verified
              FROM ops.f01_mutation_receipt r
             WHERE r.tenant = ops.f01_tenant()
               AND r.entity = p_selector ->> 'entity'
               AND r.field = p_selector ->> 'field') s;
  ELSIF p_kind = 'state_transitions' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY id), '[]'::jsonb) INTO v_body
      FROM (SELECT t.transition_id AS id,
                   ops.f01_verify_envelope(t.envelope, t.envelope_digest, t.transition_digest,
                                           'stored_state_transition') AS verified
              FROM ops.f01_state_transition t
             WHERE t.tenant = ops.f01_tenant()
               AND t.entity = p_selector ->> 'entity'
               AND t.field = p_selector ->> 'field') s;
  ELSIF p_kind = 'reconciliation_items' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY id), '[]'::jsonb) INTO v_body
      FROM (SELECT i.item_id AS id,
                   ops.f01_verify_envelope(i.envelope, i.envelope_digest, i.item_digest,
                                           'stored_reconciliation_item') AS verified
              FROM ops.f01_reconciliation_item i
             WHERE i.tenant = ops.f01_tenant()
               AND i.entity = p_selector ->> 'entity'
               AND i.field = p_selector ->> 'field') s;
  ELSIF p_kind = 'artifact' THEN
    v_body := ops.f01_stored_artifact(p_selector ->> 'artifact_digest');
  ELSIF p_kind = 'proposal_links' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY id), '[]'::jsonb) INTO v_body
      FROM (SELECT l.link_id AS id,
                   ops.f01_verify_envelope(l.envelope, l.envelope_digest, l.link_digest,
                                           'stored_proposal_link') AS verified
              FROM ops.f01_proposal_link l
             WHERE l.tenant = ops.f01_tenant()
               AND l.artifact_digest = p_selector ->> 'artifact_digest') s;
  ELSIF p_kind = 'derivative_links' THEN
    v_body := ops.f01_derivative_links(p_selector ->> 'artifact_digest');
  ELSIF p_kind = 'derivative_coverage' THEN
    -- Readable on purpose. An operator who wants to know why a deletion refused
    -- should be able to see the coverage answer and the registered links behind
    -- it, and see for themselves that reading them changes nothing.
    v_body := ops.f01_derivative_coverage(p_selector ->> 'artifact_digest');
  ELSIF p_kind = 'document' THEN
    SELECT ops.f01_verify_envelope(v.envelope, v.envelope_digest, v.document_digest,
                                   'stored_document_version')
      INTO v_body
      FROM ops.f01_document_current c
      JOIN ops.f01_document_version v ON v.document_version_id = c.document_version_id
     WHERE c.tenant = ops.f01_tenant() AND c.document_id = p_selector ->> 'document_id';
  ELSIF p_kind = 'document_versions' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY no), '[]'::jsonb) INTO v_body
      FROM (SELECT v.version_no AS no,
                   ops.f01_verify_envelope(v.envelope, v.envelope_digest, v.document_digest,
                                           'stored_document_version') AS verified
              FROM ops.f01_document_version v
             WHERE v.tenant = ops.f01_tenant()
               AND v.document_id = p_selector ->> 'document_id') s;
  ELSIF p_kind = 'holds' THEN
    v_body := ops.f01_hold_inventory(p_selector ->> 'artifact_digest');
  ELSIF p_kind = 'hold_history' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY seq), '[]'::jsonb) INTO v_body
      FROM (SELECT h.hold_seq AS seq,
                   ops.f01_verify_envelope(h.envelope, h.envelope_digest, h.hold_digest,
                                           'stored_preservation_hold') AS verified
              FROM ops.f01_preservation_hold_event h
             WHERE h.tenant = ops.f01_tenant() AND h.hold_id = p_selector ->> 'hold_id') s;
  ELSIF p_kind = 'deletion_evaluations' THEN
    SELECT coalesce(jsonb_agg(verified ORDER BY id), '[]'::jsonb) INTO v_body
      FROM (SELECT d.evaluation_id AS id,
                   ops.f01_verify_envelope(d.envelope, d.envelope_digest, d.evaluation_digest,
                                           'stored_deletion_evaluation') AS verified
              FROM ops.f01_deletion_evaluation d
             WHERE d.tenant = ops.f01_tenant()
               AND d.artifact_digest = p_selector ->> 'artifact_digest') s;
  ELSE
    RAISE EXCEPTION 'f01_unknown_read_kind: %', p_kind USING ERRCODE = '22023';
  END IF;

  RETURN jsonb_build_object(
    'operation', 'read-record-source-authority',
    'kind', p_kind,
    'tenant', ops.f01_tenant(),
    'actor_slug', v_actor,
    'server_time', ops.f01_now_text(),
    'policy_digest', ops.f01_current_policy_digest(),
    'body', v_body,
    'integrity', 'recomputed_not_trusted',
    'external_effects', false);
END;
$$;

-- ===========================================================================
-- 10. Grants.
--
-- NO DIRECT RUNTIME DML, anywhere. The runtime principal receives EXECUTE on the
-- nine operation functions and SELECT on the relations for diagnosis; every
-- INSERT, UPDATE, DELETE and TRUNCATE arrives through a security-definer writer
-- or does not arrive at all.
--
-- PARENT WIRING. The runtime role name below is the one thing this file cannot
-- know from inside the capsule. The DO block applies the grants only to roles
-- that already exist and creates none, so the migration is additive and safe to
-- apply before the parent confirms the exact name.
--
-- THE READER'S EXCLUSION LIST IS TEN NAMES AND IT IS MIRRORED IN THREE PLACES:
-- here, in the posture readback in section 11, in the SQL fixture's
-- least-privilege block, and in the local PostgreSQL gate's READER_FORBIDDEN.
-- The eight writers are the obvious part; f01_replay_outcome and
-- f01_require_authority_principal are the two that a mirror written from the
-- writer list alone will miss, and a mirror that misses them fails a CORRECT
-- schema. Anything added to or removed from this list has to move in all four.
--
-- f01_register_derivative_link is a WRITER, so the reader is excluded from it
-- like the rest; carr_writer KEEPS it, because the ordinary evidence principal
-- is exactly the trusted producer identity the settled decision names.
-- f01_insert_derivative_link is a PRIVATE helper and joins the claim/settle pair
-- that nobody may execute at runtime.
--
-- THE PRIVATE LIST NAMES ONE FUNCTION THIS FILE DOES NOT CREATE:
-- f01_insert_document_source_provenance, the document-source seam's inserter. It
-- arrives with the document hunk, and the loop below iterates over whatever
-- ops.f01_% functions EXIST when this file runs — so if this file is applied
-- after that hunk, an unnamed helper would be granted to every runtime role and
-- the posture readback in section 11 would not notice. Naming it in both places
-- costs nothing in a database that does not have it yet.
-- ===========================================================================

DO $grants$
DECLARE f record; r text; t record;
BEGIN
  -- PUBLIC IS REVOKED UNCONDITIONALLY, before any role is considered. Doing this
  -- inside the per-role loop would make the whole revoke depend on at least one
  -- carr_* role already existing, which is exactly the additive case this file
  -- is meant to survive.
  FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='ops' AND p.proname LIKE 'f01\_%'
  LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.signature);
  END LOOP;
  FOR t IN SELECT c.oid::regclass AS relation FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='ops' AND c.relkind='r' AND c.relname LIKE 'f01\_%'
  LOOP
    EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC', t.relation);
  END LOOP;

  FOR f IN SELECT p.oid::regprocedure AS signature, p.proname
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='ops' AND p.proname LIKE 'f01\_%'
  LOOP
    FOREACH r IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
      IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN CONTINUE; END IF;
      EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f.signature, r);
      -- Private mutation and trigger helpers never receive runtime EXECUTE.
      -- Nothing needs it: the guards are reached only as triggers, and the claim
      -- and settle helpers are reached only from inside a SECURITY DEFINER
      -- writer, which executes them as the owner regardless of the caller.
      IF f.proname IN ('f01_claim_idempotency','f01_settle_idempotency',
        'f01_insert_derivative_link',
        -- The document-source seam's private inserter. It is named here even
        -- though this file does not create it: it is created by the document
        -- hunk, and if THIS file is applied after that one the loop below would
        -- otherwise hand it to every runtime role. A name in this list for a
        -- function that does not exist yet costs nothing; its absence would be a
        -- hole that opens on a re-apply.
        'f01_insert_document_source_provenance')
        OR f.proname LIKE 'f01_guard_%' THEN CONTINUE; END IF;
      -- A READ-ONLY PRINCIPAL GETS NO WRITE-SHAPED SURFACE. The seven writers are
      -- obvious. f01_replay_outcome is here because it is a SECURITY DEFINER door
      -- onto the settled result of somebody else's mutation, which is a write
      -- outcome and none of a reader's business; f01_require_authority_principal
      -- is here because it is an authority probe, and both of the definer
      -- writers that genuinely need it call it as the owner rather than as the
      -- caller, so revoking it costs nothing.
      IF r='carr_reader' AND f.proname IN ('f01_install_policy','f01_apply_observation',
        'f01_record_artifact','f01_record_proposal','f01_register_derivative_link',
        'f01_record_document','f01_record_hold',
        'f01_record_deletion_evaluation','f01_replay_outcome',
        'f01_require_authority_principal') THEN CONTINUE; END IF;
      IF r='carr_writer' AND f.proname IN ('f01_install_policy','f01_record_hold',
        'f01_require_authority_principal') THEN CONTINUE; END IF;
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', f.signature, r);
    END LOOP;
  END LOOP;
  FOREACH r IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN CONTINUE; END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA ops TO %I',r);
    FOR t IN SELECT c.oid::regclass AS relation FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='ops' AND c.relkind='r' AND c.relname LIKE 'f01\_%'
    LOOP
      EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',t.relation,r);
      EXECUTE format('GRANT SELECT ON TABLE %s TO %I',t.relation,r);
    END LOOP;
  END LOOP;
END;
$grants$;

-- ===========================================================================
-- 11. The grant posture, VERIFIED rather than intended.
--
-- The block above states a posture; this one proves the catalog actually holds
-- it, and refuses the install if it does not. That distinction matters because
-- every claim in section 10 is made by a loop with CONTINUE branches in it: a
-- mis-edited predicate would silently grant instead of skipping, and a silent
-- over-grant is precisely the failure nobody notices. Reading the ACLs back is
-- the same discipline the record writers apply to digests — recompute, then
-- agree or refuse.
-- ===========================================================================

DO $grant_posture$
DECLARE
  v_bad text;
  v_role text;
  v_case record;
BEGIN
  -- A NULL ACL IS NOT "NO GRANTS", and the two object classes below differ in
  -- what it means. For a FUNCTION the built-in default includes PUBLIC EXECUTE,
  -- so a NULL proacl is itself the violation — that is a function the revoke
  -- loop never touched. For a TABLE the default is owner-only, so a NULL relacl
  -- is fine and aclexplode(NULL) correctly yields no rows. Getting this backwards
  -- is how a posture check passes while the hole it looks for is wide open.

  -- (a) PUBLIC holds EXECUTE on nothing under this prefix.
  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'));
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: PUBLIC holds EXECUTE on (or no ACL was ever set for) %', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (b) The private helpers hold no EXECUTE for anyone but the owner — not
  -- PUBLIC, not a named role. They are reached as triggers or from inside a
  -- definer writer, and never as a runtime entry point.
  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops'
     AND (p.proname IN ('f01_claim_idempotency', 'f01_settle_idempotency',
                        'f01_insert_derivative_link',
                        -- The document-source seam's private inserter, listed for
                        -- the same reason and in the same breath as the grant
                        -- loop above: this readback is what turns a mis-edited
                        -- CONTINUE, or an application order that reached this
                        -- file last, into a refused install rather than a quiet
                        -- runtime grant on a writer nobody may call.
                        'f01_insert_document_source_provenance')
          OR p.proname LIKE 'f01\_guard\_%')
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.privilege_type = 'EXECUTE'
                        AND a.grantee IS DISTINCT FROM p.proowner));
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: a private helper is runtime-executable: %', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (c) NO RUNTIME DML, structurally. Not one non-owner INSERT, UPDATE, DELETE
  -- or TRUNCATE grant exists on any F01 relation. The direct-DML trigger is the
  -- second line of this defence; the absent grant is the first.
  SELECT string_agg(DISTINCT c.oid::regclass::text || ' -> ' ||
                    coalesce(g.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ') INTO v_bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    LEFT JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
     AND a.grantee IS DISTINCT FROM c.relowner;
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: runtime DML grant on an F01 relation: %', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (c2) And PUBLIC holds no privilege of any kind on an F01 relation.
  SELECT string_agg(DISTINCT c.oid::regclass::text || ':' || a.privilege_type, ', ') INTO v_bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND a.grantee = 0;
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: PUBLIC holds % on an F01 relation', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (c3) Nobody but the owner may CREATE in this schema. The direct-DML guard
  -- decides by looking for a frame naming a registered ops.f01_* writer, so a
  -- principal that could define its own ops.f01_something() could satisfy that
  -- test at will. REVOKE CREATE at the head of this file is what closes it; this
  -- is the readback proving it stayed closed.
  SELECT string_agg(DISTINCT coalesce(g.rolname, 'PUBLIC'), ', ') INTO v_bad
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(n.nspacl) a
    LEFT JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND a.privilege_type = 'CREATE'
     AND a.grantee IS DISTINCT FROM n.nspowner;
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: % may CREATE in schema ops', v_bad
      USING ERRCODE = '42501';
  END IF;

  -- (d) The named exclusions hold, checked against the ACL rather than against
  -- has_function_privilege(). The difference is deliberate: has_function_privilege
  -- reports EFFECTIVE privilege, so it answers true for a superuser and for any
  -- role that inherits from one, and a schema install has no business refusing to
  -- apply because somebody's dev database grants carr_writer more than it should.
  -- This file verifies what this file did — the grants — and leaves the question
  -- of whether the runtime roles are otherwise over-powered to the fixture, which
  -- runs in an environment it has already established is free of that.
  FOR v_case IN
    SELECT * FROM (VALUES
      ('carr_reader', 'f01_install_policy'),
      ('carr_reader', 'f01_apply_observation'),
      ('carr_reader', 'f01_record_artifact'),
      ('carr_reader', 'f01_record_proposal'),
      ('carr_reader', 'f01_register_derivative_link'),
      ('carr_reader', 'f01_record_document'),
      ('carr_reader', 'f01_record_hold'),
      ('carr_reader', 'f01_record_deletion_evaluation'),
      ('carr_reader', 'f01_replay_outcome'),
      ('carr_reader', 'f01_require_authority_principal'),
      ('carr_writer', 'f01_install_policy'),
      ('carr_writer', 'f01_record_hold'),
      ('carr_writer', 'f01_require_authority_principal')
    ) AS t(role_name, fn)
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_case.role_name) THEN CONTINUE; END IF;
    SELECT string_agg(p.oid::regprocedure::text, ', ') INTO v_bad
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'ops' AND p.proname = v_case.fn
       AND EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                     JOIN pg_roles g ON g.oid = a.grantee
                    WHERE g.rolname = v_case.role_name
                      AND a.privilege_type = 'EXECUTE');
    IF v_bad IS NOT NULL THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % is granted EXECUTE on %',
        v_case.role_name, v_bad USING ERRCODE = '42501';
    END IF;
  END LOOP;

  -- (e) And the positive half, so that a loop which quietly granted NOTHING is a
  -- failure too rather than a clean run. Every role that exists can read every
  -- relation and reach the one read entry point.
  FOREACH v_role IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN CONTINUE; END IF;
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
                  AND NOT has_table_privilege(v_role, c.oid, 'SELECT')) THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % cannot read every F01 relation', v_role
        USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'ops' AND p.proname = 'f01_read'
                  AND NOT has_function_privilege(v_role, p.oid, 'EXECUTE')) THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % cannot reach ops.f01_read', v_role
        USING ERRCODE = '42501';
    END IF;
    IF NOT has_schema_privilege(v_role, 'ops', 'USAGE') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % holds no USAGE on schema ops', v_role
        USING ERRCODE = '42501';
    END IF;
  END LOOP;
END;
$grant_posture$;

-- ===========================================================================
-- Part 2 of 2: ops/document-derivative-registration.candidate.sql, verbatim
-- except the psql-only line noted in the header.
-- ===========================================================================
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

-- (psql-only ON_ERROR_STOP line removed: psycopg applies this file)

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

-- ===========================================================================
-- Tail 1: the runtime grants, restated as static statements.
--
-- The two sources grant carr_reader and carr_writer their F01 surface from
-- inside DO loops, with the role names as string literals. That is correct SQL
-- and invisible to tools/schema_snapshot_grants.py, which composes the canonical
-- carr_reader/carr_writer plan from STATIC grant statements in committed
-- migrations and which the staging bundle-parity gates compare against the
-- database. Every statement below re-grants exactly what the loops above just
-- granted (generated from the catalog of a database this file was applied to),
-- so it changes no privilege and makes the plan and the database agree.
-- mcp-server/test/record-source-authority-live-pg.v5.test.mjs reads the catalog
-- back and fails if the loops and this list ever disagree.
-- ===========================================================================
grant execute on function ops.f01_apply_observation(text,text,text,text,text,jsonb,jsonb,jsonb,jsonb,jsonb,text,text,jsonb) to carr_writer;
grant execute on function ops.f01_canonical_json(jsonb) to carr_reader;
grant execute on function ops.f01_canonical_json(jsonb) to carr_writer;
grant execute on function ops.f01_context_actor_slug() to carr_reader;
grant execute on function ops.f01_context_actor_slug() to carr_writer;
grant execute on function ops.f01_current_field_state(text,text) to carr_reader;
grant execute on function ops.f01_current_field_state(text,text) to carr_writer;
grant execute on function ops.f01_current_policy() to carr_reader;
grant execute on function ops.f01_current_policy() to carr_writer;
grant execute on function ops.f01_current_policy_digest() to carr_reader;
grant execute on function ops.f01_current_policy_digest() to carr_writer;
grant execute on function ops.f01_derivative_coverage(text) to carr_reader;
grant execute on function ops.f01_derivative_coverage(text) to carr_writer;
grant execute on function ops.f01_derivative_coverage_digest(text) to carr_reader;
grant execute on function ops.f01_derivative_coverage_digest(text) to carr_writer;
grant execute on function ops.f01_derivative_links(text) to carr_reader;
grant execute on function ops.f01_derivative_links(text) to carr_writer;
grant execute on function ops.f01_digest_jsonb(jsonb) to carr_reader;
grant execute on function ops.f01_digest_jsonb(jsonb) to carr_writer;
grant execute on function ops.f01_docsource_is_external_ident(text,integer) to carr_reader;
grant execute on function ops.f01_docsource_is_external_ident(text,integer) to carr_writer;
grant execute on function ops.f01_docsource_is_safe_text(text,integer) to carr_reader;
grant execute on function ops.f01_docsource_is_safe_text(text,integer) to carr_writer;
grant execute on function ops.f01_docsource_utf16_length(text) to carr_reader;
grant execute on function ops.f01_docsource_utf16_length(text) to carr_writer;
grant execute on function ops.f01_document_source_history(text) to carr_reader;
grant execute on function ops.f01_document_source_history(text) to carr_writer;
grant execute on function ops.f01_document_version_source(text,integer) to carr_reader;
grant execute on function ops.f01_document_version_source(text,integer) to carr_writer;
grant execute on function ops.f01_hold_inventory(text) to carr_reader;
grant execute on function ops.f01_hold_inventory(text) to carr_writer;
grant execute on function ops.f01_hold_inventory_digest(text) to carr_reader;
grant execute on function ops.f01_hold_inventory_digest(text) to carr_writer;
grant execute on function ops.f01_instant(text) to carr_reader;
grant execute on function ops.f01_instant(text) to carr_writer;
grant execute on function ops.f01_instant_text(p_at timestamp with time zone) to carr_reader;
grant execute on function ops.f01_instant_text(p_at timestamp with time zone) to carr_writer;
grant execute on function ops.f01_is_digest_ref(text) to carr_reader;
grant execute on function ops.f01_is_digest_ref(text) to carr_writer;
grant execute on function ops.f01_is_instant_text(text) to carr_reader;
grant execute on function ops.f01_is_instant_text(text) to carr_writer;
grant execute on function ops.f01_json_number(numeric) to carr_reader;
grant execute on function ops.f01_json_number(numeric) to carr_writer;
grant execute on function ops.f01_json_string(text) to carr_reader;
grant execute on function ops.f01_json_string(text) to carr_writer;
grant execute on function ops.f01_now_text() to carr_reader;
grant execute on function ops.f01_now_text() to carr_writer;
grant execute on function ops.f01_principal() to carr_reader;
grant execute on function ops.f01_principal() to carr_writer;
grant execute on function ops.f01_read(text,jsonb) to carr_reader;
grant execute on function ops.f01_read(text,jsonb) to carr_writer;
grant execute on function ops.f01_record_artifact(jsonb,text,text) to carr_writer;
grant execute on function ops.f01_record_deletion_evaluation(jsonb,text,text,text) to carr_writer;
grant execute on function ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text) to carr_writer;
grant execute on function ops.f01_record_proposal(jsonb,jsonb,jsonb,text,text) to carr_writer;
grant execute on function ops.f01_register_derivative_link(jsonb,text,text) to carr_writer;
grant execute on function ops.f01_replay_outcome(text,text,text) to carr_writer;
grant execute on function ops.f01_reserved_derivative_kinds() to carr_reader;
grant execute on function ops.f01_reserved_derivative_kinds() to carr_writer;
grant execute on function ops.f01_retention_clock(text) to carr_reader;
grant execute on function ops.f01_retention_clock(text) to carr_writer;
grant execute on function ops.f01_retention_clock_digest(text) to carr_reader;
grant execute on function ops.f01_retention_clock_digest(text) to carr_writer;
grant execute on function ops.f01_stored_artifact(text) to carr_reader;
grant execute on function ops.f01_stored_artifact(text) to carr_writer;
grant execute on function ops.f01_stored_artifact_by_identity(text,text,text,text,text) to carr_reader;
grant execute on function ops.f01_stored_artifact_by_identity(text,text,text,text,text) to carr_writer;
grant execute on function ops.f01_stored_derivatives(text) to carr_reader;
grant execute on function ops.f01_stored_derivatives(text) to carr_writer;
grant execute on function ops.f01_tenant() to carr_reader;
grant execute on function ops.f01_tenant() to carr_writer;
grant execute on function ops.f01_utf16_sortkey(text) to carr_reader;
grant execute on function ops.f01_utf16_sortkey(text) to carr_writer;
grant execute on function ops.f01_verify_envelope(jsonb,text,text,text) to carr_reader;
grant execute on function ops.f01_verify_envelope(jsonb,text,text,text) to carr_writer;
grant select on table ops.f01_corporate_artifact to carr_reader;
grant select on table ops.f01_corporate_artifact to carr_writer;
grant select on table ops.f01_deletion_evaluation to carr_reader;
grant select on table ops.f01_deletion_evaluation to carr_writer;
grant select on table ops.f01_derivative_link to carr_reader;
grant select on table ops.f01_derivative_link to carr_writer;
grant select on table ops.f01_document_current to carr_reader;
grant select on table ops.f01_document_current to carr_writer;
grant select on table ops.f01_document_source_provenance to carr_reader;
grant select on table ops.f01_document_source_provenance to carr_writer;
grant select on table ops.f01_document_version to carr_reader;
grant select on table ops.f01_document_version to carr_writer;
grant select on table ops.f01_field_event to carr_reader;
grant select on table ops.f01_field_event to carr_writer;
grant select on table ops.f01_field_state to carr_reader;
grant select on table ops.f01_field_state to carr_writer;
grant select on table ops.f01_idempotency to carr_reader;
grant select on table ops.f01_idempotency to carr_writer;
grant select on table ops.f01_mutation_receipt to carr_reader;
grant select on table ops.f01_mutation_receipt to carr_writer;
grant select on table ops.f01_parsed_proposal to carr_reader;
grant select on table ops.f01_parsed_proposal to carr_writer;
grant select on table ops.f01_policy_current to carr_reader;
grant select on table ops.f01_policy_current to carr_writer;
grant select on table ops.f01_policy_version to carr_reader;
grant select on table ops.f01_policy_version to carr_writer;
grant select on table ops.f01_preservation_hold_current to carr_reader;
grant select on table ops.f01_preservation_hold_current to carr_writer;
grant select on table ops.f01_preservation_hold_event to carr_reader;
grant select on table ops.f01_preservation_hold_event to carr_writer;
grant select on table ops.f01_proposal_link to carr_reader;
grant select on table ops.f01_proposal_link to carr_writer;
grant select on table ops.f01_reconciliation_item to carr_reader;
grant select on table ops.f01_reconciliation_item to carr_writer;
grant select on table ops.f01_state_transition to carr_reader;
grant select on table ops.f01_state_transition to carr_writer;

-- ===========================================================================
-- Tail 2: the authority surface follows carr_authority membership.
--
-- Exactly the set the two sources hand an authority LOGIN: every ops.f01_*
-- function except the private mutation helpers and the trigger guards, SELECT
-- on every ops.f01_* relation, and USAGE on ops. The exclusion list is the
-- union of both sources' private lists; a function missing from it would be
-- caught by the posture readback below rather than silently granted.
-- ===========================================================================
DO $f01_authority_group_grants$
DECLARE
  f record;
  t record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'carr_authority') THEN
    RAISE EXCEPTION 'f01_authority_group_missing: the carr_authority group role is absent';
  END IF;
  EXECUTE 'GRANT USAGE ON SCHEMA ops TO carr_authority';
  FOR f IN SELECT p.oid::regprocedure AS signature, p.proname
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
  LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM carr_authority', f.signature);
    IF f.proname IN ('f01_claim_idempotency', 'f01_settle_idempotency',
                     'f01_insert_derivative_link', 'f01_insert_document_source_provenance')
       OR f.proname LIKE 'f01_guard_%' THEN
      CONTINUE;
    END IF;
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO carr_authority', f.signature);
  END LOOP;
  FOR t IN SELECT c.oid::regclass AS relation FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
  LOOP
    EXECUTE format('REVOKE ALL ON TABLE %s FROM carr_authority', t.relation);
    EXECUTE format('GRANT SELECT ON TABLE %s TO carr_authority', t.relation);
  END LOOP;
END;
$f01_authority_group_grants$;

DO $f01_authority_group_posture$
DECLARE
  v_bad text;
BEGIN
  SELECT string_agg(p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops'
     AND (p.proname IN ('f01_claim_idempotency', 'f01_settle_idempotency',
                        'f01_insert_derivative_link', 'f01_insert_document_source_provenance')
          OR p.proname LIKE 'f01\_guard\_%')
     AND EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                  WHERE a.grantee = 'carr_authority'::regrole AND a.privilege_type = 'EXECUTE');
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: carr_authority holds EXECUTE on a private helper: %', v_bad
      USING ERRCODE = '42501';
  END IF;
  SELECT string_agg(c.oid::regclass::text || ':' || a.privilege_type, ', ') INTO v_bad
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND a.grantee = 'carr_authority'::regrole
     AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE');
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: carr_authority holds DML on %', v_bad
      USING ERRCODE = '42501';
  END IF;
  IF NOT has_function_privilege('carr_authority',
       'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE')
     OR NOT has_function_privilege('carr_authority',
       'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE')
     OR NOT has_function_privilege('carr_authority', 'ops.f01_read(text,jsonb)', 'EXECUTE') THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: carr_authority cannot reach the F01 authority writers'
      USING ERRCODE = '42501';
  END IF;
  -- carr_writer and carr_reader must still be refused the authority writers;
  -- this tail touched neither, and reads that back rather than assuming it.
  IF has_function_privilege('carr_writer',
       'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE')
     OR has_function_privilege('carr_reader',
       'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE') THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: a runtime role reached an authority-only or write surface'
      USING ERRCODE = '42501';
  END IF;
END;
$f01_authority_group_posture$;

-- ===========================================================================
-- Tail 3: the group is the only holder of the authority surface.
--
-- The sources granted that surface directly to whichever authority login
-- existed when this file ran. Tail 2 already gave the group the same set, so
-- the direct grants are redundant for the login and harmful for the seal: the
-- SCAC catalog measures every grant to a carr_* role connected by membership,
-- so a direct login grant counts in production (carr_authority_joe exists) and
-- not in CI (no login exists), and 0627's measured baseline would refuse the
-- production apply. Revoking them makes the catalog the same in both. Schema
-- USAGE is left alone: it is not F01's to take back. The readback then proves
-- each existing login is a carr_authority member and still reaches the
-- authority writers through that membership, and refuses the install if a
-- login is outside the group or NOINHERIT.
-- ===========================================================================
DO $f01_authority_login_direct_grants$
DECLARE
  r text;
  f record;
  t record;
  v_bad text;
BEGIN
  FOREACH r IN ARRAY ARRAY['carr_authority_joe', 'carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN CONTINUE; END IF;
    FOR f IN SELECT p.oid::regprocedure AS signature
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
    LOOP
      EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f.signature, r);
    END LOOP;
    FOR t IN SELECT c.oid::regclass AS relation FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
    LOOP
      EXECUTE format('REVOKE ALL ON TABLE %s FROM %I', t.relation, r);
    END LOOP;
  END LOOP;

  SELECT string_agg(DISTINCT g.rolname || ' -> ' || p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    CROSS JOIN LATERAL aclexplode(p.proacl) a
    JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
     AND g.rolname IN ('carr_authority_joe', 'carr_authority_dell');
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: an authority login holds a direct F01 function grant: %', v_bad
      USING ERRCODE = '42501';
  END IF;
  SELECT string_agg(DISTINCT g.rolname || ' -> ' || c.oid::regclass::text, ', ') INTO v_bad
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND g.rolname IN ('carr_authority_joe', 'carr_authority_dell');
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'f01_grant_posture_violation: an authority login holds a direct F01 relation grant: %', v_bad
      USING ERRCODE = '42501';
  END IF;

  FOREACH r IN ARRAY ARRAY['carr_authority_joe', 'carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN CONTINUE; END IF;
    -- An authority login that exists but is outside the group would have just
    -- lost its direct grants above and gained nothing back: it would silently
    -- stop reaching every F01 authority writer. That is a broken install, not a
    -- notice, so it refuses and the whole transaction (revokes included) rolls
    -- back. 0273 and db/schema.sql make every existing login a member.
    IF NOT pg_has_role(r, 'carr_authority', 'MEMBER') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: authority login % exists but is not a carr_authority member, so revoking its direct F01 grants would leave it no authority surface', r
        USING ERRCODE = '42501';
    END IF;
    IF NOT has_function_privilege(r, 'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE')
       OR NOT has_function_privilege(r, 'ops.f01_read(text,jsonb)', 'EXECUTE')
       OR NOT has_table_privilege(r, 'ops.f01_state_transition', 'SELECT') THEN
      RAISE EXCEPTION 'f01_grant_posture_violation: % is a carr_authority member but does not inherit the F01 authority surface', r
        USING ERRCODE = '42501';
    END IF;
  END LOOP;
END;
$f01_authority_login_direct_grants$;
