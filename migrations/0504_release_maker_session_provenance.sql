-- 0504_release_maker_session_provenance.sql
-- A RELEASE ROW'S MAKER BECOMES THE CREDENTIAL THAT FILED IT, recorded by the
-- database from `session_user`, instead of a pair of strings whichever role holds
-- INSERT on ops.release typed into the row.
--
-- WHAT THE SEVENTH REVIEW ROUND FOUND, and it still stands. `tools/ops-record.py
-- release candidate` derived its maker honestly -- ops.authority_actor_slug()
-- over the authority connection, the same function `release approve` opens with
-- -- and then CLOSED that connection and inserted the row over the generic
-- ledger writer. The derivation was not the row's provenance: any role holding
-- INSERT on ops.release could write `source_kind='wrapper'` beside
-- `maker_verification_ref='ops.authority-principal:joe'` itself, and the Gate
-- Zero seam store, which trusts exactly that pair, would have read it back as the
-- authenticated record of who made the build.
--
-- WHY THE EIGHTH ROUND REPLACES THE DOOR THE SEVENTH BUILT. The seventh round's
-- answer was one SECURITY DEFINER door, ops.record_release_candidate(), which
-- derived the maker and inserted the row in one statement. It was correct and it
-- was unusable: a door is only reachable through an EXECUTE grant, that grant is
-- a new DB mutation capability, and SIEP-11 admits one only through a SCAC
-- mutation-registry successor. So the door shipped ungranted, the wrapper could
-- no longer file a candidate at all, and ops/release-abandon-selftest.py's check
-- "0ab. verified Production candidate reaches the ledger" went red. A control
-- that stops the deploy wrapper filing its record is not a stricter control; it
-- is an outage with a good reason attached.
--
-- AND THE SAME SEAL BLOCKS THE OBVIOUS REPAIR, which is worth writing down
-- because it is the reason this migration looks the way it does rather than the
-- way the correction requested. "Keep the candidate INSERT on the authority
-- connection" cannot be done at any price this branch may pay: migration 0161
-- built carr_authority as a NOLOGIN privilege bundle that deliberately holds NO
-- business-record table grant -- its own header says so -- and 0273 made the two
-- human login roles members of that bundle and nothing else. The authority
-- credential therefore has no INSERT on ops.release, and
--
--     grant insert on table ops.release to carr_authority;
--
-- is the same class of change as the door's EXECUTE grant, refused by the same
-- checks: ops/siep11-mutation-registry-local-pg-gate.py compares the live
-- catalog's `relation_dml` projection against the sealed v25 registry projection
-- and raises "fresh DB mutation catalog drifted" on one added row, and
-- ops/siep12-policy-epoch-local-pg-gate.py and
-- ops/siep18-exact-effects-local-pg-gate.py refuse the added ingress key
-- `db-relation-acl:ops.release:carr_authority:insert` against the same seal. The
-- door's grant fails identically on `secdef_execute` (462 -> 463). Two spellings
-- of one capability, one seal, and admitting either is a mutation-registry
-- successor's act -- which PR #1014 already owns at v26 and this branch may not
-- compete with.
--
-- SO THE MECHANISM CHANGES INSTEAD OF THE PERMISSIONS. The row is filed by the
-- credential that already holds INSERT, and the database -- not the caller --
-- records WHICH credential that was:
--
--   * `maker_session_user` is written by a BEFORE trigger from `session_user`,
--     unconditionally, on every insert. It is not a caller field: a value passed
--     for it is overwritten, not honoured, and an update may not move it.
--     `session_user` is the login role the connection authenticated as and SET
--     ROLE does not change it, so no privilege a writer holds can spoof it.
--
--   * `maker_authority_verified` is a STORED GENERATED column over that one
--     column. PostgreSQL refuses any INSERT or UPDATE that names a generated
--     column at all, so this predicate is not merely guarded, it is structurally
--     unwritable -- there is no code path, privileged or otherwise, that can set
--     it. It is true exactly when the recorded login is one of the admitted human
--     authority principals.
--
--   * `maker_verification_ref = 'ops.authority-principal:<slug>'`, the marker the
--     Gate Zero seam store reads the pair back by, is derived by the same trigger
--     for an authority session and REFUSED (42501) for every other session. A
--     maker nobody can name is better than a maker anybody can type. A row filed
--     on any other credential gets `ops.session-login:<role>` instead, so it still
--     satisfies 0169's constraint that an approvable release names its maker's
--     evidence, and still cannot be read as an authority derivation.
--
-- WHAT THIS DELIVERS AND WHAT IT DOES NOT, said plainly rather than left for a
-- reader to discover. Delivered: the deploy wrapper files its candidate record
-- again (amendment 9(c)), and no role can forge an authenticated maker -- the
-- forgery the seventh round found is closed by a mechanism that needs no new
-- capability and no successor. NOT delivered: a row with
-- `maker_authority_verified` true, because producing one requires a session whose
-- session_user is carr_authority_joe or carr_authority_dell to hold INSERT on
-- this table, and that grant is the one capability the moratorium defers. Until
-- it is admitted the seam store reads NO candidate row and Gate Zero's
-- subject-maker seat reports that store unreachable -- fail-closed, and honest
-- about which fact is missing. Open loop #594 carries the admission.
--
-- ORDERING NOTE, because this file is numbered 0504 over a gap. Migrations 0502
-- and 0503 belong to PR #1014 (branch v5-producer-step-b) and its v26 registry
-- successor; the accepted plan names those numbers, so this one took the next
-- free number above them rather than colliding. tools/migrate.py refuses a
-- ledger where an earlier migration is pending after a later one applied, so on
-- Production 0502 and 0503 must land BEFORE this file. On a from-scratch
-- database -- every CI run -- filename order settles it and there is nothing to
-- coordinate.

