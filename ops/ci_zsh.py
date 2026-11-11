"""Install zsh using verified offline archives before ordinary apt installation."""
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote


RETRY_EXHAUSTED = 75
ACQUISITION_FAILURE = re.compile(
    r"Temporary failure resolving|Could not resolve|Failed to fetch|Unable to fetch some archives|"
    r"Connection timed out|Connection refused|Could not connect|Cannot initiate the connection|"
    r"https?://[^\n]*\b5[0-9]{2}\b|\b5[0-9]{2}\b[^\n]*https?://", re.IGNORECASE)
PACKAGE_FAILURE = re.compile(
    r"dpkg: error|Sub-process .*dpkg.*error|Unmet dependencies|"
    r"held broken packages|Unable to correct problems|post-installation script|"
    r"pre-installation script", re.IGNORECASE)


def acquisition_failure(result):
    output = result.stdout or ""
    return (result.returncode == 100 and ACQUISITION_FAILURE.search(output) is not None
            and PACKAGE_FAILURE.search(output) is None)


def verify_archives(root, private, cache, phase):
    # Cold runners can take tens of seconds to answer apt-cache; keep a finite
    # bound that a slow index read does not trip.
    signal.alarm(60)
    metadata_unavailable = False
    for archive in root.glob("*.deb"):
        match = re.fullmatch(r"([a-z0-9][a-z0-9+.-]*)_([^_]+)_([a-z0-9][a-z0-9-]*)\.deb", archive.name)
        verified = False
        if match and archive.is_file() and not archive.is_symlink() and not metadata_unavailable:
            package, version, architecture = match.groups()
            try:
                metadata = subprocess.run(["apt-cache", "show", "--no-all-versions", package + ":" + architecture],
                                          capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                if phase == "install":
                    sys.exit("Refusing to cache zsh: archive metadata verification timed out")
                metadata_unavailable = True
                metadata_text = ""
                print("Ignoring cached zsh archives: metadata verification timed out", file=sys.stderr)
            else:
                if metadata.returncode not in (0, 100):
                    metadata.check_returncode()
                metadata_text = metadata.stdout
            content = archive.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            for paragraph in metadata_text.split("\n\n"):
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
                # replacement symlink in the shared cache.
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
                sys.exit("Refusing to cache unverified zsh archive")
    signal.alarm(0)


def verification_timeout(_signum, _frame):
    raise TimeoutError("zsh archive verification exceeded 60 seconds")


def main():
    try:
        if subprocess.run(["zsh", "--version"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return 0
    except FileNotFoundError:
        pass
    signal.signal(signal.SIGALRM, verification_timeout)
    private = Path(tempfile.mkdtemp(prefix="carr-zsh."))
    cache = Path(os.environ.get("ZSH_ARCHIVE_DIR", str(private / "cache")))
    try:
        cache.mkdir(parents=True, exist_ok=True)
        verify_archives(cache, private, cache, "seed")
        options = ["-o", "Dir::Cache::archives=" + str(private),
                   "-o", "APT::Keep-Downloaded-Packages=true",
                   "-o", "Acquire::Retries=0", "-o", "Acquire::http::Timeout=15",
                   "-o", "Acquire::https::Timeout=15", "-o", "DPkg::Lock::Timeout=30"]
        install = ["install", "-y", "-qq", "--no-install-recommends", "zsh"]

        def apt(arguments, seconds):
            completed = subprocess.run(["timeout", "--kill-after=5s", str(seconds) + "s",
                                        "sudo", "apt-get", *options, *arguments],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       env={**os.environ, "LC_ALL": "C"})
            print(completed.stdout or "", end="", file=sys.stderr)
            return completed

        if any(private.glob("*.deb")):
            result = apt([*install, "--no-download"], 120)
            if not acquisition_failure(result):
                return result.returncode
        for attempt, backoff in enumerate((0, 2, 4), start=1):
            if backoff:
                time.sleep(backoff)
            result = apt(install, 180)
            if result.returncode == 0:
                break
            if result.returncode != 124 and not acquisition_failure(result):
                return result.returncode
            if result.returncode == 100 and attempt == 1:
                refreshed = apt(["update", "-qq"], 60)
                if refreshed.returncode != 0:
                    print("zsh package index refresh failed with exit " + str(refreshed.returncode), file=sys.stderr)
                    return refreshed.returncode
            print(f"zsh install attempt {attempt}/3 failed with exit {result.returncode}", file=sys.stderr)
        if result.returncode != 0:
            print(f"zsh installation failed after 3 attempts; last cause exit {result.returncode}",
                  file=sys.stderr)
            return RETRY_EXHAUSTED
        verify_archives(private, private, cache, "install")
        return 0
    finally:
        subprocess.run(["sudo", "rm", "-rf", "--", str(private)], check=True)


if __name__ == "__main__":
    sys.exit(main())
