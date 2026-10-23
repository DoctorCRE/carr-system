"""Install/read back the named Grok desk; never changes login."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from desks import DEFAULT_REGISTRY, Registry
from grok_wire import EFFORT


def install(registry: Registry, cwd: str) -> dict:
    registry.register("grok-desk", "grok-cli", effort=EFFORT, cwd=cwd)
    return registry.resolve("grok-desk")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()
    print(json.dumps(install(Registry(args.registry), args.cwd), indent=2))
