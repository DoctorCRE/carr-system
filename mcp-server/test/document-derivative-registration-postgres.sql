-- V5-F01 — the DOCUMENT half of the derivative-registration rule: PostgreSQL
-- fixtures for ops/document-derivative-registration.candidate.sql.
--
-- HOW TO RUN THIS. Never by hand against anything that matters. It is applied by
-- ops/record-source-authority-local-pg-gate.py against a DISPOSABLE local
-- database, after domain.sql AND after the candidate hunk this file exercises.
-- It creates no role, reads no credential, reaches no provider, touches no legacy
-- row and installs no policy.
--
-- IT HAS NOT BEEN RUN. This change writes the fixture; it does not execute it,
-- and it makes no claim that the candidate applies cleanly or that these
-- assertions pass. The gate is the parent's to point at both files.
--
-- TWO INTEGRATION FACTS the parent has to settle before this file can run at all.
--
--   1. THE GATE TAKES A SINGLE --fixture PATH (run_verified reads args.fixture,
--      not a list), so this file is either a second gate invocation or the parent
--      makes --fixture repeatable. This change does not edit the gate.
--
--   2. TWO ENVELOPE DIGESTS MUST BE SUPPLIED, and this file refuses without them.
--      Every envelope the Node store writes carries `domain_policy_digest` and
--      `decision_subset_digest` — v5F01PolicyDigest() and
--      v5F01DecisionSubsetDigest() from the reviewed kernel, hashed over the
--      settled contract itself. They are part of the bytes the envelope digest is
--      computed from, so a fixture that omitted them would exercise the writers
--      with an envelope shape the store NEVER produces, and every digest it
--      compared would be a digest of the wrong preimage. This file cannot compute
--      them: reproducing the kernel's contract preimage in SQL would be a second
--      copy of the thing the digest exists to pin. So they are passed in:
--
--        node --input-type=module -e 'import {v5F01PolicyDigest,
--          v5F01DecisionSubsetDigest} from "./mcp-server/src/record-source-authority.v5.js";
--          console.log(v5F01PolicyDigest(), v5F01DecisionSubsetDigest())'
--
--        psql ... -v f01ds_domain_policy_digest=sha256:... \
--                 -v f01ds_decision_subset_digest=sha256:... -f <this file>
--
--      A missing or malformed value BLOCKS rather than defaulting. A default here
--      would be a fixture that ran green while proving something about bytes
--      nobody stores, which is worse than a fixture that does not run.
--
-- WHAT IT PROVES, and what it deliberately leaves elsewhere:
--
--   HERE  Structure. That a document version cannot be completed without a
--         statement about where it came from; that a DERIVED one cannot be
--         completed without its ops.f01_derivative_link row; that a forged
--         source, an unknown artifact, a mismatched document and a repointing
--         attempt each refuse by name; that the PUBLIC registration surface
--         refuses an 'f01_document_version' link outright, so a document
--         version's provenance can be neither pre-claimed before the document
--         exists nor repointed after it does, while the genuine
--         one-derivative-one-original conflict still fires for the kinds that
--         still reach it; that free text is held to the same bounds, alphabet,
--         canonical form and UTF-16 code-unit count the Node module holds it to;
--         that an original and a legacy-unknown document are stored as different
--         things; that nothing here moves coverage off 'unknown'; and that the
--         guards, the compare-and-swap, the append-only history and the
--         idempotency ledger all still hold.
--
--   NODE  The decision matrix, byte for byte, in
--         mcp-server/test/document-derivative-registration.v5.test.mjs. The two
--         suites assert the same refusal NAMES from opposite sides, so a drift on
--         either side fails both.
--
--   GATE  Independence — the byte-for-byte agreement between
--         ops.f01_canonical_json and the Node canonicaliser is asserted by the
--         gate against bytes it computed in Node, and is not re-derived here.
--
-- WHO RUNS WHAT:
--   the applying superuser   0-2, 8, 9 (the guard halves), 10
--   carr_writer              3-7 (artifact, documents, refusals, replay)
--   carr_reader              9.3
--
-- Section 8 runs as the applying superuser on purpose: a principal with no DML
-- grant proves nothing by being refused DML, so the guards are tested by an
-- identity that could otherwise have written.
--
-- EVERY VALUE IS SYNTHETIC. "synthetic-", "SYNTHETIC-" and the repeated-digit
-- digests are unmistakably test data. No real document, account, native id,
-- object key, drive item or workflow appears anywhere.

\set ON_ERROR_STOP on
\timing off

-- The two envelope digests, or a value that cannot be mistaken for one. The check
-- that turns 'unset' into a hard stop is at the foot of section 1, where the
-- fixture's own helpers exist to report it.
\if :{?f01ds_domain_policy_digest}
\else
\set f01ds_domain_policy_digest 'unset'
\endif
\if :{?f01ds_decision_subset_digest}
\else
\set f01ds_decision_subset_digest 'unset'
\endif

-- ===========================================================================
-- 0. Bootstrap, or block. There is no third option.
-- ===========================================================================

