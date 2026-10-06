-- 0355_rename_cognition_control_to_installed_reality.sql
--
-- The first live Guidance Registry activation attempt (2026-08-27, WR-000019)
-- found rule 5e89c211's enforcement split across two names: production's
-- ops.rule_enforcement_point registers the INSTALLED control as
-- 'control-plane-workflow-manifest' (manifest + selftest, written by the
-- control-plane build), while the reviewed map still said
-- 'cognition_token_admission' with an aspirational four-ref catalog entry.
-- The companion file change in this PR renames the map to the installed
-- reality; this migration retires the stale catalog row the parity gate
-- refuses to delete implicitly, and registers the installed control's row.

delete from ops.enforcement_control_catalog
 where control_key='cognition_token_admission';

insert into ops.enforcement_control_catalog
  (control_key,enforcement_class,implementation_ref,test_ref,installed,verified_at)
values
  ('control-plane-workflow-manifest','deny_gate',
   'ops/config/control-plane-workflows.v1.json','ops/control-plane-selftest.py; ops/control-plane-db-gate.py',
   true,now())
on conflict (control_key) do update
  set enforcement_class=excluded.enforcement_class,
      implementation_ref=excluded.implementation_ref,
      test_ref=excluded.test_ref,
      installed=true,verified_at=now();

do $$
begin
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key='cognition_token_admission') then
    raise exception '0355 FAILED: stale cognition_token_admission row survives';
  end if;
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key='control-plane-workflow-manifest' and installed) then
    raise exception '0355 FAILED: installed control-plane-workflow-manifest row absent';
  end if;
end $$;
