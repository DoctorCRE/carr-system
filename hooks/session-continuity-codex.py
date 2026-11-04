"""Admit native Codex lifecycle metadata without inventing checkpoint authority."""
import argparse
import json
import sys

MAX_INPUT = 65536
MISSING = (
    'native_epoch_and_cursor',
    'fresh_model_window_observation',
    't3_ownership_receipt',
    'authoritative_checkpoint_producer',
    'matching_acknowledged_checkpoint',
)


def diagnose(payload):
    if not isinstance(payload, dict):
        return {'status': 'unknown', 'reason': 'invalid_native_input'}
    event = payload.get('hook_event_name')
    if not isinstance(event, str):
        return {'status': 'unknown', 'reason': 'invalid_native_metadata'}
    if event not in ('PreCompact', 'SessionStart'):
        return {'status': 'ignored', 'reason': 'unrelated_event'}
    if event == 'SessionStart' and payload.get('source') in ('startup', 'resume', 'clear'):
        return {'status': 'ignored', 'reason': 'not_compact_recovery'}
    required = ('session_id', 'cwd', 'model') + (('turn_id', 'trigger') if event == 'PreCompact' else ('source',))
    if any(not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 2048 for key in required):
        return {'status': 'unknown', 'reason': 'invalid_native_metadata'}
    if (event == 'PreCompact' and payload['trigger'] not in ('auto', 'manual') or
            event == 'SessionStart' and payload['source'] != 'compact'):
        return {'status': 'unknown', 'reason': 'invalid_native_metadata'}
    if 'agent_id' in payload or 'agent_type' in payload:
        return {'status': 'unsupported', 'reason': 'nested_identity_not_established'}
    return {'status': 'unknown', 'reason': 'firsthand_producer_unavailable', 'missing': list(MISSING)}


def native_output(report):
    if report['status'] == 'ignored':
        return {}
    return {'systemMessage': 'CARR Codex continuity unavailable (' + report['reason'] +
            '). No checkpoint was saved or injected; native compaction may continue.'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_native_field')
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError('nonfinite')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose', action='store_true', help='print contract gaps, never session content')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('input_too_large')
        report = diagnose(json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant))
    except (ValueError, TypeError, RecursionError):
        report = {'status': 'unknown', 'reason': 'invalid_native_input'}
    print(json.dumps(report if args.diagnose else native_output(report), separators=(',', ':')))


if __name__ == '__main__':
    main()
