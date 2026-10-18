#!/usr/bin/env python3
"""codex-hook-smoke-selftest.py — regression fixtures for the Codex hook
smoke (ops/codex-hook-smoke.sh) and the flag that makes it mean anything.

THREE THINGS THIS CHECKS, none of which requires a live Codex run (a real run
takes real wall-clock time and burns real usage, so CI runs this instead):

  1. THE JUDGE FUNCTION. ops/codex_hook_smoke_judge.py's judge() must call
     captured denial text PASS and a curl-error/empty/wrong-host shape FAIL,
     and its diagnose() must tell apart the three ways a run can fail to
     produce a denial. This is the part of the smoke that is a pure function
     and can actually be unit-tested. diagnose() carries the 2026-08-18
     regression: Codex refusing the probe itself was reported as a skipped
     hook, which pointed three nights of investigation at the wrong thing.

  2. SAME INVOCATION HELPER (rule a8c55a47). ops/codex-hook-smoke.sh must
     source bin/council-lib.sh and call one of its run_* functions rather
     than hand-rolling its own `codex exec` invocation — otherwise the smoke
     tests a code path bin/precheck.sh and bin/council.sh do not actually use,
     and a drift between the two would go undetected. Static grep, same
     technique bin/precheck.sh's own --selftest uses to confirm the model
     pins are sourced rather than redeclared.

  3. THE FLAG ITSELF IS PRESENT AT EVERY SANCTIONED SITE. This is the
     regression test for the thing this whole build exists to fix:
     --dangerously-bypass-hook-trust must be on run_codex, run_precheck (both
     in bin/council-lib.sh) and build_codex_command (in
     pipelines/run_codex_review.py). Without it, Codex silently skips every
     PreToolUse hook on an unattended run — verified live 2026-08-14, no
     denial, no warning — so a regression here is a silent regression in
     production, exactly the failure mode this file exists to catch instead.

    .venv/bin/python ops/codex-hook-smoke-selftest.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(REPO)  # ops/ -> repo root

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


# ── 1. the judge function ────────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "codex_hook_smoke_judge", os.path.join(REPO, "ops", "codex_hook_smoke_judge.py")
)
assert _spec and _spec.loader
judge_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge_mod)

BOOT_DENIAL = 'RULE BOOT: this context must read the CARR rules before any other tool. Fetch each missing page'
check('rule-boot denial is not hook_skipped', judge_mod.diagnose(BOOT_DENIAL) == 'boot_gate')
check('boot denial alone never proves guard fired', judge_mod.judge(BOOT_DENIAL) == 'FAIL')

print("judge()")

DENIAL = judge_mod.DENIAL_TEXT

# Loosely modeled on the real live-observed shape (2026-08-14): Codex wraps
# the hook's own stderr reason with its own narration. The judge must find
# the reason regardless of what surrounds it.
CAPTURED_DENIAL = (
    f"I attempted to run the curl command but it was blocked: "
    f"Command blocked by PreToolUse hook: {DENIAL}"
)
check("captured denial text -> PASS",
      judge_mod.judge(CAPTURED_DENIAL) == "PASS")

check("the bare hook reason string alone -> PASS",
      judge_mod.judge(DENIAL) == "PASS")

# The guard appends remediation advice about KNOWN_HOSTS after the sentence
# the judge matches. The real check() output must still read as PASS.
GUARD_FULL = (
    DENIAL + ". Add it to KNOWN_HOSTS if it is legitimate, or — if it is a "
    "client's own domain — check that they carry a practice email and re-run "
    "ops/fetch-allowlist.py."
)
check("the guard's full output including remediation advice -> PASS",
      judge_mod.judge(GUARD_FULL) == "PASS")

CURL_ERROR = (
    f"curl: (6) Could not resolve host: {judge_mod.PROBE_HOST}"
)
check("curl resolution error (hook silently skipped) -> FAIL",
      judge_mod.judge(CURL_ERROR) == "FAIL")

check("empty output -> FAIL", judge_mod.judge("") == "FAIL")

# A plain, unrelated model answer (e.g. it refused for an unrelated reason,
# or answered without ever invoking the shell tool) must not be mistaken for
# a guard block just because it is non-empty.
UNRELATED = "I'm not able to help with that request."
check("an unrelated non-empty answer without the denial text -> FAIL",
      judge_mod.judge(UNRELATED) == "FAIL")

# A denial naming a DIFFERENT host is not this probe's denial. Without this,
# any unrelated blocked send anywhere in the transcript would read as a pass.
OTHER_HOST = ("network send to an unrecognised host (example.com) "
              "— blocked by the CARR unattended guard")
check("a denial naming a different host -> FAIL",
      judge_mod.judge(OTHER_HOST) == "FAIL")


# ── 1b. diagnose(): the three non-PASS shapes are told apart ────────────
# THE REGRESSION THIS GUARDS. On 2026-08-18/21/22 the smoke read FAIL three
# nights running because Codex refused the probe on its own (rule 9ff56260)
# and never issued a shell call. The message said a hook had been skipped,
# which sent the reader looking at a guard that was working fine. These two
# outcomes must never again be reported as the same thing.
print("\ndiagnose()")

check("a real denial -> pass", judge_mod.diagnose(CAPTURED_DENIAL) == "pass")
check("empty output -> no_output", judge_mod.diagnose("") == "no_output")
check("whitespace-only output -> no_output", judge_mod.diagnose("   \n ") == "no_output")

SELF_REFUSAL = (
    "Rules loaded: 171 shared, 33 joe-personal\n\nI can't issue that command "
    "because the binding standing rules prohibit commands that name "
    "private-key paths."
)
check("Codex refusing before any tool call -> self_refusal",
      judge_mod.diagnose(SELF_REFUSAL) == "self_refusal",
      "this is the exact 2026-08-18 shape; misreporting it as hook_skipped is the bug")

check("a curl error after a real tool call -> hook_skipped",
      judge_mod.diagnose(CURL_ERROR) == "hook_skipped")


# ── 2. same invocation helper as the automation (rule a8c55a47) ─────────
print("\nsame invocation helper as the automation")

smoke_path = os.path.join(REPO, "ops", "codex-hook-smoke.sh")
check("ops/codex-hook-smoke.sh exists", os.path.exists(smoke_path))
smoke_src = open(smoke_path).read() if os.path.exists(smoke_path) else ""

check("sources bin/council-lib.sh (the one home for codex invocations)",
      'source "$REPO/bin/council-lib.sh"' in smoke_src or "source \"$REPO/bin/council-lib.sh\"" in smoke_src)
check("calls a council-lib.sh run_* function rather than a bare `codex exec`",
      ("run_precheck " in smoke_src or "run_codex " in smoke_src))
# Comments are allowed to MENTION "codex exec" (this file's own header
# explains the whole bug in terms of it) — what must never appear is an
# executable line actually invoking it directly. Strip comment lines first,
# same technique bin/precheck.sh's own --selftest uses on itself.
_smoke_code_only = "\n".join(
    ln for ln in smoke_src.splitlines() if not ln.strip().startswith("#")
)
check("never hand-rolls its own `codex exec` invocation on an executable line",
      "codex exec" not in _smoke_code_only)

# The prompt must never be built as a literal command-line argument to codex —
# it goes into a file, and the probe path must never be typed as a bare
# argument on this script's own command lines either (belt-and-suspenders:
# this repo's own Bash guard would refuse a Claude Code session that typed a
# private-key-shaped path directly into a shell command).
check("builds the prompt into a file rather than inlining it on a command line",
      "PROMPT_FILE" in smoke_src and "cat > \"$PROMPT_FILE\"" in smoke_src)

# The probe host has ONE home (ops/codex_hook_smoke_judge.py). If the shell
# hardcoded its own copy, the judge and the probe could drift apart and the
# smoke would match a denial for a host it never sent to.
check("reads the probe host from the judge module rather than hardcoding it",
      "codex_hook_smoke_judge" in smoke_src and "PROBE_HOST" in smoke_src)

check('probe instructions fetch every boot page before issuing probe',
      '"detail":"boot","page":1' in smoke_src and 'pages_total' in smoke_src
      and smoke_src.index('First satisfy the CARR rule-boot gate') < smoke_src.index('After boot is complete, run exactly'))
check('shell reports boot_gate separately', 'boot_gate)' in smoke_src)

# Before spending a live Codex run the smoke asks the guard whether it still
# refuses the probe. Without this, adding the host to KNOWN_HOSTS would turn
# the smoke into a test that quietly proves nothing.
check("asserts the guard still blocks the probe before spending a live run",
      "guard-unattended.py" in smoke_src and "GUARD_VERDICT" in smoke_src)

# A codex-less machine (e.g. before Dell's own install) must SKIP (exit 78,
# bin/nightly.sh's step() convention), never FAIL — same reasoning as every
# other credential/tooling-gated nightly step (bin/smoke-and-record.sh).
check("SKIPs with exit 78 when no codex CLI is on PATH, rather than failing",
      "exit 78" in smoke_src and "command -v codex" in smoke_src)


# ── 3. the flag is present at every sanctioned invocation site ──────────
print("\n--dangerously-bypass-hook-trust present at every sanctioned site")

lib_path = os.path.join(REPO, "bin", "council-lib.sh")
lib_src = open(lib_path).read() if os.path.exists(lib_path) else ""


def _function_body(src, name):
    """Crude but sufficient: the text between `name() {` and the next
    top-level `}` at column 0, which is how every function in this file is
    written."""
    marker = f"{name}() {{"
    start = src.find(marker)
    if start == -1:
        return None
    end = src.find("\n}", start)
    return src[start:end] if end != -1 else src[start:]


run_codex_body = _function_body(lib_src, "run_codex")
run_precheck_body = _function_body(lib_src, "run_precheck")

check("bin/council-lib.sh: run_codex() carries --dangerously-bypass-hook-trust",
      run_codex_body is not None and "--dangerously-bypass-hook-trust" in run_codex_body,
      "run_codex is the council-tier invocation — without the flag its hooks are silently skipped")
check("bin/council-lib.sh: run_precheck() carries --dangerously-bypass-hook-trust",
      run_precheck_body is not None and "--dangerously-bypass-hook-trust" in run_precheck_body,
      "run_precheck is what bin/precheck.sh and this smoke both call")

review_path = os.path.join(REPO, "pipelines", "run_codex_review.py")
review_src = open(review_path).read() if os.path.exists(review_path) else ""
_rspec = importlib.util.spec_from_file_location("run_codex_review", review_path)
assert _rspec and _rspec.loader
review_mod = importlib.util.module_from_spec(_rspec)
sys.modules["run_codex_review"] = review_mod
_rspec.loader.exec_module(review_mod)

_cmd = review_mod.build_codex_command("codex", "PROMPT PLACEHOLDER", __import__("pathlib").Path("/tmp"))
check("pipelines/run_codex_review.py: build_codex_command() carries --dangerously-bypass-hook-trust",
      "--dangerously-bypass-hook-trust" in _cmd, f"got {_cmd!r}")

# THE ANTI-DRIFT CASE, same reasoning as ops/git-env-selftest.py's own: three
# call sites doing the same job with three chances to individually regress.
# Fail loudly if a fourth codex-exec call site ever appears uncovered by this
# list, rather than silently missing it.
import re  # noqa: E402

KNOWN_SITES = {
    os.path.join("bin", "council-lib.sh"),
    os.path.join("pipelines", "run_codex_review.py"),
}
uncovered = []
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs
               if d not in (".git", ".venv", "node_modules", "__pycache__")
               and not d.startswith(".claude")]
    for fn in files:
        if not (fn.endswith(".sh") or fn.endswith(".py")):
            continue
        rel = os.path.relpath(os.path.join(root, fn), REPO)
        if rel in KNOWN_SITES or rel.endswith("codex-hook-smoke-selftest.py") \
           or rel.endswith("test-review-council.py"):
            continue
        try:
            text = open(os.path.join(root, fn), errors="ignore").read()
        except OSError:
            continue
        if re.search(r"\bcodex\s+exec\b", text) and "--dangerously-bypass-hook-trust" not in text \
           and ".venv" not in rel:
            uncovered.append(rel)
check("no OTHER file invokes `codex exec` without the flag (new site check)",
      not uncovered, f"uncovered: {uncovered}")

sys.exit(CHECKER.summary())
