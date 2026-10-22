"""Bounded source expressions for the evidence-only table census. Never execute code."""
import ast
from itertools import product
from math import prod
import re
import warnings

UNKNOWN = '__CENSUS_UNKNOWN__'
RELATION = re.compile(
    r'\b(?:from|join|into|update|table|truncate|references)\s+'
    r'(?:(?:only|if\s+(?:not\s+)?exists)\s+)?([\w$.%{}"-]+)', re.I)
SQL_WORD = re.compile(r'\b(?:select|insert|update|delete|alter|create|truncate|execute)\b', re.I)


def _combine(parts, join=''):
    if not parts or any(not part for part in parts):
        return {UNKNOWN}
    if len(parts) > 16 or prod(map(len, parts)) > 128:
        return {UNKNOWN}
    return {join.join(values) for values in product(*parts)}


def _values(node, bindings, seen=frozenset()):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        return {str(node.value)}
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in bindings:
            return {UNKNOWN}
        return set().union(*(_values(value, bindings, seen | {node.id}) for value in bindings[node.id])) or {UNKNOWN}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_values(value, bindings, seen) for value in node.elts))
    if isinstance(node, ast.Dict):
        return set().union(*(_values(value, bindings, seen) for value in node.keys))
    if isinstance(node, ast.Subscript):
        target = node.value
        if isinstance(target, ast.Name) and target.id not in seen:
            return set().union(*(_values(ast.Subscript(value=value, slice=node.slice), bindings,
                                        seen | {target.id}) for value in bindings.get(target.id, []))) or {UNKNOWN}
        if isinstance(target, (ast.List, ast.Tuple)):
            return _values(target, bindings, seen)
        if isinstance(target, ast.Dict):
            return set().union(*(_values(value, bindings, seen) for value in target.values))
    if isinstance(node, ast.JoinedStr):
        return _combine([_values(value.value if isinstance(value, ast.FormattedValue) else value,
                                 bindings, seen) for value in node.values])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _combine([_values(node.left, bindings, seen), _values(node.right, bindings, seen)])
    if isinstance(node, ast.IfExp):
        return _values(node.body, bindings, seen) | _values(node.orelse, bindings, seen)
    if isinstance(node, ast.Call):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
        arguments = [_values(arg, bindings, seen) for arg in node.args]
        if name in {'SQL', 'quote_ident'} and len(arguments) == 1:
            return arguments[0]
        if name == 'Identifier':
            return _combine(arguments, '.')
        if name == 'format' and isinstance(node.func, ast.Name) and arguments:
            if prod(map(len, arguments)) > 128:
                return {UNKNOWN}
            values = set()
            for args in product(*arguments):
                cursor = 1
                def field(match):
                    nonlocal cursor
                    if match[0] == '%%':
                        return '%'
                    index = int(match[1]) if match[1] else cursor
                    cursor = index + 1
                    return args[index] if 0 < index < len(args) else UNKNOWN
                values.add(re.sub(r'%%|%(?:([0-9]+)\$)?[ILs]', field, args[0]))
            return values
        if name == 'format' and isinstance(node.func, ast.Attribute):
            templates = _values(node.func.value, bindings, seen)
            if len(arguments) > 8 or prod(map(len, arguments)) > 128:
                return {UNKNOWN}
            values = set()
            for template in templates:
                for args in product(*arguments):
                    try:
                        values.add(template.format(*args, **{arg.arg: UNKNOWN for arg in node.keywords if arg.arg}))
                    except (ValueError, KeyError, IndexError):
                        values.add(UNKNOWN)
            return values
    return {UNKNOWN}


def _scope_nodes(scope):
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield child
        else:
            yield child
            yield from _scope_nodes(child)


