"""One behavioral preflight for machine-local CARR dependencies."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


PSQL_CANDIDATES = (
    "/opt/homebrew/opt/libpq/bin/psql",
    "/usr/local/opt/libpq/bin/psql",
    os.path.join(os.path.expanduser("~"), ".local", "bin", "psql"),
    os.path.join(os.path.expanduser("~"), ".local", "pgsql", "bin", "psql"),
    "psql",
)

OPENSSL_CANDIDATES = (
    "/opt/homebrew/opt/openssl@3/bin/openssl",
    "/usr/local/opt/openssl@3/bin/openssl",
    os.path.join(os.path.expanduser("~"), ".local", "bin", "openssl"),
    "openssl",
)


@dataclass(frozen=True)
class Requirement:
    key: str
    label: str
    ok: bool
    detail: str
    fix: str


def find_executable(
    candidates: Iterable[str], *, which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Resolve the first real executable; a bare name is never assumed."""
    for candidate in candidates:
        if os.path.sep in candidate:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            continue
        found = which(candidate)
        if found:
            return found
    return None


def openssl_executable(
    *, candidates: Sequence[str] = OPENSSL_CANDIDATES,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """The OpenSSL every Ed25519 signer and verifier must run.

    macOS puts /usr/bin ahead of Homebrew on PATH, and /usr/bin/openssl is
    Apple's LibreSSL, which has no Ed25519 raw signing or verification. A bare
    "openssl" therefore verified nothing on the Mac Studio (2026-09-23) while
    hosted Linux CI, whose openssl is OpenSSL 3, stayed green. Homebrew's
    OpenSSL 3 is preferred by absolute path; PATH is the last resort.
    """
    return find_executable(candidates, which=which) or "openssl"


def probe_postgres_client(
    *, candidates: Sequence[str] = PSQL_CANDIDATES,
    which: Callable[[str], str | None] = shutil.which,
) -> Requirement:
    binary = find_executable(candidates, which=which)
    return Requirement(
        "postgres-client", "PostgreSQL client", binary is not None,
        binary or "psql is not installed in any supported machine-local location",
        "Homebrew: brew install libpq; without Homebrew, install libpq under ~/.local",
    )


def probe_openssl_ed25519(
    *, candidates: Sequence[str] = OPENSSL_CANDIDATES,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., object] = subprocess.run,
    temp_root: Path | None = None,
) -> Requirement:
    binary = find_executable(candidates, which=which)
    fix = "install OpenSSL 3 (Homebrew: brew install openssl@3; no Homebrew: install it under ~/.local)"
    if not binary:
        return Requirement("openssl-ed25519", "OpenSSL Ed25519", False,
                           "no OpenSSL executable is installed", fix)
    with tempfile.TemporaryDirectory(dir=str(temp_root) if temp_root else None) as raw:
        attempt = runner(
            [binary, "genpkey", "-algorithm", "ED25519", "-out", str(Path(raw) / "probe.pem")],
            capture_output=True, text=True,
        )
    if getattr(attempt, "returncode", 1) == 0:
        return Requirement("openssl-ed25519", "OpenSSL Ed25519", True, binary, "")
    version = runner([binary, "version"], capture_output=True, text=True)
    build = (getattr(version, "stdout", "") or "").strip() or "unknown build"
    return Requirement("openssl-ed25519", "OpenSSL Ed25519", False,
                       f"{binary} cannot mint an Ed25519 key ({build})", fix)


def probe_output_root(repo: Path | str) -> Requirement:
    output = Path(repo) / "out"
    if output.is_symlink():
        detail = f"{output} is a symlink; evidence proofs require a real directory"
        ok = False
    elif output.is_dir():
        detail = str(output)
        ok = True
    else:
        detail = f"{output} is missing or is not a directory"
        ok = False
    return Requirement(
        "output-root", "output directory", ok, detail,
        f"create the real directory at {output} in the canonical checkout",
    )


def machine_prerequisites(
    repo: Path | str, *,
    psql_candidates: Sequence[str] = PSQL_CANDIDATES,
    openssl_candidates: Sequence[str] = OPENSSL_CANDIDATES,
    which: Callable[[str], str | None] = shutil.which,
) -> list[Requirement]:
    return [
        probe_postgres_client(candidates=psql_candidates, which=which),
        probe_openssl_ed25519(candidates=openssl_candidates, which=which),
        probe_output_root(repo),
    ]


def prerequisite_failure_report(results: Sequence[Requirement]) -> str:
    missing = [result for result in results if not result.ok]
    if not missing:
        return ""
    lines = [
        f"config-as-code: PREREQUISITES MISSING — {len(missing)} of {len(results)} unavailable: "
        + ", ".join(result.label for result in missing)
    ]
    for result in missing:
        lines.append(f"  {result.label}\n      {result.detail}\n      fix: {result.fix}")
    return "\n".join(lines)


def require_openssl_ed25519_or_exit() -> str:
    result = probe_openssl_ed25519()
    if result.ok:
        return result.detail
    print(f"{result.detail}; this proof needs OpenSSL 3")
    raise SystemExit(78)


def require_real_output_root_or_exit(repo: Path | str) -> None:
    result = probe_output_root(repo)
    if result.ok:
        return
    print(f"{result.detail}; run this proof in the canonical checkout or on CI")
    raise SystemExit(78)
