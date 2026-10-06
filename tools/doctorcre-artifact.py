"""Verify and atomically stage the exact DoctorCRE artifact pinned by CARR."""
# doctrine: doctorcre-v5-astra-integration-review

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
SHA256 = frozenset("0123456789abcdef")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def valid_sha(value: object, length: int = 64) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= SHA256


def load_pin(path: Path) -> dict[str, Any]:
    pin = json_object(path.read_bytes(), "DoctorCRE pin")
    required = {
        "schema", "repository", "release_tag", "release_url", "source_commit",
        "archive_url", "archive_bytes", "archive_sha256", "manifest_url",
        "manifest_bytes", "manifest_sha256", "file_count", "entrypoint", "contracts",
    }
    if set(pin) != required:
        raise ValueError("DoctorCRE pin fields do not match the v1 contract")
    if pin["schema"] != "carr-doctorcre-artifact-pin.v1":
        raise ValueError("unsupported DoctorCRE pin schema")
    if pin["repository"] != "jbookout/doctorcre-app":
        raise ValueError("DoctorCRE pin names an unexpected repository")
    if not isinstance(pin["release_tag"], str) or not pin["release_tag"].startswith("app-v"):
        raise ValueError("DoctorCRE release tag is invalid")
    if not valid_sha(pin["source_commit"], 40):
        raise ValueError("DoctorCRE source commit must be a full Git SHA")
    for key in ("archive_sha256", "manifest_sha256"):
        if not valid_sha(pin[key]):
            raise ValueError(f"DoctorCRE {key} is invalid")
    for key, limit in (("archive_bytes", MAX_ARCHIVE_BYTES), ("manifest_bytes", MAX_FILE_BYTES), ("file_count", 10000)):
        if not isinstance(pin[key], int) or not 0 < pin[key] <= limit:
            raise ValueError(f"DoctorCRE {key} is invalid")
    base = f"https://github.com/{pin['repository']}/releases/download/{pin['release_tag']}"
    if pin["archive_url"] != f"{base}/doctorcre-app.tar" or pin["manifest_url"] != f"{base}/doctorcre-app.manifest.json":
        raise ValueError("DoctorCRE artifact URLs do not match the pinned repository and tag")
    if pin["release_url"] != f"https://github.com/{pin['repository']}/releases/tag/{pin['release_tag']}":
        raise ValueError("DoctorCRE release URL does not match the pinned repository and tag")
    if not isinstance(pin["entrypoint"], str) or not safe_path(pin["entrypoint"]):
        raise ValueError("DoctorCRE entrypoint is invalid")
    if not isinstance(pin["contracts"], dict) or set(pin["contracts"]) != {"carr_interface", "route_contract"}:
        raise ValueError("DoctorCRE contract pin is invalid")
    for contract in pin["contracts"].values():
        if not isinstance(contract, dict) or set(contract) != {"schema", "version", "sha256"}:
            raise ValueError("DoctorCRE contract identity is invalid")
        if not all(isinstance(contract.get(key), str) and contract[key] for key in ("schema", "version")) or not valid_sha(contract["sha256"]):
            raise ValueError("DoctorCRE contract identity is invalid")
    return pin


def safe_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        return False
    path = PurePosixPath(value)
    return path.as_posix() == value and all(part not in ("", ".", "..") for part in path.parts)


