"""dossier_roster.py — the dossier roster lives in a gitignored LOCAL file.

WHY IT MOVED (WR-000049, Joe's 2026-09-03 public-repo ruling). DOSSIER_FILES
used to be a dict literal in exporters/targets.py. Its keys are the dossier
filenames, and a dossier filename is a client's name: the literal was a live
client roster in a public repository. The roster now lives in

    exporters/dossier-roster.local.json        (gitignored, per machine)

shaped as {"dossiers": {"<file>.md": "flat" | "chronological", ...}}. The
record layer stays the authority for WHICH clients have a dossier (notes_path);
this file only mirrors that set locally, exactly as the literal did.

ONE READER, THREE CALLERS. exporters/targets.py builds DOSSIER_FILES from it,
pipelines/import_dossier_analysis.py reads it through targets.py, and
hooks/record-home-gate.py loads THIS module by path (the hook runs under the
system python with no repo imports, so this file is stdlib-only on purpose).
A second parser for the same file would be the two-homes defect again.

RESOLUTION ORDER, first hit wins:
  1. $CARR_DOSSIER_ROSTER — an explicit path (tests point it at a synthetic file)
  2. exporters/dossier-roster.local.json beside this module
  3. ~/carr-system/exporters/dossier-roster.local.json — the canonical checkout,
     so a worktree (which never carries gitignored files) sees the same roster

ABSENT IS NOT AN ERROR HERE. load_roster() returns {} and roster_status() says
why; each caller decides what absence means for it (the gate logs it and keeps
its other layers, the importer refuses to run).
"""
import json
import os

ROSTER_BASENAME = "dossier-roster.local.json"
ENV_VAR = "CARR_DOSSIER_ROSTER"
MODES = ("flat", "chronological")
CANONICAL = os.path.join(os.path.expanduser("~"), "carr-system", "exporters", ROSTER_BASENAME)


def candidates():
    """Every path roster_path() consults, in order (for an exact refusal message)."""
    override = os.environ.get(ENV_VAR)
    if override:
        return [override]
    return [os.path.join(os.path.dirname(os.path.abspath(__file__)), ROSTER_BASENAME), CANONICAL]


def roster_path():
    """The roster file to read, or None when no candidate exists."""
    override = os.environ.get(ENV_VAR)
    if override:
        return override
    for candidate in candidates():
        if os.path.isfile(candidate):
            return candidate
    return None


def missing_message():
    """Why there is no roster and exactly how to get one. Never carries a name:
    it names only file paths, the variable and the record-layer query."""
    looked = "\n".join(f"    {p}  (missing)" for p in candidates())
    how = (f"${ENV_VAR} is set, so only that path is read" if os.environ.get(ENV_VAR)
           else f"set ${ENV_VAR} to read a roster from somewhere else")
    return (
        "no dossier roster on this machine. Looked for:\n"
        f"{looked}\n"
        f"({how}.)\n"
        "The roster is gitignored per-machine data and never in the repo (WR-000049). "
        "It is a JSON file shaped {\"dossiers\": {\"<file>.md\": \"flat\" | \"chronological\"}} whose "
        "keys are the dossier filenames of the clients that carry notes_path in the record layer "
        "(select count(*) from client where notes_path is not null). Get it by copying "
        f"~/carr-system/exporters/{ROSTER_BASENAME} from Joe's machine, which is the canonical "
        f"copy, into exporters/{ROSTER_BASENAME} here, mode 600, over a private channel, never "
        "through git, chat or email."
    )


def load_roster(path=None):
    """{dossier filename: render mode}; {} when there is no roster file.

    A roster that EXISTS but is malformed raises ValueError: a half-read list of
    guarded files is worse than a loud failure the caller can log.
    """
    path = path or roster_path()
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    roster = doc.get("dossiers") if isinstance(doc, dict) else None
    if not isinstance(roster, dict):
        raise ValueError(f"{path}: expected {{\"dossiers\": {{name: mode}}}}")
    out = {}
    for name, mode in roster.items():
        if not (isinstance(name, str) and name.endswith(".md") and "/" not in name):
            raise ValueError(f"{path}: bad dossier filename {name!r}")
        if mode not in MODES:
            raise ValueError(f"{path}: {name} has mode {mode!r}, expected one of {MODES}")
        out[name] = mode
    return out


def roster_status():
    """One line for logs: where the roster came from and how many entries."""
    path = roster_path()
    if not path or not os.path.isfile(path):
        return "no dossier roster (looked for: " + ", ".join(candidates()) + ")"
    return f"dossier roster {path}"
