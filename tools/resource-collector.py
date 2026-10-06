#!/Library/Developer/CommandLineTools/usr/bin/python3
"""tools/resource-collector.py — DoctorCRE V5-UX-C06 local capacity/route collector.

WHAT THIS IS. The one local collector V5-UX-C06 needs: it measures the Mac
Studio's host capacity (CPU cores, memory, disk) and probes the already-running
ds4 Flash Next server (127.0.0.1:8000, launchd label local.ds4-flash-next,
shared with the Model Room flash desk and tools/dictation-rig/bin/post_call.py)
for model-route availability, then writes ONE observation per provider
(local_compute, model_route) through the record-resource-observation MCP verb
(migrations/0580_resource_observation.sql).

NO CREDENTIAL. This script never opens a database connection, never reads a
DB credential file, and never holds an API key. It shells out to
`./run.sh call record-resource-observation '<json>'`, which is the SAME door
tools/call-verb.py documents: the deployed Worker derives identity from a
LOCAL_TOKENS bearer that call-verb.py's underlying local-verb.mjs resolves on
its own -- this script never sees or handles that token. That is what "no
credential" means here: nothing in this file's own code path holds a secret.
This script is NOT the "fallback door" CLAUDE.md's break-glass section
describes (`./run.sh call --reason ... --branch ...` against a denied verb);
it makes an ordinary, always-allowed call to a verb nobody has denied.

MEASURED VS CONFIGURED, NEVER COLLAPSED (C06 checkable_done). Every
local_compute observation carries measured_capacity and configured_capacity as
two independent JSON objects. measured_capacity holds what this run actually
read from the host (psutil / os.cpu_count() / shutil.disk_usage); a field this
run could not read is OMITTED from measured_capacity, never written as 0 --
absence is represented by config-JSON-side documentation and the top-level
`reason`, not by a fabricated zero inside the payload.
configured_capacity holds the operator-declared nominal spec for THIS Mac
Studio (RESOURCE_COLLECTOR_CONFIGURED_CAPACITY below) -- explicitly not the
old Mac mini's numbers (C06 included_scope: "Bind actual configured Mac
Studio host; do not substitute old Mini specifications").

HOST / SERVER OFFLINE. If the ds4 Flash Next server does not answer
GET /v1/models within FLASH_HEALTH_TIMEOUT, model_route's observation is
state='host_offline' with an explicit reason -- never a fabricated "route
available: false" wearing the shape of a real qualification result, and never
silently skipped.

RUN IT:
    tools/resource-collector.py            # collect and write both providers
    tools/resource-collector.py --dry-run  # print the payloads, write nothing

launchd. ops/launchd/com.carr.resource-collector.plist ships in this repo but
is NOT installed or loaded by this change -- see that file's own header.
launchd's PATH does not include Homebrew's python3, and the CPython on this
Mac that has no scripting-bridge quirks under launchd's sandboxed environment
is Xcode's command-line-tools python3, so the shebang above is intentional;
do not "fix" it to /usr/bin/python3 or /opt/homebrew/bin/python3.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable
import flashlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SH = os.path.join(REPO_ROOT, "run.sh")

FLASH_SERVER_URL = "http://127.0.0.1:8000"
FLASH_SERVER_LAUNCHD_LABEL = "local.ds4-flash-next"
FLASH_HEALTH_TIMEOUT = 2.0

# The operator-declared nominal spec for THIS Mac Studio. Not measured; not
# the old Mac mini's numbers. Update this constant by hand if the hardware
# changes -- it is deliberately not auto-detected, because "configured"
# means "what Joe says this box is provisioned as," which is a decision, not
# a sensor reading.
RESOURCE_COLLECTOR_CONFIGURED_CAPACITY = {
    "host": "mac-studio",
    "cpu_cores": 24,
    "memory_gb": 192,
    "disk_gb": 2000,
    "gpu": "apple-m2-ultra",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def measure_host_capacity() -> dict[str, Any]:
    """Read what this run can actually observe. Omits a field it cannot
    read rather than writing 0 for it."""
    measured: dict[str, Any] = {}
    cpu_count = os.cpu_count()
    if cpu_count:
        measured["cpu_cores"] = cpu_count
    try:
        total, used, free = shutil.disk_usage(REPO_ROOT)
        measured["disk_total_gb"] = round(total / (1024 ** 3), 1)
        measured["disk_free_gb"] = round(free / (1024 ** 3), 1)
    except OSError:
        pass
    try:
        import psutil  # optional; absent is not an error

        measured["memory_total_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        measured["memory_available_gb"] = round(psutil.virtual_memory().available / (1024 ** 3), 1)
        measured["load_avg_1m"] = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    except ImportError:
        pass
    return measured


def flash_server_available(
    opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = FLASH_HEALTH_TIMEOUT
) -> tuple[bool, dict[str, Any] | None]:
    """Ensure Flash before probing; retain the offline observation on timeout."""
    try:
        with flashlib.request_scope(FLASH_SERVER_URL, opener=opener), opener(f"{FLASH_SERVER_URL}/v1/models", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return True, body
    except Exception:
        return False, None


# The sensors a fully healthy run expects to read. cpu_cores and the two
# disk_* fields come from stdlib (os.cpu_count / shutil.disk_usage); the two
# memory_* fields come from optional psutil. A run that reads SOME but not
# ALL of these is not "ok" -- it is missing real evidence for part of the
# host, and must say so rather than let a partial read pass as a clean one.
EXPECTED_MEASURED_FIELDS = (
    "cpu_cores", "disk_total_gb", "disk_free_gb", "memory_total_gb", "memory_available_gb",
)


def build_local_compute_observation() -> dict[str, Any]:
    measured = measure_host_capacity()
    missing = [field for field in EXPECTED_MEASURED_FIELDS if field not in measured]
    if not measured:
        state = "collector_absent"
        reason = "host sensors unavailable to this run"
    elif missing:
        state = "partial"
        reason = f"host sensors partially unavailable to this run: missing {', '.join(missing)}"
    else:
        state = "ok"
        reason = None
    return {
        "provider": "local_compute",
        "measured_capacity": measured or None,
        "configured_capacity": RESOURCE_COLLECTOR_CONFIGURED_CAPACITY,
        "state": state,
        "reason": reason,
        "source": "tools/resource-collector.py",
        "observed_at": now_iso(),
    }


def build_model_route_observation() -> dict[str, Any]:
    available, body = flash_server_available()
    if available:
        return {
            "provider": "model_route",
            "model_route": {
                "launchd_label": FLASH_SERVER_LAUNCHD_LABEL,
                "endpoint": FLASH_SERVER_URL,
                "reachable": True,
                "models": body,
            },
            "state": "ok",
            "reason": None,
            "source": "tools/resource-collector.py",
            "observed_at": now_iso(),
        }
    return {
        "provider": "model_route",
        "model_route": {
            "launchd_label": FLASH_SERVER_LAUNCHD_LABEL,
            "endpoint": FLASH_SERVER_URL,
            "reachable": False,
        },
        "state": "host_offline",
        "reason": f"{FLASH_SERVER_LAUNCHD_LABEL} did not answer GET /v1/models within {FLASH_HEALTH_TIMEOUT}s",
        "source": "tools/resource-collector.py",
        "observed_at": now_iso(),
    }


def record(observation: dict[str, Any]) -> int:
    import uuid

    payload = {"idempotency_key": str(uuid.uuid4()), **observation}
    proc = subprocess.run(
        [RUN_SH, "call", "record-resource-observation", json.dumps(payload)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"resource-collector: {observation['provider']} write refused: {proc.stderr}\n")
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print payloads; write nothing")
    args = parser.parse_args(argv)

    observations = [build_local_compute_observation(), build_model_route_observation()]

    if args.dry_run:
        print(json.dumps(observations, indent=2))
        return 0

    exit_code = 0
    for observation in observations:
        rc = record(observation)
        exit_code = exit_code or rc
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
