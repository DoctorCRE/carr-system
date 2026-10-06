#!/usr/bin/env python3
"""release-smoke.py — prove the live DoctorCRE system works, read-only.

ops/release-pipeline.py runs this before a release (the baseline) and again
after it (the proof). The pipeline compares the two runs: a journey that worked
before the release and fails after it is the release's fault. That journey
marks the release FAILED and rolls the lane back.

READ-ONLY, BY CONSTRUCTION. Every HTTP request is a GET with redirects off. Every
authenticated journey goes through /mcp as the `smoke-probe` actor (the
CARR_MCP_PROBE_TOKEN bearer in <credential-dir>/mcp-tokens.env). The Worker pins
that actor to the read-only `probe` profile on the server side, and every write
verb answers not_in_profile. This file calls only the read verbs in READ_VERBS.

THE JOURNEYS. A lane runs only the journeys its own release can break
(LANE_JOURNEYS), because the pipeline rolls back the lane that released and a
rollback cannot repair the other one:
  release-identity  both    the lane's identity endpoint (/release for the
                            Worker, /app-release for the app) answers; after a
                            release it serves the released SHA. The other
                            lane's endpoint is read for the evidence only.
  deal-board        worker  deal-board answers
  leads-workspace   worker  lead-board answers
  invoices-list     worker  read-invoice-tracker answers
  dr-cre-chat       worker  list-doc-conversations answers
  verb-registry     worker  list-verbs serves every verb the released Worker
                            SHA carries (--worker-dir); before the release, the
                            missing ones are reported as the verbs it adds
  sign-in-gate      app     each core app page (deal board, Leads, invoices,
                            progress board, Dr. CRE chat) redirects to its own
                            /auth/login?return_to=<page>, and /status renders
  browser-journeys  app     the committed browser-product-proof.v1 producer in
                            the --app-dir checkout, against that revision's
                            synthetic build. A failed run names each failed
                            test (evidence.failed_tests) so the pipeline can
                            attribute per test. Screenshots and traces are
                            copied into the evidence folder.

NOT EXERCISED: no journey renders a signed-in app page (the probe actor is
accepted only on /mcp), the progress board directory needs a partner sponsor
the probe does not have, and Dr. CRE replying needs write verbs. NOT_EXERCISED
lists them, and every summary repeats that list.

EVIDENCE. <out>/summary.json (one row per journey: status, milliseconds,
detail, evidence) and <out>/probes.jsonl. MCP evidence records the SHAPE of
each answer (its keys and list lengths), never the record values in it: a
production deal name never lands in out/.

  ops/release-smoke.py --lane worker --sha <sha> --phase post --out out/release-smoke/<sha>/post \
      [--worker-dir <release worktree>/mcp-server] [--app-dir <doctorcre-app checkout>] [--only id,id]

Exit 0 when every journey that ran passed, 1 otherwise, 2 on a usage error.
Tested offline by ops/release-smoke-selftest.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]

# The identity and redirect reader is the production smoke's own (GET, no
# redirects, HTTP errors returned as replies), not a second copy of it.
_SPEC = importlib.util.spec_from_file_location("doctorcre_production_smoke",
                                               Path(__file__).with_name("doctorcre-production-smoke.py"))
assert _SPEC is not None and _SPEC.loader is not None
_prod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _prod   # its dataclass resolves its own module by name
_SPEC.loader.exec_module(_prod)
Reply: Any = _prod.Reply
read = _prod.read

DEFAULT_API = "https://api.doctorcre.com"
DEFAULT_APP = "https://app.doctorcre.com"
BROWSER = "browser-journeys"
LANE_JOURNEYS = {
    "worker": ("release-identity", "deal-board", "leads-workspace", "invoices-list", "dr-cre-chat",
               "verb-registry"),
    "app": ("release-identity", "sign-in-gate", BROWSER),
}
READ_VERBS = {"deal-board": "deal-board", "leads-workspace": "lead-board",
              "invoices-list": "read-invoice-tracker", "dr-cre-chat": "list-doc-conversations"}
GATED_PAGES = ("/deals", "/leads", "/invoices", "/control-room/progress", "/doc-chats")
NOT_EXERCISED = (
    "signed-in deal board, Leads workspace, invoices list and progress board pages: the app gates "
    "every page on a CARR session cookie, and the only machine identity (smoke-probe) is accepted on "
    "/mcp alone, so the journeys above read the same verbs those pages call instead of rendering them",
    "progress board directory: list-progress-boards answers only for a partner sponsor "
    "(board-answers.js sponsor()), and smoke-probe is a machine actor with no sponsor, so the read "
    "could only ever fail",
    "Dr. CRE chat responding: a reply needs create-doc-conversation/add-doc-conversation-turn, which "
    "write records; the read-only probe profile refuses both, so only the chat's read path is proven",
)
IDENTITY_PATH = {"worker": "/release", "app": "/app-release"}
USER_AGENT = "carr-release-smoke/1 (+ops/release-smoke.py)"
MCP_TIMEOUT = 30


def _shape(value: Any) -> Any:
    """Keys and lengths, never values: evidence without production data."""
    if isinstance(value, dict):
        return {k: (f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__)
                for k, v in sorted(value.items())}
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def _json(reply: Any) -> Any:
    try:
        return json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def served_version(lane: str, live: Any) -> str | None:
    """The provider version a lane's identity payload says is serving: the one
    reader the baseline, the rollback readback and this smoke all use."""
    if not isinstance(live, dict):
        return None
    version = (live.get("worker_version") or {}).get("id") if lane == "worker" else live.get("provider_version_id")
    return version if isinstance(version, str) else None


def _served_sha(lane: str, reply: Any) -> str | None:
    """The SHA a lane's identity endpoint serves, or None when it does not
    answer as that lane's production identity."""
    live = _json(reply)
    if reply.status != 200 or not isinstance(live, dict):
        return None
    if lane == "worker":
        sha = (live.get("git_sha") or {}).get("value") if isinstance(live.get("git_sha"), dict) else None
        valid = live.get("ok") is True and (live.get("env") or {}).get("value") == "production"
    else:
        sha = live.get("source_commit")
        valid = live.get("service") == "doctorcre-app" and live.get("environment") == "production"
    return sha if valid and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _release_identity(ctx: dict) -> tuple[list[str], dict]:
    lane = ctx["lane"]
    other = "app" if lane == "worker" else "worker"
    base = {"worker": ctx["api"], "app": ctx["app"]}
    own = ctx["http"](base[lane] + IDENTITY_PATH[lane])
    sha = _served_sha(lane, own)
    version = served_version(lane, _json(own))
    evidence = {"served_sha": sha,
                "served_version_id": version if version and re.fullmatch(r"[0-9a-f-]{36}", version) else None,
                # Informational: an outage of the other lane is not this release's.
                "other_lane": "answered" if _served_sha(other, ctx["http"](base[other] + IDENTITY_PATH[other]))
                else "unavailable"}
    if sha is None:
        return [f"{lane}_identity_unavailable_or_invalid"], evidence
    if ctx["phase"] == "post" and sha != ctx["sha"]:
        return ["released_source_mismatch"], evidence
    return [], evidence


