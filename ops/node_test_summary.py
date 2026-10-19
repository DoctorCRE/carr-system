"""Read the complete Node test-runner summary from TAP or spec output."""
import re

FIELDS = ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo')


def test_counts(output):
    pairs = re.findall(r'^(?:#|ℹ) (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$',
                       output, re.MULTILINE)
    counts = {name: int(value) for name, value in pairs}
    if len(pairs) != len(FIELDS) or set(counts) != set(FIELDS):
        return None
    if counts['tests'] != sum(counts[name] for name in FIELDS[1:]):
        return None
    # Node counts an empty test file as one passing test under its filename.
    empty_files = re.findall(
        r"^(?:# Subtest: (.+\.test\.(?:mjs|cjs|js))|✔ (.+\.test\.(?:mjs|cjs|js)) \([\d.]+ms\))$",
        output, re.MULTILINE)
    counts['tests'] -= len(empty_files)
    counts['pass'] -= len(empty_files)
    if counts['pass'] < 0:
        return None
    return counts
