#!/usr/bin/env python3
"""corpus_renders.py — the deny set for CORPUS RENDERS: files git owns and the
Drive/home copies merely display.

WHY THIS EXISTS. Joe, 2026-08-10, after the nightly chain failed on it:
"create a hook that prevents anything on me or dells side from writing to the
drive files ever again."

THE INCIDENT IT CLOSES. On 2026-08-09 the chair-verdict-bars work was written
into the DRIVE copies of eight council files. Git has been canonical for the
doctrine corpus since 2026-08-06, so those edits landed on renders. Nothing
stopped them and nothing noticed until the nightly corpus push refused to
overwrite them the next morning and exited 2. Nothing was lost that time — the
push refuses rather than clobbering, which is the one thing standing between
this failure and silent data loss. Rely on that twice and the second time is a
`--push` running before anyone reads the conflict.

The cost was not the eight files. It was that the chain went red for a reason
nobody could distinguish from the mypy tripwire that had already been red for
three days, so the chain's exit code stopped carrying information.

WHY record-home-gate DIDN'T ALREADY COVER IT, and this is the interesting part.
That gate's docstring names the `~/.claude` and `My Drive/.claude` trees as
explicitly out of scope: "harness-required; repo-canonical from P6." The word
doing the work is repo-canonical — the reasoning was that because git owns
them, something else governs them. Nothing did. An exclusion justified by
another control's existence is only as good as that control, and here the other
control was an assumption.

WHAT IT DENIES — TWO PROJECTIONS, because the Drive tree has two owners and a
file is worth protecting regardless of which tool writes it:
  * every `source` in corpus/manifest.json — `vault:` doctrine under CARR AI and
    `home:` portable meta-skills under ~/.claude, written by corpus-sync.py
  * every file under claude-tree/{skills,agents}, projected onto
    My Drive/.claude by bin/sync-skills.sh

THE SECOND ONE WAS ADDED 2026-08-10 WITH THE TWO-OWNERS RULING (loop #312), and
the reason is worth stating. Those 24 files were originally in the corpus set, so
this module covered them for free. The ruling moved ownership to claude-tree/ and
took them OUT of the set — which would have silently removed the write-block from
exactly the files whose loss started the investigation. Protection follows the
FILE, never the tool that happens to own it this week.

NEITHER LIST IS RETYPED. One is read from the manifest, the other walked from the
directory. Same discipline as record-home-gate parsing exporters/targets.py: a
duplicated list is the two-homes disease one layer down, and it goes stale on the
first file anyone adds.

WHAT IT DOES NOT TOUCH. Anything not in the manifest. The repo's own
corpus/ copies — those are the canonical originals and editing them is the
CORRECT path this gate steers people toward. Scratchpads, Life AI, non-corpus
files anywhere.

DELL'S SIDE. The gate resolves its roots from `CARR_VAULT` when set and falls
back to a glob over `~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI`, so
it binds on his machine the moment he pulls, with no per-machine edit. His
settings already carry the two hooks this module plugs into.

FAILS OPEN ON ERROR, by deliberate inheritance from the other gates: a gate that
wedges a session costs more than the marginal safety of failing closed on a
single-operator machine. A parse failure logs and allows.
"""

import glob
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "corpus", "manifest.json")


def _vault():
    """CARR AI's absolute path. Env first so Dell needs no edit; then Joe's
    literal path; then a glob, which is what makes this portable to any Drive
    account without the gate knowing whose it is."""
    env = os.environ.get("CARR_VAULT")
    if env and os.path.isdir(env):
        return env
    joe = os.path.join(os.path.expanduser("~"), "Library", "CloudStorage",
                       "GoogleDrive-joe.bookout.carr.us@gmail.com", "My Drive", "CARR AI")
    if os.path.isdir(joe):
        return joe
    for hit in sorted(glob.glob(os.path.expanduser(
            "~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI"))):
        if os.path.isdir(hit):
            return hit
    return joe                       # unreachable Drive: paths just won't match


def roots():
    v = _vault()
    return {"vault": v,
            "drive": os.path.dirname(v),          # "My Drive"
            "home": os.path.expanduser("~")}


def _abs(source, rt):
    """One manifest `source` -> absolute path. `drive:x` and `home:x` are
    root-prefixed; a bare path is vault-relative."""
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return os.path.join(rt[kind], rel)
    return os.path.join(rt["vault"], source)


SKILLS_TREE = os.path.join(REPO, "claude-tree")   # bin/sync-skills.sh's source


