#!/usr/bin/env python3
"""CI-declared Python checkers must run outside their fixture selftests."""
import ast
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


def unwired_checks(catalog, ci, gates_source=""):
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
        if 'ops/ci-gates.py' in checker_calls(body) and gates_source:
            module = ast.parse(gates_source)
            inventories = [ast.literal_eval(node.value) for node in module.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == 'INVARIANTS' for t in node.targets)]
            executes = any(isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                and node.iter.id == 'INVARIANTS'
                and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == 'execute' and len(call.args) >= 3
                        and ast.unparse(call.args[2]) == "f'ops/{inv}.py'"
                        for call in ast.walk(node)) for node in ast.walk(module))
            if inventories and executes:
                calls.update(f'ops/{name}.py' for name in inventories[0])
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
                                           (REPO / 'ops/ci.sh').read_text(),
                                           (REPO / 'ops/ci-gates.py').read_text()))
        for name, path in declared_checks(mapping['control_catalog']):
            self.assertTrue((REPO / path).is_file(), f'{name}: missing {path}')

    def test_python_inventory_requires_live_runner_and_execution(self):
        source = (REPO / 'ops/ci-gates.py').read_text()
        catalog = {'order': {'failure_mode': 'CI failure',
                             'implementation': ['ops/migration-order-gate.py']}}
        ci = self.ci('  "$PY" ops/ci-gates.py --logdir "$LOGDIR"')
        self.assertEqual([], unwired_checks(catalog, ci, source))
        for broken_ci, broken_source in (
                (ci.replace('"$PY"', 'echo'), source),
                (ci.replace('"gates"', '"unit"'), source),
                (ci, source.replace('self.execute(', 'self.describe('))):
            self.assertEqual(['order: ops/migration-order-gate.py'],
                             unwired_checks(catalog, broken_ci, broken_source))

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