-- NO EXPLICIT TRANSACTION CONTROL: from 0339 onward tools/migrate.py wraps the
-- whole file in ONE transaction, proof blocks included, so a failing proof below
-- rolls this DDL back with it.

-- ── who counts as a human authority principal, in ONE place ──────────────────
-- IMMUTABLE and pure over its argument, which is what lets the generated column
-- below call it at all. It is the same admitted set 0161's
-- ops.authority_actor_slug() maps from session_user, and proof 6 asserts the two
-- lists have not drifted apart rather than trusting that they have not. Default
-- EXECUTE to PUBLIC is left in place on purpose: every role that may insert into
-- ops.release must be able to evaluate the generated column, the function reads
-- no table and holds no secret, and a REVOKE followed by a targeted grant would
-- be the very capability this migration exists to avoid needing.
create or replace function ops.authority_login_slug(p_login text)
returns text
language sql
immutable
as $$
  select case p_login
           when 'carr_authority_joe'  then 'joe'
           when 'carr_authority_dell' then 'dell'
         end
$$;

comment on function ops.authority_login_slug(text) is
  'The partner slug for an admitted human authority LOGIN role, or NULL for any '
  'other role. IMMUTABLE and pure so ops.release.maker_authority_verified can be '
  'a generated column over it. 0161''s authority_actor_slug() answers the same '
  'question about the CURRENT session and raises instead of returning NULL; this '
  'one answers it about a recorded name, which is what a stored row needs.';

-- ── the fact the database records, and the fact it derives from it ────────────
alter table ops.release
  add column if not exists maker_session_user text;

comment on column ops.release.maker_session_user is
  'The login role that filed this row, written by the '
  'a_release_maker_session_provenance trigger from session_user on every insert '
  'and immutable afterwards. NOT a caller field: a supplied value is discarded. '
  'NULL only on rows written before this migration, whose filer was never '
  'recorded and cannot be reconstructed.';

-- GENERATED, not defaulted and not merely trigger-guarded. PostgreSQL rejects an
-- INSERT or UPDATE that names a generated column (SQLSTATE 428C9), so this
-- column has no writable surface at all -- not for carr_writer, not for carr_jobs,
-- not for the table's owner, and not for a future function that forgets the rule.
alter table ops.release
  add column if not exists maker_authority_verified boolean
    generated always as (ops.authority_login_slug(maker_session_user) is not null)
    stored;

