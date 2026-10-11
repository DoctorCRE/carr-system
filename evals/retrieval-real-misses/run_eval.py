#!/usr/bin/env python3
"""Read-only real-miss retrieval measurement. Persist refs, never response text."""

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
READ_VERBS = frozenset({
    'find', 'find-and-catch-up', 'who-do-we-know', 'search-doctrine',
    'standing-context', 'find-precedent', 'lead-board', 'deal-board',
})
CAUSES = frozenset({'never-asked', 'never-asked/drift', 'search-quality', 'not-linked', 'not-captured'})
REF = re.compile(r'(?:[PCL]-[0-9]+|V-[A-Za-z0-9]+-[0-9]+|'
                 r'(?:section|decision|deal|loop):[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|'
                 r'rule:[0-9a-f]{8})\Z')
LANES = {'find': {'parties', 'organizations', 'deals', 'deals_via_link'},
         'find-and-catch-up': {'candidates'}, 'who-do-we-know': {'resolved'},
         'search-doctrine': {'hits'}, 'find-precedent': {'rulings'},
         'rule-routing': {'routed_rules'}}
STATES = {None, 'completed', 'not_found', 'needs_disambiguation'}
ERRORS = {'timeout', 'read_command_unavailable', 'read_command_failed', 'invalid_response_json',
          'read_response_error', 'verb_not_read_allowlisted', 'deal_identity_not_unique',
          'unsupported_result_contract', 'boot_changed_during_read', 'invalid_boot_contract',
          'rule_trigger_table_invalid', 'invalid_board_contract', 'query_name_not_unique',
          'deal_board_unavailable', 'invalid_result_contract'}


class ReadError(Exception):
    pass


def call_read(verb, args):
    if verb not in READ_VERBS:
        raise ReadError('verb_not_read_allowlisted')
    try:
        result = subprocess.run(
            [str(REPO / 'run.sh'), 'call', verb, json.dumps(args)],
            cwd=REPO, text=True, capture_output=True, timeout=65,
        )
    except subprocess.TimeoutExpired:
        raise ReadError('timeout') from None
    except OSError:
        raise ReadError('read_command_unavailable') from None
    if result.returncode:
        raise ReadError('read_command_failed')
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise ReadError('invalid_response_json') from None
    if not isinstance(payload, dict) or payload.get('error') or payload.get('ok') is False:
        raise ReadError('read_response_error')
    return payload


def candidate(refs, eligible=True):
    return {'refs': [r for r in refs if isinstance(r, str) and REF.fullmatch(r)], 'eligible': eligible}


def record_candidates(verb, payload, lane, deal_index=None):
    if verb == 'find':
        rows = payload[lane]
        if lane == 'organizations':
            return [candidate(r['refs'] + r.get('role_refs', []), not r['all_retired']) for r in rows]
        if lane in {'deals', 'deals_via_link'}:
            result = []
            for row in rows:
                key = (row.get('client_ref'), row.get('name'))
                ids = (deal_index or {}).get(key, [])
                if len(ids) != 1:
                    raise ReadError('deal_identity_not_unique')
                result.append(candidate(['deal:' + ids[0]]))
            return result
        return [candidate([r.get('ref')], r.get('merged') is False) for r in rows]
    if verb == 'find-and-catch-up':
        if payload['state'] not in STATES - {None}:
            raise ReadError('invalid_result_contract')
        if payload['state'] == 'completed':
            return [candidate([payload['match']['target']])]
        return [candidate([r.get('target')]) for r in payload['candidates']]
    if verb == 'who-do-we-know':
        resolved = payload['resolved']
        if isinstance(resolved, dict):
            return [candidate([resolved.get('ref')], resolved.get('merged') is not True)]
        return [candidate([r.get('ref')], r.get('merged') is not True)
                for r in payload['matching_records']]
    if verb == 'search-doctrine':
        return [candidate(['section:' + r['section_id']]) for r in payload['hits']]
    if verb == 'find-precedent':
        return [candidate(['decision:' + r['decision_id']], r['record_kind'] == 'settled_ruling')
                for r in payload['rulings']]
    raise ReadError('unsupported_result_contract')


def project_observations(observations):
    projected = []
    for row in observations:
        result = {'case_id': row['case_id'], 'candidates': [], 'error': row.get('error')}
        if not isinstance(result['case_id'], str) or result['error'] not in ERRORS | {None}:
            raise ValueError('invalid observation identity or error')
        for entry in row.get('candidates', []):
            if not isinstance(entry['eligible'], bool) or not isinstance(entry['refs'], list):
                raise ValueError('invalid candidate shape')
            if any(not isinstance(ref, str) or not REF.fullmatch(ref) for ref in entry['refs']):
                raise ValueError('invalid candidate ref')
            result['candidates'].append(candidate(entry['refs'], entry['eligible']))
        for key in ('state', 'fallback', 'live_rows'):
            if key not in row:
                continue
            value = row[key]
            if ((key == 'state' and value not in STATES) or
                    (key == 'fallback' and value is not None and not isinstance(value, bool)) or
                    (key == 'live_rows' and value is not None and
                     (type(value) is not int or value < 0))):
                raise ValueError('invalid observation metadata')
            result[key] = value
        projected.append(result)
    return projected


