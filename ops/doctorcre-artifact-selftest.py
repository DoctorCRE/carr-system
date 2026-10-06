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
from pathlib import Path

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


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def add_file(bundle: tarfile.TarFile, name: str, content: bytes) -> None:
    row = tarfile.TarInfo(name)
    row.size = len(content)
    row.mode = 0o644
    row.uid = row.gid = row.mtime = 0
    bundle.addfile(row, io.BytesIO(content))


def bundle(label: str, malicious_path: str | None = None) -> tuple[dict, bytes, bytes]:
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
        "repository": "jbookout/doctorcre-app",
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
    base = f"https://github.com/jbookout/doctorcre-app/releases/download/{tag}"
    pin = {
        "schema": "carr-doctorcre-artifact-pin.v1",
        "repository": "jbookout/doctorcre-app",
        "release_tag": tag,
        "release_url": f"https://github.com/jbookout/doctorcre-app/releases/tag/{tag}",
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


def main() -> int:
    print("doctorcre-artifact-selftest: exact pin and reversible activation")
    prior_pin, prior_archive, prior_manifest = bundle("prior")
    candidate_pin, candidate_archive, candidate_manifest = bundle("candidate")

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

    if FAILURES:
        print(f"doctorcre-artifact-selftest: {len(FAILURES)} FAILED")
        return 1
    print("doctorcre-artifact-selftest: PASS; two local rollback rehearsals completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
