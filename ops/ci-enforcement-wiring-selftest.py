#!/usr/bin/env python3
"""CI-declared Python checkers must run outside their fixture selftests."""
import json
from pathlib import Path
import re
import shlex
import unittest


REPO = Path(__file__).resolve().parent.parent


def declared_checks(catalog):
    return [(name, path) for name, control in catalog.items()
            if re.search(r'\bCI\b', control.get('failure_mode', ''))
            for path in control.get('implementation', [])
            if path.startswith('ops/') and path.endswith('.py')]


def unwired_checks(catalog, ci):
    order = re.search(r'^CLASS_ORDER="([^"]+)"', ci, re.M)
    active = set(order[1].split()) if order else set()
    calls = set()
    for name, body in re.findall(r'^check_(\w+)\(\) \{\n(.*?)^\}', ci, re.M | re.S):
        if name not in active:
            continue
        body = body.replace('\\\n', '')
        def checker_calls(text):
            return {quoted or bare for quoted, bare in re.findall(
                r'^\s*(?:(?:if|elif)\s+!\s+)?(?:\w+=(?:"[^"]*"|\S+)\s+)*'
                r'(?:run_quiet\s+"[^"]+"\s+)?'
                r'"\$PY"\s+(?:"(ops/[^"]+)"|(ops/[\w./-]+))', text, re.M)}
        calls.update(checker_calls(body))
        for variable, values, loop in re.findall(
                r'for (\w+) in ([^;]+); do\n(.*?)^  done', body, re.M | re.S):
            if f'ops/${variable}.py' in checker_calls(loop):
                calls.update(f'ops/{value}.py' for value in shlex.split(values, comments=True))
    return sorted(
        f'{name}: {path}'
        for name, path in declared_checks(catalog) if path not in calls
    )


class EnforcementWiringTest(unittest.TestCase):
    catalog = {'example': {'failure_mode': 'CI class failure',
                           'implementation': ['ops/example-check.py']}}

    def ci(self, body, order='gates'):
        return f'CLASS_ORDER="{order}"\ncheck_gates() {{\n{body}\n}}\n'

    def test_current_map_has_no_unwired_ci_checkers(self):
        mapping = json.loads((REPO / 'ops/config/rule-enforcement-map.json').read_text())
        self.assertEqual([], unwired_checks(mapping['control_catalog'],
                                           (REPO / 'ops/ci.sh').read_text()))
        for name, path in declared_checks(mapping['control_catalog']):
            self.assertTrue((REPO / path).is_file(), f'{name}: missing {path}')

    def test_selftest_comment_and_echo_are_not_checker_calls(self):
        ci = self.ci('  "$PY" ops/example-selftest.py\n'
                     '  # "$PY" ops/example-check.py\n'
                     '  echo "$PY" ops/example-check.py')
        self.assertEqual(['example: ops/example-check.py'], unwired_checks(self.catalog, ci))

    def test_direct_checker_call_in_active_class(self):
        ci = self.ci('  run_quiet "$LOGDIR/example.log" "$PY" ops/example-check.py')
        self.assertEqual([], unwired_checks(self.catalog, ci))
        self.assertEqual(['example: ops/example-check.py'],
                         unwired_checks(self.catalog, ci.replace('"gates"', '"unit"')))

    def test_inventory_requires_execution(self):
        ci = self.ci('  for inv in example-check; do\n'
                     '    run_quiet "$LOGDIR/$inv.log" "$PY" "ops/$inv.py"\n'
                     '  done')
        self.assertEqual([], unwired_checks(self.catalog, ci))
        self.assertEqual(['example: ops/example-check.py'],
                         unwired_checks(self.catalog, ci.replace('run_quiet', 'echo')))


if __name__ == '__main__':
    unittest.main()
