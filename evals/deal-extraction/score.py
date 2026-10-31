"""Score synthetic scalar smoke predictions; this corpus cannot authorize promotion."""
import argparse
import json
from pathlib import Path


def score(predictions):
    corpus = json.loads(Path(__file__).with_name('fixtures.json').read_text())
    expected = {(case['id'], key): value for case in corpus['cases'] for key, value in case['expected'].items()}
    if not isinstance(predictions, list):
        raise ValueError('predictions must be an array of case_id, fact_key and value')
    seen = set()
    correct = emitted = 0
    for item in predictions:
        key = (item['case_id'], item['fact_key'])
        if key not in expected or key in seen:
            raise ValueError('unknown or duplicate smoke prediction')
        seen.add(key)
        if item.get('value') is not None:
            emitted += 1
            correct += item['value'] == expected[key]
    return {'schema_version': corpus['schema_version'], 'correct': correct, 'emitted': emitted,
            'supported_fields': len(expected), 'precision': correct / emitted if emitted else None,
            'recall': correct / len(expected), 'abstention_rate': 1 - emitted / len(expected),
            'automatic_promotion_eligible': False, 'reason': 'smoke_corpus_is_not_a_frozen_holdout'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('predictions', type=Path)
    print(json.dumps(score(json.loads(parser.parse_args().predictions.read_text()))))
