#!/usr/bin/env python3
# ci: db-gate
# doctrine: playbook-review
"""Behavioral regressions for the Dot database review, on disposable Postgres only."""
import hashlib
import json
import os
import re
import unittest
import uuid
import importlib.util
import sys
import concurrent.futures
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]

EXECUTION_ENVIRONMENT = "rehearsal"


def sha(character: str) -> str:
    return "sha256:" + character * 64


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def design_contract(slice_row: dict) -> dict:
    return {
        "contract_version": "engineering-design-contract.v1",
        "rationale": "The reproduction exercises the canonical ledgers end to end.",
        "dependency_rationale": "No accepted predecessor slice is required.",
        "code_model_decision": {
            "rationale": "Every step is enumerable ledger behavior, so deterministic code owns it.",
            "selection_basis": ["capability_gain", "quality_gain"],
            "model_judgment_steps": [],
        },
        "routing": {
            "executor_class": "deterministic_code",
            "adapter_ref": "adapter:codex-desktop",
            "fresh_session_required": True,
        },
        "authority": {
            "capability_profile": "capability:engineering-repository-write",
            "read_only": False,
            "environment": EXECUTION_ENVIRONMENT,
        },
        "isolation": {"worktree_required": True, "branch_required": True, "shared_resource_refs": []},
        "tests": {
            "planned_check_refs": [check["check_ref"] for check in slice_row["planned_checks"]],
            "verification_lanes": ["contract"],
        },
        "review": {"independent_review_required": True, "reviewer_class": "independent_agent"},
        "failure": {"failure_modes": [{
            "failure_ref": "failure:siep01-evidence-drift",
            "detection": "Canonical evidence does not match the observation.",
            "compensation": "Refuse closure and keep the Work Request unresolved.",
        }]},
        "evidence": {
            "redaction_class": "metadata_only",
            "retention": "material_redacted",
            "evidence_refs": [{"ref": "evidence:siep01:design", "redaction_class": "metadata_only",
                               "content_digest": sha("d")}],
        },
        "deployment": {"release_requirement": "not_required", "rollback_ref": None, "confirmation_required": False},
        "completion": {
            "completion_predicate": "Every planned check passes under independent review.",
            "verified_by": "independent_review",
        },
        "seam_decision": {
            "mode": "reuse",
            "target_seam_ref": "seam:engineering-passport",
            "measurement": {"basis": "complexity_reduction", "note": "The existing ledgers already carry this evidence."},
            "new_module_justification": None,
            "replaced_seam_refs": [],
            "residual_authority_refs": [],
        },
        "full_design_refs": None,
        "short_template": {
            "template_ref": "template:short-governed-v1",
            "objective_summary": "One bounded parallel-safe reproduction with no dependencies.",
            "verification_ref": "verification:short-governed-v1",
        },
    }


def typed_slice(slice_ref: str, ordinal: int) -> dict:
    row: dict = {
        "baseline_evidence_refs": [
            {
                "ref": f"evidence:siep01:{ordinal}",
                "content_digest": sha(str(ordinal)),
                "redaction_class": "metadata_only",
            }
        ],
        "concurrency_posture": "parallel_safe",
        "declared_component_refs": ["component:engineering-passport"],
        "declared_plan_step_refs": [f"step:siep01:{ordinal}"],
        "declared_resource_refs": [f"resource:canonical-ledgers:{ordinal}"],
        "definition_of_done": "The exact canonical execution evidence is read back.",
        "dependency_refs": [],
        "forbidden_change_refs": ["forbidden:production-mutation"],
        "manual_qa_required": False,
        "objective": "Reproduce one bounded execution history.",
        "ordinal": ordinal,
        "planned_checks": [
            {
                "check_ref": f"check:siep01:{ordinal}",
                "evidence_requirement": "metadata_only_sufficient",
                "failure_condition": "Canonical evidence does not match the observation.",
            }
        ],
        "release_requirement": "not_required",
        "risk_class": "R1",
        "scope_boundary": "Disposable source-test evidence only.",
        "slice_ref": slice_ref,
    }
    row["design_contract"] = design_contract(row)
    return row



