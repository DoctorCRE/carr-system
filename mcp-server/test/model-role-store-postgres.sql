-- DoctorCRE v5 durable role-description store: transaction-scoped PostgreSQL proof.
--
-- HOW TO RUN THIS FILE, exactly:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f mcp-server/test/model-role-store-postgres.sql
-- after ops/model-role-store.candidate.sql has been applied to that database.
-- THAT CANDIDATE INSTALLS FRESH: it refuses, before any DDL, a database that
-- already carries ops.model_role_* relations or functions, and it holds no ALTER
-- TABLE, no ADD COLUMN and no backfill. So the database this proof wants is one
-- BUILT from the current candidate, not one an older copy installed and a re-run
-- was expected to bring forward -- re-running brings nothing forward, by design,
-- because deciding what happens to rows already stored is a migration's act. The
-- "installed shape" block below detects that drift by name rather than assuming
-- it away.
-- Run it TWICE to cover both halves: once as the schema owner, and once on the
-- retained system-authority partner's authority connection (session_user
-- carr_authority_joe), which is where the positive current-pointer path and the
-- acting-principal refusal separate. A carr_writer session is also a supported
-- run mode and is expected to reach the pointer writer only as a permission
-- denial; see the assertion at "the current pointer" below.
--
-- THE NODE CASES ARE A DIFFERENT FILE AND A DIFFERENT COMMAND. To reproduce
-- exactly the cases in mcp-server/test/model-role-store.v5.test.mjs:
--   cd mcp-server && node --test test/model-role-store.v5.test.mjs
-- `npm test` in that directory runs `node --test test/*.test.js test/*.test.mjs`,
-- which is the WHOLE repository suite, not this slice -- so a green `npm test`
-- is not a statement about this file's cases and must not be reported as one.
--
-- EVERY FIXTURE ROW IS ROLLED BACK. This file writes nothing that survives it,
-- creates no role, no actor and no extension, applies no migration, and makes no
-- provider or network call. It is a PROOF, not an install: it does not apply
-- ops/model-role-store.candidate.sql and cannot -- that file is candidate source
-- and landing it is a separate, reviewed act.
--
-- THE ROLE IS SYNTHETIC ON PURPOSE. "Reviewer" below is a fixture for the
-- MECHANISM. It is not a proposal of DoctorCRE's real Reviewer job description,
-- nothing in the module or the candidate SQL seeds a role, and no revision
-- recorded here outlives the rollback.
--
-- EXPLICIT PREREQUISITES, checked before anything is attempted. Each one SKIPS
-- with a notice naming what is absent rather than failing, and a skip is
-- reported as a skip -- this file never prints a clean run for assertions it did
-- not execute:
--   * ops.model_role_record_revision must exist. It does not until
--     ops/model-role-store.candidate.sql has been applied here, which has NOT
--     been done by the change that introduced this file.
--   * ops.portfolio_writer_actor_id and ops.portfolio_canonical_json (migration
--     0496) and ops.authority_actor_slug (migration 0161) must exist. This rail
--     reuses all three rather than restating them.
--   * public.digest (pgcrypto) must exist.
--   * One active NON-HUMAN actor must already exist, for the writer context.
--     This file CREATES NO ACTOR AND NO ROLE.
--
-- WHAT IT PROVES, none of which can be shown by reading SQL text:
--   * the preimage rebuilt FROM THE STORED ROWS equals, jsonb for jsonb, a
--     preimage assembled independently in this file -- and both hash to the same
--     role_digest, over the bare object with NO domain tag
--   * list ORDER participates in the digest for skills, rules and evidence
--     requirements, and does NOT for the three reference sets: those are emitted
--     sorted whatever ordinals their rows carry
--   * NON-EMPTY TEXT MEANS WHAT IT MEANS IN JAVASCRIPT. All 25 code points
--     ECMAScript String.prototype.trim strips are empty to
--     ops.model_role_is_nonempty_text; U+0085 and U+180E, which it does NOT
--     strip, are not; a lone tab, newline, NBSP, BOM or Unicode space is refused
--     as a title, a mission and a skill, on the writer path and on a direct
--     insert; and text carrying LEGITIMATE embedded whitespace is stored
--     VERBATIM and rebuilds to the byte-identical preimage
--   * a version hole, an ordinal gap, a repeated content digest, a reused
--     idempotency key with different content, and a system-authority role class
--     are each refused, and a refused write leaves nothing behind
--   * update, delete and TRUNCATE are refused on all four relations
--   * a direct insert naming another writer is refused
--   * NAMING THE CURRENT REVISION IS NOT DELEGABLE TO A SHARED LOGIN. On the
--     partner's own authority connection, the pointer writer still REFUSES while
--     this transaction's acting actor is anyone other than that partner's own
--     active human actor -- which is exactly the sponsored-agent case, since
--     partner-authority.js routes codex, claude, joe-local and dell-local onto
--     the partner's login
--   * naming the current revision REFUSES for any session that is not the
--     retained system-authority partner's own authority principal, the pointer
--     ledger stays empty, and the current revision stays null -- no role becomes
--     current by default
--   * the current readback names the POINTER's authority class as
--     pointer_authority_class and carries no bare top-level authority_class, so
--     the pointer's system_authority cannot be misread as the role's declared
--     class, which is at preimage.authority.authority_class and is occupiable
--   * every revision entry reports the role contract version it was stored under
--   * the per-role advisory lock is actually taken, and both writers take it
--   * carr_writer can record a revision and cannot name the current one
--   * THE INSTALLED SHAPE IS THE ONE THE WRITERS DEPEND ON: the thirty named
--     constraints listed there are present on the live relations (the primary and
--     foreign keys the rail does not turn on are not all listed, and the block
--     says which it checks), acting_actor_id is NOT NULL,
--     the three non-empty gates apply ops.model_role_is_nonempty_text in their own
--     definitions, and nothing gates text with the space-only btrim()
--   * no relation here carries an occupant, a qualification, a grant or a
--     numeric floor column
--
-- WHAT THIS FILE DOES NOT PROVE, named rather than implied. Read this list
-- before treating a clean run as coverage:
--   1. THE CROSS-LANGUAGE DIGEST EQUALITY. Whether ops.model_role_digest and
--      model-routing.v5.js's defineRole produce the SAME hash for the same role
--      cannot be shown by a rollback-only fixture, which executes no JavaScript.
--      Both sides are asserted structurally to hash the canonical twelve-field
--      object with no domain tag, and the SQL side reuses
--      ops.portfolio_canonical_json, which migration 0496 already reconciles
--      against the module canonicalJson. A role preimage contains NO NUMBER, so
--      the number-rendering half of that reconciliation cannot arise here. The
--      string half remains live-integration verification, and it is the first
--      thing to check when this rail is exercised end to end. It fails CLOSED if
--      it is ever wrong: the writer refuses on the digest comparison rather than
--      storing a role the two sides hash differently.
--   2. THE CONTENT FREEZE ACROSS TRANSACTIONS. ops.model_role_content_guard
--      compares a revision's created_xid against pg_current_xact_id(), and
--      pg_current_xact_id() returns the TOP-LEVEL transaction id -- so inside
--      one rolled-back transaction every subtransaction shares it and the guard
--      cannot be made to fire. Its presence and attachment are asserted
--      structurally below; its behaviour needs two committed transactions, which
--      this file deliberately does not create.
--   3. CONCURRENCY. Two sessions racing a compare-and-swap needs two sessions.
--      What is proved here is that the lock is real and both writers take it,
--      and that the compare-and-swap refusals fire on their own terms.
--   4. THE POSITIVE CURRENT-POINTER PATH, unless this file is run on the
--      retained system-authority partner's authority connection. On any other
--      connection ops.authority_actor_slug() raises and every pointer assertion
--      becomes a refusal assertion. The block at the end reports EXACTLY which
--      assertions were exercised and which were not, by name.
--   5. THAT A HUMAN WAS PRESENT. To reach the positive pointer path this file
--      SETS carr.acting_actor_slug and carr.verified_human_actor_slug itself.
--      That is not a workaround -- it is the residual gap, demonstrated: those
--      are ordinary transaction-local settings, so any session holding the
--      partner's authority login can establish the same context. The
--      acting-principal binding closes the sponsored-agent flattening on the
--      RUNTIME path, where mcp.js's setWriterActorContext is the only thing that
--      sets them; it does not turn possession of a login into proof of a human,
--      and this file demonstrates precisely that rather than obscuring it.
--   6. THE STRUCTURE-CHECK HALF OF THE NON-EMPTY GATE, in isolation. The CHECK
--      constraints refuse a whitespace-only value at insert, so a revision
--      carrying one cannot be brought into existence for
--      ops.model_role_structure_error to then reject. That ordering is correct
--      and it means only the first of the two gates is observable here; the
--      second is asserted from source below.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_writer text; v_actor_count integer; v_id uuid; v_id2 uuid; v_replay uuid;
  v_digest text; v_digest2 text; v_canonical text; v_readback jsonb;
  v_count integer; v_locks integer; v_def text; v_session text; v_partner_ok boolean;
  v_state text; v_cp integer; v_probe text; v_cur jsonb;
  v_partner text; v_ws_id uuid; v_ws_digest text; v_ws_preimage jsonb;
  v_exercised text[] := array[]::text[];
  v_not_exercised text[] := array[]::text[];
  v_marker constant text := 'model-role-proof-expected-refusal';

  v_role constant text := 'reviewer';

  -- THE 25 CODE POINTS ECMAScript String.prototype.trim STRIPS, as
  -- mcp-server/src/model-role-store.v5.js exports them in
  -- MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS. Written here independently of
  -- ops.model_role_ecmascript_whitespace() on purpose: a fixture that read the
  -- implementation's own list back would agree with it by construction and prove
  -- nothing about whether the list is right.
  v_trim_code_points constant integer[] := array[
    9, 10, 11, 12, 13, 32, 160, 5760,
    8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202,
    8232, 8233, 8239, 8287, 12288, 65279];

  -- NOT stripped by ECMAScript trim, so NOT empty here either. A wider SQL rule
  -- would refuse content defineRole accepts, which is the same divergence as a
  -- narrower one pointing the other way.
  v_not_trimmed constant integer[] := array[133, 6158];

  -- THE PREIMAGE, ASSEMBLED HERE AND NOT READ FROM ANY ROW. Its reference sets
  -- are written in SORTED order because that is what defineRole hashes; the rows
  -- inserted below deliberately carry some of them in the other order, so the
  -- emitter's sort is proved rather than assumed.
  v_preimage constant jsonb := jsonb_build_object(
    'schema_version', 'role-description.v1',
    'tenant', 'carr-internal',
    'role_key', 'reviewer',
    'title', 'Reviewer (synthetic fixture)',
    'mission', 'Synthetic fixture mission: establish independently that delivered work meets its stated contract.',
    'skills', jsonb_build_array(
      'read a diff against the contract it claims to satisfy',
      'reproduce a claimed finding from source rather than from a report'),
    'rules', jsonb_build_array(
      'never grade work you produced',
      'a refusal names the exact missing fact, authority or dependency'),
    'authority', jsonb_build_object(
      'authority_class', 'developer',
      'capability_refs', jsonb_build_array('evidence.read', 'source.read')),
    'evidence_requirements', jsonb_build_array('the exact command run and its output'),
    'quality_floor_refs', jsonb_build_array('floor.evidence_bound', 'floor.independent_review'),
    'minimum_strength_ref', 'strength.high_risk_engineering',
    'task_classes', jsonb_build_array('review.contract', 'review.source'));

  v_scalars constant jsonb := jsonb_build_object(
    'title', 'Reviewer (synthetic fixture)',
    'mission', 'Synthetic fixture mission: establish independently that delivered work meets its stated contract.',
    'minimum_strength_ref', 'strength.high_risk_engineering',
    'authority_class', 'developer');

  v_texts constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'skills', 'ordinal', 1,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'rules', 'ordinal', 1,
      'value', 'a refusal names the exact missing fact, authority or dependency'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- THE SAME SKILLS IN THE OTHER ORDER, and nothing else changed.
  v_texts_reordered constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'skills', 'ordinal', 1,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'rules', 'ordinal', 1,
      'value', 'a refusal names the exact missing fact, authority or dependency'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- ORDINAL 0 AND ORDINAL 2, WITH NOTHING AT 1. The shape a partial insert
  -- leaves behind, and the shape that would rebuild a shorter list.
  v_texts_gapped constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'skills', 'ordinal', 2,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- THE REFERENCE SETS, WITH task_classes AND capability_refs DELIBERATELY
  -- ORDINALED IN REVERSE. If the emitter honoured these ordinals the preimage
  -- would not equal the hand-built one above and the digest would differ.
  v_refs constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'capability_refs', 'ordinal', 0, 'value', 'source.read'),
    jsonb_build_object('field', 'capability_refs', 'ordinal', 1, 'value', 'evidence.read'),
    jsonb_build_object('field', 'quality_floor_refs', 'ordinal', 0, 'value', 'floor.evidence_bound'),
    jsonb_build_object('field', 'quality_floor_refs', 'ordinal', 1, 'value', 'floor.independent_review'),
    jsonb_build_object('field', 'task_classes', 'ordinal', 0, 'value', 'review.source'),
    jsonb_build_object('field', 'task_classes', 'ordinal', 1, 'value', 'review.contract'));

  -- LEGITIMATE EMBEDDED WHITESPACE. Every one of these is non-empty under the
  -- ECMAScript rule and must be stored VERBATIM: defineRole hashes the value as
  -- supplied, so a store that trimmed on the way in would hash to something no
  -- proposer computed. Built with chr() rather than as literal characters so the
  -- fixture cannot be silently altered by an editor normalising whitespace.
  v_ws_title constant text := '  Reviewer (embedded whitespace fixture)  ';
  v_ws_mission constant text :=
    'Mission line one.' || chr(10) || 'Mission line two after a real newline.';
  v_ws_skill constant text :=
    'a skill with a' || chr(9) || 'tab, a' || chr(160) ||
    'no-break space and a trailing space ';
  v_ws_rule constant text :=
    'a rule split across' || chr(8232) || 'a Unicode line separator';
