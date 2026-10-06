#!/usr/bin/env python3
"""Hermetic tests for tools/store-token.py.

No network, no real getpass prompt, no real clipboard. Loaded by file path
(same pattern as ops/rotate-credential-mint-selftest.py) because the module
lives under a hyphenated filename.

What this asserts, per the build's own requirements:
  * the secret value never appears in stdout, stderr, or any file other than
    the target env file
  * the target file ends up mode 0600 (parent directory 0700)
  * lines for other keys already in the file survive untouched
  * an unknown NAME is refused, and nothing is written for it
  * verify runs BEFORE the write: a failed verify writes nothing and leaves
    any existing line for that NAME byte-for-byte untouched (2026-09-24 fix,
    after live use saved a value Cloudflare's verify endpoint had rejected)
  * the clipboard is cleared only after a save that actually happened, never
    on a refused empty value, an unknown NAME, or a failed verify
  * a refusal for a NAME with a known shape (Cloudflare, GitHub) carries a
    length/character-class hint, never the value itself
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import urllib.error
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("store_token", REPO / "tools" / "store-token.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("store-token-selftest: cannot load tools/store-token.py")
st: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(st)

FAILURES: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


SECRET = "s3cr3t-value-should-never-leak-9f8e7d"  # ci-secret-scan: allow — hermetic fixture


@contextlib.contextmanager
def scratch_env_file():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "tokens.env")


def _run(name: str, secret: str, verify=None):
    """Run store_token with a fake prompt/clipboard, capturing stdout/stderr."""
    out, err = io.StringIO(), io.StringIO()
    clipboard_calls = []

    def fake_prompt(_msg: str) -> str:
        return secret

    def fake_clear_clipboard() -> None:
        clipboard_calls.append(True)

    rc = st.store_token(
        name,
        prompt=fake_prompt,
        do_clear_clipboard=fake_clear_clipboard,
        out=out,
        err=err,
    )
    return rc, out.getvalue(), err.getvalue(), clipboard_calls


def test_writes_target_and_never_leaks_value():
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (True, "verified active"),
            }
            rc, out, err, clipboard_calls = _run("CLOUDFLARE_API_TOKEN", SECRET)
            check("exit code 0 on success", rc == 0)
            check("clipboard cleared once", clipboard_calls == [True])
            check("secret absent from stdout", SECRET not in out)
            check("secret absent from stderr", SECRET not in err)
            check("success message names the file, not the value",
                  target in out and "verified active" in out)

            with open(target, encoding="utf-8") as fh:
                content = fh.read()
            check("secret IS present in the target file", SECRET in content)
            check("target file has exactly one CLOUDFLARE_API_TOKEN line",
                  content.count("CLOUDFLARE_API_TOKEN=") == 1)

            mode = stat.S_IMODE(os.stat(target).st_mode)
            check("target file mode is 0600", mode == 0o600)
            parent_mode = stat.S_IMODE(os.stat(os.path.dirname(target)).st_mode)
            check("parent directory mode is 0700", parent_mode == 0o700)
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_other_lines_preserved_and_replace_is_exact():
    with scratch_env_file() as target:
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("# a comment\nGITHUB_FINE_GRAINED_TOKEN=old-github-value\n"
                      "CLOUDFLARE_API_TOKEN=stale-should-be-replaced\n")
        os.chmod(target, 0o600)

        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (True, "verified active"),
            }
            rc, out, err, _clip = _run("CLOUDFLARE_API_TOKEN", SECRET)
            check("exit code 0", rc == 0)

            with open(target, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            check("comment line preserved", "# a comment" in lines)
            check("unrelated key's line preserved verbatim",
                  "GITHUB_FINE_GRAINED_TOKEN=old-github-value" in lines)
            check("target key line replaced, not duplicated",
                  lines.count(f"CLOUDFLARE_API_TOKEN={SECRET}") == 1)
            check("stale value gone", "stale-should-be-replaced" not in lines)
            check("still exactly 3 lines", len(lines) == 3)
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_unknown_name_refused_and_writes_nothing():
    with scratch_env_file() as target:
        rc, out, err, clipboard_calls = _run("NOT_A_REAL_TOKEN_NAME", SECRET)
        check("unknown NAME refused with nonzero exit", rc != 0)
        check("unknown NAME error mentions the allow-list",
              "Allowed:" in err)
        check("clipboard not touched for a refused name", clipboard_calls == [])
        check("nothing written for an unknown name", not os.path.exists(target))
        check("secret absent from stdout on refusal", SECRET not in out)
        check("secret absent from stderr on refusal", SECRET not in err)


def test_empty_value_refused():
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (True, "verified active"),
            }
            rc, out, err, clipboard_calls = _run("CLOUDFLARE_API_TOKEN", "   ")
            check("empty (whitespace-only) value refused", rc != 0)
            check("nothing written for an empty value", not os.path.exists(target))
            check("clipboard not touched when refused", clipboard_calls == [])
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_verify_runs_before_write_and_writes_nothing_on_failure():
    """The 2026-09-24 live-use bug: a value Cloudflare's verify rejected (HTTP
    400) had already been written by the time the probe ran. Verify must run
    BEFORE the write, and a failed verify must leave the target file exactly
    as it was -- untouched, not just "still holding the old value by luck"."""
    with scratch_env_file() as target:
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("CLOUDFLARE_API_TOKEN=still-the-old-working-value\n")
        os.chmod(target, 0o600)
        with open(target, "rb") as fh:
            before_bytes = fh.read()
        before_mtime_ns = os.stat(target).st_mtime_ns

        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (False, "Cloudflare verify failed (HTTP 400, result.status=None)"),
                "shape_hint": st.cloudflare_shape_hint,
            }
            rc, out, err, clipboard_calls = _run("CLOUDFLARE_API_TOKEN", SECRET)
            check("verify failure is exit 1", rc == 1)
            with open(target, "rb") as fh:
                after_bytes = fh.read()
            check("nothing new was written: file bytes unchanged",
                  after_bytes == before_bytes)
            check("nothing new was written: mtime unchanged",
                  os.stat(target).st_mtime_ns == before_mtime_ns)
            check("the old value is still exactly what it was",
                  "CLOUDFLARE_API_TOKEN=still-the-old-working-value" in before_bytes.decode())
            check("the refused value never landed in the file", SECRET not in before_bytes.decode())
            check("failure message on stderr, not the value", SECRET not in err and "HTTP 400" in err)
            check("clipboard NOT cleared on a failed verify", clipboard_calls == [])
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_clipboard_cleared_only_after_a_verified_save():
    """The clipboard used to clear unconditionally -- wiping Joe's clipboard
    on a refused empty paste or a failed verify, exactly when he still needs
    the value there to retry. It must clear on success and stay untouched on
    every refusal path."""
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (True, "verified active"),
                "shape_hint": st.cloudflare_shape_hint,
            }
            # success: cleared
            _rc, _out, _err, clip_ok = _run("CLOUDFLARE_API_TOKEN", SECRET)
            check("clipboard cleared after a verified save", clip_ok == [True])

            # empty value: not cleared
            _rc, _out, _err, clip_empty = _run("CLOUDFLARE_API_TOKEN", "   ")
            check("clipboard untouched on an empty-value refusal", clip_empty == [])

            # unknown name: not cleared
            _rc, _out, _err, clip_unknown = _run("NOT_A_REAL_TOKEN_NAME", SECRET)
            check("clipboard untouched on an unknown-NAME refusal", clip_unknown == [])

            # failed verify: not cleared
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (False, "Cloudflare verify failed (HTTP 400, result.status=None)"),
                "shape_hint": st.cloudflare_shape_hint,
            }
            _rc, _out, _err, clip_failed = _run("CLOUDFLARE_API_TOKEN", SECRET)
            check("clipboard untouched on a failed verify", clip_failed == [])
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_shape_hint_on_refusal_never_echoes_value():
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (False, "Cloudflare verify failed (HTTP 400, result.status=None)"),
                "shape_hint": st.cloudflare_shape_hint,
            }
            # A 37-char value containing a space and a slash -- exercises both
            # the length mismatch and the "contains ..." clause together, the
            # exact combination the build called out by name.
            bad_value = "abc def/ghijklmnopqrstuvwxyz01234567"
            _rc, _out, err, _clip = _run("CLOUDFLARE_API_TOKEN", bad_value)
            check("hint states the expected length and charset",
                  "expected 40 characters of letters, digits, _ or -" in err)
            check("hint states the actual length",
                  f"got {len(bad_value)} characters" in err)
            check("hint names the shape problem", "contains spaces/slashes" in err)
            check("the bad value itself never appears in the refusal", bad_value not in err)

            # A clean-length value with no shape violations gets no "contains" clause.
            st.CREDENTIALS["CLOUDFLARE_API_TOKEN"] = {
                "target": target,
                "verify": lambda v: (False, "Cloudflare verify failed (HTTP 400, result.status=None)"),
                "shape_hint": st.cloudflare_shape_hint,
            }
            clean_40 = "a" * 40
            _rc2, _out2, err2, _clip2 = _run("CLOUDFLARE_API_TOKEN", clean_40)
            check("a well-shaped-length value gets no spurious 'contains' clause",
                  "contains" not in err2)
            check("its hint still reports the (matching) length", "got 40 characters" in err2)

            # Empty-value refusal also carries the hint (0 characters, no charset issue).
            _rc3, _out3, err3, _clip3 = _run("CLOUDFLARE_API_TOKEN", "  ")
            check("empty-value refusal also carries a shape hint", "got 0 characters" in err3)
            check("empty-value hint has no spurious 'contains' clause", "contains" not in err3)
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_github_shape_hint_flags_missing_prefix_and_spaces():
    hint = st.github_shape_hint("has a space no-prefix-here")
    check("github hint flags spaces", "spaces" in hint)
    check("github hint flags the missing github_pat_ prefix", "github_pat_" in hint)
    check("github hint reports length", f"got {len('has a space no-prefix-here')} characters" in hint)


def test_claude_oauth_token_has_no_shape_hint():
    """CLAUDE_CODE_OAUTH_TOKEN has no known shape -- its refusal (e.g. an
    unknown NAME test elsewhere covers unknown names; this covers an empty
    value for a real, shape-hint-less entry) must not fabricate one."""
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLAUDE_CODE_OAUTH_TOKEN"] = {
                "target": target, "verify": None, "shape_hint": None,
            }
            _rc, _out, err, _clip = _run("CLAUDE_CODE_OAUTH_TOKEN", "   ")
            check("no parenthetical hint is invented for a shape-hint-less NAME",
                  "(" not in err)
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_claude_oauth_token_has_no_verify_probe():
    with scratch_env_file() as target:
        original = dict(st.CREDENTIALS)
        try:
            st.CREDENTIALS["CLAUDE_CODE_OAUTH_TOKEN"] = {"target": target, "verify": None}
            rc, out, err, _clip = _run("CLAUDE_CODE_OAUTH_TOKEN", SECRET)
            check("presence-only credential still exits 0", rc == 0)
            check("presence-only message says no probe configured",
                  "no verify probe configured" in out)
            landed = False
            if os.path.exists(target):
                with open(target, encoding="utf-8") as fh:
                    landed = SECRET in fh.read()
            check("value still landed in the file", landed)
        finally:
            st.CREDENTIALS.clear()
            st.CREDENTIALS.update(original)


def test_http_status_helper_never_raises_on_http_error_status():
    """verify_cloudflare/verify_github read a status code, including 4xx/5xx,
    without the tool crashing -- only a genuine network failure should raise
    out of _http_status, and that is caught by the verify_* wrappers too."""

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self, code: int, body: bytes):
            super().__init__("https://example.invalid", code, "err", {}, None)  # type: ignore[arg-type]
            self._body = body

        def read(self) -> bytes:  # type: ignore[override]
            return self._body

    def fake_urlopen_cloudflare(_req, timeout=15):
        raise _FakeHTTPError(403, json.dumps({"result": {"status": "inactive"}}).encode())

    def fake_urlopen_github(_req, timeout=15):
        raise _FakeHTTPError(401, b"{}")

    import urllib.request as _urlreq

    original_urlopen = _urlreq.urlopen
    try:
        _urlreq.urlopen = fake_urlopen_cloudflare  # type: ignore[assignment]
        ok, message = st.verify_cloudflare(SECRET)
        check("cloudflare verify: 403/inactive reports failure, not a crash", ok is False)
        check("cloudflare verify: failure message excludes the secret", SECRET not in message)

        _urlreq.urlopen = fake_urlopen_github  # type: ignore[assignment]
        ok2, message2 = st.verify_github(SECRET)
        check("github verify: 401 reports failure, not a crash", ok2 is False)
        check("github verify: failure message excludes the secret", SECRET not in message2)
    finally:
        _urlreq.urlopen = original_urlopen  # type: ignore[assignment]


def main() -> int:
    test_writes_target_and_never_leaks_value()
    test_other_lines_preserved_and_replace_is_exact()
    test_unknown_name_refused_and_writes_nothing()
    test_empty_value_refused()
    test_verify_runs_before_write_and_writes_nothing_on_failure()
    test_clipboard_cleared_only_after_a_verified_save()
    test_shape_hint_on_refusal_never_echoes_value()
    test_github_shape_hint_flags_missing_prefix_and_spaces()
    test_claude_oauth_token_has_no_shape_hint()
    test_claude_oauth_token_has_no_verify_probe()
    test_http_status_helper_never_raises_on_http_error_status()

    if FAILURES:
        print(f"\nstore-token-selftest: {len(FAILURES)} failure(s)", file=sys.stderr)
        return 1
    print("\nstore-token-selftest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
