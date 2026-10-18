"""Reject newly introduced unpinned, uncached or split-state Jev calls.

Existing sibling-owned callers are baseline debt, bound to exact source hashes.
Changed/new calls must use the semantic seam or explicitly declare a pinned
model and cache key. This is a source check, not proof of runtime equivalence.
"""
import ast
import copy
import itertools
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = ROOT/'ops/config/jev-conformance-legacy.v1.json'

def scope_nodes(scope):
    """Walk one executable scope, leaving independent function bodies alone."""
    yield scope
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from scope_nodes(child)

def python_errors(source):
    tree = ast.parse(source)
    errors = []
    if not re.search(r'typesafe|jev_judge|jev_semantic', source):
        return errors
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    scopes = [tree]+[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    for scope in scopes:
        ancestry, parent = [scope], parents.get(scope)
        while parent is not None:
            if parent in scopes:
                ancestry.append(parent)
            parent = parents.get(parent)
        imports = {}
        for ancestor in reversed(ancestry):
            for node in scope_nodes(ancestor):
                if isinstance(node, ast.ImportFrom):
                    module = (node.module or '').split('.')[-1]
                    for alias in node.names:
                        imports[alias.asname or alias.name] = (module, alias.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports[alias.asname or alias.name] = (alias.name.split('.')[-1], None)
        bindings, seen = {}, set()
        local_nodes = list(scope_nodes(scope))

        def resolve(node, env):
            names = sorted({v.id for v in ast.walk(node)
                            if isinstance(v, ast.Name) and v.id in env})
            class Substitute(ast.NodeTransformer):
                def visit_Name(self, value):
                    return copy.deepcopy(values.get(value.id, value))
            results = []
            for choices in itertools.product(*(env[name] for name in names)):
                values = dict(zip(names, choices))
                results.append(Substitute().visit(copy.deepcopy(node)))
            return results

        def target(node, env):
            if isinstance(node, ast.Name):
                return imports.get(node.id, ('', node.id))
            if isinstance(node, ast.Attribute):
                modules = set()
                for owner in resolve(node.value, env):
                    if isinstance(owner, ast.Name):
                        module = imports.get(owner.id, ('', None))[0]
                        if not module and 'semantic' in owner.id:
                            module = 'jev_semantic'
                    elif isinstance(owner, ast.Call) and owner.args and isinstance(owner.args[0], ast.Constant):
                        module = str(owner.args[0].value).split('.')[-1]
                    else:
                        module = 'jev_semantic' if 'semantic' in ast.unparse(owner) else ''
                    modules.add(module)
                # Any possible raw transport retains its model/cache checks.
                module = next((m for m in ('typesafe_client', 'jev_judge') if m in modules),
                              'jev_semantic' if modules == {'jev_semantic'} else '')
                return module, node.attr
            return '', ''

        def inspect(call, env, prior):
            module, method = target(call.func, env)
            known = module in {'typesafe_client', 'jev_judge', 'jev_semantic'}
            if method not in {'ask', 'judge', '_ask_jev', 'server_ask', 'evaluate'}:
                return
            if method == 'evaluate' and not known:
                return
            if not known and not isinstance(call.func, ast.Attribute):
                return
            semantic_call = module == 'jev_semantic'
            request_call = semantic_call and method == 'evaluate'
            argument = call.args[0] if call.args else next((k.value for k in call.keywords
                if k.arg == ('request' if request_call else 'state')), ast.Constant(None))
            states = set()
            for request in resolve(argument, env):
                if request_call and isinstance(request, ast.Call):
                    state_node = request.args[0] if request.args else next(
                        (k.value for k in request.keywords if k.arg == 'state'), argument)
                else:
                    state_node = request
                state = ast.dump(state_node, include_attributes=False)
                states.add(state)
                state_names = {v.id.split('@')[0] for v in ast.walk(state_node) if isinstance(v, ast.Name)}
                for loop in (v for v in local_nodes if isinstance(v, (ast.For, ast.AsyncFor, ast.While))):
                    if not any(v is call for child in loop.body for v in scope_nodes(child)):
                        continue
                    changed_names = {v.id for child in loop.body for v in scope_nodes(child)
                                     if isinstance(v, ast.Name) and isinstance(v.ctx, ast.Store)}
                    if isinstance(loop, (ast.For, ast.AsyncFor)):
                        changed_names |= {v.id for v in ast.walk(loop.target) if isinstance(v, ast.Name)}
                    if not state_names & changed_names:
                        errors.append(f'{call.lineno}: fanout: loop repeats unchanged state')
                if state in prior:
                    errors.append(f'{call.lineno}: fanout: combine all questions for this state')
                if semantic_call:
                    keywords = request.keywords if request_call and isinstance(request, ast.Call) else (
                        [] if request_call else call.keywords)
                    if not {'caller', 'version'} <= {k.arg for k in keywords}:
                        errors.append(f'{call.lineno}: cache: semantic call needs caller/version')
            prior.update(states)
            if semantic_call:
                return
            kw = {k.arg: k.value for k in call.keywords}
            model = kw.get('model')
            if not isinstance(model, ast.Constant) or model.value != 'jev-1.13.0':
                errors.append(f'{call.lineno}: model: pin jev-1.13.0')
            if 'cache_key' not in kw:
                errors.append(f'{call.lineno}: cache: use jev_semantic.ask with complete input key')

        exception_collectors = []

        def capture_prefix(env, prior):
            for prefixes in exception_collectors:
                prefixes.append((dict(env), set(prior)))

        def expression(node, env, prior):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            for child in ast.iter_child_nodes(node):
                expression(child, env, prior)
            if isinstance(node, ast.Call):
                inspect(node, env, prior)
                capture_prefix(env, prior)

        def merge(env, prior, branches):
            for name in set(env).union(*(branch[0] for branch in branches)):
                values = [value for branch, _ in branches
                          for value in branch.get(name, [ast.Name(id=name, ctx=ast.Load())])]
                env[name] = list({ast.dump(value, include_attributes=False): value
                                  for value in values}.values())
            prior.update(*(branch[1] for branch in branches))

        def irrefutable(pattern):
            if isinstance(pattern, ast.MatchAs):
                return pattern.pattern is None or irrefutable(pattern.pattern)
            return isinstance(pattern, ast.MatchOr) and any(
                irrefutable(alternative) for alternative in pattern.patterns)

        def alternatives(pattern):
            while isinstance(pattern, ast.MatchAs) and pattern.pattern is not None:
                pattern = pattern.pattern
            if isinstance(pattern, ast.MatchOr):
                return [leaf for option in pattern.patterns for leaf in alternatives(option)]
            return [pattern]

        def disjoint(left, right):
            # Only identity patterns prove exclusion: once a singleton matched,
            # value patterns compare against a builtin, never a custom __eq__.
            def excludes(a, b):
                if not isinstance(a, ast.MatchSingleton):
                    return False
                if isinstance(b, ast.MatchSingleton):
                    return a.value is not b.value
                if isinstance(b, ast.MatchValue):
                    try:
                        return a.value != ast.literal_eval(b.value)
                    except (ValueError, TypeError):
                        return False
                return False
            return all(excludes(a, b) or excludes(b, a)
                       for a in alternatives(left) for b in alternatives(right))

        def walk(statements, env, prior):
            for node in statements:
                capture_prefix(env, prior)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(node, ast.If):
                    expression(node.test, env, prior)
                    branches = []
                    for body in (node.body, node.orelse):
                        branch_env, branch_seen = dict(env), set(prior)
                        walk(body, branch_env, branch_seen)
                        branches.append((branch_env, branch_seen))
                    merge(env, prior, branches)
                elif isinstance(node, ast.Match):
                    expression(node.subject, env, prior)
                    branches = []
                    exhaustive = False
                    # A false guard continues after its calls ran, but only to
                    # cases its subject can still match; a selected body cannot.
                    failed_guards = []
                    for case in node.cases:
                        branch_env = dict(env)
                        branch_seen = set(prior).union(*(calls for pattern, calls in failed_guards
                                                         if not disjoint(pattern, case.pattern)))
                        for pattern in ast.walk(case.pattern):
                            name = (pattern.name if isinstance(pattern, (ast.MatchAs, ast.MatchStar))
                                    else pattern.rest if isinstance(pattern, ast.MatchMapping) else None)
                            if name:
                                branch_env.pop(name, None)
                        if case.guard is not None:
                            before = set(branch_seen)
                            expression(case.guard, branch_env, branch_seen)
                            failed_guards.append((case.pattern, branch_seen - before))
                        walk(case.body, branch_env, branch_seen)
                        branches.append((branch_env, branch_seen))
                        exhaustive |= case.guard is None and irrefutable(case.pattern)
                    if not exhaustive:
                        branches.append((dict(env), set(prior).union(
                            *(calls for _, calls in failed_guards))))
                    merge(env, prior, branches)
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                    expression(node.iter if hasattr(node, 'iter') else node.test, env, prior)
                    branch_env, branch_seen = dict(env), set(prior)
                    if hasattr(node, 'target'):
                        for name in ast.walk(node.target):
                            if isinstance(name, ast.Name):
                                branch_env.pop(name.id, None)
                    walk(node.body, branch_env, branch_seen)
                    merge(env, prior, [(dict(env), set(prior)), (branch_env, branch_seen)])
                    walk(node.orelse, env, prior)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    if node.value is not None:
                        expression(node.value, env, prior)
                        value = node.value
                        is_request = isinstance(value, ast.Call) and target(value.func, env) == ('jev_semantic', 'JudgmentRequest')
                        is_loader = (isinstance(value, ast.Call) and value.args
                                     and isinstance(value.args[0], ast.Constant)
                                     and value.args[0].value in {'jev_semantic', 'jev_judge', 'typesafe_client'})
                        try:
                            ast.literal_eval(value)
                            is_literal = True
                        except (ValueError, TypeError):
                            is_literal = False
                        # Closed literals have stable state identity. Keep other
                        # data expressions opaque to bound expansion on reuse.
                        values = resolve(value, env) if isinstance(value, ast.Name) or is_request or is_loader or is_literal else None
                        for name in node.targets if isinstance(node, ast.Assign) else [node.target]:
                            if isinstance(name, ast.Name):
                                env[name.id] = values or [ast.Name(id=f'{name.id}@{node.lineno}', ctx=ast.Load())]
                elif isinstance(node, (ast.Try, ast.TryStar)):
                    prefixes = [(dict(env), set(prior))]
                    success_env, success_seen = dict(env), set(prior)
                    exception_collectors.append(prefixes)
                    walk(node.body, success_env, success_seen)
                    exception_collectors.pop()
                    handler_env, handler_seen = {}, set()
                    merge(handler_env, handler_seen, prefixes)
                    branches = []
                    for handler in node.handlers:
                        branch_env, branch_seen = dict(handler_env), set(handler_seen)
                        if handler.type is not None:
                            expression(handler.type, branch_env, branch_seen)
                        if handler.name:
                            branch_env.pop(handler.name, None)
                        walk(handler.body, branch_env, branch_seen)
                        if handler.name:
                            branch_env.pop(handler.name, None)
                        branches.append((branch_env, branch_seen))
                        if isinstance(node, ast.TryStar):
                            merge(handler_env, handler_seen, [(handler_env, handler_seen),
                                                             (branch_env, branch_seen)])
                    walk(node.orelse, success_env, success_seen)
                    branches.append((success_env, success_seen))
                    merge(env, prior, branches)
                    if node.finalbody:
                        # Unhandled exceptions also execute finally, possibly
                        # before a later try assignment replaces a request.
                        merge(env, prior, [(dict(env), set(prior)), *prefixes])
                    walk(node.finalbody, env, prior)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        expression(item.context_expr, env, prior)
                    walk(node.body, env, prior)
                else:
                    expression(node, env, prior)
                capture_prefix(env, prior)
        walk(scope.body, bindings, seen)
    return sorted(set(errors))

def javascript_errors(source):
    """Require the pinned cache seam; detect repeated request variables in a function.

    This deliberately bounded source check does not prove alias equivalence.
    Runtime tests cover the complete-input key and single-flight semantics.
    """
    if not re.search(r'typesafe|askJev|cachedSemanticAsk', source):
        return []
    errors=[]
    for match in re.finditer(r'\b(?:askJev|typesafe\.ask|jev\.ask)\s*\(', source):
        line=source.count('\n',0,match.start())+1
        errors.extend([f'{line}: model: use the pinned semantic seam',
                       f'{line}: cache: use cachedSemanticAsk'])
    # A request variable repeats unchanged. New evidence must construct a new
    # request, and all questions against one variable belong in its one batch.
    seen=set()
    functions=list(re.finditer(r'\bfunction\s+(\w+)\s*\(',source))
    for match in re.finditer(r'cachedSemanticAsk\(\s*\w+\s*,\s*(\w+)\s*,',source):
        scope=next((f.start() for f in reversed(functions) if f.start()<match.start()),0)
        key=(scope,match.group(1))
        if key in seen:
            errors.append(f'{source.count(chr(10),0,match.start())+1}: fanout: combine request questions')
        seen.add(key)
    return errors

def scan(root=ROOT):
    legacy = json.loads(BASELINE.read_text())
    paths = subprocess.check_output(['git','ls-files','*.py','*.js','*.mjs'],cwd=root,text=True).splitlines()
    errors=[]
    for rel in paths:
        # Match CI's hyphen and underscore test-suite conventions as well as
        # test directories. Cache/failure probes intentionally repeat requests.
        if (any(x in rel for x in ('selftest','/tests/','/fixtures/','.test.'))
                or rel.startswith('tests/')
                or pathlib.PurePosixPath(rel).name.startswith(('test-', 'test_'))):
            continue
        source=(root/rel).read_text(errors='replace')
        if rel in legacy and hashlib.sha256(source.encode()).hexdigest()==legacy[rel]:
            continue
        if rel in {'ops/typesafe_client.py','ops/jev_judge.py','ops/jev_semantic.py','tools/judge/interface.py','mcp-server/src/jev-call-receipt.js','mcp-server/src/jev-semantic.js'}:
            continue # budget/credential transport, audited in its own lane
        if rel.endswith('.py'):
            errors += [rel+':'+e for e in python_errors(source)]
        else:
            errors += [rel+':'+e for e in javascript_errors(source)]
    return errors

if __name__ == '__main__':
    errors=scan()
    print('\n'.join(errors) if errors else 'OK Jev conformance: pinned cached requests; on breach: owner orchestrator must combine questions and use semantic seam')
    sys.exit(bool(errors))
