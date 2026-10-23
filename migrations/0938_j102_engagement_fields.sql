-- J102 build step 2. Governing decisions 3388d01d, 51416bcf, 20b993cf.
-- rollback: forward-only — preserve recorded agreement facts; correct through a successor migration.
-- expand-contract: contract — remove unused J102 client_paused only after a locked zero-usage assertion.
-- lock-review: J102 current/event tables only; zero relationships/events in the disposable census. No legacy table changes.

lock table ops.j102_subject_current, ops.j102_subject_event in share row exclusive mode;
do $$
begin
  if exists (select 1 from ops.j102_subject_current
      where subject_kind = 'relationship'
        and envelope #>> '{record,state,relationship_state}' = 'client_paused')
     or exists (select 1 from ops.j102_subject_event
      where subject_kind = 'relationship' and envelope::text like '%client_paused%') then
    raise exception 'j102_client_paused_in_use: stop and review; no automatic conversion';
  end if;
end;
$$;

-- Replace the installed literal rather than retaining a second policy function.
do $migration$
declare
  v_definition text := pg_get_functiondef('ops.j102_admission_policy()'::regprocedure);
  v_old text;
  v_policy jsonb := ops.j102_admission_policy();
  v_field text;
  v_fields jsonb := $engagement_fields$
  {"effective_date":"date","executed_at":"instant","term_end":"date",
   "ended_at":"instant","end_reason":"text","protection_until":"instant",
   "protected_property_ids":"identifiers","governing_state":"governing_state"}
  $engagement_fields$::jsonb;
begin
  v_old := substring(v_definition from '(?s)\$policy\$(.*?)\$policy\$');
  if v_old is null or v_old::jsonb is distinct from v_policy then
    raise exception 'j102_policy_literal_changed: inspect the installed policy before upgrading';
  end if;
  v_policy := v_policy || jsonb_build_object('engagement_fields', v_fields,
    'relationship_states', '["prospect","client","client_ended"]'::jsonb,
    'engagement_states', '["active","completed","expired","terminated"]'::jsonb);
  for v_field in select jsonb_object_keys(v_fields) loop
    v_policy := jsonb_set(v_policy,
      array['transitions','establish-client-and-engagement','subjects','engagement','creation_shape',v_field],
      '{"op":"const","value":null}'::jsonb);
    v_policy := jsonb_set(v_policy, array['reconciliation','field_class_registry',v_field], '"lifecycle"');
  end loop;
  execute replace(v_definition, '$policy$' || v_old || '$policy$', '$policy$' || v_policy::text || '$policy$');
end;
$migration$;

create function ops.j102_engagement_fields_valid(p_state jsonb)
returns boolean language plpgsql immutable
set search_path = pg_catalog, ops
as $$
declare
  v_field text; v_type text; v_value jsonb; v_text text; v_item jsonb;
  v_character text; v_point integer; v_length integer;
begin
  if jsonb_typeof(p_state) is distinct from 'object' then return false; end if;
  for v_field, v_type in select * from jsonb_each_text(ops.j102_admission_policy() -> 'engagement_fields') loop
    v_value := p_state -> v_field;
    if v_value is null or v_value = 'null'::jsonb then continue; end if;
    if v_type = 'identifiers' then
      if jsonb_typeof(v_value) <> 'array' then return false; end if;
      if jsonb_array_length(v_value) > 512 then return false; end if;
      for v_item in select * from jsonb_array_elements(v_value) loop
        if jsonb_typeof(v_item) <> 'string' or
           (v_item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$' then return false; end if;
      end loop;
      continue;
    end if;
    if jsonb_typeof(v_value) <> 'string' then return false; end if;
    v_text := v_value #>> '{}';
    case v_type
      when 'date' then
        if v_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' or left(v_text,4) = '0000' then return false; end if;
        perform v_text::date;
      when 'instant' then
        if v_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,9})?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$'
           or left(v_text,4) = '0000' then return false; end if;
        perform left(v_text,10)::date;
      when 'governing_state' then
        if v_text not in ('FL','AL') then return false; end if;
      when 'text' then
        if v_text = '' or normalize(v_text, NFC) <> v_text then return false; end if;
        if ascii(left(v_text,1)) = any(array[9,10,11,12,13,32,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288,65279])
           or ascii(right(v_text,1)) = any(array[9,10,11,12,13,32,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288,65279]) then return false; end if;
        v_length := 0;
        for v_character in select regexp_split_to_table(v_text, '') loop
          v_point := ascii(v_character);
          if v_point between 0 and 31 or v_point between 127 and 159
             or v_point between 8203 and 8207 or v_point between 8234 and 8238
             or v_point between 8288 and 8292 or v_point between 8294 and 8297
             or v_point = 65279 then return false; end if;
          v_length := v_length + case when v_point > 65535 then 2 else 1 end;
        end loop;
        if v_length > 1000 then return false; end if;
      else return false;
    end case;
  end loop;
  return true;
exception when datetime_field_overflow or invalid_datetime_format then return false;
end;
$$;
revoke all on function ops.j102_engagement_fields_valid(jsonb) from public;
grant execute on function ops.j102_engagement_fields_valid(jsonb) to carr_reader, carr_writer, carr_jobs, carr_authority;

alter table ops.j102_subject_current
  add constraint j102_relationship_state_vocabulary check (subject_kind <> 'relationship' or
    coalesce(envelope #>> '{record,state,relationship_state}' in ('prospect','client','client_ended'), false)),
  add constraint j102_engagement_state_vocabulary check (subject_kind <> 'engagement' or
    coalesce(envelope #>> '{record,state,engagement_state}' in ('active','completed','expired','terminated'), false)),
  add constraint j102_engagement_field_types check (subject_kind <> 'engagement' or
    ops.j102_engagement_fields_valid(envelope #> '{record,state}'));