def _sign_in_gate(ctx: dict) -> tuple[list[str], dict]:
    failures: list[str] = []
    pages: dict[str, dict] = {}
    expected = urllib.parse.urlparse(ctx["app"])
    for page in GATED_PAGES:
        reply = ctx["http"](ctx["app"] + page)
        location = reply.headers.get("Location", "")
        pages[page] = {"status": reply.status}
        target = urllib.parse.urlparse(urllib.parse.urljoin(ctx["app"] + "/", location))
        return_to = urllib.parse.parse_qs(target.query).get("return_to", [None])[0]
        if reply.status not in (301, 302, 303, 307, 308):
            failures.append(f"{page} answered HTTP {reply.status}, expected a sign-in redirect")
        elif (target.scheme, target.hostname, target.path, return_to) != (
                expected.scheme, expected.hostname, "/auth/login", page):
            failures.append(f"{page} redirected outside its expected sign-in route")
    status = ctx["http"](ctx["app"] + "/status")
    pages["/status"] = {"status": status.status, "html": "text/html" in status.headers.get("Content-Type", "")}
    if status.status != 200 or "text/html" not in status.headers.get("Content-Type", ""):
        failures.append(f"/status answered HTTP {status.status}, expected the ungated status page")
    return failures, {"pages": pages}


