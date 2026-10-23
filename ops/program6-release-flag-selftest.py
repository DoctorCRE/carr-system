#!/usr/bin/env python3
"""Keep Program 6's reviewed browser activation release-bound and exact."""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRANGLER = REPO / "mcp-server" / "wrangler.toml"
RELEASE = REPO / "mcp-server" / "src" / "release.js"
DEALROOM = REPO / "mcp-server" / "src" / "dealroom-web.js"
MANIFEST = REPO / "tools" / "release-manifest.py"


def main() -> int:
    config = tomllib.loads(WRANGLER.read_text(encoding="utf-8"))
    failures: list[str] = []

    for name, table in (("production", config.get("vars", {})),):
        if table.get("DEALROOM_PROGRAM6_ACTIONS_ENABLED") != "true":
            failures.append(f"{name} must declare DEALROOM_PROGRAM6_ACTIONS_ENABLED=true")

    release = RELEASE.read_text(encoding="utf-8")
    dealroom = DEALROOM.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    if 'program6_actions' not in release or 'program6ActionPosture' not in release:
        failures.append("/release must expose the shared Program 6 boolean posture")
    if 'program6ActionsEnabled' not in dealroom:
        failures.append("Deal Room must use the shared Program 6 flag parser")
    if '"mcp-server/wrangler.toml"' not in manifest:
        failures.append("release manifest must fingerprint wrangler.toml")

    if failures:
        print("program6-release-flag-selftest: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("program6-release-flag-selftest: reviewed activation is config-bound and release-readable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
