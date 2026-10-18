-- V5-F01 persistence tail — PostgreSQL fixtures.
--
-- HOW TO RUN THIS. Never by hand against anything that matters. It is applied by
-- ops/record-source-authority-local-pg-gate.py against a DISPOSABLE local
-- database, after the parent-bound predecessor migrations and exactly one F01
-- successor have been applied. It creates no role, reads no credential, reaches
-- no provider and touches no legacy row.
--
-- WHAT IT PROVES, and what it deliberately leaves to the gate:
--
--   HERE  Structure. Positive round trips for every stored record kind, the
--         refusal matrix, compare-and-swap, append-only history, direct-DML
--         refusal, wrong-principal refusal, idempotency substitution, corrupt
--         newest-row readback, and that public.record_source / public.document
--         are byte-identical before and after.
--
--   GATE  Independence. The byte-for-byte agreement between
--         ops.f01_canonical_json and the Node canonicalJson is asserted by the
--         gate, which feeds PostgreSQL bytes it computed in Node. The canonical
--         cases in section 2 below are the SAME cases the Node suite asserts, so
--         a drift on either side fails both, but the database is never allowed
--         to be the source of its own expected bytes.
--
--   GATE  Concurrency. Section 16 EMITS a two-sided race request rather than
--         running it, because one connection cannot race itself. The gate runs
--         the two emitted calls in two sessions and requires exactly one winner.
--
-- WHO RUNS WHAT. This fixture switches identity as it goes, because the schema
-- derives its principal from session_user and there is no other honest way to
-- exercise it. Section 0 blocks the run outright if the real principals are not
-- present. The map, section by section:
--
--   the applying superuser       0-3, 3.5.1, 3.5.5, 11, 12, 12.1, 14 (the
--   (the gate's --fixture-role,  ALTER TABLE halves), 15, 17
--    or the owner if none)
--   carr_authority_joe           4 (policy), 9 (holds), 3.5.2
--   carr_writer                  5-8 (evidence, proposals, derivative-source
--                                registrations, documents), 10 (deletion), 13
--                                (idempotency), 14 (the read halves), 16, 3.5.3
--   carr_reader                  3.5.4
--
-- Sections 11 and 12 run as the OWNER on purpose: a principal with no DML grant
-- proves nothing by being refused DML, so the guards are tested by the one
-- identity that could otherwise have written.
--
-- EVERY VALUE IS SYNTHETIC. "synthetic-", "SYNTHETIC-" and the repeated-digit
-- digests are unmistakably test data. No real account, native id, object key,
-- drive item, field owner, retention period or hold appears anywhere.

\set ON_ERROR_STOP on
\timing off

-- ===========================================================================
-- 0. Bootstrap, or block. There is no third option.
--
-- WHY THIS SECTION IS THE SHAPE IT IS. ops.f01_context_actor_slug() derives the
-- principal from session_user. It reads no GUC that could name an actor, so a
-- fixture cannot describe itself as Joe by setting one — an earlier revision of
-- this file tried exactly that, and under the shipped schema every one of its
-- writer calls would have refused with f01_principal_refused at the first
-- statement past section 4.2. The only way to exercise the real semantics is to
-- BE the real principals, which means SET SESSION AUTHORIZATION, which means a
-- superuser session and four roles that already exist.
--
-- THE FIXTURE CREATES NO ROLE, by standing instruction, and it never weakens the
-- authorization path to accommodate a database that is missing one. If the
-- principals or ops.authority_actor_slug() are absent, this raises
-- F01 FIXTURE BLOCKED naming the exact missing piece and stops. A run that
-- "passed" by falling back to the schema owner would be proving that the owner
-- can do authority work, which is the precise thing the schema forbids.
--
-- WHAT THE GATE MUST SUPPLY. A disposable database in which:
--   * the connecting user is a superuser (SET SESSION AUTHORIZATION requires it;
--     role membership is NOT sufficient and is not checked for that reason).
--     THIS NEED NOT BE — AND SHOULD NOT BE — THE SCHEMA OWNER: the gate takes a
--     separate --fixture-role for exactly this one file, so that the owner it
--     makes every privilege proof about can remain unprivileged. Nothing here
--     requires the two to be the same login, and nothing here grants either of
--     them anything,
--   * carr_reader, carr_writer, carr_authority_joe and carr_authority_dell exist,
--   * domain.sql has been applied, AND THEN the document-source hunk
--     (ops/document-derivative-registration.candidate.sql, or the successor
--     migration carrying it) has been applied on top of it. That order is not a
--     preference: the hunk forward-replaces ops.f01_record_document with the
--     six-argument form this fixture calls, and domain.sql applied AFTER it is a
--     hard stop inside the hunk's own posture readback. Section 0 blocks on both,
--     by name,
--   * THE CANONICAL AUTHORITY BOUNDARY IS ALREADY IN PLACE. That is
--     0161_control_plane_authority_boundary.sql, and it — not this fixture and
--     not domain.sql — is what defines ops.authority_actor_slug() as a SECURITY
--     DEFINER function over session_user returning 'joe'/'dell', revokes it from
--     PUBLIC, and grants EXECUTE on it to the NOLOGIN group role carr_authority.
--     Section 0 below reads that arrangement back, because F01 depends on three
--     separate parts of it and each part fails differently:
--       - the helper must EXIST, or the authority path refuses outright;
--       - the two authority LOGINS must be able to execute it (they are members
--         of carr_authority), because ops.f01_require_authority_principal is
--         CALLER-RIGHTS and reaches the helper as the login;
--       - the F01 SCHEMA OWNER must be able to execute it, because
--         ops.f01_context_actor_slug is SECURITY DEFINER and reaches the helper
--         as the owner. The F01 grant loop cannot supply this: the helper's name
--         does not match f01\_%, and this fixture grants nothing either.
--     A missing piece BLOCKS here, by name. It is never granted, worked around,
--     or resolved to the schema owner.
-- ===========================================================================

DO $bootstrap$
DECLARE
  v_missing text[] := ARRAY[]::text[];
  r text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper) THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: % is not a superuser, so this fixture cannot SET SESSION AUTHORIZATION to the real principals. It will not impersonate them with a GUC instead.', current_user;
  END IF;

  IF to_regnamespace('ops') IS NULL OR to_regprocedure('ops.f01_read(text,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: the F01 domain schema is not installed in this database (ops.f01_read is absent). Apply domain.sql first.';
  END IF;

  -- THE DOCUMENT-SOURCE HUNK MUST ALREADY BE APPLIED, and this blocks rather
  -- than adapting. The persistence tail completes every document version with a
  -- provenance statement through the SIX-argument ops.f01_record_document, so a
  -- database carrying only the four-argument writer is not the schema this
  -- fixture describes. Section 8 would fail on "function does not exist" three
  -- hundred statements from here; this says what to apply instead.
  IF to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') IS NULL THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: the six-argument ops.f01_record_document is absent. Apply domain.sql and then ops/document-derivative-registration.candidate.sql (or the successor migration carrying it), in that order.';
  END IF;
  IF to_regclass('ops.f01_document_source_provenance') IS NULL
     OR to_regprocedure('ops.f01_document_version_source(text,integer)') IS NULL THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: the document-source relation or its reader is absent; the document half of the derivative-registration rule is not installed here.';
  END IF;

  FOREACH r IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      v_missing := v_missing || r;
    END IF;
  END LOOP;
  IF cardinality(v_missing) > 0 THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: missing principal role(s): %. This fixture creates no role and will not substitute the schema owner for one.',
      array_to_string(v_missing, ', ');
  END IF;

  -- A carr_* role that is itself a superuser would pass every refusal test for
  -- the wrong reason, so it blocks here rather than producing a green run.
  SELECT array_agg(rolname ORDER BY rolname) INTO v_missing
    FROM pg_roles
   WHERE rolname IN ('carr_reader','carr_writer','carr_authority_joe','carr_authority_dell')
     AND rolsuper;
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: runtime principal(s) % are superusers; every least-privilege assertion below would pass vacuously.',
      array_to_string(v_missing, ', ');
  END IF;

  IF to_regprocedure('ops.authority_actor_slug()') IS NULL THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: ops.authority_actor_slug() is not installed. The authority path depends on it and refuses rather than falling back to the owner, so there is nothing here to exercise.';
  END IF;

  -- The canonical helper's own posture, read back rather than assumed. A helper
  -- that PUBLIC could execute would make the authority derivation reachable by
  -- every principal in the cluster, and this fixture would then be proving the
  -- refusal matrix against a boundary that had already been opened elsewhere.
  IF EXISTS (SELECT 1 FROM pg_proc p, LATERAL aclexplode(
               coalesce(p.proacl, acldefault('f', p.proowner))) a
              WHERE p.oid = 'ops.authority_actor_slug()'::regprocedure
                AND a.grantee = 0 AND a.privilege_type = 'EXECUTE') THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: PUBLIC holds EXECUTE on ops.authority_actor_slug(). 0161_control_plane_authority_boundary.sql revokes exactly that; this fixture does not re-revoke it and will not test around it.';
  END IF;

  -- THE TWO PATHS TO THE HELPER, each needed by a different F01 function and
  -- each supplied by the parent, never by this file.
  FOREACH r IN ARRAY ARRAY['carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT has_function_privilege(r, 'ops.authority_actor_slug()', 'EXECUTE') THEN
      RAISE EXCEPTION 'F01 FIXTURE BLOCKED: % cannot EXECUTE ops.authority_actor_slug(). 0161 grants that to the group role carr_authority and this login must be a member of it: ops.f01_require_authority_principal is caller-rights and reaches the helper AS THIS LOGIN. This fixture grants nothing.', r;
    END IF;
  END LOOP;

  IF NOT has_function_privilege(
       (SELECT pg_get_userbyid(proowner) FROM pg_proc
         WHERE oid = 'ops.f01_context_actor_slug()'::regprocedure),
       'ops.authority_actor_slug()', 'EXECUTE') THEN
    RAISE EXCEPTION 'F01 FIXTURE BLOCKED: the owner of ops.f01_context_actor_slug() cannot EXECUTE ops.authority_actor_slug(). That function is SECURITY DEFINER, so it reaches the helper as its owner; the owner must own the helper or be a member of carr_authority. The F01 grant loop cannot supply this — the helper does not match f01\_%%.';
  END IF;
END;
$bootstrap$;

-- The fixture's own bookkeeping. NOTE THE ABSENT serial: a sequence lives in the
-- temp schema too, and USAGE on it is a separate grant that a session running
-- under SET SESSION AUTHORIZATION would not hold. A timestamp orders the log
-- just as well and needs no sequence.
CREATE TEMP TABLE f01_fixture_log (
  logged_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  section    text NOT NULL,
  label      text NOT NULL,
  outcome    text NOT NULL,
  detail     text
);

CREATE TEMP TABLE f01_fixture_state (
  key   text PRIMARY KEY,
  value text
);

-- These are temp tables owned by the bootstrap superuser, and every section
-- below writes to them under a DIFFERENT identity. Without these grants the
-- first assertion made as carr_writer would fail on the log rather than on the
-- thing it was testing. PUBLIC is the right grantee for a per-session temp
-- relation that vanishes with the connection.
GRANT SELECT, INSERT, UPDATE ON f01_fixture_log, f01_fixture_state TO PUBLIC;

-- ===========================================================================
-- 1. Fixture helpers.
-- ===========================================================================

-- Grant only disposable helpers to the fixture's callers. Production creator
-- defaults no longer grant EXECUTE to PUBLIC; later helper definitions need
-- the same explicit caller scope as the helpers defined here.
DO $fixture_helper_defaults$
BEGIN
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO carr_reader, carr_writer, carr_authority_joe, carr_authority_dell',
                 pg_my_temp_schema()::regnamespace::text);
END;
$fixture_helper_defaults$;

CREATE FUNCTION pg_temp.f01_note(p_section text, p_label text, p_outcome text,
                                 p_detail text DEFAULT NULL)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO f01_fixture_log (section, label, outcome, detail)
  VALUES (p_section, p_label, p_outcome, p_detail);
$$;

CREATE FUNCTION pg_temp.f01_assert(p_condition boolean, p_section text, p_label text,
                                   p_detail text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF p_condition IS NOT TRUE THEN
    RAISE EXCEPTION 'F01 FIXTURE FAILED [%] %: %', p_section, p_label, coalesce(p_detail, '');
  END IF;
  PERFORM pg_temp.f01_note(p_section, p_label, 'passed', p_detail);
END;
$$;

CREATE FUNCTION pg_temp.f01_assert_eq(p_actual text, p_expected text,
                                      p_section text, p_label text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF p_actual IS DISTINCT FROM p_expected THEN
    RAISE EXCEPTION 'F01 FIXTURE FAILED [%] %: expected %, got %',
      p_section, p_label, coalesce(p_expected, '<null>'), coalesce(p_actual, '<null>');
  END IF;
  PERFORM pg_temp.f01_note(p_section, p_label, 'passed', p_expected);
END;
$$;

/**
 * Run one statement that MUST refuse, and require the refusal to be the named
 * one. A statement that succeeds is a fixture failure, and so is a statement
 * that fails for a different reason — a refusal matrix that accepts any error is
 * not a refusal matrix.
 */
CREATE FUNCTION pg_temp.f01_expect_refusal(p_sql text, p_fragment text,
                                           p_section text, p_label text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_message text;
BEGIN
  BEGIN
    EXECUTE p_sql;
  EXCEPTION WHEN OTHERS THEN
    v_message := SQLERRM;
    IF strpos(v_message, p_fragment) = 0 THEN
      RAISE EXCEPTION 'F01 FIXTURE FAILED [%] %: expected refusal containing "%", got "%"',
        p_section, p_label, p_fragment, v_message;
    END IF;
    PERFORM pg_temp.f01_note(p_section, p_label, 'refused', v_message);
    RETURN;
  END;
  RAISE EXCEPTION 'F01 FIXTURE FAILED [%] %: the statement SUCCEEDED and should have refused',
    p_section, p_label;
END;
$$;

/** Build a stored-record envelope exactly as the Node store builds one. */
CREATE FUNCTION pg_temp.f01_envelope(p_kind text, p_record jsonb,
                                     p_extra jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-record-envelope.v1',
    'record_kind', p_kind,
    'tenant', 'carr-internal',
    'record', p_record,
    'record_digest', ops.f01_digest_jsonb(p_record)
  ) || p_extra;
$$;

CREATE FUNCTION pg_temp.f01_actor() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT ops.f01_principal() ->> 'actor_slug';
$$;

CREATE FUNCTION pg_temp.f01_remember(p_key text, p_value text) RETURNS void
LANGUAGE sql AS $$
  INSERT INTO f01_fixture_state (key, value) VALUES (p_key, p_value)
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
$$;

CREATE FUNCTION pg_temp.f01_recall(p_key text) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT value FROM f01_fixture_state WHERE key = p_key;
$$;

-- ===========================================================================
-- 2. Canonical bytes.
--
-- These are the SAME cases mcp-server/test/record-source-authority-store.v5.test.mjs
-- asserts against canonicalJson. The gate additionally feeds PostgreSQL bytes
-- computed in Node, so the two implementations are compared without either one
-- supplying the other's expectation.
-- ===========================================================================

DO $canonical$
DECLARE
  v_case record;
BEGIN
  FOR v_case IN
    SELECT * FROM (VALUES
      ('{"a":1,"b":"x"}',                    '{"a":1,"b":"x"}'),
      ('{"b":1,"a":2}',                      '{"a":2,"b":1}'),
      ('{"k":null}',                         '{"k":null}'),
      ('{"k":true,"j":false}',               '{"j":false,"k":true}'),
      ('{"k":[1,2,3]}',                      '{"k":[1,2,3]}'),
      ('{"k":[]}',                           '{"k":[]}'),
      ('{"k":{}}',                           '{"k":{}}'),
      ('{"k":0.75}',                         '{"k":0.75}'),
      ('{"k":9007199254740991}',              '{"k":9007199254740991}'),
      ('{"k":0}',                            '{"k":0}'),
      ('{"k":-1}',                           '{"k":-1}'),
      -- Unicode rides through literally; only the JSON escapes are escaped.
      ('{"é":"café"}',             '{"é":"café"}'),
      ('{"k":"line\nbreak"}',                '{"k":"line\nbreak"}'),
      ('{"k":"tab\there"}',                  '{"k":"tab\there"}'),
      ('{"k":"quote\"and\\slash"}',          '{"k":"quote\"and\\slash"}'),
      ('{"k":""}',                           '{"k":""}'),
      -- U+007F is NOT escaped by JSON.stringify, so it must not be escaped here.
      ('{"k":""}',                     E'{"k":""}'),
      ('{"k":"🗂"}',               '{"k":"🗂"}'),
      -- Timestamp boundaries are stored as TEXT, so no parser can renormalize
      -- them between the two implementations.
      ('{"observed_at":"2026-02-28T23:59:59.999Z"}',
       '{"observed_at":"2026-02-28T23:59:59.999Z"}'),
      ('{"observed_at":"2028-02-29T00:00:00Z"}',
       '{"observed_at":"2028-02-29T00:00:00Z"}'),
      ('{"observed_at":"2026-09-09T12:00:00-07:00"}',
       '{"observed_at":"2026-09-09T12:00:00-07:00"}')
    ) AS t(input, expected)
  LOOP
    PERFORM pg_temp.f01_assert_eq(
      ops.f01_canonical_json(v_case.input::jsonb), v_case.expected,
      'canonical', 'canonical_json ' || v_case.input);
  END LOOP;
END;
$canonical$;

-- Key ordering is UTF-16 CODE UNIT order, so an astral key sorts BELOW a high
-- BMP key. Code-point ordering would put them the other way round.
SELECT pg_temp.f01_assert_eq(
  ops.f01_canonical_json(jsonb_build_object(chr(65280), 2, chr(65536), 1)),
  '{"' || chr(65536) || '":1,"' || chr(65280) || '":2}',
  'canonical', 'utf16 code unit key order');

SELECT pg_temp.f01_assert(
  ops.f01_utf16_sortkey(chr(65536)) < ops.f01_utf16_sortkey(chr(65280)),
  'canonical', 'astral sort key precedes high BMP');

-- BOTH DIGEST-BEARING ORDERINGS ARE COLLATION-PINNED, and this is asserted
-- against the SOURCE rather than against behaviour on purpose. Key order inside
-- f01_canonical_json and hold order inside f01_hold_inventory both feed digests
-- that are stored and later re-derived, so they must be properties of the bytes
-- rather than of the cluster's default collation. On a C-collation cluster the
-- two orders coincide, so no behavioural case here could distinguish them; what
-- CAN be checked, exactly and now, is that the COLLATE "C" did not go missing.
SELECT pg_temp.f01_assert(
  pg_get_functiondef('ops.f01_canonical_json(jsonb)'::regprocedure) LIKE '%COLLATE "C"%'
  AND pg_get_functiondef('ops.f01_hold_inventory(text)'::regprocedure) LIKE '%COLLATE "C"%'
  -- The derivative link list and the kind list inside the coverage answer feed
  -- ops.f01_derivative_coverage_digest, which a deletion evaluation is bound to
  -- and the writer re-derives. Same rule, same reason.
  AND pg_get_functiondef('ops.f01_derivative_links(text)'::regprocedure) LIKE '%COLLATE "C"%'
  AND pg_get_functiondef('ops.f01_derivative_coverage(text)'::regprocedure) LIKE '%COLLATE "C"%',
  'canonical', 'every digest-bearing ordering is pinned to C collation');

-- 2.1 THE EXPONENT BOUNDARIES ARE REPRODUCED, NOT REFUSED.
--
-- An earlier revision of this fixture asserted that 1e21 and 1e-7 raise
-- f01_number_outside_canonical_range. No such error exists in the schema, and no
-- such refusal would be correct: JavaScript has a defined string form for both,
-- ops.f01_json_number reproduces it, and a refusal here would make a whole class
-- of legal JSON undigestible. What follows are the four cases that actually
-- matter — the two values immediately inside the plain-decimal range and the two
-- immediately outside it — so that a drift in EITHER direction fails.
DO $number_boundaries$
DECLARE
  v_case record;
BEGIN
  FOR v_case IN
    SELECT * FROM (VALUES
      -- Inside: plain decimal, exactly as String() renders it.
      ('{"k":1e20}',    '{"k":100000000000000000000}'),
      ('{"k":1e-6}',    '{"k":0.000001}'),
      -- Outside: exponent notation, with the '+' that JavaScript prints for a
      -- positive exponent and does not print for a negative one.
      ('{"k":1e21}',    '{"k":1e+21}'),
      ('{"k":1e-7}',    '{"k":1e-7}'),
      ('{"k":1.25e21}', '{"k":1.25e+21}'),
      ('{"k":1.5e-7}',  '{"k":1.5e-7}'),
      ('{"k":-1e-7}',   '{"k":-1e-7}'),
      ('{"k":-1e21}',   '{"k":-1e+21}')
    ) AS t(input, expected)
  LOOP
    PERFORM pg_temp.f01_assert_eq(
      ops.f01_canonical_json(v_case.input::jsonb), v_case.expected,
      'canonical', 'js number form ' || v_case.input);
  END LOOP;
END;
$number_boundaries$;

-- 2.2 WHAT IS REFUSED IS PRECISION A JAVASCRIPT NUMBER NEVER HAD.
-- 9007199254740993 is 2^53+1: exact as a numeric, unrepresentable as a double.
-- Node could not have serialized it, so PostgreSQL refuses to invent canonical
-- bytes for it rather than silently hashing 9007199254740992 instead.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_canonical_json('{"k":9007199254740993}'::jsonb)$$,
  'f01_number_not_js_roundtrip', 'canonical', 'a value beyond double precision refuses');
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_canonical_json('{"k":0.1000000000000000000000001}'::jsonb)$$,
  'f01_number_not_js_roundtrip', 'canonical', 'excess decimal precision refuses');

-- And 2^53 itself, one below, round-trips and is therefore canonical.
SELECT pg_temp.f01_assert_eq(
  ops.f01_canonical_json('{"k":9007199254740992}'::jsonb), '{"k":9007199254740992}',
  'canonical', 'the largest exact integer double is canonical');

SELECT pg_temp.f01_assert_eq(
  ops.f01_digest_jsonb('{"a":1}'::jsonb),
  'sha256:' || encode(sha256(convert_to('{"a":1}', 'UTF8')), 'hex'),
  'canonical', 'digest is sha256 over the canonical bytes');

-- ===========================================================================
-- 3. Legacy compatibility — the BEFORE snapshot.
--
-- Column shape, constraint names, grants and row counts for the two legacy
-- tables. Nothing in this fixture writes to either, and section 15 proves the
-- snapshot is unchanged.
--
-- BOTH SNAPSHOTS ARE TAKEN UNDER THE SAME IDENTITY, and that identity is the
-- bootstrap superuser. information_schema.role_table_grants is grantee-relative:
-- it shows only the grants the CURRENT role is party to. Fingerprinting the
-- before-image as one principal and the after-image as another would produce a
-- difference that says nothing about whether the legacy tables changed, so
-- section 15 runs after the authorization switches have been reset.
-- ===========================================================================

CREATE TEMP TABLE f01_legacy_snapshot (
  phase       text NOT NULL,
  table_name  text NOT NULL,
  fingerprint text NOT NULL
);

GRANT SELECT, INSERT ON f01_legacy_snapshot TO PUBLIC;

CREATE FUNCTION pg_temp.f01_legacy_fingerprint(p_table text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
  v_columns text;
  v_constraints text;
  v_grants text;
  v_rows bigint;
BEGIN
  IF to_regclass('public.' || p_table) IS NULL THEN
    RETURN 'absent';
  END IF;
  SELECT string_agg(format('%s:%s:%s:%s', column_name, data_type, is_nullable,
                           coalesce(column_default, '-')), '|' ORDER BY ordinal_position)
    INTO v_columns
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = p_table;
  SELECT coalesce(string_agg(conname || ':' || pg_get_constraintdef(oid), '|' ORDER BY conname), '')
    INTO v_constraints
    FROM pg_constraint WHERE conrelid = ('public.' || p_table)::regclass;
  SELECT coalesce(string_agg(grantee || ':' || privilege_type, '|'
                             ORDER BY grantee, privilege_type), '')
    INTO v_grants
    FROM information_schema.role_table_grants
   WHERE table_schema = 'public' AND table_name = p_table;
  EXECUTE format('SELECT count(*) FROM public.%I', p_table) INTO v_rows;
  RETURN format('cols=%s;cons=%s;grants=%s;rows=%s', v_columns, v_constraints, v_grants, v_rows);
END;
$$;

INSERT INTO f01_legacy_snapshot (phase, table_name, fingerprint)
SELECT 'before', t, pg_temp.f01_legacy_fingerprint(t)
  FROM unnest(ARRAY['record_source', 'document']) AS t;

SELECT pg_temp.f01_note('legacy', 'before snapshot captured', 'recorded',
  (SELECT string_agg(table_name || '=' || left(fingerprint, 40), ', ')
     FROM f01_legacy_snapshot WHERE phase = 'before'));

-- ===========================================================================
-- 3.5 WHO THE DATABASE THINKS YOU ARE.
--
-- Every refusal in the rest of this file rests on ops.f01_context_actor_slug()
-- deriving the principal from session_user rather than from anything a caller
-- can set. This section proves that derivation directly, before any record
-- exists to confuse the picture, and it proves the negative case that matters
-- most: a session that CLAIMS to be Joe is not Joe.
--
-- SET SESSION AUTHORIZATION, not SET ROLE. SET ROLE changes current_user and
-- leaves session_user untouched, so a fixture built on it would be testing
-- nothing at all — the schema would still see the superuser underneath and every
-- assertion below would pass or fail for reasons unrelated to the principal.
-- ===========================================================================

-- 3.5.1 The applying session is NOT a principal, whether it owns the schema or
-- merely holds every privilege in the cluster. Owning the tables — or being able
-- to bypass every ACL over them — confers no standing to write THROUGH them,
-- which is the whole point of deriving the actor rather than accepting one.
-- These refusals name session_user, so they hold for the owner and for a
-- separate bootstrap superuser identically.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_principal()$$,
  'f01_principal_refused', 'principal',
  'the bootstrap superuser is not an F01 principal');
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_read('current_policy')$$,
  'f01_principal_refused', 'principal',
  'the owner cannot even read through the F01 entry point');
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_artifact('{}'::jsonb, 'syn-pg-principal-owner-0001',
      'sha256:' || repeat('7', 64))$$,
  'f01_principal_refused', 'principal',
  'the owner cannot record evidence');
