-- WR-000115: the Doc conversation LIST door.
--
-- 0520 shipped a read that answers about ONE conversation, and 0523 shipped the
-- three write doors. Nothing could ask "which conversations may I see?": the
-- Conversations page (V5-UX-B07) has been folding a device-local localStorage
-- roster instead, which is empty on any device that has not already opened a
-- conversation. This file adds the ONE security-definer function that answers
-- that question, its revoke and its grant, AND NOTHING ELSE.
--
-- THIS IS A READ. No relation, no column, no index, no trigger and no table
-- grant is created, amended or dropped here. `stable`, not `volatile`:
-- mcp-server/src/mcp.js opens `begin read only` for a tool that declares
-- writerConnection without write, which is the transaction this function is
-- written for -- the same transaction read-doc-conversation already runs in.
-- ops.doc_conversation_facts is `stable` at 0520:171 and this one matches it.
--
-- NO ACTOR PARAMETER, and that is the whole difference from the shipped read.
-- 0520:169-170 takes p_actor_id in slot two and defends it at 0520:164-168 as
-- "a LOOKUP KEY, not an authorisation claim: the access list below still
-- refuses a non-member". That defence holds for one row and collapses for a
-- list, because a list has no row to refuse against. The acting actor is
-- therefore derived here from the server-installed transaction context by
-- ops.portfolio_writer_actor_id(), exactly as the three 0523 write doors and
-- the 0520 append derive it -- there is no parameter for it anywhere in the
-- signature, so a caller cannot name one.
--
-- NO OFFSET AND NO SORT PARAMETER either. updated_at moves on every appended
-- turn (0520:155) and on every share and revoke (0523:182, 0523:200), so an
-- offset page over this table skips and repeats by construction. The cursor is
-- an opaque server-minted token carrying the WHOLE three-part sort key.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0525/0526 declared in tools/migrate.py.

do $wr115_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0523_doc_conversation_write_doors.sql'
      and sha256 = 'cc53998cf7350fccaae6d8389ec1ab6999a71192f97320aca2a33465a7f02b26') then
    raise exception '0525 requires the exact 0523 Doc conversation write doors';
  end if;
  if to_regprocedure('ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)') is null then
    raise exception '0525 requires the 0520 Doc conversation append door';
  end if;
  if to_regprocedure('ops.doc_conversation_facts(uuid,text,integer,integer)') is null then
    raise exception '0525 requires the 0520 Doc conversation read door';
  end if;
  if to_regprocedure('ops.create_doc_conversation(text,text,uuid)') is null then
    raise exception '0525 requires the 0523 Doc conversation create door';
  end if;
  if to_regprocedure('ops.share_doc_conversation(uuid,text,boolean,uuid)') is null then
    raise exception '0525 requires the 0523 Doc conversation share door';
  end if;
  if to_regprocedure('ops.rename_doc_conversation(uuid,integer,text,boolean,boolean,uuid)') is null then
    raise exception '0525 requires the 0523 Doc conversation rename door';
  end if;
end $wr115_preflight$;

-- -------------------------------------------------------------------------
-- ops.list_doc_conversations
--
-- THE SET. A conversation is in it when the acting actor created it OR holds
-- an unrevoked grant on it, and in no other case. The disjunction below is
-- LIFTED byte for byte out of 0520:186-189 / 0520:211-214 rather than retyped.
-- `visibility` is deliberately NOT part of the test: visibility is a display
-- field the header carries (0523:182, 0523:199) and the access list is the
-- authority. An implementation that filtered on visibility = 'shared' would
-- hide a shared conversation from its own creator.
--
-- THE ORDER is pinned first, then most recently updated, with the id as the
-- tiebreak that makes it a total order. It is stored and compared as the
-- single DESCENDING tuple
--     (coalesce(pinned_at,'-infinity'), updated_at, id)
-- so that one row(...) comparison expresses the whole key.
-- coalesce(...,'-infinity') is exactly "nulls last under desc", written out
-- rather than reached for through least/greatest, which ignore NULLs.
--
-- THE CURSOR CARRIES ALL THREE PARTS. A two-part (updated_at, id) keyset is
-- only correct when the sort key has two parts. Under a pinned-first sort it
-- has three, and a cursor that omits the pinned component cannot say whether
-- the reader has left the pinned block: pinning a conversation mid-walk then
-- re-pages from the wrong place and returns the same conversation twice or
-- skips one.
-- -------------------------------------------------------------------------

create or replace function ops.list_doc_conversations(
  p_cursor text, p_limit integer, p_include_archived boolean)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_limit integer; v_include_archived boolean;
        v_cursor jsonb; v_cursor_pinned timestamptz; v_cursor_updated timestamptz;
        v_cursor_id uuid; v_fetched jsonb; v_count integer; v_rows jsonb;
        v_more boolean; v_next text; v_last jsonb; v_visible integer;
