#!/usr/bin/env python3
"""
write-effect-check-selftest.py — acceptance test for hooks/write-effect-check.py,
written before the check (rule e65efc68).

WHY IT EXISTS, and it is the lesson of 2026-08-14 rather than a new idea. Every
write control here parses INTENT: record-home-gate reads a tool's file_path,
bash-write-gate extracts targets from a shell command. Intent-parsing cannot be
complete against arbitrary shell, and on the day bash-write-gate shipped it
produced TWO false refusals of legitimate work in one afternoon — an unexpanded
`$D` judged as literal text, and a path merely MENTIONED beside an unrelated
write. Both came from the gate answering a question it could not answer instead
of declining.

This asks a different question, and one that has an answer: not "what is this
command going to write" but "what actually changed". That is complete with
respect to HOW a write happened — heredoc, unknown interpreter, a script invoked
by name, a path built from variables at runtime, any of it. None of those can
hide from the filesystem.

WHAT IT WATCHES, and the scoping is what makes it quiet enough to live with.
Only vault markdown that the record-home policy says NOTHING may write: 564 of
the 620 files, against 44 generated renders and 12 machine-required exemptions.
Renders are DELIBERATELY EXCLUDED — the exporter writes them several times a day,
often from launchd while a session's Bash command is mid-flight, and effects
alone cannot tell the exporter's write from a session's. Watching only files with
no legitimate writer means no allowlist is needed and the hourly refresh cannot
produce a single false report.

WHAT IT IS NOT. It cannot prevent — the write has already happened when the check
runs. It reports, and that is the honest ceiling of an effect-based control at
the hook layer. Prevention at this completeness needs the filesystem or the
record layer, which is a bigger build. Reporting is still worth having: the
failure this closes is the SILENT one, where a write lands somewhere no query
finds and nobody learns it happened.

DIRECTION OF ERROR, chosen deliberately after today: this is a detector, so its
worst failure is noise rather than blocked work. That is the right way round for
a first version, and the opposite of what bash-write-gate got wrong twice.

Tested in two halves plus an end-to-end, and the split is stated rather than
hidden: the SWEEP half runs against a throwaway directory, because a fixture
cannot be placed inside the real vault; the VERDICT half runs against the real
policy and real vault paths WITHOUT touching any file. Testing the sweep against
the real vault would make the suite depend on Drive being mounted, and testing
the verdict against a fixture would repeat exactly the mistake that made
bash-write-gate's first suite measure nothing.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHECK = os.path.join(REPO, "hooks", "write-effect-check.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("carr_write_effect", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    print("write effect check")
    module = load()
    tmp = tempfile.mkdtemp(prefix="write-effect-selftest-")
    try:
        # ── THE SWEEP HALF, against a throwaway tree ────────────────────────
        root = os.path.join(tmp, "vault")
        os.makedirs(os.path.join(root, "DNA"), exist_ok=True)
        os.makedirs(os.path.join(root, ".generations"), exist_ok=True)
        old = os.path.join(root, "DNA", "old.md")
        with open(old, "w") as fh:
            fh.write("old\n")
        time.sleep(0.02)
        cutoff = time.time_ns()
        time.sleep(0.02)

        check("nothing changed after the cutoff yet",
              module.changed_since(root, cutoff) == [], "")

        fresh = os.path.join(root, "DNA", "fresh.md")
        with open(fresh, "w") as fh:
            fh.write("new\n")
        hits = module.changed_since(root, cutoff)
        check("a NEW markdown file after the cutoff is seen", fresh in hits, str(hits))
        check("a file untouched since before the cutoff is not seen",
              old not in hits, str(hits))

        with open(old, "a") as fh:
            fh.write("edited\n")
        hits = module.changed_since(root, cutoff)
        check("an EDIT to an existing file is seen", old in hits, str(hits))

        noise = os.path.join(root, "DNA", "thing.json")
        with open(noise, "w") as fh:
            fh.write("{}\n")
        check("a non-markdown file is ignored",
              noise not in module.changed_since(root, cutoff), "")

        skipped = os.path.join(root, ".generations", "old-copy.md")
        with open(skipped, "w") as fh:
            fh.write("x\n")
        check("a bare .generations directory is skipped",
              skipped not in module.changed_since(root, cutoff), "")

        # THE SUFFIX CASE, and it was worth 237 false reports. The real archive
        # directories are named after the file they shadow —
        # `compiled-rules-shared.md.generations` — so a set membership test on
        # ".generations" skipped none of them. Found by running the sweep against
        # the real vault BEFORE shipping, which is the check the earlier gates in
        # this family did not get.
        suffixed = os.path.join(root, "DNA", "compiled-rules-shared.md.generations")
        os.makedirs(suffixed, exist_ok=True)
        archived = os.path.join(suffixed, "20260814T210006Z-compiled-rules-shared.md")
        with open(archived, "w") as fh:
            fh.write("x\n")
        check("a <file>.md.generations archive directory is skipped too",
              archived not in module.changed_since(root, cutoff),
              str(module.changed_since(root, cutoff)))

        # ── THE VERDICT HALF, real policy and real vault paths, no writes ───
        vault = module.vault_root()
        hand = os.path.join(vault, "DNA", "Network", "deals.md")
        render = os.path.join(vault, "DNA", "compiled-rules-shared.md")
        exempt = os.path.join(vault, "CLAUDE.md")

        check("hand-authored vault markdown counts as a violation",
              module.violations([hand]) == [hand], str(module.violations([hand])))
        check("a GENERATED RENDER does not — the exporter writes those, and "
              "effects cannot tell it from a session",
              module.violations([render]) == [], str(module.violations([render])))
        check("a machine-required exempt file does not",
              module.violations([exempt]) == [], str(module.violations([exempt])))

        # ── END TO END through the hook contract ───────────────────────────
        marker_dir = os.path.join(tmp, "markers")
        env = {**os.environ,
               "CARR_WRITE_EFFECT_ROOT": root,
               "CARR_WRITE_EFFECT_STATE": marker_dir}

        def drive(event, session="s1"):
            payload = json.dumps({"session_id": session, "tool_name": "Bash",
                                  "hook_event_name": event,
                                  "tool_input": {"command": "true"}})
            return subprocess.run([sys.executable, CHECK], input=payload,
                                  capture_output=True, text=True, env=env)

        pre = drive("PreToolUse")
        check("the pre pass exits clean", pre.returncode == 0, pre.stderr[:120])
        check("the pre pass leaves a marker",
              os.path.isdir(marker_dir) and os.listdir(marker_dir), "")

        post = drive("PostToolUse")
        check("the post pass exits clean when nothing violated",
              post.returncode == 0, post.stderr[:160])
        check("and says NOTHING when there is nothing to say — a check that "
              "speaks on every command gets muted, and then it is not a check",
              post.stderr.strip() == "", post.stderr[:160])

        # A fixture file cannot be judged a violation by the real policy, so the
        # end-to-end proves the plumbing and the silence; the verdict half above
        # proves the judgement. Stated because a suite that blurred the two is
        # what made bash-write-gate's first version measure nothing.
        for junk in ("{not json", json.dumps({"session_id": "s2"})):
            p = subprocess.run([sys.executable, CHECK], input=junk,
                               capture_output=True, text=True, env=env)
            check(f"malformed input fails OPEN ({junk[:12]}…)", p.returncode == 0,
                  f"exit {p.returncode}")

        post_no_marker = drive("PostToolUse", session="never-seen")
        check("a post pass with no matching marker is silent, not an error",
              post_no_marker.returncode == 0 and post_no_marker.stderr.strip() == "",
              post_no_marker.stderr[:160])
    finally:
        subprocess.run(["rm", "-rf", tmp])

    return CHECKER.summary()


if __name__ == "__main__":
    raise SystemExit(main())