READ_CONTRACTS: dict[str, dict[str, type]] = {
    "deal-board": {"deals": list},
    "lead-board": {"leads": list, "stages": list, "metrics": dict, "generated_at": str},
    "read-invoice-tracker": {"entries": list, "schema_version": str, "actor": str, "observed_at": str},
    "list-doc-conversations": {"ok": bool, "conversations": list},
}


def _read_verb(verb: str) -> Callable[[dict], tuple[list[str], dict]]:
    def journey(ctx: dict) -> tuple[list[str], dict]:
        ok, answer = ctx["mcp"](verb, {})
        evidence = {"verb": verb}
        if not ok:
            return ["mcp_read_failed"], evidence
        contract = READ_CONTRACTS[verb]
        if (not isinstance(answer, dict) or answer.get("ok") is False
                or any(type(answer.get(key)) is not kind for key, kind in contract.items())):
            return ["read_contract_invalid"], evidence
        if ((verb == "list-doc-conversations" and answer["ok"] is not True)
                or (verb == "read-invoice-tracker" and answer["schema_version"] != "invoice-tracker.v1")):
            return ["read_contract_invalid"], evidence
        evidence["shape"] = _shape({key: answer[key] for key in contract})
        return [], evidence
    return journey


def _verb_registry(ctx: dict) -> tuple[list[str], dict]:
    ok, answer = ctx["mcp"]("list-verbs", {"names_only": True})
    if not ok:
        return ["registry_read_failed"], {}
    if (not isinstance(answer, dict) or answer.get("ok") is not True or not isinstance(answer.get("verbs"), list)
            or any(not isinstance(v, dict) or not isinstance(v.get("name"), str)
                   or not re.fullmatch(r"[a-z][a-z0-9-]*", v["name"]) for v in answer["verbs"])):
        return ["registry_contract_invalid"], {}
    names = {v["name"] for v in answer["verbs"]}
    evidence: dict[str, Any] = {"live_count": len(names)}
    if not names:
        return ["list-verbs listed no verbs"], evidence
    expected = ctx["expected_verbs"]
    if ctx["expected_verbs_error"]:
        return ["released_registry_unavailable"], evidence
    if expected is None:
        evidence["expected"] = "not checked: no released Worker registry was given"
        return [], evidence
    missing = sorted(set(expected) - names)
    evidence["expected_count"] = len(expected)
    if ctx["phase"] == "baseline":
        evidence["new_verbs"] = missing
        return [], evidence
    evidence["missing"] = missing
    return ([f"{len(missing)} released verb(s) not served: {', '.join(missing[:20])}"] if missing else []), evidence


CHECKS: dict[str, Callable[[dict], tuple[list[str], dict]]] = {
    "release-identity": _release_identity,
    "sign-in-gate": _sign_in_gate,
    **{journey: _read_verb(verb) for journey, verb in READ_VERBS.items()},
    "verb-registry": _verb_registry,
}


