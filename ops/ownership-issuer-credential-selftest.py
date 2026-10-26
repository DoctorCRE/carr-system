#!/usr/bin/env python3
"""Offline check that the ownership runtime stays disabled in every Worker environment."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    wrangler = (ROOT / "mcp-server/wrangler.toml").read_text(encoding="utf-8")
    config = tomllib.loads(wrangler)
    modes = [config["vars"].get("CANONICAL_OWNERSHIP_RUNTIME_MODE")] + [
        env.get("vars", {}).get("CANONICAL_OWNERSHIP_RUNTIME_MODE")
        for env in config.get("env", {}).values()]
    # Every environment's vars table replaces the top-level one, so each must
    # say "disabled" itself, and the literal may appear nowhere else.
    if set(modes) != {"disabled"} or wrangler.count('CANONICAL_OWNERSHIP_RUNTIME_MODE = "disabled"') != len(modes):
        print("ownership runtime selftest: FAIL, the ownership runtime must stay disabled in every environment")
        return 1
    print("ownership runtime selftest: PASS, the ownership runtime is disabled in every environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
