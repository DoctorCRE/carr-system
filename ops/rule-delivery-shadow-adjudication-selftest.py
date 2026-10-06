#!/usr/bin/env python3
"""Static contract for the source-only legacy shadow adjudication package."""
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import finding, observation_id  # noqa:E402
ARTIFACT = REPO / "audits/rule-delivery-shadow-adjudication-2026-08-26.v1.json"

# HOST-EVIDENCE AUDIT IS OPT-IN. The raw shadow ledger and the raw transcripts
# this document pins live only on the evidence-owning machine, so reading them
# made the suite give a different verdict per host. The committed digest
# envelope, the pinned prefixes and every assertion below are unchanged; only
# WHEN the raw bytes are read. Ordinary CI is hermetic. `--verify-host-evidence`
# performs the audit, and it still FAILS on historical evidence that has moved.
# OBSERVED, CAUSE NOT ESTABLISHED: several pinned session transcripts on the
# evidence-owning machine no longer match their recorded digests, and the change
# is not confined to appended rows. Nothing here diagnoses why, and no
# explanation should be inferred from this suite passing hermetically.
_ARGUMENTS = sys.argv[1:]
_UNKNOWN = [argument for argument in _ARGUMENTS if argument != "--verify-host-evidence"]
if _UNKNOWN:
    # A mistyped flag must never read as a passing audit.
    raise SystemExit(f"unknown argument(s): {' '.join(_UNKNOWN)}")
VERIFY_HOST_EVIDENCE = "--verify-host-evidence" in _ARGUMENTS
HEX = set("0123456789abcdef")
EXPLAINED = {
    "a76928580de0fddc3231420e6663b3219831b80526d8cabb60b69e4cc85c2028",
    "46117889922249d1726395fdbe7f79e3ec8451e6ade050360b7a2b5262e83bf1",
    "95570890c5594f2196787adbbd0ab5096acc1dace9869f0b04ec7c5819666524",
    "c3f85ecea5509e543433d396eaead5ab4181d7dcfd3210d2c96e1727a76c6655",
    "c86bbe2ed62db23a747926008d29eb9ec12b25307e43aa100590f15cf8e48d96",
    "6717fc4071cf00837c4cb7ff3611d45f4f1de40e9af20d610e7d36150036a1e0",
    "d2364911e26f0efab8ea0b7ea68331fb32a1842cc7b0e936dbee2c311184d895",
    "df7a24c8d2113b609bbe04dd0b49fc96e52cff949503501d24497ca7bd6cd041",
}
REMEDIATED = {
    "ef796a5acc749ddaedf31c2fbe772046283d7a388c30a71299705ec2d5a2caba",
    "84b830dc596a702facf39ddd7fa43896a94493fa6943b8ab63f659fcb0ae5e9a",
    "4d2572bbfbf7942ee47c589a4dcc67542fe320539bc58502043327472bc5a98c",
    "55590cf93cb601888c34ea70e944561b171819dda59466a550205d25c38e8216",
    "04bcc76d7f557e4cf7680db48f1f0f7b6ef2b1f5cb359e4e39b043ca87617207",
    "f0c4df3eb2e3d4e8c9843ef62afc587bccd36043a1bbb1aa7a5af00851fef559",
}
EXPLAINED_CONTEXT = {
    "a76928580de0fddc3231420e6663b3219831b80526d8cabb60b69e4cc85c2028":
        ("Git/PR merge",),
    "46117889922249d1726395fdbe7f79e3ec8451e6ade050360b7a2b5262e83bf1":
        ("Git merge", "release schema ledger", "release key"),
    "95570890c5594f2196787adbbd0ab5096acc1dace9869f0b04ec7c5819666524":
        ("schema/release ledger", "design problem"),
    "c3f85ecea5509e543433d396eaead5ab4181d7dcfd3210d2c96e1727a76c6655":
        ("Git merge", "release ledger", "release key"),
    "c86bbe2ed62db23a747926008d29eb9ec12b25307e43aa100590f15cf8e48d96":
        ("Git merge", "migration/schema ledger", "post-apply"),
    "6717fc4071cf00837c4cb7ff3611d45f4f1de40e9af20d610e7d36150036a1e0":
        ("Git merge", "migration ledger", "release key"),
    "d2364911e26f0efab8ea0b7ea68331fb32a1842cc7b0e936dbee2c311184d895":
        ("Git merge", "schema/migration ledger", "post-apply"),
    "df7a24c8d2113b609bbe04dd0b49fc96e52cff949503501d24497ca7bd6cd041":
        ("Git merge", "release/schema ledger"),
}


def digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def pinned_file_prefix(raw: bytes, expected_sha256: str) -> tuple[bytes, int]:
    """Bind the once-complete transcript while allowing later JSONL appends."""
    assert digest(expected_sha256)
    lines = raw.splitlines(keepends=True)
    running = hashlib.sha256()
    for line_count, line in enumerate(lines, 1):
        running.update(line)
        if running.hexdigest() == expected_sha256:
            suffix = lines[line_count:]
            assert all(line.endswith(b"\n") for line in suffix), \
                "transcript suffix contains an unterminated JSONL fragment"
            for appended_line in suffix:
                try:
                    json.loads(appended_line)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise AssertionError(
                        "transcript suffix contains a malformed JSONL row") from exc
            return b"".join(lines[:line_count]), len(suffix)
    raise AssertionError("transcript is shorter than or changed within its pinned prefix")


def verify_legacy_ledger(raw: bytes, *, prefix_rows: int, prefix_sha256: str,
                         expected: dict[str, tuple[str, str]],
                         require_evidence: bool = False) -> bool:
    """Verify the immutable legacy prefix, while allowing append-only suffixes.

    A fresh clone can legitimately create a new local shadow log during tests.
    If it contains none of the pinned legacy identities it is not the external
    evidence artifact. Once even one pinned identity is present, however, the
    evidence must be complete and the original byte prefix must match exactly.
    """
    lines = raw.splitlines(keepends=True)
    rows = [json.loads(line) for line in lines if line.strip()]
    derived = {observation_id(row): (row.get("session"), row.get("ts"))
               for row in rows if finding(row)}
    pinned_present = set(derived) & set(expected)
    if not pinned_present:
        assert not require_evidence, "canonical legacy evidence has no pinned identities"
        return False
    assert set(expected) <= set(derived), "partial pinned legacy evidence is invalid"
    assert len(lines) >= prefix_rows, "pinned legacy ledger prefix is truncated"
    prefix = b"".join(lines[:prefix_rows])
    assert hashlib.sha256(prefix).hexdigest() == prefix_sha256, \
        "pinned legacy ledger prefix bytes changed"
    prefix_derived = {}
    for line in lines[:prefix_rows]:
        row = json.loads(line)
        if finding(row):
            prefix_derived[observation_id(row)] = (row.get("session"), row.get("ts"))
    assert prefix_derived == expected, "pinned legacy finding identities changed"
    return True


def verify_owned_ledger(root: Path, relative: str, *, prefix_rows: int,
                        prefix_sha256: str,
                        expected: dict[str, tuple[str, str]]) -> bool:
    """The evidence-owning checkout may be absent, never partially present."""
    if not root.exists():
        return False
    path = root / relative
    assert path.is_file(), "canonical legacy ledger is missing or not a regular file"
    assert verify_legacy_ledger(
        path.read_bytes(), prefix_rows=prefix_rows, prefix_sha256=prefix_sha256,
        expected=expected, require_evidence=True)
    return True


def verified_ledger_sources(*, relative: str, prefix_rows: int, prefix_sha256: str,
                            expected: dict[str, tuple[str, str]],
                            clone_ledger: Path | None,
                            owning_root: Path | None) -> list[str]:
    """Name every raw ledger source that ACTUALLY verified against the pins.

    Split out from the call site so the composition can be tested, because the
    composition is where the defect was: an earlier revision on this branch
    recorded that a clone-local ledger had been LOOKED AT rather than whether
    it verified, so an explicit audit could report success with no verifiable
    ledger anywhere. A clone-local log holding none of the pinned identities is
    not the evidence artifact and contributes nothing here.
    """
    sources = []
    if clone_ledger is not None and clone_ledger.is_file() and verify_legacy_ledger(
            clone_ledger.read_bytes(), prefix_rows=prefix_rows,
            prefix_sha256=prefix_sha256, expected=expected):
        sources.append("clone-local")
    if owning_root is not None and verify_owned_ledger(
            owning_root, relative, prefix_rows=prefix_rows,
            prefix_sha256=prefix_sha256, expected=expected):
        sources.append("canonical")
    return sources


