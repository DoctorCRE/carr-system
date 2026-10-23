#!/usr/bin/env python3
"""Resolve the production proof connection through Neon with redacted diagnostics."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secret_redaction import redact_text, sensitive_env_values

DETAIL_MAX = 2000


def cmd_neon_dsn(args: argparse.Namespace) -> int:
    """Keep raw provider stderr in memory; emit only redacted diagnostics."""
    try:
        result = subprocess.run(['neonctl', 'connection-string', 'production',
            '--project-id', args.project_id, '--role-name', 'neondb_owner'],
            capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"neonctl unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    if result.stderr:
        safe = redact_text(result.stderr, known_secrets=sensitive_env_values(os.environ))
        print(safe[:DETAIL_MAX].replace('\n', ' · '), file=sys.stderr)
    if result.returncode == 0:
        print(result.stdout.strip())
    return result.returncode if result.returncode > 0 else (1 if result.returncode < 0 else 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    neon = sub.add_parser("neon-dsn")
    neon.add_argument("--project-id", required=True)
    neon.set_defaults(func=cmd_neon_dsn)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
