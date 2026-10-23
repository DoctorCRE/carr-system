"""Decode literal native shell tool calls without interpreting JavaScript."""

import ast
import re

class UninspectableCommand(ValueError):
    pass

def native_commands(code):
    tokens = re.findall(r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`|[A-Za-z_$][\w$]*|[^\s]''', code)
    tokens = [token for token in tokens if not token.startswith(('//', '/*'))]
    commands = []
    for i, token in enumerate(tokens):
        if token == 'exec_command':
            argument = i + 1
        elif token in ('"exec_command"', "'exec_command'", '`exec_command`') and i and tokens[i - 1] == '[':
            if tokens[i + 1:i + 2] != [']']:
                raise UninspectableCommand('Dynamic native tool names cannot be inspected.')
            argument = i + 2
        else:
            continue
        if tokens[argument:argument + 2] == ['?', '.']:
            argument += 2
        if tokens[argument:argument + 2] != ['(', '{']:
            raise UninspectableCommand('Native exec_command requires an inspectable object argument.')
        depth, values = 1, []
        for j in range(argument + 2, len(tokens)):
            current = tokens[j]
            if depth == 1 and current in ('.', '['):
                raise UninspectableCommand('Spread or computed native command arguments cannot be inspected.')
            if depth == 1 and current in ('cmd', '"cmd"', "'cmd'") and tokens[j + 1:j + 2] == [':']:
                literal = tokens[j + 2] if j + 2 < len(tokens) else ''
                if not literal.startswith(('"', "'", '`')) or tokens[j + 3:j + 4] not in ([','], ['}']):
                    raise UninspectableCommand('Dynamic native commands cannot be inspected; use a literal cmd.')
                if literal.startswith('`'):
                    if '${' in literal or '\\' in literal:
                        raise UninspectableCommand('Interpolated or escaped native templates cannot be inspected; use a plain literal cmd.')
                    values.append(literal[1:-1])
                else:
                    if re.search(r'''\\(?![\\'"abfnrtv0-7xuU\n])''', literal):
                        raise UninspectableCommand('Unsupported JavaScript string escape in native command.')
                    try:
                        values.append(ast.literal_eval(literal))
                    except (ValueError, SyntaxError):
                        raise UninspectableCommand('Unsupported native command literal.') from None
            if current in ('{', '[', '('):
                depth += 1
            elif current in ('}', ']', ')'):
                depth -= 1
            if depth == 0:
                break
        if depth != 0 or len(values) != 1:
            raise UninspectableCommand('Native exec_command requires exactly one literal cmd.')
        commands.extend(values)
    return '\n'.join(commands)
