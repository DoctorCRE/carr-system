"""Resolve the existing CARR vault roots for write safeguards."""
import os


def vault_roots():
    """Every CARR vault root that exists on THIS machine, trailing slash.

    Added 2026-08-10 in the Dell migration audit. Two gates pinned the vault to
    "/Users/booko/…/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI",
    which is one person's Drive account. On any second machine that path cannot
    exist, so the vault write-protections installed, passed their selftests, and
    matched nothing — a gate that is green and guards zero paths, which is worse
    than an absent one because it reports safety. The Drive mount is named for
    the ACCOUNT, so the only portable answer is to glob for it, the same way
    ops/config-as-code.py resolves {{VAULT}}.

    Callers ADD these to their existing hardcoded spellings rather than
    replacing them, so the primary machine's behaviour is unchanged.
    """
    import glob as _glob
    home = os.path.expanduser("~")
    found = list(_glob.glob(os.path.join(
        home, "Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI")))
    found.append(os.path.join(home, "My Drive/CARR AI"))
    env = os.environ.get("CARR_VAULT")
    if env:
        found.append(env)
    roots = [p.rstrip("/") + "/" for p in sorted(found) if os.path.isdir(p)]
    return tuple(dict.fromkeys(roots))          # de-duped, order preserved