def _skills_renders(rt):
    """{realpath: label} for the OTHER projection onto My Drive/.claude.

    Added 2026-08-10 with the two-owners ruling (loop #312). Those 24 files used
    to be in the corpus set, so this module protected them for free. The ruling
    moved ownership to claude-tree/ and took them OUT of the set — which would
    have silently removed the write-block from exactly the files whose loss
    started the whole investigation. Protection follows the file, not the tool
    that happens to own it this week.

    Enumerated from the claude-tree directory rather than listed, same discipline
    as reading the corpus manifest: a list goes stale on the first skill anyone
    adds."""
    out = {}
    dst = os.path.join(rt["drive"], ".claude")
    for sub in ("skills", "agents"):
        src_root = os.path.join(SKILLS_TREE, sub)
        for dirpath, _dirs, files in os.walk(src_root):
            for fn in files:
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), src_root)
                try:
                    real = os.path.realpath(os.path.join(dst, sub, rel))
                except Exception:
                    continue
                out[real] = f"claude-tree:{sub}/{rel}"
    return out


def render_map():
    """{realpath: source-label} for every projected render — the doctrine corpus
    AND the skills/agents tree. Empty on any failure, which is the fail-open
    path."""
    out = {}
    rt = roots()
    try:
        man = json.load(open(MANIFEST))
        for f in man.get("files", []):
            src = f.get("source")
            if not src:
                continue
            try:
                out[os.path.realpath(_abs(src, rt))] = src
            except Exception:
                continue
    except Exception:
        pass
    try:
        out.update(_skills_renders(rt))
    except Exception:
        pass
    return out


def _fix(source):
    """The exact repo path to edit instead, so the denial names the next move
    rather than only the refusal. A refusal without the alternative is how a
    gate gets switched off."""
    if source.startswith("claude-tree:"):
        return "claude-tree/" + source.split(":", 1)[1]
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return f"corpus/_{kind}/{rel}"
    return f"corpus/{source}"


def _publish(source):
    """The command that publishes the repo original — different per projection."""
    if source.startswith("claude-tree:"):
        return "./bin/sync-skills.sh --apply"
    return "python3 tools/corpus-sync.py --push"


def verdict(path, tool="this write"):
    """Deny reason for an absolute path, or None. `path` may be user-relative."""
    try:
        real = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return None
    src = render_map().get(real)
    if not src:
        return None
    owner = ("the skills-and-agents tree" if src.startswith("claude-tree:")
             else "the doctrine corpus")
    return (
        f"'{src}' is a PROJECTED RENDER, not a source file. Git owns it through "
        f"{owner}, and this copy exists only to be read — the next projection either "
        f"overwrites {tool} or refuses and turns the nightly chain red. Both happened "
        f"on 2026-08-10: the corpus push refused eight council files in the morning, "
        f"and the skills projection silently deleted the panel-verdict rule from the "
        f"roster file that afternoon. "
        f"EDIT THIS INSTEAD: ~/carr-system/{_fix(src)} — then run `{_publish(src)}` "
        f"to publish it. That is the only path that reaches BOTH partners; a "
        f"Drive-side edit reaches neither, because Dell's copy is projected from git "
        f"too."
    )


def command_spellings():
    """{literal path string: realpath} for every projected render, under EVERY
    spelling a shell command might use.

    This exists because `/Users/booko/My Drive` is a symlink to the CloudStorage
    mount, so realpath collapses two very different-looking strings into one.
    The Write/Edit door never notices — it resolves before comparing. The Bash
    door matches TEXT, so a command written against the CloudStorage spelling
    would sail past a deny set holding only realpaths. guard-unattended.py
    already carries `_VAULT_SPELLINGS` for exactly this reason; this is the same
    problem one directory up.

    DERIVED FROM render_map(), not from the manifest directly. It used to read
    the manifest alone, and when the skills tree became a second source on
    2026-08-10 the Write door gained those 24 files while this one silently did
    not — caught by the selftest, which is the whole reason that test spawns the
    real hooks instead of importing them."""
    out = {}
    for real in render_map():
        out[real] = real
        # The unresolved spelling: swap the resolved Drive root back for the
        # CloudStorage one, when they differ.
        rt = roots()
        drive_real = os.path.realpath(rt["drive"])
        if drive_real != rt["drive"] and real.startswith(drive_real + os.sep):
            out[rt["drive"] + real[len(drive_real):]] = real
    return out


def paths_in_command(cmd):
    """{the literal string found in the command: its realpath}, for every corpus
    render the command names under either spelling.

    Keyed by the SPELLING rather than the realpath on purpose: the caller has to
    locate the write operator immediately before the path, and it can only do
    that by searching for the text the command actually contains."""
    hits = {}
    if not isinstance(cmd, str) or not cmd:
        return hits
    for spelling, real in command_spellings().items():
        if spelling in cmd:
            hits[spelling] = real
    return hits