def download(url: str, expected_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "carr-system-doctorcre-artifact/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) != expected_bytes:
        raise ValueError(f"downloaded byte count mismatch for {url}")
    return data


def artifact_inputs(pin: dict[str, Any], archive_path: Path | None = None,
                    manifest_path: Path | None = None) -> tuple[bytes, bytes]:
    if (archive_path is None) != (manifest_path is None):
        raise ValueError("--archive and --manifest must be supplied together")
    if archive_path is None:
        return (download(pin["archive_url"], pin["archive_bytes"]),
                download(pin["manifest_url"], pin["manifest_bytes"]))
    archive = archive_path.read_bytes()
    manifest = manifest_path.read_bytes()  # type: ignore[union-attr]
    if len(archive) != pin["archive_bytes"] or len(manifest) != pin["manifest_bytes"]:
        raise ValueError("local DoctorCRE artifact byte count does not match the pin")
    return archive, manifest


def verify(pin: dict[str, Any], archive: bytes, detached_manifest: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(archive) > MAX_ARCHIVE_BYTES or digest(archive) != pin["archive_sha256"]:
        raise ValueError("DoctorCRE archive digest mismatch")
    if digest(detached_manifest) != pin["manifest_sha256"]:
        raise ValueError("DoctorCRE manifest digest mismatch")
    manifest = json_object(detached_manifest, "DoctorCRE manifest")
    expected_manifest_keys = {"schema", "repository", "source_commit", "entrypoint", "carr_interface", "route_contract", "files"}
    if set(manifest) != expected_manifest_keys or manifest["schema"] != "doctorcre-static-artifact.v1":
        raise ValueError("DoctorCRE manifest fields do not match the v1 contract")
    if (manifest["repository"] != pin["repository"] or manifest["source_commit"] != pin["source_commit"]
            or manifest["entrypoint"] != pin["entrypoint"]):
        raise ValueError("DoctorCRE manifest identity does not match the pin")
    for manifest_key, pin_key in (("carr_interface", "carr_interface"), ("route_contract", "route_contract")):
        row = manifest.get(manifest_key)
        expected = pin["contracts"][pin_key]
        if not isinstance(row, dict) or {key: row.get(key) for key in ("schema", "version", "sha256")} != expected:
            raise ValueError(f"DoctorCRE {manifest_key} identity does not match the pin")
    rows = manifest.get("files")
    if not isinstance(rows, list) or len(rows) != pin["file_count"]:
        raise ValueError("DoctorCRE manifest file count does not match the pin")

    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or not safe_path(member.name) or member.name in members:
                raise ValueError(f"unsafe or duplicate DoctorCRE archive entry: {member.name}")
            if member.uid != 0 or member.gid != 0 or member.mtime != 0 or member.mode != 0o644 or member.size > MAX_FILE_BYTES:
                raise ValueError(f"non-reproducible DoctorCRE archive metadata: {member.name}")
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f"unreadable DoctorCRE archive entry: {member.name}")
            members[member.name] = stream.read()
    if members.pop("artifact-manifest.json", None) != detached_manifest:
        raise ValueError("embedded and detached DoctorCRE manifests differ")

    expected_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("DoctorCRE manifest contains an invalid file row")
        path, size, sha = row["path"], row["bytes"], row["sha256"]
        if not safe_path(path) or path in expected_paths or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES or not valid_sha(sha):
            raise ValueError("DoctorCRE manifest contains an invalid file identity")
        expected_paths.add(path)
        content = members.get(path)
        if content is None or len(content) != size or digest(content) != sha:
            raise ValueError(f"DoctorCRE payload does not match its manifest: {path}")
    if set(members) != expected_paths or pin["entrypoint"] not in expected_paths:
        raise ValueError("DoctorCRE archive and manifest file sets differ")
    return manifest, members


def receipt(pin: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "carr-doctorcre-artifact-receipt.v1",
        "repository": pin["repository"], "release_tag": pin["release_tag"],
        "source_commit": pin["source_commit"], "archive_sha256": pin["archive_sha256"],
        "manifest_sha256": pin["manifest_sha256"], "file_count": pin["file_count"],
        "entrypoint": pin["entrypoint"], "contracts": pin["contracts"], "files": manifest["files"],
    }


def verify_materialized(directory: Path, saved: dict[str, Any]) -> None:
    rows = saved.get("files")
    if not isinstance(rows, list):
        raise ValueError("cached DoctorCRE receipt is invalid")
    expected: set[str] = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
                or not safe_path(row.get("path")) or row["path"] in expected
                or not isinstance(row.get("bytes"), int) or not 0 <= row["bytes"] <= MAX_FILE_BYTES
                or not valid_sha(row.get("sha256"))):
            raise ValueError("cached DoctorCRE receipt contains an invalid file row")
        expected.add(row["path"])
    all_paths = list(directory.rglob("*"))
    if any(path.is_symlink() for path in all_paths):
        raise ValueError("materialized DoctorCRE tree contains a symbolic link")
    paths = [path for path in all_paths if not path.is_dir()]
    if any(not path.is_file() for path in paths):
        raise ValueError("materialized DoctorCRE tree contains a non-regular file")
    actual = {path.relative_to(directory).as_posix() for path in paths}
    if actual != expected:
        raise ValueError("materialized DoctorCRE file set differs from its receipt")
    for row in rows:
        content = (directory / Path(*PurePosixPath(row["path"]).parts)).read_bytes()
        if len(content) != row["bytes"] or digest(content) != row["sha256"]:
            raise ValueError(f"materialized DoctorCRE file differs from its receipt: {row['path']}")


