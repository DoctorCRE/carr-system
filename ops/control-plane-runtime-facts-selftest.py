#!/usr/bin/env python3
"""Hermetic coverage for runtime-derived (not payload-asserted) fact paths."""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(REPO),str(REPO/'ops')]
from lib.control_plane_facts import evaluate_stage
from lib.loadpy import load_module_from_path

cp=load_module_from_path('control_plane_runtime_under_test',str(REPO/'tools'/'control-plane.py'))
manifest=json.loads((REPO/'ops/config/control-plane-workflows.v1.json').read_text())
by={w['key']:w for w in manifest['workflows']}; payload={'scheduled_for':'2026-08-17T13:00:00+00:00'} # Sunday 08:00 Chicago
passed=0; failed: list[str]=[]
def check(label: str, value: bool) -> None:
 global passed
 if value: passed+=1
 else: failed.append(label)

# The four deterministic routes derive their preconditions from the scheduled
# instant/registered contract, never payload.fact_evidence.
for key in ('calendar-fetch-daily','nightly-record-layer','notes-sweep-hourly','restore-rehearse-weekly'):
 collector=cp._workflow_fact_collector(by[key],payload,mode='shadow')
 try:
  built=collector.collect(fact=by[key]['routing']['spec']['all_of'][0],workflow_key=key,stage='routing')
  check(key+' runtime routing emits a typed envelope',len(tuple(built))==1)
 except Exception: check(key+' runtime routing emits a typed envelope',False)

weekday_payload={'scheduled_for':'2026-08-17T13:00:00+00:00'} # Monday
calendar=cp._workflow_fact_collector(by['calendar-fetch-daily'],weekday_payload,mode='shadow')
check('calendar shadow derives weekday+registered EventKit bundle routing without payload facts',evaluate_stage(by['calendar-fetch-daily'],'routing',calendar))
notes=cp._workflow_fact_collector(by['notes-sweep-hourly'],weekday_payload,execution={'entrypoint':'bin/notes-sweep-post.sh','mode':'shadow','args':['--dry-run'],'exit_code':0,'stdout_tail':'notes-sweep shadow: scanned=1 unposted=1 writes=0 posts=0'},mode='shadow')
check('notes filtering derives from subprocess evidence',evaluate_stage(by['notes-sweep-hourly'],'filtering',notes))
# Priority 7 with no 1..6 ahead of it: a real queue slice is contiguous from 1,
# so this is precisely the unproved ordering filtering must still refuse. It used
# to be expressed as 'fewer than 40 rows', which stopped meaning that once a short
# week became valid work (0387).
input_payload={'subjects':[{'reverification_due':'expired','current_verification_status':'not_current','priority':7,'expired_at':'2026-01-01T00:00:00Z'}],'source_policy':{}}
cognition=cp._workflow_fact_collector(by['contact-enrichment-weekly'],weekday_payload,input_payload=input_payload,mode='shadow')
check('cognition routing derives queue existence but not unproved ordering',evaluate_stage(by['contact-enrichment-weekly'],'routing',cognition) and not evaluate_stage(by['contact-enrichment-weekly'],'filtering',cognition))
completion=cp._workflow_fact_collector(by['calendar-fetch-daily'],weekday_payload,execution={'exit_code':0,'stdout_tail':'summary failed=0'},receipt_ref='job:fixture:attempt:1',mode='shadow')
check('receipt existence alone cannot satisfy a named completion fact',not evaluate_stage(by['calendar-fetch-daily'],'completion',completion))
proposal_receipt={'proposal':{'findings':[]},'cognition':{'key':'audit.proposal','version':1,'output_schema_version':1,'canonical_write_authority':False}}
proposal_completion=cp._workflow_fact_collector(by['cc-update-audit'],weekday_payload,execution={'proposal':{'findings':[]}},receipt_ref='job:fixture:attempt:1',receipt_evidence=proposal_receipt,mode='shadow')
check('cognition completion requires and accepts typed immutable proposal receipt',evaluate_stage(by['cc-update-audit'],'completion',proposal_completion))
fuel_input={'lanes':[{'lane':'local-healthcare','temperature':'local'},
                     {'lane':'rotating-cold-lane','temperature':'cold'}],
            'freshness_cutoff':'2026-08-17T00:00:00Z','previous_receipt_state':'absent'}
fuel=cp._workflow_fact_collector(by['content-fuel-harvest-weekly'],weekday_payload,
    input_payload=fuel_input,mode='shadow')
check('content-fuel pre-provider filtering proves only the deterministic rotation',
      evaluate_stage(by['content-fuel-harvest-weekly'],'filtering',fuel))