document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
assert document["schema"] == "rule-delivery-shadow-adjudication/v1"
assert document["status"] == "independently-reviewed"
assert document["review"] == {
    "reviewers": ["sol-orchestration", "independent-cutover-matrix"],
    "reviewed_at": "2026-08-26",
    "scope": "classification,evidence-bindings,source-remedies",
    "verdict": "clean",
}
assert document["ledger"] == {
    "path": "out/rule-delivery-shadow.jsonl",
    "snapshot": {
        "line_count": 30,
        "sha256": "6a7a06e2b032088013090aa97bc34cb668e3a8a3b4809a19cd6938849213853f",
    },
    "finding_count": 14,
    "mutation": "none",
}
assert "any recurrence invalidates eligibility" in document["disposition_boundary"]
assert "remain shadow" in document["source_remedy"]["validation"]

events = document["events"]
assert len(events) == 14
assert len({event["event_id"] for event in events}) == 14
assert {event["event_id"] for event in events
        if event["proposed_disposition"] == "explained"} == EXPLAINED
assert {event["event_id"] for event in events
        if event["proposed_disposition"] == "remediated"} == REMEDIATED
for event in events:
    assert set(event) == {"event_id", "observed_at", "session_id",
                          "proposed_disposition", "reason", "remedy_scope",
                          "transcript"}
    assert digest(event["event_id"]) and event["reason"]
    transcript = event["transcript"]
    assert transcript["path"].endswith(".jsonl") and digest(transcript["sha256"])
    excerpt = transcript["excerpt"]
    assert set(excerpt) == {"start_line", "end_line", "sha256"}
    assert 0 < excerpt["start_line"] <= excerpt["end_line"] and digest(excerpt["sha256"])
    if event["proposed_disposition"] == "explained":
        assert event["remedy_scope"] == "adjudication"
        assert "polysemy" in event["reason"]
        assert all(context in event["reason"]
                   for context in EXPLAINED_CONTEXT[event["event_id"]])
    else:
        assert "generic" in event["remedy_scope"]
    for scope in event["remedy_scope"].split("+"):
        refs = document["source_remedy"].get(scope)
        assert isinstance(refs, list) and refs
        assert all((REPO / ref).is_file() for ref in refs)

# On the owning machine, independently recompute the pinned append-only ledger,
# derive its finding identities, and rehash every transcript/excerpt. Hosted CI
# does not own these raw local files; that branch explicitly validates the full
# committed digest envelope above and reports which external proof was absent.
expected_events = {event["event_id"]: (event["session_id"], event["observed_at"])
                   for event in events}
ledger_snapshot = document["ledger"]["snapshot"]
prefix_rows = ledger_snapshot["line_count"]
prefix_sha256 = ledger_snapshot["sha256"]
local_ledger = (REPO / document["ledger"]["path"]).resolve()
canonical_root = Path("/Users/booko/carr-system")
canonical_ledger = (canonical_root / document["ledger"]["path"]).resolve()
ledger_sources = verified_ledger_sources(
    relative=document["ledger"]["path"], prefix_rows=prefix_rows,
    prefix_sha256=prefix_sha256, expected=expected_events,
    clone_ledger=(local_ledger if VERIFY_HOST_EVIDENCE
                  and local_ledger != canonical_ledger else None),
    owning_root=canonical_root if VERIFY_HOST_EVIDENCE else None)
for ledger_source in ledger_sources:
    print(f"verified {ledger_source} immutable {prefix_rows}-row ledger prefix "
          f"and {len(expected_events)} derived finding identities")
if not ledger_sources:
    # An explicit audit that verified no raw ledger has proven nothing about the
    # ledger, and must not be reported the same way as a hermetic run that never
    # looked. A clone-local log carrying no pinned identities lands here.
    assert not VERIFY_HOST_EVIDENCE, \
        "host-evidence audit verified no raw shadow ledger"
    print("raw ledger not read or not verifiable (hermetic mode); "
          "validated committed 14-event digest envelope")