def run_smoke(*, lane: str, sha: str, phase: str, api: str, app: str,
              http: Callable[..., Any], mcp: Callable[[str, dict], tuple[bool, Any]],
              expected_verbs: list[str] | None, browser: Callable[[], dict] | None,
              only: list[str] | None = None, expected_verbs_error: str | None = None) -> dict:
    ctx = {"lane": lane, "sha": sha, "phase": phase, "api": api.rstrip("/"), "app": app.rstrip("/"),
           "http": http, "mcp": mcp, "expected_verbs": expected_verbs,
           "expected_verbs_error": expected_verbs_error}
    probes: list[dict] = []
    selected = [j for j in LANE_JOURNEYS[lane] if j != BROWSER and (only is None or j in only)]
    for journey in selected:
        started = time.monotonic()
        try:
            failures, evidence = CHECKS[journey](ctx)
        except Exception:  # noqa: BLE001 — classify failure without persisting response values
            failures, evidence = ["journey_exception"], {}
        probes.append({"id": journey, "status": "fail" if failures else "pass",
                       "ms": int((time.monotonic() - started) * 1000),
                       "detail": "; ".join(failures), "evidence": evidence})
    if BROWSER in LANE_JOURNEYS[lane] and (only is None or BROWSER in only):
        started = time.monotonic()
        if browser is None:
            probes.append({"id": BROWSER, "status": "fail", "ms": 0, "evidence": {},
                           "detail": "no installed doctorcre-app checkout was given (--app-dir)"})
        else:
            try:
                outcome = browser()
                tests = outcome.get("tests") if isinstance(outcome, dict) else None
                required = outcome.get("required") if isinstance(outcome, dict) else None
                complete = (isinstance(tests, list) and bool(tests) and isinstance(required, list)
                            and bool(required) and {t.get("id") for t in tests} == set(required or []))
                passed = complete and all(t.get("status") == "passed" for t in tests or [])
                exit_code = outcome.get("exit")
                exit_code = exit_code if type(exit_code) is int and 0 <= exit_code <= 255 else None
                source = outcome.get("source_commit")
                source = source if isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source) else None
                detail = "" if exit_code == 0 and passed else "browser_proof_failed_or_incomplete"
                failed_tests = sorted(t["id"] for t in tests or [] if t.get("status") != "passed") if complete else []
                outcome = {"exit": exit_code, "source_commit": source, "test_count": len(tests or []),
                           "scope": "synthetic application journeys",
                           "artifacts": ["browser/checkpoint.png", "browser/video.webm", "browser/trace.zip"] if complete and passed else [],
                           # Present only when a complete run names what failed: the
                           # pipeline then attributes each test against the baseline.
                           **({"failed_tests": failed_tests} if detail and failed_tests else {})}
            except Exception:  # noqa: BLE001
                outcome, detail = {}, "browser_runner_unavailable_or_failed"
            probes.append({"id": BROWSER, "status": "fail" if detail else "pass",
                           "ms": int((time.monotonic() - started) * 1000), "detail": detail, "evidence": outcome})
    failed_ids = [p["id"] for p in probes if p["status"] == "fail"]
    return {"schema": "carr-release-smoke.v1", "lane": lane, "sha": sha, "phase": phase,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "ok": not failed_ids, "failed": failed_ids, "probes": probes,
            "not_exercised": list(NOT_EXERCISED)}


class McpProbe:
    """The smoke-probe door: JSON-RPC tools/call to <api>/mcp with the probe
    bearer. It refuses any verb outside the read journeys before a request is
    made, so this file cannot write even if the server-side lock were lifted."""

    ALLOWED = frozenset(READ_VERBS.values()) | {"list-verbs"}

    def __init__(self, api: str, token: str, *, opener: Callable[..., Any] = urllib.request.urlopen):
        self.url, self.token, self.opener, self.next_id = api.rstrip("/") + "/mcp", token, opener, 0

    def __call__(self, verb: str, args: dict) -> tuple[bool, Any]:
        if verb not in self.ALLOWED:
            return False, f"{verb} is not a read journey; release-smoke never calls it"
        self.next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
                           "params": {"name": verb, "arguments": args}}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": USER_AGENT})
        try:
            with self.opener(request, timeout=MCP_TIMEOUT) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return False, f"HTTP {error.code} from /mcp"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            return False, f"{type(error).__name__} reading /mcp"
        if (not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0"
                or type(envelope.get("id")) is not int or envelope["id"] != self.next_id or "error" in envelope):
            return False, "mcp_rpc_invalid"
        result = envelope.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            return False, "mcp_tool_failed"
        content = result.get("content")
        if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
            return False, "mcp_content_invalid"
        try:
            answer = json.loads(content[0]["text"])
        except ValueError:
            return False, "mcp_content_invalid"
        if not isinstance(answer, dict):
            return False, "mcp_content_invalid"
        return True, answer