def score(cases, observations):
    observations = project_observations(observations)
    indexed = {r['case_id']: r for r in observations}
    if len(indexed) != len(observations) or set(indexed) != {c['id'] for c in cases}:
        raise ValueError('observations must cover each case exactly once')
    buckets = {}
    rows = []
    for case in cases:
        observation = indexed[case['id']]
        routing = case.get('measurement') == 'rule_delivery'
        candidates = observation.get('candidates', [])
        top = candidates if routing else candidates[:5]
        refs = {ref for row in top if row['eligible'] for ref in row['refs']}
        error = observation.get('error')
        hit = not error and bool(refs.intersection(case['expected_refs']))
        contract = not error and not refs.intersection(case.get('forbidden_live_refs', []))
        if 'expected_live_rows' in case:
            contract = contract and observation.get('live_rows') == case['expected_live_rows']
        row = {'case_id': case['id'], 'cause': case['cause'], 'hit_at_5': None if routing else bool(hit),
               'rule_delivered': bool(hit) if routing else None,
               'contract_pass': bool(contract), 'error': error}
        rows.append(row)
        buckets.setdefault(case['cause'], []).append(row)
    def aggregate(group):
        retrieval = [r for r in group if r['hit_at_5'] is not None]
        routing = [r for r in group if r['rule_delivered'] is not None]
        count = len(retrieval)
        hits = sum(r['hit_at_5'] for r in retrieval)
        return {'questions': count, 'hits_at_5': hits, 'hit_rate_at_5': hits / count if count else 0,
                'errors': sum(bool(r['error']) for r in group),
                'contract_failures': sum(not r['contract_pass'] for r in group),
                'rule_routing': {'questions': len(routing),
                                 'delivered': sum(r['rule_delivered'] for r in routing),
                                 'delivery_recall': sum(r['rule_delivered'] for r in routing) / len(routing)
                                 if routing else None}}
    return {'overall': aggregate(rows), 'by_cause': {k: aggregate(v) for k, v in sorted(buckets.items())},
            'cases': rows}


def validate_fixture(fixture):
    cases = fixture['cases']
    if fixture['version'] != 'retrieval-real-misses/v1' or not 25 <= len(cases) <= 40:
        raise ValueError('invalid fixture version or case count')
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate case IDs')
    if {c['source_case'] for c in cases} != set(range(1, 11)):
        raise ValueError('all ten historical cases are required')
    for case in cases:
        if case['cause'] not in CAUSES or not case['expected_refs']:
            raise ValueError('invalid cause or missing expected refs')
        if case['source_case'] not in {s['case'] for s in fixture['sources']}:
            raise ValueError('missing source evidence')
        if case['request']['verb'] not in READ_VERBS | {'rule-routing'}:
            raise ValueError('fixture requests a non-read verb')
        if case['lane'] not in LANES.get(case['request']['verb'], set()):
            raise ValueError('invalid result lane')
        if any(not REF.fullmatch(ref) for ref in case['expected_refs']):
            raise ValueError('expected target is not a record ref')
        if (case['request']['verb'] == 'rule-routing') != (case.get('measurement') == 'rule_delivery'):
            raise ValueError('rule routing must use unordered delivery measurement')
    return cases


def boot_rule_ids(read):
    first = read('standing-context', {'detail': 'boot', 'page': 1})['rule_boot']
    pages = [first]
    for page in range(2, first['pages_total'] + 1):
        result = read('standing-context', {'detail': 'boot', 'page': page})['rule_boot']
        if result['digest'] != first['digest'] or result['page'] != page:
            raise ReadError('boot_changed_during_read')
        pages.append(result)
    return set(re.findall(r'^### ([0-9a-f]{8})\b', '\n'.join(p['text'] for p in pages), re.M))


def routing_candidates(case, read, boot_ids):
    sys.path.insert(0, str(REPO))
    from lib.rule_routes import load_routes, matched_rule_ids
    spec = importlib.util.spec_from_file_location('real_miss_trigger', REPO / 'ops/rule_trigger_delivery.py')
    if spec is None or spec.loader is None:
        raise ReadError('rule_trigger_table_invalid')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.prompt_rows(str(REPO / 'ops/config/rule-jit-triggers.v1.json'))
    if rows is None:
        raise ReadError('rule_trigger_table_invalid')
    args = case['request']['args']
    ids = list(module.match(args['prompt'], rows))
    if 'tool_name' in args:
        ids += matched_rule_ids(load_routes(REPO), args['tool_name'], args['tool_input'])
    ids = sorted(set(ids))
    response = read('standing-context', {'rule_ids': ids})
    returned = {r['id'] for r in response['shared_rules'] + response['personal_rules']
                if r.get('statement')}
    delivered = boot_ids | (set(ids) & returned)
    return [candidate(['rule:' + rid]) for rid in sorted(delivered)]


