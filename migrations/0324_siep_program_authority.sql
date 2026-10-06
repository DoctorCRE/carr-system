-- SIEP v1 bootstrap: one DB-backed DAG over the existing Work Request ledger.
-- This migration creates relations around the existing ledgers, never a second
-- task, execution, finding, decision, or evidence store.  Applying it to
-- Production is a separate Joe-approved operation.

begin;

-- The current tenant constraint admits the fixed AI program and sourced Program
-- 6 rows.  Forward-replace it with one additional exact program identity.
alter table ops.work_request drop constraint if exists work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape check (
  (
    capture_idempotency_key is null and organization_tenant_id is null
    and doctrine_section_id is null and doctrine_revision_id is null
    and sourced_capture_sequence is null and triage_classification is null
    and triaged_by_actor_id is null and triaged_at is null
    and program_key is null and program_ordinal is null
  ) or (
    capture_idempotency_key is null
    and organization_tenant_id is not distinct from 'carr-internal'
    and doctrine_section_id is null and doctrine_revision_id is null
    and sourced_capture_sequence is null and triage_classification is null
    and triaged_by_actor_id is null and triaged_at is null
    and program_key in ('carr-ai-engineering-suite-v1','carr-system-integrity-elimination-v1')
    and program_ordinal is not null and program_ordinal > 0
    and requester_actor is not distinct from 'joe'
    and owner_actor is not distinct from 'joe'
  ) or (
    capture_idempotency_key is not null
    and organization_tenant_id is not distinct from 'carr-internal'
    and doctrine_section_id is not null and doctrine_revision_id is not null
    and sourced_capture_sequence is not null
    and program_key is null and program_ordinal is null
    and origin_ref is not null
    and origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
    and (
      (state='captured' and triage_classification is null
       and triaged_by_actor_id is null and triaged_at is null)
      or
      (state in ('triaged','ready')
       and triage_classification in ('operational','needs_judgment','safety_review')
       and triaged_by_actor_id is not null and triaged_at is not null)
    )
  )
) not valid;

create table if not exists ops.siep_package_contract (
  package_key text primary key check (package_key ~ '^(B0|00|0[1-5]|06A|06B|1[0-9]|2[0-3]|24A|24B|25|26|3[0-7]|4[0-4])$'),
  work_request_id uuid not null unique references ops.work_request(id) on delete restrict,
  lane_key text not null check (lane_key in ('program-control','heavy-build','scac-core','dell-mpe')),
  minimum_executor_tier text not null check (minimum_executor_tier in ('luna','terra','sol','main','human_authority')),
  test_contract jsonb not null check (jsonb_typeof(test_contract)='object'),
  delivery_contract jsonb not null check (
    jsonb_typeof(delivery_contract)='object'
    and delivery_contract ?& array['source','migration','deploy','readback','rollback']
  ),
  rollback_contract jsonb not null check (jsonb_typeof(rollback_contract)='object'),
  required_evidence_kinds text[] not null check (cardinality(required_evidence_kinds)>0),
  approval_gate text not null default 'none'
    check (approval_gate in ('none','joe_approval','joe_go_no_go')),
  created_at timestamptz not null default now()
);

create table if not exists ops.siep_program_dependency (
  package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  depends_on_package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  created_at timestamptz not null default now(),
  primary key (package_key,depends_on_package_key),
  check (package_key <> depends_on_package_key)
);

create table if not exists ops.siep_component_alias (
  alias_key text primary key check (alias_key ~ '^(SCAC-[0-9]{2}|MPE-17[A-H])$'),
  package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  created_at timestamptz not null default now()
);

