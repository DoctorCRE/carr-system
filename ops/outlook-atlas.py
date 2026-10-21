#!/usr/bin/env python3
"""Static Outlook bundle atlas. All output goes to stdout; inputs are read-only."""

import argparse
import copy
import hashlib
import json
import mmap
import os
from pathlib import Path
import plistlib
import re
import sys
import subprocess
from urllib.parse import urlsplit

from git_env import scrubbed_env


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
RECONSTRUCTED_KINDS = {"url_reconstructed", "url_query_vocabulary"}
RECONCILED_SCOPE = "static declarations, binary URL literals and audited arm64 grammar; runtime behavior untested"


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
    if baseline and "reconciliation" in baseline:
        binding = baseline["reconciliation"]
        _check_binary(result, binding["binary"])
        for source, expected in binding["binary_sources"].items():
            if result["sources"].get(source) != expected:
                raise ValueError("audited evidence source changed")
        result["reconciliation"] = copy.deepcopy(binding)
        result["scope"] = RECONCILED_SCOPE
        for key, entry in baseline["surfaces"].items():
            if entry["kind"] in RECONSTRUCTED_KINDS:
                result["surfaces"][key] = copy.deepcopy(entry)
        result["surfaces"] = dict(sorted(result["surfaces"].items()))
    return result


def _check_binary(atlas, subject):
    if (atlas["outlook"]["CFBundleVersion"] != subject["build"] or
            atlas["sources"].get(subject["binary"]) != {
                "sha256": subject["sha256"], "bytes": subject["bytes"]}):
        raise ValueError("audited binary or build does not match atlas")


