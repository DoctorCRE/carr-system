"""Installation and observation share the same launchd machine scope.

The scope is read from ops/config/services.json, where every launchd
environment declares runs_on: "primary", "secondary" or "every". This module
derives PRIMARY_ONLY and SECONDARY_ONLY from it, so the installer, the
scheduled-job inventory cannot hold a different
list from the catalog (rule a8c55a47: one job, one code path).
"""
import json
import os

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ops", "config", "services.json")
RUNS_ON = ("primary", "secondary", "every")

def placements(catalog: str = CATALOG) -> dict[str, str]:
    """{plist filename: runs_on} for every launchd environment in the catalog.

    A launchd environment without a valid runs_on, or two services placing the
    same plist differently, is refused rather than defaulted: a guessed
    placement is how a shared-state job ends up running on both Macs.
    """
    with open(catalog, encoding="utf-8") as source:
        services = json.load(source)["services"]
    placed: dict[str, str] = {}
    for service in services:
        for environment in service.get("environments", []):
            mechanism = environment.get("deploy_mechanism") or ""
            if not (mechanism.startswith("ops/launchd/") and mechanism.endswith(".plist")):
                continue
            where = environment.get("runs_on")
            if where not in RUNS_ON:
                raise ValueError(f"{service['key']}:{environment.get('environment')} declares "
                                 f"runs_on={where!r}; expected one of {', '.join(RUNS_ON)}")
            name = os.path.basename(mechanism)
            if placed.setdefault(name, where) != where:
                raise ValueError(f"{name} is placed both {placed[name]!r} and {where!r}")
    return placed


PLACEMENT = placements()
PRIMARY_ONLY = frozenset(name for name, where in PLACEMENT.items() if where == "primary")
# The mirror image: jobs only the SECOND machine needs, because the primary
# already gets the same effect from a chain the second machine must not run.
SECONDARY_ONLY = frozenset(name for name, where in PLACEMENT.items() if where == "secondary")


# MacBook rehearsal remains scheduled after promotion; host identity owns it.
HOST_ONLY = {"com.carr.studio-failover-rehearsal.plist": "macbook"}


def allowed_on_machine(filename: str, primary: bool) -> bool:
    if filename in HOST_ONLY:
        import socket
        from pathlib import Path
        config = json.loads((Path(__file__).resolve().parents[1] / "ops/config/studio-failover.v1.json").read_text())
        return socket.gethostname() == config["hosts"][HOST_ONLY[filename]]["hostname"]
    return not ((filename in PRIMARY_ONLY and not primary)
                or (filename in SECONDARY_ONLY and primary))
