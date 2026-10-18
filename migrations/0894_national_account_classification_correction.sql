-- Joe's 2026-09-29 classification: C-161 is the only national
-- account. C-204 and C-205 entered through create-national-account on
-- 2026-08-15 but have no franchisee clients or deals. Preserve both client
-- records and their creation history; remove only the incorrect account role.
-- This is a reviewed correction path, not an authorization to apply it.
-- rollback: forward-only; reversing the recorded human correction requires a
-- reviewed corrective migration with fresh census and linked-work guards.

do $$
declare
  target_ids uuid[] := array[
    '8550b702-e6fb-4bea-8b3e-1fec5b9499bd'::uuid, -- C-204
    '2f7364a7-8167-421d-8492-79815307fba3'::uuid  -- C-205
  ];
  census text[];
begin
  select array_agg(roster_ref order by roster_ref) into census
    from client where client_type='national_account' and merged_into is null;
  -- CI's newly built schema has no historical client rows. Only that exact
  -- absence may skip this production-data correction; partial drift fails.
  if census is null and not exists
       (select 1 from client where roster_ref in ('C-161','C-204','C-205')) then
    return;
  end if;

  -- Fail rather than silently correcting a different live account census.
  if census is distinct from array['C-161','C-204','C-205']::text[] then
    raise exception 'national-account correction: live account census changed';
  end if;

  if (select count(*) from client c join party p on p.id=c.party_id
        where (c.id,c.roster_ref,p.kind,c.client_type,c.acquisition_source)
          in (('8550b702-e6fb-4bea-8b3e-1fec5b9499bd'::uuid,'C-204','org','national_account','national_account'),
              ('2f7364a7-8167-421d-8492-79815307fba3'::uuid,'C-205','org','national_account','national_account'))
          and c.merged_into is null) <> 2 then
    raise exception 'national-account correction: target identity or classification changed';
  end if;

  if exists (select 1 from v_client_account
              where account_client_id=any(target_ids) and is_sub_client)
     or exists (select 1 from deal where client_id=any(target_ids))
     or exists (select 1 from deal_review_session where account_client_id=any(target_ids)) then
    raise exception 'national-account correction: target has linked work';
  end if;

  if (select count(*) from national_account_owner nao join actor a on a.id=nao.owner_actor_id
        where nao.account_client_id=any(target_ids) and a.slug='joe') <> 2 then
    raise exception 'national-account correction: account ownership changed';
  end if;

  update client
     set client_type=null, updated_by=(select id from actor where slug='system')
   where id=any(target_ids);

  insert into event (occurred_at,recorded_at,actor_id,verb,subject_type,subject_id,
                     field,old_value,new_value,cause)
  select now(),now(),(select id from actor where slug='system'),
         'correct-national-account-classification','client',c.id,'client_type',
         to_jsonb('national_account'::text),'null'::jsonb,'human_correction'
    from client c where c.id=any(target_ids);

  delete from national_account_owner where account_client_id=any(target_ids);

  if (select array_agg(roster_ref order by roster_ref)
        from client where client_type='national_account' and merged_into is null)
       is distinct from array['C-161']::text[] then
    raise exception 'national-account correction: postcondition failed';
  end if;
end $$;