-- The two authorityOnly surfaces refuse the owner one step earlier still, by
-- name, and NEVER by resolving the authority to whoever installed the schema.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_install_policy('{}'::jsonb, null, 'syn-pg-principal-owner-0002',
      'sha256:' || repeat('7', 64))$$,
  'f01_authority_principal_refused', 'principal',
  'the owner is refused the policy surface by name');
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_hold('{}'::jsonb, null, 'syn-pg-principal-owner-0003',
      'sha256:' || repeat('7', 64))$$,
  'f01_authority_principal_refused', 'principal',
  'the owner is refused the hold surface by name');

-- 3.5.2 GENUINE JOE. A real authority connection is human and a verified
-- partner because of WHO IT IS, and no session setting can talk it out of that.
SET SESSION AUTHORIZATION carr_authority_joe;

DO $genuine_joe$
DECLARE
  v_principal jsonb := ops.f01_principal();
BEGIN
  PERFORM pg_temp.f01_remember('authority_slug', v_principal ->> 'actor_slug');
  PERFORM pg_temp.f01_assert(
    (v_principal ->> 'actor_slug') ~ '^[a-z][a-z0-9-]{1,62}$',
    'principal', 'ops.authority_actor_slug() returns a well-shaped slug',
    v_principal ->> 'actor_slug');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'human', 'true',
    'principal', 'a genuine authority connection is human');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'authorization_class', 'verified_partner',
    'principal', 'a genuine authority connection is a verified partner');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'derived_by',
    'authenticated_database_principal',
    'principal', 'the principal says where it came from');

  -- THE TWO DERIVATIONS AGREE, AND THEY ARRIVE BY DIFFERENT ROUTES.
  --
  -- ops.f01_principal() reads its slug through ops.f01_context_actor_slug(),
  -- which is SECURITY DEFINER and therefore calls ops.authority_actor_slug() as
  -- the schema OWNER. ops.f01_require_authority_principal() is caller-rights and
  -- calls the same helper as THIS LOGIN, over the EXECUTE grant carr_authority
  -- holds. Two callers, two grants, one answer — because the helper derives from
  -- session_user, which SECURITY DEFINER does not change.
  --
  -- This is a CONSISTENCY assertion, not a repair. The asymmetry is deliberate
  -- and is left exactly as it is: making the caller-rights function a definer
  -- would not change a single answer here, it would only move which role needs
  -- the helper grant.
  PERFORM pg_temp.f01_assert_eq(
    ops.f01_require_authority_principal('fixture-authority-derivation-probe'),
    v_principal ->> 'actor_slug',
    'principal', 'the caller-rights and definer-rights authority derivations agree');
  PERFORM pg_temp.f01_assert_eq(
    ops.authority_actor_slug(), v_principal ->> 'actor_slug',
    'principal', 'and both agree with the canonical helper called directly');
END;
$genuine_joe$;

-- The forged-GUC case, from the inside. Joe sets every flag AGAINST himself and
-- stays exactly as human and as verified as he was, because none of these keys
-- is read by anything.
SELECT set_config('carr.actor_human', 'false', false);
SELECT set_config('carr.actor_authorization_class', 'sponsored_agent', false);
SELECT set_config('carr.actor_slug', 'somebody-else', false);
SELECT set_config('carr.acting_actor_slug', 'somebody-else', false);

DO $joe_cannot_demote_himself$
DECLARE
  v_principal jsonb := ops.f01_principal();
BEGIN
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'human', 'true',
    'principal', 'carr.actor_human=false does not make Joe non-human');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'authorization_class', 'verified_partner',
    'principal', 'carr.actor_authorization_class does not demote a verified partner');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'actor_slug',
    pg_temp.f01_recall('authority_slug'),
    'principal', 'no GUC renames the authority principal');
END;
$joe_cannot_demote_himself$;

SELECT set_config('carr.actor_human', '', false);
SELECT set_config('carr.actor_authorization_class', '', false);
SELECT set_config('carr.actor_slug', '', false);
SELECT set_config('carr.acting_actor_slug', '', false);

-- 3.5.3 THE ORDINARY WRITER. Its acting slug is attribution and nothing more.
SET SESSION AUTHORIZATION carr_writer;

-- With no acting slug there is nobody to attribute the write to, and that is a
-- refusal rather than a default.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_principal()$$,
  'f01_no_authenticated_actor', 'principal',
  'a sponsored connection with no acting actor refuses');

-- Now the impersonation attempt: a carr_writer session naming itself with the
-- AUTHORITY's own slug. It is attributed under that name — attribution is what
-- the key is for — and it is still not human, still not a verified partner, and
-- still cannot reach either authorityOnly surface.
SELECT set_config('carr.acting_actor_slug', pg_temp.f01_recall('authority_slug'), false);

DO $false_joe$
DECLARE
  v_principal jsonb := ops.f01_principal();
BEGIN
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'actor_slug',
    pg_temp.f01_recall('authority_slug'),
    'principal', 'the acting slug is honoured as attribution');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'human', 'false',
    'principal', 'wearing the authority slug does not make a writer human');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'authorization_class', 'sponsored_agent',
    'principal', 'wearing the authority slug does not confer verified_partner');
END;
$false_joe$;

-- 3.5.4 The reader is named by the schema, not by itself.
SET SESSION AUTHORIZATION carr_reader;
DO $reader_principal$
DECLARE
  v_principal jsonb := ops.f01_principal();
BEGIN
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'actor_slug', 'carr-reader',
    'principal', 'the reader principal is fixed by the schema');
  PERFORM pg_temp.f01_assert_eq(v_principal ->> 'human', 'false',
    'principal', 'the reader principal is not human');
END;
$reader_principal$;
-- A reader that sets an acting slug is still the reader.
SELECT set_config('carr.acting_actor_slug', 'somebody-else', false);
SELECT pg_temp.f01_assert_eq(ops.f01_principal() ->> 'actor_slug', 'carr-reader',
  'principal', 'an acting slug cannot rename the reader');
SELECT set_config('carr.acting_actor_slug', '', false);

RESET SESSION AUTHORIZATION;

-- 3.5.5 THE GRANT LAYER, which refuses before the function body is ever
-- entered. The refusals in 3.5.1 prove the in-function check; these prove that
-- no non-authority runtime principal can reach the check in the first place.
-- Both layers, because either one alone is a single point of failure.
DO $least_privilege$
DECLARE
  v_case record;
BEGIN
  FOR v_case IN
    SELECT * FROM (VALUES
      ('carr_reader', 'f01_install_policy'),
      ('carr_reader', 'f01_apply_observation'),
      ('carr_reader', 'f01_record_artifact'),
      ('carr_reader', 'f01_record_proposal'),
      -- A read-only principal is not a producer workflow.
      ('carr_reader', 'f01_register_derivative_link'),
      ('carr_reader', 'f01_record_document'),
      ('carr_reader', 'f01_record_hold'),
      ('carr_reader', 'f01_record_deletion_evaluation'),
      ('carr_reader', 'f01_replay_outcome'),
      ('carr_reader', 'f01_require_authority_principal'),
      ('carr_writer', 'f01_install_policy'),
      ('carr_writer', 'f01_record_hold'),
      ('carr_writer', 'f01_require_authority_principal'),
      -- The private helpers are reachable by nobody at runtime.
      ('carr_reader', 'f01_claim_idempotency'),
      ('carr_writer', 'f01_claim_idempotency'),
      ('carr_authority_joe', 'f01_claim_idempotency'),
      ('carr_reader', 'f01_settle_idempotency'),
      ('carr_writer', 'f01_settle_idempotency'),
      ('carr_authority_joe', 'f01_settle_idempotency'),
      ('carr_reader', 'f01_insert_derivative_link'),
      ('carr_writer', 'f01_insert_derivative_link'),
      ('carr_authority_joe', 'f01_insert_derivative_link'),
      -- The document-source seam's private inserter, on the same terms. It is
      -- reached only from inside ops.f01_record_document, where it runs as the
      -- owner, so nobody needs EXECUTE and any EXECUTE is a hole. All four
      -- principals are named because a helper granted to the AUTHORITY logins
      -- would be just as reachable as one granted to the writer.
      ('carr_reader', 'f01_insert_document_source_provenance'),
      ('carr_writer', 'f01_insert_document_source_provenance'),
      ('carr_authority_joe', 'f01_insert_document_source_provenance'),
      ('carr_authority_dell', 'f01_insert_document_source_provenance'),
      ('carr_authority_joe', 'f01_guard_direct_dml'),
      ('carr_writer', 'f01_guard_append_only')
    ) AS t(role_name, fn)
  LOOP
    PERFORM pg_temp.f01_assert(
      NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'ops' AND p.proname = v_case.fn
                     AND has_function_privilege(v_case.role_name, p.oid, 'EXECUTE')),
      'principal', format('%s may not execute ops.%s', v_case.role_name, v_case.fn));
  END LOOP;

  -- PUBLIC holds EXECUTE on nothing under the prefix. A NULL proacl counts as a
  -- violation: for a function the built-in default grants PUBLIC EXECUTE, so a
  -- NULL there means the revoke loop skipped it entirely.
  PERFORM pg_temp.f01_assert(
    NOT EXISTS (
      SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
         AND (p.proacl IS NULL
              OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                          WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))),
    'principal', 'PUBLIC holds EXECUTE on no ops.f01_* function');

  -- And the positive half: each principal can still do its own job.
  PERFORM pg_temp.f01_assert(
    has_function_privilege('carr_writer', 'ops.f01_apply_observation(text,text,text,text,text,jsonb,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)', 'EXECUTE')
    AND has_function_privilege('carr_writer', 'ops.f01_replay_outcome(text,text,text)', 'EXECUTE')
    -- The ordinary evidence principal IS the trusted producer identity, so it
    -- keeps the registration surface. Without this the approved registration rule
    -- would have nobody able to satisfy it, and "registration is automatic" would
    -- quietly become "registration never happens".
    AND has_function_privilege('carr_writer',
      'ops.f01_register_derivative_link(jsonb,text,text)', 'EXECUTE')
    AND has_function_privilege('carr_authority_joe', 'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE')
    AND has_function_privilege('carr_authority_joe', 'ops.f01_record_hold(jsonb,text,text,text)', 'EXECUTE')
    AND has_function_privilege('carr_reader', 'ops.f01_read(text,jsonb)', 'EXECUTE')
    -- The document producer keeps the six-argument writer, and the reader keeps
    -- both document-source reads. A read-only principal is not a producer, and
    -- section 8.6 proves the writer half of that; this is the positive half, so a
    -- grant loop that quietly granted NOTHING fails here too.
    AND has_function_privilege('carr_writer',
      'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE')
    AND NOT has_function_privilege('carr_reader',
      'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE')
    AND has_function_privilege('carr_reader',
      'ops.f01_document_version_source(text,integer)', 'EXECUTE')
    AND has_function_privilege('carr_reader',
      'ops.f01_document_source_history(text)', 'EXECUTE')
    AND has_table_privilege('carr_reader', 'ops.f01_document_source_provenance', 'SELECT'),
    'principal', 'each principal retains exactly the surface its job needs');
END;
$least_privilege$;

-- ===========================================================================
-- 4. register-record-source-authority-policy.
--
-- RUNS AS carr_authority_joe. The policy surface is humanOnly plus
-- authorityOnly, so it is exercised by the principal that genuinely satisfies
-- both, and 4.6 steps back out to prove the refusal for one that does not.
--
-- EVERY pg_temp HELPER IN THIS FILE IS DEFINED WHILE STILL THE OWNER, before the
-- section switches identity. Creating them under a switched authorization would
-- make each one owned by whichever principal happened to be current, and would
-- depend on that principal holding CREATE on the session temp schema — which is
-- a database-level TEMP privilege, not something this fixture should quietly
-- require of a runtime role.
-- ===========================================================================

-- The synthetic registries. These are TEST POLICY, shaped like a compiled
-- kernel preimage: one field entry and one retention class, naming nothing real.
CREATE FUNCTION pg_temp.f01_field_registry(p_version integer DEFAULT 1) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-field-authority-registry.v1',
    'registry_version', p_version,
    'tenant', 'carr-internal',
    'entries', jsonb_build_array(jsonb_build_object(
      'entity', 'deal',
      'field', 'commission_amount',
      'authoritative_home', 'salesforce',
      'owner_source', 'salesforce',
      'permitted_sources', jsonb_build_array(
        jsonb_build_object('source_system', 'neon_record_layer', 'direction', 'outbound'),
        jsonb_build_object('source_system', 'salesforce', 'direction', 'inbound')),
      'requires_account_identity', true,
      'requires_native_identity', true,
      'version_comparator', 'integer_sequence',
      'version_order', null,
      'conflict_behavior', 'reconcile',
      'human_resolver_class', 'deal_owner',
      'readback_required', true,
      'sensitivity_classes', jsonb_build_array('lease_economics'),
      'taint_class', 'corporate_source_of_record',
      'privacy_route', 'permitted')));
$$;

CREATE FUNCTION pg_temp.f01_retention_registry(p_version integer DEFAULT 1) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-retention-registry.v1',
    'registry_version', p_version,
    'tenant', 'carr-internal',
    'classes', jsonb_build_array(jsonb_build_object(
      'artifact_class', 'synthetic_test_lease',
      'authoritative_home', 'onedrive',
      'default_retention_days', 1,
      'governing_constraints', jsonb_build_array('synthetic_test_constraint'),
      'deletion_proof_required', true,
      'surviving_derivatives', jsonb_build_array('synthetic_test_abstract'))));
$$;

CREATE FUNCTION pg_temp.f01_policy_record(p_version integer, p_prior text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-policy.v1',
    'tenant', 'carr-internal',
    'registry_version', p_version,
    'field_registry', pg_temp.f01_field_registry(p_version),
    'field_registry_digest', ops.f01_digest_jsonb(pg_temp.f01_field_registry(p_version)),
    'retention_registry', pg_temp.f01_retention_registry(p_version),
    'retention_registry_digest', ops.f01_digest_jsonb(pg_temp.f01_retention_registry(p_version)),
    'domain_policy_digest', 'sha256:' || repeat('a', 64),
    'decision_subset_digest', 'sha256:' || repeat('b', 64),
    'prior_policy_digest', p_prior,
    'installed_by', pg_temp.f01_actor(),
    'installed_at', ops.f01_now_text());
$$;

-- The helpers exist; from here on this section IS Joe.
SET SESSION AUTHORIZATION carr_authority_joe;

-- 4.1 nothing is installed by the migration itself.
SELECT pg_temp.f01_assert(
  (SELECT count(*) FROM ops.f01_policy_version) = 0
  AND (SELECT count(*) FROM ops.f01_policy_current) = 0,
  'policy', 'the migration ships no policy row');
SELECT pg_temp.f01_assert(ops.f01_current_policy() IS NULL,
  'policy', 'no current policy before installation');

-- 4.2 an observation with no installed registry cannot be judged.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_apply_observation('accept','deal','commission_amount',
      null, null, null, null, null, null, null, 'syn-pg-noreg-0001',
      'sha256:' || repeat('1', 64))$$,
  'f01_no_installed_policy', 'policy', 'observation without a registry refuses');

-- 4.3 genesis install.
DO $install$
DECLARE
  v_record jsonb := pg_temp.f01_policy_record(1, null);
  v_result jsonb;
BEGIN
  v_result := ops.f01_install_policy(
    pg_temp.f01_envelope('stored_policy_version', v_record,
      '{"humanOnly":true,"authorityOnly":true,"installs_defaults":false}'::jsonb),
    null, 'syn-pg-policy-0001', 'sha256:' || repeat('2', 64));
  PERFORM pg_temp.f01_remember('policy_v1', v_result -> 'readback' ->> 'policy_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'installed', 'policy', 'genesis installed');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'actor_slug', pg_temp.f01_actor(),
    'policy', 'installed_by is the derived actor');
  PERFORM pg_temp.f01_assert((v_result ->> 'external_effects') = 'false',
    'policy', 'installation produces no external effect');
  PERFORM pg_temp.f01_assert_eq(v_result -> 'readback' ->> 'integrity',
    'recomputed_from_committed_row', 'policy', 'the readback is recomputed');
END;
$install$;

-- 4.4 an install claiming a prior that is not the stored current refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_install_policy(
      pg_temp.f01_envelope('stored_policy_version', pg_temp.f01_policy_record(2, %L)),
      %L, 'syn-pg-policy-stale-0001', 'sha256:' || repeat('3', 64))$$,
    'sha256:' || repeat('c', 64), 'sha256:' || repeat('c', 64)),
  'f01_stale_policy_digest', 'policy', 'stale prior-current digest refuses');

-- 4.5 an install whose envelope lies about its own digest refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_install_policy(
      pg_temp.f01_envelope('stored_policy_version', pg_temp.f01_policy_record(2, %L))
        || jsonb_build_object('record_digest', 'sha256:' || repeat('d', 64)),
      %L, 'syn-pg-policy-forged-0001', 'sha256:' || repeat('4', 64))$$,
    pg_temp.f01_recall('policy_v1'), pg_temp.f01_recall('policy_v1')),
  'f01_policy_digest_mismatch', 'policy', 'a forged policy digest refuses');

-- 4.6 WRONG PRINCIPAL, with a payload that is otherwise PERFECTLY GOOD.
--
-- The two refusals this section used to make — f01_authority_requires_human_
-- principal and f01_authority_requires_verified_partner — do not exist in the
-- shipped schema and cannot exist in it. Humanness and authorization class are
-- derived from session_user, so they are not separately forgeable and there is
-- no separate refusal for forging them; both collapse into the single
-- f01_authority_principal_refused, raised on the identity itself.
--
-- To keep the test about the PRINCIPAL rather than about the payload, the exact
-- envelope that section 4.7 will successfully install is built here, while still
-- Joe, and then offered by a principal who is not Joe. Nothing about it is
-- malformed; the only thing wrong with it is who is holding it.
SELECT pg_temp.f01_remember('policy_v2_envelope',
  pg_temp.f01_envelope('stored_policy_version',
    pg_temp.f01_policy_record(2, pg_temp.f01_recall('policy_v1')))::text);

RESET SESSION AUTHORIZATION;

SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_install_policy(%L::jsonb, %L,
      'syn-pg-policy-nonauthority-0001', 'sha256:' || repeat('5', 64))$$,
    pg_temp.f01_recall('policy_v2_envelope'), pg_temp.f01_recall('policy_v1')),
  'f01_authority_principal_refused', 'policy',
  'a good policy offered by a non-authority principal refuses');

-- Nothing landed, and the current pointer did not move.
SELECT pg_temp.f01_assert(
  (SELECT count(*) FROM ops.f01_policy_version) = 1,
  'policy', 'a refused install writes no version');

SET SESSION AUTHORIZATION carr_authority_joe;

-- The grant layer refuses the same call one step earlier for carr_reader and
-- carr_writer, so neither ever reaches the check above. That is asserted without
-- depending on a server error message, in section 3.5.5.
SELECT pg_temp.f01_assert(
  NOT has_function_privilege('carr_writer',
    'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE')
  AND NOT has_function_privilege('carr_reader',
    'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE'),
  'policy', 'no ordinary runtime principal can reach the policy surface at all');

-- 4.7 a second version installs against the correct prior and moves the pointer.
DO $install2$
DECLARE
  v_prior text := pg_temp.f01_recall('policy_v1');
  v_result jsonb;