create table if not exists ops.siep_evidence_link (
  id uuid primary key default gen_random_uuid(),
  package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  evidence_kind text not null check (evidence_kind in (
    'source','tests','migration','deploy','readback','live_readback','rollback',
    'independent_review','joe_approval','joe_go_no_go','zero_unresolved_findings',
    'zero_blockers','two_clean_audit_cycles','material_fix'
  )),
  ledger_kind text not null check (ledger_kind in (
    'job_receipt','decision_event'
  )),
  ledger_id uuid not null,
  work_request_version integer not null check (work_request_version>0),
  manifest_digest text not null check (manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  evidence_digest text not null check (evidence_digest ~ '^sha256:[0-9a-f]{64}$'),
  note text not null check (note ~ '^safe:[a-z0-9][a-z0-9:_./-]*$' and char_length(note)<=300),
  linked_actor_id uuid not null references public.actor(id) on delete restrict,
  attested_session_principal text not null,
  source_observed_at timestamptz not null,
  idempotency_key uuid not null unique,
  linked_at timestamptz not null default now(),
  unique(package_key,evidence_kind,ledger_kind,ledger_id),
  unique(package_key,ledger_kind,ledger_id)
);

create table if not exists ops.siep_lane_lock (
  lane_key text primary key check (lane_key in ('program-control','heavy-build','scac-core','dell-mpe')),
  package_key text not null unique references ops.siep_package_contract(package_key) on delete restrict,
  work_request_version integer not null check (work_request_version>0),
  holder_actor_id uuid not null references public.actor(id) on delete restrict,
  executor_tier text not null check (executor_tier in ('luna','terra','sol','main')),
  session_ref text not null check (btrim(session_ref)<>'' and char_length(session_ref)<=300),
  lease_token uuid not null unique,
  acquired_at timestamptz not null default now(),
  expires_at timestamptz not null,
  idempotency_key uuid not null unique,
  check (expires_at > acquired_at)
);

-- This is an immutable command receipt, not a second lifecycle tracker.  It
-- supplies the uniqueness that public.event deliberately does not have, while
-- Work Request and lane-lock rows remain the only package and lease state.
create table if not exists ops.siep_command_receipt (
  idempotency_key uuid primary key,
  verb text not null check (verb in ('claim','transition','acquire_lane','release_lane')),
  package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  request_digest text not null check (request_digest ~ '^sha256:[0-9a-f]{64}$'),
  result jsonb not null check (jsonb_typeof(result)='object'),
  recorded_actor_id uuid not null references public.actor(id) on delete restrict,
  recorded_at timestamptz not null default now()
  ,constraint siep_command_receipt_never_stores_lease_token check (not (result ? 'lease_token'))
);

-- Immutable admission metadata binds an existing job execution to one package,
-- Work Request version, manifest, and server-owned purpose contract.  Job and
-- attempt state remain exclusively in the existing ops.job ledger.
create table if not exists ops.siep_job_evidence_binding (
  job_id uuid primary key references ops.job(id) on delete restrict,
  package_key text not null references ops.siep_package_contract(package_key) on delete restrict,
  work_request_version integer not null check (work_request_version>0),
  manifest_digest text not null check (manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  evidence_kind text not null check (evidence_kind in (
    'source','tests','migration','deploy','readback','live_readback','rollback',
    'independent_review','zero_unresolved_findings','zero_blockers',
    'two_clean_audit_cycles','material_fix'
  )),
  definition_key text not null,
  definition_version integer not null,
  bound_by_actor_id uuid not null references public.actor(id) on delete restrict,
  idempotency_key uuid not null unique,
  bound_at timestamptz not null default now(),
  foreign key (definition_key,definition_version) references ops.job_definition(key,version),
  check (definition_key='engineering-slice' and definition_version=1)
);

create or replace function ops.siep_append_only_guard()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops as $$
begin
  raise exception 'SIEP program contracts, dependencies, aliases, and evidence links are append-only';
end $$;

create trigger siep_package_contract_append_only
before update or delete on ops.siep_package_contract
for each row execute function ops.siep_append_only_guard();
create trigger siep_program_dependency_append_only
before update or delete on ops.siep_program_dependency
for each row execute function ops.siep_append_only_guard();
create trigger siep_component_alias_append_only
before update or delete on ops.siep_component_alias
for each row execute function ops.siep_append_only_guard();
create trigger siep_evidence_link_append_only
before update or delete on ops.siep_evidence_link
for each row execute function ops.siep_append_only_guard();
create trigger siep_command_receipt_append_only
before update or delete on ops.siep_command_receipt
for each row execute function ops.siep_append_only_guard();
create trigger siep_job_evidence_binding_append_only
before update or delete on ops.siep_job_evidence_binding
for each row execute function ops.siep_append_only_guard();

-- Forty first-class packages.  06 and 24 remain aggregate labels and are not rows.
with package(package_key,ordinal,ref,title,tier,approval_gate) as (values
  ('00',1,'WR-SIEP-00','Live collision reconciliation','main','none'),
  ('B0',2,'WR-SIEP-B0','Canonical ledger and DAG bootstrap','main','none'),
  ('01',3,'WR-SIEP-01','Heavy-build good versus half-executed reproduction','sol','none'),
  ('02',4,'WR-SIEP-02','Deterministic trigger classifier and scoped delivery','sol','none'),
  ('03',5,'WR-SIEP-03','Signed Session Passport','sol','none'),
  ('04',6,'WR-SIEP-04','Runtime enforcement and truthful completion','sol','none'),
  ('05',7,'WR-SIEP-05','Scheduler incidents nightly and self-healing','terra','none'),
  ('06A',8,'WR-SIEP-06A','Evidence graph','sol','none'),
  ('06B',9,'WR-SIEP-06B','Terminal closure authority','main','none'),
  ('10',10,'WR-SIEP-10','SCAC charter and taxonomy','terra','none'),
  ('11',11,'WR-SIEP-11','Mutation registry and default-deny ingress','sol','none'),
  ('12',12,'WR-SIEP-12','Monotonic transactional epoch and compatibility state','sol','none'),
  ('13',13,'WR-SIEP-13','Immutable artifact registry signing and transparency','sol','none'),
  ('14',14,'WR-SIEP-14','Root trust ceremony and offline recovery custodians','main','none'),
  ('15',15,'WR-SIEP-15','Device enrollment lifecycle and assurance','sol','none'),
  ('16',16,'WR-SIEP-16','SCAC-POP verifier libraries and golden vectors','sol','none'),
  ('17',17,'WR-SIEP-17','Token challenge revocation and kill switch','sol','none'),
  ('18',18,'WR-SIEP-18','Atomic database reference monitor and grant cutover','sol','none'),
  ('19',19,'WR-SIEP-19','Signed updater and convergence','sol','none'),
  ('20',20,'WR-SIEP-20','Shared rules tools source projection and declared overlays','sol','none'),
  ('21',21,'WR-SIEP-21','Workload identities','sol','none'),
  ('22',22,'WR-SIEP-22','Offline and partition drafts-only policy','terra','none'),
  ('23',23,'WR-SIEP-23','Health SLO alerts runbooks and Completion Register evidence','sol','none'),
  ('24A',24,'WR-SIEP-24A','SCAC integrated adversarial verification','main','none'),
  ('25',25,'WR-SIEP-25','SCAC staged canary and enforcement','main','joe_approval'),
  ('26',26,'WR-SIEP-26','SCAC legacy retirement and drift scans','sol','none'),
  ('30',30,'WR-SIEP-30','Dell signed consent and policy enrollment','main','none'),
  ('31',31,'WR-SIEP-31','Dell managed typed task broker','sol','none'),
  ('32',32,'WR-SIEP-32','Signed Host Broker and isolated VM','sol','none'),
  ('33',33,'WR-SIEP-33','Claude typed adapter and governed Model Gateway','sol','none'),
  ('34',34,'WR-SIEP-34','Encrypted stale-file quarantine and restore','sol','none'),
  ('35',35,'WR-SIEP-35','Capability build install and rollback','sol','none'),
  ('36',36,'WR-SIEP-36','Structured evidence redaction and Completion Register','sol','none'),
  ('37',37,'WR-SIEP-37','MPE SLO runbooks incidents offboarding and recovery','terra','none'),
  ('24B',38,'WR-SIEP-24B','Dell MPE integrated adversarial verification','main','none'),
  ('40',40,'WR-SIEP-40','Exhaustive system integrity audit','main','none'),
  ('41',41,'WR-SIEP-41','Dependency-first finding elimination','main','none'),
  ('42',42,'WR-SIEP-42','Full end-to-end verification','main','none'),
  ('43',43,'WR-SIEP-43','Staged Production rollout','main','joe_go_no_go'),
  ('44',44,'WR-SIEP-44','Retirement and continuous prevention','main','none')
), inserted as (
  insert into ops.work_request
    (program_ordinal,program_key,ref,title,disposition,existing_status,state,
     desired_outcome,acceptance_criteria,project_context,requester_actor,owner_actor,
     organization_tenant_id,shape_disposition,shape_fixed_surface_ref,shape_rationale,
     shape_decided_by_actor_id,shape_decided_at)
  select ordinal,'carr-system-integrity-elimination-v1',ref,title,'extend','reviewed','ready',
         title,
         jsonb_build_array('Dependencies closed','Tests pass','Rollback proven','Independent evidence linked'),
         jsonb_build_object('package_key',package_key,'terminal_authority','SIEP-06B') ||
         case when package_key in ('15','21','23','33','35') then jsonb_build_object(
           'optional_capability_profile','studio-executor',
           'availability','optional_non_blocking',
           'hardware_facts_discovered',jsonb_build_array(
             'exact_model','cpu','gpu','memory','storage','os','filevault','sip','virtualization_entitlement'),
           'benchmark_gated',jsonb_build_array(
             'thermal_sustained_cpu_gpu','ssd','vm_isolation','mlx_inference_context_memory',
             'concurrent_jobs','reboot_power_loss','network_egress','workload_quotas','failover'),
           'permitted_after_receipts',jsonb_build_array(
             'isolated_build_test_vms','governed_model_gateway_mlx_metal_provider',
             'classification_redaction_indexing_evals','signed_artifact_builds',
             'clean_room_install_verification','compute_heavy_routing',
             'provider_egress_sensitive_routing','warm_ci_cache','recovery_standby'),
           'hard_boundaries',jsonb_build_array(
             'not_source_of_truth','not_critical_dependency','no_offline_root_signing_authority',
             'Dell filesystem and admin actions remain on Dell MPE',
             'outage_does_not_affect_record_access_dell_or_central_core',
             'no_performance_or_security_claim_before_receipts'))
         else '{}'::jsonb end,
         'joe','joe','carr-internal','not_required',
         'decision:e1e04703-4de6-4947-b2fa-ea0133c6bd74',
         'The integrated SIEP architecture and exact DAG were reviewed before this bootstrap; package implementation follows that fixed surface.',
         (select id from public.actor where slug='joe' and active limit 1),now()
    from package
  returning id,ref
)
insert into ops.siep_package_contract
  (package_key,work_request_id,lane_key,minimum_executor_tier,test_contract,delivery_contract,
   rollback_contract,required_evidence_kinds,approval_gate)
select p.package_key,w.id,
       case when p.package_key in ('01','02','03','04','05') then 'heavy-build'
            when p.package_key ~ '^(1[0-9]|2[0-3]|24A|25|26)$' then 'scac-core'
            when p.package_key ~ '^(3[0-7]|24B)$' then 'dell-mpe'
            else 'program-control' end,
       p.tier,
       jsonb_build_object('required',jsonb_build_array('unit','contract','independent')),
       jsonb_build_object('source','required','migration','declared','deploy',
         case when p.package_key in ('25','43') then 'approval_gated' else 'declared' end,
         'readback','required','rollback','required'),
       jsonb_build_object('required',true,'forward_compensating',true),
       case when p.package_key='44'
         then array['source','tests','readback','rollback','independent_review','live_readback',
                    'zero_unresolved_findings','zero_blockers','two_clean_audit_cycles']::text[]
         when p.package_key='41'
         then array['source','tests','readback','rollback','independent_review','material_fix']::text[]
         when p.approval_gate='joe_approval'
         then array['source','tests','deploy','readback','rollback','independent_review','joe_approval']::text[]
         when p.approval_gate='joe_go_no_go'
         then array['source','tests','deploy','readback','rollback','independent_review','joe_go_no_go']::text[]
         else array['source','tests','readback','rollback','independent_review']::text[] end,
       p.approval_gate
  from package p join inserted w on w.ref=p.ref;

insert into ops.siep_program_dependency(package_key,depends_on_package_key) values
  ('B0','00'),('01','B0'),('02','01'),('06A','B0'),('10','B0'),
  ('11','10'),('12','11'),('13','12'),('14','13'),('15','14'),('16','15'),
  ('17','16'),('18','17'),('19','18'),('20','19'),('21','20'),('22','21'),
  ('26','25'),('31','30'),('32','31'),('33','32'),('34','33'),('35','34'),
  ('36','35'),('37','36'),('41','40'),
  ('40','05'),('40','06B'),('40','24A'),('40','24B'),('40','25'),('40','26'),('40','37'),
  ('03','02'),('03','12'),('03','15'),('03','17'),('03','20'),
  ('04','03'),('04','11'),('04','18'),
  ('05','04'),('05','17'),('05','18'),('05','21'),('05','23'),
  ('23','06A'),('23','12'),('23','17'),('23','18'),('23','19'),('23','20'),('23','21'),('23','22'),
  ('06B','06A'),('06B','04'),('06B','23'),
  ('24A','14'),('24A','16'),('24A','17'),('24A','18'),('24A','19'),
  ('24A','20'),('24A','21'),('24A','22'),('24A','23'),
  ('25','24A'),('30','15'),('30','23'),
  ('24B','24A'),('24B','30'),('24B','31'),('24B','32'),('24B','33'),
  ('24B','34'),('24B','35'),('24B','36'),('24B','37'),
  ('42','05'),('42','06B'),('42','24A'),('42','24B'),('42','25'),('42','26'),('42','37'),('42','41'),
  ('43','42'),('44','43');

insert into ops.siep_component_alias(alias_key,package_key) values
  ('SCAC-00','10'),('SCAC-01','11'),('SCAC-02','12'),('SCAC-03','13'),
  ('SCAC-04','14'),('SCAC-05','15'),('SCAC-06','16'),('SCAC-07','17'),
  ('SCAC-08','18'),('SCAC-09','19'),('SCAC-10','20'),('SCAC-11','21'),
  ('SCAC-12','22'),('SCAC-13','23'),('SCAC-14','24A'),('SCAC-15','25'),('SCAC-16','26'),
  ('MPE-17A','30'),('MPE-17B','31'),('MPE-17C','32'),('MPE-17D','33'),
  ('MPE-17E','34'),('MPE-17F','35'),('MPE-17G','36'),('MPE-17H','37');

create or replace function ops.siep_manifest_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $$
begin
  raise exception 'the reviewed SIEP package, dependency, and alias manifest is sealed';
end $$;
create trigger siep_package_contract_sealed_before_insert
before insert on ops.siep_package_contract for each row execute function ops.siep_manifest_insert_guard();
create trigger siep_program_dependency_sealed_before_insert
before insert on ops.siep_program_dependency for each row execute function ops.siep_manifest_insert_guard();
create trigger siep_component_alias_sealed_before_insert
before insert on ops.siep_component_alias for each row execute function ops.siep_manifest_insert_guard();

-- The pre-SIEP projection assumed every program was a simple ordinal queue.
-- Preserve that behavior for existing programs and derive SIEP readiness from
-- the reviewed dependency graph instead.
create or replace view ops.v_capability_program_next as
select w.* from ops.work_request w
 where w.program_key is not null
   and w.program_key<>'carr-system-integrity-elimination-v1'
   and w.state<>'confirmed_closed'
   and not exists(select 1 from ops.work_request p where p.program_key=w.program_key
                    and p.program_ordinal<w.program_ordinal and p.state<>'confirmed_closed')
union all
select w.* from ops.work_request w join ops.siep_package_contract c on c.work_request_id=w.id
 where w.state<>'confirmed_closed'
   and not exists(select 1 from ops.siep_program_dependency d
     join ops.siep_package_contract dc on dc.package_key=d.depends_on_package_key
     join ops.work_request dw on dw.id=dc.work_request_id
     where d.package_key=c.package_key and dw.state<>'confirmed_closed');

create or replace function ops.siep_manifest_digest()
returns text language sql stable security definer set search_path=pg_catalog,ops,public as $$
  select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object(
    'program_key','carr-system-integrity-elimination-v1',
    'packages',(select jsonb_agg(jsonb_build_object('key',c.package_key,'work_request',w.ref,
      'ordinal',w.program_ordinal,'title',w.title,'lane',c.lane_key,'tier',c.minimum_executor_tier,
      'approval_gate',c.approval_gate,'test_contract',c.test_contract,
      'delivery_contract',c.delivery_contract,'rollback_contract',c.rollback_contract,
      'required_evidence_kinds',to_jsonb(c.required_evidence_kinds)) order by w.program_ordinal)
      from ops.siep_package_contract c join ops.work_request w on w.id=c.work_request_id),
    'dependencies',(select jsonb_agg(jsonb_build_array(package_key,depends_on_package_key)
      order by package_key,depends_on_package_key) from ops.siep_program_dependency),
    'aliases',(select jsonb_agg(jsonb_build_array(alias_key,package_key) order by alias_key)
      from ops.siep_component_alias)
  )),'sha256'),'hex')