def python_queries(text):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return [(1, {UNKNOWN})]
    results = []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    mutations = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.attr in {'append', 'extend', 'insert', 'update', 'add', 'setdefault'}):
            index = 1 if node.func.attr == 'insert' else 0
            value = node.args[index] if len(node.args) > index else None
            if value is not None and any(isinstance(child, ast.Name) for child in ast.walk(value)):
                value = None
            mutations.setdefault(node.func.value.id, []).append(value)
    globals_written = {name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names}

    def visit(scope, inherited):
        bindings = dict(inherited)
        nodes = list(_scope_nodes(scope))
        local_names = set()
        for node in nodes:
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        if name.id not in local_names:
                            bindings[name.id] = []
                            local_names.add(name.id)
                        bindings[name.id].append(value if isinstance(target, ast.Name) else None)
        for name, values in mutations.items():
            bindings[name] = bindings.get(name, []) + values
        for name in globals_written:
            bindings[name] = [None]
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for arg in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs,
                        scope.args.vararg, scope.args.kwarg):
                if arg is not None:
                    bindings[arg.arg] = [None]
        selected = set()
        for node in nodes:
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                if (name.lower() in {'query', 'execute', 'executemany', 'sql', 'identifier'}
                        or (name == 'format' and isinstance(node.func, ast.Attribute)
                            and any(SQL_WORD.search(value) for value in _values(node.func.value, bindings)))):
                    selected.add(node)
        def nested(node):
            parent = parents.get(node)
            while parent is not None:
                if parent in selected:
                    return True
                parent = parents.get(parent)
            return False
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                visit(node, bindings)
            elif node in selected and not nested(node):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                if name.lower() in {'query', 'execute', 'executemany'}:
                    query = node.args[0] if node.args else next((arg.value for arg in node.keywords
                                                               if arg.arg in {'query', 'sql', 'operation'}), None)
                    values = _values(query, bindings)
                else:
                    values = _values(node, bindings)
                    if name == 'Identifier':
                        values = {'select * from ' + value for value in values}
                results.append((node.lineno, values))
            elif isinstance(node, ast.JoinedStr) and not nested(node):
                literal = ''.join(str(n.value) for n in node.values if isinstance(n, ast.Constant))
                if SQL_WORD.search(literal):
                    results.append((node.lineno, _values(node, bindings)))
    visit(tree, {})
    return results


def relation_targets(values, tables):
    known = {f"{table['schema']}.{table['name']}" for table in tables}
    referenced, possible = set(), set()
    for value in values:
        matches = list(RELATION.finditer(value))
        outside_relations = RELATION.sub('', value)
        if UNKNOWN in outside_relations:
            possible.update(known)
        for match in matches:
            token = match[1].replace('"', '')
            if UNKNOWN in token or any(char in token for char in '{}$%'):
                pattern = re.escape(token).replace(re.escape(UNKNOWN), '.*')
                if any(char in token for char in '{}$%'):
                    pattern = re.escape(token.split('.')[0]) + r'\..*' if '.' in token else '.*'
                possible.update(name for name in known if re.fullmatch(pattern, name)
                                or ('.' not in token and re.fullmatch(pattern, name.split('.', 1)[1])))
            else:
                referenced.update(name for name in known if name == token or
                                  ('.' not in token and name.split('.', 1)[1] == token))
    return sorted(referenced), sorted(possible)


QUOTED = re.compile(r"`(?:\\.|[^`\\])*`|'(?:''|\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", re.S)


def _expression(text, bindings):
    literals = {}
    def replace(match):
        token = match[0]
        key = '__literal_' + str(len(literals))
        if token.startswith('`'):
            parts, end = [], 0
            for field in re.finditer(r'\$\{([^{}]+)\}', token[1:-1]):
                parts.append({token[1:-1][end:field.start()]})
                parts.append(_expression(field[1], bindings))
                end = field.end()
            parts.append({token[1:-1][end:]})
            values = _combine(parts)
        else:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', SyntaxWarning)
                    values = {ast.literal_eval(token)}
            except (SyntaxError, ValueError):
                values = {token[1:-1].replace("''", "'")}
        literals[key] = [ast.Constant(value=value) for value in values]
        return key
    normalized = QUOTED.sub(replace, text.strip()).replace('||', '+')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)
            node = ast.parse(normalized, mode='eval').body
    except (ValueError, SyntaxError):
        return {UNKNOWN}
    return _values(node, dict(bindings, **literals))