BEGIN
  v_result := ops.f01_install_policy(
    pg_temp.f01_envelope('stored_policy_version', pg_temp.f01_policy_record(2, v_prior)),
    v_prior, 'syn-pg-policy-0002', 'sha256:' || repeat('7', 64));
  PERFORM pg_temp.f01_remember('policy_v2', v_result -> 'readback' ->> 'policy_digest');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_policy_version) = 2,
    'policy', 'the previous version is retained, not replaced');
  PERFORM pg_temp.f01_assert_eq(ops.f01_current_policy_digest(),
    v_result -> 'readback' ->> 'policy_digest', 'policy', 'the current pointer advanced');
END;
$install2$;

-- 4.8 no actor may be supplied through the record.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_install_policy(
      pg_temp.f01_envelope('stored_policy_version',
        pg_temp.f01_policy_record(3, %L) || '{"installed_by":"somebody-else"}'::jsonb),
      %L, 'syn-pg-policy-actor-0001', 'sha256:' || repeat('8', 64))$$,
    pg_temp.f01_recall('policy_v2'), pg_temp.f01_recall('policy_v2')),
  'f01_actor_injection_refused', 'policy', 'installed_by cannot be supplied');

-- ===========================================================================
-- 5. record-source-observation — four relations, one atomic outcome.
--
-- RUNS AS carr_writer. Recording an observation is ordinary evidence work, and
-- carr_writer is the principal that does it: sponsored, not human, attributed by
-- carr.acting_actor_slug. Everything from here to section 8 is written by that
-- principal, which is also what makes section 9's authority refusal meaningful.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

CREATE FUNCTION pg_temp.f01_event_record(p_seq bigint, p_prev text, p_version integer,
                                         p_kind text DEFAULT 'source_field_observed')
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'record_kind', 'append_only_event',
    'schema_version', 'doctorcre-v5-f01-source-event.v1',
    'tenant', 'carr-internal',
    'entity', 'deal', 'field', 'commission_amount',
    'event_kind', p_kind,
    'event_seq', p_seq,
    'previous_event_digest', p_prev,
    'source_system', 'salesforce',
    'account', 'synthetic-account-0001',
    'native_identity', jsonb_build_object(
      'source_system', 'salesforce',
      'native_id', 'SYNTHETIC-NATIVE-0001',
      'native_id_epoch', 'synthetic-epoch-1'),
    'version', p_version,
    'observed_at', '2026-09-05T09:00:00Z',
    'provenance', jsonb_build_object(
      'adapter_kind', 'synthetic_test_adapter',
      'evidence_ref', 'synthetic-evidence-0001',
      'retrieval_class', 'corporate_record_export'),
    'taint_class', 'corporate_source_of_record',
    'append_only', true, 'rewrites_prior_event', false, 'alone_sufficient', false);
$$;

CREATE FUNCTION pg_temp.f01_transition_record(p_from text, p_to text,
                                              p_from_v integer, p_to_v integer)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'record_kind', 'current_state_transition',
    'schema_version', 'doctorcre-v5-f01-current-state-transition.v1',
    'tenant', 'carr-internal',
    'entity', 'deal', 'field', 'commission_amount',
    'authoritative_home', 'salesforce', 'owner_source', 'salesforce',
    'from_value_digest', p_from, 'to_value_digest', p_to,
    'from_version', p_from_v, 'to_version', p_to_v,
    'observed_at', '2026-09-05T09:00:00Z',
    'alone_sufficient', false);
$$;

CREATE FUNCTION pg_temp.f01_receipt_record(p_transition text, p_event text, p_reason text)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'record_kind', 'mutation_receipt',
    'schema_version', 'doctorcre-v5-f01-mutation-receipt.v1',
    'tenant', 'carr-internal',
    'entity', 'deal', 'field', 'commission_amount',
    'reason_id', p_reason,
    'current_state_transition_digest', p_transition,
    'event_digest', p_event,
    'registry_digest', 'sha256:' || repeat('e', 64),
    'domain_policy_digest', 'sha256:' || repeat('a', 64),
    'human_resolver_class', 'deal_owner',
    'actor', null, 'actor_derived_by', 'authenticated_handler_context',
    'alone_sufficient', false);
$$;

CREATE FUNCTION pg_temp.f01_state_record(p_value text, p_version integer,
                                         p_seq bigint, p_last_event text)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-field-state.v1',
    'tenant', 'carr-internal',
    'entity', 'deal', 'field', 'commission_amount',
    'account', 'synthetic-account-0001',
    'native_identity', jsonb_build_object(
      'source_system', 'salesforce',
      'native_id', 'SYNTHETIC-NATIVE-0001',
      'native_id_epoch', 'synthetic-epoch-1'),
    'value_digest', p_value, 'version', p_version,
    'owner_source', 'salesforce', 'authoritative_home', 'salesforce',
    'observed_at', '2026-09-05T09:00:00Z',
    'event_seq', p_seq, 'last_event_digest', p_last_event,
    'policy_digest', ops.f01_current_policy_digest(),
    'updated_by', pg_temp.f01_actor(),
    'updated_at', ops.f01_now_text());
$$;

/** One accepted observation, with every digest chained the way the store does. */
CREATE FUNCTION pg_temp.f01_accept(p_seq bigint, p_prev text, p_from text, p_to text,
                                   p_from_v integer, p_to_v integer, p_expected_state text,
                                   p_key text, p_kind text DEFAULT 'source_field_observed')
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  v_event jsonb := pg_temp.f01_event_record(p_seq, p_prev, p_to_v, p_kind);
  v_event_digest text := ops.f01_digest_jsonb(v_event);
  v_transition jsonb := pg_temp.f01_transition_record(p_from, p_to, p_from_v, p_to_v);
  v_transition_digest text := ops.f01_digest_jsonb(v_transition);
  v_receipt jsonb := pg_temp.f01_receipt_record(v_transition_digest, v_event_digest,
                                                'owner_value_updated');
  v_state jsonb := pg_temp.f01_state_record(p_to, p_to_v, p_seq, v_event_digest);
BEGIN
  RETURN ops.f01_apply_observation(
    'accept', 'deal', 'commission_amount',
    ops.f01_current_policy_digest(), p_expected_state,
    pg_temp.f01_envelope('stored_field_state', v_state),
    pg_temp.f01_envelope('stored_state_transition', v_transition, '{"alone_sufficient":false}'),
    pg_temp.f01_envelope('stored_source_event', v_event, '{"alone_sufficient":false}'),
    pg_temp.f01_envelope('stored_mutation_receipt', v_receipt,
      '{"alone_sufficient":false,"binds_transition_and_event":true}'),
    null, p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

-- From here on this section IS the sponsored writer, acting under a name of its
-- own that is plainly not an authority's.
SET SESSION AUTHORIZATION carr_writer;
SELECT set_config('carr.acting_actor_slug', 'synthetic-writer-agent', false);
SELECT pg_temp.f01_assert_eq(pg_temp.f01_actor(), 'synthetic-writer-agent',
  'observation', 'evidence is attributed to the acting sponsored agent');

-- 5.1 the field is established, and the three bound records land in three
-- different relations.
DO $establish$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_accept(1, null, null, 'sha256:' || repeat('01', 32),
                                 null, 5, null, 'syn-pg-obs-0001',
                                 'source_field_established');
  PERFORM pg_temp.f01_remember('state_v1', v_result -> 'readback' ->> 'state_digest');
  PERFORM pg_temp.f01_remember('event_1', v_result ->> 'event_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'accepted', 'observation', 'field established');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_field_state) = 1
    AND (SELECT count(*) FROM ops.f01_field_event) = 1
    AND (SELECT count(*) FROM ops.f01_state_transition) = 1
    AND (SELECT count(*) FROM ops.f01_mutation_receipt) = 1
    AND (SELECT count(*) FROM ops.f01_reconciliation_item) = 0,
    'observation', 'four relations, one row each, no reconciliation');
  -- The receipt binds both; neither of the other two binds the receipt.
  PERFORM pg_temp.f01_assert(
    (SELECT r.transition_digest = t.transition_digest AND r.event_digest = e.event_digest
       FROM ops.f01_mutation_receipt r, ops.f01_state_transition t, ops.f01_field_event e),
    'observation', 'the receipt binds the transition and the event');
  PERFORM pg_temp.f01_assert(
    (SELECT NOT (envelope -> 'record' ? 'mutation_receipt_digest') FROM ops.f01_state_transition)
    AND (SELECT NOT (envelope -> 'record' ? 'mutation_receipt_digest') FROM ops.f01_field_event),
    'observation', 'neither the transition nor the event names the receipt');
  PERFORM pg_temp.f01_assert(
    (SELECT count(DISTINCT d) FROM (
       SELECT transition_digest AS d FROM ops.f01_state_transition
       UNION ALL SELECT event_digest FROM ops.f01_field_event
       UNION ALL SELECT receipt_digest FROM ops.f01_mutation_receipt) s) = 3,
    'observation', 'no one record substitutes for another');
END;
$establish$;

-- 5.2 a second accepted observation extends the chain.
DO $advance$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_accept(2, pg_temp.f01_recall('event_1'),
    'sha256:' || repeat('01', 32), 'sha256:' || repeat('02', 32), 5, 6,
    pg_temp.f01_recall('state_v1'), 'syn-pg-obs-0002');
  PERFORM pg_temp.f01_remember('state_v2', v_result -> 'readback' ->> 'state_digest');
  PERFORM pg_temp.f01_remember('event_2', v_result ->> 'event_digest');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_field_event) = 2
    AND (SELECT count(*) FROM ops.f01_field_state) = 1,
    'observation', 'history grows while current state is replaced in place');
END;
$advance$;

-- 5.3 CAS: an observation decided against the OLD state refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_accept(3, %L, %L, %L, 6, 7, %L, 'syn-pg-obs-stale-0001')$$,
    pg_temp.f01_recall('event_2'), 'sha256:' || repeat('02', 32),
    'sha256:' || repeat('03', 32), pg_temp.f01_recall('state_v1')),
  'f01_stale_current_state', 'observation', 'a stale current-state digest refuses');

-- 5.4 the append-only chain cannot be forked or reordered.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_accept(2, %L, %L, %L, 6, 7, %L, 'syn-pg-obs-fork-0001')$$,
    pg_temp.f01_recall('event_2'), 'sha256:' || repeat('02', 32),
    'sha256:' || repeat('03', 32), pg_temp.f01_recall('state_v2')),
  'f01_event_sequence_out_of_order', 'observation', 'a repeated sequence refuses');
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_accept(3, %L, %L, %L, 6, 7, %L, 'syn-pg-obs-break-0001')$$,
    'sha256:' || repeat('f', 64), 'sha256:' || repeat('02', 32),
    'sha256:' || repeat('03', 32), pg_temp.f01_recall('state_v2')),
  'f01_event_chain_broken', 'observation', 'an event not extending the chain refuses');

-- 5.5 a transition starting from a value that is not stored refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_accept(3, %L, %L, %L, 6, 7, %L, 'syn-pg-obs-from-0001')$$,
    pg_temp.f01_recall('event_2'), 'sha256:' || repeat('09', 32),
    'sha256:' || repeat('03', 32), pg_temp.f01_recall('state_v2')),
  'f01_transition_from_mismatch', 'observation', 'a transition from an unstored value refuses');

-- 5.6 NO RECORD SUBSTITUTES FOR ANOTHER. An accepted change missing any of the
-- four refuses, and a conflict carrying mutation records refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_apply_observation('accept','deal','commission_amount',
      ops.f01_current_policy_digest(), %L, null, null, null, null, null,
      'syn-pg-obs-partial-0001', 'sha256:' || repeat('1', 64))$$,
    pg_temp.f01_recall('state_v2')),
  'f01_incomplete_mutation_set', 'observation', 'an accepted change writes all four or none');

SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_apply_observation('reconcile','deal','commission_amount',
      ops.f01_current_policy_digest(), %L,
      pg_temp.f01_envelope('stored_field_state',
        pg_temp.f01_state_record('sha256:' || repeat('04', 32), 8, 4,
                                 'sha256:' || repeat('0a', 32))),
      null, null, null, null, 'syn-pg-obs-mixed-0001', 'sha256:' || repeat('1', 64))$$,
    pg_temp.f01_recall('state_v2')),
  'f01_mutation_records_on_unaccepted_observation',
  'observation', 'a conflict cannot advance current state');

-- 5.7 a reconciliation outcome writes the visible item and nothing else.
DO $reconcile$
DECLARE
  v_item jsonb := jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-reconciliation-item.v1',
    'tenant', 'carr-internal',
    'entity', 'deal', 'field', 'commission_amount',
    'conflict_kind', 'equal_version_contradiction',
    'human_resolver_class', 'deal_owner',
    'authoritative_home', 'salesforce', 'owner_source', 'salesforce',
    'established', jsonb_build_object(
      'value_digest', 'sha256:' || repeat('02', 32), 'version', 6,
      'owner_source', 'salesforce', 'observed_at', '2026-09-05T09:00:00Z'),
    'observed', jsonb_build_object(
      'value_digest', 'sha256:' || repeat('05', 32), 'version', 6,
      'source_system', 'salesforce', 'account', 'synthetic-account-0001',
      'observed_at', '2026-09-05T09:00:00Z'),
    'applied', false, 'visible', true, 'resolved_by_machine', false);
  v_result jsonb;
  v_events bigint := (SELECT count(*) FROM ops.f01_field_event);
BEGIN
  v_result := ops.f01_apply_observation(
    'reconcile', 'deal', 'commission_amount',
    ops.f01_current_policy_digest(), pg_temp.f01_recall('state_v2'),
    null, null, null, null,
    pg_temp.f01_envelope('stored_reconciliation_item', v_item,
      '{"visible":true,"resolved_by_machine":false}'::jsonb),
    'syn-pg-obs-reconcile-0001', ops.f01_digest_jsonb('{"k":"reconcile"}'::jsonb));
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'reconcile',
    'observation', 'a conflict becomes a visible reconciliation item');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_reconciliation_item) = 1
    AND (SELECT count(*) FROM ops.f01_field_event) = v_events,
    'observation', 'reconciliation writes no event');
  PERFORM pg_temp.f01_assert_eq(ops.f01_current_field_state('deal', 'commission_amount')
    ->> 'state_digest', pg_temp.f01_recall('state_v2'),
    'observation', 'current state is untouched by a reconciliation');
END;
$reconcile$;

-- ===========================================================================
-- 6. Corporate artifacts.  (carr_writer)
-- ===========================================================================

RESET SESSION AUTHORIZATION;

CREATE FUNCTION pg_temp.f01_artifact_record(p_content text, p_native_version text,
                                            p_evidence_class text DEFAULT 'corporate_record_export',
                                            p_epoch text DEFAULT 'synthetic-epoch-1')
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-corporate-artifact.v1',
    'tenant', 'carr-internal',
    'source_system', 'salesforce',
    'source_class', 'synthetic_test_object',
    'source_account', 'synthetic-account-0001',
    'native_identity', jsonb_build_object(
      'source_system', 'salesforce',
      'native_id', 'SYNTHETIC-ARTIFACT-0001',
      'native_id_epoch', p_epoch),
    'native_version', p_native_version,
    'content_digest', p_content,
    'byte_length', 1024,
    'observed_at', '2026-09-05T09:00:00Z',
    'provenance', jsonb_build_object(
      'adapter_kind', 'synthetic_test_adapter',
      'evidence_ref', 'synthetic-evidence-0020',
      'retrieval_class', 'corporate_record_export'),
    'evidence_class', p_evidence_class,
    'declared_data_classes', jsonb_build_array('lease_economics'),
    'taint_class', 'corporate_source_of_record');
$$;

SET SESSION AUTHORIZATION carr_writer;

DO $artifact$
DECLARE
  v_result jsonb;
BEGIN
  v_result := ops.f01_record_artifact(
    pg_temp.f01_envelope('stored_corporate_artifact',
      pg_temp.f01_artifact_record('sha256:' || repeat('11', 32), 'synthetic-version-1'),
      '{"is_fact":false,"makes_field_authoritative":false,"immutable":true}'::jsonb),
    'syn-pg-artifact-0001', ops.f01_digest_jsonb('{"k":"artifact-1"}'::jsonb));
  PERFORM pg_temp.f01_remember('artifact_1', v_result ->> 'artifact_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'recorded', 'artifact', 'artifact recorded');
  PERFORM pg_temp.f01_assert((v_result ->> 'is_fact') = 'false',
    'artifact', 'an artifact is evidence, never a fact');
  PERFORM pg_temp.f01_assert_eq(v_result -> 'readback' ->> 'integrity',
    'recomputed_from_committed_row', 'artifact', 'the readback is recomputed');
END;
$artifact$;

-- 6.1 IMMUTABLE: the same identity may never name different bytes.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_artifact(
      pg_temp.f01_envelope('stored_corporate_artifact',
        pg_temp.f01_artifact_record('sha256:' || repeat('12', 32), 'synthetic-version-1')),
      'syn-pg-artifact-conflict-0001', ops.f01_digest_jsonb('{"k":"artifact-2"}'::jsonb))$$,
  'f01_artifact_identity_conflict', 'artifact', 'a reused identity with new bytes refuses');

-- 6.2 A RECYCLED native id under a new epoch is a DIFFERENT artifact, not a
-- conflict, so it lands beside the first rather than replacing it.
DO $recycled$
DECLARE
  v_result jsonb;
BEGIN
  v_result := ops.f01_record_artifact(
    pg_temp.f01_envelope('stored_corporate_artifact',
      pg_temp.f01_artifact_record('sha256:' || repeat('13', 32), 'synthetic-version-1',
                                  'corporate_record_export', 'synthetic-epoch-2')),
    'syn-pg-artifact-epoch-0001', ops.f01_digest_jsonb('{"k":"artifact-3"}'::jsonb));
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_corporate_artifact) = 2,
    'artifact', 'a new epoch is a new artifact, never a rewrite');
END;
$recycled$;

-- 6.3 Tour-only evidence is refused structurally, by name.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_artifact(
      pg_temp.f01_envelope('stored_corporate_artifact',
        pg_temp.f01_artifact_record('sha256:' || repeat('14', 32), 'synthetic-version-9',
                                    'tour_rights_receipt')),
      'syn-pg-artifact-tour-0001', ops.f01_digest_jsonb('{"k":"artifact-4"}'::jsonb))$$,
  'f01_artifact_not_tour_only', 'artifact', 'Tour-only evidence cannot become a corporate artifact');

-- ===========================================================================
-- 7. Parsed proposals and reversible links.  (carr_writer)
-- ===========================================================================

RESET SESSION AUTHORIZATION;

CREATE FUNCTION pg_temp.f01_link_record(p_artifact text, p_supersedes text,
                                        p_confidence numeric DEFAULT 0.75)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-proposal-link.v1',
    'tenant', 'carr-internal',
    'artifact_digest', p_artifact,
    'source_system', 'salesforce',
    'source_account', 'synthetic-account-0001',
    'proposed_bindings', jsonb_build_array(jsonb_build_object(
      'entity', 'deal', 'field', 'commission_amount',
      'value_digest', 'sha256:' || repeat('21', 32), 'version', 7,
      'human_resolver_class', 'deal_owner')),
    'confidence', p_confidence,
    'evidence_refs', jsonb_build_array('synthetic-evidence-0030'),
    'observed_at', '2026-09-05T09:00:00Z',
    'supersedes_link_digest', p_supersedes,
    'reversible', true, 'history_preserved', true,
    'registry_digest', 'sha256:' || repeat('e', 64));
$$;

/**
 * One registered derivative-source link, shaped exactly as the Node store shapes
 * one. `registered_by` is the DERIVED actor: ops.f01_insert_derivative_link
 * refuses a record naming anybody else, and 7.6 proves it.
 */
CREATE FUNCTION pg_temp.f01_derivative_link_record(
  p_source text, p_kind text, p_id text, p_content text,
  p_workflow text, p_run text, p_evidence_ref text, p_evidence_digest text,
  p_registered_by text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-derivative-source-link.v1',
    'derivative_link_schema_version', 'doctorcre-v5-f01-derivative-source-link.v1',
    'tenant', 'carr-internal',
    'source_artifact_digest', p_source,
    'derivative_kind', p_kind,
    'derivative_id', p_id,
    'derivative_content_digest', p_content,
    'producer_workflow', p_workflow,
    'producer_run_ref', p_run,
    'produced_at', ops.f01_now_text(),
    'evidence_ref', p_evidence_ref,
    'evidence_digest', p_evidence_digest,
    'registration_is_provenance', true,
    'is_exhaustive_inventory', false,
    'establishes_coverage', false,
    'permits_deletion', false,
    'registered_by', coalesce(p_registered_by, pg_temp.f01_actor()),
    'registered_at', ops.f01_now_text());
$$;

CREATE FUNCTION pg_temp.f01_derivative_envelope(p_record jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT pg_temp.f01_envelope('stored_derivative_link', p_record,
    '{"establishes_coverage":false,"is_exhaustive_inventory":false,'
    '"permits_deletion":false,"deletes_nothing":true}'::jsonb);
$$;

CREATE FUNCTION pg_temp.f01_record_proposal_pair(p_supersedes text, p_key text,
                                                 p_confidence numeric DEFAULT 0.75)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  v_link jsonb := pg_temp.f01_link_record(pg_temp.f01_recall('artifact_1'), p_supersedes,
                                          p_confidence);
  v_proposal jsonb := jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-parsed-proposal.v1') || (v_link - 'schema_version');
  v_notfact jsonb := '{"becomes_fact":false,"advances_state":false,'
                     '"carries_effect_authority":false,"requires_human_review":true}'::jsonb;
  v_proposal_envelope jsonb := pg_temp.f01_envelope('stored_parsed_proposal', v_proposal,
                                                    v_notfact);
  v_proposal_digest text := v_proposal_envelope ->> 'record_digest';
BEGIN
  -- THE THIRD ENVELOPE IS NOT OPTIONAL. A parsed proposal is a record derived
  -- from a stored artifact, so the producing workflow registers which original
  -- produced it in the same call. The store builds this envelope itself from
  -- values it already holds; nothing here is a caller decision, and 7.4 proves
  -- the write refuses without it.
  RETURN ops.f01_record_proposal(
    v_proposal_envelope,
    pg_temp.f01_envelope('stored_proposal_link', v_link,
      v_notfact || '{"reversible":true,"history_preserved":true}'::jsonb),
    pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
      pg_temp.f01_recall('artifact_1'), 'f01_parsed_proposal',
      v_proposal_digest, v_proposal_digest,
      'f01_record_parsed_proposal', p_key,
      'stored_parsed_proposal', v_proposal_digest)),
    p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_writer;

DO $proposal$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_record_proposal_pair(null, 'syn-pg-proposal-0001');
  PERFORM pg_temp.f01_remember('link_1', v_result ->> 'link_digest');
  PERFORM pg_temp.f01_assert((v_result ->> 'becomes_fact') = 'false'
    AND (v_result ->> 'advances_state') = 'false'
    AND (v_result ->> 'carries_effect_authority') = 'false'
    AND (v_result ->> 'requires_human_review') = 'true',
    'proposal', 'a proposal stays reviewable and never becomes a fact');
  PERFORM pg_temp.f01_assert(
    (SELECT proposal_digest <> link_digest FROM ops.f01_proposal_link),
    'proposal', 'the proposal and its link are two records');
  -- A PROPOSAL WROTE NO FACT. Current state is untouched by recording one.
  PERFORM pg_temp.f01_assert_eq(ops.f01_current_field_state('deal', 'commission_amount')
    ->> 'state_digest', pg_temp.f01_recall('state_v2'),
    'proposal', 'recording a proposal advances no current state');