$$;

create or replace function ops.siep_request_digest(p_request jsonb)
returns text language sql immutable security definer set search_path=pg_catalog,ops,public as $$
  select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_request),'sha256'),'hex')
$$;

create or replace function ops.siep_resolve_package(p_component text)
returns text language sql stable security definer set search_path=pg_catalog,ops as $$
  select coalesce(
    (select package_key from ops.siep_package_contract where package_key=p_component),
    (select package_key from ops.siep_component_alias where alias_key=p_component)
  )
$$;

create or replace function ops.siep_joe_decision_event_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare old_scoped boolean:=false; new_scoped boolean:=false;
begin
  if tg_op<>'INSERT' then
    select old.verb='siep-joe-decision'
      and old.new_value->>'program_key'='carr-system-integrity-elimination-v1'
      and ops.siep_resolve_package(old.new_value->>'package_key') is not null
      and old.new_value->>'gate' in ('joe_approval','joe_go_no_go')
      into old_scoped;
  end if;
  if tg_op<>'DELETE' then
    select new.verb='siep-joe-decision'
      and new.new_value->>'program_key'='carr-system-integrity-elimination-v1'
      and ops.siep_resolve_package(new.new_value->>'package_key') is not null
      and new.new_value->>'gate' in ('joe_approval','joe_go_no_go')
      into new_scoped;
  end if;
  if tg_op='INSERT' and new_scoped and session_user<>'carr_authority_joe' then
    raise exception 'SIEP Joe decisions require the authenticated Joe authority session';
  end if;
  if tg_op in ('UPDATE','DELETE') and (old_scoped or new_scoped) then
    raise exception 'SIEP Joe decision events are immutable';
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;

create trigger siep_joe_decision_event_guard_before_write
before insert or update or delete on public.event
for each row execute function ops.siep_joe_decision_event_guard();

create or replace function ops.siep_record_joe_decision(
  p_component text,p_gate text,p_decision text,p_idempotency_key uuid
) returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; c ops.siep_package_contract%rowtype; w ops.work_request%rowtype;
        joe_id uuid; existing public.event%rowtype; eid uuid; payload jsonb;
        decision_at timestamptz;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP decisions require the authenticated Joe authority session';
  end if;
  k:=ops.siep_resolve_package(p_component);
  select * into c from ops.siep_package_contract where package_key=k;
  select * into w from ops.work_request where id=c.work_request_id;
  if k is null or p_idempotency_key is null or p_gate<>c.approval_gate
     or p_gate not in ('joe_approval','joe_go_no_go')
     or (p_gate='joe_approval' and p_decision not in ('approved','rejected','revoked'))
     or (p_gate='joe_go_no_go' and p_decision not in ('go','no_go','revoked')) then
    raise exception 'exact SIEP approval gate and typed decision are required';
  end if;
  select id into joe_id from public.actor where slug='joe' and active;
  if joe_id is null then raise exception 'active Joe actor is required'; end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-joe-decision:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-joe-decision-package:'||k,0));
  payload:=jsonb_build_object('program_key','carr-system-integrity-elimination-v1',
    'package_key',k,'work_request_version',w.version,'gate',p_gate,'decision',p_decision,
    'manifest_digest',ops.siep_manifest_digest());
  select * into existing from public.event where actor_id=joe_id and verb='siep-joe-decision'
    and idempotency_key=p_idempotency_key::text order by occurred_at,id limit 1;
  if found then
    if existing.subject_id<>w.id or existing.new_value<>payload then
      raise exception 'idempotency_key_reuse: SIEP Joe decision inputs changed';
    end if;
    return existing.id;
  end if;
  select greatest(clock_timestamp(),coalesce(max(e.occurred_at)+interval '1 microsecond',clock_timestamp()))
    into decision_at from public.event e
   where e.actor_id=joe_id and e.verb='siep-joe-decision'
     and e.new_value->>'package_key'=k and e.new_value->>'gate'=p_gate;
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,
                           old_value,new_value,cause,agent_rationale,idempotency_key)
  values(decision_at,joe_id,'siep-joe-decision','work_request',w.id,'decision',null,payload,
         'human_stated','typed SIEP authority decision',p_idempotency_key::text)
  returning id into eid;
  return eid;
end $$;

create or replace function ops.siep_read_program()
returns jsonb language sql stable security definer set search_path=pg_catalog,ops as $$
  select jsonb_build_object(
    'program_key','carr-system-integrity-elimination-v1',
    'manifest_digest',ops.siep_manifest_digest(),
    'packages',coalesce(jsonb_agg(jsonb_build_object(
      'package_key',c.package_key,'work_request_ref',w.ref,'title',w.title,'state',w.state,
      'version',w.version,'owner',w.owner_actor,'executor',w.executor_actor,
      'minimum_executor_tier',c.minimum_executor_tier,'approval_gate',c.approval_gate,
      'dependencies',(select coalesce(jsonb_agg(d.depends_on_package_key order by d.depends_on_package_key),'[]'::jsonb)
                        from ops.siep_program_dependency d where d.package_key=c.package_key),
      'evidence',(select coalesce(jsonb_object_agg(e.evidence_kind,e.count),'{}'::jsonb)
                    from (select evidence_kind,count(*) from ops.siep_evidence_link
                           where package_key=c.package_key group by evidence_kind) e),
      'delivery_contract',c.delivery_contract
    ) order by w.program_ordinal),'[]'::jsonb)
  ) from ops.siep_package_contract c join ops.work_request w on w.id=c.work_request_id
$$;

create or replace function ops.siep_claim_package(
  p_component text,p_session_ref text,p_lease_token uuid,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; w ops.work_request%rowtype; c ops.siep_package_contract%rowtype;
        a public.actor%rowtype; prior ops.siep_command_receipt%rowtype; lane ops.siep_lane_lock%rowtype;
        result jsonb; request_digest text;
begin
  k:=ops.siep_resolve_package(p_component);
  if k is null or p_idempotency_key is null or p_lease_token is null
     or coalesce(p_session_ref,'') !~ '^session:[a-zA-Z0-9._:-]{1,240}$' then
    raise exception 'known SIEP package, bounded session, lease token, and UUID idempotency key are required';
  end if;
  select * into c from ops.siep_package_contract where package_key=k;
  select * into a from public.actor where slug='system' and active for share;
  if not found then raise exception 'active system actor is required'; end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-package:'||k,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-lane:'||c.lane_key,0));
  request_digest:=ops.siep_request_digest(jsonb_build_object(
    'package_key',k,'session_ref',p_session_ref,'lease_token',p_lease_token));
  if exists(select 1 from ops.siep_evidence_link where idempotency_key=p_idempotency_key) then
    raise exception 'idempotency_key_reuse: SIEP evidence key cannot be reused for a command';
  end if;
  select * into prior from ops.siep_command_receipt where idempotency_key=p_idempotency_key;
  if found then
    if prior.verb<>'claim' or prior.package_key<>k or prior.request_digest<>request_digest then
      raise exception 'idempotency_key_reuse: SIEP claim inputs changed';
    end if;
    return prior.result||jsonb_build_object('replayed',true);
  end if;
  select w0.* into w from ops.work_request w0 where w0.id=c.work_request_id for update;
  if w.state<>'ready' then raise exception 'SIEP package % is %, not ready',k,w.state; end if;
  select * into lane from ops.siep_lane_lock where lane_key=c.lane_key for update;
  if not found or lane.package_key<>k or lane.holder_actor_id<>a.id
     or lane.executor_tier<>c.minimum_executor_tier or lane.work_request_version<>w.version
     or lane.session_ref<>p_session_ref or lane.lease_token<>p_lease_token
     or lane.expires_at<=now() then
    raise exception 'SIEP claim requires the exact live server-derived lane lock and Work Request version';
  end if;
  if exists (select 1 from ops.siep_program_dependency d join ops.siep_package_contract dc on dc.package_key=d.depends_on_package_key
             join ops.work_request dw on dw.id=dc.work_request_id where d.package_key=k and dw.state<>'confirmed_closed') then
    raise exception 'SIEP package % has unresolved dependencies',k;
  end if;
  if c.approval_gate<>'none' and not ops.siep_current_approval(k,w.version,c.approval_gate)
    then raise exception 'SIEP package % admission requires %',k,c.approval_gate; end if;
  result:=jsonb_build_object('package_key',k,'state','claimed','version',w.version+1,
                             'executor_actor','system','executor_tier',c.minimum_executor_tier,
                             'lane_key',c.lane_key,'session_ref',p_session_ref,
                             'lease_digest','sha256:'||encode(public.digest(p_lease_token::text,'sha256'),'hex'));
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause,agent_rationale,idempotency_key)
  values(now(),a.id,'siep-claim-package','work_request',w.id,'state',to_jsonb(w.state),
         result,
         'system','typed SIEP dependency-first claim',p_idempotency_key::text);
  update ops.work_request set state='claimed',executor_actor='system',claimed_at=now(),updated_at=now(),version=version+1 where id=w.id;
  update ops.siep_lane_lock set work_request_version=w.version+1
   where lane_key=c.lane_key and lease_token=p_lease_token and session_ref=p_session_ref;
  insert into ops.siep_command_receipt(idempotency_key,verb,package_key,request_digest,result,recorded_actor_id)
  values(p_idempotency_key,'claim',k,request_digest,result,a.id);
  return result||jsonb_build_object('replayed',false);
