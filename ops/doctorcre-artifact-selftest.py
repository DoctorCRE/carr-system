#!/usr/bin/env python3
"""Seed failures into DoctorCRE verification, staging, and rollback."""
# doctrine: doctorcre-v5-astra-integration-review

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import call, patch

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "doctorcre-artifact.py"
SPEC = importlib.util.spec_from_file_location("doctorcre_artifact", TOOL)
assert SPEC and SPEC.loader
ARTIFACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARTIFACT)

FAILURES: list[str] = []
CONTRACTS = {
    "carr_interface": {"schema": "doctorcre-carr-interface.v1", "version": "1.0.0", "sha256": "1" * 64},
    "route_contract": {"schema": "doctorcre-app-routes.v1", "version": "1.0.0", "sha256": "2" * 64},
}


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=FAILURES)


def add_file(bundle: tarfile.TarFile, name: str, content: bytes) -> None:
    row = tarfile.TarInfo(name)
    row.size = len(content)
    row.mode = 0o644
    row.uid = row.gid = row.mtime = 0
    bundle.addfile(row, io.BytesIO(content))


def bundle(label: str, malicious_path: str | None = None, repository: str = "jbookout/doctorcre-app") -> tuple[dict, bytes, bytes]:
    files = {
        "index.html": f"<h1>{label}</h1>\n".encode(),
        "workspace.html": f"<main>{label}</main>\n".encode(),
    }
    rows = [
        {"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items())
    ]
    commit = hashlib.sha1(label.encode()).hexdigest()
    manifest = {
        "schema": "doctorcre-static-artifact.v1",
        "repository": repository,
        "source_commit": commit,
        "entrypoint": "workspace.html",
        "carr_interface": {"path": "contracts/carr-interface.v1.json", **CONTRACTS["carr_interface"]},
        "route_contract": {"path": "contracts/app-routes.v1.json", **CONTRACTS["route_contract"]},
        "files": rows,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        add_file(archive, "artifact-manifest.json", manifest_bytes)
        for name, content in files.items():
            add_file(archive, name, content)
        if malicious_path:
            add_file(archive, malicious_path, b"escape")
    archive_bytes = stream.getvalue()
    tag = f"app-v0.0.0-{label}"
    base = f"https://github.com/{repository}/releases/download/{tag}"
    pin = {
        "schema": "carr-doctorcre-artifact-pin.v1",
        "repository": repository,
        "release_tag": tag,
        "release_url": f"https://github.com/{repository}/releases/tag/{tag}",
        "source_commit": commit,
        "archive_url": f"{base}/doctorcre-app.tar",
        "archive_bytes": len(archive_bytes),
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "manifest_url": f"{base}/doctorcre-app.manifest.json",
        "manifest_bytes": len(manifest_bytes),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "file_count": len(files),
        "entrypoint": "workspace.html",
        "contracts": CONTRACTS,
    }
    return pin, archive_bytes, manifest_bytes


def current_digest(root: Path) -> str:
    target = (root / "current").readlink().as_posix()
    return target.rsplit("/", 1)[-1]


def test_download_failures() -> None:
    url = "https://github.com/example/release.tar"
    payload = b"verified bytes"

    for status in (502, 503, 504):
        failure = urllib.error.HTTPError(url, status, "transient", Message(), None)
        with patch.object(ARTIFACT.urllib.request, "urlopen", side_effect=[failure, io.BytesIO(payload)]) as request, patch("time.sleep") as sleep:
            try:
                result = ARTIFACT.download(url, len(payload))
            except urllib.error.HTTPError:
                result = None
            check(f"download recovers from HTTP {status}", result == payload and request.call_count == 2)
            check(f"download backs off after HTTP {status}", sleep.call_args_list == [call(1)])

    failure = urllib.error.HTTPError(url, 504, "unavailable", Message(), None)
    with patch.object(ARTIFACT.urllib.request, "urlopen", side_effect=failure) as request, patch("time.sleep") as sleep:
        try:
            ARTIFACT.download(url, len(payload))
        except urllib.error.HTTPError as exc:
            check("persistent outage preserves the error and stops after three attempts", exc is failure and request.call_count == 3)
        else:
            check("persistent outage fails closed", False)
        check("persistent outage has two bounded backoffs", sleep.call_args_list == [call(1), call(2)])

    for status in (403, 404):
        failure = urllib.error.HTTPError(url, status, "permanent", Message(), None)
        with patch.object(ARTIFACT.urllib.request, "urlopen", side_effect=failure) as request, patch("time.sleep") as sleep:
            try:
                ARTIFACT.download(url, len(payload))
            except urllib.error.HTTPError as exc:
                check(f"HTTP {status} fails immediately", exc is failure and request.call_count == 1 and not sleep.called)
            else:
                check(f"HTTP {status} fails closed", False)

    with patch.object(ARTIFACT.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as request, patch("time.sleep") as sleep:
        try:
            ARTIFACT.download(url, len(payload) + 1)
        except ValueError:
            check("wrong byte count fails without retry", request.call_count == 1 and not sleep.called)
        else:
            check("wrong byte count fails closed", False)


def test_download_cache() -> None:
    pin, archive, manifest = bundle("cache")
    with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)):
        with patch.object(ARTIFACT, "download", side_effect=[archive, manifest]) as fetch:
            result = ARTIFACT.artifact_inputs(pin)
            ARTIFACT.verify(pin, *result)
            check("cold cache downloads and verifies exact artifact", result == (archive, manifest) and fetch.call_count == 2)
        with patch.object(ARTIFACT, "download", side_effect=AssertionError("offline cache must not fetch")):
            try:
                result = ARTIFACT.artifact_inputs(pin)
                check("warm cache works offline", result == (archive, manifest))
            except AssertionError:
                check("warm cache works offline", False)
        cache = Path(directory) / '.cache/doctorcre-artifact' / pin['archive_sha256'] / pin['manifest_sha256']
        for filename in ('doctorcre-app.tar', 'doctorcre-app.manifest.json'):
            path = cache / filename
            if not path.exists():
                check("verified files persisted for actions/cache", False)
                continue
            original = path.read_bytes()
            path.write_bytes(b'X' * len(original))
            with patch.object(ARTIFACT, "download", side_effect=AssertionError("corruption must refuse")) as fetch:
                try:
                    ARTIFACT.artifact_inputs(pin)
                except ValueError:
                    check("cached " + filename + " tamper refused without fetch", not fetch.called)
                else:
                    check("cached tamper refused", False)
            path.write_bytes(original)
        (cache / 'doctorcre-app.manifest.json').unlink(missing_ok=True)
        with patch.object(ARTIFACT, "download", side_effect=[archive, manifest]) as fetch:
            result = ARTIFACT.artifact_inputs(pin)
            check("interrupted cache fill recovers with a verified pair", result == (archive, manifest) and fetch.call_count == 2)
        other_pin, other_archive, other_manifest = bundle("changed-pin")
        with patch.object(ARTIFACT, "download", side_effect=[other_archive, other_manifest]) as fetch:
            result = ARTIFACT.artifact_inputs(other_pin)
            check("changed pin cannot reuse previous bytes", result == (other_archive, other_manifest) and fetch.call_count == 2)
    for mode in ('bad-archive', 'bad-manifest', 'outage'):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)):
            effects: list[bytes | urllib.error.HTTPError] = [archive, b'invalid'] if mode == 'bad-manifest' else [b'invalid', manifest]
            if mode == 'outage':
                effects = [urllib.error.HTTPError(pin['archive_url'], 504, 'outage', Message(), None)]
            with patch.object(ARTIFACT, "download", side_effect=effects):
                try:
                    result = ARTIFACT.artifact_inputs(pin)
                    ARTIFACT.verify(pin, *result)
                except (ValueError, urllib.error.HTTPError):
                    check(mode + " leaves no published cache files", not list(Path(directory).rglob('doctorcre-app.*')))
                else:
                    check(mode + " refused", False)