class DatabaseDesign(unittest.TestCase):
    def setUp(self):
        dsn = os.environ.get('DOT_DATABASE_URL', '')
        if not dsn:
            self.skipTest('requires explicit disposable loopback DOT_DATABASE_URL')
        host=psycopg.conninfo.conninfo_to_dict(dsn).get('host')
        if host not in {'127.0.0.1','localhost','::1'}:
            raise RuntimeError('DOT_DATABASE_URL must name a disposable loopback database')
        self.dsn = dsn
        self.c = psycopg.connect(dsn)
        self.c.execute("set local carr.acting_actor_slug='joe'; set local carr.verified_human_actor_slug='joe'; set local carr.organization_tenant_id='carr-internal'")
        self.actor = self.one("select id from public.actor where slug='joe'")

    def tearDown(self):
        if hasattr(self, 'c') and not self.c.closed:
            self.c.rollback()
            self.c.close()

    def one(self, sql, args=()):
        return self.c.execute(sql, args).fetchone()[0]

    def insert(self, table, **fields):
        cols = ','.join(fields)
        placeholders = ','.join(['%s'] * len(fields))
        return self.one(f'insert into {table} ({cols}) values ({placeholders}) returning id', tuple(fields.values()))

    def seed(self, table, **fields):
        # Prerequisites only: do not fire unrelated policy-epoch hooks while
        # constructing synthetic rows. Every operation under test uses origin.
        self.c.execute('set local session_replication_role=replica')
        value = self.insert(table, **fields)
        self.c.execute('set local session_replication_role=origin')
        return value

    def refuses(self, sql, args=()):
        try:
            with self.c.transaction():
                self.c.execute(sql, args)
        except psycopg.Error as e:
            return e.sqlstate, str(e).splitlines()[0]
        return None

    def conversation(self):
        return self.seed('ops.doc_conversation', title='Synthetic test', created_by_actor=self.actor)

    def turn(self, conversation, sequence):
        return self.seed('ops.doc_conversation_turn', conversation_id=conversation,
                         sequence=sequence, role='human', body='Synthetic body', msg_id=uuid.uuid4(),
                         origin_actor='joe')

    def job(self, cognition=None):
        key = 'dot-' + uuid.uuid4().hex
        self.c.execute('set local session_replication_role=replica')
        self.c.execute("insert into ops.job_definition(key,version,risk,execution_kind,execution_contract,recurrence,retry_policy,deduplication,completion_contract) values(%s,1,'green',%s,%s,'{}','{}','{}','{}')",
                       (key, 'cognition' if cognition else 'deterministic', Jsonb({'cognition_job': cognition} if cognition else {'entrypoint': 'synthetic'})))
        token = uuid.uuid4()
        job = self.insert('ops.job', definition_key=key, definition_version=1, idempotency_key=str(uuid.uuid4()),
                          scheduled_for=self.one('select now()'), state='running', attempt=1, max_attempts=1,
                          timeout_seconds=60, lease_owner='synthetic', lease_token=token,
                          leased_until=self.one("select now()+interval '1 hour'"))
        self.insert('ops.job_attempt', job_id=job, attempt=1, lease_owner='synthetic', lease_token=token, state='running')
        self.c.execute('set local session_replication_role=origin')
        return job, token

    def cognition(self):
        key = 'dot-' + uuid.uuid4().hex
        self.c.execute('set local session_replication_role=replica')
        self.c.execute("insert into ops.cognition_job(key,version,input_schema_version,output_schema_version,input_schema,output_schema,max_tokens,max_cost_usd,timeout_seconds,provider_routes) values(%s,1,1,1,'{}','{}',1,1,60,array['synthetic'])", (key,))
        self.c.execute('set local session_replication_role=origin')
        return key

    def meeting(self):
        return self.seed('ops.meeting', id=uuid.uuid4(), organization_tenant_id='carr-internal',
                         source_system='synthetic', native_id=uuid.uuid4().hex, native_id_epoch='one',
                         title='Synthetic meeting', activation_intent='one_tap_user_activation', started_by_actor=self.actor)

    def business(self):
        party = self.seed('public.party', kind='person', name='Synthetic fixture', created_by=self.actor, updated_by=self.actor)
        client = self.seed('public.client', party_id=party, created_by=self.actor, updated_by=self.actor)
        deal = self.seed('public.deal', client_id=client, name='Synthetic fixture', deal_type='lease', phase='search', created_by=self.actor, updated_by=self.actor)
        return party, client, deal

    def test_01_merge_survivorship_query(self):
        source = (ROOT / 'mcp-server/src/tools.js').read_text()
        query = re.search(r'`(/\* merge_survivorship \*/.*?)`', source, re.S).group(1).replace('$1', '%s')
        party,client,_=self.business()
        self.seed('public.activity',occurred_at=self.one('select now()'),actor_id=self.actor,
                  kind='call',summary='Synthetic activity',client_id=client)
        row=self.c.execute(query, ([party],)).fetchone()
        self.assertEqual(row[-1],1)
        sweep=re.search(r'`(/\* merge_orphan_sweep \*/.*?)`',source,re.S).group(1)
        # psycopg uses one binding per occurrence; the production query uses $1.
        rows=self.c.execute(sweep.replace('$1','%s'),tuple([party]*sweep.count('$1'))).fetchall()
        self.assertEqual(dict(rows)['activity'],1)

    def test_02_private_conversation_has_no_direct_read_door(self):
        conv = self.conversation()
        self.turn(conv, 0)
        self.c.execute("set local carr.acting_actor_slug='dell'; set local carr.verified_human_actor_slug='dell'; set local role carr_writer")
        refusal = self.refuses('select body from ops.doc_conversation_turn where conversation_id=%s', (conv,))
        if refusal:
            self.assertEqual(refusal[0], '42501')
        else:
            self.assertEqual(self.c.execute('select body from ops.doc_conversation_turn where conversation_id=%s', (conv,)).fetchall(), [])

    def test_02b_private_facts_door_preserves_owner_and_grant_access(self):
        conv = self.conversation()
        self.turn(conv, 0)
        peer = self.one("select id from public.actor where slug='dell'")
        self.c.execute('set local role carr_writer')
        owner = self.one('select ops.doc_conversation_facts(%s,%s,0,200)', (conv, str(self.actor)))
        self.assertTrue(owner['ok'])
        self.assertEqual(owner['turns'][0]['body'], 'Synthetic body')
        denied = self.one('select ops.doc_conversation_facts(%s,%s,0,200)', (conv, str(peer)))
        self.assertFalse(denied['ok'])
        self.c.execute('reset role')
        self.c.execute('insert into ops.doc_conversation_grant(conversation_id,grantee_actor,granted_by_actor) values(%s,%s,%s)', (conv,peer,self.actor))
        self.c.execute('set local role carr_writer')
        self.assertTrue(self.one('select ops.doc_conversation_facts(%s,%s,0,200)', (conv, str(peer)))['ok'])

    def test_03_suggestion_requires_base_version(self):
        conv = self.conversation()
        self.turn(conv, 20)
        key = uuid.uuid4()
        self.one("select ops.suggest_doc_work(%s,20,'synthetic','Synthetic',null,'{\"a\":1}',%s)", (conv,key))
        self.c.execute('update ops.doc_suggestion set version=7 where id=%s', (key,))
        result = self.one("select ops.decide_doc_suggestion(%s,null,'dismiss',null,null,%s)", (key,uuid.uuid4()))
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.one('select version from ops.doc_suggestion where id=%s', (key,)), 7)

    def test_04_delayed_suggestion_does_not_replace_newer_facts(self):
        conv = self.conversation()
        self.turn(conv,20); self.turn(conv,10)
        key = uuid.uuid4()
        self.one("select ops.suggest_doc_work(%s,20,'synthetic','New',null,'{\"a\":2}',%s)", (conv,key))
        self.c.execute("update ops.doc_suggestion set disposition='dismissed' where id=%s", (key,))
        result = self.one("select ops.suggest_doc_work(%s,10,'synthetic','Old',null,'{\"a\":1}',%s)", (conv,uuid.uuid4()))
        row = self.c.execute('select source_sequence,material_facts,disposition from ops.doc_suggestion where id=%s', (key,)).fetchone()
        self.assertEqual(row, (20, {'a':2}, 'dismissed'), (result,row))

    def test_05_allocation_parent_is_same_commission_and_acyclic(self):
        _, _, deal = self.business()
        c1=self.seed('public.commission',deal_id=deal,gross_amount=100,status='expected',created_by=self.actor)
        c2=self.seed('public.commission',deal_id=deal,gross_amount=100,status='expected',created_by=self.actor)
        a=self.insert('public.commission_allocation',commission_id=c1,actor_id=self.actor,kind='house',fraction=1)
        cross=self.refuses("insert into public.commission_allocation(commission_id,parent_id,actor_id,kind,fraction) values(%s,%s,%s,'house',1)", (c2,a,self.actor))
        self.assertIsNotNone(cross, 'cross-commission parent accepted')
        self.assertIsNotNone(self.refuses('update public.commission_allocation set parent_id=id where id=%s',(a,)), 'self cycle accepted')

    def test_05b_allocation_self_cycle_is_refused(self):
        _,_,deal=self.business()
        commission=self.seed('public.commission',deal_id=deal,gross_amount=100,status='expected',created_by=self.actor)
        a=self.insert('public.commission_allocation',commission_id=commission,actor_id=self.actor,kind='house',fraction=1)
        self.assertIsNotNone(self.refuses('update public.commission_allocation set parent_id=id where id=%s',(a,)))

    def test_05c_allocation_long_cycle_is_refused_as_writer(self):
        _,_,deal=self.business()
        commission=self.seed('public.commission',deal_id=deal,gross_amount=100,status='expected',created_by=self.actor)
        a=self.insert('public.commission_allocation',commission_id=commission,actor_id=self.actor,kind='house',fraction=1)
        b=self.insert('public.commission_allocation',commission_id=commission,parent_id=a,actor_id=self.actor,kind='house',fraction=1)
        c=self.insert('public.commission_allocation',commission_id=commission,parent_id=b,actor_id=self.actor,kind='house',fraction=1)
        self.c.execute('set local role carr_writer')
        self.assertIsNotNone(self.refuses('update public.commission_allocation set parent_id=%s where id=%s',(c,a)))

    def helper(self, filename):
        sys.path.insert(0, str(ROOT / 'ops'))
        spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), ROOT / 'ops' / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def accepted_plan(self):
        p6 = self.helper('program6-ready-plan-gate.py')
        cur = self.c.cursor()
        p6.ensure_authority_roles(cur)
        source, revision, origin, _, _, runbook = p6.doctrine_fixture(cur, self.actor)
        cur.execute('set local role carr_writer')
        captured = p6.capture(cur, source, revision, origin, 'Synthetic Dot regression')
        cur.execute('reset role')
        triaged = p6.triage(cur, captured[1], captured[3], 'joe')
        cur.execute('set local role carr_writer')
        proposed = p6.propose(cur, captured[1], triaged[3], runbook, uuid.uuid4())
        cur.execute('reset role')
        p6.accept(cur, captured[1], triaged[3], proposed[2], uuid.uuid4(), 'joe')
        return captured[1], proposed

    def test_06_reader_cannot_register_engineering_plan(self):
        ref, _ = self.accepted_plan()
        source = self.one('select ops.engineering_admission_source(%s)', (ref,))
        plan = {
            'schema_version': 'engineering-slice-plan.v2',
            'accepted_plan_revision': {'id': source['accepted_plan']['plan_ref'], 'revision': source['accepted_plan']['revision'], 'digest': source['accepted_plan']['digest']},
            'work_request': {'id': source['work_request']['id'], 'state_version': source['work_request']['version'], 'canonical_record_digest': source['work_request']['canonical_record_digest']},
            'slices': [typed_slice('slice:dot:reader', 1)],
        }
        digest = canonical_digest(plan)
        plan['plan_digest'] = digest
        self.c.execute('set local role carr_reader')
        refusal = self.refuses('select ops.engineering_register_slice_plan(%s,%s,%s,%s)', (ref, Jsonb(plan), digest, uuid.uuid4()))
        self.assertIsNotNone(refusal, 'reader registered a valid v2 engineering plan')
        self.assertEqual(refusal[0], '42501')
        self.c.execute('set local role carr_writer')
        self.assertIsNotNone(self.one('select (ops.engineering_register_slice_plan(%s,%s,%s,%s)).id', (ref, Jsonb(plan), digest, uuid.uuid4())))

    def test_07_reader_cannot_issue_execution_envelope(self):
        ref, proposed = self.accepted_plan()
        self.c.execute('set local role carr_writer')
        bundle = self.one('select ops.compile_context_bundle(%s,%s,%s)', (ref, proposed[1], 'carr-internal'))
        binding = self.c.execute('select * from ops.activate_context_bundle(%s,%s,%s,%s)', (ref, proposed[1], Jsonb(bundle), uuid.uuid4())).fetchone()[0]
        self.c.execute('reset role')
        self.c.execute("insert into public.agent_profile(profile_key,display_name,charter,status,current_model,current_desk) values('builder','Builder','[]','active','synthetic','synthetic') on conflict(profile_key) do update set status='active',current_model='synthetic',current_desk='synthetic'")
        self.c.execute('set session authorization carr_authority_joe')
        self.c.execute('select * from ops.assign_execution_profile(%s,%s,%s,%s,%s,%s)', (ref, 'builder', 'rehearsal', 'policy:execution-lane-v1', 'sha256:'+'c'*64, uuid.uuid4()))
        self.c.execute('reset session authorization; set local role carr_reader')
        refusal = self.refuses('select * from ops.issue_execution_envelope_v1(%s,%s,%s)', (ref, binding, uuid.uuid4()))
        self.assertIsNotNone(refusal, 'reader issued a bound execution envelope')
        self.assertEqual(refusal[0], '42501')
        self.c.execute('set local role carr_writer')
        self.assertIsNotNone(self.c.execute('select * from ops.issue_execution_envelope_v1(%s,%s,%s)', (ref, binding, uuid.uuid4())).fetchone()[0])

    def test_10_completion_requires_lease_token(self):
        job,_ = self.job()
        self.c.execute('set local role carr_jobs')
        refusal=self.refuses("select ops.complete_job(%s,null,'{}','synthetic')",(job,))
        self.assertIsNotNone(refusal, 'NULL lease completed job while attempt stayed running')

    def test_12_cache_collision_preserves_matching_metadata(self):
        a,b = self.cognition(),self.cognition()
        ja,ta=self.job(a); jb,tb=self.job(b)
        key=uuid.uuid4().hex
        self.one("select ops.put_cognition_cache_for_job(%s,%s,%s,%s,1,1,'{\"contract\":\"A\"}','{}',60)",(ja,ta,key,a))
        self.one("select ops.put_cognition_cache_for_job(%s,%s,%s,%s,1,1,'{\"contract\":\"B\"}','{}',60)",(jb,tb,key,b))
        row=self.c.execute('select cognition_key,proposal from ops.cognition_result_cache where cache_key=%s',(key,)).fetchone()
        self.assertEqual(row, (b,{'contract':'B'}), row)

    def test_13_month_boundary_counts_settled_reservations(self):
        j,t=self.job(); j2,t2=self.job(); route=uuid.uuid4().hex
        self.c.execute("insert into ops.provider_route(route_key,priority,endpoint_ref,monthly_budget_usd) values(%s,(select coalesce(max(priority),0)+1 from ops.provider_route),'synthetic',10)",(route,))
        self.c.execute("update ops.job_attempt set started_at=date_trunc('month',now())-interval '1 second' where job_id=%s",(j,))
        rid=self.one('select ops.reserve_job_cost(%s,%s,%s,6)',(j,t,route))
        self.one('select ops.settle_job_cost(%s,%s,%s,1,1,6)',(rid,j,t))
        self.assertIsNotNone(self.refuses('select ops.reserve_job_cost(%s,%s,%s,6)',(j2,t2,route)), 'second $6 admitted after $6 settled this month')

    def test_14_invalidation_counts_changed_cache_rows(self):
        a=self.cognition();j,t=self.job(a); key=uuid.uuid4().hex; dep=uuid.uuid4().hex
        for attempt in range(2):
            self.one("select ops.put_cognition_cache_for_job(%s,%s,%s,%s,1,1,'{}',array[%s],60)",(j,t,key,a,dep))
            n=self.one('select ops.invalidate_cognition_cache_for_job(%s,%s,%s)',(j,t,dep))
            self.assertEqual(n,1, f'invalidation {attempt+1} changed row but returned {n}')

    def test_15_cadence_does_not_read_temporary_actor(self):
        before=self.one("select ops.v5_a05_assurance_cadence_batch('joe')")
        self.c.execute('create temp table actor(id uuid,slug text,kind text,active boolean); grant select on actor to public; set local role carr_reader')
        self.assertEqual(self.one("select ops.v5_a05_assurance_cadence_batch('joe')"), before)

    def test_16_participant_side_does_not_read_temporary_role(self):
        party,_,deal=self.business()
        from psycopg import sql
        self.c.execute(sql.SQL('grant temporary on database {} to carr_writer').format(sql.Identifier(self.c.info.dbname)))
        self.c.execute("set local role carr_writer; create temp table participant_role(slug text,side text); insert into participant_role values('lead',null)")
        self.assertIsNotNone(self.refuses("insert into public.deal_participant(deal_id,party_id,role,set_by) values(%s,%s,'lead',%s)",(deal,party,self.actor)), 'actor-only lead role accepted a party')

    def test_17_note_replay_rejects_changed_revision_target(self):
        m=self.meeting();k=uuid.uuid4()
        self.one("select ops.add_meeting_note(%s,'X',null,null,'synthetic',%s)",(m,k))
        self.one("select ops.add_meeting_note(%s,'Other',null,null,'synthetic',%s)",(m,uuid.uuid4()))
        result=self.one("select ops.add_meeting_note(%s,'X',2,1,'synthetic',%s)",(m,k))
        self.assertFalse(result['ok'], result)

    def test_18_action_replay_rejects_changed_action(self):
        m=self.meeting();k=uuid.uuid4()
        self.c.execute('set local session_replication_role=replica')
        for n in [1,2]:
            self.c.execute("insert into ops.meeting_action(meeting_id,action_number,state,current_revision) values(%s,%s,'proposed',1)",(m,n))
            self.insert('ops.meeting_action_revision',id=uuid.uuid4(),meeting_id=m,action_number=n,revision=1,
                        summary='Synthetic action',basis='tentative_discussion',source='participant',
                        proposed_by_actor=self.actor,proposed_by_instance='synthetic')
        self.c.execute('set local session_replication_role=origin')
        self.one("select ops.decide_meeting_action(%s,1,'decline',1,null,null,'synthetic',%s)",(m,k))
        result=self.one("select ops.decide_meeting_action(%s,2,'decline',1,null,null,'synthetic',%s)",(m,k))
        self.assertFalse(result['ok'], result)

    def test_20_subject_timeline_uses_index(self):
        _,client,_=self.business()
        self.c.execute('set local session_replication_role=replica')
        self.c.execute("insert into public.activity(occurred_at,actor_id,kind,summary,client_id) select now(),%s,'call','Synthetic',case when n<=25 then %s else gen_random_uuid() end from generate_series(1,1000000) n",(self.actor,client))
        self.c.execute('set local session_replication_role=origin; analyze public.activity')
        rows=self.c.execute("explain (analyze,format json) select * from public.v_subject_timeline where subject_type='client' and subject_id=%s order by occurred_at desc limit 20",(client,)).fetchone()[0]
        def nodes(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from nodes(child)
        scans = [n for n in nodes(rows) if n.get('Relation Name') == 'activity' and n.get('Node Type') == 'Seq Scan']
        self.assertEqual(scans, [], [{k:n.get(k) for k in ['Node Type','Relation Name','Rows Removed by Filter','Actual Rows']} for n in scans])

    def isolated_race_database(self):
        dsn=self.dsn
        self.c.rollback(); self.c.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            name='dot_race_'+uuid.uuid4().hex
            from psycopg import sql
            admin.execute(sql.SQL('create database {} template {}').format(sql.Identifier(name),sql.Identifier(admin.info.dbname)))
        self.race_dsn=psycopg.conninfo.make_conninfo(dsn,dbname=name)
        self.c=psycopg.connect(self.race_dsn)
        self.c.execute("set carr.acting_actor_slug='joe'; set carr.verified_human_actor_slug='joe'; set carr.organization_tenant_id='carr-internal'")

    def overlap(self, first_sql, first_args, second_sql, second_args, *, isolation=None, census=None):
        self.c.commit()
        other=psycopg.connect(self.race_dsn)
        other.execute("set carr.acting_actor_slug='joe'; set carr.verified_human_actor_slug='joe'; set carr.organization_tenant_id='carr-internal'")
        other.commit()
        if isolation is not None:
            self.c.isolation_level = isolation
            other.isolation_level = isolation
            # Both snapshots must exist BEFORE either competing mutation.
            self.c.execute(census).fetchone()
            other.execute(census).fetchone()
        try:
            first=self.one(first_sql, first_args)
        except psycopg.Error as e:
            self.c.rollback()
            first={'refused':e.sqlstate,'error':str(e).splitlines()[0]}
        def run():
            try:
                result=other.execute(second_sql,second_args).fetchone()[0]
                other.commit()
                return result
            except psycopg.Error as e:
                other.rollback()
                return {'refused':e.sqlstate,'error':str(e).splitlines()[0]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            # Wait for deterministic lock observation or completed mutation,
            # then release the first transaction. The second cannot see it yet.
            deadline=time.monotonic()+5
            with psycopg.connect(self.race_dsn,autocommit=True) as monitor:
                while not future.done() and time.monotonic()<deadline:
                    if monitor.execute("select wait_event_type='Lock' from pg_stat_activity where pid=%s",(other.info.backend_pid,)).fetchone()[0]:
                        break
                    time.sleep(.01)
            self.c.commit()
            second=future.result(timeout=5)
        other.close()
        self.c.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        return first,second

    def test_05d_allocation_competing_parents_preserve_tree(self):
        for isolation in (psycopg.IsolationLevel.READ_COMMITTED,
                          psycopg.IsolationLevel.REPEATABLE_READ,
                          psycopg.IsolationLevel.SERIALIZABLE):
            with self.subTest(isolation=isolation):
                self.isolated_race_database()
                _,_,deal=self.business()
                commission=self.seed('public.commission',deal_id=deal,gross_amount=100,status='expected',created_by=self.actor)
                a=self.insert('public.commission_allocation',commission_id=commission,actor_id=self.actor,kind='house',fraction=1)
                b=self.insert('public.commission_allocation',commission_id=commission,actor_id=self.actor,kind='house',fraction=1)
                first,second=self.overlap(
                    'update public.commission_allocation set parent_id=%s where id=%s returning id',(b,a),
                    'update public.commission_allocation set parent_id=%s where id=%s returning id',(a,b),
                    isolation=isolation,census='select count(*) from public.commission_allocation')
                cycles=self.one('select count(*) from public.commission_allocation a join public.commission_allocation b on a.parent_id=b.id and b.parent_id=a.id')
                self.assertEqual(cycles,0,(first,second))
                self.assertIsInstance(second,dict,(first,second))
                self.assertIn(second['refused'],('23514','40001','25001'))
                if isolation == psycopg.IsolationLevel.READ_COMMITTED:
                    self.assertNotIsInstance(first,dict)
                else:
                    self.assertEqual([result['refused'] for result in (first,second)],['25001','25001'])

    def test_09_wip_limit_serializes_competing_claims(self):
        self.isolated_race_database()
        self.c.execute('set session_replication_role=replica')
        ids=[]
        for n in [1,2]:
            ids.append(self.insert('ops.work_request',ref='WR-'+uuid.uuid4().hex[:10],title='Synthetic claim',requester_actor='joe',executor_actor='synthetic',shape_disposition='not_required',shape_fixed_surface_ref='synthetic',shape_rationale='Synthetic fixture',shape_decided_by_actor_id=self.actor,shape_decided_at=self.one('select now()')))
        self.c.execute('set session_replication_role=origin')
        first,second=self.overlap("update ops.work_request set state='claimed' where id=%s returning state",(ids[0],),"update ops.work_request set state='claimed' where id=%s returning state",(ids[1],))
        self.assertIsInstance(second,dict,(first,second))
        self.assertEqual(self.one("select count(*) from ops.work_request where state='claimed'"),1)

    def test_09b_wip_competing_fixed_snapshots_preserve_both_limits(self):
        for isolation in (psycopg.IsolationLevel.REPEATABLE_READ,
                          psycopg.IsolationLevel.SERIALIZABLE):
            for limit in ('executor','system'):
                with self.subTest(isolation=isolation,limit=limit):
                    self.isolated_race_database()
                    self.c.execute('set session_replication_role=replica')
                    ids=[]
                    executors=['synthetic','synthetic'] if limit=='executor' else ['synthetic-a','synthetic-b','synthetic-c']
                    for executor in executors:
                        ids.append(self.insert('ops.work_request',ref='WR-'+uuid.uuid4().hex[:10],title='Synthetic fixed-snapshot claim',requester_actor='joe',executor_actor=executor,shape_disposition='not_required',shape_fixed_surface_ref='synthetic',shape_rationale='Synthetic fixture',shape_decided_by_actor_id=self.actor,shape_decided_at=self.one('select now()')))
                    if limit=='system':
                        self.c.execute("update ops.work_request set state='claimed' where id=%s",(ids.pop(),))
                    self.c.execute('set session_replication_role=origin')
                    first,second=self.overlap(
                        "update ops.work_request set state='claimed' where id=%s returning state",(ids[0],),
                        "update ops.work_request set state='claimed' where id=%s returning state",(ids[1],),
                        isolation=isolation,census='select count(*) from ops.work_request')
                    count=self.one("select count(*) from ops.work_request where state in ('claimed','in_progress')")
                    self.assertLessEqual(count,1 if limit=='executor' else 2,(first,second))
                    self.assertIsInstance(second,dict,(first,second))
                    self.assertIn(second['refused'],('23514','40001','25001'))
                    self.assertEqual([result['refused'] for result in (first,second)],['25001','25001'])

    def test_11_budget_serializes_competing_reservations(self):
        self.isolated_race_database()
        a,ta=self.job();b,tb=self.job();route=uuid.uuid4().hex
        self.c.execute("insert into ops.provider_route(route_key,priority,endpoint_ref,monthly_budget_usd) values(%s,(select coalesce(max(priority),0)+1 from ops.provider_route),'synthetic',10)",(route,))
        first,second=self.overlap('select ops.reserve_job_cost(%s,%s,%s,6)',(a,ta,route),'select ops.reserve_job_cost(%s,%s,%s,6)',(b,tb,route))
        self.assertIsInstance(second,dict,(first,second))
        self.assertEqual(self.one('select sum(estimated_cost_usd) from ops.cost_reservation where route_key=%s',(route,)),6)

    def test_11b_budget_competing_fixed_snapshots_preserve_limit(self):
        for isolation in (psycopg.IsolationLevel.REPEATABLE_READ,
                          psycopg.IsolationLevel.SERIALIZABLE):
            with self.subTest(isolation=isolation):
                self.isolated_race_database()
                a,ta=self.job();b,tb=self.job();route=uuid.uuid4().hex
                self.c.execute("insert into ops.provider_route(route_key,priority,endpoint_ref,monthly_budget_usd) values(%s,(select coalesce(max(priority),0)+1 from ops.provider_route),'synthetic',10)",(route,))
                first,second=self.overlap(
                    'select ops.reserve_job_cost(%s,%s,%s,6)',(a,ta,route),
                    'select ops.reserve_job_cost(%s,%s,%s,6)',(b,tb,route),
                    isolation=isolation,census='select count(*) from ops.cost_reservation')
                reserved=self.one('select coalesce(sum(estimated_cost_usd),0) from ops.cost_reservation where route_key=%s',(route,))
                self.assertLessEqual(reserved,10,(first,second))
                self.assertIsInstance(second,dict,(first,second))
                self.assertIn(second['refused'],('40001','25001'))
                self.assertEqual([result['refused'] for result in (first,second)],['25001','25001'])

    def test_19_refused_conversation_rename_leaves_no_revision(self):
        self.isolated_race_database()
        conv=self.conversation()
        first,second=self.overlap('select ops.rename_doc_conversation(%s,1,%s,null,null,%s)',(conv,'First',uuid.uuid4()),'select ops.rename_doc_conversation(%s,1,%s,null,null,%s)',(conv,'Second',uuid.uuid4()))
        self.assertTrue(first['ok'],first)
        self.assertFalse(second['ok'],second)
        self.assertEqual(self.one('select count(*) from ops.doc_conversation_title_revision where conversation_id=%s',(conv,)),1)

if __name__ == '__main__':
    os.environ['DOT_DATABASE_URL'] = os.environ.get('DOT_DATABASE_URL') or os.environ.get('DATABASE_URL', '')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseDesign))
    if result.skipped:
        raise SystemExit('Database design checks require a disposable loopback database')
    raise SystemExit(0 if result.wasSuccessful() else 1)
