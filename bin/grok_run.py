"""Implementation of grok-run.sh; stdout contains the substantive Grok answer."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/room-bridge"))
from grok_wire import TIMEOUT_S, PreflightError, invoke_cli, run_request
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from grok_session import sign_in_alert, authentication_result

PREFIX = "Do not call any CARR or record-layer tool; do not write anything unless asked."


def command(argv, timeout):
    # Never echo subprocess diagnostics: they may contain authentication data.
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def version_info(text):
    match = re.search(r"\b(\d+\.\d+\.\d+)(?:-([0-9A-Za-z.-]+))?", text)
    if not match:
        raise PreflightError("grok-run: cannot determine CLI version")
    release = tuple(int(part) for part in match[1].split("."))
    # npm's latest channel ships stable releases; prereleases sort below stable.
    return match[0], (*release, not bool(match[2]))


def installed_version():
    result = command(["grok", "--version"], 20)
    if result.returncode:
        raise PreflightError("grok-run: grok --version failed")
    return version_info(result.stdout)


def preflight():
    version, rank = installed_version()
    try:
        latest = command(["npm", "view", "@xai-official/grok", "version"], 20)
    except (OSError, subprocess.TimeoutExpired):
        latest = None
    if latest is None or latest.returncode:
        print("grok-run: npm registry unreachable; continuing with installed CLI", file=sys.stderr)
    else:
        target, target_rank = version_info(latest.stdout.strip())
        if rank < target_rank:
            upgrade = command(["npm", "install", "-g", f"@xai-official/grok@{target}"], 180)
            if upgrade.returncode:
                raise PreflightError("grok-run: CLI upgrade failed")
            version, rank = installed_version()
            if rank < target_rank:
                raise PreflightError("grok-run: CLI still behind after upgrade")
    models = command(["grok", "models"], 60)
    if authentication_result(models) == "refused":
        raise PreflightError("Grok needs sign-in: run grok login", 3)
    if models.returncode:
        raise PreflightError("grok-run: grok models preflight failed")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Exit 3: sign-in required; 4: incomplete run; 5: wrong model; 6: unusable retrieval. "
        "--retrieve explicitly requires source text JSON and retains a private diagnostic receipt. "
        "GROK_RUN_RECEIPT selects a receipt file instead of stderr. "
        "GROK_RUN_FAKE_NDJSON replays a fixture without calling Grok/npm."))
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="high")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=int, default=int(TIMEOUT_S),
                        help="model invocation timeout in seconds (1-1800; default: 180)")
    parser.add_argument("--writable", action="store_true")
    parser.add_argument("--no-sign-in-alert", action="store_true",
                        help="The calling health job owns failure notification")
    parser.add_argument("--retrieve", action="store_true",
                        help="validate retrieved public source text for the prompt URLs")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    if args.max_turns < 1:
        parser.error("--max-turns must be a positive integer")
    if not 1 <= args.timeout_seconds <= 1800:
        parser.error("--timeout-seconds must be between 1 and 1800")
    try:
        requested_prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt
    except (OSError, ValueError) as error:
        print(f"grok-run: {type(error).__name__}", file=sys.stderr)
        return 4

    def checked_preflight():
        try:
            return preflight()
        except PreflightError as error:
            line = str(error)
            if error.code == 3 and not args.no_sign_in_alert:
                try:
                    sign_in_alert()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    line += " · alert FAILED"
            print(line, file=sys.stderr)
            raise

    configured = os.environ.get("GROK_RUN_RECEIPT")
    outcome = run_request(requested_prompt, retrieval=args.retrieve, prefix=PREFIX,
        effort=args.effort, max_turns=args.max_turns, writable=args.writable,
        timeout_seconds=args.timeout_seconds, invoke=invoke_cli, preflight=checked_preflight,
        fixture=os.environ.get("GROK_RUN_FAKE_NDJSON"), require_identity=False,
        receipt_path=configured)
    receipt = outcome.pop("receipt")
    if not configured or outcome.get("detail") == "grok_receipt_unavailable":
        # The same schema lands on stderr for ordinary and retrieval runs.
        if args.retrieve or receipt["cli_version"] is not None or configured:
            sys.stderr.write(json.dumps(receipt, sort_keys=True) + "\n")
    if args.retrieve:
        sys.stdout.write(json.dumps(outcome, sort_keys=True) + "\n")
    elif output := outcome.get("result"):
        sys.stdout.write(output + ("" if output.endswith("\n") else "\n"))
    return outcome["code"]


if __name__ == "__main__":
    sys.exit(main())
