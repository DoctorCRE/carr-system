"""Compare two schema snapshots while ignoring one operational sequence value."""
from __future__ import annotations

import re
from pathlib import Path


WORK_REQUEST_SEQUENCE_VALUE = re.compile(
    rb"(?m)^select pg_catalog\.setval\('ops\.work_request_ref_seq', ([0-9]+), true\);$"
)
WORK_REQUEST_SEQUENCE_INVOCATION = re.compile(
    rb"^[ \t]*select[ \t]+pg_catalog[ \t]*\.[ \t]*setval[ \t]*\("
    rb"[ \t]*'ops\.work_request_ref_seq'[ \t]*,[^\r\n;]*\)[ \t]*;[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def normalized(path: Path) -> bytes | None:
    content = path.read_bytes()
    if len(WORK_REQUEST_SEQUENCE_INVOCATION.findall(content)) != 1:
        return None
    if len(WORK_REQUEST_SEQUENCE_VALUE.findall(content)) != 1:
        return None
    return WORK_REQUEST_SEQUENCE_VALUE.sub(
        b"select pg_catalog.setval('ops.work_request_ref_seq', <current>, true);",
        content,
    )


def snapshots_match(expected_path: Path, observed_path: Path) -> bool:
    """Return whether snapshots differ only in the admitted sequence value."""
    expected = normalized(expected_path)
    observed = normalized(observed_path)
    return expected is not None and expected == observed
