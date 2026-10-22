#!/usr/bin/env python3
"""Static Outlook bundle atlas. All output goes to stdout; inputs are read-only."""

import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import plistlib
import re
import sys
from urllib.parse import urlsplit


SCHEMA = "outlook-surface-atlas/v1"
PLIST_KEYS = {
    "CFBundleURLTypes": "url_scheme",
    "CFBundleDocumentTypes": "document_type",
    "NSUserActivityTypes": "user_activity",
    "INIntentsSupported": "supported_intent",
}
MACHO_MAGICS = {
    bytes.fromhex(x) for x in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe",
        "cafebabe", "bebafeca", "cafebabf", "bfbafeca")
}
URL_LITERAL = re.compile(rb"ms-outlook://[\x21-\x7e]+")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _plist(raw):
    obj = plistlib.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("unsupported plist declaration shape")
    for key, kind in PLIST_KEYS.items():
        values = obj.get(key, [])
        item_type = dict if kind in ("url_scheme", "document_type") else str
        if not isinstance(values, list) or any(not isinstance(v, item_type) for v in values):
            raise ValueError("unsupported plist declaration shape")
        if kind == "url_scheme":
            for value in values:
                schemes = value.get("CFBundleURLSchemes")
                if not isinstance(schemes, list) or any(not isinstance(v, str) for v in schemes):
                    raise ValueError("unsupported URL declaration shape")
    return obj


