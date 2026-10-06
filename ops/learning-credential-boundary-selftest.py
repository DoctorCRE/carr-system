#!/usr/bin/env python3
"""Hermetic proof that weekly learning cannot borrow a writer credential."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEEKLY = REPO / "bin" / "learning-weekly.sh"
FAILED: list[str] = []


def check(label: str, value: bool, detail: str = "") -> None:
    print(("  ok    " if value else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not value:
        FAILED.append(label)


def fixture_root(root: Path) -> tuple[Path, Path]:
    script = root / "bin" / "learning-weekly.sh"
    script.parent.mkdir()
    shutil.copy2(WEEKLY, script)
    # The script sources bin/routine-credential-env.sh for the shared
    # db.env sourceability guard, so the fixture bin/ must carry it too.
    shutil.copy2(REPO / "bin" / "routine-credential-env.sh",
                 script.parent / "routine-credential-env.sh")
    fake = root / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CAPTURE'], 'a', encoding='utf-8') as fh:\n"
        " fh.write(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return script, fake


def run_weekly(script: Path, home: Path, vault: Path, capture: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "CARR_VAULT": str(vault),
           "CAPTURE": str(capture), **extra}
    return subprocess.run(["/bin/zsh", str(script)], env=env, text=True,
                          capture_output=True, check=False)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        script, _fake = fixture_root(root)
        home = root / "home"; db = home / ".config" / "carr" / "db.env"
        db.parent.mkdir(parents=True)
        capture = root / "captured.jsonl"
        db.write_text("CARR_DB_JOBS_URL=postgresql://carr_jobs:file-only@db/carr\n"  # ci-secret-scan: allow — selftest fixture
                      "CARR_DB_WRITER_URL=postgresql://carr_writer:must-not-pass@db/carr\n"  # ci-secret-scan: allow — selftest fixture
                      "CARR_DB_OWNER_URL=postgresql://owner:must-not-pass@db/carr\n",  # ci-secret-scan: allow — selftest fixture
                      encoding="utf-8")
        completed = run_weekly(script, home, root / "vault", capture,
                               DATABASE_URL="postgresql://writer:ambient-must-not-pass@db/carr")  # ci-secret-scan: allow — selftest fixture
        rows = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()] if capture.exists() else []
        check("jobs-configured weekly workflow invokes both established programs", completed.returncode == 0 and len(rows) == 2,
              f"rc={completed.returncode} stderr={completed.stderr.strip()!r}")
        # THE CUTOFF, 2026-08-19: the report directory moved from the vault's
        # Automation/Learning to the repo's own out/Learning, because both
        # clauses are pure readers and their reports are renderings of rows that
        # never left the database. The harness copies the script to root/bin, so
        # the script's own REPO resolves to root and LEARN_DIR is root/out/Learning.
        check("metrics program retains its registered --apply invocation",
              bool(rows) and rows[0]["args"] == ["pipelines/pull_placement_metrics.py", "--apply", "--report-dir", str(root / "out" / "Learning")])
        # AND THE RETIREMENT HOLDS. run_weekly still points CARR_VAULT at a temp
        # directory, so if anything ever writes a report back into the vault —
        # including by restoring the old default, which was the real Drive path —
        # this catches it here instead of in the live vault.
        check("nothing is written into the vault any more",
              not (root / "vault").exists(),
              f"vault dir reappeared: {sorted(p.name for p in (root / 'vault').rglob('*'))[:3]}"
              if (root / "vault").exists() else "")
        check("children receive only the jobs database credential",
              bool(rows) and all(r["env"].get("CARR_DB_JOBS_URL") == "postgresql://carr_jobs:file-only@db/carr"  # ci-secret-scan: allow — selftest fixture
              and not any(key in r["env"] for key in ("DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL", "CARR_DB_CADENCE_URL", "CARR_IMPORT_DB_URL")) for r in rows))

        capture.unlink()
        db.write_text("CARR_DB_WRITER_URL=postgresql://carr_writer:writer-only@db/carr\n", encoding="utf-8")  # ci-secret-scan: allow — selftest fixture
        writer_only = run_weekly(script, home, root / "vault", capture)
        check("writer-only configuration is refused before workflow invocation",
              writer_only.returncode == 78 and not capture.exists(), writer_only.stderr.strip())

        ambient_only = run_weekly(script, root / "empty-home", root / "vault", capture,
                                  DATABASE_URL="postgresql://carr_writer:ambient-only@db/carr")  # ci-secret-scan: allow — selftest fixture
        check("ambient DATABASE_URL is refused before workflow invocation",
              ambient_only.returncode == 78 and not capture.exists(), ambient_only.stderr.strip())
    print(f"learning credential boundary selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