DO $bootstrap$
DECLARE
  v_missing text[] := ARRAY[]::text[];
  r text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper) THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: % is not a superuser, so this fixture cannot SET SESSION AUTHORIZATION to the real principals. It will not impersonate them with a GUC instead.', current_user;
  END IF;

  IF to_regnamespace('ops') IS NULL OR to_regprocedure('ops.f01_read(text,jsonb)') IS NULL THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: the F01 domain schema is not installed here. Apply domain.sql first.';
  END IF;

  -- THE CANDIDATE MUST ALREADY BE APPLIED. This fixture tests a forward
  -- replacement; against the un-replaced schema every assertion below would
  -- either fail for the wrong reason or pass vacuously.
  IF to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') IS NULL THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: the six-argument ops.f01_record_document is absent. Apply ops/document-derivative-registration.candidate.sql (or the successor migration carrying it) first.';
  END IF;
  IF to_regclass('ops.f01_document_source_provenance') IS NULL THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: ops.f01_document_source_provenance is absent.';
  END IF;

  FOREACH r IN ARRAY ARRAY['carr_reader','carr_writer','carr_authority_joe','carr_authority_dell'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN v_missing := v_missing || r; END IF;
  END LOOP;
  IF cardinality(v_missing) > 0 THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: missing principal role(s): %. This fixture creates no role.',
      array_to_string(v_missing, ', ');
  END IF;

  -- A carr_* role that is itself a superuser would pass every refusal test for
  -- the wrong reason, so it blocks here rather than producing a green run.
  SELECT array_agg(rolname ORDER BY rolname) INTO v_missing
    FROM pg_roles
   WHERE rolname IN ('carr_reader','carr_writer','carr_authority_joe','carr_authority_dell')
     AND rolsuper;
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: runtime principal(s) % are superusers; every least-privilege assertion below would pass vacuously.',
      array_to_string(v_missing, ', ');
  END IF;
END;
$bootstrap$;

CREATE TEMP TABLE f01ds_log (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  section text NOT NULL,
  label   text NOT NULL,
  outcome text NOT NULL,
  detail  text
);
CREATE TEMP TABLE f01ds_state (key text PRIMARY KEY, value text);

-- Owned by the bootstrap superuser and written from every switched identity
-- below; without these grants the first assertion made as carr_writer would fail
-- on the log rather than on the thing it was testing.
GRANT SELECT, INSERT, UPDATE ON f01ds_log, f01ds_state TO PUBLIC;

-- ===========================================================================
-- 1. Fixture helpers. Defined while still the applying superuser, before any
--    section switches identity, so none of them depends on a runtime role
--    holding TEMP privilege.
-- ===========================================================================

CREATE FUNCTION pg_temp.f01ds_note(p_section text, p_label text, p_outcome text,
                                   p_detail text DEFAULT NULL)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO f01ds_log (section, label, outcome, detail)
  VALUES (p_section, p_label, p_outcome, p_detail);
$$;

CREATE FUNCTION pg_temp.f01ds_assert(p_condition boolean, p_section text, p_label text,
                                     p_detail text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF p_condition IS NOT TRUE THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE FAILED [%] %: %',
      p_section, p_label, coalesce(p_detail, '');
  END IF;
  PERFORM pg_temp.f01ds_note(p_section, p_label, 'passed', p_detail);
END;
$$;

CREATE FUNCTION pg_temp.f01ds_assert_eq(p_actual text, p_expected text,
                                        p_section text, p_label text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF p_actual IS DISTINCT FROM p_expected THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE FAILED [%] %: expected %, got %',
      p_section, p_label, coalesce(p_expected, '<null>'), coalesce(p_actual, '<null>');
  END IF;
  PERFORM pg_temp.f01ds_note(p_section, p_label, 'passed', p_expected);
END;
$$;

/**
 * Run one statement that MUST refuse, and require the refusal to be the named
 * one. A statement that succeeds is a failure, and so is one that fails for a
 * different reason — a refusal matrix that accepts any error is not one.
 */
CREATE FUNCTION pg_temp.f01ds_expect_refusal(p_sql text, p_fragment text,
                                             p_section text, p_label text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_message text;
BEGIN
  BEGIN
    EXECUTE p_sql;
  EXCEPTION WHEN OTHERS THEN
    v_message := SQLERRM;
    IF strpos(v_message, p_fragment) = 0 THEN
      RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE FAILED [%] %: expected refusal containing "%", got "%"',
        p_section, p_label, p_fragment, v_message;
    END IF;
    PERFORM pg_temp.f01ds_note(p_section, p_label, 'refused', v_message);
    RETURN;
  END;
  RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE FAILED [%] %: the statement SUCCEEDED and should have refused',
    p_section, p_label;
END;
$$;

CREATE FUNCTION pg_temp.f01ds_remember(p_key text, p_value text) RETURNS void
LANGUAGE sql AS $$
  INSERT INTO f01ds_state (key, value) VALUES (p_key, p_value)
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
$$;

CREATE FUNCTION pg_temp.f01ds_recall(p_key text) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT value FROM f01ds_state WHERE key = p_key;
$$;

CREATE FUNCTION pg_temp.f01ds_actor() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT ops.f01_principal() ->> 'actor_slug';
$$;

-- THE TWO SUPPLIED DIGESTS, remembered before anything builds an envelope. The
-- interpolation happens HERE, in an ordinary statement, because psql does not
-- substitute variables inside a dollar-quoted function body — so the values reach
-- f01ds_envelope through the state table rather than through its source.
SELECT pg_temp.f01ds_remember('domain_policy_digest', :'f01ds_domain_policy_digest');
SELECT pg_temp.f01ds_remember('decision_subset_digest', :'f01ds_decision_subset_digest');

DO $envelope_digests$
BEGIN
  IF pg_temp.f01ds_recall('domain_policy_digest') !~ '^sha256:[0-9a-f]{64}$'
     OR pg_temp.f01ds_recall('decision_subset_digest') !~ '^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: -v f01ds_domain_policy_digest and -v f01ds_decision_subset_digest must both be supplied as sha256 references. Every envelope the Node store writes carries them, they are part of the bytes the envelope digest is computed over, and this fixture will not exercise the writers with an envelope shape the store never produces. Got % and %.',
      pg_temp.f01ds_recall('domain_policy_digest'),
      pg_temp.f01ds_recall('decision_subset_digest');
  END IF;
  -- Two different contracts, two different digests. One value pasted into both
  -- slots is a copy-paste, not a pinning.
  IF pg_temp.f01ds_recall('domain_policy_digest')
       = pg_temp.f01ds_recall('decision_subset_digest') THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE BLOCKED: the domain policy digest and the decision subset digest are the same value; they are digests of different preimages and one of them is wrong.';
  END IF;
  PERFORM pg_temp.f01ds_note('bootstrap', 'the two envelope digests were supplied by the caller',
    'passed', pg_temp.f01ds_recall('domain_policy_digest'));
END;
$envelope_digests$;

/**
 * A stored-record envelope, built exactly as the Node store's storeEnvelope()
 * builds one — the same seven keys, in the same meaning, with the caller-supplied
 * extras merged last exactly as `...extra` is spread last there.
 */
CREATE FUNCTION pg_temp.f01ds_envelope(p_kind text, p_record jsonb,
                                       p_extra jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-record-envelope.v1',
    'record_kind', p_kind,
    'tenant', 'carr-internal',
    'record', p_record,
    'record_digest', ops.f01_digest_jsonb(p_record),
    'domain_policy_digest', pg_temp.f01ds_recall('domain_policy_digest'),
    'decision_subset_digest', pg_temp.f01ds_recall('decision_subset_digest')
  ) || p_extra;
$$;

/** The three claims a provenance-bearing envelope must carry. */
CREATE FUNCTION pg_temp.f01ds_claims() RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT '{"establishes_coverage":false,"is_exhaustive_inventory":false,
           "permits_deletion":false,"deletes_nothing":true}'::jsonb;
$$;

-- The synthetic corporate artifact, shaped exactly like the reviewed kernel's
-- admitted-artifact preimage. TEST DATA, naming nothing real.
CREATE FUNCTION pg_temp.f01ds_artifact_record(p_content text, p_native_version text)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-corporate-artifact.v1',
    'tenant', 'carr-internal',
    'source_system', 'onedrive',
    'source_class', 'synthetic_test_lease',
    'source_account', 'synthetic-account-0001',
    'native_identity', jsonb_build_object(
      'source_system', 'onedrive',
      'native_id', 'SYNTHETIC-LEASE-' || p_native_version,
      'native_id_epoch', 'synthetic-epoch-1'),
    'native_version', p_native_version,
    'content_digest', p_content,
    'byte_length', 4096,
    'observed_at', '2026-09-05T09:00:00Z',
    'provenance', jsonb_build_object(
      'adapter_kind', 'synthetic_test_adapter',
      'evidence_ref', 'synthetic-evidence-0090',
      'retrieval_class', 'corporate_record_export'),
    'evidence_class', 'corporate_record_export',
    'declared_data_classes', jsonb_build_array('lease_economics'),
    'taint_class', 'corporate_source_of_record');
$$;

/** One stored document version, shaped exactly like storedDocumentRecord(). */
CREATE FUNCTION pg_temp.f01ds_document_record(
  p_document_id text, p_version integer, p_content text,
  p_prior text, p_recorded_at text)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-document-version.v1',
    'document_identity_schema_version', 'doctorcre-v5-f01-document-identity.v1',
    'tenant', 'carr-internal',
    'document_class', 'synthetic_test_lease_abstract',
    'neon_identity', jsonb_build_object(
      'document_id', p_document_id,
      'content_digest', p_content,
      'version_no', p_version),
    'object_storage_identity', NULL,
    'onedrive_identity', NULL,
    'preparation_state', 'drafting',
    'delivery_state', 'undelivered',
    'signature_state', 'unsigned',
    'validity_state', 'draft',
    'version_state', 'current',
    'official_filing_state', 'not_required',
    'prior_document_digest', p_prior,
    'homes', jsonb_build_object(
      'identity_and_state', 'neon_record_layer',
      'working_and_sealed_bytes', 'object_storage',
      'official_executed_copy', 'onedrive'),
    'recorded_by', pg_temp.f01ds_actor(),
    'recorded_at', p_recorded_at);
$$;

/** One derivative-source link, shaped exactly like storedDerivativeLinkRecord(). */
CREATE FUNCTION pg_temp.f01ds_link_record(
  p_source text, p_document_id text, p_version integer, p_document_digest text,
  p_workflow text, p_run_ref text, p_at text)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'schema_version', 'doctorcre-v5-f01-stored-derivative-source-link.v1',
    'derivative_link_schema_version', 'doctorcre-v5-f01-derivative-source-link.v1',
    'tenant', 'carr-internal',
    'source_artifact_digest', p_source,
    'derivative_kind', 'f01_document_version',
    'derivative_id', p_document_id || ':' || p_version::text,
    'derivative_content_digest', p_document_digest,
    'producer_workflow', p_workflow,
    'producer_run_ref', p_run_ref,
    'produced_at', p_at,
    'evidence_ref', 'stored_document_version',
    'evidence_digest', p_document_digest,
    'registration_is_provenance', true,
    'is_exhaustive_inventory', false,
    'establishes_coverage', false,
    'permits_deletion', false,
    'registered_by', pg_temp.f01ds_actor(),
    'registered_at', p_at);
$$;

/**
 * One derivative-source link for a kind this schema does NOT produce itself.
 *
 * WHY IT EXISTS. The public registration surface is for producers OUTSIDE this
 * contract, and after the candidate it refuses both kinds inside it. Proving that
 * the genuine one-derivative-one-original conflict still fires therefore needs a
 * kind the reservation does not cover — otherwise the only thing section 7 would
 * prove is that the reservation refuses everything, which is not the same
 * property and would hide a broken conflict check.
 */
CREATE FUNCTION pg_temp.f01ds_external_link_record(
  p_kind text, p_id text, p_source text, p_content text,
  p_workflow text, p_run_ref text, p_at text)
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
    'producer_run_ref', p_run_ref,
    'produced_at', p_at,
    'evidence_ref', 'synthetic-external-evidence',
    'evidence_digest', p_content,
    'registration_is_provenance', true,
    'is_exhaustive_inventory', false,
    'establishes_coverage', false,
    'permits_deletion', false,
    'registered_by', pg_temp.f01ds_actor(),
    'registered_at', p_at);
$$;