END;
$proposal$;

-- 7.1 superseding NAMES the earlier link and preserves it.
DO $supersede$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_record_proposal_pair(pg_temp.f01_recall('link_1'),
                                               'syn-pg-proposal-0002', 0.9);
  PERFORM pg_temp.f01_remember('link_2', v_result ->> 'link_digest');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_proposal_link) = 2,
    'proposal', 'the superseded link is preserved, not erased');
END;
$supersede$;

-- 7.2 two links may not supersede the same link: history is a chain, not a fork.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_record_proposal_pair(%L, 'syn-pg-proposal-fork-0001', 0.5)$$,
    pg_temp.f01_recall('link_1')),
  'f01_proposal_link_supersedes_uq', 'proposal', 'a forked supersede chain refuses');

-- 7.3 a proposal naming an artifact nobody stored refuses. Its derivative
-- registration is well formed and names the same absent artifact, so the
-- refusal is about the artifact and not about the third envelope.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_proposal(
      pg_temp.f01_envelope('stored_parsed_proposal',
        jsonb_build_object('schema_version','doctorcre-v5-f01-parsed-proposal.v1')
          || (pg_temp.f01_link_record('sha256:' || repeat('cc', 32), null) - 'schema_version'),
        '{"becomes_fact":false,"advances_state":false,'
        '"carries_effect_authority":false,"requires_human_review":true}'::jsonb),
      pg_temp.f01_envelope('stored_proposal_link',
        pg_temp.f01_link_record('sha256:' || repeat('cc', 32), null),
        '{"becomes_fact":false,"advances_state":false,"carries_effect_authority":false,'
        '"requires_human_review":true,"reversible":true,"history_preserved":true}'::jsonb),
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        'sha256:' || repeat('cc', 32), 'f01_parsed_proposal',
        'sha256:' || repeat('ca', 32), 'sha256:' || repeat('ca', 32),
        'f01_record_parsed_proposal', 'syn-pg-proposal-unknown-0001',
        'stored_parsed_proposal', 'sha256:' || repeat('ca', 32))),
      'syn-pg-proposal-unknown-0001', ops.f01_digest_jsonb('{"k":"pu"}'::jsonb))$$,
  'f01_unknown_artifact', 'proposal', 'a proposal cannot assert an artifact into existence');

-- ===========================================================================
-- 7.4 THE PRODUCER RULE: a derived record does not complete without its source
--     registration.  (carr_writer)
--
-- This is the APPROVED REGISTRATION RULE made structural — the session approval
-- domain.sql section 5.3.1 names, not Q129.D1, which settles the retention
-- registry and nothing about provenance registration. A parsed proposal is
-- derived from a stored artifact, so ops.f01_record_proposal REQUIRES the
-- registration envelope and writes both records in one transaction or neither. A
-- caller cannot omit it, and cannot satisfy it with a link about something else.
-- ===========================================================================

DO $proposal_producer_binding$
DECLARE
  v_link jsonb := pg_temp.f01_link_record(pg_temp.f01_recall('artifact_1'), null, 0.6);
  v_proposal jsonb := jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-parsed-proposal.v1') || (v_link - 'schema_version');
  v_notfact jsonb := '{"becomes_fact":false,"advances_state":false,'
                     '"carries_effect_authority":false,"requires_human_review":true}'::jsonb;
  v_proposal_envelope jsonb := pg_temp.f01_envelope('stored_parsed_proposal', v_proposal,
                                                    v_notfact);
  v_link_envelope jsonb := pg_temp.f01_envelope('stored_proposal_link', v_link,
    v_notfact || '{"reversible":true,"history_preserved":true}'::jsonb);
  v_digest text := v_proposal_envelope ->> 'record_digest';
  v_proposals bigint := (SELECT count(*) FROM ops.f01_parsed_proposal);
  v_links bigint := (SELECT count(*) FROM ops.f01_derivative_link);
BEGIN
  -- No registration at all.
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_proposal(%L::jsonb, %L::jsonb, null,
        'syn-pg-proposal-noderiv-0001', ops.f01_digest_jsonb('{"k":"nd"}'::jsonb))$$,
      v_proposal_envelope::text, v_link_envelope::text),
    'f01_derivative_link_required', 'derivative',
    'a parsed proposal cannot be recorded without registering its source');

  -- A registration about a DIFFERENT derivative. "Some link was supplied" is not
  -- the rule; the rule is that THIS derived record names THIS original.
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_proposal(%L::jsonb, %L::jsonb, %L::jsonb,
        'syn-pg-proposal-wrongderiv-0001', ops.f01_digest_jsonb('{"k":"wd"}'::jsonb))$$,
      v_proposal_envelope::text, v_link_envelope::text,
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        pg_temp.f01_recall('artifact_1'), 'f01_parsed_proposal',
        'sha256:' || repeat('7a', 32), 'sha256:' || repeat('7a', 32),
        'f01_record_parsed_proposal', 'syn-pg-proposal-wrongderiv-0001',
        'stored_parsed_proposal', 'sha256:' || repeat('7a', 32)))::text),
    'f01_derivative_link_not_bound_to_proposal', 'derivative',
    'a registration about another derivative does not satisfy this one');

  -- A registration naming a DIFFERENT source artifact is refused on the same
  -- clause: provenance that points somewhere else is not this record's.
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_proposal(%L::jsonb, %L::jsonb, %L::jsonb,
        'syn-pg-proposal-wrongsource-0001', ops.f01_digest_jsonb('{"k":"ws"}'::jsonb))$$,
      v_proposal_envelope::text, v_link_envelope::text,
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        'sha256:' || repeat('cc', 32), 'f01_parsed_proposal', v_digest, v_digest,
        'f01_record_parsed_proposal', 'syn-pg-proposal-wrongsource-0001',
        'stored_parsed_proposal', v_digest))::text),
    'f01_derivative_link_not_bound_to_proposal', 'derivative',
    'a registration naming another source does not satisfy this one');

  -- NOTHING PARTIAL LANDED. Three refusals, no proposal, no link.
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_parsed_proposal) = v_proposals
    AND (SELECT count(*) FROM ops.f01_derivative_link) = v_links,
    'derivative', 'a refused producer binding writes neither the derivative nor its link');
END;
$proposal_producer_binding$;

-- 7.5 The successful path already ran in section 7: assert what it registered.
DO $proposal_registered_provenance$
DECLARE
  v_row ops.f01_derivative_link%ROWTYPE;
  v_coverage jsonb;
BEGIN
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_derivative_link
      WHERE source_artifact_digest = pg_temp.f01_recall('artifact_1')
        AND producer_workflow = 'f01_record_parsed_proposal') = 2,
    'derivative', 'both recorded proposals registered their source automatically');

  SELECT * INTO v_row FROM ops.f01_derivative_link
   WHERE derivative_id = (SELECT proposal_digest FROM ops.f01_parsed_proposal
                           ORDER BY proposal_id LIMIT 1);
  PERFORM pg_temp.f01_assert(FOUND, 'derivative',
    'the registration names the proposal digest as the derivative identity');
  PERFORM pg_temp.f01_assert_eq(v_row.source_artifact_digest,
    pg_temp.f01_recall('artifact_1'),
    'derivative', 'the registration names the artifact the proposal was parsed from');
  PERFORM pg_temp.f01_assert_eq(v_row.actor_slug, pg_temp.f01_actor(),
    'derivative', 'the producer principal is the derived actor, not a supplied one');
  PERFORM pg_temp.f01_assert(
    (v_row.envelope -> 'record' ->> 'registration_is_provenance') = 'true'
    AND (v_row.envelope -> 'record' ->> 'is_exhaustive_inventory') = 'false'
    AND (v_row.envelope -> 'record' ->> 'establishes_coverage') = 'false'
    AND (v_row.envelope -> 'record' ->> 'permits_deletion') = 'false',
    'derivative', 'a stored link says in its own hashed bytes what it is not');

  -- AND REGISTERING DID NOT MAKE COVERAGE KNOWN. This is the assertion the whole
  -- section exists for: rows appeared, and the answer to "are these all of
  -- them?" is still no better than it was.
  v_coverage := ops.f01_derivative_coverage(pg_temp.f01_recall('artifact_1'));
  PERFORM pg_temp.f01_assert_eq(v_coverage ->> 'state', 'unknown',
    'derivative', 'registered links do not establish coverage');
  PERFORM pg_temp.f01_assert_eq(v_coverage ->> 'reason_id', 'producer_closure_not_established',
    'derivative', 'the coverage answer names what is missing rather than shrugging');
  PERFORM pg_temp.f01_assert((v_coverage ->> 'registered_link_count')::int = 2
    AND (v_coverage ->> 'is_exhaustive_inventory') = 'false'
    AND (v_coverage ->> 'empty_link_set_means_verified_absence') = 'false',
    'derivative', 'the observed links are reported, and reported as not exhaustive');
  PERFORM pg_temp.f01_assert(
    ops.f01_stored_derivatives(pg_temp.f01_recall('artifact_1')) IS NULL,
    'derivative', 'the deletion inventory stays UNKNOWN while coverage is unknown');
END;
$proposal_registered_provenance$;

-- ===========================================================================
-- 7.6 register-derivative-source-link, on its own.  (carr_writer)
--
-- The public surface a future producer workflow uses. Everything below is about
-- what one registration may and may not say.
-- ===========================================================================

-- Back to the applying identity to define the helper: a pg_temp function created
-- under a switched authorization would be owned by whichever principal happened
-- to be current and would need CREATE on the session temp schema.
RESET SESSION AUTHORIZATION;

/**
 * One registration through the public surface.
 *
 * p_run DEFAULTS TO THE IDEMPOTENCY KEY, which is the ordinary case, but it is a
 * separate parameter because the two are separate facts and 7.6.1 needs to move
 * one without the other. producer_run_ref is part of the provenance claim — WHICH
 * RUN made this derivative — while the idempotency key is about this request.
 * A real producer supplies them independently.
 */
CREATE FUNCTION pg_temp.f01_register_derivative(p_kind text, p_id text, p_content text,
                                                p_key text, p_source text DEFAULT NULL,
                                                p_registered_by text DEFAULT NULL,
                                                p_run text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  RETURN ops.f01_register_derivative_link(
    pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
      coalesce(p_source, pg_temp.f01_recall('artifact_1')), p_kind, p_id, p_content,
      'synthetic_test_producer', coalesce(p_run, p_key),
      'synthetic-evidence-0040', 'sha256:' || repeat('61', 32), p_registered_by)),
    p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_writer;

DO $register_derivative$
DECLARE
  v_result jsonb;
  v_artifacts bigint := (SELECT count(*) FROM ops.f01_corporate_artifact);
BEGIN
  v_result := pg_temp.f01_register_derivative(
    'synthetic_test_abstract', 'synthetic-abstract-0001',
    'sha256:' || repeat('62', 32), 'syn-pg-deriv-0001');
  PERFORM pg_temp.f01_remember('derivative_1', v_result ->> 'link_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'registered',
    'derivative', 'a trusted producer registers one derivative against its source');
  PERFORM pg_temp.f01_assert((v_result ->> 'establishes_coverage') = 'false'
    AND (v_result ->> 'is_exhaustive_inventory') = 'false'
    AND (v_result ->> 'permits_deletion') = 'false'
    AND (v_result ->> 'external_effects') = 'false',
    'derivative', 'the answer says registration establishes nothing and permits nothing');
  PERFORM pg_temp.f01_assert_eq(v_result -> 'coverage' ->> 'state', 'unknown',
    'derivative', 'the coverage readback beside the answer is the proof, not the promise');
  PERFORM pg_temp.f01_assert_eq(v_result -> 'readback' ->> 'integrity',
    'recomputed_from_committed_row', 'derivative', 'the readback is recomputed');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_corporate_artifact) = v_artifacts,
    'derivative', 'registering provenance changes no artifact');

  -- IDEMPOTENT: the same registration replays to the same stored answer.
  v_result := pg_temp.f01_register_derivative(
    'synthetic_test_abstract', 'synthetic-abstract-0001',
    'sha256:' || repeat('62', 32), 'syn-pg-deriv-0001');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'link_digest',
    pg_temp.f01_recall('derivative_1'),
    'derivative', 'an exact replay returns the stored registration');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_derivative_link
      WHERE derivative_id = 'synthetic-abstract-0001') = 1,
    'derivative', 'a replay writes no second link');
END;
$register_derivative$;

-- 7.6.1 ONE DERIVATIVE, ONE ORIGINAL. Re-registering the same derivative
-- against a different source is a rewrite of where a record came from.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0001', 'sha256:' || repeat('62', 32),
      'syn-pg-deriv-repoint-0001', %L)$$,
    (SELECT artifact_digest FROM ops.f01_corporate_artifact
      WHERE artifact_digest <> pg_temp.f01_recall('artifact_1') LIMIT 1)),
  'f01_derivative_source_conflict', 'derivative',
  'a derivative may not be repointed at a second original');

-- ...and the same identity claiming different bytes is refused on the same rule.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0001', 'sha256:' || repeat('63', 32),
      'syn-pg-deriv-rebyte-0001')$$,
  'f01_derivative_source_conflict', 'derivative',
  'one derivative identity may not name two sets of bytes');

-- 7.6.1.1 AND SO IS A SECOND, CONTRARY PROVENANCE CLAIM ABOUT THE SAME BYTES.
-- Source and content agree here; the PRODUCER RUN does not. An earlier revision
-- compared only source and bytes and answered this with success, handing back the
-- FIRST producer's link digest — so a second run's claim about where a derivative
-- came from was reported as agreement with a claim it contradicted.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0001', 'sha256:' || repeat('62', 32),
      'syn-pg-deriv-rerun-0001', null, null, 'synthetic-run-9999')$$,
  'f01_derivative_source_conflict', 'derivative',
  'a second registration naming a different producer run refuses');

-- ...and the other two IMMUTABLE halves of the same comparison, one case each,
-- because "the five fields are compared" is a claim about five fields and an
-- assertion about one of them proves nothing about the rest. A different
-- producing WORKFLOW is a different claim about who made the derivative; a
-- different EVIDENCE DIGEST is a different claim about what the production left
-- behind. Both are contrary provenance, and both must refuse rather than be
-- answered with the first producer's link digest.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
        'synthetic-abstract-0001', 'sha256:' || repeat('62', 32),
        'synthetic_other_producer', 'syn-pg-deriv-0001',
        'synthetic-evidence-0040', 'sha256:' || repeat('61', 32))),
      'syn-pg-deriv-reworkflow-0001', ops.f01_digest_jsonb('{"k":"drw"}'::jsonb))$$,
  'f01_derivative_source_conflict', 'derivative',
  'a second registration naming a different producer workflow refuses');

SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
        'synthetic-abstract-0001', 'sha256:' || repeat('62', 32),
        'synthetic_test_producer', 'syn-pg-deriv-0001',
        'synthetic-evidence-0040', 'sha256:' || repeat('6c', 32))),
      'syn-pg-deriv-reevidence-0001', ops.f01_digest_jsonb('{"k":"dre"}'::jsonb))$$,
  'f01_derivative_source_conflict', 'derivative',
  'a second registration naming different immutable evidence refuses');

-- AND THE FIELD THAT IS DELIBERATELY NOT COMPARED STILL IS NOT. `evidence_ref`
-- is an external LABEL whose immutable half — evidence_digest — is compared
-- instead, so the same fact arriving under a re-labelled reference is a no-op
-- rather than a refusal. Asserting this is what stops the two cases above from
-- being read as "any difference refuses", which would make honest repeats
-- unrecordable.
DO $register_derivative_relabelled_evidence$
DECLARE
  v_result jsonb;
BEGIN
  v_result := ops.f01_register_derivative_link(
    pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
      pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
      'synthetic-abstract-0001', 'sha256:' || repeat('62', 32),
      'synthetic_test_producer', 'syn-pg-deriv-0001',
      'synthetic-evidence-0040-relabelled', 'sha256:' || repeat('61', 32))),
    'syn-pg-deriv-relabel-0001', ops.f01_digest_jsonb('{"k":"drl"}'::jsonb));
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'already_registered',
    'derivative', 'a re-labelled evidence reference is the same fact, not a new claim');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'link_digest',
    pg_temp.f01_recall('derivative_1'),
    'derivative', 'and it returns the original link rather than a second one');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_derivative_link
      WHERE derivative_id = 'synthetic-abstract-0001') = 1,
    'derivative', 'no second row was written for the same derivative identity');
END;
$register_derivative_relabelled_evidence$;

-- ...AND THE POSITIVE HALF, which is what stops the rule above from being a
-- blanket refusal of every repeat. A genuinely identical claim — same source,
-- same bytes, same workflow, same run, same evidence — arriving under a NEW
-- idempotency key is the same fact twice, and is a no-op that returns the
-- original link rather than a second row.
DO $register_derivative_same_fact_twice$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_register_derivative(
    'synthetic_test_abstract', 'synthetic-abstract-0001',
    'sha256:' || repeat('62', 32), 'syn-pg-deriv-samefact-0001', null, null,
    'syn-pg-deriv-0001');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'already_registered',
    'derivative', 'an identical claim under a new key is the same fact arriving twice');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'link_digest',
    pg_temp.f01_recall('derivative_1'),
    'derivative', 'and it returns the original link rather than a new one');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_derivative_link
      WHERE derivative_id = 'synthetic-abstract-0001') = 1,
    'derivative', 'the same fact twice writes no second row');
END;
$register_derivative_same_fact_twice$;

-- 7.6.2 The producer principal is DERIVED. A record naming somebody else is
-- refused before the row is written, exactly as installed_by is for a policy.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0002', 'sha256:' || repeat('64', 32),
      'syn-pg-deriv-actor-0001', null, 'somebody-else')$$,
  'f01_actor_injection_refused', 'derivative',
  'registered_by cannot be supplied');

-- 7.6.3 Provenance must point at something. A link to an artifact nobody stored
-- refuses, and naming a digest never brings one into existence.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0003', 'sha256:' || repeat('65', 32),
      'syn-pg-deriv-unknown-0001', 'sha256:' || repeat('ce', 32))$$,
  'f01_unknown_artifact', 'derivative',
  'a derivative link must name a stored artifact');

-- 7.6.4 A DERIVATIVE MAY NOT BE ITS OWN SOURCE, and there are TWO claims that
-- shape can take. They are asserted separately because they are refused by two
-- different mechanisms, and because conflating them is exactly the mistake that
-- left the real one unenforced.
--
--   THE RECORD IDENTITY. A link whose derivative_content_digest is the artifact's
--   RECORD digest claims its bytes are the stored artifact row's canonical bytes.
--   The structural constraint f01_derivative_not_self refuses that, whatever the
--   writer does.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0004', %L, 'syn-pg-deriv-self-0001')$$,
    pg_temp.f01_recall('artifact_1')),
  'f01_derivative_not_self', 'derivative',
  'a record cannot be registered as claiming the artifact record''s own bytes');

--   THE BYTES. A link whose derivative_content_digest is the artifact's CONTENT
--   digest is a verbatim copy of the source registered as something derived from
--   it. The constraint above never catches this — a record digest is taken over
--   source system, account, native identity, provenance and observed instant as
--   well as the content, so a copy's content digest and the artifact's record
--   digest are never equal — and a CHECK cannot reach the other row to compare
--   them. The private inserter loads the source it has already authenticated and
--   refuses by name.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('synthetic_test_abstract',
      'synthetic-abstract-0012', 'sha256:' || repeat('11', 32),
      'syn-pg-deriv-selfbytes-0001')$$,
  'f01_derivative_is_its_own_source', 'derivative',
  'a byte-identical copy of the source is not a derivative of it');

-- ...and the two are genuinely different values, so neither assertion above is
-- passing for the other one's reason.
SELECT pg_temp.f01_assert(
  (SELECT content_digest = 'sha256:' || repeat('11', 32)
      AND artifact_digest <> content_digest
     FROM ops.f01_corporate_artifact
    WHERE artifact_digest = pg_temp.f01_recall('artifact_1')),
  'derivative',
  'the artifact''s record identity and its content digest are different values');

-- AND DISTINCT DERIVED BYTES ARE STILL REGISTRABLE, which is what stops the rule
-- above from being a refusal of every registration against this artifact.
DO $register_derivative_distinct_bytes$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_register_derivative(
    'synthetic_test_abstract', 'synthetic-abstract-0013',
    'sha256:' || repeat('73', 32), 'syn-pg-deriv-distinct-0001');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'registered',
    'derivative', 'bytes that are not the source''s bytes register normally');
END;
$register_derivative_distinct_bytes$;

-- 7.6.5 A LINK MAY NOT CLAIM TO ESTABLISH COVERAGE. This is the forgery that
-- would matter: a stored row asserting the registry is complete would be a
-- caller-written permission to delete. The CHECK refuses it on the row.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_envelope('stored_derivative_link',
        pg_temp.f01_derivative_link_record(
          pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
          'synthetic-abstract-0005', 'sha256:' || repeat('66', 32),
          'synthetic_test_producer', 'syn-pg-deriv-forged-0001',
          'synthetic-evidence-0041', 'sha256:' || repeat('67', 32))
          || '{"establishes_coverage":true,"is_exhaustive_inventory":true,'
             '"permits_deletion":true}'::jsonb,
        '{"establishes_coverage":false,"is_exhaustive_inventory":false,'
        '"permits_deletion":false,"deletes_nothing":true}'::jsonb),
      'syn-pg-deriv-forged-0001', ops.f01_digest_jsonb('{"k":"df"}'::jsonb))$$,
  'f01_derivative_claims_nothing', 'derivative',
  'a link claiming to establish coverage cannot be stored at all');

-- ...and the same claim made on the ENVELOPE rather than inside the record is
-- refused by the same constraint, so neither half is a way round the other.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_envelope('stored_derivative_link',
        pg_temp.f01_derivative_link_record(
          pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
          'synthetic-abstract-0006', 'sha256:' || repeat('68', 32),
          'synthetic_test_producer', 'syn-pg-deriv-forged-0002',
          'synthetic-evidence-0042', 'sha256:' || repeat('69', 32)),
        '{"establishes_coverage":true,"is_exhaustive_inventory":false,'
        '"permits_deletion":false,"deletes_nothing":true}'::jsonb),
      'syn-pg-deriv-forged-0002', ops.f01_digest_jsonb('{"k":"df2"}'::jsonb))$$,
  'f01_derivative_claims_nothing', 'derivative',
  'an envelope claiming to establish coverage cannot be stored either');

