#!/usr/bin/env python3
"""ops/credential-file-selftest.py — the interface test for lib/credential_file.py,
the one Python reader of ~/.config/carr/*.env files.

These files have two consumers: shell jobs that `set -a; . db.env`, and
Python jobs. Every value here is a synthetic fixture written to a temp
directory; no real credential file is opened.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.credential_file import credential, read_env_file  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, got: object = None) -> None:
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"  (got {got!r})"))
    if not cond:
        FAILS.append(name)


def shell_quote(value: str) -> str:
    """The quoting tools/rotate-credential.py writes."""
    return "'" + value.replace("'", "'\\''") + "'"


FIXTURE = "\n".join([
    "# a comment line",
    "",
    "PLAIN=plain-value",
    "SINGLE='postgres://u:p@h/db?sslmode=require&channel_binding=require'",
    "DOUBLE=\"say \\\"hi\\\" now\"",
    "export EXPORTED='exported-value'",
    "ESCAPED=" + shell_quote("it's&fine"),
    "COMMENTED='kept' # trailing comment",
    "EMPTY=",
    "QUOTED_EMPTY=''",
    "  INDENTED=indented-value",
    "REPEATED=first",
    "REPEATED=second",
    "lower_case=lower",
    "1BAD=not-a-name",
    "not an assignment",
]) + "\n"

with tempfile.TemporaryDirectory() as raw:
    path = Path(raw) / "fixture.env"
    path.write_text(FIXTURE, encoding="utf-8")
    values = read_env_file(path)

    check("a plain value", values.get("PLAIN") == "plain-value", values.get("PLAIN"))
    check("a single-quoted DSN keeps its & and loses its quotes",
          values.get("SINGLE") == "postgres://u:p@h/db?sslmode=require&channel_binding=require",
          values.get("SINGLE"))
    check("a double-quoted value honours its escapes", values.get("DOUBLE") == 'say "hi" now',
          values.get("DOUBLE"))
    check("an export prefix names the key, not 'export KEY'",
          values.get("EXPORTED") == "exported-value" and "export EXPORTED" not in values, values)
    check("rotate-credential's own quoting reads back exactly", values.get("ESCAPED") == "it's&fine",
          values.get("ESCAPED"))
    check("a trailing shell comment is not part of the value", values.get("COMMENTED") == "kept",
          values.get("COMMENTED"))
    check("an empty value is present and empty", values.get("EMPTY") == "" and values.get("QUOTED_EMPTY") == "",
          (values.get("EMPTY"), values.get("QUOTED_EMPTY")))
    check("leading whitespace is allowed", values.get("INDENTED") == "indented-value")
    check("the last assignment wins, as in the shell", values.get("REPEATED") == "second")
    check("lower-case names are names", values.get("lower_case") == "lower")
    check("lines that are not assignments set nothing",
          set(values) == {"PLAIN", "SINGLE", "DOUBLE", "EXPORTED", "ESCAPED", "COMMENTED", "EMPTY",
                          "QUOTED_EMPTY", "INDENTED", "REPEATED", "lower_case"}, sorted(values))

    literal_cases = {
        "HASH": ("left#right", "left#right"),
        "LEADING_HASH": ("#right", "#right"),
        "ADJACENT_HASH": ("'left'#right", "left#right"),
        "TRAILING_COMMENT": ("left#right # dropped", "left#right"),
        "EMPTY_COMMENT": (" # dropped", ""),
        "DOLLAR": (r'"left\$right"', "left$right"),
        "BACKTICK": (r'"left\`right"', "left`right"),
        "BACKSLASH": (r'"left\\right"', "left\\right"),
        "OTHER_ESCAPE": (r'"left\qright"', "left\\qright"),
        "ESCAPED_SPACE": (r"left\ right", "left right"),
    }
    literal_path = Path(raw) / "literal.env"
    literal_path.write_text("".join(f"{name}={source}\n" for name, (source, _) in literal_cases.items()))
    literal_values = read_env_file(literal_path)
    for name, (_, expected) in literal_cases.items():
        check(f"literal assignment {name}", literal_values.get(name) == expected, literal_values.get(name))
        for shell_name in ("bash", "zsh"):
            shell_path = shutil.which(shell_name)
            if shell_path:
                shell = subprocess.run(
                    [shell_path, "-c", f'. "$1"; printf %s "${{{name}}}"', "_", str(literal_path)],
                    capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
                check(f"{shell_name} agrees on literal {name}",
                      shell.returncode == 0 and shell.stdout == literal_values.get(name),
                      (shell.stdout, literal_values.get(name)))

    broken = Path(raw) / "broken.env"
    broken.write_text("GOOD=good\nBAD='never closed\nAFTER=after\n", encoding="utf-8")
    got = read_env_file(broken)
    check("an unterminated quote drops only its own key",
          got.get("GOOD") == "good" and "BAD" not in got and got.get("AFTER") == "after", got)

    zsh = shutil.which("zsh") or ("/bin/zsh" if os.path.exists("/bin/zsh") else None)
    if zsh:
        for name, value in values.items():
            shell = subprocess.run(
                [zsh, "-c", f'set -a; . "$1"; printf %s "${{{name}}}"', "_", str(path)],
                capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
            check(f"the shell agrees on {name}", shell.stdout == value, (shell.stdout, value))
    else:
        print("skip  the shell-agreement cases: no zsh on this runner")

    try:
        read_env_file(Path(raw) / "absent.env")
        missing_raised = False
    except OSError:
        missing_raised = True
    check("a missing file raises OSError", missing_raised)

    env = {"PLAIN": "from-environment", "EMPTY_ENV": ""}
    check("credential() prefers the job's environment",
          credential("PLAIN", path=path, environ=env) == "from-environment")
    check("credential() falls back to the file",
          (credential("SINGLE", path=path, environ={}) or "").endswith("require"))
    check("an empty environment value falls through to the file",
          credential("PLAIN", path=path, environ={"PLAIN": ""}) == "plain-value")
    check("an empty value is no credential", credential("EMPTY", path=path, environ={}) is None)
    check("an absent key is no credential", credential("NOPE", path=path, environ={}) is None)
    check("an absent file is no credential", credential("PLAIN", path=Path(raw) / "absent.env", environ={}) is None)
    check("environ defaults to the process environment",
          credential("CREDENTIAL_FILE_SELFTEST_ONLY", path=path) is None)

    home = Path(raw) / "home"
    (home / ".config" / "carr").mkdir(parents=True)
    (home / ".config" / "carr" / "db.env").write_text("FROM_HOME='home-value'\n", encoding="utf-8")
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        check("the default file is db.env under HOME as it is when called",
              credential("FROM_HOME", environ={}) == "home-value")
    finally:
        os.environ["HOME"] = saved_home or ""

if FAILS:
    print(f"credential-file-selftest: {len(FAILS)} FAILED")
    sys.exit(1)
print("credential-file-selftest: all checks passed")