/**
 * One provenance statement, shaped exactly like
 * storedDocumentSourceProvenanceRecord(). The derived and non-derived shapes are
 * built by the SAME function on purpose: the halves that must be null in one case
 * and present in the other are visible side by side, so a fixture that filled in
 * the wrong half would be obvious rather than subtle.
 */
CREATE FUNCTION pg_temp.f01ds_provenance_record(
  p_document_id text, p_version integer, p_document_digest text, p_state text,
  p_source text, p_link_digest text, p_workflow text, p_run_ref text,
  p_basis text, p_recorded_at text)
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
    'producer_run_ref', p_run_ref,
    'basis_statement', p_basis,
    'registration_is_provenance', true,
    'source_artifact_inferred_from_document_bytes', false,
    'source_artifact_inferred_from_onedrive_identity', false,
    'is_exhaustive_inventory', false,
    'establishes_coverage', false,
    'permits_deletion', false,
    'recorded_by', pg_temp.f01ds_actor(),
    'recorded_at', p_recorded_at);
$$;

-- ===========================================================================
-- 2. The forward replacement is real: the old door is gone.
-- ===========================================================================

SELECT pg_temp.f01ds_assert(
  to_regprocedure('ops.f01_record_document(jsonb,text,text,text)') IS NULL,
  'replacement', 'the four-argument ops.f01_record_document no longer exists',
  'an overload would leave a path that completes a derived document with no source statement');

SELECT pg_temp.f01ds_assert(
  to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') IS NOT NULL
  AND to_regprocedure('ops.f01_insert_document_source_provenance(jsonb,text,text)') IS NOT NULL
  AND to_regprocedure('ops.f01_document_version_source(text,integer)') IS NOT NULL
  AND to_regprocedure('ops.f01_document_source_history(text)') IS NOT NULL
  AND to_regprocedure('ops.f01_docsource_utf16_length(text)') IS NOT NULL
  AND to_regprocedure('ops.f01_docsource_is_safe_text(text,integer)') IS NOT NULL
  AND to_regprocedure('ops.f01_docsource_is_external_ident(text,integer)') IS NOT NULL,
  'replacement', 'the six-argument writer, the private inserter, both readers and the text helpers exist');

-- THE PRE-CLAIM IS CLOSED AT THE SOURCE. Both internally produced derivative kinds
-- are reserved, and the public registration surface still consults the list. A
-- replacement that named only the new kind would have reopened the parsed-proposal
-- hole the shipped guard was written for, so both are asserted.
SELECT pg_temp.f01ds_assert(
  'f01_parsed_proposal' = ANY (ops.f01_reserved_derivative_kinds())
  AND 'f01_document_version' = ANY (ops.f01_reserved_derivative_kinds())
  AND pg_get_functiondef('ops.f01_register_derivative_link(jsonb,text,text)'::regprocedure)
        LIKE '%f01_reserved_derivative_kinds()%',
  'replacement', 'both internally produced derivative kinds are reserved against the public surface',
  'a document version''s derivative identity is its own name and version, so it is predictable before the document exists');

-- 2.1 THE UTF-16 ARITHMETIC, pinned against values a reader can check by eye.
--
-- This is the number JavaScript's String.prototype.length reports, which is what
-- the Node module bounds its free text by. PostgreSQL's own length() counts CODE
-- POINTS and disagrees for exactly the astral characters most likely to be pasted
-- into a basis statement; a bound written with length() would be silently weaker
-- than the module's for that text and only for that text.
DO $utf16$
DECLARE
  v_astral text := chr(128450);   -- U+1F5C2, one code point, TWO UTF-16 code units
  v_bmp    text := chr(233);      -- U+00E9, one of each
BEGIN
  PERFORM pg_temp.f01ds_assert_eq(ops.f01_docsource_utf16_length(v_bmp)::text, '1',
    'utf16', 'a BMP character is one code unit');
  PERFORM pg_temp.f01ds_assert_eq(ops.f01_docsource_utf16_length(v_astral)::text, '2',
    'utf16', 'an astral character is TWO code units, where length() would say one');
  PERFORM pg_temp.f01ds_assert_eq(length(v_astral)::text, '1',
    'utf16', 'and length() does say one, which is the whole reason the helper exists');
  PERFORM pg_temp.f01ds_assert_eq(
    ops.f01_docsource_utf16_length('a' || v_astral || 'b')::text, '4',
    'utf16', 'mixed text adds up the same way it does in Node');
  -- The bound the module enforces, at and one past the edge.
  PERFORM pg_temp.f01ds_assert(
    ops.f01_docsource_is_safe_text(repeat('x', 510) || v_astral, 512)
    AND NOT ops.f01_docsource_is_safe_text(repeat('x', 511) || v_astral, 512),
    'utf16', '512 code units is the bound on both sides of the seam');
  -- And the rest of the rules the module applies to the same string.
  PERFORM pg_temp.f01ds_assert(
    NOT ops.f01_docsource_is_safe_text('e' || chr(769), 512)
    AND NOT ops.f01_docsource_is_safe_text('a' || chr(8203) || 'b', 512)
    AND NOT ops.f01_docsource_is_safe_text(chr(160) || 'leading', 512)
    AND NOT ops.f01_docsource_is_safe_text('trailing' || chr(8195), 512)
    AND NOT ops.f01_docsource_is_safe_text('', 512)
    AND ops.f01_docsource_is_safe_text('an ordinary synthetic basis statement', 512),
    'utf16', 'non-NFC, invisible, untrimmed and empty free text are refused in SQL too');
  PERFORM pg_temp.f01ds_assert(
    ops.f01_docsource_is_external_ident('syn_test_document_producer', 128)
    AND NOT ops.f01_docsource_is_external_ident('has a space', 128)
    AND NOT ops.f01_docsource_is_external_ident('_leading_underscore', 128)
    AND NOT ops.f01_docsource_is_external_ident(repeat('w', 129), 128),
    'utf16', 'the identifier alphabet is the kernel''s, character for character');
END;
$utf16$;

-- The direct-DML guard kept every writer it already named AND gained the new one.
SELECT pg_temp.f01ds_assert(
  (SELECT bool_and(pg_get_functiondef('ops.f01_guard_direct_dml()'::regprocedure) LIKE '%' || w || '%')
     FROM unnest(ARRAY['install_policy','apply_observation','record_artifact','record_proposal',
                       'record_document','record_hold','record_deletion_evaluation',
                       'register_derivative_link','insert_derivative_link',
                       'insert_document_source_provenance',
                       'claim_idempotency','settle_idempotency']) AS w),
  'replacement', 'the replaced direct-DML guard names every old writer and the new one');

-- The private inserter is reachable by nobody at runtime, exactly like
-- ops.f01_insert_derivative_link. This ALSO catches the wrong application order:
-- domain.sql's by-name private list does not know the new helper, so a re-apply
-- after the candidate would grant it and fail here.
DO $private$
DECLARE v_case record;
BEGIN
  FOR v_case IN
    SELECT * FROM (VALUES
      ('carr_reader', 'f01_insert_document_source_provenance'),
      ('carr_writer', 'f01_insert_document_source_provenance'),
      ('carr_authority_joe', 'f01_insert_document_source_provenance'),
      ('carr_authority_dell', 'f01_insert_document_source_provenance'),
      -- A read-only principal is not a producer workflow, and completing a
      -- document version now registers a derivative.
      ('carr_reader', 'f01_record_document')
    ) AS t(role_name, fn)
  LOOP
    PERFORM pg_temp.f01ds_assert(
      NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'ops' AND p.proname = v_case.fn
                     AND has_function_privilege(v_case.role_name, p.oid, 'EXECUTE')),
      'replacement', format('%s may not execute ops.%s', v_case.role_name, v_case.fn));
  END LOOP;

  -- And the positive half, so a grant loop that quietly granted NOTHING fails too.
  PERFORM pg_temp.f01ds_assert(
    has_function_privilege('carr_writer',
      'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE')
    AND has_function_privilege('carr_reader',
      'ops.f01_document_version_source(text,integer)', 'EXECUTE')
    AND has_table_privilege('carr_reader', 'ops.f01_document_source_provenance', 'SELECT'),
    'replacement', 'the writer keeps its producer, and the reader keeps its reads');

  -- The three pure text helpers are NOT private: they decide nothing and hold
  -- nothing, and the local gate expects every non-private, non-guard ops.f01_*
  -- function to be reachable by all four principals. A helper the candidate forgot
  -- to grant would fail the gate against an otherwise correct schema, so it fails
  -- here first, where the message says which one.
  FOR v_case IN
    SELECT * FROM (VALUES
      ('carr_reader', 'ops.f01_docsource_utf16_length(text)'),
      ('carr_reader', 'ops.f01_docsource_is_safe_text(text,integer)'),
      ('carr_reader', 'ops.f01_docsource_is_external_ident(text,integer)'),
      ('carr_reader', 'ops.f01_reserved_derivative_kinds()'),
      ('carr_writer', 'ops.f01_docsource_utf16_length(text)'),
      ('carr_writer', 'ops.f01_reserved_derivative_kinds()'),
      ('carr_authority_joe', 'ops.f01_docsource_is_safe_text(text,integer)'),
      ('carr_authority_dell', 'ops.f01_docsource_is_external_ident(text,integer)')
    ) AS t(role_name, fn)
  LOOP
    PERFORM pg_temp.f01ds_assert(
      has_function_privilege(v_case.role_name, v_case.fn, 'EXECUTE'),
      'replacement', format('%s may execute %s', v_case.role_name, v_case.fn));
  END LOOP;

  -- NO RUNTIME DML on the new relation, for anyone.
  PERFORM pg_temp.f01ds_assert(
    NOT EXISTS (
      SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
       WHERE n.nspname = 'ops' AND c.relname = 'f01_document_source_provenance'
         AND (a.grantee = 0
              OR (a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')
                  AND a.grantee IS DISTINCT FROM c.relowner))),
    'replacement', 'no runtime DML grant exists on ops.f01_document_source_provenance');