def probe_token(credential_dir: Path) -> str | None:
    """CARR_MCP_PROBE_TOKEN from mcp-tokens.env (NAME=value, optional export
    and quotes). Returned to the caller only; nothing here prints it."""
    try:
        text = (credential_dir / "mcp-tokens.env").read_text(encoding="utf-8")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?CARR_MCP_PROBE_TOKEN\s*=\s*(.*?)\s*$", line)
        if m:
            v = m.group(1)
            value = (v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v) or None
    return value


def released_verbs(worker_dir: Path) -> list[str]:
    """Every verb name the released Worker registry exports, read by importing
    <worker_dir>/src/tools.js exactly as ops/verb-count.sh counts it."""
    script = ("import(process.argv[1]).then(m => console.log(JSON.stringify(Object.keys(m.TOOLS))))"
              ".catch(e => { console.error(e.message); process.exit(1); })")
    url = (worker_dir.resolve() / "src" / "tools.js").as_uri()
    proc = subprocess.run(["node", "--input-type=module", "-e", script, url], cwd=str(worker_dir),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("released_registry_import_failed")
    names = json.loads(proc.stdout)
    if not names:
        raise RuntimeError("the released registry exported no verbs")
    return names


def browser_runner(app_dir: Path, out: Path, *, expected_sha: str | None = None) -> Callable[[], dict]:
    """Run the application's committed browser-product-proof.v1 producer.

    This contract exercises its synthetic fixture build, including native
    journeys and continuity. Live authenticated pages remain unexercised.
    A configured checkout with no producer fails; it never becomes a skip.
    When the producer refuses, this run's own native report (.e2e/report.json,
    deleted before the run) still names each test's status, so a failing test
    is reported by id instead of as a broken proof.
    """
    def run() -> dict:
        contract_paths = ("scripts/browser-product-proof.mjs", "e2e.config.ts",
                          "tests/journeys/required-coverage.json", "node_modules/e2e/dist/cli/bin.js")
        if not all((app_dir / path).is_file() for path in contract_paths):
            raise RuntimeError("application_browser_contract_unavailable")
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app_dir, text=True).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", source) or (expected_sha is not None and source != expected_sha):
            raise RuntimeError("application_browser_source_mismatch")
        coverage = json.loads((app_dir / "tests/journeys/required-coverage.json").read_text())
        required = [row["file"] + "::" + urllib.parse.quote(row["title"], safe="~()*!.'-") for row in coverage["tests"]]
        if not required or len(required) != len(set(required)):
            raise RuntimeError("application_browser_coverage_invalid")
        proof = app_dir / ".e2e/proof"
        packet_path, native_path, run_report = proof / "packet.json", proof / "native-report.json", app_dir / ".e2e/report.json"
        for path in (packet_path, native_path, run_report):
            path.unlink(missing_ok=True)

        def statuses(native: dict) -> list[dict]:
            selected = {row.get("testId"): row for row in native.get("results", []) if row.get("selected")}
            return [{"id": key, "status": "passed" if key in selected and selected[key].get("status") == "passed"
                     and len(selected[key].get("attempts", [])) == 1 else "failed"} for key in required]
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if os.environ.get(k)}
        env.update({"CI": "1", "E2E_TELEMETRY_DISABLED": "1"})
        proc = subprocess.run(["node", "scripts/browser-product-proof.mjs"], cwd=app_dir, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        if proc.returncode:
            try:
                native = json.loads(run_report.read_text()).get("run", {})
            except (OSError, ValueError, AttributeError):
                raise RuntimeError("application_browser_process_failed") from None
            if (native.get("vcs", {}).get("commit") != source or native.get("vcs", {}).get("dirty") is not False
                    or not isinstance(native.get("results"), list)):
                raise RuntimeError("application_browser_process_failed")
            return {"exit": proc.returncode, "source_commit": source, "required": required,
                    "tests": statuses(native), "artifacts": []}
        packet = json.loads(packet_path.read_text())
        report = json.loads(native_path.read_text())
        binding, native = packet.get("binding", {}), report.get("run", {})
        if (packet.get("schema") != "browser-product-proof.v1" or binding.get("sourceCommit") != source
                or binding.get("repo") != "jbookout/doctorcre-app" or not binding.get("runId")
                or native.get("id") != binding["runId"] or native.get("vcs", {}).get("commit") != source
                or native.get("vcs", {}).get("dirty") is not False or native.get("exitCode") != 0
                or native.get("status") != "passed"):
            raise RuntimeError("application_browser_report_invalid")
        tests = statuses(native)
        dest = out / "browser"
        dest.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for name in ("checkpoint.png", "video.webm", "trace.zip"):
            shutil.copyfile(proof / name, dest / name)
            artifacts.append(str(dest / name))
        return {"exit": 0, "source_commit": source, "required": required, "tests": tests, "artifacts": artifacts}
    return run


def main(argv: list[str] | None = None, *, http: Callable[..., Any] = read,
         mcp_factory: Callable[[str, str], Callable[[str, dict], tuple[bool, Any]]] = McpProbe,
         out_line: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", choices=("worker", "app"), required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--phase", choices=("baseline", "post"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--credential-dir", default="~/.config/carr")
    parser.add_argument("--worker-dir", help="the released Worker's mcp-server/ (installed), for verb-registry")
    parser.add_argument("--app-dir", help="an installed doctorcre-app checkout, for the app lane's browser-journeys")
    parser.add_argument("--invocation-id", default=None)
    parser.add_argument("--only", help="comma-separated journey ids (a retry)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        print("release-smoke: --sha must be a full 40-character SHA", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    token = probe_token(Path(os.path.expanduser(args.credential_dir)))
    if token:
        mcp = mcp_factory(args.api, token)
    else:
        def mcp(verb: str, _args: dict) -> tuple[bool, Any]:
            return False, "no CARR_MCP_PROBE_TOKEN in mcp-tokens.env: the authenticated journeys cannot run"
    expected, expected_error = None, None
    if args.worker_dir:
        try:
            expected = released_verbs(Path(args.worker_dir))
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            expected_error = str(error)
    browser = browser_runner(Path(args.app_dir), out, expected_sha=args.sha) if args.app_dir else None
    only = [j.strip() for j in args.only.split(",") if j.strip()] if args.only else None

    summary = run_smoke(lane=args.lane, sha=args.sha, phase=args.phase, api=args.api, app=args.app,
                        http=http, mcp=mcp, expected_verbs=expected, browser=browser, only=only,
                        expected_verbs_error=expected_error)
    summary["invocation_id"] = args.invocation_id or str(uuid.uuid4())
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "probes.jsonl").open("w", encoding="utf-8") as fh:
        for row in summary["probes"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    for row in summary["probes"]:
        out_line(f"release-smoke: {row['status'].upper():4} {row['id']} {row['ms']}ms {row['detail']}".rstrip())
    out_line(f"release-smoke: {'OK' if summary['ok'] else 'FAILED ' + ','.join(summary['failed'])} -> {out / 'summary.json'}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