fuel_proposal={'candidates':[{'source_ref':'source:market-release:1','source_class':'primary',
    'citation_refs':['source:market-release:1#fact-1'],'decision':'retain','current':True,
    'action':'propose'}],'lane_health':[]}
fuel_valid=cp._workflow_fact_collector(by['content-fuel-harvest-weekly'],weekday_payload,
    input_payload=fuel_input,execution={'proposal':fuel_proposal},mode='shadow')
check('content-fuel post-provider contract accepts cited current primary proposals',
      evaluate_stage(by['content-fuel-harvest-weekly'],'validation',fuel_valid))
fuel_secondary={**fuel_proposal,'candidates':[{**fuel_proposal['candidates'][0],
    'source_class':'secondary'}]}
fuel_invalid=cp._workflow_fact_collector(by['content-fuel-harvest-weekly'],weekday_payload,
    input_payload=fuel_input,execution={'proposal':fuel_secondary},mode='shadow')
check('content-fuel post-provider contract refuses retained secondary sources',
      not evaluate_stage(by['content-fuel-harvest-weekly'],'validation',fuel_invalid))
proposal=cp._workflow_fact_collector(by['cc-update-audit'],weekday_payload,execution={'proposal':{'findings':[]}},mode='shadow')
check('typed nonempty proposal cannot claim release source validation',not evaluate_stage(by['cc-update-audit'],'validation',proposal))
npi_input={'npi_candidates':[{'npi':'1234567890','source_ref':'nppes:weekly:1'}]}
npi_good={'candidates':[{'npi':'1234567890','source_row_ref':'nppes:weekly:1','action':'propose'}]}
npi=cp._workflow_fact_collector(by['npi-sweep-weekly'],weekday_payload,input_payload=npi_input,
    execution={'proposal':npi_good},mode='shadow')
check('NPI proposal reconciles nonempty NPI/source pairs to deterministic input',
      evaluate_stage(by['npi-sweep-weekly'],'validation',npi))
npi_forged=cp._workflow_fact_collector(by['npi-sweep-weekly'],weekday_payload,input_payload=npi_input,
    execution={'proposal':{'candidates':[{'npi':'1234567890','source_row_ref':'invented','action':'propose','territory_match':True}]}},mode='shadow')
check('NPI provider territory assertion cannot replace exact input reconciliation',
      not evaluate_stage(by['npi-sweep-weekly'],'validation',npi_forged))
npi_empty=cp._workflow_fact_collector(by['npi-sweep-weekly'],weekday_payload,input_payload=npi_input,
    execution={'proposal':{'candidates':[]}},mode='shadow')
check('NPI empty proposal cannot satisfy source or dedup validation vacuously',
      not evaluate_stage(by['npi-sweep-weekly'],'validation',npi_empty))
npi_nonproposal=cp._workflow_fact_collector(by['npi-sweep-weekly'],weekday_payload,input_payload=npi_input,
    execution={'proposal':{'candidates':[{'npi':'1234567890','source_row_ref':'nppes:weekly:1','action':'accept'}]}},mode='shadow')
check('NPI candidate is proposal-only even when its deterministic input pair matches',
      not evaluate_stage(by['npi-sweep-weekly'],'validation',npi_nonproposal))
npi_duplicate=cp._workflow_fact_collector(by['npi-sweep-weekly'],weekday_payload,input_payload=npi_input,
    execution={'proposal':{'candidates':[
        {'npi':'1234567890','source_row_ref':'nppes:weekly:1','action':'propose'},
        {'npi':'1234567890','source_row_ref':'nppes:weekly:1','action':'propose'},
    ]}},mode='shadow')
check('NPI candidate dedup is deterministic and rejects repeated exact input pairs',
      not evaluate_stage(by['npi-sweep-weekly'],'validation',npi_duplicate))

capability_missing_mutation=cp._workflow_fact_collector(by['ai-capability-builder'],weekday_payload,
    input_payload={'facts':{}},mode='shadow')
check('capability admission refuses an omitted requested_mutation field',
      not evaluate_stage(by['ai-capability-builder'],'routing',capability_missing_mutation))
release_timezone=cp._workflow_fact_collector(by['cc-update-audit'],weekday_payload,
    input_payload={'facts':{'release':{'from':'1.0','to':'1.1',
        'released_at':'2026-08-17T01:31:00+00:00',
        'last_accepted_at':'2026-08-16T20:30:00-05:00'}}},mode='shadow')
check('release recency compares parsed UTC instants rather than timestamp strings',
      evaluate_stage(by['cc-update-audit'],'filtering',release_timezone))
