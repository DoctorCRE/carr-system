-- 0513 — make release-candidate identity explicit about its environment.
--
-- An authority-filed staging release and its Production candidate legitimately
-- bind the same git SHA. Migration 0504's original global partial unique index
-- treated those two environment-specific records as duplicates, so Production
-- candidate filing failed before rehearsal. Gate Zero consumes only the
-- Production record; keep exactly-one uniqueness there and permit staging
-- history for the same revision.

do $preflight$
declare v_predicate text;
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0512_foundation_assurance_scac_successor.sql'
      and sha256 = 'df42b1bf2b4bd6036520fdb8ee4958a1da2ca0ae461fdedf62787181624a17c5') then
    raise exception '0513 requires the exact 0512 Foundation Assurance successor';
  end if;

  select pg_get_expr(i.indpred, i.indrelid)
    into v_predicate
    from pg_index i
   where i.indexrelid = to_regclass('ops.release_authority_candidate_sha_uniq')
     and i.indisunique;
  if v_predicate is distinct from 'maker_authority_verified' then
    raise exception '0513 requires the original global authority-candidate index, found %',
      coalesce(v_predicate, '<missing>');
  end if;
end $preflight$;

drop index ops.release_authority_candidate_sha_uniq;

create unique index release_authority_candidate_sha_uniq
  on ops.release (git_sha)
  where maker_authority_verified and environment = 'production';

comment on index ops.release_authority_candidate_sha_uniq is
  'The Gate Zero producer reads one authority-filed Production subject maker '
  'by git_sha. Authority-filed staging history may share that revision; a '
  'second Production row is refused. The generated authority predicate cannot '
  'be moved by caller input.';

do $verify$
declare v_predicate text; v_unique boolean;
begin
  select i.indisunique, pg_get_expr(i.indpred, i.indrelid)
    into v_unique, v_predicate
    from pg_index i
   where i.indexrelid = to_regclass('ops.release_authority_candidate_sha_uniq');
  if v_unique is distinct from true
     or position('maker_authority_verified' in coalesce(v_predicate, '')) = 0
     or position('environment = ''production''::text' in coalesce(v_predicate, '')) = 0 then
    raise exception '0513 failed to install Production-scoped authority-candidate uniqueness: %',
      coalesce(v_predicate, '<missing>');
  end if;
end $verify$;