# Hermetic boundary cases for the evidence classifier itself.
fixture_rows = [
    {"session": "legacy-a", "ts": "2026-08-25T00:00:00Z", "missed_rules": ["a"]},
    {"session": "legacy-b", "ts": "2026-08-25T00:01:00Z", "missed_rules": ["b"]},
]
fixture_raw = b"".join(json.dumps(row, sort_keys=True).encode() + b"\n"
                       for row in fixture_rows)
fixture_expected = {observation_id(row): (str(row["session"]), str(row["ts"]))
                    for row in fixture_rows}
fixture_digest = hashlib.sha256(fixture_raw).hexdigest()
suffix = json.dumps({"record_type": "epoch", "owner": "test"}).encode() + b"\n"
assert verify_legacy_ledger(fixture_raw + suffix, prefix_rows=2,
                            prefix_sha256=fixture_digest, expected=fixture_expected)
unrelated = json.dumps({"session": "clone", "ts": "2026-08-26T00:00:00Z",
                        "missed_rules": ["new"]}).encode() + b"\n"
assert not verify_legacy_ledger(unrelated, prefix_rows=2,
                                prefix_sha256=fixture_digest, expected=fixture_expected)
for bad in (fixture_raw.splitlines(keepends=True)[0],
            fixture_raw.replace(b"legacy-a", b"legacy-x", 1)):
    try:
        verify_legacy_ledger(bad, prefix_rows=2, prefix_sha256=fixture_digest,
                             expected=fixture_expected)
    except AssertionError:
        pass
    else:
        raise AssertionError("partial or tampered pinned legacy evidence was accepted")
fully_tampered = fixture_raw.replace(b"legacy-a", b"changed-a", 1).replace(
    b"legacy-b", b"changed-b", 1)
try:
    verify_legacy_ledger(fully_tampered, prefix_rows=2, prefix_sha256=fixture_digest,
                         expected=fixture_expected, require_evidence=True)
except AssertionError:
    pass
else:
    raise AssertionError("total canonical pinned-evidence tamper was accepted as unavailable")

# REGRESSION for the ledger ACCOUNTING, not the ledger checks. Independent
# review found that an earlier revision on this branch counted a clone-local
# ledger as checked before knowing whether it verified, so an explicit audit
# holding no verifiable ledger could still report success. The shape that
# exposes it is a fresh machine: a clone-local log with none of the pinned
# identities, and no evidence-owning checkout. The third case is the positive
# control, so an empty result can never pass for the trivial reason that this
# function returns nothing at all.
with tempfile.TemporaryDirectory() as directory:
    accounting = Path(directory)
    clone_only = accounting / "clone-shadow.jsonl"
    clone_only.write_bytes(unrelated)
    assert verified_ledger_sources(
        relative="out/shadow.jsonl", prefix_rows=2, prefix_sha256=fixture_digest,
        expected=fixture_expected, clone_ledger=clone_only,
        owning_root=accounting / "absent-checkout") == [], \
        "an unrelated clone-local ledger with no owning checkout counted as verified"
    assert verified_ledger_sources(
        relative="out/shadow.jsonl", prefix_rows=2, prefix_sha256=fixture_digest,
        expected=fixture_expected, clone_ledger=None, owning_root=None) == [], \
        "absent ledger sources counted as verified"
    owning = accounting / "owning"
    (owning / "out").mkdir(parents=True)
    (owning / "out/shadow.jsonl").write_bytes(fixture_raw)
    assert verified_ledger_sources(
        relative="out/shadow.jsonl", prefix_rows=2, prefix_sha256=fixture_digest,
        expected=fixture_expected, clone_ledger=clone_only,
        owning_root=owning) == ["canonical"], \
        "a verifying owning ledger was not reported, or an unrelated clone was"

with tempfile.TemporaryDirectory() as directory:
    owning_root = Path(directory) / "carr-system"
    owning_root.mkdir()
    for state in ("missing", "directory"):
        ledger = owning_root / "out/shadow.jsonl"
        if state == "directory":
            ledger.mkdir(parents=True)
        try:
            verify_owned_ledger(
                owning_root, "out/shadow.jsonl", prefix_rows=2,
                prefix_sha256=fixture_digest, expected=fixture_expected)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"owning-root {state} ledger evidence was accepted")
        if ledger.is_dir():
            ledger.rmdir()
            ledger.parent.rmdir()