release_naive=cp._workflow_fact_collector(by['cc-update-audit'],weekday_payload,
    input_payload={'facts':{'release':{'from':'1.0','to':'1.1',
        'released_at':'2026-08-17T01:00:00',
        'last_accepted_at':'2026-08-16T20:30:00Z'}}},mode='shadow')
check('release recency refuses timestamp without an explicit timezone',
      not evaluate_stage(by['cc-update-audit'],'filtering',release_naive))
metrics_empty=cp._workflow_fact_collector(by['social-metrics-pull-weekly'],weekday_payload,
    execution={'proposal':{'measurements':[]}},mode='shadow')
check('metrics proposal refuses an empty measurement list',
      not evaluate_stage(by['social-metrics-pull-weekly'],'validation',metrics_empty))
linkedin_posts=lambda count: [{'url':f'https://linkedin.example/{n}'} for n in range(count)]
linkedin_two=cp._workflow_fact_collector(by['linkedin-engagement-daily'],weekday_payload,
    input_payload={'source_posts':linkedin_posts(2)},mode='shadow')
linkedin_three=cp._workflow_fact_collector(by['linkedin-engagement-daily'],weekday_payload,
    input_payload={'source_posts':linkedin_posts(3)},mode='shadow')
linkedin_six=cp._workflow_fact_collector(by['linkedin-engagement-daily'],weekday_payload,
    input_payload={'source_posts':linkedin_posts(6)},mode='shadow')
check('LinkedIn selection refuses fewer than the declared three posts',
      next(iter(linkedin_two.collect(fact='linkedin.post_count_in_range',workflow_key='linkedin-engagement-daily',stage='filtering')))['value'] is False)
check('LinkedIn selection accepts the declared lower bound of three posts',
      next(iter(linkedin_three.collect(fact='linkedin.post_count_in_range',workflow_key='linkedin-engagement-daily',stage='filtering')))['value'] is True)
check('LinkedIn selection refuses more than the declared five posts',
      next(iter(linkedin_six.collect(fact='linkedin.post_count_in_range',workflow_key='linkedin-engagement-daily',stage='filtering')))['value'] is False)
x_drafts=lambda count: [{'action':'draft'} for _ in range(count)]
x_four=cp._workflow_fact_collector(by['x-reply-run-daily'],weekday_payload,
    execution={'proposal':{'drafts':x_drafts(4)}},mode='shadow')
x_five=cp._workflow_fact_collector(by['x-reply-run-daily'],weekday_payload,
    execution={'proposal':{'drafts':x_drafts(5)}},mode='shadow')
x_eleven=cp._workflow_fact_collector(by['x-reply-run-daily'],weekday_payload,
    execution={'proposal':{'drafts':x_drafts(11)}},mode='shadow')
check('X draft validation refuses fewer than the declared five drafts',
      next(iter(x_four.collect(fact='x.draft_count_in_range',workflow_key='x-reply-run-daily',stage='validation')))['value'] is False)
check('X draft validation accepts the declared lower bound of five drafts',
      next(iter(x_five.collect(fact='x.draft_count_in_range',workflow_key='x-reply-run-daily',stage='validation')))['value'] is True)
check('X draft validation refuses more than the declared ten drafts',
      next(iter(x_eleven.collect(fact='x.draft_count_in_range',workflow_key='x-reply-run-daily',stage='validation')))['value'] is False)

health=cp._workflow_fact_collector(by['health-audit-monthly'],weekday_payload,
    input_payload={'facts':{'monthly_receipt_state':'absent'}},mode='shadow')
check('health monthly absence proves both admission stages from one immutable ledger condition',
      evaluate_stage(by['health-audit-monthly'],'routing',health)
      and next(iter(health.collect(fact='health.one_run_in_monthly_window',
                                  workflow_key='health-audit-monthly',stage='filtering')))['value'] is True)
playbook=cp._workflow_fact_collector(by['playbook-review-monthly'],weekday_payload,
    input_payload={'facts':{'monthly_receipt_state':'absent','sweep_receipt_state':'present'}},mode='shadow')
check('playbook own absence and prerequisite sweep receipt are independent facts',
      evaluate_stage(by['playbook-review-monthly'],'routing',playbook))
playbook_missing_sweep=cp._workflow_fact_collector(by['playbook-review-monthly'],weekday_payload,
    input_payload={'facts':{'monthly_receipt_state':'absent','sweep_receipt_state':'absent'}},mode='shadow')
check('playbook refuses when the prerequisite sweep receipt is absent',
      not evaluate_stage(by['playbook-review-monthly'],'routing',playbook_missing_sweep))

