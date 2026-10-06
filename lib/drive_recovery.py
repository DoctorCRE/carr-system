"""The explicit, noncanonical Drive boundary for legacy local artifacts.

Normal callers must never obtain a Drive path from this module.  A caller that
still needs a legacy projection has to ask for recovery, say why, and label its
output.  This is deliberately a boundary, not a canonical-source adapter.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RECOVERY_VAULT = (
    Path.home() / "Library" / "CloudStorage"
    / "GoogleDrive-joe.bookout.carr.us@gmail.com" / "My Drive" / "CARR AI"
)


class RecoveryArgumentError(ValueError):
    """A malformed or incomplete explicit-recovery request."""


@dataclass(frozen=True)
class RecoveryContext:
    """The parsed boundary shared by every legacy local surface."""

    recovery: bool
    reason: str | None
    vault: Path | None
    args: tuple[str, ...]



@dataclass(frozen=True)
class LegacyVault:
    """A resolved legacy Drive root, plus whatever arguments the caller still owns.

    Separate from RecoveryContext because the guarantee is different and the
    type should say so: RecoveryContext.vault is optional, since normal mode
    legitimately has no Drive root. A caller that reached this type has already
    passed the refusal, so its root is never None and a type checker can see
    that without an assert at every call site.
    """

    vault: Path
    reason: str
    args: tuple[str, ...]


def parse_recovery_controls(argv: list[str] | tuple[str, ...], seam: str) -> RecoveryContext:
    """Strip only recovery controls while preserving all surface arguments.

    Normal mode is canonical-record-only and deliberately erases ambient Drive
    selection. Recovery is an explicit three-part act: ``--recovery``, a
    separate non-option ``--reason`` value, and optionally a separate
    ``--vault`` value. Caller-selected source modes are refused; the boundary,
    not a remembered flag, chooses records versus recovery files.
    """
    raw = list(argv)
    args: list[str] = []
    recovery = False
    reason: str | None = None
    vault_value: str | None = None
    seen: set[str] = set()
    index = 0
    while index < len(raw):
        token = raw[index]
        if token in ("--files", "--records") or token.startswith(("--files=", "--records=")):
            raise RecoveryArgumentError(
                f"{token.split('=', 1)[0]} is not caller-selectable; normal mode is canonical records "
                "and legacy files require explicit --recovery"
            )
        if token.startswith(("--recovery=", "--reason=", "--vault=")):
            raise RecoveryArgumentError(
                f"{token.split('=', 1)[0]} does not accept = form; pass separate arguments"
            )
        if token == "--recovery":
            if token in seen:
                raise RecoveryArgumentError("duplicate --recovery")
            seen.add(token)
            recovery = True
            index += 1
            continue
        if token in ("--reason", "--vault"):
            if token in seen:
                raise RecoveryArgumentError(f"duplicate {token}")
            seen.add(token)
            if index + 1 >= len(raw):
                raise RecoveryArgumentError(f"{token} requires a separate value")
            value = raw[index + 1]
            if not value.strip() or value.startswith("-"):
                raise RecoveryArgumentError(
                    f"{token} requires a nonblank, non-option-looking separate value"
                )
            if token == "--reason":
                reason = value.strip()
            else:
                vault_value = value
            index += 2
            continue
        args.append(token)
        index += 1

    if not recovery and (reason is not None or vault_value is not None):
        present = "--reason" if reason is not None else "--vault"
        raise RecoveryArgumentError(f"{present} is recovery-only; pass --recovery")
    if recovery and reason is None:
        raise RecoveryArgumentError("--recovery requires a separate nonblank --reason")

    if not recovery:
        os.environ.pop("CARR_VAULT", None)
        os.environ["CARR_SOURCE_MODE"] = "records"
        return RecoveryContext(False, None, None, tuple(args))

    vault = Path(vault_value or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT)
    os.environ["CARR_SOURCE_MODE"] = "files"
    print(f"RECOVERY MODE - NONCANONICAL Drive projection - reason: {reason}", file=sys.stderr)
    print(f"RECOVERY MODE - NONCANONICAL Drive root: {vault}", file=sys.stderr)
    return RecoveryContext(True, reason, vault, tuple(args))



# Exit 69 (EX_UNAVAILABLE) is the repo's "canonical seam missing" code:
# bin/nightly.sh's step() maps 69 to BLOCKED rather than FAIL, so a vault-only
# tool that refuses reads as a deliberate refusal in the chain log instead of a
# broken step. Same code bin/routine-canonical-seam-refusal.sh uses.
SEAM_MISSING_EXIT = 69


class LegacyVaultRefused(SystemExit):
    """A vault-only tool asked to run without explicit, reasoned recovery."""

    def __init__(self, seam: str) -> None:
        super().__init__(SEAM_MISSING_EXIT)
        self.seam = seam


def require_legacy_vault(seam: str, argv: list[str] | tuple[str, ...] | None = None) -> LegacyVault:
    """Gate a tool whose ENTIRE job is reading or writing the legacy vault.

    parse_recovery_controls is for a tool that has canonical work to do and a
    legacy fallback. These callers have no canonical mode at all: without a
    Drive root there is nothing for them to look at. So the boundary is the
    same explicit three-part act, but the normal path REFUSES rather than
    continuing in records mode.

    Before this existed each such tool carried its own hardcoded Drive root and
    reached for it with no flag, no reason and no refusal, which is exactly the
    ambient Drive selection the rest of this module removes. Six of them were
    found on 2026-08-22 during Phase 4.

    Returns the parsed context so the caller can hand the REMAINING arguments to
    its own parser; the recovery controls are already stripped out of
    ``context.args``.
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    context = parse_recovery_controls(raw, seam)
    if not context.recovery:
        print(f"MISSING_CANONICAL_SEAM: {seam}", file=sys.stderr)
        print("This tool reads or writes the legacy Drive vault and has no canonical mode. "
              "Normal operation refuses it. To run it as an acknowledged recovery exercise, "
              "pass --recovery --reason WHY [--vault PATH].", file=sys.stderr)
        raise LegacyVaultRefused(seam)
    # parse_recovery_controls always sets both on the recovery branch; assert the
    # invariant here once so no caller has to.
    assert context.vault is not None and context.reason is not None
    return LegacyVault(context.vault, context.reason, context.args)

def add_recovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recovery", action="store_true",
                        help="use noncanonical Drive projections during an acknowledged outage")
    parser.add_argument("--reason", help="nonblank reason for the recovery exercise")
    parser.add_argument("--vault", help="Drive recovery root; requires --recovery")


def require_recovery(args: argparse.Namespace, seam: str) -> Path:
    """Return a Drive root only after explicit, reasoned recovery selection."""
    if args.vault and not args.recovery:
        raise ValueError("--vault is recovery-only; pass --recovery")
    if not args.recovery:
        raise ValueError(
            f"canonical seam missing: {seam}. Normal mode refuses Drive reads and writes."
        )
    reason = (args.reason or "").strip()
    if not reason:
        raise ValueError("--recovery requires a nonblank --reason")
    vault = Path(args.vault or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT)
    print(f"RECOVERY MODE - NONCANONICAL Drive projection - reason: {reason}", file=sys.stderr)
    print(f"RECOVERY MODE - Drive root: {vault}", file=sys.stderr)
    return vault