begin
  -- The acting actor, resolved from the server-installed transaction context.
  -- A caller cannot name one: there is no parameter for it.
  v_actor := ops.portfolio_writer_actor_id();
  -- Clamped, never refused: a caller asking for 1000 gets 100 and an honest
  -- `more`. The idiom is 0520:181's.
  v_limit := least(greatest(coalesce(p_limit, 25), 1), 100);
  v_include_archived := coalesce(p_include_archived, false);

  if p_cursor is not null then
    begin
      v_cursor := convert_from(decode(p_cursor, 'base64'), 'utf8')::jsonb;
      v_cursor_pinned := (v_cursor->>'p')::timestamptz;
      v_cursor_updated := (v_cursor->>'u')::timestamptz;
      v_cursor_id := (v_cursor->>'i')::uuid;
    exception when others then
      -- The token is the server's own; any failure to read one back is ONE
      -- refusal, never a partial page from a half-understood key.
      return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_cursor_invalid');
    end;
    if v_cursor_pinned is null or v_cursor_updated is null or v_cursor_id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_cursor_invalid');
    end if;
  end if;

  -- v_limit + 1 rows are fetched so `more` comes from the extra row rather
  -- than from a second count(*) over a set that is moving. The latest-turn
  -- lateral is applied to the subquery that has ALREADY ordered and cut the
  -- page, so its work is bounded by the page size (at most 101 backward index
  -- scans on doc_conversation_turn_cursor, 0520:59) and is independent of how
  -- many conversations or turns exist. Joining it before the limit would make
  -- the work proportional to the actor's whole visible set.
  select coalesce(jsonb_agg(to_jsonb(r) - 'ord' order by r.ord), '[]'::jsonb), count(*)
    into v_fetched, v_count
    from (
      select row_number() over (
               order by coalesce(c.pinned_at, '-infinity'::timestamptz) desc,
                        c.updated_at desc, c.id desc) as ord,
             c.id, c.title, c.visibility, c.pinned_at, c.archived_at, c.version,
             c.created_by_actor as created_by, c.updated_at as sort_updated,
             coalesce(t.sequence, -1) as latest_sequence, t.at as latest_turn_at
        from (select c.* from ops.doc_conversation c
               where (
       c.created_by_actor = v_actor
      or exists (select 1 from ops.doc_conversation_grant g
                  where g.conversation_id = c.id and g.grantee_actor = v_actor
                    and g.revoked_at is null)
                     )
                 and (v_include_archived or c.archived_at is null)
                 and (p_cursor is null or
                      row(coalesce(c.pinned_at, '-infinity'::timestamptz), c.updated_at, c.id)
                    < row(v_cursor_pinned, v_cursor_updated, v_cursor_id))
               order by coalesce(c.pinned_at, '-infinity'::timestamptz) desc,
                        c.updated_at desc, c.id desc
               limit v_limit + 1) c
        left join lateral (
          select t.sequence, t.at from ops.doc_conversation_turn t
           where t.conversation_id = c.id
           order by t.sequence desc limit 1) t on true
    ) r;

  v_more := v_count > v_limit;
  -- The page is the first v_limit of what was fetched; sort_updated is the
  -- cursor's own middle component and is stripped from what a caller sees.
  select coalesce(jsonb_agg(e - 'sort_updated' order by n), '[]'::jsonb) into v_rows
    from jsonb_array_elements(v_fetched) with ordinality as page(e, n)
   where n <= v_limit;
  -- next_cursor is the sort key of the LAST RETURNED row, and is null exactly
  -- when `more` is false.
  if v_more then
    v_last := v_fetched->(v_limit - 1);
    v_next := encode(convert_to(jsonb_build_object(
      'p', coalesce((v_last->>'pinned_at')::timestamptz, '-infinity'::timestamptz),
      'u', (v_last->>'sort_updated')::timestamptz,
      'i', (v_last->>'id')::uuid)::text, 'utf8'), 'base64');
  end if;

  -- 0520:210-214 UNCHANGED: the WHOLE visible set, archived rows included and
  -- paging ignored, so this door and the single-row read cannot disagree about
  -- the number the page prints. It does NOT count the filtered page.
  select count(*) into v_visible from ops.doc_conversation c
   where c.created_by_actor = v_actor
      or exists (select 1 from ops.doc_conversation_grant g
                  where g.conversation_id = c.id and g.grantee_actor = v_actor
                    and g.revoked_at is null);

  return jsonb_build_object('ok', true,
    'conversations', v_rows, 'more', v_more, 'next_cursor', v_next,
    'visible_conversation_count', v_visible);
end $$;

comment on function ops.list_doc_conversations(text,integer,boolean) is
  'WR-000115: the only door that answers which Doc conversations the acting actor may see. The actor is derived from the server-installed transaction context -- there is no parameter for it, no offset and no sort argument; the cursor is an opaque server-minted token carrying the whole three-part sort key.';

-- -------------------------------------------------------------------------
-- Grants. carr_writer AND carr_authority, exactly as ops.doc_conversation_facts
-- is granted at 0520:249-252 and all three 0523 doors at 0523:296-309: the app
-- must call this as the signed-in partner, which arrives on the writer
-- connection. The argument types are spelled in full in BOTH lines -- a stale
-- arity revokes nothing and would leave PUBLIC execute on a security definer
-- (0523:288-294 states the rule). NO new table grant of any kind, and no change
-- to 0520:236-242.
-- -------------------------------------------------------------------------

revoke all on function ops.list_doc_conversations(text,integer,boolean)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.list_doc_conversations(text,integer,boolean)
  to carr_writer,carr_authority;