begin
  -- === prerequisites ========================================================
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'model_role_record_revision') then
    raise notice 'SKIPPED: ops.model_role_record_revision is absent; ops/model-role-store.candidate.sql has not been applied here. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'model_role_is_nonempty_text') then
    raise notice 'SKIPPED: ops.model_role_is_nonempty_text is absent; this database carries an OLDER copy of the candidate whose non-empty gate is PostgreSQL''s space-only btrim rather than the ECMAScript rule defineRole applies. RE-APPLYING THE CANDIDATE HERE WILL NOT FIX THAT AND IS NOT MEANT TO: it installs fresh and refuses a database that already carries these objects, because deciding what happens to rows already stored under the older gate is a migration''s job and not candidate source''s. Run this proof on a database built from the current candidate. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_writer_actor_id')
     or not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'ops' and p.proname = 'portfolio_canonical_json') then
    raise notice 'SKIPPED: migration 0496 is absent; this rail reuses its writer context and canonicalizer. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'authority_actor_slug') then
    raise notice 'SKIPPED: migration 0161 is absent; the current-pointer principal is derived from it. NOTHING BELOW RAN.';
    return;
  end if;
  if to_regprocedure('public.digest(bytea,text)') is null then
    raise notice 'SKIPPED: pgcrypto''s public.digest is absent; every digest here is computed with it. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'ops' and table_name = 'model_role_current_pointer'
                    and column_name = 'acting_actor_id') then
    raise notice 'SKIPPED: ops.model_role_current_pointer carries no acting_actor_id; this database predates the acting-principal binding, so every pointer row here would record the partner regardless of who acted. RE-APPLYING THE CANDIDATE WILL NOT ADD IT: the column is in the CREATE TABLE shape of a FRESH install, and the candidate refuses a database that already carries these relations rather than altering one. Run this proof on a database built from the current candidate. NOTHING BELOW RAN.';
    return;
  end if;
  select count(*) into v_actor_count from public.actor where active and kind <> 'human';
  if v_actor_count < 1 then
    raise notice 'SKIPPED: no active non-human actor exists for the writer context; this proof creates none. NOTHING BELOW RAN.';
    return;
  end if;
  select slug into v_writer from public.actor where active and kind <> 'human'
   order by slug collate "C" limit 1;
  perform set_config('carr.acting_actor_slug', v_writer, true);
  perform set_config('carr.verified_human_actor_slug', '', true);

  -- === the non-empty-text rule, before anything is stored ===================
  --
  -- PURE-FUNCTION ASSERTIONS, and the only ones in this file that need no rows.
  -- They are first because everything below depends on the two implementations
  -- of "non-empty" being the same rule: if they diverge, a direct writer can
  -- store text defineRole refuses, and because reads revalidate every revision
  -- and this rail has no repair path, that role's reads refuse permanently.
  foreach v_cp in array v_trim_code_points loop
    if ops.model_role_is_nonempty_text(chr(v_cp)) then
      raise exception 'U+% is stripped by ECMAScript trim but is not empty to ops.model_role_is_nonempty_text; a direct writer could store a value defineRole refuses',
        upper(to_hex(v_cp));
    end if;
  end loop;
  -- The whole set at once, and the set doubled: a run of trimmed code points is
  -- still empty, which is what a real BOM-plus-newline paste looks like.
  select string_agg(chr(cp), '' order by cp) into v_probe from unnest(v_trim_code_points) as cp;
  if ops.model_role_is_nonempty_text(v_probe)
     or ops.model_role_is_nonempty_text(v_probe || v_probe) then
    raise exception 'a run of ECMAScript-trimmed code points is not empty to ops.model_role_is_nonempty_text';
  end if;
  -- And the other direction: NOT wider than ECMAScript, or this rail would
  -- refuse content the kernel accepts.
  foreach v_cp in array v_not_trimmed loop
    if not ops.model_role_is_nonempty_text(chr(v_cp)) then
      raise exception 'U+% is NOT stripped by ECMAScript trim, but ops.model_role_is_nonempty_text calls it empty; this rail is refusing content defineRole accepts',
        upper(to_hex(v_cp));
    end if;
  end loop;
  -- Legitimate content survives, including whitespace INSIDE and around it.
  if not ops.model_role_is_nonempty_text(v_ws_skill)
     or not ops.model_role_is_nonempty_text(v_ws_rule)
     or not ops.model_role_is_nonempty_text(v_ws_title)
     or not ops.model_role_is_nonempty_text(chr(9) || 'x' || chr(10)) then
    raise exception 'text carrying embedded or surrounding whitespace was called empty; only whitespace-ONLY values are empty';
  end if;
  -- The count is part of the claim: 25, not 1 as btrim(text) would give.
  if length(ops.model_role_ecmascript_whitespace()) <> 25 then
    raise exception 'the ECMAScript whitespace set holds % code points, not 25',
      length(ops.model_role_ecmascript_whitespace());
  end if;
  v_exercised := array_append(v_exercised, 'ecmascript-trim-rule-matches-defineRole-both-directions');

  -- === the digest, computed from bytes assembled in this file ===============
  v_digest := ops.model_role_digest_of_preimage(v_preimage);
  v_canonical := ops.portfolio_canonical_json(v_preimage);

  -- NO DOMAIN TAG. defineRole hashes the object itself; a leading '[' would mean
  -- a second digest scheme had appeared. The first C-sorted key of a role
  -- preimage is "authority", so the canonical bytes begin there.
  if left(v_canonical, 13) <> '{"authority":' then
    raise exception 'the hashed preimage is not the bare role-description.v1 object: %', left(v_canonical, 60);
  end if;
  if v_digest !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'the role digest is not a sha256 reference: %', v_digest;
  end if;
  v_exercised := array_append(v_exercised, 'digest-shape-and-no-domain-tag');

  -- === revision 1 ===========================================================
  v_id := ops.model_role_record_revision(
    v_role, 1, gen_random_uuid(), v_digest, v_scalars, v_texts, v_refs);

  -- THE STRONGEST ASSERTION IN THIS FILE. The rebuild from the stored rows is
  -- compared to the hand-assembled object field for field, not merely through a
  -- hash that could agree for reasons nobody checked.
  if ops.model_role_preimage(v_id) <> v_preimage then
    raise exception 'the preimage rebuilt from the stored rows is not the one that was hashed: %',
      ops.model_role_preimage(v_id);
  end if;
  if ops.model_role_digest(v_id) <> v_digest then
    raise exception 'the digest recomputed from the stored rows is %, not %',
      ops.model_role_digest(v_id), v_digest;
  end if;
  if ops.model_role_structure_error(v_id) is not null then
    raise exception 'a freshly recorded revision is structurally invalid: %',
      ops.model_role_structure_error(v_id);
  end if;
  v_exercised := array_append(v_exercised, 'stored-rows-rebuild-equals-hashed-preimage');

  -- THE ROLE CONTRACT VERSION IS REPORTED, not assumed. It is what lets a future
  -- reader refuse a revision it cannot revalidate BY NAME rather than by failing
  -- somewhere inside a rebuild -- and what makes "write a versioned reader" the
  -- remedy instead of "rewrite the stored rows", which append-only forbids.
  if (ops.model_role_revision_readback(v_id) ->> 'role_schema_version')
       <> ops.model_role_schema_version() then
    raise exception 'a revision readback does not report the role contract version it was stored under';
  end if;
  v_exercised := array_append(v_exercised, 'revision-entry-reports-stored-role-schema-version');

  -- REFERENCE SETS ARE EMITTED SORTED, whatever ordinals their rows carry. The
  -- rows above deliberately number task_classes and capability_refs in reverse,
  -- and the preimage comparison passed anyway.
  if ops.model_role_ref_array(v_id, 'task_classes')
       <> jsonb_build_array('review.contract', 'review.source') then
    raise exception 'reference sets are being emitted in row order rather than sorted: %',
      ops.model_role_ref_array(v_id, 'task_classes');
  end if;
  -- TEXT LISTS ARE EMITTED IN ORDINAL ORDER, because their order is hashed.
  if ops.model_role_text_array(v_id, 'skills') -> 0
       <> to_jsonb('read a diff against the contract it claims to satisfy'::text) then
    raise exception 'text lists are not emitted in their stored ordinal order';
  end if;
  v_exercised := array_append(v_exercised, 'ref-sets-sorted-and-text-lists-ordinal');

  -- === revision 2: the same role, one list reordered ========================
  v_digest2 := ops.model_role_digest_of_preimage(
    jsonb_set(v_preimage, '{skills}', jsonb_build_array(
      'reproduce a claimed finding from source rather than from a report',
      'read a diff against the contract it claims to satisfy')));
  if v_digest2 = v_digest then
    raise exception 'reordering a hashed text list did not change the role digest';
  end if;
  v_id2 := ops.model_role_record_revision(
    v_role, 2, gen_random_uuid(), v_digest2, v_scalars, v_texts_reordered, v_refs);
  if ops.model_role_digest(v_id2) <> v_digest2 then
    raise exception 'revision 2 does not hash to the digest it was recorded under';
  end if;
  -- HISTORY IS PRESERVED. Revision 1 is still readable, still hashes to its own
  -- digest, and was not rewritten by revision 2.
  if ops.model_role_digest(v_id) <> v_digest then
    raise exception 'recording revision 2 changed what revision 1 hashes to';
  end if;
  select jsonb_array_length(ops.model_role_readback(v_role) -> 'history') into v_count;
  if v_count <> 2 then
    raise exception 'the role history holds % revisions, not 2', v_count;
  end if;
  v_exercised := array_append(v_exercised, 'versioned-revisions-and-preserved-history');

  -- === refusals on the record path ==========================================

  -- A VERSION HOLE. "The version before this one" must always be answerable.
  -- The content is fresh, so the duplicate-content refusal cannot fire first and
  -- the observed refusal is unambiguously about the version.
  begin
    perform ops.model_role_record_revision(
      v_role, 5, gen_random_uuid(),
      ops.model_role_digest_of_preimage(jsonb_set(v_preimage, '{title}', '"Reviewer (hole)"')),
      jsonb_set(v_scalars, '{title}', '"Reviewer (hole)"'), v_texts, v_refs);
    raise exception '%: a non-contiguous revision number was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%the next revision is%' then
      raise exception 'wrong refusal for a version hole: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-version-hole');

  -- CONTENT THAT REPEATS AN EXISTING REVISION. A revision recording no change is
  -- not history; rolling back is done with the current pointer.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), v_digest, v_scalars, v_texts, v_refs);
    raise exception '%: a duplicate content digest was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%exact bytes%' then
      raise exception 'wrong refusal for duplicate content: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-duplicate-content');

  -- AN ORDINAL GAP. The structure check fires before the digest check, so the
  -- caller learns the shape is wrong rather than only that a hash disagreed.
  -- The digest is a placeholder no stored revision carries, so the
  -- duplicate-content refusal cannot fire first.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('d', 64),
      v_scalars, v_texts_gapped, v_refs);
    raise exception '%: a gapped list was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%contiguously ordinaled%' then
      raise exception 'wrong refusal for a gapped list: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-ordinal-gap');

  -- A DIGEST THE STORED ROWS DO NOT PRODUCE. The caller's hash is compared, and
  -- a refused write leaves nothing behind.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('a', 64),
      v_scalars, v_texts_gapped || jsonb_build_array(jsonb_build_object(
        'field', 'skills', 'ordinal', 1, 'value', 'a third distinct skill')), v_refs);
    raise exception '%: a body/digest mismatch was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%the stored rows produce%' then
      raise exception 'wrong refusal for a body/digest mismatch: %', sqlerrm;
    end if;
  end;
  select count(*) into v_count from ops.model_role_revision
   where tenant = ops.model_role_tenant() and role_key = v_role;
  if v_count <> 2 then
    raise exception 'a refused write left % revisions behind instead of 2', v_count;
  end if;
  v_exercised := array_append(v_exercised, 'refuses-body-digest-drift-and-leaves-nothing');

  -- A ROLE DECLARING RETAINED SYSTEM AUTHORITY. No occupant could ever hold it.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('e', 64),
      jsonb_set(v_scalars, '{authority_class}', '"system_authority"'), v_texts, v_refs);
    raise exception '%: a system-authority role class was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%system authority%' and sqlerrm not like '%authority_class%'
       and sqlerrm not like '%model_role_revision_authority%' then
      raise exception 'wrong refusal for a system-authority role class: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-system-authority-role-class');

  -- AN UNSETTLED ROLE KEY.
  begin
    perform ops.model_role_record_revision(
      'chief_of_staff', 1, gen_random_uuid(), 'sha256:' || repeat('f', 64),
      v_scalars, v_texts, v_refs);
    raise exception '%: an unsettled role key was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-unsettled-role-key');

  -- === idempotency ==========================================================
  declare v_key constant uuid := gen_random_uuid();
  begin
    v_replay := ops.model_role_record_revision(
      v_role, 3, v_key, ops.model_role_digest_of_preimage(
        jsonb_set(v_preimage, '{title}', '"Reviewer (synthetic fixture, third revision)"')),
      jsonb_set(v_scalars, '{title}', '"Reviewer (synthetic fixture, third revision)"'),
      v_texts, v_refs);
    -- AN EXACT REPLAY RETURNS THE SAME ROW rather than writing a second one.
    if ops.model_role_record_revision(
         v_role, 3, v_key, ops.model_role_digest_of_preimage(
           jsonb_set(v_preimage, '{title}', '"Reviewer (synthetic fixture, third revision)"')),
         jsonb_set(v_scalars, '{title}', '"Reviewer (synthetic fixture, third revision)"'),
         v_texts, v_refs) <> v_replay then
      raise exception 'an exact idempotent replay did not return the first row';
    end if;
    select count(*) into v_count from ops.model_role_revision
     where tenant = ops.model_role_tenant() and role_key = v_role;
    if v_count <> 3 then
      raise exception 'an idempotent replay wrote a second row: % revisions exist', v_count;
    end if;

    -- THE SAME KEY WITH DIFFERENT CONTENT IS A DIFFERENT REQUEST WEARING ITS
    -- NAME, and is refused rather than silently returning the first row.
    begin
      perform ops.model_role_record_revision(
        v_role, 3, v_key, v_digest2, v_scalars, v_texts_reordered, v_refs);
      raise exception '%: a reused idempotency key with different content was accepted', v_marker;
    exception when others then
      if sqlerrm like v_marker || '%' then raise; end if;
      if sqlerrm not like '%idempotency key%' then
        raise exception 'wrong refusal for an idempotency mismatch: %', sqlerrm;
      end if;
    end;
  end;
  v_exercised := array_append(v_exercised, 'idempotent-replay-and-mismatch-refusal');

  -- === whitespace-only text is refused on the writer path ===================
  --
  -- THE REPRODUCER FOR THE DIVERGENCE THIS RAIL CLOSES. Under a btrim(value)
  -- gate every one of these values passes, hashes consistently and commits --
  -- and is then refused forever by the reader, which revalidates every revision
  -- through defineRole and has no repair path. Each attempt below carries a
  -- placeholder digest no stored revision holds, so the duplicate-content
  -- refusal cannot fire first and the observed refusal is unambiguously about
  -- the text.
  begin
    perform ops.model_role_record_revision(
      v_role, 4, gen_random_uuid(), 'sha256:' || repeat('1', 64),
      jsonb_set(v_scalars, '{title}', to_jsonb(chr(9))), v_texts, v_refs);
    raise exception '%: a title of one tab was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%ECMAScript trim%' and sqlerrm not like '%_nonempty%' then
      raise exception 'wrong refusal for a whitespace-only title: %', sqlerrm;
    end if;
  end;
  begin
    perform ops.model_role_record_revision(
      v_role, 4, gen_random_uuid(), 'sha256:' || repeat('2', 64),
      jsonb_set(v_scalars, '{mission}', to_jsonb(chr(160) || chr(65279))), v_texts, v_refs);
    raise exception '%: a mission of an NBSP and a BOM was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%ECMAScript trim%' and sqlerrm not like '%_nonempty%' then
      raise exception 'wrong refusal for a whitespace-only mission: %', sqlerrm;
    end if;
  end;
  -- Every representative class of trimmed code point, as a SKILL: ASCII control,
  -- newline, Latin-1 NBSP, a Unicode space separator, a Unicode line separator
  -- and the BOM. These are the values a copy-paste actually produces.
  foreach v_cp in array array[9, 10, 13, 160, 8195, 8232, 12288, 65279] loop
    begin
      perform ops.model_role_record_revision(
        v_role, 4, gen_random_uuid(), 'sha256:' || repeat('3', 64), v_scalars,
        jsonb_set(v_texts, '{0,value}', to_jsonb(chr(v_cp))), v_refs);
      raise exception '%: a skill of only U+% was accepted', v_marker, upper(to_hex(v_cp));
    exception when others then
      if sqlerrm like v_marker || '%' then raise; end if;
      if sqlerrm not like '%ECMAScript trim%' and sqlerrm not like '%_nonempty%'
         and sqlerrm not like '%violates check constraint%' then
        raise exception 'wrong refusal for a skill of only U+%: %', upper(to_hex(v_cp)), sqlerrm;
      end if;
    end;
  end loop;
  select count(*) into v_count from ops.model_role_revision
   where tenant = ops.model_role_tenant() and role_key = v_role;
  if v_count <> 3 then
    raise exception 'a refused whitespace write left % revisions behind instead of 3', v_count;
  end if;
  v_exercised := array_append(v_exercised, 'refuses-whitespace-only-title-mission-and-skill');

  -- A DIRECT INSERT OF WHITESPACE-ONLY CONTENT, which is the path the writer
  -- functions do not cover. On a session that holds INSERT (the schema owner)
  -- the CHECK constraint speaks; on a runtime bundle the grant posture does.
  -- Both are correct refusals; what must never happen is that it succeeds,
  -- because one such row would refuse this role's reads permanently.
  begin
    insert into ops.model_role_revision_text(revision_id, field, ordinal, value)
    values (v_id, 'skills', 99, chr(9));
    raise exception '%: a directly inserted whitespace-only skill was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    get stacked diagnostics v_state = returned_sqlstate;
    if v_state not in ('23514', '42501') then
      raise exception 'wrong refusal (SQLSTATE %) for a directly inserted whitespace-only skill: %',
        v_state, sqlerrm;
    end if;
    v_exercised := array_append(v_exercised, format(
      'refuses-direct-whitespace-only-content-insert (SQLSTATE %s: %s)', v_state,
      case v_state when '23514' then 'the check constraint'
                   when '42501' then 'the grant posture' else 'unexpected' end));
  end;

  -- === legitimate embedded whitespace is stored VERBATIM ====================
  --
  -- THE OTHER HALF OF THE RULE, and the one a too-eager gate would break. A tab
  -- inside a sentence, a real newline in a mission, an NBSP between words and a
  -- trailing space are all legitimate role text. defineRole hashes the value AS
  -- SUPPLIED, so this rail must store it unchanged: a store that trimmed would
  -- produce a digest no proposer computed and every readback would then refuse.
  v_ws_preimage := v_preimage
    || jsonb_build_object(
         'title', v_ws_title,
         'mission', v_ws_mission,
         'skills', jsonb_build_array(v_ws_skill, 'an ordinary second skill'),
         'rules', jsonb_build_array(v_ws_rule, 'an ordinary second rule'));
  v_ws_digest := ops.model_role_digest_of_preimage(v_ws_preimage);
  v_ws_id := ops.model_role_record_revision(
    v_role, 4, gen_random_uuid(), v_ws_digest,
    jsonb_build_object(
      'title', v_ws_title, 'mission', v_ws_mission,
      'minimum_strength_ref', 'strength.high_risk_engineering',
      'authority_class', 'developer'),
    jsonb_build_array(
      jsonb_build_object('field', 'skills', 'ordinal', 0, 'value', v_ws_skill),
      jsonb_build_object('field', 'skills', 'ordinal', 1, 'value', 'an ordinary second skill'),
      jsonb_build_object('field', 'rules', 'ordinal', 0, 'value', v_ws_rule),
      jsonb_build_object('field', 'rules', 'ordinal', 1, 'value', 'an ordinary second rule'),
      jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
        'value', 'the exact command run and its output')),
    v_refs);
  -- BYTE FOR BYTE, not merely "accepted". A single trimmed space anywhere makes
  -- this comparison fail, which is exactly what it is here to catch.
  if ops.model_role_preimage(v_ws_id) <> v_ws_preimage then
    raise exception 'embedded whitespace was not stored verbatim; the rebuilt preimage differs from the one that was hashed';
  end if;
  if ops.model_role_digest(v_ws_id) <> v_ws_digest then
    raise exception 'the revision carrying embedded whitespace does not hash to the digest it was recorded under';
  end if;
  if ops.model_role_structure_error(v_ws_id) is not null then
    raise exception 'legitimate embedded whitespace was called structurally invalid: %',
      ops.model_role_structure_error(v_ws_id);
  end if;
  v_exercised := array_append(v_exercised, 'stores-legitimate-embedded-whitespace-verbatim');

  -- === append-only, truncate, and direct writes =============================
  --
  -- TWO MECHANISMS REFUSE THESE, and which one speaks depends on who is running
  -- the file: the append-only trigger for a session that holds UPDATE/DELETE
  -- (the schema owner), and the grant posture for a runtime bundle, which holds
  -- neither. Both are correct refusals and the assertion accepts either; what
  -- must never happen is that the statement succeeds.
  begin
    update ops.model_role_revision set title = 'edited' where id = v_id;
    raise exception '%: a revision was updated', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for an update: %', sqlerrm;
    end if;
  end;
  begin
    delete from ops.model_role_revision where id = v_id;
    raise exception '%: a revision was deleted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for a delete: %', sqlerrm;
    end if;
  end;
  begin
    delete from ops.model_role_revision_text where revision_id = v_id;
    raise exception '%: revision content was deleted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for a content delete: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'append-only-on-revisions-and-content');

  -- TRUNCATE IS A STATEMENT EVENT, so the row-level append-only trigger cannot
  -- see it. Without a statement-level BEFORE TRUNCATE trigger, "a superseded
  -- current pointer cannot be erased" would be true of every runtime bundle,
  -- from which the privilege is revoked, and FALSE of the table owner, from whom
  -- TRUNCATE cannot be revoked at all. The pointer relation is chosen because
  -- nothing references it, so a foreign-key complaint cannot stand in for the
  -- refusal being asserted.
  select count(*) into v_count from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and not t.tgisinternal
     and c.relname in ('model_role_revision', 'model_role_revision_text',
                       'model_role_revision_ref', 'model_role_current_pointer')
     and (t.tgtype & 32) <> 0;
  if v_count <> 4 then
    raise exception 'TRUNCATE is guarded on % of the four role relations, not 4', v_count;
  end if;
  begin
    truncate ops.model_role_current_pointer;
    raise exception '%: the current-pointer ledger was truncated', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for a truncate: %', sqlerrm;
    end if;
  end;
  v_exercised := array_append(v_exercised, 'refuses-truncate-on-all-four-relations');

  -- A DIRECT INSERT NAMING ANOTHER WRITER.
  --
  -- WHICH MECHANISM SPEAKS DEPENDS ON THE DATABASE, and all three outcomes are
  -- correct refusals rather than one assertion with three meanings:
  --   * on a runtime bundle, the grant posture (42501) -- direct INSERT is
  --     granted to nobody, so the guard is never reached and only the grant is
  --     proved;
  --   * on a database holding exactly one active actor, the subselect is null
  --     and the NOT NULL column speaks (23502) -- also only a column, not the
  --     guard;
  --   * on the schema owner with two or more active actors,
  --     ops.model_role_revision_guard actually runs and refuses the
  --     misattribution (42501 with its own message), which is the case this
  --     assertion is really about.
  -- The observed SQLSTATE is recorded in the report so a reader knows which of
  -- the three a given run actually exercised instead of assuming the third.
  begin
    insert into ops.model_role_revision(
      tenant, role_key, revision_no, idempotency_key, schema_version, role_digest,
      title, mission, minimum_strength_ref, authority_class, recorded_by_actor_id)
    values (ops.model_role_tenant(), v_role, 5, gen_random_uuid(),
      ops.model_role_schema_version(), 'sha256:' || repeat('b', 64),
      'forged', 'forged', 'strength.high_risk_engineering', 'developer',
      (select id from public.actor
        where active and id <> ops.portfolio_writer_actor_id() order by id limit 1));
    raise exception '%: a directly inserted, misattributed revision was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    get stacked diagnostics v_state = returned_sqlstate;
    if v_state not in ('42501', '23502') then
      raise exception 'wrong refusal (SQLSTATE %) for a direct misattributed insert: %',
        v_state, sqlerrm;
    end if;
    v_exercised := array_append(v_exercised, format(
      'refuses-direct-misattributed-insert (SQLSTATE %s: %s)', v_state,
      case
        when v_state = '23502' then 'the NOT NULL column, because this database holds one active actor -- the writer guard was NOT reached'
        when sqlerrm like '%authenticated writer context%' then 'ops.model_role_revision_guard itself'
        else 'the grant posture -- the writer guard was NOT reached'
      end));
    if v_state = '23502' or sqlerrm not like '%authenticated writer context%' then
      v_not_exercised := array_append(v_not_exercised,
        'ops.model_role_revision_guard''s misattribution refusal itself: this session was refused earlier, by the grant posture or by a NOT NULL column. Re-run as the schema owner on a database with two or more active actors to reach the guard.');
    end if;
  end;

  -- === the lock is real, and both writers take it ===========================
  --
  -- The lock helper is granted to no runtime bundle on purpose, so calling it
  -- directly is only possible for a session that owns it. When it is not
  -- reachable, that is reported rather than skipped silently, and the SOURCE
  -- assertions below still run.
  if has_function_privilege(current_user, 'ops.model_role_lock(text)', 'execute') then
    perform ops.model_role_lock(v_role);
    select count(*) into v_locks from pg_locks
     where locktype = 'advisory' and pid = pg_backend_pid() and granted;
    if v_locks < 1 then
      raise exception 'ops.model_role_lock did not take an advisory lock';
    end if;
    v_exercised := array_append(v_exercised, 'per-role-advisory-lock-is-actually-taken');
  else
    v_not_exercised := array_append(v_not_exercised, format(
      'the runtime observation that ops.model_role_lock takes an advisory lock: %s does not hold EXECUTE on it, which is the intended posture. Only the source assertion that both writers call it ran.',
      current_user));
  end if;
  select pg_get_functiondef(p.oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_record_revision' limit 1;
  if v_def not like '%model_role_lock%' then
    raise exception 'the revision writer does not take the per-role lock';
  end if;

  -- THE STRUCTURE CHECK IS THE SECOND HALF OF THE NON-EMPTY GATE, and it cannot
  -- be observed from a row -- the CHECK constraints refuse first, which is the
  -- correct ordering. Asserted from the installed source instead, because a
  -- schema where somebody dropped the clause would pass every row-level
  -- assertion in this file.
  select pg_get_functiondef(p.oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_structure_error' limit 1;
  if v_def not like '%model_role_is_nonempty_text%' then
    raise exception 'ops.model_role_structure_error does not apply the kernel''s non-empty-text rule, so a poisoned revision could still be called complete and become current';
  end if;
  -- BOTH content relations are swept for an unknown field, not only the text one.
  if v_def not like '%model_role_revision_ref%'
     or v_def not like '%reference rows name an unknown field%' then
    raise exception 'ops.model_role_structure_error sweeps text rows for an unknown field but not reference rows';
  end if;
  v_not_exercised := array_append(v_not_exercised,
    'the structure-check half of the non-empty gate as a runtime refusal: the CHECK constraints refuse a whitespace-only value at insert, so no revision carrying one can be brought into existence for ops.model_role_structure_error to reject. Its clause is asserted from the installed source instead.');

  select pg_get_functiondef(p.oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_set_current_revision' limit 1;
  if v_def not like '%model_role_lock%' then
    raise exception 'the current-pointer writer does not take the per-role lock';
  end if;
  -- THE PRINCIPAL IS DERIVED IN THE WRITER, not compared to a parameter, and BOTH
  -- halves are derived: the authority login scope AND the acting principal.
  -- Checked against the installed source, because a schema where somebody
  -- replaced either derivation with an argument would pass every other assertion
  -- here.
  if v_def not like '%authority_actor_slug()%'
     or v_def not like '%model_role_system_authority_partner()%' then
    raise exception 'the current-pointer writer does not derive its authority login scope from the authenticated session';
  end if;
  if v_def not like '%portfolio_writer_actor_id()%' then
    raise exception 'the current-pointer writer does not derive the ACTING principal, so a sponsored agent on the partner''s shared authority login would be recorded as the partner';
  end if;
  if v_def ~* 'p_(actor|approved|verified|partner|principal|authority)' then
    raise exception 'the current-pointer writer takes an identity, an approval or an authority as a parameter';
  end if;
  -- The guard re-derives both facts too, so a writer bug cannot skip either.
  select pg_get_functiondef(p.oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_pointer_guard' limit 1;
  if v_def not like '%authority_actor_slug()%'
     or v_def not like '%portfolio_writer_actor_id()%' then
    raise exception 'the current-pointer guard does not independently re-derive both the authority login scope and the acting principal';
  end if;
  v_exercised := array_append(v_exercised, 'both-writers-lock-and-the-pointer-principal-is-derived-twice-in-source');

  -- The content guard exists and is attached to both content relations, and the
  -- revision carries the transaction that created it. Its BEHAVIOUR needs two
  -- committed transactions; see the header, item 2.
  select count(*) into v_count from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
    join pg_proc p on p.oid = t.tgfoid
   where n.nspname = 'ops' and p.proname = 'model_role_content_guard' and not t.tgisinternal;
  if v_count <> 2 then
    raise exception 'the content freeze is attached to % relations, not 2', v_count;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'ops' and table_name = 'model_role_revision'
                    and column_name = 'created_xid') then
    raise exception 'the revision carries no creating transaction, so its content cannot be frozen';
  end if;
  v_not_exercised := array_append(v_not_exercised,
    'content-freeze-behaviour (needs two committed transactions); only its presence and attachment were checked)');

  -- === the current pointer ==================================================
  v_readback := ops.model_role_readback(v_role);
  if v_readback -> 'current' <> 'null'::jsonb then
    raise exception 'a role became current without any system-authority act';
  end if;
  if (v_readback ->> 'confers_authority') <> 'false'
     or (v_readback ->> 'occupant_bound') <> 'false'
     or (v_readback ->> 'measured_qualification_bound') <> 'false'
     or (v_readback ->> 'signed') <> 'false'
     or (v_readback ->> 'capability_token_issued') <> 'false' then
    raise exception 'a role readback claims more than re-derivation from committed rows: %', v_readback;
  end if;
  v_exercised := array_append(v_exercised, 'no-default-current-role-and-scoped-readback');

  v_session := session_user;
  v_partner_ok := false;
  begin
    v_partner_ok := (ops.authority_actor_slug() = ops.model_role_system_authority_partner());
  exception when others then
    v_partner_ok := false;
  end;

  if not v_partner_ok then
    -- THE REFUSAL PATH, which is what any non-authority session can prove.
    --
    -- WHICH REFUSAL ARRIVES DEPENDS ON THE SESSION, and the assertion has to
    -- accept both without accepting anything else:
    --   * on a carr_writer session, EXECUTE on this function is revoked, so
    --     PostgreSQL raises insufficient_privilege (42501) with the message
    --     "permission denied for function model_role_set_current_revision" --
    --     which carries no "authority" substring at all. That run mode is
    --     claimed by this file's own header, so a message-only match would abort
    --     the whole proof on a supported connection;
    --   * on any other non-partner session, ops.authority_actor_slug() raises
    --     P0001 naming the authority principal, or the partner comparison raises
    --     42501 naming the retained system authority.
    -- SQLSTATE is read rather than inferred from the message, and an unrelated
    -- failure -- a missing function (42883), a null column (23502), a
    -- serialization refusal (40001) -- still fails this assertion.
    begin
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 1, v_digest, true, null);
      raise exception '%: a current pointer was set without the retained system authority', v_marker;
    exception when others then
      if sqlerrm like v_marker || '%' then raise; end if;
      get stacked diagnostics v_state = returned_sqlstate;
      if v_state <> '42501' and sqlerrm not like '%authority%' then
        raise exception 'wrong refusal (SQLSTATE %) for an unauthorized current-pointer change: %',
          v_state, sqlerrm;
      end if;
      v_exercised := array_append(v_exercised, format(
        'refuses-current-pointer-without-system-authority (SQLSTATE %s: %s)', v_state,
        case
          when sqlerrm like '%permission denied%' then 'the grant posture -- EXECUTE is revoked, so the writer was never entered'
          when sqlerrm like '%not an admitted human authority principal%' then 'ops.authority_actor_slug() -- this session_user maps to no partner'
          else 'the writer''s retained-system-authority comparison'
        end));
    end;
    select count(*) into v_count from ops.model_role_current_pointer;
    if v_count <> 0 then
      raise exception 'the current-pointer ledger holds % rows after a refused change', v_count;
    end if;
    if ops.model_role_current_revision(v_role) is not null then
      raise exception 'a refused current-pointer change still selected a revision';
    end if;
    v_not_exercised := array_append(v_not_exercised, format(
      'the POSITIVE current-pointer path, the acting-principal refusal, and every compare-and-swap refusal (stale expectation, creation collision, already-current, unknown revision, wrong digest). This session is %s, which ops.authority_actor_slug() does not admit as the retained system-authority partner. Re-run this file on that partner''s authority connection to exercise them.',
      v_session));
  else
    v_partner := ops.model_role_system_authority_partner();
    -- THE POSITIVE PATH, available only on the retained partner's connection.
    if not exists (select 1 from public.actor
                    where slug = v_partner and active and kind = 'human') then
      v_not_exercised := array_append(v_not_exercised,
        'the positive current-pointer path: the authority session is the retained partner, but no active human actor exists for that slug, so the writer refuses. No actor is created here.');
    else
      -- ============================================================
      -- FIRST, THE REFUSAL THAT MATTERS MOST, and it runs on the PARTNER'S OWN
      -- authority connection: the acting actor is still the non-human writer
      -- this file established above, which is exactly the shape of a sponsored
      -- agent acting on the partner's shared login. partner-authority.js routes
      -- codex, claude, joe-local and dell-local onto this same connection, and
      -- ops.authority_actor_slug() answers with the PARTNER for every one of
      -- them. Naming the current revision is non-delegable, so the writer must
      -- refuse here even though the authority login is beyond reproach.
      -- ============================================================
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 1, v_digest, true, null);
        raise exception '%: the current pointer was set while the acting actor was %, not the partner',
          v_marker, v_writer;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        get stacked diagnostics v_state = returned_sqlstate;
        if v_state <> '42501' or sqlerrm not like '%non-delegable%' then
          raise exception 'wrong refusal (SQLSTATE %) for a non-partner acting actor on the partner authority login: %',
            v_state, sqlerrm;
        end if;
        if sqlerrm not like '%' || v_writer || '%' then
          raise exception 'the acting-principal refusal does not name the actor that actually acted: %', sqlerrm;
        end if;
      end;
      select count(*) into v_count from ops.model_role_current_pointer;
      if v_count <> 0 then
        raise exception 'a refused acting-principal change still wrote % pointer rows', v_count;
      end if;
      v_exercised := array_append(v_exercised,
        'refuses-current-pointer-when-acting-actor-is-not-the-partner-human-on-the-shared-authority-login');

      -- ============================================================
      -- NOW THE POSITIVE PATH -- AND READ THIS BEFORE READING IT AS A PROOF OF
      -- IDENTITY. To reach it this file SETS the actor context itself, with the
      -- same two ordinary transaction-local settings mcp.js's
      -- setWriterActorContext uses. That is not a shortcut around a boundary; it
      -- IS the residual gap, demonstrated in place: any session holding this
      -- login can establish the same context, so what the record layer enforces
      -- is a TRUSTED RUNTIME CONTEXT and not the presence of a human. What the
      -- binding does buy is the refusal just above -- on the runtime path,
      -- where these settings are written only by setWriterActorContext from the
      -- authenticated actor, a sponsored agent cannot reach the positive path at
      -- all. Header item 5 says the same thing; it is repeated here because this
      -- is the line that would otherwise be quoted out of context.
      -- ============================================================
      perform set_config('carr.acting_actor_slug', v_partner, true);
      perform set_config('carr.verified_human_actor_slug', v_partner, true);

      -- Creation: the expectation is null and the creation assertion is its own
      -- parameter, so "create" is never inferred from an omitted argument.
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 1, v_digest, true, null);
      v_cur := ops.model_role_current_revision(v_role);
      if (v_cur ->> 'revision_no')::integer <> 1 then
        raise exception 'the created current pointer does not name revision 1';
      end if;
      -- THE TWO PRINCIPALS ARE RECORDED SEPARATELY AND BOTH ARE READABLE. The
      -- login scope and who acted are different facts; a readback that reported
      -- only the first is the flattening this binding exists to end.
      if (v_cur ->> 'set_by_partner_slug') <> v_partner then
        raise exception 'the current pointer does not record the authority login scope';
      end if;
      if (v_cur ->> 'set_by_acting_actor_slug') <> v_partner then
        raise exception 'the current pointer records the acting principal as %, not %',
          v_cur ->> 'set_by_acting_actor_slug', v_partner;
      end if;
      if (v_cur ->> 'set_by_acting_actor_id') is null then
        raise exception 'the current pointer records no acting actor id';
      end if;
      -- THE POINTER'S AUTHORITY CLASS IS NAMED AS THE POINTER'S. On a rail whose
      -- central rule is that no ROLE may declare system_authority, a bare
      -- top-level authority_class of 'system_authority' on this readback is a
      -- misread waiting to happen. The role's own class stays where it belongs.
      if (v_cur ->> 'pointer_authority_class') <> 'system_authority'
         or (v_cur ->> 'pointer_authority_grant_kind') <> 'retained_system_authority' then
        raise exception 'the current readback does not name the pointer''s own authority under pointer_authority_*: %', v_cur;
      end if;
      if v_cur ? 'authority_class' or v_cur ? 'authority_grant_kind' then
        raise exception 'the current readback still merges a bare authority_class, which reads as the ROLE''s declared class';
      end if;
      if (v_cur -> 'preimage' -> 'authority' ->> 'authority_class') <> 'developer' then
        raise exception 'the role''s own declared authority class is not where a reader looks for it';
      end if;
      v_exercised := array_append(v_exercised,
        'creates-current-pointer-under-retained-system-authority-recording-login-scope-and-acting-principal-separately');

      -- A SECOND CREATION COLLIDES rather than overwriting.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, true, null);
        raise exception '%: a second creation overwrote an existing current pointer', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%creation compare-and-swap cannot move it%' then
          raise exception 'wrong refusal for a creation collision: %', sqlerrm;
        end if;
      end;
      v_exercised := array_append(v_exercised, 'refuses-creation-collision');

      -- A STALE EXPECTATION LOSES.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, false, 7);
        raise exception '%: a stale compare-and-swap was accepted', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%not the expected%' then
          raise exception 'wrong refusal for a stale compare-and-swap: %', sqlerrm;
        end if;
      end;
      v_exercised := array_append(v_exercised, 'refuses-stale-compare-and-swap');

      -- A DIGEST THE NAMED REVISION DOES NOT HAVE.
      begin
        perform ops.model_role_set_current_revision(
          v_role, gen_random_uuid(), 2, 'sha256:' || repeat('c', 64), false, 1);
        raise exception '%: a current pointer named a digest its revision does not have', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%hashes to%' then
          raise exception 'wrong refusal for a stale pointer digest: %', sqlerrm;
        end if;
      end;

      -- A REVISION NOBODY RECORDED.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 99, v_digest, false, 1);
        raise exception '%: a current pointer named an unrecorded revision', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%has no revision%' then
          raise exception 'wrong refusal for an unknown revision: %', sqlerrm;
        end if;
      end;
      v_exercised := array_append(v_exercised, 'refuses-missing-revision-and-stale-pointer-digest');

      -- A LEGITIMATE SWAP, and the history of what was current is preserved.
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, false, 1);
      if (ops.model_role_current_revision(v_role) ->> 'revision_no')::integer <> 2 then
        raise exception 'the compare-and-swap did not move the current pointer';
      end if;
      v_readback := ops.model_role_readback(v_role);
      select jsonb_array_length(v_readback -> 'pointer_history') into v_count;
      if v_count <> 2 then
        raise exception 'the pointer ledger holds % rows, not 2; a superseded pointer was erased', v_count;
      end if;
      -- Every ledger entry carries both principals and the pointer-prefixed
      -- authority names, not only the current one.
      if (v_readback -> 'pointer_history' -> 0 ->> 'set_by_acting_actor_slug') <> v_partner
         or (v_readback -> 'pointer_history' -> 0 ->> 'pointer_authority_class') <> 'system_authority' then
        raise exception 'a superseded pointer entry does not carry the acting principal and the pointer authority class: %',
          v_readback -> 'pointer_history' -> 0;
      end if;

      -- AN IDEMPOTENT REPLAY OF A POINTER CHANGE returns the same row rather
      -- than appending a second one, and the ledger does not grow.
      declare v_pkey constant uuid := gen_random_uuid(); v_pid uuid;
      begin
        v_pid := ops.model_role_set_current_revision(v_role, v_pkey, 4, v_ws_digest, false, 2);
        if ops.model_role_set_current_revision(v_role, v_pkey, 4, v_ws_digest, false, 2) <> v_pid then
          raise exception 'an exact idempotent pointer replay did not return the first row';
        end if;
        select count(*) into v_count from ops.model_role_current_pointer;
        if v_count <> 3 then
          raise exception 'an idempotent pointer replay wrote a second row: % pointer rows exist', v_count;
        end if;
        -- THE SAME KEY WITH A DIFFERENT COMPARE-AND-SWAP is a different act.
        begin
          perform ops.model_role_set_current_revision(v_role, v_pkey, 1, v_digest, false, 4);
          raise exception '%: a reused pointer idempotency key with a different swap was accepted', v_marker;
        exception when others then
          if sqlerrm like v_marker || '%' then raise; end if;
          if sqlerrm not like '%idempotency key%' then
            raise exception 'wrong refusal for a pointer idempotency mismatch: %', sqlerrm;
          end if;
        end;
      end;
      v_exercised := array_append(v_exercised, 'swaps-current-pointer-preserves-ledger-and-replays-idempotently');

      -- RE-POINTING AT WHAT IS ALREADY CURRENT IS A NO-OP AND IS REFUSED, so a
      -- stale caller cannot read one as a success.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 4, v_ws_digest, false, 4);
        raise exception '%: a re-point at the already-current revision was accepted', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%already current%' then
          raise exception 'wrong refusal for an already-current re-point: %', sqlerrm;
        end if;
      end;
      v_exercised := array_append(v_exercised, 'refuses-already-current-re-point');

      -- The writer context is restored, so nothing after this block runs under
      -- an actor context this file manufactured.
      perform set_config('carr.acting_actor_slug', v_writer, true);
      perform set_config('carr.verified_human_actor_slug', '', true);
    end if;
    if v_session <> 'carr_authority_' || ops.model_role_system_authority_partner() then
      v_not_exercised := array_append(v_not_exercised, format(
        'the refusal path for a NON-system-authority authority principal: this session (%s) is the retained partner, so the other partner''s refusal was not observed here.',
        v_session));
    end if;
  end if;

  -- === least privilege ======================================================
  if exists (select 1 from pg_roles where rolname = 'carr_writer')
     and exists (select 1 from pg_roles where rolname = 'carr_authority') then
    if has_table_privilege('carr_writer', 'ops.model_role_revision', 'insert')
       or has_table_privilege('carr_authority', 'ops.model_role_current_pointer', 'insert') then
      raise exception 'a runtime role holds direct INSERT; every write must go through a definer writer';
    end if;
    if has_table_privilege('carr_writer', 'ops.model_role_revision', 'truncate')
       or has_table_privilege('carr_authority', 'ops.model_role_current_pointer', 'truncate') then
      raise exception 'a runtime role holds TRUNCATE on an append-only relation';
    end if;
    if not has_function_privilege('carr_writer',
        'ops.model_role_record_revision(text,integer,uuid,text,jsonb,jsonb,jsonb)', 'execute') then
      raise exception 'carr_writer cannot record an inert role revision';
    end if;
    if has_function_privilege('carr_writer',
        'ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)', 'execute') then
      raise exception 'carr_writer can name the current revision; that act reaches the authority bundle only';
    end if;
    if not has_function_privilege('carr_authority',
        'ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)', 'execute') then
      raise exception 'carr_authority cannot name the current revision';
    end if;
    if has_function_privilege('carr_writer', 'ops.model_role_lock(text)', 'execute')
       or has_function_privilege('carr_reader', 'ops.model_role_lock(text)', 'execute') then
      raise exception 'the per-role write lock is reachable without writing';
    end if;
    v_exercised := array_append(v_exercised, 'least-privilege-on-tables-writers-and-lock');
  else
    v_not_exercised := array_append(v_not_exercised,
      'the least-privilege assertions: carr_writer and carr_authority do not both exist here. This file creates no role.');
  end if;

  -- === the installed shape the writers and guards depend on =================
  --
  -- ASSERTED HERE BECAUSE THE CANDIDATE HAS NO FORWARD FIX AND SHOULD NOT HAVE
  -- ONE. It installs fresh and refuses a database that already carries these
  -- relations, precisely so that no ALTER TABLE from candidate source ever decides
  -- what happens to rows somebody already stored. The cost of that posture is that
  -- a database whose schema drifted -- an older install, or one hand-edited after
  -- the fact -- is NOT repaired by re-applying the file. So the drift has to be
  -- detectable, and this is where it is detected: against the live catalog, by
  -- name, rather than by reading the candidate's text.
  --
  -- A FAILURE HERE IS NOT A SKIP. Every row-level assertion above can pass on a
  -- schema that is missing a constraint, because these fixtures write well-formed
  -- content; what a missing constraint changes is what a DIRECT writer could store
  -- when this file is not looking.
  --
  -- THIRTY NAMES, AND THE LIST IS THE CLAIM. Each one is a rule this rail depends
  -- on: the closed field enums, the tenant and schema pins, the two uniqueness
  -- rules that make a version and its bytes singular, the compare-and-swap shape,
  -- and the acting-principal foreign key. The primary keys and the two revision
  -- foreign keys are deliberately NOT in the list -- nothing here turns on their
  -- names, and a list padded with them would read as more coverage than it is.
  select string_agg(t.conname, ', ' order by t.conname) into v_probe
    from (values
      ('model_role_revision',       'model_role_revision_title_nonempty'),
      ('model_role_revision',       'model_role_revision_mission_nonempty'),
      ('model_role_revision',       'model_role_revision_tenant'),
      ('model_role_revision',       'model_role_revision_schema_version'),
      ('model_role_revision',       'model_role_revision_role_key'),
      ('model_role_revision',       'model_role_revision_digest_shape'),
      ('model_role_revision',       'model_role_revision_no_positive'),
      ('model_role_revision',       'model_role_revision_version_unique'),
      ('model_role_revision',       'model_role_revision_content_unique'),
      ('model_role_revision',       'model_role_revision_idempotency_key_unique'),
      ('model_role_revision',       'model_role_revision_authority_class_occupiable'),
      ('model_role_revision',       'model_role_revision_authority_class_not_system'),
      ('model_role_revision',       'model_role_revision_minimum_strength_ref'),
      ('model_role_revision_text',  'model_role_revision_text_field'),
      ('model_role_revision_text',  'model_role_revision_text_value_nonempty'),
      ('model_role_revision_text',  'model_role_revision_text_position_unique'),
      ('model_role_revision_ref',   'model_role_revision_ref_field'),
      ('model_role_revision_ref',   'model_role_revision_ref_position_unique'),
      ('model_role_revision_ref',   'model_role_revision_ref_member_unique'),
      ('model_role_revision_ref',   'model_role_ref_grammar'),
      ('model_role_current_pointer','model_role_pointer_acting_actor_fkey'),
      ('model_role_current_pointer','model_role_pointer_tenant'),
      ('model_role_current_pointer','model_role_pointer_role_key'),
      ('model_role_current_pointer','model_role_pointer_digest_shape'),
      ('model_role_current_pointer','model_role_pointer_authority_class'),
      ('model_role_current_pointer','model_role_pointer_authority_grant_kind'),
      ('model_role_current_pointer','model_role_pointer_ledger_unique'),
      ('model_role_current_pointer','model_role_pointer_idempotency_key_unique'),
      ('model_role_current_pointer','model_role_pointer_creation_shape'),
      ('model_role_current_pointer','model_role_pointer_not_self_expected')
    ) as t(rel, conname)
   where not exists (
     select 1 from pg_constraint c
       join pg_class k on k.oid = c.conrelid
       join pg_namespace n on n.oid = k.relnamespace
      where n.nspname = 'ops' and k.relname = t.rel and c.conname = t.conname);
  if v_probe is not null then
    raise exception 'the installed role relations are missing constraints the writers and guards depend on: %. This database was not built from the current ops/model-role-store.candidate.sql, and that file will not bring it forward -- it installs fresh and refuses an existing installation. Build a database from the current candidate and run this proof there.',
      v_probe;
  end if;

  -- THE ACTING PRINCIPAL IS NOT NULL. A nullable one would let a pointer row exist
  -- that cannot say who acted, which is the flattening the column exists to end.
  if exists (select 1 from information_schema.columns
              where table_schema = 'ops' and table_name = 'model_role_current_pointer'
                and column_name = 'acting_actor_id' and is_nullable <> 'NO') then
    raise exception 'ops.model_role_current_pointer.acting_actor_id is nullable, so a pointer row could record the authority login without recording who acted';
  end if;

  -- THE NON-EMPTY GATES CARRY THE ECMAScript RULE, checked in the constraint's own
  -- definition rather than trusted from its name.
  select string_agg(t.conname, ', ' order by t.conname) into v_probe
    from (values
      ('model_role_revision',      'model_role_revision_title_nonempty'),
      ('model_role_revision',      'model_role_revision_mission_nonempty'),
      ('model_role_revision_text', 'model_role_revision_text_value_nonempty')
    ) as t(rel, conname)
   where not exists (
     select 1 from pg_constraint c
       join pg_class k on k.oid = c.conrelid
       join pg_namespace n on n.oid = k.relnamespace
      where n.nspname = 'ops' and k.relname = t.rel and c.conname = t.conname
        and pg_get_constraintdef(c.oid) like '%model\_role\_is\_nonempty\_text%');
  if v_probe is not null then
    raise exception 'these non-empty constraints do not apply ops.model_role_is_nonempty_text: %; a gate that is not the kernel''s rule admits text defineRole refuses',
      v_probe;
  end if;

  -- AND NOTHING GATES TEXT WITH btrim(), which strips SPACE ONLY. One such
  -- constraint would admit a lone tab, which the reader then refuses forever.
  select string_agg(c.conname::text, ', ' order by c.conname) into v_probe
    from pg_constraint c
    join pg_class k on k.oid = c.conrelid
    join pg_namespace n on n.oid = k.relnamespace
   where n.nspname = 'ops'
     and k.relname in ('model_role_revision', 'model_role_revision_text',
                       'model_role_revision_ref', 'model_role_current_pointer')
     and pg_get_constraintdef(c.oid) like '%btrim(%';
  if v_probe is not null then
    raise exception 'these constraints gate text with the space-only btrim(): %', v_probe;
  end if;
  v_exercised := array_append(v_exercised, 'installed-shape-carries-every-constraint-and-not-null-the-writers-depend-on');

  -- === what this rail does not carry ========================================
  if to_regclass('ops.model_role_occupant') is not null
     or to_regclass('ops.model_role_qualification') is not null then
    raise exception 'this rail has grown an occupancy or qualification relation';
  end if;
  select count(*) into v_count from information_schema.columns
   where table_schema = 'ops'
     and table_name in ('model_role_revision', 'model_role_revision_text',
                        'model_role_revision_ref', 'model_role_current_pointer')
     and (column_name ~* 'occupant|occupancy|qualif|approved|verified'
          -- A NUMERIC COLUMN WOULD BE A BUSINESS FLOOR. Quality floors here are
          -- NAMED REFERENCES; the only integers are ordinals and version numbers.
          or data_type in ('numeric', 'double precision', 'real'));
  if v_count <> 0 then
    raise exception 'a role relation carries an occupant, a qualification, an approval or a numeric floor column';
  end if;
  -- The one column whose name contains "grant" is authority_grant_kind, which
  -- RECORDS which authority was exercised and confers nothing. Named explicitly
  -- so the sweep above cannot be widened past it by accident.
  select count(*) into v_count from information_schema.columns
   where table_schema = 'ops'
     and table_name in ('model_role_revision', 'model_role_revision_text',
                        'model_role_revision_ref', 'model_role_current_pointer')
     and column_name ~* 'grant' and column_name <> 'authority_grant_kind';
  if v_count <> 0 then
    raise exception 'a role relation carries a grant column beyond the recorded authority kind';
  end if;
  v_exercised := array_append(v_exercised, 'no-occupant-qualification-approval-or-numeric-floor-column');

  -- === the honest report ====================================================
  raise notice 'EXERCISED (%): %', cardinality(v_exercised), array_to_string(v_exercised, '; ');
  if cardinality(v_not_exercised) > 0 then
    raise notice 'NOT EXERCISED (%): %', cardinality(v_not_exercised),
      array_to_string(v_not_exercised, ' | ');
  end if;
  raise notice 'NOT PROVED HERE, ALWAYS: the cross-language digest equality with defineRole (no JavaScript runs in this fixture); concurrent compare-and-swap between two sessions; and that a HUMAN was present -- the acting-principal binding proves the runtime named the partner''s human actor on this transaction, not that a person did. Every row written above is about to be rolled back, and no role is current.';
end;
$proof$;

rollback;