def run_cases(cases, read=call_read):
    deal_index = None
    names = {}
    name_errors = {}
    boot_ids = set()
    boot_error = None
    if any(c['request']['verb'] == 'rule-routing' for c in cases):
        try:
            boot_ids = boot_rule_ids(read)
        except ReadError as exc:
            boot_error = str(exc)
        except (KeyError, TypeError, ValueError):
            boot_error = 'invalid_boot_contract'
    if any(c.get('name_from_ref') or c['lane'] in {'deals', 'deals_via_link'} for c in cases):
        try:
            board = read('deal-board', {})['deals']
            resolved_deals = {}
            resolved_names = {}
            for row in board:
                resolved_deals.setdefault((row['client_ref'], row['name']), []).append(row['id'])
                if row.get('client_ref') and row.get('client_name'):
                    resolved_names.setdefault(row['client_ref'], set()).add(row['client_name'])
            deal_index = resolved_deals
            names.update(resolved_names)
        except (ReadError, KeyError, TypeError, AttributeError) as exc:
            name_errors['deal-board'] = str(exc) if isinstance(exc, ReadError) else 'invalid_board_contract'
    if any(str(c.get('name_from_ref', '')).startswith('L-') for c in cases):
        try:
            resolved_names = {}
            for row in read('lead-board', {'workspace': 'leads'})['leads']:
                if row.get('registry_ref') and row.get('name'):
                    resolved_names.setdefault(row['registry_ref'], set()).add(row['name'])
            names.update(resolved_names)
        except (ReadError, KeyError, TypeError, AttributeError) as exc:
            name_errors['lead-board'] = str(exc) if isinstance(exc, ReadError) else 'invalid_board_contract'
    observations = []
    for case in cases:
        row = {'case_id': case['id'], 'candidates': [], 'error': None}
        try:
            verb = case['request']['verb']
            if verb == 'rule-routing':
                if boot_error:
                    raise ReadError(boot_error)
                row['candidates'] = routing_candidates(case, read, boot_ids)
            else:
                args = dict(case['request']['args'])
                if case.get('name_from_ref'):
                    source = case['name_from_ref']
                    choices = names.get(source, set())
                    if len(choices) != 1:
                        raise ReadError(name_errors.get('lead-board' if source.startswith('L-') else 'deal-board',
                                                       'query_name_not_unique'))
                    name = next(iter(choices))
                    args['query'] = args['query'].replace('{name}', name)
                if case['lane'] in {'deals', 'deals_via_link'} and deal_index is None:
                    raise ReadError(name_errors.get('deal-board', 'deal_board_unavailable'))
                payload = read(verb, args)
                row['candidates'] = record_candidates(verb, payload, case['lane'], deal_index)
                state = payload.get('state')
                fallback = payload.get('fallback')
                if state not in STATES:
                    raise ReadError('invalid_result_contract')
                if fallback is not None and not isinstance(fallback, bool):
                    raise ReadError('invalid_result_contract')
                row['state'] = state
                row['fallback'] = fallback
                if 'expected_live_rows' in case:
                    orgs = [r for r in payload['organizations']
                            if set(case['expected_refs']).intersection(r['refs'] + r.get('role_refs', []))]
                    row['live_rows'] = sum(r['live_rows'] + r.get('live_as_role', 0) for r in orgs)
        except ReadError as exc:
            row['error'] = str(exc)
        except (KeyError, TypeError, ValueError, AttributeError):
            row['error'] = 'invalid_result_contract'
        observations.append(row)
        print(f"{case['id']}: {'ERROR ' + row['error'] if row['error'] else 'read'}", file=sys.stderr)
    return observations


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance():
    return {'measured_at': datetime.now(timezone.utc).isoformat(),
            'runner_sha256': sha(Path(__file__)),
            'source_revision': subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'routing_sha256': {p: sha(REPO / p) for p in [
                'ops/config/rule-routes.v1.json', 'ops/config/rule-jit-triggers.v1.json',
                'ops/rule_trigger_delivery.py', 'lib/rule_routes.py']}}


def build_result(cases, observations, fixture_digest, saved=None):
    current = provenance()
    origin = {k: saved[k] for k in current} if saved is not None else current
    result = {'version': 'retrieval-real-misses-run/v1', **origin,
              'fixture_sha256': fixture_digest, 'observations': project_observations(observations),
              'report': score(cases, observations)}
    if saved is not None:
        result['rescored_with'] = current
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=HERE / 'questions.v1.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay', type=Path, help='Rescore a refs-only run without calling production')
    args = parser.parse_args()
    try:
        fixture = json.loads(args.fixture.read_text())
        cases = validate_fixture(fixture)
        if fixture.get('requires_private_fixture') and not args.replay:
            parser.error('live evaluation requires --fixture with private source queries')
        saved = None
        if args.replay:
            saved = json.loads(args.replay.read_text())
            if saved['fixture_sha256'] != sha(args.fixture):
                raise ValueError('replay fixture digest mismatch')
            observations = saved['observations']
        else:
            observations = run_cases(cases)
        result = build_result(cases, observations, sha(args.fixture), saved)
        report = result['report']
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print('eval failed: ' + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ['overall', 'by_cause']}, indent=2))
    return 1 if report['overall']['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
