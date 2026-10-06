#!/usr/bin/env python3
"""deploy-ledger-selftest.py — every deploy records that it happened, staging
included. Fixtures written before the fix (rule e65efc68).

THE FAILURE, 2026-08-16, defect cb65fc17. The first approved release in this
system's history shipped to staging, read back clean, and wrote NO row to
ops.deployment. The table held exactly one row and it belonged to a different
session's production release.

THE CAUSE, read out of bin/deploy-worker.sh rather than guessed: all three
`record_deployment` call sites live INSIDE

    if [ "$TARGET_ENV" = "production" ] && [ -x .../smoke-and-record.sh ]; then

so a staging deploy reaches none of them.

WHY THAT BLOCK IS PRODUCTION-ONLY, AND WHY THAT PART IS CORRECT. The script's own
header explains it: smoke-reads.sh defaults to the production API, and a staging
deploy prints its workers.dev trigger from wrangler rather than from anything the
script holds, so aiming the suite at staging would mean reconstructing a hostname
this file would have to keep in sync with wrangler.toml. Pointing post-deploy
verification at the wrong hostname is what the 2026-08-13 routes incident was
made of. That refusal stays.

WHAT WAS WRONG IS THE NESTING, NOT THE REFUSAL. Running the golden suite and
recording that a deploy happened are different facts. The first is genuinely
production-only. The second is the Program 3 job ledger, which Program 5's
promotion path reads, and a staging deploy that leaves no row is indistinguishable
from a staging deploy that never ran.

THE STATE FOR A STAGING DEPLOY IS `verifying`, NOT `complete`, and the script
already defines that word for exactly this case: the suite could not run, so
nothing was proven. Migration 0115 refuses `complete` without a read-back, which
is the constraint doing its job. Automating the staging read-back so it could
honestly claim `complete` is Program 5 work (production read-back is a Program 5
bullet), deliberately not smuggled in here.

The structural fixtures parse the shipped script. Program 5's post-promotion
failure semantics are also executed in a temporary fake repo with mocked
Wrangler, curl, smoke, and ledger commands; no Cloudflare or database call runs.

Run: .venv/bin/python ops/deploy-ledger-selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"

PASSED: int = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def block_of(text: str, start_pat: str) -> tuple[int, int]:
    """Line span of the shell `if` block whose opening line matches start_pat,
    found by matching if/fi depth. Crude on purpose: the assertion is about
    which lines sit inside which block, so the parse must be literal."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.search(start_pat, l)), -1)
    if start < 0:
        return (-1, -1)
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if re.match(r"^if\b", stripped):
            depth += 1
        elif stripped == "fi" or stripped.startswith("fi "):
            depth -= 1
            if depth == 0:
                return (start, i)
    return (start, len(lines) - 1)


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def exercise_program5_failure(failure: str, *, posture: str = "enabled") -> subprocess.CompletedProcess[str]:
    """Run the Production promotion path with every external boundary mocked."""
    # PRIVATE TMP, NOT THE REPO ROOT (2026-08-23 load-flake sweep). This used
    # to be dir=REPO, which left a deploy-ledger-* directory sitting in the
    # checkout for the duration of the run and made every concurrent ci.sh run
    # share one filesystem namespace with it. tempfile's own default is already
    # per-process private; nothing here needs the fixture inside the repo.
    with tempfile.TemporaryDirectory(prefix="deploy-ledger-") as raw:
        root = Path(raw)
        script = root / "bin" / "deploy-worker.sh"
        write_executable(script, SCRIPT.read_text(encoding="utf-8"))
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "ops-record.py").write_text("# fake dispatch target\n")

        write_executable(root / ".venv" / "bin" / "python", r'''
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            failure = os.environ.get("FAKE_PROGRAM5_FAILURE", "")
            if args and args[0] == "-c":
                expression = args[1] if len(args) > 1 else ""
                if "schema_ledger_sha256" in expression:
                    print("sha256:" + "7" * 64)
                elif "schema_highest_migration" in expression:
                    print("0300_operational_hermes_bot_profiles.sql")
                elif "schema_applied_count" in expression:
                    print("236")
                else:
                    print("100")
                raise SystemExit(0)
            tool = Path(args[0]).name if args else ""
            rest = args[1:]
            if tool == "ops-record.py":
                if rest[:2] == ["release", "locate"]:
                    print("release-test " + "a" * 40)
                    raise SystemExit(0)
                if rest[:2] == ["release", "require"]:
                    print("release-test " + "a" * 40)
                    raise SystemExit(0)
                if rest and rest[0] == "deployment":
                    state = rest[rest.index("--state") + 1]
                    if failure == "identity" and state == "verifying" and "--read-back-at" in rest:
                        raise SystemExit(9)
                    if failure == "deployment" and state == "complete":
                        raise SystemExit(9)
                    raise SystemExit(0)
                if rest[:2] == ["release", "complete"]:
                    raise SystemExit(9 if failure == "release" else 0)
                if rest and rest[0] == "run":
                    raise SystemExit(0)
            if tool == "release-manifest.py":
                if rest and rest[0] in ("build", "bind-provider"):
                    print("{}")
                elif rest and rest[0] == "plan-hash":
                    print("plan:selftest")
                elif rest and rest[0] == "program6-posture":
                    print(os.environ.get("FAKE_PROGRAM6_POSTURE", "enabled"))
                raise SystemExit(0)
            if tool == "verify-worker-release.py":
                expected = os.environ.get("FAKE_PROGRAM6_POSTURE", "enabled")
                if ("--expected-program6-actions" not in rest
                        or rest[rest.index("--expected-program6-actions") + 1] != expected):
                    raise SystemExit(9)
                raise SystemExit(0)
            if tool == "performance-budget-gate.py":
                raise SystemExit(0)
            raise SystemExit(0)
        ''')
        write_executable(root / "mcp-server" / "node_modules" / ".bin" / "wrangler",
                         "#!/bin/sh\nexit 0\n")
        # deploy-worker.sh validates the checked Wrangler manifest before it
        # reaches any mocked boundary. Keep this fixture's issuer runtime
        # explicitly inert, matching the source manifest's safety contract.
        (root / "mcp-server" / "wrangler.toml").write_text(
            'CANONICAL_OWNERSHIP_RUNTIME_MODE = "disabled"\n',
            encoding="utf-8")
        write_executable(root / "bin" / "smoke-and-record.sh",
                         "#!/bin/sh\nexit 0\n")
        fake_bin = root / "fake-bin"
        write_executable(fake_bin / "curl", "#!/bin/sh\nprintf '%s\\n' '{}'\n")

        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        (root / "tmp").mkdir()
        env["TMPDIR"] = str(root / "tmp")
        env["CARR_CORRELATION_ID"] = "77777777-7777-4777-8777-777777777777"
        env["FAKE_PROGRAM5_FAILURE"] = failure
        env["FAKE_PROGRAM6_POSTURE"] = posture
        return subprocess.run(
            ["sh", str(script), "--promote-version",
             "11111111-2222-4333-8444-555555555555",
             "--performance-budget-ref", "runbook:performance-v1",
             "--performance-budget-ms", "1000",
             "--recovery-strategy", "rollback",
             "--rollback-plan-ref", "runbook:rollback-v1"],
            cwd=root, env=env, capture_output=True, text=True, check=False)