def main() -> int:
    print("doctorcre-artifact-selftest: exact pin and reversible activation")
    test_download_failures()
    test_download_cache()
    prior_pin, prior_archive, prior_manifest = bundle("prior")
    candidate_pin, candidate_archive, candidate_manifest = bundle("candidate", repository="DoctorCRE/doctorcre-app")

    manifest, files = ARTIFACT.verify(candidate_pin, candidate_archive, candidate_manifest)
    check("1. exact artifact verifies", manifest["source_commit"] == candidate_pin["source_commit"] and len(files) == 2)

    changed = bytearray(candidate_archive)
    changed[600] ^= 1
    try:
        ARTIFACT.verify(candidate_pin, bytes(changed), candidate_manifest)
    except ValueError as exc:
        check("2. changed archive is refused", "digest mismatch" in str(exc))
    else:
        check("2. changed archive is refused", False)

    unsafe_pin, unsafe_archive, unsafe_manifest = bundle("unsafe", "../escape")
    try:
        ARTIFACT.verify(unsafe_pin, unsafe_archive, unsafe_manifest)
    except ValueError as exc:
        check("3. traversal entry is refused", "unsafe or duplicate" in str(exc))
    else:
        check("3. traversal entry is refused", False)

    with tempfile.TemporaryDirectory(prefix="doctorcre-rollback-") as tmp:
        root = Path(tmp) / "artifacts"
        ARTIFACT.materialize(root, prior_pin, prior_archive, prior_manifest)
        ARTIFACT.materialize(root, candidate_pin, candidate_archive, candidate_manifest)
        check("4. candidate activates", current_digest(root) == candidate_pin["archive_sha256"])

        rehearsals = 0
        for cycle in range(2):
            ARTIFACT.activate(root, prior_pin["archive_sha256"])
            if current_digest(root) == prior_pin["archive_sha256"] and (root / "current" / "workspace.html").read_text() == "<main>prior</main>\n":
                rehearsals += 1
            ARTIFACT.activate(root, candidate_pin["archive_sha256"])
            check(f"5{chr(97 + cycle)}. candidate re-activates after rollback {cycle + 1}", current_digest(root) == candidate_pin["archive_sha256"])
        check("6. two rollback rehearsals succeed", rehearsals == 2)

        prior_file = root / "versions" / prior_pin["archive_sha256"] / "workspace.html"
        prior_file.write_text("tampered\n")
        try:
            ARTIFACT.activate(root, prior_pin["archive_sha256"])
        except ValueError as exc:
            check("7. cached artifact tampering blocks activation", "differs from its receipt" in str(exc))
        else:
            check("7. cached artifact tampering blocks activation", False)

    with tempfile.TemporaryDirectory() as directory:
        pin = json.loads((REPO / 'ops/config/doctorcre-artifact.v1.json').read_text())
        for repo, accepted in [('DoctorCRE/doctorcre-app', True), ('jbookout/doctorcre-app', True),
                               ('DoctorCRE/carr-system', False), ('DoctorCRE/unrelated', False),
                               ('someone/doctorcre-app', False)]:
            candidate = json.loads(json.dumps(pin).replace(pin['repository'], repo))
            path = Path(directory) / 'pin.json'
            path.write_text(json.dumps(candidate))
            try:
                ARTIFACT.load_pin(path)
                check('repository identity ' + repo, accepted)
            except ValueError:
                check('repository refusal ' + repo, not accepted)

    if FAILURES:
        print(f"doctorcre-artifact-selftest: {len(FAILURES)} FAILED")
        return 1
    print("doctorcre-artifact-selftest: PASS; two local rollback rehearsals completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