END;
$private$;

-- The migration ships no provenance row of its own.
SELECT pg_temp.f01ds_assert(
  (SELECT count(*) FROM ops.f01_document_source_provenance) = 0,
  'replacement', 'the candidate installs no provenance row');

-- ===========================================================================
-- 3. The source artifact.  (carr_writer)
--
-- No policy version is installed anywhere in this fixture, deliberately: an
-- artifact, a derivative link and a provenance statement are all facts about
-- records rather than about a field-authority registry, and every policy_digest
-- column on that path is nullable for exactly that reason. If any of them ever
-- starts requiring a registry, this fixture fails and says so.
-- ===========================================================================

-- The production creator now revokes the ambient PUBLIC EXECUTE default.
-- These disposable assertion helpers have two explicit fixture callers.
DO $fixture_helper_grants$
BEGIN
  EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO carr_writer, carr_reader',
                 pg_my_temp_schema()::regnamespace::text);
END;
$fixture_helper_grants$;

SET SESSION AUTHORIZATION carr_writer;
SELECT set_config('carr.acting_actor_slug', 'codex', false);

DO $artifact$
DECLARE v_result jsonb;
BEGIN
  v_result := ops.f01_record_artifact(
    pg_temp.f01ds_envelope('stored_corporate_artifact',
      pg_temp.f01ds_artifact_record('sha256:' || repeat('91', 32), 'synthetic-version-1'),
      '{"is_fact":false,"makes_field_authoritative":false,"immutable":true}'::jsonb),
    'syn-pg-ds-artifact-0001', ops.f01_digest_jsonb('{"k":"ds-artifact-1"}'::jsonb));
  PERFORM pg_temp.f01ds_remember('artifact_1', v_result ->> 'artifact_digest');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'outcome', 'recorded',
    'artifact', 'the synthetic source artifact is stored');

  v_result := ops.f01_record_artifact(
    pg_temp.f01ds_envelope('stored_corporate_artifact',
      pg_temp.f01ds_artifact_record('sha256:' || repeat('92', 32), 'synthetic-version-2'),
      '{"is_fact":false,"makes_field_authoritative":false,"immutable":true}'::jsonb),
    'syn-pg-ds-artifact-0002', ops.f01_digest_jsonb('{"k":"ds-artifact-2"}'::jsonb));
  PERFORM pg_temp.f01ds_remember('artifact_2', v_result ->> 'artifact_digest');
END;
$artifact$;

-- ===========================================================================
-- 4. A DERIVED document version completes with its source registration.
-- ===========================================================================

DO $derived$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb;
  v_doc_digest text;
  v_link jsonb;
  v_link_digest text;
  v_prov jsonb;
  v_result jsonb;
BEGIN
  v_doc := pg_temp.f01ds_document_record('synthetic-doc-0001', 1,
    'sha256:' || repeat('a1', 32), NULL, v_now);
  v_doc_digest := ops.f01_digest_jsonb(v_doc);
  v_link := pg_temp.f01ds_link_record(pg_temp.f01ds_recall('artifact_1'),
    'synthetic-doc-0001', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0001', v_now);
  v_link_digest := ops.f01_digest_jsonb(v_link);
  v_prov := pg_temp.f01ds_provenance_record('synthetic-doc-0001', 1, v_doc_digest,
    'derived_from_stored_artifact', pg_temp.f01ds_recall('artifact_1'), v_link_digest,
    'syn_test_document_producer', 'syn-test-run-0001', NULL, v_now);

  PERFORM pg_temp.f01ds_remember('doc1_record', v_doc::text);
  PERFORM pg_temp.f01ds_remember('doc1_digest', v_doc_digest);
  PERFORM pg_temp.f01ds_remember('doc1_link_digest', v_link_digest);
  PERFORM pg_temp.f01ds_remember('doc1_now', v_now);

  v_result := ops.f01_record_document(
    pg_temp.f01ds_envelope('stored_document_version', v_doc,
      '{"object_storage_success_implies_official_filing":false,
        "neon_success_implies_official_filing":false}'::jsonb),
    pg_temp.f01ds_envelope('stored_document_source_provenance', v_prov, pg_temp.f01ds_claims()),
    pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims()),
    NULL, 'syn-pg-ds-doc-0001', ops.f01_digest_jsonb('{"k":"ds-doc-1"}'::jsonb));

  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'outcome', 'recorded',
    'derived', 'a derived document version is recorded');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'provenance_state',
    'derived_from_stored_artifact', 'derived', 'the stored statement says it is derived');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'derivative_link_digest', v_link_digest,
    'derived', 'the answer names the link that was written');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'derivative_registration_bound', 'true',
    'derived', 'the derivative registration is bound to the completion');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'actor_slug', pg_temp.f01ds_actor(),
    'derived', 'recorded_by is the derived actor');
  PERFORM pg_temp.f01ds_assert((v_result ->> 'external_effects') = 'false',
    'derived', 'completing a document produces no external effect');

  -- ALL THREE ROWS LANDED, and they name each other.
  PERFORM pg_temp.f01ds_assert(
    (SELECT count(*) FROM ops.f01_document_version) = 1
    AND (SELECT count(*) FROM ops.f01_derivative_link) = 1
    AND (SELECT count(*) FROM ops.f01_document_source_provenance) = 1,
    'derived', 'one document version, one derivative link, one provenance statement');
  PERFORM pg_temp.f01ds_assert(
    EXISTS (SELECT 1 FROM ops.f01_derivative_link
             WHERE derivative_kind = 'f01_document_version'
               AND derivative_id = 'synthetic-doc-0001:1'
               AND derivative_content_digest = v_doc_digest
               AND source_artifact_digest = pg_temp.f01ds_recall('artifact_1')
               AND actor_slug = pg_temp.f01ds_actor()),
    'derived', 'the link names this document version, its bytes and its source');

  -- THE READBACKS ARE RECOMPUTED, not trusted.
  PERFORM pg_temp.f01ds_assert_eq(v_result -> 'readback' ->> 'integrity',
    'recomputed_from_committed_row', 'derived', 'the document readback is recomputed');
  PERFORM pg_temp.f01ds_assert_eq(v_result -> 'provenance_readback' ->> 'integrity',
    'recomputed_from_committed_row', 'derived', 'the provenance readback is recomputed');
END;
$derived$;

-- 4.1 NOTHING ABOUT COVERAGE MOVED, and that is the point of the whole seam.
DO $coverage$
DECLARE v_coverage jsonb := ops.f01_derivative_coverage(pg_temp.f01ds_recall('artifact_1'));
BEGIN
  PERFORM pg_temp.f01ds_assert_eq(v_coverage ->> 'state', 'unknown',
    'coverage', 'registering a document does not establish coverage');
  PERFORM pg_temp.f01ds_assert_eq(v_coverage ->> 'reason_id',
    'producer_closure_not_established', 'coverage', 'and it says why');
  PERFORM pg_temp.f01ds_assert_eq(v_coverage ->> 'registered_link_count', '1',
    'coverage', 'the registered link is visible as evidence');
  PERFORM pg_temp.f01ds_assert((v_coverage ->> 'is_exhaustive_inventory') = 'false'
    AND (v_coverage ->> 'empty_link_set_means_verified_absence') = 'false',
    'coverage', 'the link set is still not an inventory');
  PERFORM pg_temp.f01ds_assert(
    ops.f01_stored_derivatives(pg_temp.f01ds_recall('artifact_1')) IS NULL,
    'coverage', 'the deletion evaluator still gets NULL, meaning unknown');
  -- Said in the writer's own answer too, so a caller storing it cannot later read
  -- a registration as a permission.
  PERFORM pg_temp.f01ds_assert(
    (SELECT (result ->> 'establishes_coverage') = 'false'
        AND (result ->> 'is_exhaustive_inventory') = 'false'
        AND (result ->> 'permits_deletion') = 'false'
        AND (result ->> 'absent_statement_means_no_source') = 'false'
       FROM ops.f01_idempotency
      WHERE operation = 'record-document-identity'
        AND idempotency_key = 'syn-pg-ds-doc-0001'),
    'coverage', 'the stored outcome claims no coverage, no inventory and no deletion');