# Transcript files are append-only session logs too. A source session may keep
# running after adjudication pins its then-complete bytes; later rows are lawful,
# but no byte in the pinned prefix may move.
transcript_fixture = b'{"row":1}\n{"row":2}\n'
transcript_digest = hashlib.sha256(transcript_fixture).hexdigest()
prefix, appended = pinned_file_prefix(
    transcript_fixture + b'{"row":3}\n', transcript_digest)
assert prefix == transcript_fixture and appended == 1
for bad_transcript in (
        transcript_fixture.splitlines(keepends=True)[0],
        transcript_fixture.replace(b'"row":1', b'"row":9', 1),
        transcript_fixture + b'{"row":',
        transcript_fixture + b'not-json\n'):
    try:
        pinned_file_prefix(bad_transcript, transcript_digest)
    except AssertionError:
        pass
    else:
        raise AssertionError("short or rewritten transcript prefix was accepted")

# The same excerpt arithmetic the raw-transcript audit performs below, proven
# on synthetic bytes so it keeps its coverage where no transcript exists. An
# excerpt must address a window inside the PINNED prefix only: lawful appended
# rows are never selectable, and a different window must produce a different
# digest, or the assertion would pass for the wrong reason.
excerpt_fixture = b'{"row":1}\n{"row":2}\n{"row":3}\n'
excerpt_pin = hashlib.sha256(excerpt_fixture).hexdigest()
excerpt_prefix, excerpt_appended = pinned_file_prefix(
    excerpt_fixture + b'{"row":4}\n', excerpt_pin)
excerpt_lines = excerpt_prefix.splitlines(keepends=True)
assert excerpt_appended == 1 and len(excerpt_lines) == 3, \
    "appended rows must never enter the pinned excerpt window"
selected_window = b"".join(excerpt_lines[1:3])
assert hashlib.sha256(selected_window).hexdigest() == hashlib.sha256(
    b'{"row":2}\n{"row":3}\n').hexdigest(), "pinned excerpt window did not verify"
assert hashlib.sha256(b"".join(excerpt_lines[0:2])).hexdigest() \
    != hashlib.sha256(selected_window).hexdigest(), \
    "excerpt digests must distinguish one line window from another"

# Raw transcript bytes are host evidence; the loop reads them only under the
# explicit audit. Every committed path/digest/line envelope was validated above
# on every machine.
verified_transcripts = 0
missing_transcripts = []
for event in events if VERIFY_HOST_EVIDENCE else []:
    transcript = event["transcript"]
    path = Path(transcript["path"])
    if not path.is_file():
        missing_transcripts.append(transcript["path"])
        continue
    raw = path.read_bytes()
    pinned_raw, _appended_lines = pinned_file_prefix(raw, transcript["sha256"])
    lines = pinned_raw.splitlines(keepends=True)
    excerpt = transcript["excerpt"]
    assert excerpt["end_line"] <= len(lines), "excerpt falls outside pinned transcript"
    selected = b"".join(lines[excerpt["start_line"] - 1:excerpt["end_line"]])
    assert hashlib.sha256(selected).hexdigest() == excerpt["sha256"]
    verified_transcripts += 1
if not VERIFY_HOST_EVIDENCE:
    print("raw transcripts not read (hermetic mode); validated every committed "
          "file/excerpt digest and line envelope")
elif missing_transcripts:
    assert verified_transcripts == 0, "partial transcript evidence availability is invalid"
    raise AssertionError(
        f"host-evidence audit is missing {len(missing_transcripts)} transcript file(s): "
        + ", ".join(missing_transcripts))
else:
    print(f"verified {verified_transcripts} transcript files/excerpts against raw bytes")

if VERIFY_HOST_EVIDENCE:
    print("rule-delivery-shadow-adjudication-selftest: 14/14 source candidates valid, "
          "INCLUDING the pinned host evidence")
else:
    # Never let a hermetic pass read as "the historical evidence still matches".
    print("rule-delivery-shadow-adjudication-selftest: 14/14 source candidates valid; "
          "pinned host evidence NOT audited "
          "(run with --verify-host-evidence on the evidence-owning machine)")
