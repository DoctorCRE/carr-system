#!/usr/bin/env python3
"""Validate the store atlas and generate its reference or hxprobe key names."""

import argparse
import json
from pathlib import Path
import re
import sys

SCHEMA = "outlook-hxstore-atlas/v1"
KINDS = {"container", "record_anchor", "block_type", "record_type", "envelope_tag", "wire_field_id", "byte_offset", "string_run_selector"}

ENCODING_LENGTHS = {f'u{bits}{order}': bits // 8 for bits in (16, 32, 64) for order in ('le', 'be')}
ENCODING_LENGTHS.update({f'{kind}-{order}': 8 for kind in ('filetime', 'unix-ms', 'unix-s') for order in ('le', 'be')})
ENCODING_LENGTHS.update({f'unix-s-u32{order}': 4 for order in ('le', 'be')})


def validate_hypotheses(hypotheses):
    if not isinstance(hypotheses, list):
        raise ValueError('invalid hypotheses')
    ids = set()
    for h in hypotheses:
        if set(h) != {'field_id', 'record_type', 'offset', 'length', 'encoding', 'confidence', 'evidence', 'corpus_sha256', 'manifest_field'}:
            raise ValueError('invalid hypothesis shape')
        if not re.fullmatch(r'object\.class\.[0-9a-f]{4}', h['record_type']):
            raise ValueError('invalid hypothesis record type')
        if type(h['offset']) is not int or not 10 <= h['offset'] <= 32 << 20:
            raise ValueError('invalid object offset')
        if type(h['length']) is not int or not 0 < h['length'] <= 65536:
            raise ValueError('invalid hypothesis length')
        if h['field_id'] != f"{h['record_type']}.offset.{h['offset']}" or h['field_id'] in ids:
            raise ValueError('invalid or duplicate hypothesis field ID')
        ids.add(h['field_id'])
        if h['encoding'] in ENCODING_LENGTHS:
            if h['length'] != ENCODING_LENGTHS[h['encoding']]:
                raise ValueError('encoding length mismatch')
        elif h['encoding'] not in {'utf-8', 'utf-16le'} or h['encoding'] == 'utf-16le' and h['length'] % 2:
            raise ValueError('invalid hypothesis encoding')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,80}', h['manifest_field']):
            raise ValueError('invalid manifest field')
        if h['confidence'] not in {'candidate', 'confirmed'}:
            raise ValueError('invalid hypothesis confidence')
        if not re.fullmatch(r'[0-9a-f]{64}', h['corpus_sha256']):
            raise ValueError('unbound hypothesis corpus')
        if not isinstance(h['evidence'], list) or not h['evidence'] or any(not re.fullmatch(r'case-\d{2}', case) for case in h['evidence']):
            raise ValueError('missing hypothesis evidence')
    return hypotheses


def validate(atlas):
    if atlas["schema"] != SCHEMA:
        raise ValueError("unsupported store atlas schema")
    validate_hypotheses(atlas.get("decode_hypotheses", []))
    sources = atlas["sources"]
    for source in sources.values():
        if not re.fullmatch(r"[0-9a-f]{40}", source["revision"]) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            raise ValueError("unbound source")
    ids, keys, names = set(), set(), set()
    for entry in atlas["entries"]:
        if not entry["id"] or entry["id"] in ids:
            raise ValueError("duplicate or empty entry ID")
        ids.add(entry["id"])
        kind, value = entry["identifier"]["kind"], entry["identifier"]["value"]
        if kind not in KINDS:
            raise ValueError("unknown identifier kind")
        if kind in {"block_type", "record_type", "envelope_tag", "wire_field_id"} and value is not None:
            limit = 0xffff if kind in {"record_type", "envelope_tag"} else 0xffffffff
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError("invalid wire identifier")
        if kind == "byte_offset" and not (type(value) is int and value >= 0 or isinstance(value, str) and value):
            raise ValueError("invalid byte offset")
        if kind in {"record_anchor", "string_run_selector"} and not (isinstance(value, str) and value):
            raise ValueError("invalid selector")
        if entry["confidence"] not in {"proven", "inferred", "unknown"}:
            raise ValueError("unknown confidence")
        if not entry["name"] or any(ord(c) < 32 for c in entry["name"]):
            raise ValueError("invalid display name")
        if not isinstance(entry["wire_type"], str) or not entry["wire_type"] or entry["decoder"] is not None and not isinstance(entry["decoder"], str):
            raise ValueError("missing wire type or decoder")
        version = entry["first_seen_version"]
        if version is not None and (not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)+", version)):
            raise ValueError("invalid first-seen version")
        if not entry["evidence"]:
            raise ValueError("missing evidence")
        for evidence in entry["evidence"]:
            if evidence["source"] not in sources or not evidence["symbol"]:
                raise ValueError("unbound evidence")
        key = entry.get("decoder_key")
        if key is not None:
            if entry["identifier"]["kind"] != "string_run_selector" or not re.fullmatch(r"[A-Z][A-Za-z0-9]*", key) or key in keys or entry["name"] in names:
                raise ValueError("invalid or duplicate decoder key")
            keys.add(key)
            names.add(entry["name"])
    for entry in atlas["entries"]:
        if entry["parent"] is not None and entry["parent"] not in ids:
            raise ValueError("unknown parent")
    if not keys:
        raise ValueError("no decoder keys")
    return atlas