END;
$coverage$;

-- 4.2 THE PROVENANCE READER answers about a version, and NULL means "no
-- statement" rather than "no source".
DO $reader$
DECLARE v_statement jsonb := ops.f01_document_version_source('synthetic-doc-0001', 1);
BEGIN
  PERFORM pg_temp.f01ds_assert_eq(v_statement ->> 'provenance_state',
    'derived_from_stored_artifact', 'reader', 'the statement reads back');
  PERFORM pg_temp.f01ds_assert_eq(v_statement ->> 'source_artifact_digest',
    pg_temp.f01ds_recall('artifact_1'), 'reader', 'and names the exact source');
  PERFORM pg_temp.f01ds_assert_eq(v_statement ->> 'absent_statement_means_no_source', 'false',
    'reader', 'an absent statement is never read as an absent source');
  PERFORM pg_temp.f01ds_assert(
    ops.f01_document_version_source('synthetic-doc-0001', 99) IS NULL,
    'reader', 'an unrecorded version has no statement, and no default is invented');
  PERFORM pg_temp.f01ds_assert(
    jsonb_array_length(ops.f01_document_source_history('synthetic-doc-0001')) = 1,
    'reader', 'the history holds exactly the one statement made');
END;
$reader$;

-- ===========================================================================
-- 5. The refusal matrix. Every one of these leaves the record layer unchanged.
-- ===========================================================================

SELECT pg_temp.f01ds_remember('rows_before',
  (SELECT count(*) FROM ops.f01_document_version)::text);

-- 5.1 NO PROVENANCE STATEMENT AT ALL. This is the settled producer rule made
-- structural: a document version cannot complete without saying where it came
-- from, and passing NULL is not a way around it.
SELECT pg_temp.f01ds_expect_refusal(
  format($$SELECT ops.f01_record_document(
      pg_temp.f01ds_envelope('stored_document_version',
        pg_temp.f01ds_document_record('synthetic-doc-0002', 1, 'sha256:' || repeat('a2', 32),
                                      NULL, %L)),
      NULL, NULL, NULL, 'syn-pg-ds-noprov-0001',
      ops.f01_digest_jsonb('{"k":"ds-noprov"}'::jsonb))$$,
    pg_temp.f01ds_recall('doc1_now')),
  'f01_document_provenance_required', 'refusal',
  'a document version cannot complete with no statement about its origin');

-- 5.2 A DERIVED STATEMENT WITH NO LINK.
DO $nolink$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
        'syn-pg-ds-nolink-0001', ops.f01_digest_jsonb('{"k":"ds-nolink"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'derived_from_stored_artifact', pg_temp.f01ds_recall('artifact_1'),
          'sha256:' || repeat('b1', 32), 'syn_test_document_producer',
          'syn-test-run-0002', NULL, v_now),
        pg_temp.f01ds_claims())::text),
    'f01_derivative_link_required', 'refusal',
    'a derived document version completes only with its source registration');
END;
$nolink$;

-- 5.3 A LINK THAT NAMES SOME OTHER DOCUMENT. "A link was supplied" is not the
-- same as "this document's provenance was registered".
DO $wronglink$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb := pg_temp.f01ds_link_record(pg_temp.f01ds_recall('artifact_1'),
    'synthetic-doc-0009', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0003', v_now);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, %L::jsonb, NULL,
        'syn-pg-ds-wronglink-0001', ops.f01_digest_jsonb('{"k":"ds-wronglink"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'derived_from_stored_artifact', pg_temp.f01ds_recall('artifact_1'),
          ops.f01_digest_jsonb(v_link), 'syn_test_document_producer',
          'syn-test-run-0003', NULL, v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims())::text),
    'f01_derivative_link_not_bound_to_document', 'refusal',
    'a link naming another document does not register this one''s provenance');
END;
$wronglink$;

-- 5.4 A STATEMENT THAT NAMES SOME OTHER DOCUMENT VERSION.
DO $wrongprov$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
        'syn-pg-ds-wrongprov-0001', ops.f01_digest_jsonb('{"k":"ds-wrongprov"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0007', 3, v_doc_digest,
          'original_first_party', NULL, NULL, NULL, NULL,
          'synthetic basis', v_now),
        pg_temp.f01ds_claims())::text),
    'f01_document_provenance_not_bound_to_document', 'refusal',
    'a statement about another document version is not this one''s provenance');
END;
$wrongprov$;

-- 5.5 A SOURCE ARTIFACT INVENTED FROM THE DOCUMENT'S OWN BYTES. The document's
-- content digest is the document, not the thing it came from.
DO $ownbytes$
DECLARE
  v_now text := ops.f01_now_text();
  v_content text := 'sha256:' || repeat('a2', 32);
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1, v_content, NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb := pg_temp.f01ds_link_record(v_content, 'synthetic-doc-0002', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0004', v_now);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, %L::jsonb, NULL,
        'syn-pg-ds-ownbytes-0001', ops.f01_digest_jsonb('{"k":"ds-ownbytes"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'derived_from_stored_artifact', v_content, ops.f01_digest_jsonb(v_link),
          'syn_test_document_producer', 'syn-test-run-0004', NULL, v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims())::text),
    'f01_document_bytes_are_not_a_source_artifact', 'refusal',
    'a document''s own content digest is never its provenance');
END;
$ownbytes$;

-- 5.6 AN ARTIFACT NOBODY STORED is not brought into existence by naming it.
DO $unknown$
DECLARE
  v_now text := ops.f01_now_text();
  v_ghost text := 'sha256:' || repeat('ee', 32);
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb := pg_temp.f01ds_link_record(v_ghost, 'synthetic-doc-0002', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0005', v_now);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, %L::jsonb, NULL,
        'syn-pg-ds-ghost-0001', ops.f01_digest_jsonb('{"k":"ds-ghost"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'derived_from_stored_artifact', v_ghost, ops.f01_digest_jsonb(v_link),
          'syn_test_document_producer', 'syn-test-run-0005', NULL, v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims())::text),
    'f01_unknown_artifact', 'refusal',
    'provenance pointing at an artifact nobody stored is not provenance');
END;
$unknown$;

-- 5.7 A FORGED DIGEST. The envelope must hash to its own claim, for the document
-- and for the statement alike.
DO $forged$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
        'syn-pg-ds-forged-0001', ops.f01_digest_jsonb('{"k":"ds-forged"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      (pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'original_first_party', NULL, NULL, NULL, NULL, 'synthetic basis', v_now),
        pg_temp.f01ds_claims())
       || jsonb_build_object('record_digest', 'sha256:' || repeat('dd', 32)))::text),
    'f01_document_provenance_digest_mismatch', 'refusal',
    'a statement that lies about its own bytes refuses');
END;
$forged$;

-- 5.8 ACTOR INJECTION. recorded_by is derived, never supplied.
DO $injected$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_prov jsonb := pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
    'original_first_party', NULL, NULL, NULL, NULL, 'synthetic basis', v_now)
    || jsonb_build_object('recorded_by', 'somebody-else');
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
        'syn-pg-ds-injected-0001', ops.f01_digest_jsonb('{"k":"ds-injected"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance', v_prov,
        pg_temp.f01ds_claims())::text),
    'f01_actor_injection_refused', 'refusal',
    'a statement cannot be attributed to a workflow that did not make it');
END;
$injected$;