def extract_surface(app, version, baseline=None):
    """Inventory static declarations, never execute code or consult a profile."""
    app = Path(app).resolve(strict=True)
    root_info = app / "Contents/Info.plist"
    if root_info.is_symlink() or root_info.parent.is_symlink():
        raise ValueError("root Info.plist must not be a symlink")
    if baseline is not None and baseline.get("schema") != SCHEMA:
        raise ValueError("unsupported surface atlas schema")
    initial = root_info.read_bytes()
    info = _plist(initial)
    if info.get("CFBundleIdentifier") != "com.microsoft.Outlook":
        raise ValueError("expected an Outlook application bundle")
    if info.get("CFBundleVersion") != version:
        raise ValueError("requested CFBundleVersion does not match the bundle")
    result = {
        "schema": SCHEMA,
        "outlook": {key: info[key] for key in (
            "CFBundleIdentifier", "CFBundleVersion", "CFBundleShortVersionString")},
        "scope": "static declarations and binary URL literals; runtime behavior untested",
        "sources": {},
        "surfaces": {},
        "skipped_symlinks": [],
    }

    def add(kind, owner, identity, value, evidence):
        key = f"{kind}:{owner}:{_json(identity)}"
        old = (baseline or {}).get("surfaces", {}).get(key, {})
        if key in result["surfaces"]:
            if kind != "url_literal" and result["surfaces"][key]["value"] != value:
                raise ValueError("conflicting static declarations share one identity")
            result["surfaces"][key]["evidence"].append(evidence)
            return
        result["surfaces"][key] = {
            "kind": kind, "owner": owner, "value": value,
            "confidence": "proven", "claim": "static declaration only",
            "first_seen_version": old.get("first_seen_version", version),
            "evidence": [evidence],
        }

    def walk_error(error):
        raise error

    for parent, dirs, files in os.walk(app, followlinks=False, onerror=walk_error):
        result["skipped_symlinks"].extend(
            (Path(parent) / d).relative_to(app).as_posix() for d in dirs if (Path(parent) / d).is_symlink())
        dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
        for name in sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                result["skipped_symlinks"].append(path.relative_to(app).as_posix())
                continue
            rel = path.relative_to(app).as_posix()
            if name not in ("Info.plist", "extract.actionsdata"):
                if name.startswith(".") or name.casefold() in {"credentials", "tokens", "keys", "keychain", "keychains"}:
                    continue
                framework_binary = any(part.endswith(".framework") and name == part[:-10] for part in path.parts)
                if "MacOS" not in path.parts and path.suffix not in (".dylib", ".so") and not framework_binary:
                    continue
                with path.open("rb") as stream:
                    if stream.read(4) not in MACHO_MAGICS:
                        continue
                    before = os.fstat(stream.fileno())
                    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                        result["sources"][rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                        for match in URL_LITERAL.finditer(data):
                            parsed = urlsplit(match.group().decode("ascii"))
                            if not re.fullmatch(r"[A-Za-z0-9._-]+", parsed.netloc):
                                continue
                            value = {
                                "scheme": "ms-outlook", "host": parsed.netloc,
                                "path": parsed.path,
                                "query_keys": sorted({p.split("=", 1)[0] for p in parsed.query.split("&") if p}),
                            }
                            evidence = {"source": rel, "offset": match.start(), "length": len(match.group())}
                            identity = [value["host"], value["path"]]
                            add("url_literal", "bundle", identity, value, evidence)
                            key = f"url_literal:bundle:{_json(identity)}"
                            existing = result["surfaces"][key]["value"]
                            existing["query_keys"] = sorted(set(existing["query_keys"]) | set(value["query_keys"]))
                        after = os.fstat(stream.fileno())
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise ValueError("binary changed during extraction")
                continue
            if name == "extract.actionsdata" and path.parent.name != "Metadata.appintents":
                continue
            raw = path.read_bytes()
            result["sources"][rel] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            if name == "extract.actionsdata":
                obj = json.loads(raw)
                if not isinstance(obj, dict) or not isinstance(obj.get("actions"), dict):
                    raise ValueError("unsupported App Intents actions shape")
                for key, value in sorted(obj["actions"].items()):
                    if not isinstance(value, dict):
                        raise ValueError("unsupported App Intents actions shape")
                    pointer = key.replace("~", "~0").replace("/", "~1")
                    add("app_intent", rel, key, value, {"source": rel, "pointer": f"/actions/{pointer}"})
                continue
            obj = _plist(raw)
            if "PlugIns" in path.relative_to(app).parts and path.parent.parent.parent.name == "PlugIns":
                add("plugin", rel, obj["CFBundleIdentifier"], {
                    key: obj[key] for key in ("CFBundleIdentifier", "CFBundleVersion", "NSExtension") if key in obj
                }, {"source": rel, "pointer": ""})
            for field, kind in PLIST_KEYS.items():
                for index, value in enumerate(obj.get(field, [])):
                    evidence = {"source": rel, "pointer": f"/{field}/{index}"}
                    if kind == "url_scheme":
                        for scheme in value["CFBundleURLSchemes"]:
                            add(kind, rel, scheme, {**value, "scheme": scheme}, evidence)
                    elif kind == "document_type":
                        identity = [value.get(k) for k in (
                            "CFBundleTypeName", "CFBundleTypeExtensions", "LSItemContentTypes", "CFBundleTypeOSTypes")]
                        add(kind, rel, identity, value, evidence)
                    else:
                        add(kind, rel, value, value, evidence)
    if root_info.read_bytes() != initial:
        raise ValueError("bundle changed during extraction; retry after the update finishes")
    result["surfaces"] = dict(sorted(result["surfaces"].items()))
    result["skipped_symlinks"].sort()
    for entry in result["surfaces"].values():
        if entry["kind"] == "url_literal":
            entry["claim"] = "literal grammar only; runtime acceptance unknown"
    return result


def _changes(before, after, pointer=""):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = {}
        for key in sorted(before.keys() | after.keys()):
            part = key.replace("~", "~0").replace("/", "~1")
            location = f"{pointer}/{part}"
            if key not in before or key not in after:
                changes[location] = {
                    "before": before.get(key), "after": after.get(key),
                    "before_present": key in before, "after_present": key in after,
                }
            else:
                changes.update(_changes(before[key], after[key], location))
        return changes
    return {pointer: {"before": before, "after": after}} if before != after else {}


def diff_surface(before, after):
    """Compare declarations separately from source hashes and evidence movement."""
    if before.get("schema") != SCHEMA or after.get("schema") != SCHEMA:
        raise ValueError("unsupported surface atlas schema")
    left, right = before["surfaces"], after["surfaces"]
    changed = {}
    evidence_changed = []
    for key in sorted(left.keys() & right.keys()):
        fields = _changes(
            {k: v for k, v in left[key].items() if k not in ("evidence", "first_seen_version")},
            {k: v for k, v in right[key].items() if k not in ("evidence", "first_seen_version")},
        )
        if fields:
            changed[key] = {"kind": right[key]["kind"], "fields": fields}
        if left[key]["evidence"] != right[key]["evidence"]:
            evidence_changed.append(key)
    source_changes = {
        key: {"before": before["sources"].get(key), "after": after["sources"].get(key)}
        for key in sorted(before["sources"].keys() | after["sources"].keys())
        if before["sources"].get(key) != after["sources"].get(key)
    }
    return {
        "schema": "outlook-surface-diff/v1",
        "version": {"before": before["outlook"]["CFBundleVersion"], "after": after["outlook"]["CFBundleVersion"]},
        "new": {k: right[k] for k in sorted(right.keys() - left.keys())},
        "removed": {k: left[k] for k in sorted(left.keys() - right.keys())},
        "changed": changed,
        "source_changes": source_changes,
        "evidence_changed": evidence_changed,
        "skipped_symlinks": {"before": before["skipped_symlinks"], "after": after["skipped_symlinks"]},
    }


def render_surface(atlas):
    """Render only the versioned atlas; the app bundle is not consulted."""
    if atlas.get("schema") != SCHEMA:
        raise ValueError("unsupported surface atlas schema")
    lines = ["# Outlook surface atlas", "",
             "Generated from [surface-atlas.json](surface-atlas.json) by `python3 ops/outlook-atlas.py render`.", "",
             f"Observed build `{atlas['outlook']['CFBundleVersion']}`. {atlas['scope']}.", "",
             "Confidence `proven` applies to the static declaration or literal, never to runtime acceptance.", "",
             "Binary recovery covers ASCII URL literals in Mach-O files, not routes assembled from separate constants.", "",
             "Query values are omitted. First seen means first observed in this atlas history, not introduction by Microsoft.", ""]
    for kind in sorted({e["kind"] for e in atlas["surfaces"].values()}):
        lines.extend([f"## {kind.replace('_', ' ')}", ""])
        for entry in atlas["surfaces"].values():
            if entry["kind"] != kind:
                continue
            lines.extend(["```json", json.dumps(entry["value"], sort_keys=True, indent=2), "```", "",
                          f"First seen `{entry['first_seen_version']}`. {entry['claim']}.", ""])
            for evidence in entry["evidence"]:
                location = evidence.get("pointer", f"byte {evidence.get('offset')}, length {evidence.get('length')}")
                lines.append(f"- `{evidence['source']}` at `{location}`.")
            lines.append("")
    lines.extend(["## Extraction coverage", "",
                  f"The atlas records hashes and sizes for {len(atlas['sources'])} source files.", "",
                  f"The extractor skipped {len(atlas['skipped_symlinks'])} symlink paths without following them.", "",
                  "The JSON contains the source manifest and skipped paths. Store decoding coverage is outside this slice.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("extract", "drift"):
        sub = commands.add_parser(command)
        sub.add_argument("--app", type=Path, default=Path("/Applications/Microsoft Outlook.app"))
        sub.add_argument("--version", required=True, help="exact expected CFBundleVersion")
        sub.add_argument("--baseline", type=Path, required=command == "drift",
                         help="preserve first-seen versions; drift compares with this atlas")
    sub = commands.add_parser("diff")
    sub.add_argument("baseline", type=Path)
    sub.add_argument("current", type=Path)
    commands.add_parser("render").add_argument("atlas", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "render":
            output = render_surface(json.loads(args.atlas.read_bytes()))
        elif args.command == "diff":
            output = diff_surface(json.loads(args.baseline.read_bytes()), json.loads(args.current.read_bytes()))
        else:
            baseline = json.loads(args.baseline.read_bytes()) if args.baseline else None
            output = extract_surface(args.app, args.version, baseline)
            if args.command == "drift":
                output = diff_surface(baseline, output)
        sys.stdout.write(output if isinstance(output, str) else json.dumps(output, sort_keys=True, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, plistlib.InvalidFileException):
        print('{"error":"atlas_input_invalid_or_changed","hint":"check bundle version, metadata and atlas schema"}', file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