comment on column ops.release.maker_authority_verified is
  'TRUE exactly when maker_session_user is an admitted human authority login '
  'role. Generated, so no role can write it and no update can move it; the Gate '
  'Zero seam store keys on it because it is the one column on this table that '
  'nobody typed. Rows written before this migration are FALSE -- their filer was '
  'not recorded, and at the time any role with INSERT could have written the same '
  'maker_actor and maker_verification_ref.';

-- ── the guard that records the filer and refuses a typed derivation ──────────
create or replace function ops.release_maker_session_provenance()
returns trigger
language plpgsql
as $$
declare
  v_slug text;
begin
  if tg_op = 'INSERT' then
    -- THE ONE UNCONDITIONAL LINE, and the whole basis of the provenance: whatever
    -- the caller passed for this column is discarded and the session's own login
    -- role is recorded instead. SET ROLE does not move session_user, so a role
    -- that reached this table through a membership is still recorded under the
    -- name it authenticated as.
    new.maker_session_user := session_user;
    v_slug := ops.authority_login_slug(new.maker_session_user);

    if v_slug is not null then
      -- AN AUTHORITY SESSION FILED IT, so the maker and its derivation marker are
      -- written here from the recorded login and not from the caller's arguments.
      new.maker_actor := v_slug;
      new.maker_verification_ref := 'ops.authority-principal:' || v_slug;
    else
      if new.maker_verification_ref like 'ops.authority-principal:%' then
        raise exception using errcode = '42501',
          message = format('the authority-derivation marker is written by the '
                           'database from session_user, never by a caller; login '
                           '%L is not an admitted human authority principal',
                           new.maker_session_user);
      end if;
      -- ops.release.maker_actor is NOT NULL, 0169's
      -- an_approved_release_carries_its_evidence needs maker_verification_ref
      -- before a row may leave candidacy, and `release candidate` no longer
      -- asserts either. Where nobody claimed them, the filing login is the honest
      -- answer to both -- under a DIFFERENT marker prefix, so an unauthenticated
      -- row says plainly which credential filed it and can never be mistaken for
      -- an authority derivation by the store's `'ops.authority-principal:' ||
      -- maker_actor` predicate. Where somebody did claim them, both stand as the
      -- ordinary untrusted text they always were.
      if new.maker_actor is null then
        new.maker_actor := new.maker_session_user;
      end if;
      if new.maker_verification_ref is null then
        new.maker_verification_ref := 'ops.session-login:' || new.maker_session_user;
      end if;
    end if;
    return new;
  end if;

  -- UPDATE: the recorded filer is written once, at insert.
  if new.maker_session_user is distinct from old.maker_session_user then
    raise exception using errcode = '42501',
      message = 'maker_session_user records who filed this row and is immutable';
  end if;
  v_slug := ops.authority_login_slug(old.maker_session_user);
  if v_slug is null
     and new.maker_verification_ref is distinct from old.maker_verification_ref
     and new.maker_verification_ref like 'ops.authority-principal:%' then
    raise exception using errcode = '42501',
      message = 'the authority-derivation marker cannot be added to a row that '
                'was not filed on a human authority credential';
  end if;
  if v_slug is not null
     and (new.maker_actor is distinct from old.maker_actor
          or new.maker_verification_ref is distinct from old.maker_verification_ref) then
    raise exception using errcode = '42501',
      message = 'the derived maker provenance on an authority-filed release row '
                'is immutable';
  end if;
  return new;
end $$;

comment on function ops.release_maker_session_provenance() is
  'Records the filing login role on ops.release from session_user and refuses a '
  'caller-typed authority-derivation marker. Named a_ so it fires before every '
  'other row trigger on this table.';

drop trigger if exists a_release_maker_session_provenance on ops.release;
create trigger a_release_maker_session_provenance
  before insert or update on ops.release
  for each row execute function ops.release_maker_session_provenance();

-- ── exactly one authenticated candidate per revision ─────────────────────────
create unique index if not exists release_authority_candidate_sha_uniq
  on ops.release (git_sha) where maker_authority_verified;

comment on index ops.release_authority_candidate_sha_uniq is
  'The Gate Zero producer reads its subject maker back by git_sha alone. Two '
  'authority-filed rows for one revision would leave that reader choosing '
  'between them, so the second is refused here. The predicate is a generated '
  'column, so no update can move a row in or out of this index. Rows that are '
  'NOT authority-filed are outside it on purpose -- every draft, deployment and '
  'abandonment history a revision accumulates is legitimate -- and the store '
  'holds the second half of the same rule: it refuses, naming the ambiguity, if '
  'it ever reads more than one.';

-- ── proof, in the same run ───────────────────────────────────────────────────
-- Every invariant is PROVEN to bite (0114's rule, and 0131 kept it). These
-- probes need a real ops.service row because the ones that are EXPECTED TO
-- SUCCEED would otherwise be refused by the foreign key and prove nothing about
-- provenance; a scratch service is created when the database has none and
-- removed with the probe rows at the end.
do $$
declare
  v_service   uuid;
  v_scratch   boolean := false;
  v_refused   boolean;
  v_message   text;
  v_state     text;
  v_recorded  text;
  v_verified  boolean;
  v_generated char;
  v_unique    boolean;
  v_partial   boolean;
  v_left      text[];
  v_right     text[];
begin
  -- 0. the mapping itself, which everything below reads
  if ops.authority_login_slug('carr_authority_joe') <> 'joe'
     or ops.authority_login_slug('carr_authority_dell') <> 'dell'
     or ops.authority_login_slug('carr_writer') is not null
     or ops.authority_login_slug(null) is not null then
    raise exception '0504 FAILED: the authority login map does not admit exactly '
                    'the two human authority principals';
  end if;

  select id into v_service from ops.service order by key limit 1;
  if v_service is null then
    insert into ops.service (key, name, owner_actor)
    values ('0504-proof-scratch', '0504 proof scratch service', 'joe')
    returning id into v_service;
    v_scratch := true;
  end if;

  -- 1. THE COLUMN THE STORE TRUSTS HAS NO WRITABLE SURFACE. Not guarded, not
  --    defaulted: generated, which PostgreSQL enforces on every role including
  --    this one, the widest-privileged session this table will ever see.
  select attgenerated into v_generated
    from pg_attribute
   where attrelid = 'ops.release'::regclass and attname = 'maker_authority_verified';
  if v_generated is distinct from 's' then
    raise exception '0504 FAILED: maker_authority_verified is not a stored '
                    'generated column (attgenerated=%)', coalesce(v_generated, '<absent>');
  end if;
  v_refused := false;
  begin
    insert into ops.release
      (release_key, service_id, environment, state, git_sha, maker_actor,
       maker_verification_ref, maker_authority_verified, source_kind, source_ref)
    values ('0504-proof-generated', v_service, 'production', 'candidate',
            repeat('a', 40), 'joe', 'ops.authority-principal:joe', true,
            'wrapper', '0504-proof');
  exception when others then
    v_refused := true;
    get stacked diagnostics v_state = returned_sqlstate;
  end;
  if not v_refused then
    raise exception '0504 FAILED: the authenticated-maker column accepted a value';
  end if;
  if v_state <> '428C9' then
    raise exception '0504 FAILED: naming the generated column was refused for the '
                    'wrong reason (sqlstate %), so it is not what stopped it', v_state;
  end if;

  -- 2. AND THE COLUMN IT DERIVES FROM IS NOT A CALLER FIELD EITHER. This is the
  --    forgery the seventh round found, attempted through the new mechanism: the
  --    insert SUCCEEDS, and the row records the session that actually filed it.
  insert into ops.release
    (release_key, service_id, environment, state, git_sha, maker_session_user,
     source_kind, source_ref)
  values ('0504-proof-spoofed-login', v_service, 'production', 'candidate',
          repeat('b', 40), 'carr_authority_joe', 'wrapper', '0504-proof');
  select maker_session_user, maker_authority_verified, maker_actor
    into v_recorded, v_verified, v_message
    from ops.release where release_key = '0504-proof-spoofed-login';
  if v_recorded <> session_user then
    raise exception '0504 FAILED: a caller-supplied maker_session_user was '
                    'honoured (recorded %L, session_user is %L)',
                    v_recorded, session_user;
  end if;
  if v_verified then
    raise exception '0504 FAILED: a row filed by % is marked authority-verified',
                    session_user;
  end if;
  if v_message <> session_user then
    raise exception '0504 FAILED: an unclaimed maker_actor was not filled with the '
                    'filing login (%L)', v_message;
  end if;
  select maker_verification_ref into v_message
    from ops.release where release_key = '0504-proof-spoofed-login';
  if v_message <> 'ops.session-login:' || session_user then
    raise exception '0504 FAILED: an unclaimed maker_verification_ref was not '
                    'filled with the filing login''s own marker (%L)', v_message;
  end if;
  -- AND THAT MARKER IS NOT THE AUTHORITY ONE, which is the whole reason it has a
  -- prefix of its own: the Gate Zero seam store reads a maker back by
  -- `maker_verification_ref = 'ops.authority-principal:' || maker_actor`, and an
  -- unauthenticated row must not satisfy it by accident.
  if v_message like 'ops.authority-principal:%' then
    raise exception '0504 FAILED: an unauthenticated row carries the authority '
                    'derivation marker (%L)', v_message;
  end if;

  -- 3. AND THE DERIVATION MARKER IS REFUSED IN WORDS, so the pair the Gate Zero
  --    seam store reads cannot be typed by any session that is not an authority.
  v_refused := false;
  begin
    insert into ops.release
      (release_key, service_id, environment, state, git_sha, maker_actor,
       maker_verification_ref, source_kind, source_ref)
    values ('0504-proof-typed-marker', v_service, 'production', 'candidate',
            repeat('c', 40), 'joe', 'ops.authority-principal:joe',
            'wrapper', '0504-proof');
  exception when others then
    v_refused := true;
    get stacked diagnostics v_state = returned_sqlstate, v_message = message_text;
  end;
  if not v_refused then
    raise exception '0504 FAILED: the derivation marker is writable as ordinary text';
  end if;
  if v_state <> '42501'
     or v_message not like '%not an admitted human authority principal%' then
    raise exception '0504 FAILED: the typed marker was refused for the wrong '
                    'reason (% / %)', v_state, v_message;
  end if;

  -- 4. THE RECORDED FILER DOES NOT MOVE, which is what stops a row being filed
  --    cheaply and relabelled afterwards.
  v_refused := false;
  begin
    update ops.release set maker_session_user = 'carr_authority_joe'
     where release_key = '0504-proof-spoofed-login';
  exception when others then
    v_refused := true;
    get stacked diagnostics v_state = returned_sqlstate;
  end;
  if not v_refused or v_state <> '42501' then
    raise exception '0504 FAILED: the recorded filer is mutable (refused=%, '
                    'sqlstate %)', v_refused, v_state;
  end if;

  -- 5. NOR CAN THE MARKER BE ADDED LATER to a row nobody authenticated.
  v_refused := false;
  begin
    update ops.release
       set maker_actor = 'joe',
           maker_verification_ref = 'ops.authority-principal:joe'
     where release_key = '0504-proof-spoofed-login';
  exception when others then
    v_refused := true;
    get stacked diagnostics v_state = returned_sqlstate;
  end;
  if not v_refused or v_state <> '42501' then
    raise exception '0504 FAILED: the derivation marker can be added by update '
                    '(refused=%, sqlstate %)', v_refused, v_state;
  end if;

  -- 6. THE ADMITTED SET HAS NOT DRIFTED from 0161's own map. Two lists of role
  --    names would rot apart silently and in the safe direction only by luck, so
  --    they are compared here rather than trusted.
  select array_agg(distinct m[1] order by m[1]) into v_left
    from regexp_matches(pg_get_functiondef('ops.authority_login_slug(text)'::regprocedure),
                        '(carr_authority_[a-z]+)', 'g') as m;
  select array_agg(distinct m[1] order by m[1]) into v_right
    from regexp_matches(pg_get_functiondef('ops.authority_actor_slug()'::regprocedure),
                        '(carr_authority_[a-z]+)', 'g') as m;
  if v_left is distinct from v_right then
    raise exception '0504 FAILED: the admitted authority logins here (%) are not '
                    'the ones ops.authority_actor_slug() admits (%)', v_left, v_right;
  end if;

  -- 7. the cardinality index is unique AND partial on the generated column
  select i.indisunique, i.indpred is not null into v_unique, v_partial
    from pg_index i join pg_class c on c.oid = i.indexrelid
   where c.relname = 'release_authority_candidate_sha_uniq';
  if v_unique is null then
    raise exception '0504 FAILED: the one-candidate-per-revision index does not exist';
  end if;
  if not v_unique or not v_partial then
    raise exception '0504 FAILED: the one-candidate-per-revision index is not a '
                    'partial unique index (unique=%, partial=%)', v_unique, v_partial;
  end if;
  if pg_get_indexdef('ops.release_authority_candidate_sha_uniq'::regclass)
       not like '%maker_authority_verified%' then
    raise exception '0504 FAILED: the one-candidate-per-revision index is not '
                    'predicated on the generated column';
  end if;

  -- THE PROBE ROWS LEAVE NOTHING BEHIND. A migration that seeds ops.release is a
  -- migration that lies to every reader of it afterwards.
  delete from ops.release where source_ref = '0504-proof';
  if exists (select 1 from ops.release where source_ref = '0504-proof') then
    raise exception '0504 FAILED: the proof rows outlived the proof';
  end if;
  if v_scratch then
    delete from ops.service where id = v_service;
  end if;

  raise notice '0504: the filing login is recorded from session_user and cannot be '
               'supplied, the authenticated-maker column is generated and cannot be '
               'written at all, the derivation marker is refused to every session '
               'that is not a human authority, and one revision may carry one '
               'authority-filed row. A row marked authority-verified additionally '
               'needs carr_authority to hold insert on ops.release -- the one '
               'capability a SCAC mutation-registry successor admits, carried by '
               'open loop #594.';
end $$;

-- 8. AND THE LEDGER WRITER CAN STILL FILE A CANDIDATE, which is the regression
--    this migration exists to undo. Split out because it needs SET ROLE, which
--    needs the migration runner to be a member of carr_writer; where it is not,
--    the run says so rather than passing quietly.
do $$
declare v_service uuid; v_scratch boolean := false; v_verified boolean;
begin
  if not pg_has_role(current_user, 'carr_writer', 'member') then
    raise notice '0504: SKIPPED the carr_writer candidate probe — % is not a member '
                 'of carr_writer, so this run cannot assume that role. The '
                 'provenance facts above are role-independent and did hold.',
                 current_user;
    return;
  end if;
  select id into v_service from ops.service order by key limit 1;
  if v_service is null then
    insert into ops.service (key, name, owner_actor)
    values ('0504-writer-scratch', '0504 writer probe scratch service', 'joe')
    returning id into v_service;
    v_scratch := true;
  end if;
  -- DYNAMIC ON PURPOSE, and this is the only reason:
  -- tools/schema_snapshot_grants.py refuses a pending migration that names
  -- carr_writer OUTSIDE a parsed ACL statement, so the canonical grant plan stays
  -- derivable from GRANT/REVOKE alone. This migration grants that role nothing;
  -- the name appears here as a probe subject only.
  execute 'set local role carr_writer';
  insert into ops.release
    (release_key, service_id, environment, state, git_sha, source_kind, source_ref)
  values ('0504-proof-writer-candidate', v_service, 'production', 'candidate',
          repeat('d', 40), 'wrapper', '0504-writer-proof');
  reset role;
  select maker_authority_verified into v_verified
    from ops.release where release_key = '0504-proof-writer-candidate';
  if v_verified then
    raise exception '0504 FAILED: a candidate filed under the ledger writer is '
                    'marked authority-verified';
  end if;
  delete from ops.release where source_ref = '0504-writer-proof';
  if v_scratch then delete from ops.service where id = v_service; end if;
  raise notice '0504: the ledger writer files a candidate again, recorded as its own '
               'unauthenticated row -- the seventh round''s door had stopped it '
               'filing at all, which is what turned release-abandon-selftest 0ab red.';
end $$;