def main() -> int:
    if not SCRIPT.exists():
        print(f"FAIL: {SCRIPT} not found")
        return 1
    text = SCRIPT.read_text()
    lines = text.splitlines()

    calls = [i for i, l in enumerate(lines) if re.search(r"^\s*record_deployment\s+\w", l)]
    check("the deploy script still records deployments at all", bool(calls),
          "no record_deployment call sites found")
    if not calls:
        print(f"\ndeploy-ledger-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
        return 1

    # ANCHOR ON THE GOLDEN-SUITE BLOCK, not on any production test. The script
    # has more than one `if [ "$TARGET_ENV" = "production" ]`, and the first is a
    # five-line block hundreds of lines earlier. Matching the wrong one made the
    # first run of these fixtures report three failures that were the parser's
    # rather than the script's — caught by tracing the span before trusting it.
    prod_start, prod_end = block_of(
        text, r'^\s*if \[ "\$TARGET_ENV" = "production" \]\s*&&\s*\[ -x .*smoke-and-record')
    check("the production-only golden-suite block is still present",
          prod_start >= 0,
          "the guard that keeps smoke-reads.sh off staging is gone — that guard must stay")

    # THE REGRESSION ITSELF: at least one recording path must sit OUTSIDE the
    # production-only block, or staging deploys are invisible to the ledger.
    outside = [i for i in calls if not (prod_start <= i <= prod_end)]
    check("at least one deployment is recorded OUTSIDE the production-only block",
          bool(outside),
          "every record_deployment call is nested inside the production guard, so a "
          "staging deploy records nothing — this is defect cb65fc17")

    # And it must actually be reachable for a non-production target.
    tail = "\n".join(lines[prod_end + 1:]) if prod_end >= 0 else ""
    check("the non-production path is guarded by a NOT-production test",
          bool(re.search(r'\[\s*"\$TARGET_ENV"\s*!=\s*"production"\s*\]', tail)),
          "nothing after the production block tests for a non-production target")

    # The honest state. `complete` requires a read-back the staging path does not
    # have; migration 0115 refuses it, so claiming it would fail loudly anyway.
    non_prod_states = re.findall(r"record_deployment\s+(\w+)", tail)
    check("the staging deploy records `verifying`, never `complete`",
          bool(non_prod_states) and "complete" not in non_prod_states,
          f"states recorded outside the production block: {non_prod_states}")

    # The refusal that must survive: the golden suite still never aims at staging.
    prod_block = "\n".join(lines[prod_start:prod_end + 1]) if prod_start >= 0 else ""
    check("smoke-and-record.sh is still called ONLY inside the production block",
          "smoke-and-record.sh" in prod_block
          and "smoke-and-record.sh" not in tail,
          "the golden suite escaped its production guard — that is the 2026-08-13 "
          "routes incident waiting to happen again")

    # Routine/non-Production recording remains non-fatal, while the two durable
    # Program 5 acceptance receipts must propagate failure after traffic moves.
    fn_start, fn_end = block_of(text, r"^record_deployment\(\)")
    body = text[text.find("record_deployment()"):]
    check("only Production assurance receipts are required ledger writes",
          'rd_must_record=0' in body
          and '[ "$TARGET_ENV" = "production" ]' in body
          and 'return 1' in body,
          "the wrapper either made every routine ledger miss fatal or still hides "
          "a missing Production assurance receipt")

    identity_at = text.find(
        'record_deployment verifying "$CARR_CORRELATION_ID" identity-readback')
    smoke_at = text.find('"$REPO/bin/smoke-and-record.sh"', identity_at)
    performance_at = text.find('performance-budget-gate.py', smoke_at)
    check("verified identity is durably recorded before golden/performance",
          -1 not in (identity_at, smoke_at, performance_at)
          and identity_at < smoke_at < performance_at)

    identity_failure = exercise_program5_failure("identity")
    identity_output = identity_failure.stdout + identity_failure.stderr
    check("missing durable identity receipt exits nonzero after promotion",
          identity_failure.returncode != 0
          and "durable identity receipt is missing" in identity_output,
          f"rc={identity_failure.returncode} output={identity_output[-240:]}")

    deployment_failure = exercise_program5_failure("deployment")
    deployment_output = deployment_failure.stdout + deployment_failure.stderr
    check("missing complete deployment receipt exits nonzero",
          deployment_failure.returncode != 0
          and "ledger row did NOT record" in deployment_output,
          f"rc={deployment_failure.returncode} output={deployment_output[-240:]}")

    release_failure = exercise_program5_failure("release")
    release_output = release_failure.stdout + release_failure.stderr
    check("failed release closure exits nonzero",
          release_failure.returncode != 0
          and "did not close" in release_output,
          f"rc={release_failure.returncode} output={release_output[-240:]}")

    disabled_posture = exercise_program5_failure("", posture="disabled")
    check("manifest-derived disabled posture is forwarded through Production rollback verification",
          disabled_posture.returncode == 0,
          f"rc={disabled_posture.returncode} output={(disabled_posture.stdout + disabled_posture.stderr)[-240:]}")

    check("promotion temp files are portable across macOS and GNU mktemp",
          "mktemp -t" not in text and text.count(".XXXXXX") >= 4,
          "use an explicit TMPDIR template ending in six Xs")

    print(f"\ndeploy-ledger-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
    if FAILED:
        print("FAILURES: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
