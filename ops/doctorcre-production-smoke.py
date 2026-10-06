#!/usr/bin/env python3
"""Read-only smoke check for the public DoctorCRE production surfaces.

The check makes only GET/HEAD requests.  It intentionally does not call
``/mcp`` or any mutation endpoint: its purpose is to answer whether the
independent app Worker and the CARR Worker are the deployments we think they are.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API = "https://api.doctorcre.com"
DEFAULT_APP = "https://app.doctorcre.com"
DEFAULT_LEGACY = "https://dealroom.doctorcre.com"
DEFAULT_ENVIRONMENT = "production"
# A Worker serving fewer than this is almost certainly an incomplete/old
# deployment.  This is a floor, not the exact count; use /release for identity.
DEFAULT_MIN_VERBS = 140
# The CARR-side pin of the DoctorCRE artifact CARR was built and verified
# against (tools/doctorcre-artifact.py).  Its contracts.* versions are the
# floor for the live app-release contract check below: CARR must never accept
# a served app whose CARR-facing contract is OLDER than what CARR itself was
# built against, e.g. a rollback. A newer served version is fine (and
# expected -- see app_release_result); this file is read fresh on every run
# rather than hardcoded so the floor moves only when someone deliberately
# re-pins it (ops/config/doctorcre-artifact.v1.json, most recently PR #1149).
DEFAULT_ARTIFACT_PIN = "ops/config/doctorcre-artifact.v1.json"
USER_AGENT = "doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)"


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def read(url: str, opener=_OPENER.open, timeout: int = 15) -> Reply:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return Reply(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        # Redirects are disabled by the caller for the auth probe, but HTTP
        # errors still carry useful status/headers and should be judged.
        return Reply(error.code, dict(error.headers.items()), error.read())


def release_result(reply: Reply, expected_env: str, minimum_verbs: int) -> list[str]:
    failures: list[str] = []
    try:
        payload = json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["/release returned non-JSON"]
    if reply.status != 200:
        failures.append(f"/release HTTP {reply.status}")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        failures.append("/release ok is not true")
    env = payload.get("env") if isinstance(payload, dict) else None
    observed = env.get("value") if isinstance(env, dict) else env
    if observed != expected_env:
        failures.append(f"/release environment {observed!r}, expected {expected_env!r}")
    verbs = payload.get("verb_count") if isinstance(payload, dict) else None
    if not isinstance(verbs, int) or isinstance(verbs, bool) or verbs < minimum_verbs:
        failures.append(f"/release verb_count {verbs!r}, expected integer >= {minimum_verbs}")
    return failures


def auth_result(reply: Reply, app: str) -> list[str]:
    failures: list[str] = []
    if reply.status not in (301, 302, 303, 307, 308):
        failures.append(f"Deal Room unauthenticated request returned HTTP {reply.status}, expected redirect")
        return failures
    location = reply.headers.get("Location", "")
    resolved = urllib.parse.urljoin(app.rstrip("/") + "/", location)
    target = urllib.parse.urlparse(resolved)
    expected = urllib.parse.urlparse(app)
    if not (target.scheme == "https" and target.hostname == expected.hostname
            and target.port in (None, 443) and target.path == "/auth/login"):
        failures.append(
            f"App redirect target {location!r}, expected same-origin /auth/login")
    return failures


_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse_semver(version: object) -> tuple[int, int, int] | None:
    """Parse a strict MAJOR.MINOR.PATCH string into a comparable int tuple.

    None on anything that is not exactly that shape -- callers use that to
    distinguish "not a valid version" from a real comparison.
    """
    if not isinstance(version, str):
        return None
    match = re.fullmatch(_SEMVER_RE, version)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def load_contract_floor(pin_path: Path) -> dict[str, tuple[int, int, int]]:
    """The minimum acceptable app-release contract versions, read fresh from
    the CARR-side DoctorCRE artifact pin (DEFAULT_ARTIFACT_PIN).

    Returns {"carr_contract": (major, minor, patch), "route_contract": (...)}.
    Raises ValueError for anything short of a clean read -- missing file,
    unreadable file, invalid JSON, or a malformed/missing contracts block.
    Callers must treat that as a smoke FAILURE (fail closed): an unreadable
    pin means the floor cannot be verified, which is not the same as there
    being no floor.
    """
    try:
        text = pin_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"DoctorCRE artifact pin {pin_path} is unreadable ({error})") from error
    try:
        pin = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"DoctorCRE artifact pin {pin_path} is not valid JSON ({error})") from error
    if not isinstance(pin, dict) or not isinstance(pin.get("contracts"), dict):
        raise ValueError(f"DoctorCRE artifact pin {pin_path} has no contracts block")
    floor: dict[str, tuple[int, int, int]] = {}
    for pin_key, payload_field in (("carr_interface", "carr_contract"), ("route_contract", "route_contract")):
        entry = pin["contracts"].get(pin_key)
        version = entry.get("version") if isinstance(entry, dict) else None
        parsed = _parse_semver(version)
        if parsed is None:
            raise ValueError(f"DoctorCRE artifact pin {pin_path} contracts.{pin_key}.version is not a valid semver")
        floor[payload_field] = parsed
    return floor


def _contract_result(payload: object, field: str, expected_schema: str,
                      min_version: tuple[int, int, int] | None) -> str | None:
    """None if payload[field] is {schema: expected_schema, version: <semver>}
    and version >= min_version (compared numerically per component, never as
    strings -- "1.9.0" >= "1.10.0" is false as strings and must not be).

    The version is intentionally NOT compared against a frozen literal
    ceiling: it is a build number that legitimately advances with every
    DoctorCRE app release, exactly like source_commit and provider_version_id
    just above it in app_release_result, which are validated by SHAPE rather
    than by exact value for the same reason. A newer-than-pin version is
    expected and must pass. What the earlier check missed is the FLOOR: a
    served version lower than the CARR-side pin (e.g. the app rolled back)
    must fail, because that is CARR serving a contract older than what CARR
    itself was built and verified against. Only the schema NAME needs an
    exact match; a schema rename is a real contract break, a version bump is
    not.

    min_version of None means the floor could not be established this run
    (see load_contract_floor) -- the shape/schema check below still applies,
    but the floor comparison is skipped since the caller has already recorded
    the pin failure separately and comparing against an unknown floor would
    be meaningless.
    """
    if not isinstance(payload, dict):
        return f"/app-release {field} is missing"
    contract = payload.get(field)
    if (not isinstance(contract, dict) or set(contract) != {"schema", "version"}
            or contract.get("schema") != expected_schema):
        return f"/app-release {field} is not {expected_schema}"
    version = _parse_semver(contract.get("version"))
    if version is None:
        return f"/app-release {field} version {contract.get('version')!r} is not a valid semver"
    if min_version is not None and version < min_version:
        return (f"/app-release {field} version {contract['version']} is below the "
                f"pinned floor {'.'.join(str(part) for part in min_version)}")
    return None


def app_release_result(reply: Reply, expected_env: str,
                        contract_floor: dict[str, tuple[int, int, int]] | None) -> list[str]:
    failures: list[str] = []
    try:
        payload = json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["/app-release returned non-JSON"]
    if reply.status != 200:
        failures.append(f"/app-release HTTP {reply.status}")
    if not isinstance(payload, dict) or payload.get("service") != "doctorcre-app":
        failures.append("/app-release service is not doctorcre-app")
    if not isinstance(payload, dict) or payload.get("environment") != expected_env:
        failures.append(f"/app-release environment {payload.get('environment') if isinstance(payload, dict) else None!r}, expected {expected_env!r}")
    if not isinstance(payload, dict) or not re.fullmatch(r"[0-9a-f]{40}", payload.get("source_commit", "")):
        failures.append("/app-release source_commit is not a full Git SHA")
    if not isinstance(payload, dict) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            payload.get("provider_version_id", ""), re.IGNORECASE):
        failures.append("/app-release provider_version_id is not an immutable version ID")
    carr_floor = contract_floor.get("carr_contract") if contract_floor else None
    carr_failure = _contract_result(payload, "carr_contract", "doctorcre-carr-interface.v1", carr_floor)
    if carr_failure:
        failures.append(carr_failure)
    route_floor = contract_floor.get("route_contract") if contract_floor else None
    route_failure = _contract_result(payload, "route_contract", "doctorcre-app-routes.v1", route_floor)
    if route_failure:
        failures.append(route_failure)
    return failures


def legacy_result(reply: Reply, app: str) -> list[str]:
    failures: list[str] = []
    if reply.status not in (301, 302, 303, 307, 308):
        return [f"legacy Deal Room request returned HTTP {reply.status}, expected redirect"]
    target = urllib.parse.urlparse(urllib.parse.urljoin(app.rstrip("/") + "/", reply.headers.get("Location", "")))
    expected = urllib.parse.urlparse(app)
    if not (target.scheme == "https" and target.hostname == expected.hostname
            and target.port in (None, 443) and target.path == "/deals" and not target.query):
        failures.append(f"legacy Deal Room redirect {reply.headers.get('Location', '')!r}, expected exact app /deals")
    return failures


def configured_hosts(wrangler_path: Path) -> list[str]:
    text = wrangler_path.read_text(encoding="utf-8")
    return re.findall(r'pattern\s*=\s*"([^"]+)"', text)


def host_result(wrangler_path: Path, api: str, app: str, legacy: str = DEFAULT_LEGACY) -> list[str]:
    expected = {"api.practicecre.com", "api.doctorcre.com", "dealroom.doctorcre.com", "reports.doctorcre.com"}
    configured = set(configured_hosts(wrangler_path))
    failures = [f"wrangler config missing expected host {host}" for host in sorted(expected - configured)]
    if "app.doctorcre.com" in configured:
        failures.append("CARR wrangler config still claims independent app host app.doctorcre.com")
    api_url = urllib.parse.urlparse(api)
    app_url = urllib.parse.urlparse(app)
    if not (api_url.scheme == "https" and api_url.hostname == "api.doctorcre.com"
            and api_url.port in (None, 443)):
        failures.append(
            f"API URL origin {api_url.scheme!r}, {api_url.hostname!r}, port {api_url.port!r}; "
            "expected canonical https://api.doctorcre.com")
    if not (app_url.scheme == "https" and app_url.hostname == "app.doctorcre.com"
            and app_url.port in (None, 443)):
        failures.append(
            f"App URL origin {app_url.scheme!r}, {app_url.hostname!r}, "
            f"port {app_url.port!r}; expected canonical https://app.doctorcre.com")
    legacy_url = urllib.parse.urlparse(legacy)
    if not (legacy_url.scheme == "https" and legacy_url.hostname == "dealroom.doctorcre.com"
            and legacy_url.port in (None, 443)):
        failures.append(
            f"legacy URL origin {legacy_url.scheme!r}, {legacy_url.hostname!r}, "
            f"port {legacy_url.port!r}; expected https://dealroom.doctorcre.com")
    return failures


def run(api: str, app: str, wrangler_path: Path, expected_env: str, minimum_verbs: int,
        reader=read, legacy: str = DEFAULT_LEGACY,
        artifact_pin_path: Path = Path(DEFAULT_ARTIFACT_PIN)) -> list[str]:
    failures = host_result(wrangler_path, api, app, legacy)
    try:
        failures += release_result(reader(api.rstrip("/") + "/release"), expected_env, minimum_verbs)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"/release unreadable ({type(error).__name__}: {error})")
    try:
        contract_floor = load_contract_floor(artifact_pin_path)
    except ValueError as error:
        # Fail closed: an unreadable/invalid pin means the contract floor
        # cannot be verified, so the run FAILS rather than silently skipping
        # the floor comparison and passing.
        failures.append(str(error))
        contract_floor = None
    try:
        failures += app_release_result(reader(app.rstrip("/") + "/app-release"), expected_env, contract_floor)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"/app-release unreadable ({type(error).__name__}: {error})")
    try:
        # The root is the unauthenticated surface.  /auth/login itself starts
        # the upstream Google flow and therefore redirects somewhere else.
        failures += auth_result(reader(app.rstrip("/") + "/", timeout=15), app)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"App auth unreadable ({type(error).__name__}: {error})")
    try:
        failures += legacy_result(reader(legacy.rstrip("/") + "/?stale=1", timeout=15), app)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"legacy Deal Room unreadable ({type(error).__name__}: {error})")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--app", "--dealroom", dest="app", default=DEFAULT_APP)
    parser.add_argument("--legacy", default=DEFAULT_LEGACY)
    parser.add_argument("--wrangler", default="mcp-server/wrangler.toml")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--min-verbs", type=int, default=DEFAULT_MIN_VERBS)
    parser.add_argument("--artifact-pin", default=DEFAULT_ARTIFACT_PIN)
    args = parser.parse_args(argv)
    try:
        failures = run(args.api, args.app, Path(args.wrangler), args.environment, args.min_verbs,
                       legacy=args.legacy, artifact_pin_path=Path(args.artifact_pin))
    except (OSError, ValueError) as error:
        print(f"doctorcre-production-smoke: ERROR {error}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"doctorcre-production-smoke: FAIL {failure}", file=sys.stderr)
        return 1
    print("doctorcre-production-smoke: OK CARR /release, DoctorCRE /app-release, app auth redirect, legacy redirect, environment, verb floor, and host ownership")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
