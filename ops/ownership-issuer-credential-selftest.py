#!/usr/bin/env python3
"""Offline check that the production ownership runtime remains disabled."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    wrangler = (ROOT / "mcp-server/wrangler.toml").read_text(encoding="utf-8")
    if wrangler.count('CANONICAL_OWNERSHIP_RUNTIME_MODE = "disabled"') != 1:
        print("ownership runtime selftest: FAIL, production runtime must stay disabled")
        return 1
    print("ownership runtime selftest: PASS, production runtime remains disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
