#!/usr/bin/env python3
"""fetch-allowlist-selftest.py — the allowlist's SOURCE path, proven offline.

WHY THIS EXISTS NOW (2026-08-19). ops/fetch-allowlist.py had no selftest at all,
and it had just grown a second way to read its data: the `export-email-domains`
verb first, the direct database connection only as a fallback. That change was
made so a SECOND machine needs no database credential — this one query was the
only thing on Dell's Mac that required one — and a change made for a security
reason has to be provable without the thing it removes.

It also cannot be proven the obvious way. The verb is not reachable from CI, and
the fallback needs a production credential CI must never hold, so both live
paths are unavailable exactly where the proof is wanted. Everything here stubs
the boundary instead: the verb path is a fake subprocess result, the database
path is a fake connection. What is being tested is the CHOOSING and the
PARSING, which is where the mistakes actually were — the first version of the
parser reported the failure reason as "}" because it read the last line of a
JSON error payload.

WHAT IS PINNED, and each is a real failure this file would have caught:
  1. The verb is preferred when it answers. If that inverts, a machine with a
     credential silently stops exercising the path the other machine depends on,
     and the verb rots unnoticed until it is the only option.
  2. A verb failure falls back and SAYS SO in the notes. A silent fallback would
     hide the verb being broken for as long as one machine still has a
     credential.
  3. A verb failure on a machine with NO credential raises rather than writing
     an empty list. An empty allowlist is not a smaller allowlist; it strips
     every client domain the guard trusts, and it looks identical to a book
     with no clients.
  4. The policy filter still runs on whichever path supplied the domains.
     Free-mail exclusion is by SUFFIX, so a subdomain of a free-mail host must
     not survive either route.

Run: ./.venv/bin/python ops/fetch-allowlist-selftest.py
"""
import importlib.util
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    """Load fetch-allowlist.py fresh — it is a script path, not a module name."""
    spec = importlib.util.spec_from_file_location(
        "fetch_allowlist_under_test", os.path.join(REPO, "ops", "fetch-allowlist.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def verb_ok(by_source, notes=()):
    """The real shape run.sh emits, CORRECTED 2026-08-19 after the live run.

    The fixture used to put the identity preamble on stdout ahead of the JSON,
    and that is not what happens: run.sh writes clean JSON to STDOUT and its
    preamble to STDERR. Because the fixture agreed with the parser's wrong
    assumption instead of with reality, seven cases passed while the only live
    call failed — the exact shape of a test that cannot fail for the reason
    the code does. The preamble now sits on stderr, where it belongs.
    """
    body = json.dumps({"ok": True, "domains": sorted({d for v in by_source.values() for d in v}),
                       "by_source": by_source,
                       "counts": {k: len(v) for k, v in by_source.items()},
                       "notes": list(notes)}, indent=2)
    return FakeProc(0, body,
                    "local-verb identity -> dell-local (via local-token) -> https://x")


def verb_unknown():
    """A verb the deployed worker does not carry — refusal goes to STDERR."""
    return FakeProc(1, "",
                    "local-verb identity -> dell-local (via local-token) -> https://x\n"
                    'TOOL ERROR {\n  "error": "unknown_tool",\n  "name": "export-email-domains"\n}\n')


def verb_ok_with_trailing_noise(by_source):
    """A well-formed answer followed by anything else on the same channel.

    THE REGRESSION THIS PINS. The first parser demanded that everything from
    the first brace onward be valid JSON, so one extra line after the object
    made the whole read fail — and it reported the reason as "{", which tells
    a reader nothing. raw_decode stops at the end of the first object, so
    trailing output is simply ignored.
    """
    body = json.dumps({"ok": True, "domains": sorted({d for v in by_source.values() for d in v}),
                       "by_source": by_source,
                       "counts": {k: len(v) for k, v in by_source.items()},
                       "notes": []}, indent=2)
    return FakeProc(0, body + "\nsome trailing line the wrapper printed\n", "")


def run_case(mod, proc, db_raw=None, db_exc=None):
    """Drive collect() with both boundaries stubbed."""
    mod.subprocess = type("S", (), {"run": staticmethod(lambda *a, **k: proc),
                                    "DEVNULL": subprocess.DEVNULL})
    def fake_db():
        if db_exc:
            raise db_exc
        return db_raw, ["read through the direct database connection"]
    if db_raw is not None or db_exc is not None:
        mod._raw_via_db = lambda: fake_db()
    return mod.collect()


def main():
    cases, results = [], []

    # 1. The verb answers, and is preferred over a credential that also works.
    mod = load()
    hosts, counts, notes = run_case(
        mod, verb_ok({"v_export_clients": ["example-pelvichealth.com"],
                      "v_export_leads": ["example-familymed.com"]}),
        db_raw={"v_export_clients": ["should-not-be-used.com"]})
    cases.append(("verb path is preferred when it answers",
                  hosts == ["example-familymed.com", "example-pelvichealth.com"]))
    cases.append(("a preferred verb read does not mention the database",
                  not any("direct database" in n for n in notes)))

    # 2. Verb fails, credential present -> fallback, and it is stated.
    mod = load()
    hosts, counts, notes = run_case(
        mod, verb_unknown(), db_raw={"v_export_clients": ["fellbackfine.com"]})
    cases.append(("a failed verb falls back to the database", hosts == ["fellbackfine.com"]))
    cases.append(("the fallback names the verb's actual reason",
                  any("unknown_tool" in n for n in notes)))
    cases.append(("the fallback says it used the database",
                  any("direct database" in n for n in notes)))

    # 3. Verb fails, NO credential -> raises. Never an empty allowlist.
    mod = load()
    raised = False
    try:
        run_case(mod, verb_unknown(),
                 db_exc=RuntimeError("no CARR_DB_EXPORTER_URL in ~/.config/carr/db.env"))
    except RuntimeError:
        raised = True
    cases.append(("no verb and no credential RAISES rather than writing an empty list", raised))

    # 3b. THE LIVE FAILURE, pinned. Preamble on stderr, JSON on stdout.
    mod = load()
    hosts, _, notes = run_case(
        mod, verb_ok({"v_export_clients": ["stdout-only.com"]}),
        db_raw={"v_export_clients": ["must-not-be-used.com"]})
    cases.append(("a preamble on STDERR does not corrupt the STDOUT payload",
                  hosts == ["stdout-only.com"]))

    # 3c. Trailing output after a good object must not fail the read.
    mod = load()
    hosts, _, _ = run_case(
        mod, verb_ok_with_trailing_noise({"v_export_clients": ["trailing-ok.com"]}),
        db_raw={"v_export_clients": ["must-not-be-used.com"]})
    cases.append(("trailing output after the JSON object is ignored",
                  hosts == ["trailing-ok.com"]))

    # 4. The policy filter runs on whatever the source produced.
    mod = load()
    hosts, _, _ = run_case(mod, verb_ok({"v_export_clients": [
        "realpractice.com",            # kept
        "gmail.com",                   # free-mail, excluded
        "harlowdmd.gccoxmail.com",  # free-mail SUBDOMAIN, suffix match
        "some-school.edu",             # institutional TLD
        "not a hostname",              # fails the shape check
    ]}))
    cases.append(("policy filter survives the verb path", hosts == ["realpractice.com"]))

    for label, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        results.append(ok)
    print(f"fetch-allowlist-selftest: {sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
