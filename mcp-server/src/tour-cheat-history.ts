export const tourCheatHistoryQuery = `
with authorized as materialized (
  select ops.read_tour_internal_detail($1::text,$2::uuid,$3::text) as detail
)
select case when detail is null then null else jsonb_build_object(
  'revisions',coalesce((select jsonb_agg(row_to_json(r) order by r.revision_number desc)
    from (select c.id as revision_id,c.revision_number,c.content,c.created_at,c.revision_kind
      from ops.tour_cheat_sheet_revision c
      where c.organization_tenant_id=$1::text and c.tour_id=$2::uuid
      order by c.revision_number desc limit 100) r),'[]'::jsonb),
  'limit',100
) end as data from authorized`;