-- 5.9 THE FREE TEXT IS HELD TO THE MODULE'S OWN RULES. A statement a human may
-- later have to read and weigh cannot arrive over-long, non-canonical, invisible
-- or untrimmed just because it came through SQL rather than through Node.
DO $text_rules$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0002', 1,
    'sha256:' || repeat('a2', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb := pg_temp.f01ds_link_record(pg_temp.f01ds_recall('artifact_1'),
    'synthetic-doc-0002', 1, v_doc_digest,
    'syn test document producer', 'syn-test-run-0008', v_now);
  v_case record;
BEGIN
  FOR v_case IN
    SELECT * FROM (VALUES
      -- 513 UTF-16 code units, one past the bound the module enforces.
      ('too long', repeat('b', 513)),
      -- 512 code points but 1024 code units: the exact string a bound written
      -- with PostgreSQL's length() would have admitted and Node refuses.
      ('astral over the code-unit bound', repeat(chr(128450), 512)),
      -- "e" plus a combining acute. Refused rather than normalized, because
      -- normalizing would store bytes nobody wrote and change the digest.
      ('not NFC', 'authored here e' || chr(769)),
      ('invisible', 'authored' || chr(8203) || 'here'),
      ('untrimmed with a no-break space', chr(160) || 'authored here'),
      ('empty', '')
    ) AS t(label, basis)
  LOOP
    PERFORM pg_temp.f01ds_expect_refusal(
      format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
          %L, ops.f01_digest_jsonb('{"k":"ds-text"}'::jsonb))$$,
        pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
        pg_temp.f01ds_envelope('stored_document_source_provenance',
          pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
            'original_first_party', NULL, NULL, NULL, NULL, v_case.basis, v_now),
          pg_temp.f01ds_claims())::text,
        'syn-pg-ds-text-' || md5(v_case.label)),
      'f01_document_provenance_text_refused', 'text',
      'a basis statement that is ' || v_case.label || ' is refused by name');
  END LOOP;

  -- And the two producer fields are identifiers on this side too. The link row
  -- carries the same workflow and lands first; the statement is what refuses, and
  -- the whole completion rolls back with it.
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, %L::jsonb, NULL,
        'syn-pg-ds-textident-0001', ops.f01_digest_jsonb('{"k":"ds-textident"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0002', 1, v_doc_digest,
          'derived_from_stored_artifact', pg_temp.f01ds_recall('artifact_1'),
          ops.f01_digest_jsonb(v_link), 'syn test document producer',
          'syn-test-run-0008', NULL, v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims())::text),
    'f01_document_provenance_text_refused', 'text',
    'a producer workflow outside the kernel''s identifier alphabet is refused');
END;
$text_rules$;

-- 5.10 EVERY REFUSAL ABOVE WROTE NOTHING. Atomicity is the property, not the hope.
SELECT pg_temp.f01ds_assert(
  (SELECT count(*) FROM ops.f01_document_version)::text = pg_temp.f01ds_recall('rows_before')
  AND (SELECT count(*) FROM ops.f01_document_source_provenance) = 1
  AND (SELECT count(*) FROM ops.f01_derivative_link) = 1,
  'refusal', 'not one refused completion left a document, a link or a statement behind');

-- ===========================================================================
-- 6. Originals and legacy provenance, kept apart and kept honest.
-- ===========================================================================

DO $original$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0003', 1,
    'sha256:' || repeat('a3', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_result jsonb;
BEGIN
  v_result := ops.f01_record_document(
    pg_temp.f01ds_envelope('stored_document_version', v_doc),
    pg_temp.f01ds_envelope('stored_document_source_provenance',
      pg_temp.f01ds_provenance_record('synthetic-doc-0003', 1, v_doc_digest,
        'original_first_party', NULL, NULL, NULL, NULL,
        'authored in the record layer by the synthetic test workflow', v_now),
      pg_temp.f01ds_claims()),
    NULL, NULL, 'syn-pg-ds-original-0001',
    ops.f01_digest_jsonb('{"k":"ds-original"}'::jsonb));
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'provenance_state', 'original_first_party',
    'original', 'an original is recorded as an original');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'derivative_registration_bound', 'false',
    'original', 'and registers no derivative, because it derives from nothing');
  PERFORM pg_temp.f01ds_assert(
    (SELECT count(*) FROM ops.f01_derivative_link) = 1,
    'original', 'no link row was written for a document with no source');
END;
$original$;

DO $legacy$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0004', 1,
    'sha256:' || repeat('a4', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_result jsonb;
BEGIN
  v_result := ops.f01_record_document(
    pg_temp.f01ds_envelope('stored_document_version', v_doc),
    pg_temp.f01ds_envelope('stored_document_source_provenance',
      pg_temp.f01ds_provenance_record('synthetic-doc-0004', 1, v_doc_digest,
        'legacy_provenance_unknown', NULL, NULL, NULL, NULL,
        'imported before the registration rule; the original is not known', v_now),
      pg_temp.f01ds_claims()),
    NULL, NULL, 'syn-pg-ds-legacy-0001',
    ops.f01_digest_jsonb('{"k":"ds-legacy"}'::jsonb));
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'provenance_state', 'legacy_provenance_unknown',
    'legacy', 'an imported document is recorded as UNKNOWN');
  -- THE WHOLE POINT: unknown is not original, and neither is inferred from the
  -- absence of a link. The two rows differ in the bytes they hash to.
  PERFORM pg_temp.f01ds_assert(
    (SELECT provenance_state FROM ops.f01_document_source_provenance
      WHERE document_id = 'synthetic-doc-0004')
    IS DISTINCT FROM
    (SELECT provenance_state FROM ops.f01_document_source_provenance
      WHERE document_id = 'synthetic-doc-0003'),
    'legacy', 'an imported document is never upgraded to an original');
END;
$legacy$;

-- 6.1 A NON-DERIVED DOCUMENT THAT ALSO OFFERS A LINK. This is the shape somebody
-- reaches for when they want an original to look registered.
DO $link_on_original$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0005', 1,
    'sha256:' || repeat('a5', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb := pg_temp.f01ds_link_record(pg_temp.f01ds_recall('artifact_1'),
    'synthetic-doc-0005', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0006', v_now);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, %L::jsonb, NULL,
        'syn-pg-ds-linkonorig-0001', ops.f01_digest_jsonb('{"k":"ds-linkonorig"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0005', 1, v_doc_digest,
          'original_first_party', NULL, NULL, NULL, NULL, 'synthetic basis', v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims())::text),
    'f01_derivative_link_on_non_derived_document', 'refusal',
    'a document that derives from nothing registers no derivative link');
END;
$link_on_original$;

-- 6.2 AN UNREGISTERED FOURTH ANSWER is a contract violation, not a decision.
DO $fourth$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0005', 1,
    'sha256:' || repeat('a5', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, NULL,
        'syn-pg-ds-fourth-0001', ops.f01_digest_jsonb('{"k":"ds-fourth"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0005', 1, v_doc_digest,
          'probably_derived', NULL, NULL, NULL, NULL, 'synthetic basis', v_now),
        pg_temp.f01ds_claims())::text),
    'f01_unknown_document_provenance_state', 'refusal',
    '"probably derived" is not one of the three honest answers');
END;
$fourth$;

-- ===========================================================================
-- 7. Repointing, compare-and-swap and replay.
-- ===========================================================================

-- 7.1 THE PUBLIC SURFACE CANNOT TOUCH A DOCUMENT VERSION'S PROVENANCE AT ALL.
--
-- 'f01_document_version' is produced by a writer inside this schema, so it is
-- reserved and the public registration surface refuses it outright — before it
-- claims an idempotency key, and whatever the link says. That is a STRONGER
-- property than the one-derivative-one-original conflict this section used to
-- assert here, and it has to be, because the conflict alone left the identity
-- PRE-CLAIMABLE: a document version's derivative id is its own name and version,
-- which anybody can predict before the document exists.
SELECT pg_temp.f01ds_expect_refusal(
  format($$SELECT ops.f01_register_derivative_link(
      pg_temp.f01ds_envelope('stored_derivative_link',
        pg_temp.f01ds_link_record(%L, 'synthetic-doc-0001', 1, %L,
          'syn_test_document_producer', 'syn-test-run-0007', ops.f01_now_text()),
        pg_temp.f01ds_claims()),
      'syn-pg-ds-repoint-0001', ops.f01_digest_jsonb('{"k":"ds-repoint"}'::jsonb))$$,
    pg_temp.f01ds_recall('artifact_2'), pg_temp.f01ds_recall('doc1_digest')),
  'f01_reserved_derivative_kind', 'repoint',
  'a stored document version cannot be repointed at another original through the public surface');

-- THE PRE-CLAIM ITSELF, which is the shape that mattered: registering the
-- predicted identity of a document version that has NOT been written yet. Before
-- the reservation this succeeded, and the genuine completion of that version would
-- then have raised f01_derivative_source_conflict for ever against an append-only
-- identity with no release path — leaving a provenance edge asserting the document
-- came from an artifact it did not.
SELECT pg_temp.f01ds_expect_refusal(
  format($$SELECT ops.f01_register_derivative_link(
      pg_temp.f01ds_envelope('stored_derivative_link',
        pg_temp.f01ds_link_record(%L, 'synthetic-doc-0006', 1, %L,
          'syn_test_document_producer', 'syn-test-run-0009', ops.f01_now_text()),
        pg_temp.f01ds_claims()),
      'syn-pg-ds-preclaim-0001', ops.f01_digest_jsonb('{"k":"ds-preclaim"}'::jsonb))$$,
    pg_temp.f01ds_recall('artifact_2'), 'sha256:' || repeat('a6', 32)),
  'f01_reserved_derivative_kind', 'repoint',
  'a document version that does not exist yet cannot have its provenance claimed for it');