end $$;

create or replace function ops.siep_evidence_actor(p_ledger_kind text,p_ledger_id uuid)
returns uuid language plpgsql stable security definer set search_path=pg_catalog,ops,public as $$
declare aid uuid;
begin
  case p_ledger_kind
    when 'job_receipt' then select a.id into aid from ops.job_receipt r join ops.job j on j.id=r.job_id join public.actor a on a.slug=coalesce(j.lease_owner,'system') where r.id=p_ledger_id;
    when 'decision_event' then select actor_id into aid from public.event where id=p_ledger_id and verb='siep-joe-decision';
    else aid:=null;
  end case;
  if aid is null then raise exception 'evidence target does not exist in its canonical ledger'; end if;
  return aid;
end $$;

create or replace function ops.siep_current_evidence_digest(p_ledger_kind text,p_ledger_id uuid)
returns text language sql stable security definer set search_path=pg_catalog,ops,public as $$
  select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(source_row),'sha256'),'hex')
    from (
      select jsonb_build_object('receipt',to_jsonb(r),'job',to_jsonb(j),'attempt',to_jsonb(a),
                                'binding',to_jsonb(b)) source_row
        from ops.job_receipt r join ops.job j on j.id=r.job_id
        join ops.job_attempt a on a.job_id=j.id and a.attempt=r.attempt
        join ops.siep_job_evidence_binding b on b.job_id=j.id
       where p_ledger_kind='job_receipt' and r.id=p_ledger_id
      union all select to_jsonb(e) from public.event e
       where p_ledger_kind='decision_event' and e.id=p_ledger_id
    ) canonical
$$;

create or replace function ops.siep_current_approval(
  p_package_key text,p_work_request_version integer,p_gate text
) returns boolean language sql stable security definer set search_path=pg_catalog,ops,public as $$
  select exists(
    select 1 from ops.siep_package_contract c
      join ops.work_request w on w.id=c.work_request_id
      join ops.siep_evidence_link e on e.package_key=c.package_key
      join public.event d on d.id=e.ledger_id and e.ledger_kind='decision_event'
      join public.actor joe on joe.id=d.actor_id and joe.slug='joe' and joe.active
     where c.package_key=p_package_key and c.approval_gate=p_gate
       and p_gate in ('joe_approval','joe_go_no_go')
       and e.work_request_version=p_work_request_version and e.evidence_kind=p_gate
       and e.manifest_digest=ops.siep_manifest_digest()
       and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)
       and e.source_observed_at=d.occurred_at and d.verb='siep-joe-decision'
       and d.new_value->>'package_key'=c.package_key
       and d.new_value->>'gate'=c.approval_gate
       and d.new_value->>'manifest_digest'=ops.siep_manifest_digest()
       and d.new_value->>'decision'=case when p_gate='joe_go_no_go' then 'go' else 'approved' end
       and d.occurred_at>=coalesce((
         select max(dw.closed_at) from ops.siep_program_dependency dep
           join ops.siep_package_contract dc on dc.package_key=dep.depends_on_package_key
           join ops.work_request dw on dw.id=dc.work_request_id
          where dep.package_key=c.package_key
       ),w.captured_at)
       and not exists(
         select 1 from public.event later
          where later.actor_id=joe.id and later.verb='siep-joe-decision'
            and later.new_value->>'package_key'=c.package_key
            and later.new_value->>'gate'=c.approval_gate
            and (later.occurred_at,later.id)>(d.occurred_at,d.id)
       )
  )
$$;

