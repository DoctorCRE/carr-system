#!/usr/bin/env python3
"""Unit proof for tools/credential_env.py — the shared long-lived-token loader
every unattended `claude -p` launcher in this repo uses.

Covers: reading only the requested keys, refusing a too-loose file mode,
being absent-safe (missing file / missing key), and that the merged child
env carries the token without ever touching os.environ itself.

Run:  python3 tools/test_credential_env.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import credential_env  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


def _write_tokens(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text)
    path.chmod(mode)


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="credential-env-test-")
    root = Path(tmp.name)

    def reads_only_the_requested_keys():
        path = root / "tokens-a.env"
        _write_tokens(
            path,
            "CLAUDE_CODE_OAUTH_TOKEN=sk-not-a-real-secret\n"
            "SOME_OTHER_TOKEN=also-not-real\n"
            "# a comment\n\n",
        )
        got = credential_env.load_carr_tokens(["CLAUDE_CODE_OAUTH_TOKEN"], path=path)
        assert got == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-not-a-real-secret"}, got
        assert "SOME_OTHER_TOKEN" not in got, got

    check("reads only the requested keys, nothing else in the file leaks out",
          reads_only_the_requested_keys)

    def literal_tokens_reach_the_child_unchanged():
        path = root / "tokens-literal.env"
        for source, expected in (("left#right # comment", "left#right"),
                                 (r'"left\$right"', "left$right")):
            _write_tokens(path, f"CLAUDE_CODE_OAUTH_TOKEN={source}\n")
            got = credential_env.load_carr_tokens(["CLAUDE_CODE_OAUTH_TOKEN"], path=path)
            assert got == {"CLAUDE_CODE_OAUTH_TOKEN": expected}, got
            env, warning = credential_env.claude_child_env({"PATH": "/usr/bin"}, path=path)
            assert warning is None, warning
            assert env["CLAUDE_CODE_OAUTH_TOKEN"] == expected, env

    check("literal hashes and escaped dollars reach the child unchanged",
          literal_tokens_reach_the_child_unchanged)

    def refuses_a_644_file():
        path = root / "tokens-loose.env"
        _write_tokens(path, "CLAUDE_CODE_OAUTH_TOKEN=sk-not-a-real-secret\n", mode=0o644)
        try:
            credential_env.load_carr_tokens(["CLAUDE_CODE_OAUTH_TOKEN"], path=path)
        except credential_env.TokensFilePermissionError as e:
            assert "0o644" in str(e) or "644" in str(e), str(e)
            assert "sk-not-a-real-secret" not in str(e), "value leaked into the error message"
        else:
            raise AssertionError("a 644 tokens file was read instead of refused")

    check("refuses a file whose mode is looser than 600", refuses_a_644_file)

    def a_stricter_mode_is_fine():
        path = root / "tokens-strict.env"
        _write_tokens(path, "CLAUDE_CODE_OAUTH_TOKEN=sk-not-a-real-secret\n", mode=0o400)
        got = credential_env.load_carr_tokens(["CLAUDE_CODE_OAUTH_TOKEN"], path=path)
        assert got == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-not-a-real-secret"}, got

    check("a mode stricter than 600 (e.g. 400) is accepted", a_stricter_mode_is_fine)

    def missing_file_is_absent_safe():
        got = credential_env.load_carr_tokens(
            ["CLAUDE_CODE_OAUTH_TOKEN"], path=root / "does-not-exist.env")
        assert got == {}, got

    check("a missing tokens file returns {} instead of raising",
          missing_file_is_absent_safe)

    def missing_key_is_absent_safe():
        path = root / "tokens-b.env"
        _write_tokens(path, "SOME_OTHER_TOKEN=also-not-real\n")
        got = credential_env.load_carr_tokens(["CLAUDE_CODE_OAUTH_TOKEN"], path=path)
        assert got == {}, got

    check("a key that is not in the file is simply absent from the result",
          missing_key_is_absent_safe)

    def child_env_merges_the_token_without_touching_os_environ():
        path = root / "tokens-c.env"
        _write_tokens(path, "CLAUDE_CODE_OAUTH_TOKEN=sk-not-a-real-secret\n")
        sentinel = "credential-env-test-should-not-appear-globally"
        before = dict(os.environ)
        env, warning = credential_env.claude_child_env({"PATH": "/usr/bin"}, path=path)
        assert warning is None, warning
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-not-a-real-secret", env
        assert env["PATH"] == "/usr/bin", env
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ, \
            "the token leaked into the real process environment"
        assert dict(os.environ) == before, "os.environ was mutated"
        assert sentinel not in os.environ

    check("claude_child_env merges the token into a NEW dict, never into os.environ",
          child_env_merges_the_token_without_touching_os_environ)

    def child_env_is_absent_safe_with_a_warning():
        env, warning = credential_env.claude_child_env(
            {"PATH": "/usr/bin"}, path=root / "does-not-exist.env")
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env, env
        assert warning == "long-lived Claude login not configured", warning

    check("with no token configured, the child env is unchanged and a warning is returned",
          child_env_is_absent_safe_with_a_warning)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"credential-env unit: {len(FAILURES)} FAILED")
        return 1
    print("credential-env unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