def activate(root: Path, archive_sha256: str) -> Path:
    version = root / "versions" / archive_sha256
    receipt_path = root / "receipts" / f"{archive_sha256}.json"
    if not version.is_dir() or not receipt_path.is_file():
        raise ValueError("requested DoctorCRE artifact is not materialized")
    saved = json_object(receipt_path.read_bytes(), "cached DoctorCRE receipt")
    if saved.get("archive_sha256") != archive_sha256:
        raise ValueError("cached DoctorCRE receipt names a different digest")
    verify_materialized(version, saved)
    root.mkdir(parents=True, exist_ok=True)
    temporary_link = root / f".current-{os.getpid()}"
    temporary_link.unlink(missing_ok=True)
    os.symlink(f"versions/{archive_sha256}", temporary_link)
    os.replace(temporary_link, root / "current")
    return version


def materialize(root: Path, pin: dict[str, Any], archive: bytes,
                detached_manifest: bytes) -> dict[str, Any]:
    manifest, members = verify(pin, archive, detached_manifest)
    saved = receipt(pin, manifest)
    version = root / "versions" / pin["archive_sha256"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "versions").mkdir(exist_ok=True)
    (root / "receipts").mkdir(exist_ok=True)
    if version.exists():
        verify_materialized(version, saved)
    else:
        temporary = Path(tempfile.mkdtemp(prefix=".doctorcre-", dir=root / "versions"))
        try:
            for name, content in members.items():
                destination = temporary / Path(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                destination.chmod(0o644)
            try:
                os.replace(temporary, version)
            except FileExistsError:
                verify_materialized(version, saved)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    receipt_path = root / "receipts" / f"{pin['archive_sha256']}.json"
    temporary_receipt = receipt_path.with_suffix(f".tmp-{os.getpid()}")
    temporary_receipt.write_text(json.dumps(saved, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary_receipt, receipt_path)
    activate(root, pin["archive_sha256"])
    return saved


def run_cli(argv: list[str] | None = None) -> int:
    """Library entry used only by the already-registered release CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "materialize", "activate"))
    parser.add_argument("--pin", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args(argv)
    if args.command == "activate":
        if args.root is None or not valid_sha(args.sha256):
            parser.error("activate requires --root and a full --sha256")
        version = activate(args.root, args.sha256)
        print(json.dumps({"ok": True, "active": args.sha256, "directory": str(version)}, sort_keys=True))
        return 0
    if args.pin is None:
        parser.error(f"{args.command} requires --pin")
    pin = load_pin(args.pin)
    archive, manifest_bytes = artifact_inputs(pin, args.archive, args.manifest)
    if args.command == "verify":
        manifest, _ = verify(pin, archive, manifest_bytes)
        saved = receipt(pin, manifest)
        print(json.dumps({key: saved[key] for key in ("schema", "repository", "release_tag", "source_commit", "archive_sha256", "manifest_sha256", "file_count")}, sort_keys=True))
        return 0
    if args.root is None:
        parser.error("materialize requires --root")
    saved = materialize(args.root, pin, archive, manifest_bytes)
    print(json.dumps({key: saved[key] for key in ("schema", "repository", "release_tag", "source_commit", "archive_sha256", "manifest_sha256", "file_count")}, sort_keys=True))
    return 0