def reconcile_surface(atlas, grammar, send, provenance):
    """Extract audited getter vocabulary and dispatch routes for one exact binary."""
    if (atlas.get("schema") != SCHEMA or grammar.get("schema") != "wr443-deeplink-grammar/v1"
            or send.get("schema") != "wr443-outlook-static-send-audit/v1"):
        raise ValueError("unsupported grammar reconciliation schema")
    subject = grammar["subject"]
    _check_binary(atlas, subject)
    source = next((s for s in send["sources"] if "Contents/" + s["path"] == subject["binary"]), None)
    if source is None:
        raise ValueError("missing audited binary")
    if (source["sha256"], source["size_bytes"]) != (subject["sha256"], subject["bytes"]):
        raise ValueError("audits disagree on binary")
    questions = send["questions"]
    account = questions["3_accountID"]
    message = questions["2_immutableID"]
    emails, events = grammar["emails"], grammar["events"]
    if (questions["1_emails_send"]["any_query_keys_consumed"] is not False or
            emails["send"]["result"] != "false, unhandled" or
            urlsplit(emails["existing_message"]["route"]).path !=
            urlsplit(message["applicable_path"]).path):
        raise ValueError("audits disagree on dispatch")
    result = copy.deepcopy(atlas)
    result["scope"] = RECONCILED_SCOPE
    result["surfaces"] = {k: v for k, v in result["surfaces"].items()
                          if v["kind"] not in RECONSTRUCTED_KINDS}
    result["reconciliation"] = {
        "method": "audit-guided reconstruction of raw-value getters and traced dispatch; no general disassembler",
        "binary": {k: subject[k] for k in ("binary", "build", "bytes", "sha256", "architecture")},
        "provenance": copy.deepcopy(provenance),
        "runtime_acceptance": "untested",
        "binary_sources": {},
    }
    grammar_evidence = {e["id"]: e for e in grammar["evidence"]}
    send_evidence = {e["id"]: e for e in send["evidence"]}

    def evidence(ids, send_ids=()):
        rows = []
        for key in sorted(set(ids)):
            e = grammar_evidence[key]
            rows.append({"source": subject["binary"], "offset": int(e["file_offset"], 16),
                         "length": e["byte_length"], "vm_address": e["vm_start"],
                         "sha256": e["sha256"], "audit": "grammar", "evidence_id": key})
        for key in sorted(set(send_ids)):
            e = send_evidence[key]
            rows.append({"source": "Contents/" + e["binary"],
                         "offset": int(e["universal_file_offset"], 16),
                         "length": len(bytes.fromhex(e["bytes_hex"])),
                         "vm_address": e["arm64_vm_address"], "symbol": e.get("symbol"),
                         "sha256": hashlib.sha256(bytes.fromhex(e["bytes_hex"])).hexdigest(),
                         "audit": "send", "evidence_id": key})
        if not rows or any(e["offset"] < 0 or e["length"] <= 0 or
                           e["source"] not in atlas["sources"] or
                           e["offset"] + e["length"] > atlas["sources"][e["source"]]["bytes"]
                           for e in rows):
            raise ValueError("invalid audited evidence range")
        for row in rows:
            name = row["source"]
            source = next((s for s in send["sources"] if "Contents/" + s["path"] == name), None)
            if source is None or atlas["sources"][name] != {
                    "sha256": source["sha256"], "bytes": source["size_bytes"]}:
                raise ValueError("audited evidence source does not match atlas")
            result["reconciliation"]["binary_sources"][name] = copy.deepcopy(atlas["sources"][name])
        return rows

    def add(kind, host, path, keys, rows, dispatch=None):
        if (not isinstance(keys, list) or any(not isinstance(k, str) or
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", k) for k in keys)):
            raise ValueError("invalid reconstructed query key")
        identity = [host, path] if path is not None else host
        key = f"{kind}:bundle:{_json(identity)}"
        value = {"scheme": "ms-outlook", "host": host, "query_keys": sorted(set(keys)),
                 "runtime_acceptance": "untested"}
        if path is not None:
            value.update(path=path, static_dispatch=dispatch)
        result["surfaces"][key] = {
            "kind": kind, "owner": "bundle", "value": value, "confidence": "inferred",
            "claim": "audited arm64 constants and control flow; runtime acceptance untested",
            "first_seen_version": atlas["surfaces"].get(key, {}).get(
                "first_seen_version", subject["build"]), "evidence": rows,
        }

    def route(url, keys, rows, dispatch):
        parts = urlsplit(url)
        if (parts.scheme != "ms-outlook" or parts.netloc not in {"emails", "events"} or
                not re.fullmatch(r"/[A-Za-z/]+", parts.path)):
            raise ValueError("invalid reconstructed route")
        add("url_reconstructed", parts.netloc, parts.path, keys, rows, dispatch)

    account_keys = [account["actual_query_key"], account["fallback_query_key"]]
    route(emails["new"]["route"], emails["new"]["reads"] + account_keys,
          evidence(emails["new"]["evidence"], account["evidence"]), "parsed_forwarded")
    route(emails["send"]["route"], [], evidence(emails["send"]["evidence"]), "unhandled_false")
    route(message["applicable_path"], [message["required_message_query_key"]] + account_keys,
          evidence(emails["existing_message"]["evidence"], message["evidence"] + account["evidence"]),
          "lookup_then_open_ui")
    event_keys = ["title", events["location"]["key"], events["isallday"]["key"],
                  events["attendees"]["key"]] + events["dates"]["keys"]
    route(events["route"], event_keys, evidence(events["evidence"]), "parsed_forwarded")
    for host, group, getter in (("emails", emails, "email_keys"), ("events", events, "event_keys")):
        add("url_query_vocabulary", host, None, group["recognized_query_keys"], evidence([getter]))
    result["surfaces"] = dict(sorted(result["surfaces"].items()))
    return result


def _reconcile_from_git(atlas, bindings):
    if bindings.get("schema") != "outlook-grammar-sources/v1":
        raise ValueError("unsupported grammar source schema")
    paths = {"survey": "audits/wr443-outlook-route-survey.json",
             "grammar": "audits/wr443-deeplink-grammar.json", "send": "audits/wr443-send-by-id.json"}
    audits = {}
    for entry in bindings["sources"]:
        role = entry["role"]
        if (role in audits or entry["path"] != paths.get(role) or
                not re.fullmatch(r"[0-9a-f]{40}", entry["revision"])):
            raise ValueError("invalid audit binding")
        raw = subprocess.run(["git", "show", f"{entry['revision']}:{entry['path']}"],
                             check=True, capture_output=True, env=scrubbed_env()).stdout
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("audit digest mismatch")
        audits[role] = json.loads(raw)
    if set(audits) != set(paths) or audits["survey"].get("schema") != "wr443-outlook-route-survey/v1":
        raise ValueError("incomplete grammar sources")
    return reconcile_surface(atlas, audits["grammar"], audits["send"], bindings)


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
        "reconciliation_changes": _changes(before.get("reconciliation"), after.get("reconciliation")),
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
             "Binary recovery distinguishes complete ASCII URL literals from audit-guided reconstruction of separate arm64 constants.", "",
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
                symbol = f" Symbol `{evidence['symbol']}`." if evidence.get("symbol") else ""
                audit = f" Audit `{evidence['audit']}` evidence `{evidence['evidence_id']}`." if "audit" in evidence else ""
                lines.append(f"- `{evidence['source']}` at `{location}`.{symbol}{audit}")
            lines.append("")
    if "reconciliation" in atlas:
        lines.extend(["## Reconciliation sources", "",
                      "These audit revisions bind the reconstruction. Open branches remain provisional.", ""])
        for source in atlas["reconciliation"]["provenance"]["sources"]:
            lines.append(f"- PR #{source['pr']}, `{source['path']}` at `{source['revision']}`. "
                         f"State `{source['state']}`. Merge `{source['merge_commit']}`.")
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
    sub = commands.add_parser("reconcile")
    sub.add_argument("atlas", type=Path)
    sub.add_argument("bindings", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "render":
            output = render_surface(json.loads(args.atlas.read_bytes()))
        elif args.command == "reconcile":
            output = _reconcile_from_git(json.loads(args.atlas.read_bytes()), json.loads(args.bindings.read_bytes()))
        elif args.command == "diff":
            output = diff_surface(json.loads(args.baseline.read_bytes()), json.loads(args.current.read_bytes()))
        else:
            baseline = json.loads(args.baseline.read_bytes()) if args.baseline else None
            output = extract_surface(args.app, args.version, baseline)
            if args.command == "drift":
                output = diff_surface(baseline, output)
        sys.stdout.write(output if isinstance(output, str) else json.dumps(output, sort_keys=True, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError,
            plistlib.InvalidFileException):
        print('{"error":"atlas_input_invalid_or_changed","hint":"check bundle version, metadata and atlas schema"}', file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
