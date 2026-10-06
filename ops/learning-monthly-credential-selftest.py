#!/usr/bin/env python3
"""Monthly learning passes jobs credentials only and keeps both programs wired."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MONTHLY = REPO / "bin" / "learning-monthly.sh"
FAILED: list[str] = []


def check(label: str, value: bool, detail: str = "") -> None:
    print(("  ok    " if value else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not value:
        FAILED.append(label)


def run(script: Path, home: Path, vault: Path, capture: Path, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/zsh", str(script)], text=True, capture_output=True, check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "CARR_VAULT": str(vault),
             "CAPTURE": str(capture), **extra})


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); script = root / "bin" / "learning-monthly.sh"
        script.parent.mkdir(); shutil.copy2(MONTHLY, script)
        # learning-monthly.sh sources bin/routine-credential-env.sh for the
        # shared db.env sourceability guard; the fixture bin/ must carry it.
        shutil.copy2(REPO / "bin" / "routine-credential-env.sh",
                     script.parent / "routine-credential-env.sh")
        (root / "out" / "Learning").mkdir(parents=True)
        fake = root / ".venv" / "bin" / "python"; fake.parent.mkdir(parents=True)
        fake.write_text("#!/usr/bin/python3\nimport json,os,sys\n"
                        "open(os.environ['CAPTURE'],'a').write(json.dumps({'args':sys.argv[1:],'env':dict(os.environ)})+'\\n')\n",
                        encoding="utf-8")
        fake.chmod(0o700)
        home = root / "home"; db = home / ".config" / "carr" / "db.env"; db.parent.mkdir(parents=True)
        capture = root / "capture.jsonl"
        db.write_text("CARR_DB_JOBS_URL=postgresql://carr_jobs:good@db/carr\n"  # ci-secret-scan: allow — selftest fixture
                      "CARR_DB_WRITER_URL=postgresql://carr_writer:bad@db/carr\n", encoding="utf-8")  # ci-secret-scan: allow — selftest fixture
        completed = run(script, home, root / "vault", capture,
                        {"DATABASE_URL":"postgresql://carr_writer:ambient@db/carr"})  # ci-secret-scan: allow — selftest fixture
        rows = [json.loads(x) for x in capture.read_text(encoding="utf-8").splitlines()] if capture.exists() else []
        check("jobs-configured monthly workflow invokes learning and correction programs",
              completed.returncode == 0 and [r["args"][0] for r in rows] ==
              ["pipelines/learning_jobs.py", "ops/corrections-sweep.py"],
              f"rc={completed.returncode} args={[r['args'] for r in rows]!r}")
        check("monthly children receive only the jobs DB credential",
              bool(rows) and all(r["env"].get("CARR_DB_JOBS_URL") == "postgresql://carr_jobs:good@db/carr"  # ci-secret-scan: allow — selftest fixture
              and not any(k in r["env"] for k in ("DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL", "CARR_DB_CADENCE_URL", "CARR_IMPORT_DB_URL")) for r in rows))
        capture.unlink()
        db.write_text("CARR_DB_WRITER_URL=postgresql://carr_writer:bad@db/carr\n", encoding="utf-8")  # ci-secret-scan: allow — selftest fixture
        refused = run(script, home, root / "vault", capture, {})
        check("writer-only monthly configuration refuses before either program", refused.returncode == 78 and not capture.exists())
    print(f"learning monthly credential selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
