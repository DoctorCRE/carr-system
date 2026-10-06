"""Reject newly introduced unpinned, uncached or split-state Jev calls.

Existing sibling-owned callers are baseline debt, bound to exact source hashes.
Changed/new calls must use the semantic seam or explicitly declare a pinned
model and cache key. This is a source check, not proof of runtime equivalence.
"""
import ast
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
        aliases = {name for name, (module, method) in imports.items()
                   if module in {'typesafe_client', 'jev_judge', 'jev_semantic'}
                   and method in {'ask', 'judge', '_ask_jev', 'server_ask'}}
        semantic_aliases = {name for name, value in imports.items() if value == ('jev_semantic', 'ask')}
        semantic_modules = {name for name, value in imports.items() if value == ('jev_semantic', None)}
        seen = set()
        local_nodes = list(scope_nodes(scope))
        nodes = [n for n in local_nodes if isinstance(n,ast.Call)]
        for n in nodes:
            method = n.func.attr if isinstance(n.func,ast.Attribute) else getattr(n.func,'id','')
            if not ((isinstance(n.func,ast.Attribute) and method in {'ask','judge','_ask_jev','server_ask'}) or method in aliases):
                continue
            state = ast.dump(n.args[0], include_attributes=False) if n.args else ast.dump(next((k.value for k in n.keywords if k.arg == 'state'),ast.Constant(None)))
            # A loop over question subsets is fan-out even with one source
            # call expression. A state derived inside that loop is new evidence.
            state_node = n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == 'state'), ast.Constant(None))
            state_names = {v.id for v in ast.walk(state_node) if isinstance(v, ast.Name)}
            for loop in (v for v in local_nodes if isinstance(v, (ast.For, ast.AsyncFor, ast.While))):
                if not any(v is n for child in loop.body for v in scope_nodes(child)):
                    continue
                changed_names = {v.id for child in loop.body for v in scope_nodes(child)
                                 if isinstance(v, ast.Name) and isinstance(v.ctx, ast.Store)}
                if isinstance(loop, (ast.For, ast.AsyncFor)):
                    changed_names |= {v.id for v in ast.walk(loop.target) if isinstance(v, ast.Name)}
                if not state_names & changed_names:
                    errors.append(f'{n.lineno}: fanout: loop repeats unchanged state')
            if state in seen:
                errors.append(f'{n.lineno}: fanout: combine all questions for this state')
            seen.add(state)
            semantic_call = (isinstance(n.func, ast.Name) and method in semantic_aliases) or (
                isinstance(n.func, ast.Attribute) and (
                    (isinstance(n.func.value, ast.Name) and n.func.value.id in semantic_modules)
                    or (isinstance(n.func.value, ast.Name) and n.func.value.id not in imports
                        and 'semantic' in n.func.value.id)
                    or (not isinstance(n.func.value, ast.Name) and 'semantic' in ast.unparse(n.func.value))))
            if semantic_call:
                kw = {k.arg:k.value for k in n.keywords}
                if not {'caller','version'} <= kw.keys():
                    errors.append(f'{n.lineno}: cache: semantic call needs caller/version')
                continue
            kw = {k.arg:k.value for k in n.keywords}
            model = kw.get('model')
            if not isinstance(model,ast.Constant) or model.value != 'jev-1.13.0':
                errors.append(f'{n.lineno}: model: pin jev-1.13.0')
            if 'cache_key' not in kw:
                errors.append(f'{n.lineno}: cache: use jev_semantic.ask with complete input key')
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