-- And the stored link still names the artifact it was registered against.
SELECT pg_temp.f01ds_assert_eq(
  (SELECT source_artifact_digest FROM ops.f01_derivative_link
    WHERE derivative_kind = 'f01_document_version' AND derivative_id = 'synthetic-doc-0001:1'),
  pg_temp.f01ds_recall('artifact_1'),
  'repoint', 'the refused repointing did not move the stored source');

SELECT pg_temp.f01ds_assert(
  NOT EXISTS (SELECT 1 FROM ops.f01_derivative_link
               WHERE derivative_kind = 'f01_document_version'
                 AND derivative_id = 'synthetic-doc-0006:1'),
  'repoint', 'the refused pre-claim left nothing behind for the real writer to collide with');

-- 7.1.1 AND THE PRE-CLAIMED DOCUMENT COMPLETES NORMALLY AFTERWARDS. This is the
-- half that proves the refusal was a refusal rather than a burnt identity: the
-- genuine producer writes the same version, through the writer, and gets its own
-- link against the source it really came from.
DO $after_preclaim$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0006', 1,
    'sha256:' || repeat('a7', 32), NULL, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
  v_link jsonb;
  v_result jsonb;
BEGIN
  v_link := pg_temp.f01ds_link_record(pg_temp.f01ds_recall('artifact_1'),
    'synthetic-doc-0006', 1, v_doc_digest,
    'syn_test_document_producer', 'syn-test-run-0010', v_now);
  v_result := ops.f01_record_document(
    pg_temp.f01ds_envelope('stored_document_version', v_doc),
    pg_temp.f01ds_envelope('stored_document_source_provenance',
      pg_temp.f01ds_provenance_record('synthetic-doc-0006', 1, v_doc_digest,
        'derived_from_stored_artifact', pg_temp.f01ds_recall('artifact_1'),
        ops.f01_digest_jsonb(v_link), 'syn_test_document_producer',
        'syn-test-run-0010', NULL, v_now),
      pg_temp.f01ds_claims()),
    pg_temp.f01ds_envelope('stored_derivative_link', v_link, pg_temp.f01ds_claims()),
    NULL, 'syn-pg-ds-doc-0006', ops.f01_digest_jsonb('{"k":"ds-doc-6"}'::jsonb));
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'outcome', 'recorded',
    'repoint', 'the genuine producer still completes the version somebody tried to claim');
  PERFORM pg_temp.f01ds_assert_eq(
    (SELECT source_artifact_digest FROM ops.f01_derivative_link
      WHERE derivative_kind = 'f01_document_version'
        AND derivative_id = 'synthetic-doc-0006:1'),
    pg_temp.f01ds_recall('artifact_1'),
    'repoint', 'and the link names the artifact it really came from');
END;
$after_preclaim$;

-- 7.1.2 THE GENUINE SOURCE CONFLICT STILL FIRES, through the seam that still
-- reaches it.
--
-- WHY NOT WITH A DOCUMENT. Two reasons, and both are properties rather than
-- omissions. Through the PUBLIC surface an 'f01_document_version' link is now
-- refused as reserved, so a forged one would prove nothing about the conflict.
-- Through ops.f01_record_document the document version is inserted BEFORE the
-- link, and ops.f01_document_version_identity_uq refuses a second version 1 of the
-- same document first — the link identity and the document identity are the same
-- identity by construction. So the conflict is proved where it is genuinely
-- reachable: an EXTERNAL producer's own kind, registered twice against two
-- different artifacts, through the private inserter both writers share.
DO $conflict$
DECLARE
  v_now text := ops.f01_now_text();
  v_content text := 'sha256:' || repeat('c1', 32);
  v_first jsonb := pg_temp.f01ds_external_link_record(
    'syn_external_derivative', 'syn-external-0001', pg_temp.f01ds_recall('artifact_1'),
    v_content, 'syn_external_producer', 'syn-external-run-0001', ops.f01_now_text());
  v_result jsonb;
BEGIN
  v_result := ops.f01_register_derivative_link(
    pg_temp.f01ds_envelope('stored_derivative_link', v_first, pg_temp.f01ds_claims()),
    'syn-pg-ds-external-0001', ops.f01_digest_jsonb('{"k":"ds-external-1"}'::jsonb));
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'outcome', 'registered',
    'conflict', 'a kind this schema does not produce is registered through the public surface');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'establishes_coverage', 'false',
    'conflict', 'and it still establishes no coverage');

  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_register_derivative_link(
        pg_temp.f01ds_envelope('stored_derivative_link',
          pg_temp.f01ds_external_link_record('syn_external_derivative', 'syn-external-0001',
            %L, %L, 'syn_external_producer', 'syn-external-run-0002', ops.f01_now_text()),
          pg_temp.f01ds_claims()),
        'syn-pg-ds-external-0002', ops.f01_digest_jsonb('{"k":"ds-external-2"}'::jsonb))$$,
      pg_temp.f01ds_recall('artifact_2'), v_content),
    'f01_derivative_source_conflict', 'conflict',
    'one derivative has one original, and the second registration names another');

  PERFORM pg_temp.f01ds_assert_eq(
    (SELECT source_artifact_digest FROM ops.f01_derivative_link
      WHERE derivative_kind = 'syn_external_derivative'
        AND derivative_id = 'syn-external-0001'),
    pg_temp.f01ds_recall('artifact_1'),
    'conflict', 'and the refused rewrite did not move the stored source');
END;
$conflict$;

-- 7.1.3 A SECOND, DIFFERENT STATEMENT ABOUT A VERSION THAT ALREADY HAS ONE cannot
-- land even when the compare-and-swap is satisfied. 7.2 below refuses at the CAS
-- because it claims the wrong prior; this one names the RIGHT prior and is refused
-- by the document identity itself, which is what makes "one version, one
-- statement, one link" structural rather than a consequence of arriving second.
DO $restate_with_correct_cas$
DECLARE
  v_now text := ops.f01_now_text();
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0001', 1,
    'sha256:' || repeat('a8', 32), pg_temp.f01ds_recall('doc1_digest'), v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, %L,
        'syn-pg-ds-restate-cas-0001', ops.f01_digest_jsonb('{"k":"ds-restate-cas"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0001', 1, v_doc_digest,
          'original_first_party', NULL, NULL, NULL, NULL,
          'a second, contrary statement about a version that already has one', v_now),
        pg_temp.f01ds_claims())::text,
      pg_temp.f01ds_recall('doc1_digest')),
    'f01_document_version_identity_uq', 'repoint',
    'a version that already has a statement cannot be restated under a different origin');
END;
$restate_with_correct_cas$;

-- 7.2 A SECOND STATEMENT ABOUT AN ALREADY-STATED VERSION cannot land, because a
-- version's origin is stated once.
SELECT pg_temp.f01ds_expect_refusal(
  format($$SELECT ops.f01_record_document(
      pg_temp.f01ds_envelope('stored_document_version', %L::jsonb),
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0001', 1, %L,
          'original_first_party', NULL, NULL, NULL, NULL, 'synthetic basis',
          ops.f01_now_text()),
        pg_temp.f01ds_claims()),
      NULL, NULL, 'syn-pg-ds-restate-0001',
      ops.f01_digest_jsonb('{"k":"ds-restate"}'::jsonb))$$,
    pg_temp.f01ds_recall('doc1_record'), pg_temp.f01ds_recall('doc1_digest')),
  'f01_stale_document_digest', 'repoint',
  'a second completion of a stated version refuses at the compare-and-swap');

-- 7.3 THE COMPARE-AND-SWAP still decides against the STORED current pointer, not
-- against the caller's belief about it. The payload is otherwise PERFECTLY GOOD —
-- a coherent version 2 with a well-formed statement that names its own bytes — so
-- the only thing wrong with it is the prior version it claims to follow.
DO $cas$
DECLARE
  v_now text := ops.f01_now_text();
  v_wrong_prior text := 'sha256:' || repeat('cf', 32);
  v_doc jsonb := pg_temp.f01ds_document_record('synthetic-doc-0001', 2,
    'sha256:' || repeat('a6', 32), v_wrong_prior, v_now);
  v_doc_digest text := ops.f01_digest_jsonb(v_doc);
BEGIN
  PERFORM pg_temp.f01ds_expect_refusal(
    format($$SELECT ops.f01_record_document(%L::jsonb, %L::jsonb, NULL, %L,
        'syn-pg-ds-cas-0001', ops.f01_digest_jsonb('{"k":"ds-cas"}'::jsonb))$$,
      pg_temp.f01ds_envelope('stored_document_version', v_doc)::text,
      pg_temp.f01ds_envelope('stored_document_source_provenance',
        pg_temp.f01ds_provenance_record('synthetic-doc-0001', 2, v_doc_digest,
          'original_first_party', NULL, NULL, NULL, NULL, 'synthetic basis', v_now),
        pg_temp.f01ds_claims())::text,
      v_wrong_prior),
    'f01_stale_document_digest', 'cas',
    'a completion decided against a prior version that is not the stored one refuses');