def _expression_end(text, start, stops):
    depth, index = 0, start
    while index < len(text):
        quoted = QUOTED.match(text, index)
        if quoted:
            index = quoted.end()
            continue
        char = text[index]
        if depth == 0 and char in stops:
            break
        if char in '([{':
            depth += 1
        elif char in ')]}':
            if depth == 0:
                break
            depth -= 1
        index += 1
    return index


def other_queries(text, suffix):
    # Mask strings and comments without changing offsets for code-token matching.
    mask = list(text)
    comments = r'|/\*.*?\*/|--[^\n]*' if suffix in {'.sql', '.applescript'} else r'|/\*.*?\*/|//[^\n]*|\#[^\n]*'
    for match in re.finditer(QUOTED.pattern + comments, text, re.S):
        mask[match.start():match.end()] = [' ' if c != '\n' else '\n' for c in match[0]]
    code = ''.join(mask)
    bindings = {}
    sql = suffix == '.sql'
    assignments = (r'\b([A-Za-z_]\w*)(?:\s+(?:text|name|regclass))?\s*:=' if sql else
                   r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*=(?!=|>)')
    definitions = []
    for match in re.finditer(assignments, code):
        end = _expression_end(text, match.end(), ';\n')
        definitions.append((match[1], text[match.end():end]))
    if not sql:
        for match in re.finditer(r'\bfor\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+', code):
            end = _expression_end(text, match.end(), ')')
            definitions.append((match[1], text[match.end():end]))
    if sql:
        for match in re.finditer(r'\bFOREACH\s+(\w+)\s+IN\s+ARRAY\s+ARRAY\s*(?=\[)', code, re.I):
            end = _expression_end(text, match.end() + 1, ']')
            definitions.append((match[1], text[match.end():end + 1]))
    if not sql:
        for match in re.finditer(r'\b(\w+)\.(?:push|unshift|splice)\s*\(', code):
            end = _expression_end(text, match.end(), ')')
            definitions.append((match[1], text[match.end():end]))
    for _ in range(len(definitions) + 1):
        previous = {name: {getattr(node, 'value', None) for node in nodes} for name, nodes in bindings.items()}
        fresh = {}
        for name, expression in definitions:
            fresh.setdefault(name, []).extend(ast.Constant(value=value) for value in _expression(expression, bindings))
        bindings = fresh
        if {name: {getattr(node, 'value', None) for node in nodes} for name, nodes in bindings.items()} == previous:
            break
    # A function parameter cannot be narrowed by an unrelated file-level assignment.
    for match in re.finditer(r'\bfunction\s*\w*\s*\(([^)]*)\)|\(([^)]*)\)\s*=>', code):
        for name in re.findall(r'[A-Za-z_$][\w$]*', match[1] or match[2] or ''):
            bindings[name] = [None]
    for match in re.finditer(r'\b([A-Za-z_$][\w$]*)\s*=>', code):
        bindings[match[1]] = [None]
    candidates, covered = [], []
    pattern = r'\bEXECUTE\b\s*' if sql else r'\b(?:query|execute|executemany)\s*\('
    for match in re.finditer(pattern, code, re.I):
        end = _expression_end(text, match.end(), ';' if sql else ',)')
        expression = text[match.end():end]
        candidates.append((text.count('\n', 0, match.start()) + 1, _expression(expression, bindings)))
        covered.append((match.start(), end))
    for match in QUOTED.finditer(text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        if SQL_WORD.search(match[0]) and RELATION.search(match[0]):
            values = _expression(match[0], bindings)
            values = {re.sub(r'\$[A-Za-z_]\w*', UNKNOWN, value) for value in values}
            candidates.append((text.count('\n', 0, match.start()) + 1, values))
    return candidates