# Deterministic jobs use an exact registered command identity, exit status, and
# workflow marker.  Their immutable receipt must then contain the same evidence
# verbatim; neither a receipt nor a successful process by itself is enough.
command_fixtures={
 'calendar-fetch-daily':{'entrypoint':'bin/calendar-eventkit-capture.sh','mode':'shadow','args':['--dry-run','--receipt-safe','--days','7'],'exit_code':0,'stdout_tail':'calendar-capture: source=eventkit mode=shadow scanned=81 exact=6 domain=2 unknown=3 writes=0 failed=0'},
 'nightly-record-layer':{'entrypoint':'bin/nightly.sh','mode':'shadow','args':['--preflight'],'exit_code':0,'stdout_tail':'nightly preflight: 8 chain surfaces present; writes=0'},
 'notes-sweep-hourly':{'entrypoint':'bin/notes-sweep-post.sh','mode':'shadow','args':['--dry-run'],'exit_code':0,'stdout_tail':'notes-sweep shadow: scanned=1 unposted=1 writes=0 posts=0'},
 'restore-rehearse-weekly':{'entrypoint':'run.sh','mode':'shadow','args':['restore-rehearse','--preflight'],'exit_code':0,'stdout_tail':'PREFLIGHT OK — every check that runs before anything is created has passed.\nNothing was created, decrypted or charged for. Drop --preflight for the real rehearsal.'},
}
for key,evidence in command_fixtures.items():
    workflow=by[key]
    command=cp._workflow_fact_collector(workflow,weekday_payload,execution=evidence,mode='shadow')
    check(f'{key} exact registered args pass',evaluate_stage(workflow,'filtering',command))
    check(f'{key} exact exit and marker pass',evaluate_stage(workflow,'validation',command))
    receipt=cp._workflow_fact_collector(workflow,weekday_payload,execution=evidence,receipt_ref='job:fixture:attempt:1',receipt_evidence=dict(evidence),mode='shadow')
    check(f'{key} matching immutable receipt reconciles',evaluate_stage(workflow,'completion',receipt))
    wrong_args={**evidence,'args':['--wrong']}
    check(f'{key} unregistered args refuse',not evaluate_stage(workflow,'filtering',cp._workflow_fact_collector(workflow,weekday_payload,execution=wrong_args,mode='shadow')))
    nonzero={**evidence,'exit_code':1}
    check(f'{key} nonzero exit refuses',not evaluate_stage(workflow,'validation',cp._workflow_fact_collector(workflow,weekday_payload,execution=nonzero,mode='shadow')))
    no_marker={**evidence,'stdout_tail':'unrelated successful output'}
    check(f'{key} absent marker refuses',not evaluate_stage(workflow,'validation',cp._workflow_fact_collector(workflow,weekday_payload,execution=no_marker,mode='shadow')))
    altered={**evidence,'stdout_tail':str(evidence['stdout_tail'])+' altered'}
    check(f'{key} nonidentical receipt evidence refuses',not evaluate_stage(workflow,'completion',cp._workflow_fact_collector(workflow,weekday_payload,execution=evidence,receipt_ref='job:fixture:attempt:1',receipt_evidence=altered,mode='shadow')))

