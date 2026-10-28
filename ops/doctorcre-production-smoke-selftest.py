#!/usr/bin/env python3
"""Deterministic acceptance tests for doctorcre-production-smoke.py."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("smoke", HERE / "doctorcre-production-smoke.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reply(status: int, body: object = b"", location: str | None = None) -> Any:
    headers = {"Location": location} if location else {}
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return smoke.Reply(status, headers, raw)


def write_pin(directory: Path, name: str, carr_version: str, route_version: str) -> Path:
    """A minimal DoctorCRE artifact pin fixture -- only the 'contracts' block
    that load_contract_floor() actually reads matters for these tests."""
    path = directory / name
    path.write_text(json.dumps({
        "contracts": {
            "carr_interface": {"schema": "doctorcre-carr-interface.v1", "version": carr_version},
            "route_contract": {"schema": "doctorcre-app-routes.v1", "version": route_version},
        }
    }), encoding="utf-8")
    return path


def app_release_reader(carr_version: str, route_version: str):
    def reader(url: str, timeout: int = 15) -> Any:
        if url.endswith("/release"):
            return reply(200, {"ok": True, "env": {"value": "production"}, "verb_count": 12})
        if url.endswith("/app-release"):
            return reply(200, {
                "service": "doctorcre-app",
                "environment": "production",
                "source_commit": "a" * 40,
                "provider_version_id": "12345678-1234-4123-8123-123456789abc",
                "carr_contract": {"schema": "doctorcre-carr-interface.v1", "version": carr_version},
                "route_contract": {"schema": "doctorcre-app-routes.v1", "version": route_version},
            })
        if url.startswith("https://app.doctorcre.com/"):
            return reply(302, location="/auth/login?return_to=%2F")
        return reply(302, location="https://app.doctorcre.com/deals")
    return reader


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_directory:
        directory = Path(raw_directory)
        wrangler = directory / "wrangler.toml"
        wrangler.write_text(
            'routes = [{ pattern = "api.practicecre.com" }, { pattern = "api.doctorcre.com" }, { pattern = "dealroom.doctorcre.com" }, { pattern = "reports.doctorcre.com" }]\n',
            encoding="utf-8",
        )
        pin = write_pin(directory, "artifact-pin.json", "1.20.0", "1.11.0")

        calls: list[str] = []

        def good_reader(url: str, timeout: int = 15) -> Any:
            calls.append(url)
            return app_release_reader("1.20.0", "1.11.0")(url, timeout)

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, good_reader, artifact_pin_path=pin)
        check(not failures, f"healthy fixture failed: {failures}")
        check(calls == ["https://api.doctorcre.com/release", "https://app.doctorcre.com/app-release", "https://app.doctorcre.com/", "https://dealroom.doctorcre.com/?stale=1"],
              f"unexpected requests: {calls}")

        # A contract version that has advanced past the pin is an ORDINARY app
        # release, not a deployment mismatch: app_release_result must accept
        # any well-formed semver at or above the pinned floor rather than
        # freezing on one literal value (the bug behind the PR that first
        # fixed doctorcre-production-smoke.py, after production legitimately
        # moved from carr_contract 1.1.0 to 1.25.0 without any deployment
        # defect) -- and it must accept the floor value EXACTLY, not only
        # strictly-newer versions.
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.25.0", "1.13.0"),
                             artifact_pin_path=pin)
        check(not failures, f"a version above the pinned floor was wrongly rejected: {failures}")

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.20.0", "1.11.0"),
                             artifact_pin_path=pin)
        check(not failures, f"a version exactly at the pinned floor was wrongly rejected: {failures}")

        # 1. Below the pin (e.g. the app rolled back) must FAIL -- this is the
        # whole point of the floor: CARR must never accept a served contract
        # older than what CARR itself was built and verified against.
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.19.0", "1.11.0"),
                             artifact_pin_path=pin)
        check(any("carr_contract" in item and "below the pinned floor" in item for item in failures),
              f"a carr_contract version below the pin was not caught: {failures}")

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.20.0", "1.9.0"),
                             artifact_pin_path=pin)
        check(any("route_contract" in item and "below the pinned floor" in item for item in failures),
              f"a route_contract version below the pin was not caught: {failures}")

        # Numeric, not string, comparison: "1.9.0" < "1.10.0" numerically but
        # would compare the OTHER way as plain strings ("1.9.0" > "1.10.0").
        strict_pin = write_pin(directory, "strict-pin.json", "1.10.0", "1.0.0")
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.9.0", "1.0.0"),
                             artifact_pin_path=strict_pin)
        check(any("carr_contract" in item and "below the pinned floor" in item for item in failures),
              f"string-order comparison let 1.9.0 pass a 1.10.0 floor: {failures}")
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.10.0", "1.0.0"),
                             artifact_pin_path=strict_pin)
        check(not failures, f"1.10.0 wrongly failed a 1.10.0 floor (string comparison bug): {failures}")

        # 4. A missing or unreadable pin file must fail closed -- the smoke
        # check must FAIL, not silently skip the floor and pass.
        missing_pin = directory / "does-not-exist.json"
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.25.0", "1.13.0"),
                             artifact_pin_path=missing_pin)
        check(any("unreadable" in item for item in failures),
              f"a missing artifact pin did not fail closed: {failures}")

        malformed_pin = directory / "malformed-pin.json"
        malformed_pin.write_text("not json", encoding="utf-8")
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.25.0", "1.13.0"),
                             artifact_pin_path=malformed_pin)
        check(any("not valid JSON" in item for item in failures),
              f"a malformed artifact pin did not fail closed: {failures}")

        no_contracts_pin = directory / "no-contracts-pin.json"
        no_contracts_pin.write_text(json.dumps({"schema": "carr-doctorcre-artifact-pin.v1"}), encoding="utf-8")
        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, app_release_reader("1.25.0", "1.13.0"),
                             artifact_pin_path=no_contracts_pin)
        check(any("no contracts block" in item for item in failures),
              f"a pin with no contracts block did not fail closed: {failures}")

        # The schema NAME is still load-bearing: a rename or malformed version
        # must still fail the smoke check regardless of the floor.
        floor = smoke.load_contract_floor(pin)
        check(any("carr_contract" in item for item in smoke.app_release_result(
            reply(200, {
                "service": "doctorcre-app", "environment": "production",
                "source_commit": "a" * 40,
                "provider_version_id": "12345678-1234-4123-8123-123456789abc",
                "carr_contract": {"schema": "doctorcre-carr-interface.v2", "version": "1.25.0"},
                "route_contract": {"schema": "doctorcre-app-routes.v1", "version": "1.13.0"},
            }), "production", floor)), "a renamed CARR contract schema was accepted")
        check(any("route_contract" in item for item in smoke.app_release_result(
            reply(200, {
                "service": "doctorcre-app", "environment": "production",
                "source_commit": "a" * 40,
                "provider_version_id": "12345678-1234-4123-8123-123456789abc",
                "carr_contract": {"schema": "doctorcre-carr-interface.v1", "version": "1.25.0"},
                "route_contract": {"schema": "doctorcre-app-routes.v1", "version": "not-a-semver"},
            }), "production", floor)), "a malformed route contract version was accepted")

        def bad_reader(url: str, timeout: int = 15) -> Any:
            if url.endswith("/release"):
                return reply(200, {"ok": True, "env": {"value": "staging"}, "verb_count": 0})
            return reply(200, {"html": "login"})

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, bad_reader, artifact_pin_path=pin)
        check(any("environment" in item for item in failures), "wrong environment was not caught")
        check(any("verb_count" in item for item in failures), "verb floor was not caught")
        check(any("auth" in item for item in failures), "missing auth redirect was not caught")
        check(any("/app-release" in item for item in failures), "wrong app release was not caught")

        old_wrangler = directory / "old-wrangler.toml"
        old_wrangler.write_text(wrangler.read_text() + 'routes = [{ pattern = "app.doctorcre.com" }]\n', encoding="utf-8")
        check(any("still claims" in item for item in smoke.host_result(
            old_wrangler, "https://api.doctorcre.com", "https://app.doctorcre.com")),
            "the prior CARR-owned app host was accepted")

        failures = smoke.host_result(wrangler, "https://evil.invalid", "https://app.doctorcre.com")
        check(any("API URL origin" in item for item in failures), "wrong API host was not caught")
        check(any("API URL origin" in item for item in smoke.host_result(
            wrangler, "http://api.doctorcre.com", "https://app.doctorcre.com")),
            "insecure API origin was accepted")
        check(any("App URL origin" in item for item in smoke.host_result(
            wrangler, "https://api.doctorcre.com", "https://app.doctorcre.com:8443")),
            "nonstandard Deal Room port was accepted")

        check(not smoke.auth_result(
            reply(302, location="https://app.doctorcre.com/auth/login?return_to=%2F"),
            "https://app.doctorcre.com"), "same-origin absolute auth redirect was refused")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://attacker.invalid/auth/login"),
            "https://app.doctorcre.com")), "cross-origin auth redirect was accepted")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://app.doctorcre.com:8443/auth/login"),
            "https://app.doctorcre.com")), "nonstandard-port auth redirect was accepted")
        print("doctorcre-production-smoke-selftest: 3/3 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