-- 7.6.5.1 AND SILENCE IS REFUSED EXACTLY LIKE A CONTRARY CLAIM. This is the
-- half the constraint used to miss: `->>` over an ABSENT key is SQL NULL, and a
-- CHECK fails only on FALSE, so a record that simply OMITTED the four claims was
-- stored — a link carrying no self-limiting bytes at all, which is precisely
-- what a later reader would have to find in order to know what the row is not.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_derivative_envelope(
        pg_temp.f01_derivative_link_record(
          pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
          'synthetic-abstract-0010', 'sha256:' || repeat('6d', 32),
          'synthetic_test_producer', 'syn-pg-deriv-silent-0001',
          'synthetic-evidence-0044', 'sha256:' || repeat('6e', 32))
          - 'registration_is_provenance' - 'is_exhaustive_inventory'
          - 'establishes_coverage' - 'permits_deletion'),
      'syn-pg-deriv-silent-0001', ops.f01_digest_jsonb('{"k":"ds"}'::jsonb))$$,
  'f01_derivative_claims_nothing', 'derivative',
  'a link that says nothing about what it is not cannot be stored either');

-- ...and the same omission on the ENVELOPE. f01_envelope's default extras are
-- empty, so this builds the exact shape a writer would produce if somebody
-- deleted the three flags from it.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_envelope('stored_derivative_link',
        pg_temp.f01_derivative_link_record(
          pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
          'synthetic-abstract-0011', 'sha256:' || repeat('6f', 32),
          'synthetic_test_producer', 'syn-pg-deriv-silent-0002',
          'synthetic-evidence-0045', 'sha256:' || repeat('70', 32))),
      'syn-pg-deriv-silent-0002', ops.f01_digest_jsonb('{"k":"ds2"}'::jsonb))$$,
  'f01_derivative_claims_nothing', 'derivative',
  'an envelope that omits the three flags cannot be stored either');

-- Nothing partial landed from either omission.
SELECT pg_temp.f01_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_derivative_link
               WHERE derivative_id IN ('synthetic-abstract-0010', 'synthetic-abstract-0011')),
  'derivative', 'a refused claim-shape writes no row');

-- 7.6.5.2 A KIND THIS SCHEMA PRODUCES ITSELF IS NOT REGISTRABLE FROM OUTSIDE.
--
-- WHY THIS IS A REFUSAL AND NOT A TIDINESS RULE. A parsed proposal's derivative
-- identity IS the proposal digest, and that digest is computed from caller
-- payload plus the installed registry digest, so a caller can PREDICT it. The
-- identity index is unique on (tenant, kind, id) over an append-only table with
-- no release path, so one pre-registration pointed at a different artifact would
-- make the genuine ops.f01_record_proposal raise f01_derivative_source_conflict
-- for that proposal permanently, and would leave a stored provenance edge
-- claiming the proposal came from an artifact it did not.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('f01_parsed_proposal',
      'synthetic-proposal-squat-0001', 'sha256:' || repeat('71', 32),
      'syn-pg-deriv-reserved-0001')$$,
  'f01_reserved_derivative_kind', 'derivative',
  'the public surface refuses a kind produced by a writer inside this schema');

-- The refusal is about the KIND, not about that one literal: it is read from the
-- schema's own list, and the in-schema producer path that legitimately writes the
-- kind is untouched — section 7.5 already proved two such links exist.
SELECT pg_temp.f01_assert(
  'f01_parsed_proposal' = ANY (ops.f01_reserved_derivative_kinds())
  AND (SELECT count(*) FROM ops.f01_derivative_link
        WHERE derivative_kind = 'f01_parsed_proposal') = 2,
  'derivative',
  'reserving the kind closes the caller route without closing the producer route');

-- 7.6.5.3 THE SECOND RESERVED KIND, on exactly the same terms. A document
-- version's derivative identity is the (document_id, version_no) fold, which a
-- caller can predict outright; the identity index is unique per (tenant, kind,
-- id) over an append-only table with no release path, so one pre-registration
-- pointed at another artifact would make the genuine document write conflict for
-- that version for ever. The kind is written only by ops.f01_record_document, in
-- the transaction that completes the version, through the PRIVATE inserter —
-- which is why reserving it here closes the caller route without closing that
-- one.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_register_derivative('f01_document_version',
      'synthetic-document-squat-0001:1', 'sha256:' || repeat('72', 32),
      'syn-pg-deriv-reserved-0002')$$,
  'f01_reserved_derivative_kind', 'derivative',
  'the public surface refuses the document kind an in-schema writer produces');

SELECT pg_temp.f01_assert(
  'f01_document_version' = ANY (ops.f01_reserved_derivative_kinds())
  AND cardinality(ops.f01_reserved_derivative_kinds()) = 2,
  'derivative',
  'the reserved list is the two kinds in-schema writers produce, and mirrors the kernel');

-- 7.6.6 A forged link digest refuses, like every other record here.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_register_derivative_link(
      pg_temp.f01_derivative_envelope(pg_temp.f01_derivative_link_record(
        pg_temp.f01_recall('artifact_1'), 'synthetic_test_abstract',
        'synthetic-abstract-0007', 'sha256:' || repeat('6a', 32),
        'synthetic_test_producer', 'syn-pg-deriv-digest-0001',
        'synthetic-evidence-0043', 'sha256:' || repeat('6b', 32)))
        || jsonb_build_object('record_digest', 'sha256:' || repeat('d', 64)),
      'syn-pg-deriv-digest-0001', ops.f01_digest_jsonb('{"k":"dd"}'::jsonb))$$,
  'f01_derivative_link_digest_mismatch', 'derivative',
  'a link that lies about its own bytes refuses');

-- 7.6.7 AND A READ-ONLY PRINCIPAL IS NOT A PRODUCER, in the function body as
-- well as in the grant. The grant refuses first for carr_reader — 3.5.5 proves
-- that — so this exercises the body check through a principal that can reach it.
RESET SESSION AUTHORIZATION;

SELECT pg_temp.f01_assert(
  NOT has_function_privilege('carr_reader',
    'ops.f01_register_derivative_link(jsonb,text,text)', 'EXECUTE'),
  'derivative', 'the reader cannot reach the registration surface at all');

SET SESSION AUTHORIZATION carr_writer;

-- ===========================================================================
-- 8. Document identity — five axes, three homes, and one statement about where
--    each version came from.  (carr_writer)
--
-- EVERY WRITE HERE CARRIES A PROVENANCE STATEMENT, because the six-argument
-- ops.f01_record_document cannot be called without one. That is the settled
-- producer rule made structural for documents: a version completes with a
-- statement of its origin, or it does not complete. The versions below declare
-- original_first_party — they are synthetic documents authored by this fixture,
-- and saying anything else would be a fixture inventing provenance it does not
-- have. The DERIVED path, its link row and its repointing refusals belong to
-- mcp-server/test/document-derivative-registration-postgres.sql, which exercises
-- them against its own artifact; what this section adds is the half that must
-- hold for every document: the statement is mandatory, it is bound to THIS
-- version and its exact bytes, and a derived declaration without its link
-- refuses.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

CREATE FUNCTION pg_temp.f01_document_record(
  p_version integer, p_prior text, p_signature text, p_filing text, p_official text,
  p_content text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-document-version.v1',
    'document_identity_schema_version', 'doctorcre-v5-f01-document-identity.v1',
    'tenant', 'carr-internal',
    'document_class', 'synthetic_test_agreement',
    'neon_identity', jsonb_build_object(
      'document_id', 'synthetic-document-0001',
      'content_digest', coalesce(p_content, 'sha256:' || repeat('31', 32)),
      'version_no', p_version),
    'object_storage_identity', jsonb_build_object(
      'object_key', 'synthetic/test/object-0001',
      'content_digest', coalesce(p_content, 'sha256:' || repeat('31', 32)),
      'byte_length', 2048, 'sealed', true),
    'onedrive_identity', CASE WHEN p_filing IS NULL THEN NULL ELSE jsonb_build_object(
      'drive_id', 'synthetic-drive-0001', 'item_id', 'synthetic-item-0001',
      'content_digest', coalesce(p_content, 'sha256:' || repeat('31', 32)),
      'filing_state', p_filing) END,
    'preparation_state', 'approved_for_delivery',
    'delivery_state', 'delivered',
    'signature_state', p_signature,
    'validity_state', CASE WHEN p_signature = 'fully_executed' THEN 'effective' ELSE 'draft' END,
    'version_state', 'current',
    'official_filing_state', p_official,
    'prior_document_digest', p_prior,
    'homes', jsonb_build_object(
      'identity_and_state', 'neon_record_layer',
      'working_and_sealed_bytes', 'object_storage',
      'official_executed_copy', 'onedrive'),
    'recorded_by', pg_temp.f01_actor(),
    'recorded_at', ops.f01_now_text());
$$;

/**
 * One document version's provenance statement, shaped exactly as the Node
 * storedDocumentSourceProvenanceRecord() shapes one.
 *
 * The derived and non-derived halves are built by the SAME function on purpose:
 * the fields that must be null in one case and present in the other are visible
 * side by side, so a fixture filling in the wrong half is obvious rather than
 * subtle. `recorded_by` is the DERIVED actor —
 * ops.f01_insert_document_source_provenance refuses a record naming anybody else.
 */
CREATE FUNCTION pg_temp.f01_document_source_record(
  p_document_id text, p_version integer, p_document_digest text, p_state text,
  p_source text DEFAULT NULL, p_link_digest text DEFAULT NULL,
  p_workflow text DEFAULT NULL, p_run text DEFAULT NULL, p_basis text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-document-source-provenance.v1',
    'document_source_schema_version', 'doctorcre-v5-f01-document-source-provenance.v1',
    'document_identity_schema_version', 'doctorcre-v5-f01-document-identity.v1',
    'tenant', 'carr-internal',
    'document_id', p_document_id,
    'version_no', p_version,
    'document_digest', p_document_digest,
    'provenance_state', p_state,
    'source_artifact_digest', p_source,
    'derivative_link_schema_version',
      CASE WHEN p_link_digest IS NULL THEN NULL
           ELSE 'doctorcre-v5-f01-derivative-source-link.v1' END,
    'derivative_link_digest', p_link_digest,
    'derivative_kind',
      CASE WHEN p_state = 'derived_from_stored_artifact' THEN 'f01_document_version' END,
    'derivative_id',
      CASE WHEN p_state = 'derived_from_stored_artifact'
           THEN p_document_id || ':' || p_version::text END,
    'producer_workflow', p_workflow,
    'producer_run_ref', p_run,
    'basis_statement', p_basis,
    -- The six claims a statement makes about itself, hashed with it.
    'registration_is_provenance', true,
    'source_artifact_inferred_from_document_bytes', false,
    'source_artifact_inferred_from_onedrive_identity', false,
    'is_exhaustive_inventory', false,
    'establishes_coverage', false,
    'permits_deletion', false,
    'recorded_by', pg_temp.f01_actor(),
    'recorded_at', ops.f01_now_text());
$$;

CREATE FUNCTION pg_temp.f01_document_source_envelope(p_record jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT pg_temp.f01_envelope('stored_document_source_provenance', p_record,
    '{"establishes_coverage":false,"is_exhaustive_inventory":false,'
    '"permits_deletion":false,"deletes_nothing":true}'::jsonb);
$$;

/**
 * One document version and its statement, written the way the store writes them.
 *
 * THE DOCUMENT RECORD IS BUILT ONCE and its digest is what the statement names,
 * because the writer refuses a statement that does not name this version's exact
 * stored bytes. Building it twice would work today — ops.f01_now_text() is stable
 * within a transaction — and would break the moment anything about it stopped
 * being.
 */
CREATE FUNCTION pg_temp.f01_write_document(
  p_version integer, p_prior text, p_signature text, p_filing text, p_official text,
  p_key text,
  p_state text DEFAULT 'original_first_party',
  p_basis text DEFAULT 'synthetic test fixture: authored in this record layer',
  p_content text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  v_document jsonb := pg_temp.f01_document_record(p_version, p_prior, p_signature,
                                                  p_filing, p_official, p_content);
  v_digest text := ops.f01_digest_jsonb(v_document);
BEGIN
  RETURN ops.f01_record_document(
    pg_temp.f01_envelope('stored_document_version', v_document,
      '{"object_storage_success_implies_official_filing":false,'
      '"neon_success_implies_official_filing":false}'::jsonb),
    pg_temp.f01_document_source_envelope(
      pg_temp.f01_document_source_record('synthetic-document-0001', p_version, v_digest,
                                         p_state, NULL, NULL, NULL, NULL, p_basis)),
    NULL,
    p_prior, p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_writer;

DO $document$
DECLARE
  v_result jsonb;
BEGIN
  -- Version 1: unsigned draft, no official copy required yet.
  v_result := pg_temp.f01_write_document(1, null, 'unsigned', null, 'not_required',
                                         'syn-pg-document-0001');
  PERFORM pg_temp.f01_remember('document_v1', v_result ->> 'document_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'official_filing_state', 'not_required',
    'document', 'an unsigned draft needs no official copy');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'provenance_state', 'original_first_party',
    'document', 'the version completes with a statement about where it came from');
  PERFORM pg_temp.f01_assert((v_result ->> 'derivative_registration_bound') = 'false'
    AND (v_result ->> 'derivative_link_digest') IS NULL,
    'document', 'an original registers no derivative link, because it has no source');
  -- Said on every answer: a growing set of registered documents is not a picture
  -- of what was derived, and an absent statement is not an absent source.
  PERFORM pg_temp.f01_assert((v_result ->> 'establishes_coverage') = 'false'
    AND (v_result ->> 'is_exhaustive_inventory') = 'false'
    AND (v_result ->> 'permits_deletion') = 'false'
    AND (v_result ->> 'absent_statement_means_no_source') = 'false',
    'document', 'completing a document claims no coverage, no inventory and no deletion');

  -- Version 2: fully executed with NO filed copy — visibly incomplete, stored.
  v_result := pg_temp.f01_write_document(2, pg_temp.f01_recall('document_v1'),
    'fully_executed', 'pending', 'incomplete_official_filing', 'syn-pg-document-0002');
  PERFORM pg_temp.f01_remember('document_v2', v_result ->> 'document_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'official_filing_state',
    'incomplete_official_filing',
    'document', 'full execution without a filed copy is VISIBLY incomplete');

  -- Version 3: the official copy is filed, and only now is filing complete.
  v_result := pg_temp.f01_write_document(3, pg_temp.f01_recall('document_v2'),
    'fully_executed', 'filed', 'filed', 'syn-pg-document-0003');
  PERFORM pg_temp.f01_remember('document_v3', v_result ->> 'document_digest');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'official_filing_state', 'filed',
    'document', 'a filed OneDrive copy completes the official filing');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_document_version) = 3
    AND (SELECT count(*) FROM ops.f01_document_current) = 1,
    'document', 'every version is retained and exactly one is current');
  -- ONE STATEMENT PER VERSION, and no link row anywhere: three originals
  -- registered nothing, which is exactly what an original should register.
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_document_source_provenance
      WHERE document_id = 'synthetic-document-0001') = 3
    AND (SELECT count(*) FROM ops.f01_document_source_provenance
          WHERE document_id = 'synthetic-document-0001'
            AND derivative_link_digest IS NOT NULL) = 0,
    'document', 'each version carries exactly one statement and no original registered a link');
END;
$document$;

-- 8.0.1 THE STATEMENT READS BACK, and NULL means "no statement" rather than "no
-- source". This is the distinction the whole seam exists for: before this row
-- existed, a document with no link was indistinguishable from a document nobody
-- registered, one written before the rule, and one that genuinely has no
-- corporate original.
DO $document_source_readback$
DECLARE
  v_statement jsonb := ops.f01_document_version_source('synthetic-document-0001', 3);
BEGIN
  PERFORM pg_temp.f01_assert_eq(v_statement ->> 'provenance_state', 'original_first_party',
    'document', 'the statement for version 3 reads back');
  PERFORM pg_temp.f01_assert_eq(v_statement ->> 'document_digest',
    pg_temp.f01_recall('document_v3'),
    'document', 'and names the exact bytes that version was stored as');
  PERFORM pg_temp.f01_assert((v_statement ->> 'source_artifact_digest') IS NULL
    AND (v_statement ->> 'derivative_link_digest') IS NULL,
    'document', 'an original names no source artifact and no link');
  PERFORM pg_temp.f01_assert_eq(v_statement ->> 'integrity', 'recomputed_from_committed_row',
    'document', 'the statement readback is recomputed rather than trusted');
  PERFORM pg_temp.f01_assert_eq(v_statement ->> 'absent_statement_means_no_source', 'false',
    'document', 'an absent statement is never read as an absent source');
  PERFORM pg_temp.f01_assert(
    ops.f01_document_version_source('synthetic-document-0001', 99) IS NULL,
    'document', 'a version nobody recorded has no statement, and no default is invented');
  PERFORM pg_temp.f01_assert(
    jsonb_array_length(ops.f01_document_source_history('synthetic-document-0001')) = 3,
    'document', 'the history holds exactly the three statements made');
END;
$document_source_readback$;

-- 8.0.2 NO STATEMENT, NO VERSION. Passing NULL is not a way around the rule, and
-- the refusal happens before anything is written.
DO $document_requires_provenance$
DECLARE
  v_versions bigint := (SELECT count(*) FROM ops.f01_document_version);
BEGIN
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_document(
        pg_temp.f01_envelope('stored_document_version',
          pg_temp.f01_document_record(4, %L, 'fully_executed', 'filed', 'filed')),
        NULL, NULL, %L, 'syn-pg-document-noprov-0001',
        ops.f01_digest_jsonb('{"k":"doc-np"}'::jsonb))$$,
      pg_temp.f01_recall('document_v3'), pg_temp.f01_recall('document_v3')),
    'f01_document_provenance_required', 'document',
    'a document version cannot complete with no statement about its origin');

  -- A statement about a DIFFERENT version does not satisfy this one. "Some
  -- statement was supplied" is not the rule; the rule is that THIS version says
  -- where IT came from.
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_document(
        pg_temp.f01_envelope('stored_document_version',
          pg_temp.f01_document_record(4, %L, 'fully_executed', 'filed', 'filed')),
        pg_temp.f01_document_source_envelope(
          pg_temp.f01_document_source_record('synthetic-document-0001', 2,
            %L, 'original_first_party', NULL, NULL, NULL, NULL,
            'synthetic test fixture: authored in this record layer')),
        NULL, %L, 'syn-pg-document-wrongver-0001',
        ops.f01_digest_jsonb('{"k":"doc-wv"}'::jsonb))$$,
      pg_temp.f01_recall('document_v3'), pg_temp.f01_recall('document_v2'),
      pg_temp.f01_recall('document_v3')),
    'f01_document_provenance_not_bound_to_document', 'document',
    'a statement about another version does not complete this one');

  -- AND A DERIVED DECLARATION WITHOUT ITS LINK REFUSES. This is the producer rule
  -- itself: a version that says it came from a stored artifact completes only
  -- with the ops.f01_derivative_link row that says which one.
  PERFORM pg_temp.f01_expect_refusal(
    format($$SELECT ops.f01_record_document(
        pg_temp.f01_envelope('stored_document_version',
          pg_temp.f01_document_record(4, %L, 'fully_executed', 'filed', 'filed')),
        pg_temp.f01_document_source_envelope(
          pg_temp.f01_document_source_record('synthetic-document-0001', 4,
            ops.f01_digest_jsonb(pg_temp.f01_document_record(4, %L, 'fully_executed',
                                                             'filed', 'filed')),
            'derived_from_stored_artifact', %L, 'sha256:' || repeat('7c', 32),
            'synthetic_test_document_producer', 'syn-pg-document-derived-0001', NULL)),
        NULL, %L, 'syn-pg-document-nolink-0001',
        ops.f01_digest_jsonb('{"k":"doc-nl"}'::jsonb))$$,
      pg_temp.f01_recall('document_v3'), pg_temp.f01_recall('document_v3'),
      pg_temp.f01_recall('artifact_1'), pg_temp.f01_recall('document_v3')),
    'f01_derivative_link_required', 'document',
    'a derived document version completes only with its source registration');

  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_document_version) = v_versions,
    'document', 'not one refused document write landed a version');
END;
$document_requires_provenance$;

-- 8.1 A FULLY EXECUTED DOCUMENT CANNOT CLAIM A COMPLETE FILING WITHOUT ONE.
-- The one inference Q125 forbids is refused by a structural constraint, not by
-- a handler's good manners.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_write_document(4, %L, 'fully_executed', 'pending', 'filed',
      'syn-pg-document-liar-0001')$$,
    pg_temp.f01_recall('document_v3')),
  'f01_document_official_filing', 'document',
  'a pending OneDrive copy cannot be recorded as filed');

-- 8.2 a stale document CAS refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_write_document(4, %L, 'fully_executed', 'filed', 'filed',
      'syn-pg-document-stale-0001')$$,
    pg_temp.f01_recall('document_v1')),
  'f01_stale_document_digest', 'document', 'a stale document CAS refuses');

-- 8.3 readback reproduces every stored field and recomputes the digest.
DO $doc_readback$
DECLARE
  v_read jsonb := ops.f01_read('document', '{"document_id":"synthetic-document-0001"}'::jsonb);
  v_record jsonb := v_read -> 'body' -> 'record';
BEGIN
  PERFORM pg_temp.f01_assert_eq(v_read -> 'body' ->> 'record_digest',
    pg_temp.f01_recall('document_v3'), 'document', 'readback returns the current version');
  PERFORM pg_temp.f01_assert_eq(v_read -> 'body' ->> 'integrity',
    'recomputed_from_committed_row', 'document', 'readback recomputes rather than trusting');
  PERFORM pg_temp.f01_assert(
    v_record ->> 'preparation_state' = 'approved_for_delivery'
    AND v_record ->> 'delivery_state' = 'delivered'
    AND v_record ->> 'signature_state' = 'fully_executed'
    AND v_record ->> 'validity_state' = 'effective'
    AND v_record ->> 'version_state' = 'current'
    AND v_record -> 'onedrive_identity' ->> 'filing_state' = 'filed'
    AND v_record -> 'object_storage_identity' ->> 'sealed' = 'true'
    AND v_record -> 'neon_identity' ->> 'version_no' = '3',
    'document', 'all five axes and all three identities round-trip');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM jsonb_array_elements(
       ops.f01_read('document_versions', '{"document_id":"synthetic-document-0001"}'::jsonb)
         -> 'body')) = 3,
    'document', 'the immutable version history reads back in full');
END;
$doc_readback$;

-- 8.4 REPLAY IS DECIDED BEFORE STATE IS, and this is the case that proves it.
--
-- syn-pg-document-0002 recorded version 2 against version 1. Version 3 has since
-- landed, so the compare-and-swap that call carried — prior = document_v1 — is
-- now badly stale. Replaying it returns the STORED version-2 outcome rather than
-- raising f01_stale_document_digest, because ops.f01_claim_idempotency runs
-- before the current pointer is read at all.
--
-- This is the ordering the whole retry story depends on. Were it the other way
-- round, a client whose response was lost in flight would retry, be told its
-- write had failed, and be told so by the very mechanism that exists to make
-- retrying safe. It is also the assertion that catches somebody later "tidying"
-- a writer by hoisting its CAS above its claim.
DO $replay_precedes_cas$
DECLARE
  v_versions bigint := (SELECT count(*) FROM ops.f01_document_version);
  v_statements bigint := (SELECT count(*) FROM ops.f01_document_source_provenance);
  v_replay jsonb;
