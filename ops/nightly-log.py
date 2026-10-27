#!/usr/bin/env python3
"""Keep nightly step output and outcomes in aggregate and unique per-run logs."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile


def append(run_log, output):
    for path in (run_log, run_log.parent.parent / "nightly.log"):
        with path.open("ab") as stream:
            stream.write(output)


def record(run_log, message):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    append(run_log, f"{stamp}  {message}\n".encode())


def main(argv):
    action, location, *args = argv
    if action == "begin":
        directory = Path(location) / "out/nightly-runs"
        directory.mkdir(parents=True, exist_ok=True)
        prefix = "nightly-exports-" if args[0] == "nightly-exports-daytime-retry" else "nightly-"
        prefix += datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        with tempfile.NamedTemporaryFile(prefix=prefix, suffix=".log", dir=directory, delete=False) as stream:
            run_log = Path(stream.name)
        record(run_log, f"nightly chain begin ({args[0]})")
        print(run_log)
        return 0
    run_log = Path(location)
    if action == "finish":
        rc = int(args[0])
        state = "chain_ok" if rc == 0 else "chain_skipped" if rc == 78 else "chain_failed"
        record(run_log, f"nightly result: {state} (exit {rc})")
        return 0
    if action != "step":
        raise ValueError(f"unknown nightly log action {action}")
    label, *command = args
    record(run_log, f"START {label}")
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
            output = b""
            for output in iter(lambda: child.stdout.read1(65536), b""):
                append(run_log, output)
            if output and not output.endswith(b"\n"):
                append(run_log, b"\n")
            rc = child.wait()
            if rc < 0:
                rc = 128 - rc
    except OSError as error:
        record(run_log, f"could not start step ({type(error).__name__}, errno {error.errno})")
        rc = 127
    state = "OK" if rc == 0 else "SKIP" if rc == 78 else "FAIL"
    record(run_log, f"{state} {label} (exit {rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