def rust_names(atlas):
    validate(atlas)
    fields = sorted((e["decoder_key"], e["name"]) for e in atlas["entries"] if "decoder_key" in e)
    lines = ["// Generated from spec/outlook/hxstore-atlas.json. Do not edit.",
             "#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]", "pub enum Key {"]
    lines.extend(f"    {key}," for key, _ in fields)
    lines.extend(["}", "impl Key {", "    pub const ALL: &'static [Key] = &["])
    lines.extend(f"        Key::{key}," for key, _ in fields)
    lines.extend(["    ];", "    pub fn as_str(self) -> &'static str {", "        match self {"])
    lines.extend(f"            Key::{key} => {json.dumps(name, ensure_ascii=False)}," for key, name in fields)
    lines.extend(["        }", "    }", "}", ""])
    lines.extend([
        "pub struct ConfirmedSpan {",
        "    pub field_id: &'static str, pub record_type: &'static str,",
        "    pub offset: usize, pub length: usize, pub encoding: &'static str,",
        "}",
        "pub const CONFIRMED_SPANS: &[ConfirmedSpan] = &[",
    ])
    for h in atlas.get('decode_hypotheses', []):
        if h['confidence'] == 'confirmed':
            lines.append('    ConfirmedSpan { field_id: %s, record_type: %s, offset: %d, length: %d, encoding: %s },' %
                         (json.dumps(h['field_id']), json.dumps(h['record_type']), h['offset'], h['length'], json.dumps(h['encoding'])))
    lines.extend(['];', ''])
    return "\n".join(lines)


def render_store(atlas):
    validate(atlas)
    lines = ["# HxStore atlas", "", "Generated from [hxstore-atlas.json](hxstore-atlas.json).",
             "Regenerate with `python3 ops/hxstore-atlas.py render spec/outlook/hxstore-atlas.json`.", "",
             atlas["scope"], "", atlas["first_seen_policy"], ""]
    for entry in atlas["entries"]:
        lines.extend([f"## {entry['name']} ({entry['id']})", "", "```json",
                      json.dumps({k: entry[k] for k in entry if k not in {"id", "name", "evidence"}}, indent=2), "```", ""])
        for evidence in entry["evidence"]:
            source = evidence["source"]
            revision = atlas["sources"][source]["revision"]
            lines.append(f"- [`{source}` at `{revision}`](https://github.com/jbookout/carr-system/blob/{revision}/{source}), `{evidence['symbol']}`.")
        lines.append("")
    if atlas.get('decode_hypotheses'):
        lines.extend(['## Decode hypotheses', '', '```json', json.dumps(atlas['decode_hypotheses'], indent=2), '```', ''])
    lines.extend(["## Source bindings", "", "```json", json.dumps(atlas["sources"], indent=2), "```", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render", "rust"))
    parser.add_argument("atlas", type=Path)
    args = parser.parse_args()
    try:
        atlas = json.loads(args.atlas.read_bytes())
        result = rust_names(atlas) if args.command == "rust" else render_store(atlas)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("store atlas is invalid", file=sys.stderr)
        return 2
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