BEGIN
  PERFORM pg_temp.f01_assert_eq(
    (SELECT document_digest FROM ops.f01_document_current
      WHERE document_id = 'synthetic-document-0001'),
    pg_temp.f01_recall('document_v3'),
    'replay', 'version 3 is current before the stale replay');

  v_replay := pg_temp.f01_write_document(2, pg_temp.f01_recall('document_v1'),
    'fully_executed', 'pending', 'incomplete_official_filing', 'syn-pg-document-0002');

  PERFORM pg_temp.f01_assert_eq(v_replay ->> 'document_digest',
    pg_temp.f01_recall('document_v2'),
    'replay', 'a settled key replays its stored outcome, not a stale-CAS refusal');
  PERFORM pg_temp.f01_assert_eq(v_replay ->> 'official_filing_state',
    'incomplete_official_filing',
    'replay', 'the replayed outcome is the ORIGINAL outcome, not a recomputed one');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_document_version) = v_versions,
    'replay', 'the replay wrote no fourth version');
  PERFORM pg_temp.f01_assert_eq(
    (SELECT document_digest FROM ops.f01_document_current
      WHERE document_id = 'synthetic-document-0001'),
    pg_temp.f01_recall('document_v3'),
    'replay', 'the replay did not drag the current pointer backwards');
  -- ...and it wrote no second STATEMENT either. The version, its statement and —
  -- where there is one — its link are one transaction and one settled outcome, so
  -- a replay that duplicated any of the three would be a replay that wrote.
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_document_source_provenance) = v_statements,
    'replay', 'the replay wrote no second provenance statement');
  PERFORM pg_temp.f01_assert_eq(v_replay ->> 'provenance_state', 'original_first_party',
    'replay', 'the replayed answer carries the ORIGINAL statement, not a recomputed one');
END;
$replay_precedes_cas$;

-- 8.5 Replaying early is not a way AROUND payload binding. The same key with a
-- different request digest is still a substitution attempt and still refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_record_document(
      pg_temp.f01_envelope('stored_document_version',
        pg_temp.f01_document_record(2, %L, 'fully_executed', 'pending',
                                    'incomplete_official_filing')),
      pg_temp.f01_document_source_envelope(
        pg_temp.f01_document_source_record('synthetic-document-0001', 2,
          ops.f01_digest_jsonb(pg_temp.f01_document_record(2, %L, 'fully_executed',
            'pending', 'incomplete_official_filing')),
          'original_first_party', NULL, NULL, NULL, NULL,
          'synthetic test fixture: authored in this record layer')),
      NULL, %L, 'syn-pg-document-0002',
      ops.f01_digest_jsonb('{"k":"doc-2-altered"}'::jsonb))$$,
    pg_temp.f01_recall('document_v1'), pg_temp.f01_recall('document_v1'),
    pg_temp.f01_recall('document_v1')),
  'f01_idempotency_payload_mismatch', 'replay',
  'the replay door still binds one key to one payload');

-- 8.6 A READ-ONLY PRINCIPAL IS NOT A PRODUCER, and completing a document version
-- is now producer work: it registers a derivative whenever the version declares
-- one. The grant refuses first — 3.5.5 asserts that per role — and the writer's
-- own body refuses a carr_reader session as well, because either alone is a
-- single point of failure. The grant is what is checkable from here without
-- depending on a server error message.
SELECT pg_temp.f01_assert(
  NOT has_function_privilege('carr_reader',
    'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE')
  AND has_function_privilege('carr_writer',
    'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE'),
  'document', 'the reader cannot complete a document version and the producer can');

-- ...and the four-argument door is GONE rather than shadowed. An overload would
-- leave a path that completes a document with no statement about its origin,
-- which is the whole thing the six-argument form exists to prevent.
SELECT pg_temp.f01_assert(
  to_regprocedure('ops.f01_record_document(jsonb,text,text,text)') IS NULL,
  'document', 'the four-argument ops.f01_record_document no longer exists');

-- ===========================================================================
-- 9. Preservation holds.
--
-- RUNS AS carr_authority_joe. Placing and releasing a hold is the second
-- authorityOnly surface, so it is exercised by an authority principal and 9.3
-- proves the ordinary writer cannot reach it.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

CREATE FUNCTION pg_temp.f01_hold_record(p_hold_id text, p_state text, p_prior text,
                                        p_placed text, p_released text)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-preservation-hold.v1',
    'tenant', 'carr-internal',
    'hold_id', p_hold_id,
    'artifact_digest', pg_temp.f01_recall('artifact_1'),
    'state', p_state,
    'reason', 'synthetic test hold',
    'placed_at', p_placed,
    'released_at', p_released,
    'prior_hold_digest', p_prior,
    'recorded_by', pg_temp.f01_actor(),
    'recorded_at', ops.f01_now_text());
$$;

CREATE FUNCTION pg_temp.f01_append_hold(p_hold_id text, p_state text, p_prior text,
                                        p_placed text, p_released text, p_key text)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  RETURN ops.f01_record_hold(
    pg_temp.f01_envelope('stored_preservation_hold',
      pg_temp.f01_hold_record(p_hold_id, p_state, p_prior, p_placed, p_released),
      '{"humanOnly":true,"authorityOnly":true,"append_only":true,"deletes_artifact":false}'::jsonb),
    p_prior, p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_authority_joe;

DO $holds$
DECLARE
  v_result jsonb;
  v_artifacts bigint := (SELECT count(*) FROM ops.f01_corporate_artifact);
BEGIN
  v_result := pg_temp.f01_append_hold('synthetic-hold-0001', 'active', null,
    '2026-09-05T09:00:00Z', null, 'syn-pg-hold-0001');
  PERFORM pg_temp.f01_remember('hold_1_active', v_result ->> 'hold_digest');
  PERFORM pg_temp.f01_assert((v_result ->> 'deletes_artifact') = 'false',
    'hold', 'a hold never deletes the artifact it protects');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_corporate_artifact) = v_artifacts,
    'hold', 'the artifact count is unchanged by placing a hold');

  -- A release is a NEW row naming the state it replaces.
  v_result := pg_temp.f01_append_hold('synthetic-hold-0001', 'released',
    pg_temp.f01_recall('hold_1_active'), '2026-09-05T09:00:00Z',
    '2026-09-08T09:00:00Z', 'syn-pg-hold-0002');
  PERFORM pg_temp.f01_remember('hold_1_released', v_result ->> 'hold_digest');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_preservation_hold_event WHERE hold_id = 'synthetic-hold-0001') = 2
    AND (SELECT hold_state FROM ops.f01_preservation_hold_current
          WHERE hold_id = 'synthetic-hold-0001') = 'released',
    'hold', 'the release is appended and the placement is preserved');

  -- Expired and unknown states are both recordable and both readable.
  PERFORM pg_temp.f01_append_hold('synthetic-hold-0002', 'expired', null,
    '2026-09-01T09:00:00Z', '2026-09-04T09:00:00Z', 'syn-pg-hold-0003');
  PERFORM pg_temp.f01_append_hold('synthetic-hold-0003', 'unknown', null,
    '2026-09-01T09:00:00Z', null, 'syn-pg-hold-0004');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM jsonb_array_elements(
       ops.f01_hold_inventory(pg_temp.f01_recall('artifact_1')))) = 3,
    'hold', 'active, released, expired and unknown holds all read back');
END;
$holds$;

-- 9.1 a hold append against a stale prior refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_append_hold('synthetic-hold-0001', 'active', %L,
      '2026-09-05T09:00:00Z', null, 'syn-pg-hold-stale-0001')$$,
    pg_temp.f01_recall('hold_1_active')),
  'f01_stale_hold_digest', 'hold', 'a stale hold CAS refuses');

-- 9.2 a released hold with no release moment refuses structurally.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_append_hold('synthetic-hold-0001', 'released', %L,
      '2026-09-05T09:00:00Z', null, 'syn-pg-hold-norelease-0001')$$,
    pg_temp.f01_recall('hold_1_released')),
  'f01_hold_release_time', 'hold', 'a release with no release time refuses');

-- 9.3 WRONG PRINCIPAL. The hold tool is the other authorityOnly surface, and it
-- refuses on IDENTITY. As with 4.6, the old assertion here named
-- f01_authority_requires_verified_partner, which the shipped schema does not
-- raise and could not: authorization class is derived from session_user and is
-- not separately claimable, so there is nothing to catch it lying about.
--
-- Note which artifact this hold names — artifact_1, a genuinely stored one — so
-- the refusal cannot be mistaken for f01_unknown_artifact.
SELECT pg_temp.f01_remember('hold_candidate_envelope',
  pg_temp.f01_envelope('stored_preservation_hold',
    pg_temp.f01_hold_record('synthetic-hold-0004', 'active', null,
                            '2026-09-05T09:00:00Z', null),
    '{"humanOnly":true,"authorityOnly":true,"append_only":true,"deletes_artifact":false}'::jsonb)::text);

RESET SESSION AUTHORIZATION;

SELECT pg_temp.f01_expect_refusal(
  format($$SELECT ops.f01_record_hold(%L::jsonb, null,
      'syn-pg-hold-wrongprincipal-0001', 'sha256:' || repeat('9', 64))$$,
    pg_temp.f01_recall('hold_candidate_envelope')),
  'f01_authority_principal_refused', 'hold',
  'a well-formed hold offered by a non-authority principal refuses');

SET SESSION AUTHORIZATION carr_authority_joe;

SELECT pg_temp.f01_assert(
  (SELECT count(*) FROM ops.f01_preservation_hold_event
    WHERE hold_id = 'synthetic-hold-0004') = 0,
  'hold', 'a refused hold appends nothing');

-- And an ordinary evidence writer cannot reach the surface at all, so it never
-- meets the check above. Hold authority is not something a sponsored agent can
-- manufacture by any route this schema offers.
SELECT pg_temp.f01_assert(
  NOT has_function_privilege('carr_writer',
    'ops.f01_record_hold(jsonb,text,text,text)', 'EXECUTE')
  AND NOT has_function_privilege('carr_reader',
    'ops.f01_record_hold(jsonb,text,text,text)', 'EXECUTE'),
  'hold', 'no ordinary runtime principal can reach the hold surface');

-- 9.4 a hold on an artifact nobody stored refuses.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_hold(
      pg_temp.f01_envelope('stored_preservation_hold',
        jsonb_build_object(
          'schema_version','doctorcre-v5-f01-stored-preservation-hold.v1',
          'tenant','carr-internal','hold_id','synthetic-hold-0009',
          'artifact_digest','sha256:' || repeat('dd', 32),
          'state','active','reason','synthetic test hold',
          'placed_at','2026-09-05T09:00:00Z','released_at',null,
          'prior_hold_digest',null,'recorded_by',pg_temp.f01_actor(),
          'recorded_at',ops.f01_now_text()),
        '{"deletes_artifact":false}'::jsonb),
      null, 'syn-pg-hold-unknown-0001', ops.f01_digest_jsonb('{"k":"hu"}'::jsonb))$$,
  'f01_unknown_artifact', 'hold', 'a hold must protect a stored artifact');

-- ===========================================================================
-- 10. Deletion evaluation. NOTHING IS DELETED.  (carr_writer)
--
-- READ 10.1 BEFORE CHANGING ANYTHING HERE. Section 7.6 registers real
-- derivative-source links, so the ingress now EXISTS — and every `allow`
-- decision still fails closed, which is the point. Registered links say what was
-- registered; ops.f01_derivative_coverage still answers 'unknown' to "are these
-- all of them?", ops.f01_stored_derivatives therefore still returns NULL, and an
-- allow is refused rather than taken on an inventory nobody can vouch for.
-- Rows appearing in ops.f01_derivative_link is exactly the change that would
-- tempt somebody to read absence as verified absence; 10.1 is what stops it.
--
-- The positive allow path stays unreachable through a tool path, so the two
-- assertions that used to hang off it are re-expressed through the decisions
-- that ARE reachable, and the genuinely unreachable constraints are asserted
-- structurally instead of being quietly dropped.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

/**
 * The eight fields the Node store projects out of ops.f01_retention_clock, in the
 * shape a stored evaluation carries them.
 *
 * PROJECTED RATHER THAN FORWARDED, exactly as the store projects the coverage
 * answer: the reader returns more than the record holds — the artifact digest, the
 * source's own observed instant and the integrity note — and the DIGEST beside it
 * in the record is taken over the reader's FULL answer, which is what
 * ops.f01_record_deletion_evaluation re-derives.
 *
 * `->` and not `->>` for the booleans and the nullable fields: `->>` would render
 * them as text, and an evaluation carrying "true" where the store writes true is a
 * different record with a different digest.
 */
CREATE FUNCTION pg_temp.f01_projected_clock(p_artifact_digest text)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'kind', c ->> 'kind',
    'event_kind', c -> 'event_kind',
    'started_at', c ->> 'started_at',
    'reference', c ->> 'reference',
    'provenance', c ->> 'provenance',
    'event_digest', c -> 'event_digest',
    'verified', c -> 'verified',
    'source_observed_at_used', c -> 'source_observed_at_used')
  FROM (SELECT ops.f01_retention_clock(p_artifact_digest) AS c) s;
$$;

CREATE FUNCTION pg_temp.f01_deletion_record(p_decision text, p_reason text, p_receipt jsonb,
                                            p_coverage_state text DEFAULT NULL,
                                            p_coverage_digest text DEFAULT NULL,
                                            p_clock jsonb DEFAULT NULL,
                                            p_clock_digest text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-deletion-evaluation.v1',
    'tenant', 'carr-internal',
    'artifact_class', 'synthetic_test_lease',
    'artifact_home', 'onedrive',
    'artifact_digest', pg_temp.f01_recall('artifact_1'),
    'decision', p_decision,
    'reason_id', p_reason,
    'retention_registry_digest', ops.f01_digest_jsonb(pg_temp.f01_retention_registry(2)),
    'hold_inventory_digest', ops.f01_hold_inventory_digest(pg_temp.f01_recall('artifact_1')),
    -- The coverage answer this evaluation was taken against. Both halves are
    -- LOADED by default, exactly as the store loads them; the parameters exist
    -- so 10.4 can offer a stale or forged one and watch it refuse.
    'derivative_coverage_state', coalesce(p_coverage_state,
      ops.f01_derivative_coverage(pg_temp.f01_recall('artifact_1')) ->> 'state'),
    'derivative_coverage_digest', coalesce(p_coverage_digest,
      ops.f01_derivative_coverage_digest(pg_temp.f01_recall('artifact_1'))),
    -- THE TRIGGER THE PERIOD WAS MEASURED FROM. Both halves are LOADED by default,
    -- exactly as the store loads them; the parameters exist so 10.5 can offer a
    -- forged or stale trigger and watch the writer refuse it.
    'retention_clock', coalesce(p_clock,
      pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))),
    'retention_clock_digest', coalesce(p_clock_digest,
      ops.f01_retention_clock_digest(pg_temp.f01_recall('artifact_1'))),
    'deletion_receipt', p_receipt,
    'evaluated_by', pg_temp.f01_actor(),
    'evaluated_at', ops.f01_now_text());
$$;

CREATE FUNCTION pg_temp.f01_evaluate_deletion(p_decision text, p_reason text,
                                              p_receipt jsonb, p_key text,
                                              p_inventory text DEFAULT NULL,
                                              p_coverage_state text DEFAULT NULL,
                                              p_coverage_digest text DEFAULT NULL,
                                              p_clock jsonb DEFAULT NULL,
                                              p_clock_digest text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  RETURN ops.f01_record_deletion_evaluation(
    pg_temp.f01_envelope('stored_deletion_evaluation',
      pg_temp.f01_deletion_record(p_decision, p_reason, p_receipt,
                                  p_coverage_state, p_coverage_digest,
                                  p_clock, p_clock_digest),
      '{"silent_purge":false,"purge_without_proof":false,"bytes_deleted":false,'
      '"rows_deleted":false,"external_purge_performed":false}'::jsonb),
    coalesce(p_inventory, ops.f01_hold_inventory_digest(pg_temp.f01_recall('artifact_1'))),
    p_key, ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_writer;

-- 10.0 A refusal is recorded, and recording it deletes nothing.
DO $deletion$
DECLARE
  v_result jsonb;
  v_artifacts bigint := (SELECT count(*) FROM ops.f01_corporate_artifact);
BEGIN
  v_result := pg_temp.f01_evaluate_deletion('refuse', 'unknown_hold_state_blocks_deletion',
    null, 'syn-pg-deletion-0001');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'refuse',
    'deletion', 'an unreadable hold blocks the deletion');
  PERFORM pg_temp.f01_assert((v_result ->> 'bytes_deleted') = 'false'
    AND (v_result ->> 'rows_deleted') = 'false'
    AND (v_result ->> 'external_purge_performed') = 'false',
    'deletion', 'no evaluation claims an external purge occurred');
  PERFORM pg_temp.f01_assert((v_result ->> 'receipt_digest') IS NULL,
    'deletion', 'a refusal carries no deletion receipt');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_corporate_artifact) = v_artifacts,
    'deletion', 'evaluating a deletion deletes nothing');
  -- The surviving derivatives named by the retention class are reported back
  -- from the INSTALLED registry, not from anything the caller said. This is
  -- CLASS POLICY — which kinds survive if the artifact goes — and it is reported
  -- beside the coverage answer rather than in place of it.
  PERFORM pg_temp.f01_assert_eq(v_result -> 'surviving_derivatives' ->> 0,
    'synthetic_test_abstract',
    'deletion', 'surviving derivatives come from the installed retention class');
  -- ...and the INSTANCE OBSERVATION is a different field with a different
  -- answer. A registered abstract exists; whether it is the only derivative is
  -- still unknown, and the evaluation says so instead of implying otherwise.
  PERFORM pg_temp.f01_assert_eq(v_result -> 'derivative_coverage' ->> 'state', 'unknown',
    'deletion', 'the evaluation reports the coverage answer it was taken against');
  PERFORM pg_temp.f01_assert(
    (v_result -> 'derivative_coverage' ->> 'registered_link_count')::int >= 1,
    'deletion', 'registered links are visible in the evaluation and are not a coverage claim');
END;
$deletion$;

-- 10.1 ABSENCE OF EVIDENCE IS NOT EVIDENCE OF ABSENCE, and an allow says so.
--
-- ops.f01_stored_derivatives has no ingress to read in this slice, so it returns
-- NULL — unknown — and ops.f01_record_deletion_evaluation refuses every `allow`
-- rather than proceeding on an inventory nobody produced. This is the single
-- most important assertion in the section: the failure mode it forecloses is a
-- deletion approved because the database could not find any reason not to.
--
-- An earlier revision of this fixture asserted the opposite here — that this
-- exact call returns outcome 'allow' with a receipt digest. It cannot, and a
-- schema in which it could would be one that treats an unreadable derivative
-- inventory as an empty one.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_evaluate_deletion('allow', 'deletion_permitted',
      jsonb_build_object(
        'schema_version', 'doctorcre-v5-f01-deletion-receipt.v1',
        'tenant', 'carr-internal',
        'artifact_class', 'synthetic_test_lease', 'artifact_home', 'onedrive',
        'artifact_digest', pg_temp.f01_recall('artifact_1'),
        'retention_registry_digest', ops.f01_digest_jsonb(pg_temp.f01_retention_registry(2)),
        'domain_policy_digest', 'sha256:' || repeat('a', 64),
        'deletion_proof_required', true,
        'deletion_proof_ref', 'synthetic-proof-0001',
        'deletion_proof_digest', 'sha256:' || repeat('41', 32),
        'surviving_derivatives', jsonb_build_array('synthetic_test_abstract'),
        'released_holds', jsonb_build_array('synthetic-hold-0001'),
        'actor', null, 'actor_derived_by', 'authenticated_handler_context'),
      'syn-pg-deletion-0002')$$,
  'f01_derivative_inventory_unavailable', 'deletion',
  'an allow fails closed while the derivative inventory is unknown');

-- Fail-closed is a property of the STORED STATE too, not just of one call: no
-- allow exists in the table at all.
SELECT pg_temp.f01_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_deletion_evaluation WHERE decision = 'allow'),
  'deletion', 'no allow evaluation has been persisted by any route');

-- 10.2 An evaluation taken against a hold inventory that has since moved
-- refuses. Expressed through a `refuse` decision, because an `allow` would stop
-- at 10.1's derivative check first and would prove nothing about hold staleness.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-stale-0001', %L)$$,
    'sha256:' || repeat('bb', 32)),
  'f01_stale_hold_inventory', 'deletion', 'a stale hold inventory refuses');

-- And a stale RETENTION registry refuses on the same principle.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_deletion_evaluation(
      pg_temp.f01_envelope('stored_deletion_evaluation',
        pg_temp.f01_deletion_record('refuse', 'unknown_hold_state_blocks_deletion', null)
          || jsonb_build_object('retention_registry_digest',
               ops.f01_digest_jsonb(pg_temp.f01_retention_registry(1))),
        '{"silent_purge":false,"purge_without_proof":false,"bytes_deleted":false,'
        '"rows_deleted":false,"external_purge_performed":false}'::jsonb),
      ops.f01_hold_inventory_digest(pg_temp.f01_recall('artifact_1')),
      'syn-pg-deletion-staleret-0001', ops.f01_digest_jsonb('{"k":"sr"}'::jsonb))$$,
  'f01_stale_retention_policy', 'deletion',
  'an evaluation against a superseded retention registry refuses');

-- 10.2.1 A COVERAGE ANSWER THAT HAS MOVED REFUSES, on the same principle as the
-- hold inventory: an evaluation is bound to the picture of the world it was
-- taken against, and a derivative registered in between is exactly the change
-- that would make a stale evaluation wrong.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-stalecov-0001',
      null, 'unknown', %L)$$,
    'sha256:' || repeat('7b', 32)),
  'f01_stale_derivative_coverage', 'deletion',
  'an evaluation bound to a coverage digest the database does not hold refuses');

-- And a record CLAIMING established coverage refuses too, because the writer
-- re-derives the state rather than reading the caller's copy of it. This is the
-- forgery that would matter most: 'established' is the one word that unblocks a
-- deletion, and no caller may write it.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-forgedcov-0001',
      null, 'established')$$,
  'f01_stale_derivative_coverage', 'deletion',
  'a caller cannot declare coverage established');

-- 10.2.2 THE STORED-STATE HALF. No evaluation anywhere claims established
-- coverage, and no allow exists, however many links have been registered.
SELECT pg_temp.f01_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_deletion_evaluation
               WHERE envelope -> 'record' ->> 'derivative_coverage_state'
                     IS DISTINCT FROM 'unknown'),
  'deletion', 'every persisted evaluation was taken against unknown coverage');

-- ===========================================================================
-- 10.2.3 WHEN THE RETENTION PERIOD STARTED, derived and re-derived.
--
-- The accepted rule: a default retention period starts at the SERVER-STAMPED
-- instant this record layer took custody of the artifact. The instant its SOURCE
-- says it observed the artifact is identity and provenance, and is never the
-- clock — under that name or any other. What is checkable HERE, against a real
-- database, is the half a pure suite cannot reach: that the clock is derived from
-- the stored row, that the writer re-derives it under the lock it already holds,
-- and that a forged or stale trigger refuses.
-- ===========================================================================

