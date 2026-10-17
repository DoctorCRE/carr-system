#!/usr/bin/env bash
# Download and install from private, verified archive copies.
set -euo pipefail
if zsh --version >/dev/null 2>&1; then
  exit 0
fi
mkdir -p "$ZSH_ARCHIVE_DIR/partial"
umask 077
ZSH_PRIVATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/carr-zsh.XXXXXX")
trap 'sudo rm -rf -- "$ZSH_PRIVATE_DIR"' EXIT
mkdir -p "$ZSH_PRIVATE_DIR/partial"
# APT trusts a cached archive's size. Verify its bytes against the runner's
# authenticated indexes and never let it consume the restored cache directly.
verify_archives() {
python3 - "$1" "$ZSH_PRIVATE_DIR" "$ZSH_ARCHIVE_DIR" "$2" <<'PY'
import hashlib
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import unquote

# Cold runners can take tens of seconds to answer apt-cache; keep a finite
# bound that a slow index read does not trip.
signal.alarm(60)
root = Path(sys.argv[1])
private, cache, phase = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
for archive in root.glob("*.deb"):
    match = re.fullmatch(r"([a-z0-9][a-z0-9+.-]*)_([^_]+)_([a-z0-9][a-z0-9-]*)\.deb", archive.name)
    verified = False
    if match and archive.is_file() and not archive.is_symlink():
        package, version, architecture = match.groups()
        metadata = subprocess.run(["apt-cache", "show", "--no-all-versions", package + ":" + architecture],
                                  capture_output=True, text=True, timeout=30)
        if metadata.returncode not in (0, 100):
            metadata.check_returncode()
        content = archive.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        for paragraph in metadata.stdout.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line)
            if (fields.get("Package") == package and fields.get("Version") == unquote(version)
                    and fields.get("Architecture") == architecture and fields.get("SHA256") == digest):
                verified = True
                break
    if verified:
        # APT may create root-owned downloads. Replace them with our own
        # read-only snapshot rather than trying to chmod the downloaded file.
        with tempfile.NamedTemporaryFile(dir=private, delete=False) as output:
            output.write(content)
            copied = Path(output.name)
        copied.chmod(0o444)
        if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
            sys.exit("Refusing changed private zsh archive: " + archive.name)
        copied.replace(private / archive.name)
        if phase == "install":
            # Publish verified bytes for future cache hits without following a
            # replacement symlink in the shared cache. Install uses private.
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
                output.write(content)
                staged = Path(output.name)
            staged.replace(cache / archive.name)
    if not verified:
        quarantine = root / "quarantine"
        quarantine.mkdir(exist_ok=True)
        archive.rename(quarantine / archive.name)
        print("Ignoring unverified zsh archive: " + archive.name, file=sys.stderr)
        if phase == "install":
            sys.exit("Refusing to install unverified zsh archive")
PY
}
apt_options=(-o "Dir::Cache::archives=$ZSH_PRIVATE_DIR" -o APT::Keep-Downloaded-Packages=true -o Acquire::Retries=2 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o DPkg::Lock::Timeout=30)
fetched=0
for attempt in 1 2 3; do
  if [ "$attempt" -eq 1 ]; then
    verify_archives "$ZSH_ARCHIVE_DIR" seed
  else
    verify_archives "$ZSH_PRIVATE_DIR" retry
  fi
  if timeout --kill-after=5s 60s sudo apt-get "${apt_options[@]}" install -y -qq --no-install-recommends --download-only zsh; then
    fetched=1
    break
  fi
  echo "zsh download attempt ${attempt} failed" >&2
  if [ "${attempt}" -eq 1 ]; then
    timeout --kill-after=5s 60s sudo apt-get "${apt_options[@]}" update -qq \
      || echo "index refresh failed; retrying the download" >&2
  fi
  if [ "${attempt}" -lt 3 ]; then
    sleep "$((attempt * ZSH_RETRY_BACKOFF))"
  fi
done
if [ "${fetched}" -ne 1 ]; then
  echo "zsh could not be downloaded after 3 attempts" >&2
  exit 1
fi
verify_archives "$ZSH_PRIVATE_DIR" install
timeout --kill-after=5s 120s sudo apt-get "${apt_options[@]}" install -y -qq --no-install-recommends --no-download zsh
