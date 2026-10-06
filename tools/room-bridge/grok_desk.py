"""Install/read back the named read-only Grok desk; never changes login."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from desks import DEFAULT_REGISTRY, Registry
from grok_wire import EFFORT, MODEL
import registry_ext


def install(registry: Registry, cwd: str) -> dict:
    # Leave every other desk untouched and retain existing room metadata.
    old = registry.entries().get("grok-desk", {})
    registry.register("grok-desk", "grok-cli", model=MODEL, effort=EFFORT,
                      cwd=cwd, sandbox="read-only")
    registry_ext.set_seat("grok-desk", "grok", path=registry.path)
    registry_ext.set_room_listen("grok-desk", "mention", path=registry.path)
    if old.get("profile"):
        registry_ext.set_profile("grok-desk", old["profile"], path=registry.path)
    return registry.resolve("grok-desk")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()
    print(json.dumps(install(Registry(args.registry), args.cwd), indent=2))