# Every enabled deterministic canary has a separate registered command
# and must attest their nonsecret destination plus exact leased source/receipt
# identity in the receipt marker.
notes_marker='notes-sweep: notes-canary-result {"attempted_count":1,"contract":"notes-canary-result.v1","destination_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","duplicate_count":0,"failed_count":0,"posted_count":1,"queued_count":1,"receipt_identity":"job:00000000-0000-0000-0000-000000000001:attempt:1","schema_version":1,"source_digest_kind":"note_id_set_sha256","source_new_count":1,"source_note_count":1,"source_snapshot_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_snapshot_id":"notes-sweep-hourly:00000000-0000-0000-0000-000000000001:attempt:1","still_queued_count":0}'
canary_fixtures={
 'nightly-record-layer': {'entrypoint':'bin/nightly.sh','mode':'canary','args':['--canary'],'exit_code':0,
   'stdout_tail':'nightly canary result: {"availability_count":0,"match_count":0,"open_search_count":0,"output_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","snapshot_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_snapshot_id":"00000000-0000-0000-0000-000000000001"}'},
 'notes-sweep-hourly': {'entrypoint':'bin/notes-sweep-post.sh','mode':'canary','args':['--canary'],'exit_code':0,
   'stdout_tail':notes_marker},
}
for key,evidence in canary_fixtures.items():
    workflow=by[key]
    canary=cp._workflow_fact_collector(workflow,weekday_payload,execution=evidence,mode='canary')
    check(f'{key} isolated canary marker and registered arguments validate',evaluate_stage(workflow,'validation',canary))
    if key == 'notes-sweep-hourly':
        aggregate = cp._notes_canary_aggregate(evidence)
        receipt_evidence = {**evidence, 'notes_canary_result': aggregate}
        completion = cp._workflow_fact_collector(workflow,weekday_payload,execution=evidence,
            receipt_ref='job:fixture:attempt:1',receipt_evidence=receipt_evidence,mode='canary')
        check('Notes canary generic immutable receipt binds the parsed aggregate',
              evaluate_stage(workflow,'completion',completion))
        forged_receipt = {**receipt_evidence,'notes_canary_result':{**aggregate,'posted_count':0,'duplicate_count':1}}
        check('Notes canary generic receipt refuses a mismatched structured aggregate',
              not evaluate_stage(workflow,'completion',cp._workflow_fact_collector(workflow,weekday_payload,
                  execution=evidence,receipt_ref='job:fixture:attempt:1',receipt_evidence=forged_receipt,mode='canary')))
    missing_identity={**evidence,'stdout_tail':(
        str(evidence['stdout_tail']).replace('"source_snapshot_id":"00000000-0000-0000-0000-000000000001"','"source_snapshot_id":"missing"')
        if key == 'nightly-record-layer' else str(evidence['stdout_tail']).replace('"receipt_identity":"job:00000000-0000-0000-0000-000000000001:attempt:1"','"receipt_identity":"job:00000000-0000-0000-0000-000000000002:attempt:1"'))}
    check(f'{key} canary marker without isolated source/destination identity refuses',not evaluate_stage(workflow,'validation',cp._workflow_fact_collector(workflow,weekday_payload,execution=missing_identity,mode='canary')))

# Nightly's live completion marker cannot stand in for its newly isolated
# availability-matcher canary.  The modes have distinct arguments and markers.
nightly=by['nightly-record-layer']
live_evidence={'entrypoint':'bin/nightly.sh','mode':'live','args':[],'exit_code':0,
               'stdout_tail':'nightly result: chain_ok'}
live_collector=cp._workflow_fact_collector(nightly,weekday_payload,execution=live_evidence,mode='live')
check('nightly live stdout completion marker validates',evaluate_stage(nightly,'validation',live_collector))
false_marker={**live_evidence,'stdout_tail':'nightly result: chain_failed'}
check('nightly live failed marker refuses',not evaluate_stage(nightly,'validation',cp._workflow_fact_collector(nightly,weekday_payload,execution=false_marker,mode='live')))
old_canary={**live_evidence,'mode':'canary'}
check('nightly live-equivalent marker cannot satisfy isolated canary validation',
      not evaluate_stage(nightly,'validation',cp._workflow_fact_collector(nightly,weekday_payload,execution=old_canary,mode='canary')))

# Every cognition predicate is fail-closed when its exact canonical input,
# typed proposal field, or immutable receipt evidence is removed.  This table
# intentionally covers the live manifest rather than a hand-picked subset.
for workflow in (w for w in manifest['workflows'] if w['execution']['kind']=='cognition'):
    key=workflow['key']
    empty_input=cp._workflow_fact_collector(workflow,weekday_payload,input_payload={},mode='shadow')
    for fact in workflow['filtering']['spec']['all_of']:
        value=next(iter(empty_input.collect(fact=fact,workflow_key=key,stage='filtering')))['value']
        check(f'{key} filtering {fact} refuses missing canonical field',value is False)
    empty_proposal=cp._workflow_fact_collector(workflow,weekday_payload,execution={'proposal':{}},mode='shadow')
    for fact in workflow['validation']['spec']['all_of']:
        value=next(iter(empty_proposal.collect(fact=fact,workflow_key=key,stage='validation')))['value']
        check(f'{key} validation {fact} refuses missing typed proposal field',value is False)
    empty_receipt=cp._workflow_fact_collector(workflow,weekday_payload,execution={'proposal':{}},receipt_ref='job:fixture:attempt:1',receipt_evidence={},mode='shadow')
    for fact in workflow['completion']['spec']['all_of']:
        value=next(iter(empty_receipt.collect(fact=fact,workflow_key=key,stage='completion')))['value']
        check(f'{key} completion {fact} refuses receipt without typed evidence',value is False)
print(f'control-plane runtime facts selftest — {passed}/{passed+len(failed)} passed')
if failed: print('FAILED: '+'; '.join(failed)); raise SystemExit(1)
