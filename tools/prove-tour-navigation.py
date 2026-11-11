#!/usr/bin/env python3
"""Read one authenticated Tour route and its deployed navigation evidence."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read
from lib.proof_deadline import proof_deadline


class Unavailable(Exception):
    pass


def check_coordinate_review(stop, point):
    review = stop.get('coordinate_review')
    if not review:
        raise Unavailable('coordinate review contract unavailable')
    assert review['schema'] == 'tour-coordinate-review.v1', 'coordinate review version mismatch'
    assert review['property_id'] == stop['property_id'], 'coordinate review property mismatch'
    assert review['as_of'] and isinstance(review['property_evidence'], list), 'missing property evidence'
    candidates = review['candidates']
    assert candidates and review['status'] == 'candidates', 'candidate evidence missing'
    source = point['source']
    selected = next((row for row in candidates if row['coordinate_candidate_id'] == source['coordinate_candidate_id']), None)
    assert selected is not None, 'navigation candidate absent from review'
    assert selected['property_id'] == stop['property_id'], 'candidate property mismatch'
    assert selected['position'] == point['position'], 'review/navigation position mismatch'
    for field in ('coordinate_role', 'precision_class', 'review_state'):
        assert selected[field] == source[field], f'review/navigation {field} mismatch'
    for field in ('source_evidence_id', 'rights_receipt_id'):
        owner = selected['source'] if field == 'source_evidence_id' else selected['rights']
        assert owner[field] == source[field], f'review/navigation {field} mismatch'
    assert selected['rights']['status'] == 'usable', 'navigation rights unavailable'
    assert selected['approval']['status'] == 'verified_entrance', 'entrance approval unavailable'
    receipt = selected['approval']['receipt']
    assert receipt['verification_receipt_id'] == source['entrance_verification_receipt_id'], 'entrance receipt mismatch'
    for field in ('verifier_actor_id', 'verified_at', 'evidence_reference', 'native_navigation_proof', 'receipt_digest'):
        assert receipt[field] == source[field], f'review/navigation {field} mismatch'


def check_route(detail, route_id, coordinate_review=False):
    route = next((row for row in detail['routes'] if row['id'] == route_id), None)
    if route is None:
        raise Unavailable('route unavailable')
    nav = route.get('navigation')
    assert nav and nav['contract_version'] == 'tour-navigation-evidence.v1', 'contract version mismatch'
    if nav['status'] != 'ready':
        assert nav['evidence'] is None and nav['reason'], 'invalid unavailable response'
        raise Unavailable(nav['reason'])
    evidence = nav['evidence']
    assert route['accepted'] and evidence['route_version_id'] == route_id, 'route identity mismatch'
    assert evidence['route_version'] == route['route_version'] == detail['route_version'], 'route version mismatch'
    promotion = evidence['promotion']
    assert promotion['route_version'] == evidence['route_version'], 'promotion route mismatch'
    for field in ('receipt_id', 'receipt_digest', 'component_registry_version', 'canonical_dataset_version'):
        assert promotion[field], f'missing promotion {field}'
    projection = next(row for row in detail['projections'] if row['id'] == evidence['projection_id'])
    assert projection['route_version'] == evidence['route_version'], 'projection route mismatch'
    assert projection['projection_digest'] == evidence['projection_digest'], 'projection digest mismatch'
    active = [row for row in route['stops'] if row['stop_state'] == 'active']
    assert active and len(active) == len(evidence['stops']), 'stop population mismatch'
    for stop, point in zip(active, evidence['stops']):
        assert stop['id'] == point['route_stop_id'] and stop['property_id'] == point['property_id'], 'stop identity mismatch'
        source = point['source']
        assert source['coordinate_role'] in ('entrance', 'driveway', 'parking_access'), 'unapproved coordinate role'
        assert source['review_state'] == 'reviewed' and source['retrieval_status'] == 'read', 'unreviewed source'
        for field in ('coordinate_candidate_id', 'entrance_verification_receipt_id', 'source_evidence_id', 'rights_receipt_id', 'evidence_reference'):
            assert source[field], f'missing source {field}'
        assert source['native_navigation_proof']['status'] == 'passed', 'entrance navigation not verified'
        assert -90 <= point['position']['latitude'] <= 90 and -180 <= point['position']['longitude'] <= 180, 'invalid position'
        if coordinate_review:
            check_coordinate_review(stop, point)
    return {'contract_version': nav['contract_version'], 'route_version_id': route_id, 'stops': len(active)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--tour-id', required=True)
    parser.add_argument('--route-id', required=True)
    parser.add_argument('--cookie-jar', type=Path)
    parser.add_argument('--coordinate-review', action='store_true')
    args = parser.parse_args()
    try:
        with proof_deadline(30, Unavailable):
            deadline = time.monotonic() + 30
            def get(url, jar=None):
                reply = read(url, timeout=max(.1, deadline-time.monotonic()), max_bytes=2_000_000, cookie_jar=jar)
                if reply.status != 200 or len(reply.body) > 2_000_000:
                    raise Unavailable('authenticated read unavailable')
                return json.loads(reply.body)
            release = get('https://api.doctorcre.com/release')
            if release.get('ok') is not True or release.get('env', {}).get('value') != 'production':
                raise Unavailable('production release identity unavailable')
            served = release['git_sha']['value']
            if not all(isinstance(sha, str) and re.fullmatch('[a-f0-9]{40}', sha) for sha in (served, args.merge_sha)):
                raise Unavailable('release identity unavailable')
            git = ['git', '--no-replace-objects', '-C', str(Path(__file__).resolve().parents[1])]
            if subprocess.run([*git, 'merge-base', '--is-ancestor', args.merge_sha, served], capture_output=True, timeout=max(.1, deadline-time.monotonic())).returncode:
                raise Unavailable('release does not prove containing merge')
            if not args.cookie_jar:
                raise Unavailable('authenticated cookie jar and qualifying approved route required')
            for value in (args.tour_id, args.route_id):
                if not re.fullmatch('[a-f0-9-]{36}', value):
                    raise Unavailable('invalid Tour or route id')
            jar = http.cookiejar.MozillaCookieJar(str(args.cookie_jar))
            jar.load(ignore_discard=True)
            detail = get(f'https://app.doctorcre.com/api/tours/detail?tour_id={args.tour_id}', jar)['data']
            receipt = check_route(detail, args.route_id, args.coordinate_review)
            print(json.dumps({'status': 'passed', 'source_commit': served, **receipt}))
            return 0
    except (Unavailable, OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'insufficient_evidence', 'reason': str(error)}))
        return 78
    except AssertionError as error:
        print(json.dumps({'status': 'failed', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