create or replace function ops.siep_bind_evidence_job(
  p_component text,p_base_version integer,p_evidence_kind text,p_job_id uuid,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; w ops.work_request%rowtype; existing ops.siep_job_evidence_binding%rowtype;
        aid uuid; result jsonb;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP evidence binding requires the authenticated Joe authority session';
  end if;
  k:=ops.siep_resolve_package(p_component);
  if k is null or p_base_version is null or p_job_id is null or p_idempotency_key is null
     or p_evidence_kind not in ('source','tests','migration','deploy','readback','live_readback',
       'rollback','independent_review','zero_unresolved_findings','zero_blockers',
       'two_clean_audit_cycles','material_fix') then
    raise exception 'known package, version, purpose, admitted job, and idempotency key are required';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-evidence-job:'||p_job_id,0));
  select * into existing from ops.siep_job_evidence_binding
   where idempotency_key=p_idempotency_key or job_id=p_job_id order by bound_at limit 1;
  if found then
    if (existing.package_key,existing.work_request_version,existing.evidence_kind,existing.job_id)
       is distinct from (k,p_base_version,p_evidence_kind,p_job_id) then
      raise exception 'idempotency_key_reuse: SIEP evidence binding inputs changed';
    end if;
    return jsonb_build_object('job_id',existing.job_id,'package_key',existing.package_key,
      'evidence_kind',existing.evidence_kind,'replayed',true);
  end if;
  select w0.* into w from ops.work_request w0 join ops.siep_package_contract c
    on c.work_request_id=w0.id where c.package_key=k for share;
  if w.version<>p_base_version then raise exception 'SIEP evidence binding requires the exact Work Request version'; end if;
  select id into aid from public.actor where slug='joe' and active;
  if aid is null then raise exception 'active Joe actor is required'; end if;
  if not exists(
    select 1 from ops.job j
      join ops.engineering_execution_envelope env on env.job_id=j.id and env.work_request_id=w.id
      join ops.engineering_slice_plan sp on sp.id=env.slice_plan_id and sp.work_request_id=w.id
      join ops.engineering_slice_receipt er on er.envelope_id=env.id and er.work_request_id=w.id
      join ops.job_attempt ja on ja.id=er.job_attempt_id and ja.job_id=j.id
        and ja.attempt=j.attempt and ja.state='succeeded'
      join ops.engineering_reviewer_fact rf on rf.receipt_id=er.id and rf.work_request_id=w.id
     where j.id=p_job_id and j.definition_key='engineering-slice' and j.definition_version=1
       and j.payload->>'work_request'=w.ref and j.state='succeeded'
       and env.state_version=p_base_version and sp.work_request_version=p_base_version
       and er.outcome='claimed_complete' and rf.state='passed'
       and rf.reviewer_actor_id=aid and rf.reviewer_actor_id<>er.executor_actor_id
       and exists(select 1 from ops.job_receipt jr where jr.job_id=j.id
                    and jr.attempt=j.attempt and jr.kind='completion')
  ) then
    raise exception 'SIEP evidence binding requires an exact independently reviewed engineering envelope and completion';
  end if;
  insert into ops.siep_job_evidence_binding(job_id,package_key,work_request_version,
    manifest_digest,evidence_kind,definition_key,definition_version,bound_by_actor_id,idempotency_key)
  values(p_job_id,k,p_base_version,ops.siep_manifest_digest(),p_evidence_kind,
    'engineering-slice',1,aid,p_idempotency_key);
  result:=jsonb_build_object('job_id',p_job_id,'package_key',k,
    'evidence_kind',p_evidence_kind,'replayed',false);
  return result;
end $$;

create or replace function ops.siep_attach_evidence(
  p_component text,p_evidence_kind text,p_ledger_id uuid,p_ledger_kind text,
  p_evidence_digest text,p_note text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; aid uuid; prior ops.siep_evidence_link%rowtype; eid uuid; actor_slug text;
        w ops.work_request%rowtype; c ops.siep_package_contract%rowtype;
        source_row jsonb; source_at timestamptz;
        server_digest text;
begin
  k:=ops.siep_resolve_package(p_component);
  if k is null or p_idempotency_key is null or p_note !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(p_note)>300 then
    raise exception 'known SIEP package, safe reference, and UUID idempotency key are required';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  select * into prior from ops.siep_evidence_link where idempotency_key=p_idempotency_key;
  if found then
    if (prior.package_key,prior.evidence_kind,prior.ledger_kind,prior.ledger_id,prior.evidence_digest,prior.note)
       is distinct from (k,p_evidence_kind,p_ledger_kind,p_ledger_id,p_evidence_digest,p_note) then
      raise exception 'idempotency_key_reuse: SIEP evidence inputs changed';
    end if;
    return jsonb_build_object('id',prior.id,'package_key',prior.package_key,'replayed',true);
  end if;
  if exists(select 1 from ops.siep_command_receipt where idempotency_key=p_idempotency_key) then
    raise exception 'idempotency_key_reuse: SIEP command key cannot be reused for evidence';
  end if;
  select w0.* into w from ops.work_request w0 join ops.siep_package_contract pc on pc.work_request_id=w0.id where pc.package_key=k for share;
  select * into c from ops.siep_package_contract where package_key=k;
  case p_ledger_kind
    when 'job_receipt' then
      select system_actor.id,jsonb_build_object('receipt',to_jsonb(r),'job',to_jsonb(j),
                                                'attempt',to_jsonb(ja),'binding',to_jsonb(b)),r.created_at
        into aid,source_row,source_at
        from ops.job_receipt r join ops.job j on j.id=r.job_id
        join ops.job_attempt ja on ja.job_id=j.id and ja.attempt=r.attempt
        join ops.siep_job_evidence_binding b on b.job_id=j.id
        join public.actor system_actor on system_actor.slug='system' and system_actor.active
       where r.id=p_ledger_id and j.payload->>'work_request'=w.ref
         and j.state='succeeded' and ja.state='succeeded'
         and j.attempt=r.attempt and r.kind='completion'
         and b.package_key=k and b.work_request_version=w.version
         and b.manifest_digest=ops.siep_manifest_digest()
         and b.evidence_kind=p_evidence_kind
         and b.definition_key=j.definition_key and b.definition_version=j.definition_version;
    when 'decision_event' then
      select e.actor_id,to_jsonb(e),e.occurred_at into aid,source_row,source_at
        from public.event e where e.id=p_ledger_id and e.verb='siep-joe-decision'
          and e.new_value->>'package_key'=k;
    else source_row:=null;
  end case;
  if source_row is null or aid is null then raise exception 'evidence target is not a valid package-bound canonical fact'; end if;
  if source_at>now()+interval '1 minute' then raise exception 'future SIEP evidence is refused'; end if;
  if source_at < (case when p_evidence_kind in ('joe_approval','joe_go_no_go') then w.captured_at
                       else coalesce(w.claimed_at,w.captured_at) end) then
    raise exception 'stale SIEP evidence predates the package execution boundary';
  end if;
  server_digest:=ops.siep_current_evidence_digest(p_ledger_kind,p_ledger_id);
  if p_evidence_digest is distinct from server_digest then raise exception 'SIEP evidence digest does not match its canonical ledger row'; end if;
  if p_ledger_kind='job_receipt' and not (
       source_row#>>'{job,definition_key}'='engineering-slice'
       and source_row#>>'{job,definition_version}'='1'
       and source_row#>>'{job,payload,manifest_digest}'=ops.siep_manifest_digest()
     ) then
    raise exception 'SIEP evidence requires the fixed package-purpose verifier contract';
  end if;
  select slug into actor_slug from public.actor where id=aid;
  if p_evidence_kind in ('joe_approval','joe_go_no_go')
     and (session_user<>'carr_authority_joe' or p_ledger_kind<>'decision_event' or actor_slug<>'joe') then
    raise exception 'Joe approval evidence requires the authenticated Joe authority session and a package-bound Joe decision';
  end if;
  if p_evidence_kind in ('joe_approval','joe_go_no_go') and not (
       source_row#>>'{new_value,package_key}'=k
       and source_row#>>'{new_value,gate}'=p_evidence_kind
       and source_row#>>'{new_value,manifest_digest}'=ops.siep_manifest_digest()
       and source_row#>>'{new_value,decision}'=case when p_evidence_kind='joe_go_no_go' then 'go' else 'approved' end
     ) then raise exception 'Joe approval evidence requires an exact positive typed decision'; end if;
  if p_evidence_kind in ('independent_review','live_readback','zero_unresolved_findings',
                         'zero_blockers','two_clean_audit_cycles') then
    if session_user<>'carr_authority_joe' then
      raise exception 'SIEP independent and terminal evidence requires the authenticated Joe authority session';
    end if;
    select id into aid from public.actor where slug='joe' and active;
    if aid is null or w.executor_actor='joe' then
      raise exception 'independent authority evidence requires active Joe distinct from the package executor';
    end if;
  end if;
  if p_evidence_kind not in ('joe_approval','joe_go_no_go') and not (
       p_ledger_kind='job_receipt' and source_row#>>'{receipt,evidence,status}'='pass'
       and source_row#>>'{receipt,evidence,operation}'=
         case when p_evidence_kind='two_clean_audit_cycles' then 'clean_audit_cycle' else p_evidence_kind end
     ) then raise exception 'SIEP evidence requires a successful package-bound lease completion receipt'; end if;
  if p_evidence_kind='source' and coalesce(source_row#>>'{receipt,evidence,commit_sha}','') !~ '^[0-9a-f]{40,64}$'
    then raise exception 'source evidence requires a commit-bound receipt'; end if;
  if p_evidence_kind='tests' and coalesce(source_row#>>'{receipt,evidence,result_digest}','') !~ '^sha256:[0-9a-f]{64}$'
    then raise exception 'test evidence requires a result-bound receipt'; end if;
  if p_evidence_kind in ('readback','live_readback')
     and coalesce(source_row#>>'{receipt,evidence,target_ref}','') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
    then raise exception 'readback evidence requires a typed target'; end if;
  if p_evidence_kind='rollback'
     and coalesce(source_row#>>'{receipt,evidence,recovery_ref}','') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
    then raise exception 'rollback evidence requires a typed recovery reference'; end if;
  if p_evidence_kind='independent_review'
     and coalesce(source_row#>>'{receipt,evidence,reviewed_artifact_digest}','') !~ '^sha256:[0-9a-f]{64}$'
    then raise exception 'independent review requires an authority-attested artifact-bound receipt'; end if;
  if p_evidence_kind in ('zero_unresolved_findings','zero_blockers') and not (
       source_row#>>'{receipt,evidence,count}'='0'
       and coalesce(source_row#>>'{receipt,evidence,baseline_digest}','') ~ '^sha256:[0-9a-f]{64}$'
     ) then raise exception 'zero-state evidence requires a baseline-bound canonical zero count'; end if;
  if p_evidence_kind='material_fix' and not (
       coalesce(source_row#>>'{receipt,evidence,commit_sha}','') ~ '^[0-9a-f]{40,64}$'
     ) then raise exception 'material-fix evidence requires a commit-bound receipt';
  end if;
  if p_evidence_kind='two_clean_audit_cycles' and not (
    (source_row#>>'{receipt,evidence,cycle}') in ('1','2')
    and (source_row#>>'{receipt,evidence,unresolved_count}')='0'
    and (source_row#>>'{receipt,evidence,blocker_count}')='0'
    and coalesce(source_row#>>'{receipt,evidence,baseline_digest}','') ~ '^sha256:[0-9a-f]{64}$'
    and coalesce(source_row#>>'{receipt,evidence,run_id}','') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  ) then raise exception 'clean audit evidence must be a zero-finding zero-blocker canonical cycle'; end if;
  insert into ops.siep_evidence_link(package_key,evidence_kind,ledger_kind,ledger_id,work_request_version,
    manifest_digest,evidence_digest,note,linked_actor_id,attested_session_principal,source_observed_at,idempotency_key)
  values(k,p_evidence_kind,p_ledger_kind,p_ledger_id,w.version,ops.siep_manifest_digest(),
    p_evidence_digest,p_note,aid,session_user,source_at,p_idempotency_key)
  returning id into eid;
  return jsonb_build_object('id',eid,'package_key',k,'replayed',false);
end $$;

create or replace function ops.siep_terminal_status()
returns jsonb language sql stable security definer set search_path=pg_catalog,ops as $$
  with facts as (
    select count(*) filter(where w.state<>'confirmed_closed') as open_packages,
           count(*) as contract_packages,
           (select count(*) from ops.work_request sw where sw.program_key='carr-system-integrity-elimination-v1') as program_rows,
           count(*) filter(where w.state='blocked' or w.blocker_code is not null) as blocked_packages,
           bool_or(c.package_key='06B' and w.state='confirmed_closed') as closure_authority_closed,
           bool_or(c.package_key='44' and w.state='confirmed_closed') as retirement_closed
      from ops.siep_package_contract c join ops.work_request w on w.id=c.work_request_id
  ), audit as (
    select count(distinct (r.evidence->>'cycle')) as clean_cycles,
           count(distinct (r.evidence->>'baseline_digest')) as baseline_count,
           count(distinct (r.evidence->>'run_id')) as audit_run_count,
           min(e.source_observed_at) filter(where r.evidence->>'cycle'='1') as cycle_one_at,
           min(e.source_observed_at) filter(where r.evidence->>'cycle'='2') as cycle_two_at
      from ops.siep_evidence_link e join ops.job_receipt r on r.id=e.ledger_id
     where e.package_key='44' and e.evidence_kind='two_clean_audit_cycles'
       and e.ledger_kind='job_receipt' and r.evidence->>'operation'='clean_audit_cycle'
       and r.evidence->>'cycle' in ('1','2') and r.evidence->>'unresolved_count'='0'
       and r.evidence->>'blocker_count'='0'
       and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)
  ), mutation as (
    select max(source_observed_at) as last_material_fix_at
      from ops.siep_evidence_link e where evidence_kind='material_fix'
       and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)
  ), zeroes as (
    select exists(select 1 from ops.siep_evidence_link e join ops.job_receipt r on r.id=e.ledger_id
      where e.package_key='44' and e.evidence_kind='zero_unresolved_findings'
        and r.evidence->>'operation'='zero_unresolved_findings' and r.evidence->>'count'='0'
        and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)) as zero_findings,
      exists(select 1 from ops.siep_evidence_link e join ops.job_receipt r on r.id=e.ledger_id
      where e.package_key='44' and e.evidence_kind='zero_blockers'
        and r.evidence->>'operation'='zero_blockers' and r.evidence->>'count'='0'
        and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)) as zero_blockers
  ), live as (
    select exists(select 1 from ops.siep_lane_lock where expires_at>now()) as live_lane_lock,
           exists(select 1 from ops.incident i join ops.incident_link l on l.incident_id=i.id
                   where l.kind='work_request' and l.ref like 'WR-SIEP-%'
                     and i.state not in ('resolved','reviewed')) as open_incident,
           exists(select 1 from ops.siep_evidence_link e join ops.job_receipt r on r.id=e.ledger_id
             where e.package_key='44' and e.evidence_kind='live_readback'
               and r.evidence->>'operation'='live_readback' and r.evidence->>'status'='pass'
               and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)) as live_readback
  )
  select jsonb_build_object(
    'complete',open_packages=0 and contract_packages=40 and program_rows=40 and blocked_packages=0
      and closure_authority_closed and retirement_closed and zero_findings and zero_blockers
      and clean_cycles=2 and audit_run_count=2 and baseline_count=1
      and cycle_one_at>coalesce(last_material_fix_at,'-infinity'::timestamptz) and cycle_two_at>cycle_one_at
      and not live_lane_lock and not open_incident and live_readback,
    'open_packages',open_packages,'closure_authority_closed',closure_authority_closed,
    'retirement_closed',retirement_closed,'blocked_packages',blocked_packages,
    'clean_audit_cycles',clean_cycles,'audit_run_count',audit_run_count,'audit_baseline_count',baseline_count,
    'exact_program_rowset',contract_packages=40 and program_rows=40,
    'last_material_fix_at',last_material_fix_at,'live_lane_lock',live_lane_lock,
    'open_incident',open_incident,'live_readback',live_readback,
    'required_terminal_evidence',jsonb_build_array('zero_unresolved_findings','zero_blockers','two_clean_audit_cycles','live_readback')
  ) from facts cross join audit cross join mutation cross join zeroes cross join live
$$;

create or replace function ops.siep_transition_package(
  p_component text,p_base_version integer,p_target_state text,p_session_ref text,
  p_lease_token uuid,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; w ops.work_request%rowtype; c ops.siep_package_contract%rowtype;
        actor_id uuid; prior ops.siep_command_receipt%rowtype; review_id uuid; missing text[];
        lane ops.siep_lane_lock%rowtype; result jsonb; clean_cycles integer; baselines integer; audit_runs integer;
        last_fix timestamptz; cycle_one timestamptz; cycle_two timestamptz; attestor text; request_digest text;
begin
  k:=ops.siep_resolve_package(p_component);
  if k is null or p_idempotency_key is null or p_lease_token is null or p_base_version<1
     or coalesce(p_session_ref,'') !~ '^session:[a-zA-Z0-9._:-]{1,240}$' then
    raise exception 'known package, positive base version, bounded session, lease token, and UUID idempotency key are required';
  end if;
  select * into c from ops.siep_package_contract where package_key=k;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-package:'||k,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-lane:'||c.lane_key,0));
  request_digest:=ops.siep_request_digest(jsonb_build_object(
    'package_key',k,'base_version',p_base_version,'target_state',p_target_state,
    'session_ref',p_session_ref,'lease_token',p_lease_token));
  if exists(select 1 from ops.siep_evidence_link where idempotency_key=p_idempotency_key) then
    raise exception 'idempotency_key_reuse: SIEP evidence key cannot be reused for a command';
  end if;
  select * into prior from ops.siep_command_receipt where idempotency_key=p_idempotency_key;
  if found then
    if prior.verb<>'transition' or prior.package_key<>k or prior.request_digest<>request_digest then
      raise exception 'idempotency_key_reuse: SIEP transition inputs changed';
    end if;
    return prior.result||jsonb_build_object('replayed',true);
  end if;
  select w0.* into w from ops.work_request w0
    join ops.siep_package_contract c0 on c0.work_request_id=w0.id
   where c0.package_key=k for update of w0;
  if w.version<>p_base_version then raise exception 'version_conflict: expected %, current %',p_base_version,w.version; end if;
  select * into lane from ops.siep_lane_lock where lane_key=c.lane_key for update;
  if not found or lane.package_key<>k or lane.work_request_version<>p_base_version
     or lane.session_ref<>p_session_ref or lane.lease_token<>p_lease_token or lane.expires_at<=now() then
    raise exception 'SIEP transition requires the exact live holder-bound package lane lock';
  end if;
  if (w.state,p_target_state) not in (('claimed','in_progress'),('in_progress','verification'),('verification','awaiting_release'),('awaiting_release','released'),('released','confirmed_closed')) then
    raise exception 'invalid SIEP transition % -> %',w.state,p_target_state;
  end if;
  if p_target_state in ('released','confirmed_closed') then
    select array_agg(req) into missing from unnest(c.required_evidence_kinds) req
     where not exists(select 1 from ops.siep_evidence_link e where e.package_key=k and e.evidence_kind=req
       and e.manifest_digest=ops.siep_manifest_digest()
       and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)
       and (req in ('joe_approval','joe_go_no_go')
            or e.work_request_version>=w.version-case when p_target_state='confirmed_closed' then 1 else 0 end));
    if missing is not null then raise exception 'SIEP package % missing evidence: %',k,missing; end if;
    if not exists(select 1 from ops.siep_evidence_link e
                   where e.package_key=k and e.evidence_kind='independent_review'
                     and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id)) then
      raise exception 'SIEP package % requires independent evidence',k;
    end if;
  end if;
  if p_target_state='confirmed_closed' then
    if exists(select 1 from ops.siep_program_dependency d join ops.siep_package_contract dc on dc.package_key=d.depends_on_package_key
              join ops.work_request dw on dw.id=dc.work_request_id where d.package_key=k and dw.state<>'confirmed_closed') then
      raise exception 'SIEP package % cannot close before its dependencies',k;
    end if;
    if k='44' and exists(select 1 from ops.siep_package_contract pc join ops.work_request pw on pw.id=pc.work_request_id
                          where pc.package_key<>'44' and pw.state<>'confirmed_closed') then
      raise exception 'SIEP terminal authority refuses completion: packages remain open';
    end if;
    if k='44' and ((select count(*) from ops.work_request where program_key='carr-system-integrity-elimination-v1')<>40
                   or (select count(*) from ops.siep_package_contract)<>40) then
      raise exception 'SIEP terminal authority refuses completion: program rowset is not exact';
    end if;
    if k='44' then
      select count(distinct r.evidence->>'cycle'),count(distinct r.evidence->>'baseline_digest'),
             count(distinct r.evidence->>'run_id'),
             min(e.source_observed_at) filter(where r.evidence->>'cycle'='1'),
             min(e.source_observed_at) filter(where r.evidence->>'cycle'='2')
        into clean_cycles,baselines,audit_runs,cycle_one,cycle_two
        from ops.siep_evidence_link e join ops.job_receipt r on r.id=e.ledger_id
       where e.package_key='44' and e.evidence_kind='two_clean_audit_cycles' and e.ledger_kind='job_receipt'
         and r.evidence->>'operation'='clean_audit_cycle' and r.evidence->>'cycle' in ('1','2')
         and r.evidence->>'unresolved_count'='0' and r.evidence->>'blocker_count'='0'
         and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id);
      select max(source_observed_at) into last_fix from ops.siep_evidence_link e where evidence_kind='material_fix'
       and e.evidence_digest=ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id);
      if clean_cycles<>2 or audit_runs<>2 or baselines<>1
         or cycle_one<=coalesce(last_fix,'-infinity'::timestamptz) or cycle_two<=cycle_one
         or exists(select 1 from ops.siep_lane_lock where expires_at>now() and package_key<>'44')
         or exists(select 1 from ops.incident i join ops.incident_link l on l.incident_id=i.id
                   where l.kind='work_request' and l.ref like 'WR-SIEP-%' and i.state not in ('resolved','reviewed'))
         or exists(select 1 from ops.siep_package_contract pc join ops.work_request pw on pw.id=pc.work_request_id
                   where pw.state='blocked' or pw.blocker_code is not null) then
        raise exception 'SIEP terminal authority refuses completion: final clean-cycle, blocker, incident, or lane predicate failed';
      end if;
    end if;
    select e.id,a.slug into review_id,attestor from ops.siep_evidence_link e
      join public.actor a on a.id=e.linked_actor_id
     where e.package_key=k and e.evidence_kind='independent_review' order by e.linked_at desc limit 1;
  end if;
  select id into actor_id from public.actor where slug=coalesce(w.executor_actor,'system') and active;
  if actor_id is null then raise exception 'SIEP transition requires an active claimed executor'; end if;
  result:=jsonb_build_object('package_key',k,'base_version',p_base_version,'state',p_target_state,
    'version',w.version+1,'session_ref',p_session_ref,
    'lease_digest','sha256:'||encode(public.digest(p_lease_token::text,'sha256'),'hex'));
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause,agent_rationale,idempotency_key)
  values(now(),actor_id,'siep-transition-package','work_request',w.id,'state',to_jsonb(w.state),result,
         'system','typed SIEP transition',p_idempotency_key::text);
  update ops.work_request set state=p_target_state,version=version+1,updated_at=now(),
    started_at=case when p_target_state='in_progress' then coalesce(started_at,now()) else started_at end,
    closed_at=case when p_target_state='confirmed_closed' then now() else closed_at end,
    completion_kind=case when p_target_state='confirmed_closed' then 'extended' else completion_kind end,
    completion_evidence=case when p_target_state='confirmed_closed' then jsonb_build_object(
      'acceptance_predicates',to_jsonb(c.required_evidence_kinds),
      'change_ref',ops.siep_manifest_digest(),'user_facing',false,'attested_by',attestor,
      'siep_package',k,'evidence_links',(select jsonb_agg(id order by linked_at) from ops.siep_evidence_link where package_key=k)) else completion_evidence end,
    verification_accepted_at=case when p_target_state='confirmed_closed' then now() else verification_accepted_at end,
    verification_evidence_ref=case when p_target_state='confirmed_closed' then review_id::text else verification_evidence_ref end
   where id=w.id;
  if p_target_state='confirmed_closed' then
    delete from ops.siep_lane_lock where lane_key=c.lane_key and package_key=k
      and session_ref=p_session_ref and lease_token=p_lease_token;
  else
    update ops.siep_lane_lock set work_request_version=w.version+1
     where lane_key=c.lane_key and package_key=k
       and session_ref=p_session_ref and lease_token=p_lease_token;
  end if;
  insert into ops.siep_command_receipt(idempotency_key,verb,package_key,request_digest,result,recorded_actor_id)
  values(p_idempotency_key,'transition',k,request_digest,result,actor_id);
  return result||jsonb_build_object('replayed',false);
end $$;

create or replace function ops.siep_acquire_lane_lock(
  p_component text,p_session_ref text,p_lease_seconds integer,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare k text; aid uuid; tok uuid; current_lock ops.siep_lane_lock%rowtype;
        c ops.siep_package_contract%rowtype; w ops.work_request%rowtype;
        prior ops.siep_command_receipt%rowtype; request_digest text; result jsonb;
begin
  k:=ops.siep_resolve_package(p_component);
  select * into c from ops.siep_package_contract where package_key=k;
  select id into aid from public.actor where slug='system' and active;
  if k is null or aid is null
     or coalesce(p_session_ref,'') !~ '^session:[a-zA-Z0-9._:-]{1,240}$' or p_lease_seconds not between 60 and 3600
     or p_idempotency_key is null then raise exception 'bounded typed SIEP lane lock fields are required'; end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-package:'||k,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-lane:'||c.lane_key,0));
  request_digest:=ops.siep_request_digest(jsonb_build_object(
    'package_key',k,'session_ref',p_session_ref,'lease_seconds',p_lease_seconds));
  if exists(select 1 from ops.siep_evidence_link where idempotency_key=p_idempotency_key) then
    raise exception 'idempotency_key_reuse: SIEP evidence key cannot be reused for a command';
  end if;
  select * into prior from ops.siep_command_receipt where idempotency_key=p_idempotency_key;
  if found then
    if prior.verb<>'acquire_lane' or prior.package_key<>k or prior.request_digest<>request_digest then
      raise exception 'idempotency_key_reuse: SIEP lane-lock inputs changed';
    end if;
    return prior.result||jsonb_build_object('replayed',true);
  end if;
  select * into w from ops.work_request where id=c.work_request_id for update;
  select * into current_lock from ops.siep_lane_lock where lane_key=c.lane_key for update;
  if current_lock.lane_key is not null and current_lock.expires_at>now() then
    raise exception 'SIEP lane % is already locked by another lease',c.lane_key;
  end if;
  if current_lock.lane_key is not null and current_lock.package_key<>k and exists(
    select 1 from ops.siep_package_contract pc join ops.work_request pw on pw.id=pc.work_request_id
     where pc.package_key=current_lock.package_key and pw.state not in ('ready','confirmed_closed')
  ) then raise exception 'SIEP lane recovery must resume the prior nonterminal package'; end if;
  if exists(select 1 from ops.siep_package_contract pc join ops.work_request pw on pw.id=pc.work_request_id
     where pc.lane_key=c.lane_key and pc.package_key<>k
       and pw.state not in ('ready','confirmed_closed')) then
    raise exception 'SIEP lane has another nonterminal package requiring recovery';
  end if;
  if w.state not in ('ready','claimed','in_progress','verification','awaiting_release','released') then raise exception 'SIEP package cannot acquire a lane in state %',w.state; end if;
  tok:=gen_random_uuid();
  insert into ops.siep_lane_lock(lane_key,package_key,work_request_version,holder_actor_id,executor_tier,session_ref,lease_token,expires_at,idempotency_key)
  values(c.lane_key,k,w.version,aid,c.minimum_executor_tier,p_session_ref,tok,now()+make_interval(secs=>p_lease_seconds),p_idempotency_key)
  on conflict(lane_key) do update set package_key=excluded.package_key,work_request_version=excluded.work_request_version,
    holder_actor_id=excluded.holder_actor_id,executor_tier=excluded.executor_tier,
    session_ref=excluded.session_ref,lease_token=excluded.lease_token,acquired_at=now(),expires_at=excluded.expires_at,idempotency_key=excluded.idempotency_key;
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause,agent_rationale,idempotency_key)
  values(now(),aid,'siep-acquire-lane-lock','work_request',w.id,'lane_lock',null,
    jsonb_build_object('package_key',k,
      'session_digest','sha256:'||encode(public.digest(p_session_ref,'sha256'),'hex'),
      'lease_seconds',p_lease_seconds,
      'lane_key',c.lane_key,'work_request_version',w.version),
    'system','typed SIEP lane acquisition',null);
  select jsonb_build_object('lane_key',c.lane_key,'package_key',k,'work_request_version',w.version,
    'lease_digest','sha256:'||encode(public.digest(lease_token::text,'sha256'),'hex'),
    'expires_at',expires_at) into result
    from ops.siep_lane_lock where lane_key=c.lane_key and package_key=k;
  insert into ops.siep_command_receipt(idempotency_key,verb,package_key,request_digest,result,recorded_actor_id)
  values(p_idempotency_key,'acquire_lane',k,request_digest,result,aid);
  -- The raw lease token is a one-time delivery secret.  Exact replay remains a
  -- no-op and returns the stable safe result, never a shared-role token oracle.
  return result||jsonb_build_object('lease_token',tok,'replayed',false);
end $$;

create or replace function ops.siep_release_lane_lock(
  p_component text,p_session_ref text,p_lease_token uuid,p_idempotency_key uuid
)
returns boolean language plpgsql security definer set search_path=pg_catalog,ops as $$
declare k text; n integer; c ops.siep_package_contract%rowtype; w ops.work_request%rowtype;
        prior ops.siep_command_receipt%rowtype; aid uuid; released boolean;
        request_digest text; result jsonb; lane ops.siep_lane_lock%rowtype;
begin
  k:=ops.siep_resolve_package(p_component);
  select * into c from ops.siep_package_contract where package_key=k;
  if k is null or p_lease_token is null or p_idempotency_key is null
     or coalesce(p_session_ref,'') !~ '^session:[a-zA-Z0-9._:-]{1,240}$' then
    raise exception 'known package, bounded session, lease token, and idempotency key are required';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('siep-idempotency:'||p_idempotency_key,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-package:'||k,0));
  perform pg_advisory_xact_lock(hashtextextended('siep-lane:'||c.lane_key,0));
  request_digest:=ops.siep_request_digest(jsonb_build_object(
    'package_key',k,'session_ref',p_session_ref,'lease_token',p_lease_token));
  if exists(select 1 from ops.siep_evidence_link where idempotency_key=p_idempotency_key) then
    raise exception 'idempotency_key_reuse: SIEP evidence key cannot be reused for a command';
  end if;
  select * into prior from ops.siep_command_receipt where idempotency_key=p_idempotency_key;
  if found then
    if prior.verb<>'release_lane' or prior.package_key<>k or prior.request_digest<>request_digest then
      raise exception 'idempotency_key_reuse: SIEP lane-release inputs changed';
    end if;
    return (prior.result->>'released')::boolean;
  end if;
  select w0.* into w from ops.work_request w0 where w0.id=c.work_request_id for update;
  select * into lane from ops.siep_lane_lock where lane_key=c.lane_key for update;
  delete from ops.siep_lane_lock where lane_key=c.lane_key and package_key=k
    and session_ref=p_session_ref and lease_token=p_lease_token
    and (expires_at<=now() or w.state in ('ready','confirmed_closed','declined','superseded'));
  get diagnostics n=row_count; released:=n=1;
  select id into aid from public.actor where slug='system' and active;
  result:=jsonb_build_object('package_key',k,'session_ref',p_session_ref,
      'lease_digest','sha256:'||encode(public.digest(p_lease_token::text,'sha256'),'hex'),'released',released);
  insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause,agent_rationale,idempotency_key)
  values(now(),aid,'siep-release-lane-lock','work_request',w.id,'lane_lock',null,
    result,
    'system','typed SIEP lane release',p_idempotency_key::text);
  insert into ops.siep_command_receipt(idempotency_key,verb,package_key,request_digest,result,recorded_actor_id)
  values(p_idempotency_key,'release_lane',k,request_digest,result,aid);
  return released;
end $$;

-- Trigger defense applies to owners/admins too; RLS below is the direct-writer boundary.
create or replace function ops.siep_program_identity_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $$
begin
  if old.program_key='carr-system-integrity-elimination-v1'
     and (to_jsonb(new)-array['state','executor_actor','claimed_at','started_at','closed_at','updated_at','version',
                                  'completion_kind','completion_evidence','verification_accepted_at','verification_evidence_ref'])
         is distinct from
         (to_jsonb(old)-array['state','executor_actor','claimed_at','started_at','closed_at','updated_at','version',
                                  'completion_kind','completion_evidence','verification_accepted_at','verification_evidence_ref']) then
    raise exception 'SIEP package identity and contract projection are immutable';
  end if;
  return new;
end $$;
create trigger siep_program_identity_guard_before_update
before update on ops.work_request for each row execute function ops.siep_program_identity_guard();

create or replace function ops.siep_program_transition_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $$
begin
  if old.program_key is distinct from 'carr-system-integrity-elimination-v1' then return new; end if;
  if old.state='confirmed_closed' and new is distinct from old then raise exception 'closed SIEP package is immutable'; end if;
  if old.state is not distinct from new.state and new is distinct from old then raise exception 'SIEP package mutations require a typed state transition'; end if;
  if old.state is distinct from new.state and (old.state,new.state) not in (
    ('ready','claimed'),('claimed','in_progress'),('in_progress','verification'),
    ('verification','awaiting_release'),('awaiting_release','released'),('released','confirmed_closed')
  ) then raise exception 'invalid SIEP package state transition'; end if;
  if new.version<>old.version+1 then raise exception 'SIEP package transitions require exact version plus one'; end if;
  if old.state='ready' and (new.executor_actor is distinct from 'system' or old.executor_actor is not null) then
    raise exception 'SIEP claim executor is server-derived';
  end if;
  if old.state<>'ready' and new.executor_actor is distinct from old.executor_actor then
    raise exception 'SIEP executor binding is immutable after claim';
  end if;
  if not exists(
    select 1 from public.event e where e.subject_type='work_request' and e.subject_id=old.id
      and e.verb=case when old.state='ready' then 'siep-claim-package' else 'siep-transition-package' end
      and e.old_value=to_jsonb(old.state) and e.new_value->>'state'=new.state
      and (e.new_value->>'version')::integer=new.version
  ) then raise exception 'SIEP package transition requires its exact command event'; end if;
  if new.state<>'confirmed_closed' and (
    new.completion_kind is distinct from old.completion_kind
    or new.completion_evidence is distinct from old.completion_evidence
    or new.verification_accepted_at is distinct from old.verification_accepted_at
    or new.verification_evidence_ref is distinct from old.verification_evidence_ref
    or new.closed_at is distinct from old.closed_at
  ) then raise exception 'SIEP completion fields may change only at terminal package closure'; end if;
  return new;
end $$;
create trigger siep_program_transition_guard_before_update
before update on ops.work_request for each row execute function ops.siep_program_transition_guard();

-- Raw carr_writer DML remains available for ordinary Work Requests but is denied
-- on the exact SIEP program; SECURITY DEFINER typed verbs retain their narrow path.
alter table ops.work_request enable row level security;
drop policy if exists work_request_read_all on ops.work_request;
drop policy if exists work_request_writer_non_siep_insert on ops.work_request;
drop policy if exists work_request_writer_non_siep_update on ops.work_request;
create policy work_request_read_all on ops.work_request for select using (true);
create policy work_request_writer_non_siep_insert on ops.work_request for insert
  with check (program_key is distinct from 'carr-system-integrity-elimination-v1');
create policy work_request_writer_non_siep_update on ops.work_request for update
  using (program_key is distinct from 'carr-system-integrity-elimination-v1')
  with check (program_key is distinct from 'carr-system-integrity-elimination-v1');

revoke all on ops.siep_package_contract, ops.siep_program_dependency,
  ops.siep_component_alias, ops.siep_evidence_link, ops.siep_lane_lock,ops.siep_command_receipt,
  ops.siep_job_evidence_binding
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.siep_resolve_package(text),ops.siep_evidence_actor(text,uuid),
  ops.siep_current_evidence_digest(text,uuid),ops.siep_manifest_digest(),ops.siep_request_digest(jsonb),
  ops.siep_append_only_guard(),ops.siep_manifest_insert_guard(),
  ops.siep_program_identity_guard(),ops.siep_program_transition_guard(),
  ops.siep_joe_decision_event_guard(),ops.siep_current_approval(text,integer,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.siep_read_program(),ops.siep_claim_package(text,text,uuid,uuid),
  ops.siep_transition_package(text,integer,text,text,uuid,uuid),
  ops.siep_attach_evidence(text,text,uuid,text,text,text,uuid),
  ops.siep_bind_evidence_job(text,integer,text,uuid,uuid),
  ops.siep_record_joe_decision(text,text,text,uuid),
  ops.siep_acquire_lane_lock(text,text,integer,uuid),ops.siep_release_lane_lock(text,text,uuid,uuid),
  ops.siep_terminal_status() from public,carr_reader,carr_writer,carr_jobs,carr_authority;

grant execute on function ops.siep_read_program() to carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.siep_manifest_digest() to carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.siep_claim_package(text,text,uuid,uuid) to carr_writer;
grant execute on function ops.siep_transition_package(text,integer,text,text,uuid,uuid) to carr_writer;
grant execute on function ops.siep_attach_evidence(text,text,uuid,text,text,text,uuid) to carr_writer,carr_authority;
grant execute on function ops.siep_bind_evidence_job(text,integer,text,uuid,uuid) to carr_authority;
grant execute on function ops.siep_record_joe_decision(text,text,text,uuid) to carr_authority;
grant execute on function ops.siep_acquire_lane_lock(text,text,integer,uuid) to carr_writer;
grant execute on function ops.siep_release_lane_lock(text,text,uuid,uuid) to carr_writer;
grant execute on function ops.siep_terminal_status() to carr_reader,carr_writer,carr_jobs,carr_authority;

alter table ops.work_request validate constraint work_request_sourced_capture_shape;

do $$
declare package_count integer; alias_count integer; critical_count integer; cycle_found boolean;
begin
  select count(*) into package_count from ops.siep_package_contract;
  if package_count<>40 then raise exception '0324 FAILED: expected 40 first-class packages, got %',package_count; end if;
  select count(*) into alias_count from ops.siep_component_alias;
  if alias_count<>25 then raise exception '0324 FAILED: expected 25 component aliases, got %',alias_count; end if;
  if exists(select 1 from ops.siep_package_contract where package_key in ('06','24')) then
    raise exception '0324 FAILED: aggregate labels became package rows';
  end if;
  with recursive walk(root,node,path,cycle) as (
    select package_key,depends_on_package_key,array[package_key,depends_on_package_key],false
      from ops.siep_program_dependency
    union all
    select w.root,d.depends_on_package_key,w.path||d.depends_on_package_key,d.depends_on_package_key=any(w.path)
      from walk w join ops.siep_program_dependency d on d.package_key=w.node where not w.cycle
  ) select coalesce(bool_or(cycle),false) into cycle_found from walk;
  if cycle_found then raise exception '0324 FAILED: SIEP dependency graph contains a cycle'; end if;
  select count(*) into critical_count from ops.siep_program_dependency where (package_key,depends_on_package_key) in (
    ('03','02'),('03','12'),('03','15'),('03','17'),('03','20'),('04','03'),('04','11'),('04','18'),
    ('05','04'),('05','17'),('05','18'),('05','21'),('05','23'),('23','06A'),('23','12'),('23','17'),
    ('23','18'),('23','19'),('23','20'),('23','21'),('23','22'),('06B','06A'),('06B','04'),('06B','23'),
    ('24A','14'),('24A','16'),('24A','17'),('24A','18'),('24A','19'),('24A','20'),('24A','21'),
    ('24A','22'),('24A','23'),('25','24A'),('30','15'),('30','23'),('24B','24A'),('24B','30'),
    ('24B','31'),('24B','32'),('24B','33'),('24B','34'),('24B','35'),('24B','36'),('24B','37'),
    ('42','05'),('42','06B'),('42','24A'),('42','24B'),('42','25'),('42','26'),('42','37'),('42','41'),
    ('43','42'),('44','43')
  );
  if critical_count<>55 then raise exception '0324 FAILED: reviewed critical edge set incomplete: %',critical_count; end if;
  if has_table_privilege('carr_writer','ops.siep_evidence_link','insert')
     or has_table_privilege('carr_writer','ops.siep_lane_lock','update')
     or has_table_privilege('carr_writer','ops.siep_command_receipt','select')
     or has_function_privilege('public','ops.siep_claim_package(text,text,uuid,uuid)','execute') then
    raise exception '0324 FAILED: raw SIEP authority leaked';
  end if;
end $$;

comment on table ops.siep_package_contract is
  'Immutable SIEP package contract linked one-to-one to the sole lifecycle row in ops.work_request; it is not a task tracker.';
comment on table ops.siep_evidence_link is
  'Typed links into existing receipt, event, decision, and finding ledgers. Evidence bodies are never copied here.';
comment on column ops.work_request.organization_tenant_id is
  'Server-derived CARR tenant for sourced Program 6 plus the exact fixed AI and SIEP programs. Other historical rows remain null.';

commit;
