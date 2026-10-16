#!/usr/bin/env python3
"""Hermetic adversarial contract for typed recovery-prior verb shrink."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "bin" / "deploy-worker.sh"
source = WRAPPER.read_text(encoding="utf-8")

# No caller-controlled escape survives argument parsing: reject before the
# wrapper reaches its real dependency, network, or provider checks.
unknown = subprocess.run(["sh", str(WRAPPER), "--allow-shrink"],
                         cwd=ROOT, capture_output=True, text=True)
assert unknown.returncode == 64, unknown.stderr
assert "unknown argument '--allow-shrink'" in unknown.stderr

# The internal source-root knob is not a second deploy API: production and an
# arbitrary filesystem root are refused before any fetch, database, or
# provider operation. A controller can pass only a detached source root later
# verified against the DB-bound SHA.
recovery_args = ["--release-key", "candidate", "--release-sha", "a" * 40,
                 "--recovery-attempt-id", "11111111-2222-4333-8444-555555555555",
                 "--recovery-step", "prior", "--recovery-prior-release-key", "prior",
                 "--staging-receipt-idempotency-key", "22222222-2222-4333-8444-555555555555",
                 "--internal-exact-source-root", "/"]
production_root = subprocess.run(["sh", str(WRAPPER), "--env", "production", *recovery_args],
                                 cwd=ROOT, capture_output=True, text=True)
assert production_root.returncode != 0 and "recovery deploy requires staging" in production_root.stderr
arbitrary_root = subprocess.run(["sh", str(WRAPPER), "--env", "staging", *recovery_args],
                                cwd=ROOT, capture_output=True, text=True)
assert arbitrary_root.returncode != 0 and "exact source root" in arbitrary_root.stderr

# A lower-count route is syntactically closed around a typed prepare.  The
# three bundle legs use the staging-attempt writer and the isolated repair uses
# its separate staging-restore-only writer. Both writers refuse a mismatched
# candidate/prior/SHA/service or a prior without a complete Production
# readback, and both return the deterministic tag the later prepare must replay.
gate = '[ "$RECOVERY_STEP" != "standalone" ] || fail "standalone deploys cannot authorize verb shrink."'
attempt_prepare = '"$PY" "$REPO/tools/ops-record.py" staging-attempt prepare'
restore_prepare = '"$PY" "$REPO/tools/ops-record.py" staging-restore-only prepare'
assert gate in source and attempt_prepare in source and restore_prepare in source
assert source.index(gate) < source.index(attempt_prepare)
assert source.index(gate) < source.index(restore_prepare)
assert 'current_before|prior|current_after)' in source
assert 'restore_only)' in source
assert '--prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY"' in source
assert '--recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --recovery-step "$RECOVERY_STEP"' in source
assert '--git-sha "$HEAD_SHA" --correlation "$RECOVERY_ATTEMPT_ID"' in source
assert '"$RECOVERY_STEP" = "restore_only"' in source
assert 'TYPED_RECOVERY_TAG' in source
assert 'DEPLOY_TAG" != "$TYPED_RECOVERY_TAG' in source

# A failed retirement check must exercise the typed preparation only for a
# lower count in staging. This pins the real failure shape: staging can be ahead of the
# pinned recovery candidate, so current_before and restore_only must be
# authorized even though both are lower than the live baseline.
assert 'if ! sh "$REPO/ops/verb-count.sh" "$WORKER_DIR" --check-retirements "$PREVIOUS" >/dev/null; then' in source
assert '[ "$LOST" -le 0 ] || [ "$RECOVERY_STEP" = "standalone" ] || [ "$TARGET_ENV" != "staging" ]' in source
assert 'prepare_typed_recovery_shrink' in source
assert 'exact prepared typed recovery step' in source
assert 'exact prepared `prior`' not in source

# Shell backticks in the refusal text would execute `prior` as a command under
# double quotes. Keep the message literal and regression-tested.
assert 'prepared typed recovery step' in source
assert 'prepared `prior`' not in source

# Exercise the helper with the actual failure shape without reaching Postgres,
# Wrangler, or the network: a staging baseline of 164 and a pinned candidate
# of 158 must authorize each typed step only through its matching writer, and
# restore_only must use the isolated writer. The fake Python process stands in
# for the DB writer and returns the deterministic tag it prepared.
start = source.index("prepare_typed_recovery_shrink() {")
end = source.index('\n}\n\nif [ "$VERSION_MODE"', start) + 2
helper = source[start:end]
for step, expected_writer, expected_tag in (
        ("current_before", "staging-attempt", "carr-staging-" + "a" * 32),
        ("prior", "staging-attempt", "carr-staging-" + "a" * 32),
        ("current_after", "staging-attempt", "carr-staging-" + "a" * 32),
        ("restore_only", "staging-restore-only", "carr-staging-" + "b" * 32)):
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        fake = tmp / "python"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" > \"$CALL_LOG\"\n"
            "case \" $* \" in\n"
            "  *' staging-restore-only '* ) printf '%s\\n' carr-staging-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;\n"
            "  * ) printf '%s\\n' carr-staging-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;\n"
            "esac\n", encoding="utf-8")
        fake.chmod(0o755)
        script = tmp / "helper.sh"
        script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"REPO={ROOT}\nPY={fake}\nTARGET_ENV=staging\n"
            f"RECOVERY_STEP={step}\nSTAGING_RECEIPT_KEY=11111111-2222-4333-8444-555555555555\n"
            "REQUESTED_RELEASE_KEY=current\nRECOVERY_PRIOR_RELEASE_KEY=prior\n"
            "RECOVERY_ATTEMPT_ID=33333333-2222-4333-8444-555555555555\n"
            "HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nLOST=6\n"
            "fail() { echo \"REFUSED: $1\" >&2; exit 1; }\n"
            + helper + "\nprepare_typed_recovery_shrink\n"
            "printf '%s\\n' \"$TYPED_RECOVERY_TAG\"\n", encoding="utf-8")
        script.chmod(0o755)
        call_log = tmp / "call.log"
        result = subprocess.run(["sh", str(script)], capture_output=True, text=True,
                                env={**os.environ, "CALL_LOG": str(call_log)})
        assert result.returncode == 0, (step, result.stderr)
        call = shlex.split(call_log.read_text(encoding="utf-8"))
        assert call[:3] == [str(ROOT / "tools/ops-record.py"), expected_writer, "prepare"], (step, call)
        expected = {
            "--release-key": "current",
            "--prior-release-key": "prior",
            "--recovery-attempt-id": "33333333-2222-4333-8444-555555555555",
            "--git-sha": "a" * 40,
            "--correlation": "33333333-2222-4333-8444-555555555555",
        }
        for flag, value in expected.items():
            assert flag in call and call[call.index(flag) + 1] == value, (step, flag, call)
        if expected_writer == "staging-attempt":
            assert "--recovery-step" in call and call[call.index("--recovery-step") + 1] == step, (step, call)
        else:
            assert "--recovery-step" not in call, (step, call)
        assert call[-2:] == ["--field", "expected_provider_tag"], (step, call)
        assert expected_tag in result.stdout, (step, result.stdout)

# The provider command remains after the gate; a generic source/standalone or
# mismatched prior never reaches Wrangler with a lower registry count.
assert source.index(attempt_prepare) < source.index('"$WRANGLER" deploy --env "$TARGET_ENV"')
assert '"$REPO/tools/validate-exact-recovery-source.py"' in source
assert 'an exact source root is internal to a typed staging recovery step.' in source
assert 'WRANGLER="$REPO/mcp-server/node_modules/.bin/wrangler"' in source
assert 'WORKER_DIR="$SOURCE_ROOT/mcp-server"' in source
validator = (ROOT / "tools/validate-exact-recovery-source.py").read_text(encoding="utf-8")
assert '"ls-files", "--others", "--ignored", "--exclude-standard"' in validator
print("recovery-prior-shrink: standalone/manual/mismatched routes remain closed")
