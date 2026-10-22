"""Read the complete Node test-runner summary from TAP or spec output."""
import re

FIELDS = ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo')


def test_counts(output):
    pairs = re.findall(r'^(?:#|ℹ) (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$',
                       output, re.MULTILINE)
    occurrences = {name: sum(key == name for key, _ in pairs) for name in FIELDS}
    if not pairs or len(set(occurrences.values())) != 1:
        return None
    counts = {name: sum(int(value) for key, value in pairs if key == name) for name in FIELDS}
    if counts['tests'] != sum(counts[name] for name in FIELDS[1:]):
        return None
    files = re.findall(r'^(?:# )?CARR_TEST_FILE ([01])\s*$', output, re.MULTILINE)
    if not files:
        return None
    counts['tests'] -= files.count('0')
    counts['pass'] -= files.count('0')
    if counts['pass'] < 0:
        return None
    return counts
