#!/usr/bin/env python3
"""cmd_text.py — tell a shell COMMAND apart from the prose it carries.

Shared verbatim by hooks/git-writer-gate.py and hooks/staging-attribution-gate.py,
which both pattern-match a Bash command string and both refused prose that merely
DESCRIBED a dangerous command.

WHY IT EXISTS. Three refusals on 2026-08-14, all false:
  * a .gitignore comment explaining why a whole-tree staging command is banned
  * the commit message for a git-writer-gate fix, whose subject IS which git
    commands are dangerous, so it necessarily quotes them
  * the very next commit message, for the fix to that

A heredoc body and a quoted -m message are DATA. The shell hands them to a
program as bytes; it never executes them. Matching them is not caution, it is a
category error — and the effect is perverse: it makes documenting a gate the
hardest thing to do inside that gate's own history, and it teaches whoever hits
it that the gate is noise to be worked around. A gate people learn to route
around has already stopped working.

WHY IT IS SHARED RATHER THAN COPIED. Two gates with two copies of "what counts as
inert" drift, and the drift is silent because each copy still passes its own
tests. That is the same shape as the per-file git scrubs consolidated into
ops/git_env.py hours earlier the same day.

IT FAILS CLOSED. This function decides what a gate is allowed to STOP looking at,
so every ambiguity resolves toward scanning more, not less: a heredoc body is
skipped only from its opening line to a line that is EXACTLY the delimiter, and
an unterminated or malformed heredoc strips nothing at all.
"""
import re
import shlex


SHELL_BOUNDARIES = frozenset({';', '&&', '||', '|', '&', '|&'})
SHELL_REDIRECTS = frozenset({'<', '>', '>>', '>|', '<>', '<&', '>&', '<<', '<<<', '&>', '&>>'})


def shell_tokens(command):
    """Preserve command boundaries; consume continuations and unquoted IO numbers."""
    def normalize(match):
        token = match.group()
        if token == '\\\n':
            return ''
        if token.startswith('"'):
            return token.replace('\\\n', '')
        if token == '\n':
            return ';'
        if token.isdigit():
            return ''
        return token
    command = re.sub(r"'[^']*'|\"(?:\\[\s\S]|[^\"\\])*\"|\\[\s\S]|\n|(?<!\S)\d+(?=[<>])", normalize, command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    return list(lexer)


def shell_operands(tokens):
    """Separate all redirects from command words, retaining file output targets."""
    words, outputs = [], []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in SHELL_REDIRECTS:
            if index + 1 >= len(tokens):
                raise ValueError('missing redirection target')
            target = tokens[index + 1]
            if token in {'>', '>>', '>|', '<>', '&>', '&>>'} or (token == '>&' and not target.isdigit() and target != '-'):
                outputs.append(target)
            index += 2
        else:
            words.append(token)
            index += 1
    return words, outputs

# A heredoc opener: <<EOF, <<-EOF, <<'EOF', <<"EOF".
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# The flags that carry PROSE, and whose quoted argument is therefore data.
#
# `-m/--message` was the original entry. The rest joined it on 2026-08-14 after
# the fourth false refusal in a day: opening a pull request whose --body
# DESCRIBED the very command being fixed
#
#     gh pr create --title "..." --body "...git stash drop <ref>..."
#
# was refused as though it were running one. Same category error the module
# exists to end, arriving through a flag nobody had listed. The workaround —
# writing the body to a file — is exactly the outcome the docstring above warns
# about, because it teaches whoever hits it that the gate is noise.
#
# LONG FORMS ONLY for the additions, deliberately. Single letters collide across
# tools (`-b` is a branch to git and a body elsewhere, `-d` is delete to one and
# description to another), and a collision here would make the gate stop looking
# at a real command. `-m` keeps its short form because it is unambiguous in
# every tool this repo drives.
#
# Unquoted arguments are NOT stripped, for the reason the original comment gave:
# a bare `--body dont-run-git-add-A` is one shell word with no spaces, so it
# cannot hide a command anyway, and matching it loosely would swallow the real
# text after it. An unterminated quote matches nothing and so strips nothing,
# which is the fail-closed direction.
_PROSE_FLAGS = ("-m", "--message", "--body", "--title", "--comment",
                "--notes", "--description", "--subject")
_DASH_M_RE = re.compile(
    r"(?:" + "|".join(re.escape(f) for f in _PROSE_FLAGS) + r")"
    r"\s+(?:'[^']*'|\"[^\"]*\")")


def strip_inert_text(cmd):
    """Return `cmd` with heredoc bodies and quoted messages replaced.

    The replacement keeps the opener and the delimiter line, so anything AFTER
    the heredoc is still scanned — only the body between the markers is inert.
    """
    out = cmd
    for m in _HEREDOC_RE.finditer(cmd):
        delim = m.group(2)
        lines = out.split("\n")
        start = next((i for i, line in enumerate(lines) if m.group(0) in line), None)
        if start is None:
            continue
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].strip() == delim), None)
        if end is None:
            continue  # unterminated — scan the whole thing rather than guess
        body = "\n".join(lines[start + 1:end])
        if not m.group(1) and ("$(" in body or "`" in body):
            continue  # unquoted heredocs execute substitutions
        out = "\n".join(lines[:start + 1] + lines[end:])
    def scrub(match):
        argument = match.group(0)
        quoted = argument.split(None, 1)[1]
        if quoted.startswith('"') and ("$(" in quoted or "`" in quoted):
            return argument
        return "-m <message>"
    return _DASH_M_RE.sub(scrub, out)