END;
$cas$;

-- 7.4 REPLAY. The same key over the same payload returns the SAME committed
-- outcome, and writes nothing a second time.
DO $replay$
DECLARE
  v_result jsonb;
  v_versions bigint := (SELECT count(*) FROM ops.f01_document_version);
  v_links bigint := (SELECT count(*) FROM ops.f01_derivative_link);
  v_statements bigint := (SELECT count(*) FROM ops.f01_document_source_provenance);
BEGIN
  v_result := ops.f01_record_document(
    pg_temp.f01ds_envelope('stored_document_version',
      pg_temp.f01ds_recall('doc1_record')::jsonb,
      '{"object_storage_success_implies_official_filing":false,
        "neon_success_implies_official_filing":false}'::jsonb),
    -- The replay door is reached before any state is read, so the envelopes are
    -- never examined. They are supplied as NULL deliberately: a replay that
    -- required them again would be re-evaluating a decision that was already
    -- committed.
    NULL, NULL, NULL, 'syn-pg-ds-doc-0001',
    ops.f01_digest_jsonb('{"k":"ds-doc-1"}'::jsonb));
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'document_digest',
    pg_temp.f01ds_recall('doc1_digest'), 'replay', 'the replay returns the committed outcome');
  PERFORM pg_temp.f01ds_assert_eq(v_result ->> 'derivative_link_digest',
    pg_temp.f01ds_recall('doc1_link_digest'), 'replay',
    'including the derivative link the original completion registered');
  PERFORM pg_temp.f01ds_assert(
    (SELECT count(*) FROM ops.f01_document_version) = v_versions
    AND (SELECT count(*) FROM ops.f01_derivative_link) = v_links
    AND (SELECT count(*) FROM ops.f01_document_source_provenance) = v_statements,
    'replay', 'a replay writes nothing a second time');
END;
$replay$;

-- 7.5 THE SAME KEY OVER A DIFFERENT PAYLOAD is a substitution attempt.
SELECT pg_temp.f01ds_expect_refusal(
  format($$SELECT ops.f01_record_document(
      pg_temp.f01ds_envelope('stored_document_version', %L::jsonb),
      NULL, NULL, NULL, 'syn-pg-ds-doc-0001',
      ops.f01_digest_jsonb('{"k":"ds-doc-SOMETHING-ELSE"}'::jsonb))$$,
    pg_temp.f01ds_recall('doc1_record')),
  'f01_idempotency_payload_mismatch', 'replay',
  'one key binds one payload; it never substitutes one write for another');

-- ===========================================================================
-- 8. The guards, exercised by an identity that could otherwise have written.
-- ===========================================================================

RESET SESSION AUTHORIZATION;

SELECT pg_temp.f01ds_expect_refusal(
  $$INSERT INTO ops.f01_document_source_provenance
      (tenant, document_id, version_no, document_digest, envelope, envelope_digest,
       provenance_digest, provenance_state, actor_slug, recorded_at_text, recorded_at,
       idempotency_key)
    VALUES ('carr-internal', 'synthetic-doc-0099', 1, 'sha256:' || repeat('a1', 32),
            '{}'::jsonb, 'x', 'y', 'original_first_party', 'joe',
            '2026-09-09T12:00:00Z', now(), 'syn-direct')$$,
  'f01_direct_dml_refused', 'guard',
  'a direct INSERT that did not arrive through a registered writer refuses');

SELECT pg_temp.f01ds_expect_refusal(
  $$UPDATE ops.f01_document_source_provenance SET provenance_state = 'original_first_party'$$,
  'f01_append_only_violation', 'guard',
  'a provenance statement is never edited to say something else about what happened');

SELECT pg_temp.f01ds_expect_refusal(
  $$DELETE FROM ops.f01_document_source_provenance$$,
  'f01_append_only_violation', 'guard',
  'and it is never removed to make an absent statement plausible');

SELECT pg_temp.f01ds_expect_refusal(
  $$TRUNCATE ops.f01_document_source_provenance$$,
  'f01_truncate_refused', 'guard', 'the provenance history cannot be truncated');

-- The guard replacement did not weaken any OTHER relation.
SELECT pg_temp.f01ds_expect_refusal(
  $$INSERT INTO ops.f01_derivative_link
      (tenant, envelope, envelope_digest, link_digest, source_artifact_digest,
       derivative_kind, derivative_id, derivative_content_digest, producer_workflow,
       producer_run_ref, produced_at_text, produced_at, evidence_ref, evidence_digest,
       actor_slug, recorded_at, idempotency_key)
    VALUES ('carr-internal', '{}'::jsonb, 'x', 'y', 'z', 'k', 'i', 'c', 'w', 'r',
            '2026-09-09T12:00:00Z', now(), 'e', 'd', 'joe', now(), 'syn-direct')$$,
  'f01_direct_dml_refused', 'guard',
  'the replaced guard still refuses direct DML on the derivative-link table');

SELECT pg_temp.f01ds_expect_refusal(
  $$UPDATE ops.f01_document_version SET version_state = 'withdrawn'$$,
  'f01_append_only_violation', 'guard',
  'and still refuses an UPDATE of document-version history');

-- ===========================================================================
-- 9. Least privilege, and recomputed integrity.
-- ===========================================================================

-- 9.1 THE STORED STATEMENT HASHES TO ITS OWN RECORDED DIGEST.
--
-- WHAT THIS IS AND IS NOT. This is a recomputation over a row the fixture READ,
-- not a corrupt-row readback: the guards, the CHECK constraints and the absent
-- DML grants together mean this fixture has no way to damage a row, and it does
-- not acquire one to prove a point. Proving that a DAMAGED row raises rather than
-- resolving to an older statement requires writing past the constraints, which is
-- the gate's job with a disposable database and is not attempted here. What IS
-- proved here is that the recomputation the readers perform agrees with what was
-- committed.
DO $recomputed$
DECLARE v_saved text;
BEGIN
  SELECT provenance_digest INTO v_saved FROM ops.f01_document_source_provenance
   WHERE document_id = 'synthetic-doc-0001';
  PERFORM pg_temp.f01ds_assert(
    v_saved = (SELECT ops.f01_digest_jsonb(envelope -> 'record')
                 FROM ops.f01_document_source_provenance
                WHERE document_id = 'synthetic-doc-0001'),
    'integrity', 'the stored statement hashes to its recorded digest');
  PERFORM pg_temp.f01ds_assert(
    (SELECT envelope_digest = ops.f01_digest_jsonb(envelope)
       FROM ops.f01_document_source_provenance WHERE document_id = 'synthetic-doc-0001'),
    'integrity', 'and the envelope hashes to its recorded envelope digest');
END;
$recomputed$;

-- 9.2 THE READER'S EXCLUSION, from the grant layer rather than from a message.
SELECT pg_temp.f01ds_assert(
  NOT has_function_privilege('carr_reader',
    'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)', 'EXECUTE'),
  'privilege', 'a read-only principal cannot complete a document version');

-- 9.3 …and the reader can still READ the provenance it may not write.
SET SESSION AUTHORIZATION carr_reader;
DO $reader_reads$
DECLARE v_statement jsonb := ops.f01_document_version_source('synthetic-doc-0001', 1);
BEGIN
  PERFORM pg_temp.f01ds_assert_eq(v_statement ->> 'provenance_state',
    'derived_from_stored_artifact', 'privilege',
    'an operator can see where a document came from without being able to change it');
  PERFORM pg_temp.f01ds_assert_eq(v_statement ->> 'establishes_coverage', 'false',
    'privilege', 'and reading it establishes nothing');
END;
$reader_reads$;
RESET SESSION AUTHORIZATION;

-- ===========================================================================
-- 10. The summary. A run that printed nothing would look exactly like a run that
--     proved nothing, so the counts are the last thing on screen.
-- ===========================================================================

SELECT section,
       count(*) FILTER (WHERE outcome = 'passed')  AS passed,
       count(*) FILTER (WHERE outcome = 'refused') AS refused
  FROM f01ds_log
 GROUP BY section
 ORDER BY section;

DO $summary$
DECLARE v_total bigint := (SELECT count(*) FROM f01ds_log);
BEGIN
  -- The floor moved with the file. A run that stopped early — a DO block that
  -- swallowed something, a section skipped by an edit — must not be able to print
  -- a summary and look like a pass.
  IF v_total < 60 THEN
    RAISE EXCEPTION 'F01 DOCUMENT-SOURCE FIXTURE FAILED [summary]: only % assertions ran; a short run is not a pass',
      v_total;
  END IF;
  RAISE NOTICE 'F01 DOCUMENT-SOURCE FIXTURE PASSED: % assertions', v_total;
END;
$summary$;