DO $retention_clock$
DECLARE
  v_clock jsonb := ops.f01_retention_clock(pg_temp.f01_recall('artifact_1'));
  v_row ops.f01_corporate_artifact%ROWTYPE;
BEGIN
  SELECT * INTO v_row FROM ops.f01_corporate_artifact
   WHERE artifact_digest = pg_temp.f01_recall('artifact_1');

  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'kind', 'server_recorded_custody',
    'retention', 'the clock this slice derives is custody, and only custody');
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'started_at',
    ops.f01_instant_text(v_row.recorded_at),
    'retention', 'the period starts at the server-stamped instant on the stored row');
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'reference',
    'ops.f01_corporate_artifact.recorded_at',
    'retention', 'the clock names where its instant came from');
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'provenance', 'server_stamped_custody',
    'retention', 'and whose provenance it carries');
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'verified', 'true',
    'retention', 'the server''s own stamp on its own row is verified by construction');

  -- THE ANTI-ALIAS STATEMENT, both halves. The source's instant is reported, and
  -- reported as NOT the thing the period runs from — and the two are genuinely
  -- different values here, so the assertion is not passing by coincidence.
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'source_observed_at_used', 'false',
    'retention', 'a retention period never runs from the source''s observed instant');
  PERFORM pg_temp.f01_assert_eq(v_clock ->> 'source_observed_at', v_row.observed_at_text,
    'retention', 'the source''s own instant is reported beside the clock, not in place of it');
  PERFORM pg_temp.f01_assert(
    (v_clock ->> 'started_at') IS DISTINCT FROM v_row.observed_at_text,
    'retention', 'custody and observation are different instants in this fixture',
    v_row.observed_at_text || ' observed, ' || (v_clock ->> 'started_at') || ' recorded');

  -- AND NO EVENT CLOCK EXISTS. Nothing in this schema produces, approves or
  -- authenticates a retention-clock event, so every clock it can derive is
  -- custody and every explicit-event class fails closed in the kernel.
  PERFORM pg_temp.f01_assert((v_clock ->> 'event_kind') IS NULL
    AND (v_clock ->> 'event_digest') IS NULL,
    'retention', 'no retention-clock event is produced anywhere in this slice');

  -- An artifact nobody stored has no clock, and no default is invented for it.
  PERFORM pg_temp.f01_assert(
    ops.f01_retention_clock('sha256:' || repeat('cf', 32)) IS NULL,
    'retention', 'a period that would start from nothing is not a period');
END;
$retention_clock$;

-- 10.2.3.1 THE WRITER RE-DERIVES THE TRIGGER, and a forged one refuses. This is
-- the assertion that matters most in the section: `started_at` is the single field
-- that decides whether a period has elapsed, so it is the field a forged
-- evaluation would move.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-forgedclock-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"started_at":"2019-01-01T00:00:00.000Z"}'::jsonb)::text),
  'f01_stale_retention_clock', 'retention',
  'an evaluation naming a start instant the database does not hold refuses');

-- A forged KIND is refused on the same clause: 'explicit_retention_clock_event' is
-- the word that would move a period to an event nobody produced.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-forgedkind-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || jsonb_build_object('kind', 'explicit_retention_clock_event',
                             'event_kind', 'lease_terminated',
                             'event_digest', 'sha256:' || repeat('7d', 32)))::text),
  'f01_stale_retention_clock', 'retention',
  'a caller cannot declare that an explicit retention-clock event started the period');

-- A STALE DIGEST refuses too, exactly as a stale hold inventory or a stale
-- coverage answer does: the record must be bound to the answer the database holds.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-staleclock-0001',
      null, null, null, null, %L)$$,
    'sha256:' || repeat('7e', 32)),
  'f01_stale_retention_clock', 'retention',
  'an evaluation bound to a retention-clock digest the database does not hold refuses');

-- AND THE ALIAS IS REFUSED BY ITS OWN NAME, before the digest comparison, so the
-- one substitution the rule exists to prevent does not depend on a digest happening
-- to notice it.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-aliasclock-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"source_observed_at_used":true}'::jsonb)::text),
  'f01_retention_clock_uses_source_observed_at', 'retention',
  'an evaluation whose trigger admits it is the source''s observed instant refuses');

-- ===========================================================================
-- 10.2.3.2 THE EMBEDDED TRIGGER IS BOUND WHOLE, and the digest beside it cannot
-- stand in for that.
--
-- WHY THIS SECTION EXISTS AT ALL. It is tempting to read 10.2.3.1 and conclude
-- that the digest catches everything — that any change to the embedded trigger
-- must move `retention_clock_digest` and be caught there. It does not, and the
-- reason is structural rather than subtle: the digest is taken over the reader's
-- FULL answer (which also carries the artifact digest, the source's observed
-- instant and the integrity note), while the record embeds the eight-field
-- PROJECTION the decision was allowed to read. They are two different values.
-- Nothing the caller does to the embedded object changes the digest sitting
-- beside it, because that digest was never taken over the embedded object.
--
-- So the first block below proves the premise before the refusals lean on it:
-- a record whose embedded trigger has been tampered with still carries the REAL,
-- unmoved server clock digest. Every refusal after it is therefore a refusal the
-- digest comparison could not have produced.
-- ===========================================================================

DO $clock_bound_whole$
DECLARE
  v_real jsonb := pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'));
  v_forged jsonb := v_real || '{"provenance":"source_asserted_custody"}'::jsonb;
  v_record jsonb := pg_temp.f01_deletion_record(
    'refuse', 'unknown_hold_state_blocks_deletion', null, null, null, v_forged);
BEGIN
  -- The premise, stated as an equality rather than asserted in a comment: the
  -- digest the tampered record carries IS the digest of the database's own answer.
  PERFORM pg_temp.f01_assert_eq(v_record ->> 'retention_clock_digest',
    ops.f01_retention_clock_digest(pg_temp.f01_recall('artifact_1')),
    'retention', 'tampering with the embedded trigger does not move the clock digest beside it');
  -- ...and the object it carries is genuinely not the derived one, so the fixture
  -- is not passing because the forgery failed to apply.
  PERFORM pg_temp.f01_assert((v_record -> 'retention_clock') IS DISTINCT FROM v_real,
    'retention', 'and the embedded trigger really has been altered',
    (v_record -> 'retention_clock' ->> 'provenance'));
  -- The one thing the digest DOES still bind: the reader's full answer, which is
  -- a different object from the projection the record embeds.
  PERFORM pg_temp.f01_assert(
    ops.f01_retention_clock(pg_temp.f01_recall('artifact_1')) IS DISTINCT FROM v_real,
    'retention', 'the digested full answer is not the object the record embeds');
END;
$clock_bound_whole$;

-- PROVENANCE. The forgery the digest cannot see: the timing is untouched, the
-- kind is untouched, the digest is the real one — and the stored envelope would
-- have said this period was measured from an instant a source asserted. Every
-- later reader of that envelope reads the record, not the digest.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-clockprov-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"provenance":"source_asserted_custody"}'::jsonb)::text),
  'f01_stale_retention_clock', 'retention',
  'a trigger claiming a provenance the database does not hold refuses, real digest and all');

-- REFERENCE. Same shape, and it is the field that says WHERE the instant was read
-- from. A record naming the artifact's observed_at column as its source, while
-- carrying the custody instant and the real digest, is the alias written down.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-clockref-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"reference":"ops.f01_corporate_artifact.observed_at_text"}'::jsonb)::text),
  'f01_stale_retention_clock', 'retention',
  'a trigger naming a reference the database does not hold refuses');

-- VERIFIED, and it is bound in BOTH directions. Understating it is refused here
-- because a stored evaluation is a statement about what the DATABASE answered,
-- not about what the caller was willing to claim. The direction that will matter
-- is the other one — the day an explicit event clock exists, `verified` is the
-- flag that says something authenticated it — and a writer that ignored the field
-- would be a writer that let a caller supply that flag.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-clockver-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"verified":false}'::jsonb)::text),
  'f01_stale_retention_clock', 'retention',
  'a trigger restating the database''s verification flag refuses');

-- AN EXTRA FIELD IS A FORGERY TOO, because the shape is closed. A caller that can
-- add a key can add an integrity note nobody computed, sitting inside a record
-- whose digest checks out and whose eight known fields all agree.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-clockextra-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1'))
       || '{"integrity":"asserted_by_caller"}'::jsonb)::text),
  'f01_stale_retention_clock', 'retention',
  'a trigger carrying a field the projection does not hold refuses');

-- AND A MISSING FIELD IS THE SAME FORGERY FROM THE OTHER SIDE. Dropping a field is
-- how a record stops saying something, and a comparison of a few named fields
-- would never have noticed the rest going quiet.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_evaluate_deletion('refuse',
      'unknown_hold_state_blocks_deletion', null, 'syn-pg-deletion-clockmissing-0001',
      null, null, null, %L::jsonb)$$,
    (pg_temp.f01_projected_clock(pg_temp.f01_recall('artifact_1')) - 'verified')::text),
  'f01_stale_retention_clock', 'retention',
  'a trigger that has dropped a field the projection holds refuses');

-- THE OTHER HALF OF THE SAME BINDING, so this section cannot pass by refusing
-- everything: the UNALTERED projection is still accepted, on the same writer, in
-- the same session, one statement later. Default custody is not weakened by any
-- of the above.
DO $clock_unaltered_accepted$
DECLARE
  v_result jsonb;
BEGIN
  v_result := pg_temp.f01_evaluate_deletion('refuse', 'unknown_hold_state_blocks_deletion',
    null, 'syn-pg-deletion-clockwhole-0001');
  PERFORM pg_temp.f01_assert_eq(v_result ->> 'outcome', 'refuse',
    'retention', 'the unaltered derived trigger is still accepted, field for field');
  PERFORM pg_temp.f01_assert_eq(v_result -> 'retention_clock' ->> 'kind',
    'server_recorded_custody',
    'retention', 'and the evaluation is still measured from server-stamped custody');
END;
$clock_unaltered_accepted$;

-- THE STORED-STATE HALF. Every persisted evaluation names a custody trigger, and
-- not one of them claims the source's observed instant started the period.
SELECT pg_temp.f01_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_deletion_evaluation
               WHERE envelope -> 'record' -> 'retention_clock' ->> 'kind'
                     IS DISTINCT FROM 'server_recorded_custody'
                  OR coalesce(envelope -> 'record' -> 'retention_clock'
                                ->> 'source_observed_at_used', '') <> 'false'),
  'retention', 'every persisted evaluation was measured from server-stamped custody');

-- ...and the whole-object half of the same statement, which is the one a
-- field-by-field comparison could not make: every stored trigger IS the projection
-- the database derives for that artifact, not merely one that agrees about kind
-- and timing. Nothing is deleted in this fixture, so every artifact named by a
-- stored evaluation is still there to re-derive a clock from.
SELECT pg_temp.f01_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_deletion_evaluation e
               WHERE e.envelope -> 'record' -> 'retention_clock'
                     IS DISTINCT FROM pg_temp.f01_projected_clock(e.artifact_digest)),
  'retention', 'every persisted trigger is the derived one whole, field for field');

-- 10.3 THE CONSTRAINTS THIS FIXTURE CANNOT REACH BEHAVIOURALLY, stated plainly
-- rather than
-- dropped. f01_deletion_receipt_only_on_allow forbids an allow with no receipt
-- and a refusal with one. Neither shape can be driven through a tool path today:
-- every allow stops at 10.1 before the INSERT, and the direct-DML guard forbids
-- reaching the constraint by writing the row by hand. So it is asserted to
-- EXIST and to bind BOTH directions — matched loosely enough to survive
-- PostgreSQL renormalizing the expression it stores, strictly enough that a
-- constraint weakened to one direction fails — and it becomes a behavioural test
-- the day derivative ingress lands. An assertion that silently tested nothing
-- would be worse than an honest gap.
SELECT pg_temp.f01_assert(
  (SELECT pg_get_constraintdef(c.oid) ~ 'decision = ''allow''.*receipt_digest IS NOT NULL'
      AND pg_get_constraintdef(c.oid) ~ 'decision = ''refuse''.*receipt_digest IS NULL'
     FROM pg_constraint c
    WHERE c.conrelid = 'ops.f01_deletion_evaluation'::regclass
      AND c.conname = 'f01_deletion_receipt_only_on_allow'
      AND c.contype = 'c'),
  'deletion', 'the receipt-only-on-allow constraint is installed and binds both directions',
  'unreachable behaviourally until derivative-registration coverage can be established');

-- The second one, added with the derivative seam: no evaluation may be stored
-- without saying which coverage answer it was taken against, and no ALLOW may be
-- stored while that answer is anything but 'established'. The writer refuses the
-- same shape earlier and with a clearer message, so this can only be reached by
-- editing the writer — which is precisely when it needs to still be true.
--
-- THE `IS NOT NULL` HALF IS ASSERTED SEPARATELY AND ON PURPOSE. Naming the two
-- keys and the word 'established' was satisfied by a constraint that had a hole
-- in exactly the case that matters: over an ABSENT key `->>` is NULL, NULL IN
-- (...) is NULL, the digest-shape test is non-strict and answers NULL too, and
-- for an allow the last conjunct becomes `false OR NULL` — so the whole
-- expression was NULL and a CHECK, which fails only on FALSE, admitted an ALLOW
-- carrying no coverage fields at all. A presence test in front of each key is
-- what turns that NULL into a FALSE, and its absence is what this line catches.
SELECT pg_temp.f01_assert(
  (SELECT pg_get_constraintdef(c.oid) ~ 'derivative_coverage_state'
      AND pg_get_constraintdef(c.oid) ~ 'derivative_coverage_digest'
      AND pg_get_constraintdef(c.oid) ~ '''established'''
      -- Written to survive PostgreSQL's deparsing rather than to match one
      -- rendering of it: the key literal, whatever cast and closing parens the
      -- server chose to print, then the presence test.
      AND pg_get_constraintdef(c.oid) ~ 'derivative_coverage_state''[^)]*\)+ IS NOT NULL'
      AND pg_get_constraintdef(c.oid) ~ 'derivative_coverage_digest''[^)]*\)+ IS NOT NULL'
     FROM pg_constraint c
    WHERE c.conrelid = 'ops.f01_deletion_evaluation'::regclass
      AND c.conname = 'f01_deletion_coverage_bound'
      AND c.contype = 'c'),
  'deletion',
  'the coverage-bound constraint is installed, refuses an ABSENT coverage answer, and forbids an allow under unknown coverage',
  'unreachable behaviourally while every coverage answer is unknown');

-- The third one, added with the retention clock: no evaluation may be stored
-- without saying which trigger its period was measured from, that trigger must be
-- one of the two registered kinds, its start instant must be readable, and it must
-- not admit that it is the source's observed instant. The writer refuses each of
-- those earlier and with a clearer message, so this is the version that holds if
-- somebody edits the writer — which is exactly when a period measured from a value
-- a source can backdate would come back.
--
-- THE PRESENCE TESTS ARE ASSERTED SEPARATELY, for the reason the coverage
-- constraint above spells out: over an ABSENT key `->>` is NULL, the instant test
-- is non-strict and answers NULL too, and a CHECK fails only on FALSE — so a
-- constraint written without them would admit a record carrying no clock at all.
SELECT pg_temp.f01_assert(
  (SELECT pg_get_constraintdef(c.oid) ~ 'retention_clock_digest'
      AND pg_get_constraintdef(c.oid) ~ 'server_recorded_custody'
      AND pg_get_constraintdef(c.oid) ~ 'explicit_retention_clock_event'
      AND pg_get_constraintdef(c.oid) ~ 'source_observed_at_used'
      AND pg_get_constraintdef(c.oid) ~ 'retention_clock_digest''[^)]*\)+ IS NOT NULL'
      AND pg_get_constraintdef(c.oid) ~ 'started_at''[^)]*\)+ IS NOT NULL'
     FROM pg_constraint c
    WHERE c.conrelid = 'ops.f01_deletion_evaluation'::regclass
      AND c.conname = 'f01_deletion_retention_clock_bound'
      AND c.contype = 'c'),
  'retention',
  'the retention-clock constraint is installed, refuses an ABSENT trigger, and forbids the source-observed alias',
  'the writer refuses the same shapes earlier and with better messages');

-- AND WHAT THAT CONSTRAINT DOES NOT SAY, recorded rather than left to be assumed
-- from its presence. Both tightened constraints are added NOT VALID, so rows
-- written before the tightening are UNPROVEN: they were never re-checked, and
-- convalidated = false is the catalog saying exactly that. This asserts the
-- honest reading rather than the comfortable one — every row inserted from here
-- on is bound, and history is not retro-verified. In this fixture's own database
-- the tables are created empty by the same run, so nothing unproven exists here;
-- the assertion is about what the schema CLAIMS, which travels to databases where
-- that is not true.
SELECT pg_temp.f01_assert(
  (SELECT count(*) FROM pg_constraint c
    WHERE c.conrelid IN ('ops.f01_deletion_evaluation'::regclass,
                         'ops.f01_derivative_link'::regclass)
      AND c.conname IN ('f01_deletion_coverage_bound', 'f01_derivative_claims_nothing')
      AND c.contype = 'c') = 2,
  'deletion',
  'both tightened coverage/claim constraints are installed under the names the schema states',
  'added NOT VALID: binding on every new row, and making no claim about rows written before');

-- ...AND THE CATALOG SAYS THE UNPROVEN PART OUT LOUD. convalidated = false is
-- PostgreSQL recording that these constraints were never checked against the rows
-- that already existed. Asserting the flag — rather than only the constraint's
-- presence — is what keeps the comfortable reading ("it is installed, so
-- everything satisfies it") from quietly replacing the honest one ("it binds
-- every row inserted from here on, and history is unproven").
--
-- THIS IS AN ASSERTION ABOUT THE SCHEMA, NOT ABOUT THIS DATABASE. Here the tables
-- were created empty by the same run, so nothing unproven exists; the flag
-- travels to databases where that is not true, which is exactly where somebody
-- would otherwise read a validated guarantee into it. A later, deliberate
-- VALIDATE CONSTRAINT would flip these to true and fail here, which is the right
-- moment to re-read the two comments in domain.sql that explain why they are not
-- validated on install.
SELECT pg_temp.f01_assert(
  (SELECT count(*) FROM pg_constraint c
    WHERE c.conrelid IN ('ops.f01_deletion_evaluation'::regclass,
                         'ops.f01_derivative_link'::regclass)
      AND c.conname IN ('f01_deletion_coverage_bound', 'f01_derivative_claims_nothing')
      AND c.contype = 'c' AND c.convalidated = false) = 2,
  'deletion',
  'both tightened constraints are recorded NOT VALID, so no rows written before them are claimed as proven');

-- ===========================================================================
-- 11. Append-only, no silent overwrite, no truncate.
--
-- RUNS AS THE APPLYING SUPERUSER, deliberately and necessarily. A runtime
-- principal holds no DML grant at all, so running these as carr_writer would
-- produce "permission denied" — true, but a statement about the grant, which
-- section 3.5.5 already proves, and not about the guards. Only a principal that
-- COULD write proves anything by being refused, and the session that applies
-- this file is the only such principal here: it is a superuser, so no ACL stops
-- it and every refusal below is the trigger's, not the grant's.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

DO $append_only$
DECLARE
  v_table text;
BEGIN
  FOREACH v_table IN ARRAY ARRAY[
    'f01_policy_version', 'f01_field_event', 'f01_state_transition', 'f01_mutation_receipt',
    'f01_reconciliation_item', 'f01_corporate_artifact', 'f01_parsed_proposal',
    'f01_proposal_link', 'f01_derivative_link', 'f01_document_version',
    -- A statement about one version is made once. A later version gets its own
    -- row; the earlier one is never edited to say something different about what
    -- already happened.
    'f01_document_source_provenance',
    'f01_preservation_hold_event', 'f01_deletion_evaluation']
  LOOP
    PERFORM pg_temp.f01_expect_refusal(
      format('UPDATE ops.%I SET tenant = tenant', v_table),
      'f01_', 'append_only', v_table || ' refuses UPDATE');
    PERFORM pg_temp.f01_expect_refusal(
      format('DELETE FROM ops.%I', v_table),
      'f01_', 'append_only', v_table || ' refuses DELETE');
    PERFORM pg_temp.f01_expect_refusal(
      format('TRUNCATE ops.%I CASCADE', v_table),
      'f01_truncate_refused', 'append_only', v_table || ' refuses TRUNCATE');
  END LOOP;
  -- Current-state relations are replaced in place by a writer, but never deleted
  -- and never truncated: a missing current row would make a history gap look
  -- plausible.
  FOREACH v_table IN ARRAY ARRAY[
    'f01_policy_current', 'f01_field_state', 'f01_document_current',
    'f01_preservation_hold_current']
  LOOP
    PERFORM pg_temp.f01_expect_refusal(
      format('DELETE FROM ops.%I', v_table),
      'f01_', 'append_only', v_table || ' refuses DELETE');
    PERFORM pg_temp.f01_expect_refusal(
      format('TRUNCATE ops.%I CASCADE', v_table),
      'f01_truncate_refused', 'append_only', v_table || ' refuses TRUNCATE');
  END LOOP;
END;
$append_only$;

-- ===========================================================================
-- 12. Direct DML.
--
-- Even from a session that holds every privilege, an INSERT that did not arrive
-- through a registered ops.f01_* writer refuses. This is the bypass test: the
-- guarantee cannot depend on the handler being the only thing with a connection.
-- ===========================================================================

DO $direct_dml$
DECLARE
  v_table text;
BEGIN
  FOREACH v_table IN ARRAY ARRAY[
    'f01_policy_version', 'f01_policy_current', 'f01_field_state', 'f01_field_event',
    'f01_state_transition', 'f01_mutation_receipt', 'f01_reconciliation_item',
    'f01_corporate_artifact', 'f01_parsed_proposal', 'f01_proposal_link',
    'f01_derivative_link', 'f01_document_version', 'f01_document_current',
    'f01_document_source_provenance',
    'f01_preservation_hold_event', 'f01_preservation_hold_current',
    'f01_deletion_evaluation', 'f01_idempotency']
  LOOP
    PERFORM pg_temp.f01_expect_refusal(
      format('INSERT INTO ops.%I DEFAULT VALUES', v_table),
      'f01_direct_dml_refused', 'direct_dml', v_table || ' refuses a direct INSERT');
  END LOOP;
END;
$direct_dml$;

-- ===========================================================================
-- 12.1 The grant posture, read back from the catalog.
--
-- The previous revision asked information_schema.role_table_grants for this, and
-- that view answers a narrower question than the one being asked: it is
-- grantee-relative, showing only grants the CURRENT role is party to, and the
-- old predicate then excluded every superuser by name on top of that. A grant to
-- some third role would not have appeared, and the assertion would have passed.
--
-- aclexplode over pg_class and pg_proc reads the actual ACLs, for everyone.
--
-- A NULL ACL IS NOT "NO GRANTS", and the two object classes differ. For a TABLE
-- the built-in default is owner-only, so a NULL relacl is healthy and
-- aclexplode(NULL) rightly yields nothing. For a FUNCTION the default INCLUDES
-- PUBLIC EXECUTE, so a NULL proacl is itself the finding — it means the revoke
-- loop never touched that function at all.
-- ===========================================================================

DO $grant_posture$
DECLARE
  v_bad text;
BEGIN
  SELECT string_agg(DISTINCT c.oid::regclass::text || ' -> ' ||
                    coalesce(g.rolname, 'PUBLIC') || ':' || a.privilege_type, ', ') INTO v_bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    LEFT JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
     AND a.grantee IS DISTINCT FROM c.relowner;
  PERFORM pg_temp.f01_assert(v_bad IS NULL,
    'direct_dml', 'no non-owner DML grant exists on any F01 relation', v_bad);

  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops' AND p.proname LIKE 'f01\_%'
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'));
  PERFORM pg_temp.f01_assert(v_bad IS NULL,
    'direct_dml', 'every ops.f01_* function has an explicit ACL and none grants PUBLIC EXECUTE',
    v_bad);

  -- The private helpers are executable by the owner and by nobody else. They are
  -- reached as triggers, or from inside a SECURITY DEFINER writer where they run
  -- as the owner regardless of who called it, so no runtime grant is needed and
  -- any runtime grant is a hole.
  SELECT string_agg(DISTINCT p.oid::regprocedure::text, ', ') INTO v_bad
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'ops'
     AND (p.proname IN ('f01_claim_idempotency', 'f01_settle_idempotency',
                        -- The registration seam's private half, listed here for
                        -- the same reason the other two are: it is reached only
                        -- from inside a definer writer, where it runs as the
                        -- owner, so any runtime EXECUTE on it is a hole rather
                        -- than a convenience. 3.5.5 asserts the same per role.
                        'f01_insert_derivative_link',
                        -- The document-source seam's inserter, on the same terms.
                        -- This one also catches the wrong APPLICATION ORDER:
                        -- domain.sql's grant loop iterates over the functions
                        -- that exist when it runs, so a re-apply after the
                        -- document hunk is the case where an unnamed helper gets
                        -- handed out. domain.sql names it too; this is the
                        -- readback that would notice if it stopped.
                        'f01_insert_document_source_provenance')
          OR p.proname LIKE 'f01\_guard\_%')
     AND (p.proacl IS NULL
          OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                      WHERE a.privilege_type = 'EXECUTE'
                        AND a.grantee IS DISTINCT FROM p.proowner));
  PERFORM pg_temp.f01_assert(v_bad IS NULL,
    'direct_dml', 'no private F01 helper is runtime-executable', v_bad);

  -- Nothing under the prefix is readable or writable by PUBLIC either.
  SELECT string_agg(DISTINCT c.oid::regclass::text || ':' || a.privilege_type, ', ') INTO v_bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
   WHERE n.nspname = 'ops' AND c.relkind = 'r' AND c.relname LIKE 'f01\_%'
     AND a.grantee = 0;
  PERFORM pg_temp.f01_assert(v_bad IS NULL,
    'direct_dml', 'PUBLIC holds no privilege at all on an F01 relation', v_bad);

  -- And CREATE on the schema is not open. This one closes the direct-DML guard's
  -- own escape hatch: the guard decides by looking for a call frame naming a
  -- registered ops.f01_* writer, so anyone who could define their own
  -- ops.f01_anything() could satisfy it whenever they liked.
  SELECT string_agg(DISTINCT coalesce(g.rolname, 'PUBLIC'), ', ') INTO v_bad
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(n.nspacl) a
    LEFT JOIN pg_roles g ON g.oid = a.grantee
   WHERE n.nspname = 'ops' AND a.privilege_type = 'CREATE'
     AND a.grantee IS DISTINCT FROM n.nspowner;
  PERFORM pg_temp.f01_assert(v_bad IS NULL,
    'direct_dml', 'no non-owner may CREATE in the ops schema', v_bad);
END;
$grant_posture$;

-- ===========================================================================
-- 13. Idempotency.  (carr_writer)
-- ===========================================================================

SET SESSION AUTHORIZATION carr_writer;

DO $idempotency$
DECLARE
  v_first jsonb;
  v_replay jsonb;
  v_events bigint;
BEGIN
  v_first := ops.f01_record_artifact(
    pg_temp.f01_envelope('stored_corporate_artifact',
      pg_temp.f01_artifact_record('sha256:' || repeat('51', 32), 'synthetic-version-5')),
    'syn-pg-idem-0001', ops.f01_digest_jsonb('{"k":"idem"}'::jsonb));
  v_events := (SELECT count(*) FROM ops.f01_corporate_artifact);
  -- The exact same payload replays to the stored result and writes nothing new.
  v_replay := ops.f01_record_artifact(
    pg_temp.f01_envelope('stored_corporate_artifact',
      pg_temp.f01_artifact_record('sha256:' || repeat('51', 32), 'synthetic-version-5')),
    'syn-pg-idem-0001', ops.f01_digest_jsonb('{"k":"idem"}'::jsonb));
  PERFORM pg_temp.f01_assert_eq(v_replay ->> 'artifact_digest', v_first ->> 'artifact_digest',
    'idempotency', 'an exact replay returns the stored result');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_corporate_artifact) = v_events,
    'idempotency', 'a replay writes no second row');
END;
$idempotency$;

-- 13.1 the same key over a DIFFERENT payload refuses: no substitution.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_artifact(
      pg_temp.f01_envelope('stored_corporate_artifact',
        pg_temp.f01_artifact_record('sha256:' || repeat('52', 32), 'synthetic-version-6')),
      'syn-pg-idem-0001', ops.f01_digest_jsonb('{"k":"different"}'::jsonb))$$,
  'f01_idempotency_payload_mismatch', 'idempotency',
  'one key may not bind two payloads');

-- 13.2 the same key across two DIFFERENT operations refuses.
SELECT pg_temp.f01_expect_refusal(
  format($$SELECT pg_temp.f01_write_document(4, %L, 'fully_executed', 'filed', 'filed',
      'syn-pg-idem-0001')$$,
    pg_temp.f01_recall('document_v3')),
  'f01_idempotency', 'idempotency', 'one key may not bind two operations');

-- 13.3 an empty idempotency key refuses.
SELECT pg_temp.f01_expect_refusal(
  $$SELECT ops.f01_record_artifact(
      pg_temp.f01_envelope('stored_corporate_artifact',
        pg_temp.f01_artifact_record('sha256:' || repeat('53', 32), 'synthetic-version-7')),
      '', ops.f01_digest_jsonb('{"k":"empty"}'::jsonb))$$,
  'f01_idempotency_key_required', 'idempotency', 'every write carries an idempotency key');

-- ===========================================================================
-- 14. Corrupt newest state refuses without falling back.
--
-- The digest CHECK constraints make ordinary corruption impossible, so this
-- fixture removes the constraint and the append-only guard FIRST — something
-- only a schema owner on a disposable database can do — and then proves that the
-- readback still catches it. That is the whole point: the readback recomputes
-- rather than trusting the row, and it REFUSES rather than resolving to the
-- previous healthy version.
-- ===========================================================================

-- THE WHOLE SECTION IS ONE TRANSACTION, and that is not decoration. It drops two
-- constraints and disables a guard, so an assertion failing halfway through must
-- not leave the database corrupt and unguarded — an explicit BEGIN/COMMIT means
-- ON_ERROR_STOP rolls the damage back on the way out. The identity switches in
-- the middle are transactional for the same reason: ops.f01_read derives a
-- principal and the owner is not one, so the read-side proofs have to be made by
-- a real principal, while the ALTER TABLEs can only be made by the owner.
BEGIN;

-- SECTION 13 LEFT THE SESSION AS carr_writer, and the first half of this section
-- is owner work. Without this the ALTER TABLEs below fail 42501 — a runtime
-- principal owns nothing — and the whole section aborts before section 16 has a
-- restored state to emit its race against. This is a RESET rather than a SET
-- because the identity the ALTER TABLEs need is the one that APPLIED the file,
-- which section 0 already required to exist and to be a superuser; naming a role
-- here instead would be this fixture inventing an owner rather than using the
-- one it was applied by. It is inside the transaction on purpose, exactly like
-- the two switches below it.
RESET SESSION AUTHORIZATION;

DO $corrupt_setup$
BEGIN
  PERFORM pg_temp.f01_remember('state_before_corruption',
    ops.f01_current_field_state('deal', 'commission_amount') ->> 'state_digest');
  PERFORM pg_temp.f01_assert(
    pg_temp.f01_recall('state_before_corruption') IS NOT NULL,
    'corruption', 'a healthy state reads back');

  ALTER TABLE ops.f01_field_state DROP CONSTRAINT f01_state_record_digest;
  ALTER TABLE ops.f01_field_state DROP CONSTRAINT f01_state_envelope_digest;
  ALTER TABLE ops.f01_field_state DISABLE TRIGGER f01_field_state_dml_guard;

  -- The value_digest COLUMN moves with the envelope. f01_state_binding ties the
  -- two together, and it is not one of the digest constraints this section
  -- lifts: tampering only the envelope would be refused by the binding CHECK
  -- before the readback under test ever saw the row.
  UPDATE ops.f01_field_state
     SET envelope = jsonb_set(envelope, '{record,value_digest}',
                              to_jsonb('sha256:' || repeat('ee', 32))),
         value_digest = 'sha256:' || repeat('ee', 32)
   WHERE entity = 'deal' AND field = 'commission_amount';

  -- The verification helper is not principal-bound, so this half is provable
  -- from here.
  PERFORM pg_temp.f01_expect_refusal(
    $$SELECT ops.f01_current_field_state('deal', 'commission_amount')$$,
    'f01_corrupt_stored_record', 'corruption',
    'a tampered current state refuses instead of reading back');
END;
$corrupt_setup$;

SET SESSION AUTHORIZATION carr_writer;

DO $corrupt_read$
BEGIN
  PERFORM pg_temp.f01_expect_refusal(
    $$SELECT ops.f01_read('field_state', '{"entity":"deal","field":"commission_amount"}'::jsonb)$$,
    'f01_corrupt_stored_record', 'corruption',
    'the read tool refuses rather than falling back to an older healthy row');
  -- AND IT MUST NOT SILENTLY ANSWER FROM HISTORY. The append-only events are
  -- intact, but a corrupt current row does not become a lookup through them.
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM jsonb_array_elements(
       ops.f01_read('field_events', '{"entity":"deal","field":"commission_amount"}'::jsonb)
         -> 'body')) = 2,
    'corruption', 'the intact event history is still readable on its own terms');
END;
$corrupt_read$;

RESET SESSION AUTHORIZATION;

DO $corrupt_restore$
BEGIN
  -- Restore the row and the guards so the rest of the fixture is honest.
  UPDATE ops.f01_field_state
     SET envelope = jsonb_set(envelope, '{record,value_digest}',
                              to_jsonb('sha256:' || repeat('02', 32))),
         value_digest = 'sha256:' || repeat('02', 32)
   WHERE entity = 'deal' AND field = 'commission_amount';
  ALTER TABLE ops.f01_field_state ENABLE TRIGGER f01_field_state_dml_guard;
  ALTER TABLE ops.f01_field_state
    ADD CONSTRAINT f01_state_envelope_digest
    CHECK (envelope_digest = ops.f01_digest_jsonb(envelope));
  ALTER TABLE ops.f01_field_state
    ADD CONSTRAINT f01_state_record_digest
    CHECK (state_digest = ops.f01_digest_jsonb(envelope -> 'record')
           AND state_digest = envelope ->> 'record_digest');
  PERFORM pg_temp.f01_assert_eq(
    ops.f01_current_field_state('deal', 'commission_amount') ->> 'state_digest',
    pg_temp.f01_recall('state_before_corruption'),
    'corruption', 'the restored state reads back exactly as before');
  -- Both guards are back on, which the race request in section 16 depends on.
  PERFORM pg_temp.f01_assert(
    (SELECT tgenabled FROM pg_trigger
      WHERE tgrelid = 'ops.f01_field_state'::regclass
        AND tgname = 'f01_field_state_dml_guard') = 'O',
    'corruption', 'the direct-DML guard is re-enabled');
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM pg_constraint
      WHERE conrelid = 'ops.f01_field_state'::regclass
        AND conname IN ('f01_state_record_digest', 'f01_state_envelope_digest')) = 2,
    'corruption', 'both digest constraints are restored');
END;
$corrupt_restore$;

COMMIT;

-- ===========================================================================
-- 15. Legacy compatibility — the AFTER snapshot.
-- ===========================================================================

INSERT INTO f01_legacy_snapshot (phase, table_name, fingerprint)
SELECT 'after', t, pg_temp.f01_legacy_fingerprint(t)
  FROM unnest(ARRAY['record_source', 'document']) AS t;

SELECT pg_temp.f01_assert(
  NOT EXISTS (
    SELECT 1 FROM f01_legacy_snapshot b
      JOIN f01_legacy_snapshot a ON a.table_name = b.table_name AND a.phase = 'after'
     WHERE b.phase = 'before' AND b.fingerprint IS DISTINCT FROM a.fingerprint),
  'legacy', 'public.record_source and public.document are unchanged in every respect');

SELECT pg_temp.f01_assert(
  NOT EXISTS (
    SELECT 1 FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.confrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
      JOIN pg_class s ON s.oid = c.conrelid
      JOIN pg_namespace sn ON sn.oid = s.relnamespace
     WHERE sn.nspname = 'ops' AND s.relname LIKE 'f01\_%'
       AND n.nspname = 'public'),
  'legacy', 'no F01 relation takes a foreign key on a legacy table');

-- ===========================================================================
-- 16. THE RACE REQUEST — handed to the gate, because a single session cannot
--     prove a race against itself.
--
-- Everything above runs in one connection, so every compare-and-swap above is
-- decided against a database nobody else is touching. That proves the CAS is
-- CORRECT; it cannot prove it is DECISIVE. Two sessions arriving at the same
-- moment with two different, individually valid mutations of the same record is
-- the case that matters, and it needs two connections.
--
-- So this section emits the two calls rather than making them. They are:
--   * BOTH VALID. Either one, run alone against the state left by section 14,
--     would be accepted — correct sequence 3 off event_2, correct previous-event
--     digest, transition starting from the stored value, receipt binding both.
--     Neither is a straw man that would have failed anyway.
--   * IDENTICAL IN THEIR COMPARE-AND-SWAP. Same expected policy digest, same
--     expected current-state digest (state_v2). That is what makes them
--     competitors rather than a sequence.
--   * DIFFERENT IN EVERYTHING A WINNER WOULD WRITE. Different target value,
--     different version, different idempotency key — so the winner cannot be
--     mistaken for a replay of the loser, and the idempotency ledger cannot
--     quietly resolve the race instead of the CAS.
--
-- WHAT THE GATE MUST OBSERVE: exactly one accepted, and the other refused with
-- f01_stale_current_state. Not both accepted; not both refused; not one accepted
-- and one refused for some other reason. The advisory field lock serializes the
-- two, and the CAS — not the unique index, which would refuse with a constraint
-- name — is what decides the loser.
--
-- HOW TO RUN THEM: two concurrent sessions, each `SET SESSION AUTHORIZATION
-- carr_writer` with carr.acting_actor_slug set, each issuing
--   SELECT ops.f01_apply_observation(<the twelve arguments, in order>);
-- No pg_temp helper needs to exist in those sessions — pg_temp does not survive
-- the connection that created it — so every argument is emitted as a VALUE that
-- stands on its own.
--
-- THE TRANSPORT SHAPE IS RAW JSON VALUES, AND THE GATE DOES THE QUOTING. Each
-- element of the emitted array is the argument itself: a JSON string for a text
-- parameter, a JSON object for a jsonb parameter, JSON null for the absent
-- reconciliation item. NOT a pre-rendered SQL fragment.
--
-- An earlier revision of this file emitted quote_literal(...) fragments and
-- '{...}'::jsonb casts. The gate's race_statement consumes values and quotes
-- them itself — that quoting is the injection boundary and it is adversarially
-- tested there — so the pre-rendered form was quoted a second time: 'accept'
-- arrived as the seven-character string with its own quotes attached, matched
-- no transition, and the concurrency proof (the one thing in this whole slice
-- that only a real database can establish) never ran at all. One side has to own
-- the quoting; it is the side with the tests for it.
--
-- The assertions below pin the shape ELEMENT BY ELEMENT, by jsonb_typeof, so a
-- reversion to pre-rendered fragments fails here, in the file that changed,
-- rather than as a puzzling refusal in the gate's race harness.
-- ===========================================================================

CREATE FUNCTION pg_temp.f01_race_args(p_to_value text, p_to_version integer, p_key text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_event jsonb := pg_temp.f01_event_record(3, pg_temp.f01_recall('event_2'), p_to_version);
  v_event_digest text := ops.f01_digest_jsonb(v_event);
  v_transition jsonb := pg_temp.f01_transition_record(
                          'sha256:' || repeat('02', 32), p_to_value, 6, p_to_version);
  v_transition_digest text := ops.f01_digest_jsonb(v_transition);
  v_receipt jsonb := pg_temp.f01_receipt_record(v_transition_digest, v_event_digest,
                                                'owner_value_updated');
  v_state jsonb := pg_temp.f01_state_record(p_to_value, p_to_version, 3, v_event_digest);
BEGIN
  RETURN jsonb_build_array(
    'accept',
    'deal',
    'commission_amount',
    ops.f01_current_policy_digest(),
    pg_temp.f01_recall('state_v2'),
    pg_temp.f01_envelope('stored_field_state', v_state),
    pg_temp.f01_envelope('stored_state_transition', v_transition,
      '{"alone_sufficient":false}'::jsonb),
    pg_temp.f01_envelope('stored_source_event', v_event,
      '{"alone_sufficient":false}'::jsonb),
    pg_temp.f01_envelope('stored_mutation_receipt', v_receipt,
      '{"alone_sufficient":false,"binds_transition_and_event":true}'::jsonb),
    -- p_reconciliation: an accepted transition writes no reconciliation item.
    -- jsonb_build_array renders this as JSON null, which the gate maps to a bare
    -- SQL NULL. The string 'NULL' would arrive as the four-character text value.
    -- The cast only settles the type of an untyped NULL; the emitted element is
    -- JSON null either way.
    null::text,
    p_key,
    ops.f01_digest_jsonb(jsonb_build_object('key', p_key)));
END;
$$;

SET SESSION AUTHORIZATION carr_writer;

-- The emitted pair is checked here for the two properties the gate relies on,
-- because a race request that quietly stopped being a race would turn the gate
-- green while proving nothing.
DO $race_shape$
DECLARE
  v_a jsonb := pg_temp.f01_race_args('sha256:' || repeat('0a', 32), 7, 'syn-gate-race-a');
  v_b jsonb := pg_temp.f01_race_args('sha256:' || repeat('0b', 32), 8, 'syn-gate-race-b');
  v_racer jsonb;
  v_types text;
BEGIN
  PERFORM pg_temp.f01_assert(
    jsonb_array_length(v_a) = 12 AND jsonb_array_length(v_b) = 12,
    'race', 'each racer carries the twelve required arguments');

  -- THE EXACT TRANSPORT SHAPE, position by position. This is the assertion that
  -- would have caught the pre-rendered-SQL revision: a quote_literal() fragment
  -- is a JSON *string* where an object belongs, and 'NULL' is a JSON string
  -- where JSON null belongs, so the type vector alone distinguishes the two
  -- encodings completely.
  FOREACH v_racer IN ARRAY ARRAY[v_a, v_b] LOOP
    SELECT string_agg(jsonb_typeof(e), ',' ORDER BY o)
      INTO v_types
      FROM jsonb_array_elements(v_racer) WITH ORDINALITY AS x(e, o);
    PERFORM pg_temp.f01_assert_eq(v_types,
      'string,string,string,string,string,object,object,object,object,null,string,string',
      'race', 'the emitted request is raw JSON values: five text arguments, four envelope '
              'objects, a JSON null reconciliation item, then key and request digest');
    -- And no element is a pre-rendered SQL fragment: no leading quote, no cast.
    PERFORM pg_temp.f01_assert(
      NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_racer) AS x(e)
                   WHERE jsonb_typeof(x.e) = 'string'
                     AND ((x.e #>> '{}') LIKE '''%' OR (x.e #>> '{}') LIKE '%::jsonb')),
      'race', 'no emitted argument carries SQL quoting or a cast; the gate owns the quoting');
  END LOOP;

  PERFORM pg_temp.f01_assert_eq(v_a ->> 0, 'accept',
    'race', 'the decision arrives as the bare word the writer compares against');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 1) = 'deal' AND (v_a ->> 2) = 'commission_amount'
    AND (v_a -> 9) = 'null'::jsonb,
    'race', 'entity, field and the absent reconciliation item are transported unquoted');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 3) = (v_b ->> 3) AND (v_a ->> 4) = (v_b ->> 4),
    'race', 'both racers name the SAME policy and current-state compare-and-swap');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 4) = pg_temp.f01_recall('state_v2'),
    'race', 'the shared compare-and-swap is the state section 14 restored');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 3) = ops.f01_current_policy_digest(),
    'race', 'the emitted policy compare-and-swap is the digest the database holds now');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 10) <> (v_b ->> 10) AND (v_a ->> 11) <> (v_b ->> 11),
    'race', 'the racers carry different idempotency keys, so neither replays the other');
  PERFORM pg_temp.f01_assert(
    (v_a ->> 5) <> (v_b ->> 5) AND (v_a ->> 7) <> (v_b ->> 7),
    'race', 'the racers propose genuinely different state and events');
  -- Nothing has been written by building them.
  PERFORM pg_temp.f01_assert(
    (SELECT count(*) FROM ops.f01_field_event) = 2
    AND ops.f01_current_field_state('deal', 'commission_amount') ->> 'state_digest'
        = pg_temp.f01_recall('state_v2'),
    'race', 'emitting the race request writes nothing and moves no state');
END;
$race_shape$;

-- ONE UNDECORATED LINE, via a SELECT rather than \echo. psql interpolates
-- variables into \echo arguments, and these digests are full of ":" followed by
-- text; query OUTPUT is never interpolated, so this is the safe channel.
\pset format unaligned
\pset tuples_only on

SELECT 'F01_RACE_REQUEST=' || jsonb_build_object(
  'race_a', pg_temp.f01_race_args('sha256:' || repeat('0a', 32), 7, 'syn-gate-race-a'),
  'race_b', pg_temp.f01_race_args('sha256:' || repeat('0b', 32), 8, 'syn-gate-race-b'))::text;

\pset tuples_only off
\pset format aligned

RESET SESSION AUTHORIZATION;

-- ===========================================================================
-- 17. Summary. A run with no failures prints one row per assertion.
-- ===========================================================================

SELECT section, count(*) FILTER (WHERE outcome = 'passed') AS passed,
       count(*) FILTER (WHERE outcome = 'refused') AS refusals_proved,
       count(*) AS total
  FROM f01_fixture_log
 GROUP BY section
 ORDER BY section;

SELECT count(*) AS f01_fixture_assertions FROM f01_fixture_log;
