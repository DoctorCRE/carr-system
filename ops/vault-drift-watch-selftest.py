#!/usr/bin/env python3
"""vault-drift-watch-selftest.py — prove the drift watch actually detects drift.

WHY THIS SHELLS OUT rather than importing ops/vault-drift-watch.py and calling
its functions directly: the artifact that matters is the SCRIPT AS INVOKED —
stdout report plus exit code — because that is what the nightly chain and
tools/health-check.py actually consume. An in-process import test could pass
while a real invocation's exit code path was broken (argv parsing, sys.exit
plumbing, an exception swallowed somewhere on the way out); the F10 lesson this
build brief cites is exactly that a claimed exit-code contract needs a real
subprocess test, not an assumption from reading the source.

Runs entirely against scratch roots under /private/tmp — never the real vault,
and never the real repo's out/ directory: every run() call passes its own
--baseline-dir / --quarantine-dir / --salvage-manifest / --summary under the
same per-case scratch base as --root / --manifest, so a test case can never
read or write anything the real nightly chain or a developer's own
--rebaseline touches.

v2 additions over the original 10-case suite: --rebaseline, the registry
tamper check against a rebaselined snapshot, the heal cycle (tamper ->
rebaseline -> clean), the salvage-then-heal quarantine/diff/manifest.jsonl
side effects, and the ingest-default-off / --dry-run-ingest contract — CARR_
DRIFT_INGEST unset (or 0) must never attempt a network call; this suite never
sets it to 1 without also passing --dry-run-ingest, so no test can ever
actually POST.

Exit 0 if every case passes, 1 otherwise.

    ops/vault-drift-watch-selftest.py
    ops/vault-drift-watch-selftest.py -v
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "ops", "vault-drift-watch.py")

VERBOSE = "-v" in sys.argv[1:]

# ONE SCRATCH TREE PER PROCESS. This was a hardcoded absolute path — including
# a long-dead session id — shared by every concurrent run, and fresh_dirs()
# rmtree's its per-case subdir on entry while main() rmtree's the whole base on
# exit. Two runs at once therefore deleted each other's fixtures mid-test, and
# because the interleaving differs every time, so did the set of cases that
# failed. That is exactly the signature this suite was QUARANTINED for on
# 2026-08-14 ("4 of 5 runs pass and the failing case DIFFERS between runs"),
# diagnosed then as same-second mtime resolution. It is not mtime: three
# concurrent runs reproduce it every time, and a single run passes 6 for 6.
# mkdtemp gives each process its own base, so nothing is shared and nothing
# races. TMPDIR keeps it in the sandbox's scratch area rather than /tmp.
SCRATCH_BASE = tempfile.mkdtemp(prefix="vault-drift-watch-selftest-")

# PIN THE CUTOFF FLAG OFF FOR THE WHOLE SUITE. Six cases build a fixture vault
# whose registry file is DNA/compiled-rules-shared.md, and the production
# doctrine.md_renders_retired flag went true on 2026-08-19 — which drops every
# .md registry path out of load_expected_writers() and made those six fail
# against a live config value they never meant to consult. A test whose result
# depends on which day production fired a switch is not a test. The retired
# side of the switch gets its own case below, which sets the variable itself.
os.environ.setdefault("CARR_MD_RENDERS_RETIRED", "0")

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def fresh_dirs(tag):
    """One isolated scratch tree per test case: a vault root, a structural
    check-manifest, a rebaseline dir, a quarantine dir, a salvage manifest, and
    a health-check summary — all distinct from the real repo's out/ and from
    every other test case's tree.
    """
    base = os.path.join(SCRATCH_BASE, tag)
    if os.path.exists(base):
        shutil.rmtree(base)
    root = os.path.join(base, "vault")
    os.makedirs(root)
    return {
        "root": root,
        "manifest": os.path.join(base, "manifest.json"),
        "baseline": os.path.join(base, "baseline"),
        "quarantine": os.path.join(base, "quarantine"),
        "salvage": os.path.join(base, "salvage.jsonl"),
        "summary": os.path.join(base, "summary.json"),
    }


# Run the tool under the SAME interpreter bin/nightly.sh uses, not whichever
# python launched this test. --rebaseline imports exporters/targets.py for the
# registry, which needs psycopg — present in the repo venv, absent from a bare
# system python3. Before this, `python3 ops/vault-drift-watch-selftest.py`
# silently exercised the degraded path (registry unavailable) and still passed,
# which is precisely how the 2026-08-13T18:21Z 3-path baseline got written and
# went unnoticed. A test that does not run the real invocation is not a test of
# it. Falls back to sys.executable so the file still runs without a venv.
_VENV = os.path.join(REPO, ".venv", "bin", "python")
VENV_PYTHON = _VENV if os.path.exists(_VENV) else sys.executable


def run(dirs, mode="--check", extra_args=None, env_overrides=None):
    args = [VENV_PYTHON, SCRIPT, mode,
            "--root", dirs["root"], "--manifest", dirs["manifest"],
            "--baseline-dir", dirs["baseline"], "--quarantine-dir", dirs["quarantine"],
            "--salvage-manifest", dirs["salvage"], "--summary", dirs["summary"]]
    if extra_args:
        args += extra_args
    env = dict(os.environ)
    # Isolation default: no test accidentally inherits a real CARR_DRIFT_INGEST=1
    # from the calling shell. Cases that want it ON set it explicitly via
    # env_overrides, always paired with --dry-run-ingest.
    env.pop("CARR_DRIFT_INGEST", None)
    if env_overrides:
        env.update(env_overrides)
    p = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)
    return p.returncode, p.stdout, p.stderr


def write(root, relpath, content):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


# ---------------------------------------------------------------- v1 cases
# (structural .md drift, now run via --check explicitly rather than the
# implicit legacy default — same behavior, addressed by name.)

@case("seed run: exit 0, everything reported ADDED, manifest written")
def _(assert_):
    d = fresh_dirs("seed")
    write(d["root"], "hello.md", "# hello\n")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules\n")
    rc, out, err = run(d)
    assert_(rc == 0, f"seed run should exit 0, got {rc}\nstderr={err}")
    assert_("SEED RUN" in out, "seed run should say SEED RUN")
    assert_("ADDED  " in out, "seed run should list files as ADDED")
    assert_(os.path.exists(d["manifest"]), "seed run should write the check manifest")
    data = json.loads(open(d["manifest"]).read())
    assert_(data["seed"] is True, "manifest should record seed:true")
    assert_(data["file_count"] == 2, f"expected 2 files, got {data['file_count']}")


@case("second run, no changes: exit 0, all three lists empty")
def _(assert_):
    d = fresh_dirs("nochange")
    write(d["root"], "hello.md", "# hello\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 0, f"unchanged second run should exit 0, got {rc2}\nstderr={err2}")
    assert_("ADDED: none" in out2, "no files should be ADDED")
    assert_("MODIFIED: none" in out2, "no files should be MODIFIED")
    assert_("DELETED: none" in out2, "no files should be DELETED")


@case("ADD detection: a brand-new non-exporter .md is UNEXPECTED, exit 2")
def _(assert_):
    d = fresh_dirs("add")
    write(d["root"], "hello.md", "# hello\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    write(d["root"], "surprise.md", "# a new file nobody logged\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"a new non-exporter file should exit 2, got {rc2}\nstderr={err2}")
    assert_("ADDED: 1" in out2, "should report exactly 1 ADDED")
    assert_("UNEXPECTED" in out2 and "surprise.md" in out2,
            "surprise.md should be classified UNEXPECTED")
    assert_("UNEXPECTED VAULT MARKDOWN CHANGE" in out2,
            "the warning block should print")


@case("MODIFY detection: editing an existing non-exporter .md is UNEXPECTED, exit 2")
def _(assert_):
    d = fresh_dirs("modify")
    write(d["root"], "notes.md", "version one\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    write(d["root"], "notes.md", "version two — hand-edited\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"a modified non-exporter file should exit 2, got {rc2}\nstderr={err2}")
    assert_("MODIFIED: 1" in out2, "should report exactly 1 MODIFIED")
    assert_("UNEXPECTED" in out2 and "notes.md" in out2,
            "notes.md should be classified UNEXPECTED")


@case("DELETE detection: removing an existing non-exporter .md is UNEXPECTED, exit 2")
def _(assert_):
    d = fresh_dirs("delete")
    write(d["root"], "gone-soon.md", "will be deleted\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    os.remove(os.path.join(d["root"], "gone-soon.md"))
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"a deleted non-exporter file should exit 2, got {rc2}\nstderr={err2}")
    assert_("DELETED: 1" in out2, "should report exactly 1 DELETED")
    assert_("UNEXPECTED" in out2 and "gone-soon.md" in out2,
            "gone-soon.md should be classified UNEXPECTED")


@case("expected-writer path change does NOT trip exit 2")
def _(assert_):
    d = fresh_dirs("expected")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v2 — refreshed by the exporter\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 0,
            f"an exporter-owned path changing should exit 0, got {rc2}\nstderr={err2}")
    assert_("expected-writer: exporter" in out2 and "compiled-rules-shared.md" in out2,
            "compiled-rules-shared.md should be classified expected-writer: exporter")
    assert_("UNEXPECTED VAULT MARKDOWN CHANGE" not in out2,
            "no warning block should print when nothing is unexpected")


@case("after the cutoff a retired .md render is untracked, and the vault is still watched")
def _(assert_):
    # THE CUTOFF. The registry renders were MOVED to _to_delete staging on
    # purpose. Before this guard the tamper pass reported every staged file as
    # TAMPER on the next check — a deliberate act read as an attack, and dozens
    # of lines of noise standing in front of the one real finding this file
    # exists to surface.
    #
    # THE CONTROL CHANGED ON 2026-08-20. It used to be 00_Context/today.md, on
    # the grounds that bin/local-briefs.sh wrote it rather than the exporter. The
    # cutoff reached that file too once the claim-card verb shipped and served
    # the one section no verb could read, so today.md is no longer written and
    # would be a control that proves nothing. The control is now an ordinary
    # hand-edited file: retiring renders must not make the watch blind to the
    # rest of the vault.
    #
    # Runs the REAL sequence, in order: renders live and baselined (flag off),
    # the cutoff fires and stages them, one rebaseline under the new flag, then
    # every check after that. The first check after a move legitimately reports
    # the deletion — that is the detector working — so what is asserted here is
    # the state the cutoff actually leaves behind once rebaselined.
    d = fresh_dirs("cutoff")
    retired = {"CARR_MD_RENDERS_RETIRED": "1"}
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    write(d["root"], "someones-notes.md", "draft\n")
    rc1, _, _ = run(d, mode="--rebaseline")
    assert_(rc1 == 0, "pre-cutoff rebaseline should exit 0")

    # the cutoff: the render moves to _to_delete staging and the flag goes true
    os.remove(os.path.join(d["root"], "DNA/compiled-rules-shared.md"))
    rcb, outb, _ = run(d, mode="--rebaseline", env_overrides=retired)
    assert_(rcb == 0, f"post-cutoff rebaseline should exit 0, got {rcb}")
    assert_("compiled-rules-shared.md" not in outb,
            "a retired .md render should leave the tracked set entirely")

    # Seed the structural manifest first: the very first --check on a fresh
    # manifest is a SEED RUN that reports everything ADDED and exits 0, so an
    # edit made before it would be invisible. This cost a wrong result once.
    rcs, _, _ = run(d, env_overrides=retired)
    assert_(rcs == 0, f"the seeding check should exit 0, got {rcs}")

    write(d["root"], "someones-notes.md", "draft, hand-edited without logging it\n")
    rc2, out2, err2 = run(d, env_overrides=retired)
    assert_("compiled-rules-shared.md" not in out2,
            f"a retired render must never be reported again\nstderr={err2}")
    assert_(rc2 == 2 and "UNEXPECTED" in out2 and "someones-notes.md" in out2,
            f"the watch must still catch an unlogged vault edit after the cutoff, got rc={rc2}")


@case("mixed run: one expected + one unexpected change -> exit 2, both reported")
def _(assert_):
    d = fresh_dirs("mixed")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    write(d["root"], "someones-notes.md", "draft\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v2\n")
    write(d["root"], "someones-notes.md", "draft, hand-edited without logging it\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"mixed run with one unexpected change should exit 2, got {rc2}")
    assert_("MODIFIED: 2" in out2, "both files should show as MODIFIED")
    assert_("expected-writer: exporter" in out2 and "compiled-rules-shared.md" in out2,
            "the exporter path should still classify correctly")
    assert_("UNEXPECTED" in out2 and "someones-notes.md" in out2,
            "the hand-edited path should classify UNEXPECTED")


@case(".generations snapshot directories and skip-listed dirs are never walked")
def _(assert_):
    d = fresh_dirs("skiplist")
    write(d["root"], "tracked.md", "kept\n")
    write(d["root"], "tracked.md.generations/2026-08-01T00-00-00Z.md", "snapshot copy\n")
    write(d["root"], "Source Material/raw-transcript.md", "should never be scanned\n")
    write(d["root"], "_to_delete/old.md", "pending deletion\n")
    write(d["root"], ".claude/skills/foo.md", "dotdir, skipped\n")
    rc1, out1, err1 = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    data = json.loads(open(d["manifest"]).read())
    assert_(data["file_count"] == 1,
            f"only tracked.md should be walked, got {data['file_count']}: "
            f"{sorted(data['files'].keys())}")
    assert_("tracked.md" in data["files"], "tracked.md should be in the manifest")


@case("manifest is a manifest, not a report: sha256 + mtime per tracked file")
def _(assert_):
    d = fresh_dirs("manifestshape")
    write(d["root"], "a.md", "content\n")
    run(d)
    data = json.loads(open(d["manifest"]).read())
    entry = data["files"]["a.md"]
    assert_("sha256" in entry and len(entry["sha256"]) == 64,
            "each file entry should carry a sha256 digest")
    assert_("mtime" in entry, "each file entry should carry an mtime")
    assert_(data["unexpected_count"] == 0, "seed run's unexpected_count should be 0")


@case("nonexistent root fails loud (exit 1), not silently empty")
def _(assert_):
    d = fresh_dirs("badroot")
    bogus = os.path.join(d["root"], "does-not-exist")
    d["root"] = bogus
    rc, out, err = run(d)
    assert_(rc == 1, f"a missing root should exit 1, got {rc}")
    assert_("FATAL" in err, "a missing root should say FATAL on stderr")


# ---------------------------------------------------------------- v2 cases

@case("rebaseline: writes a baseline manifest with sha256 + content copy for a .md registry file")
def _(assert_):
    d = fresh_dirs("rebaseline_basic")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    rc, out, err = run(d, mode="--rebaseline")
    assert_(rc == 0, f"rebaseline should exit 0, got {rc}\nstderr={err}")
    assert_("baseline written" in out, "rebaseline should confirm it wrote the baseline")
    manifest_path = os.path.join(d["baseline"], "manifest.json")
    assert_(os.path.exists(manifest_path), "rebaseline should write baseline/manifest.json")
    data = json.loads(open(manifest_path).read())
    entry = data["files"]["DNA/compiled-rules-shared.md"]
    assert_(entry["sha256"] is not None and len(entry["sha256"]) == 64,
            "the registry .md file should carry a sha256 in the baseline")
    assert_(entry["content_copied"] is True, ".md registry files should always be content-copied")
    content_copy = os.path.join(d["baseline"], "content", "DNA", "compiled-rules-shared.md")
    assert_(os.path.exists(content_copy), "a content copy should exist under baseline/content/")
    assert_(open(content_copy).read() == "# rules v1\n", "the content copy should be byte-identical")


@case("TAMPER: a registry file changed between --rebaseline and --check "
      "-> exit 2, quarantine + diff + salvage manifest line all exist")
def _(assert_):
    d = fresh_dirs("tamper")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    rc_seed, _, _ = run(d)
    assert_(rc_seed == 0, "structural seed check should exit 0")
    rc_base, _, _ = run(d, mode="--rebaseline")
    assert_(rc_base == 0, "rebaseline should exit 0")

    # Tamper it: rewrite the generated file OUTSIDE the export the baseline
    # was taken after — this is exactly the failure mode v1 could never catch,
    # because v1 only ever compared one post-export manifest to the next.
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1 — hand-edited outside export\n")

    rc, out, err = run(d)
    assert_(rc == 2, f"a tampered registry file should exit 2, got {rc}\nstderr={err}")
    assert_("TAMPER" in out and "DNA/compiled-rules-shared.md" in out,
            "the tampered path should be reported as TAMPER")
    assert_("hash changed since last rebaseline" in out,
            "the tamper reason should explain the hash mismatch")

    # quarantine: the current file + a real diff should exist under a
    # <run_id> subdirectory of the quarantine root
    run_dirs = [p for p in os.listdir(d["quarantine"])
                if os.path.isdir(os.path.join(d["quarantine"], p))]
    assert_(len(run_dirs) == 1, f"expected exactly one quarantine run dir, got {run_dirs}")
    qfile = os.path.join(d["quarantine"], run_dirs[0], "DNA", "compiled-rules-shared.md")
    qdiff = qfile + ".diff"
    assert_(os.path.exists(qfile), "the tampered file should be quarantined")
    assert_(open(qfile).read() == "# rules v1 — hand-edited outside export\n",
            "the quarantined copy should hold the current (tampered) content")
    assert_(os.path.exists(qdiff), "a .diff file should exist beside the quarantined file")
    diff_text = open(qdiff).read()
    assert_("-# rules v1\n" in diff_text and "+# rules v1 — hand-edited outside export\n" in diff_text,
            f"the diff should show the real before/after content, got:\n{diff_text}")

    # salvage manifest: one well-formed JSONL line for this finding
    assert_(os.path.exists(d["salvage"]), "the salvage manifest should exist")
    lines = [l for l in open(d["salvage"]).read().splitlines() if l.strip()]
    assert_(len(lines) == 1, f"expected exactly one salvage manifest line, got {len(lines)}")
    row = json.loads(lines[0])
    assert_(row["kind"] == "vault-drift-salvage", "salvage row kind should be vault-drift-salvage")
    assert_(row["path"] == "DNA/compiled-rules-shared.md", "salvage row should name the tampered path")
    assert_(row["finding_type"] == "tamper", "salvage row should be typed tamper")
    assert_(row["baseline_sha256"] and row["current_sha256"]
            and row["baseline_sha256"] != row["current_sha256"],
            "salvage row should carry two different sha256 values")

    # health-check summary
    summary = json.loads(open(d["summary"]).read())
    assert_(summary["tamper_count"] == 1, f"summary tamper_count should be 1, got {summary}")
    assert_(summary["unexpected_count"] == 0, "no unexpected findings in this case")


@case("HEAL: re-running --rebaseline after a tamper clears the next --check")
def _(assert_):
    d = fresh_dirs("heal")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    run(d)  # structural seed
    run(d, mode="--rebaseline")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1 — hand-edited\n")
    rc_tampered, out_tampered, _ = run(d)
    assert_(rc_tampered == 2, "the tampered state should still exit 2 before healing")

    # Healing: whatever wrote the file becomes the new accepted state at the
    # next rebaseline (this is what the nightly export does every night).
    rc_heal, _, err_heal = run(d, mode="--rebaseline")
    assert_(rc_heal == 0, f"re-rebaselining should exit 0, got {rc_heal}\nstderr={err_heal}")

    rc_clean, out_clean, err_clean = run(d)
    assert_(rc_clean == 0, f"the next check after re-rebaselining should be clean, got "
                           f"{rc_clean}\nstdout={out_clean}\nstderr={err_clean}")
    assert_("no tamper detected" in out_clean, "the clean check should say no tamper detected")
    summary = json.loads(open(d["summary"]).read())
    assert_(summary["tamper_count"] == 0 and summary["total_findings"] == 0,
            f"post-heal summary should show zero findings, got {summary}")


@case("ingest OFF by default: CARR_DRIFT_INGEST unset -> no POST attempted, "
      "manifest + quarantine still happen")
def _(assert_):
    d = fresh_dirs("ingest_default_off")
    rc_seed, _, _ = run(d)  # empty-vault seed, so the next run is a real comparison
    assert_(rc_seed == 0, "seed run should exit 0")
    write(d["root"], "surprise.md", "nobody logged this\n")
    rc, out, err = run(d)  # run() already strips CARR_DRIFT_INGEST from the child env
    assert_(rc == 2, f"the unexpected file should still exit 2, got {rc}\nstderr={err}")
    assert_("ingest: disabled by default" in out,
            "the default-off message should print")
    assert_("would POST" not in out and "[DRY-RUN]" not in out and "ingest POST" not in out,
            f"no POST attempt of any kind should be logged when the flag is unset, got:\n{out}")
    # salvage still happened even with ingest off — capability and enablement
    # are separate, per the design.
    assert_(os.path.exists(d["salvage"]), "the salvage manifest should still be written")
    lines = [l for l in open(d["salvage"]).read().splitlines() if l.strip()]
    assert_(len(lines) == 1, "the finding should still produce one salvage manifest line")


@case("ingest --dry-run-ingest: CARR_DRIFT_INGEST=1 + --dry-run-ingest prints the would-be "
      "POST and STILL never opens a socket")
def _(assert_):
    d = fresh_dirs("ingest_dry_run")
    rc_seed, _, _ = run(d)  # empty-vault seed, so the next run is a real comparison
    assert_(rc_seed == 0, "seed run should exit 0")
    write(d["root"], "surprise.md", "nobody logged this either\n")
    rc, out, err = run(d, extra_args=["--dry-run-ingest"],
                        env_overrides={"CARR_DRIFT_INGEST": "1"})
    assert_(rc == 2, f"the unexpected file should still exit 2, got {rc}\nstderr={err}")
    assert_("[DRY-RUN] would POST" in out, "dry-run should print the would-be POST line")
    assert_("surprise.md" in out, "the dry-run line should name the file")
    assert_("external_id=" in out, "the dry-run line should show the external_id it would send")
    # the token itself must never appear — only the fact that it would be read
    assert_("never printed" in out, "the dry-run line should say the token is never printed")
    assert_("no network call made" in out, "dry-run mode should say plainly that nothing was sent")


@case("salvage payload shape: every required key is present with the right types")
def _(assert_):
    d = fresh_dirs("salvage_shape")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1\n")
    run(d)
    run(d, mode="--rebaseline")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules v1 — tampered\n")
    write(d["root"], "someones-notes.md", "a hand-edit nobody logged\n")
    rc, out, err = run(d)
    assert_(rc == 2, f"expected exit 2, got {rc}\nstderr={err}")
    lines = [json.loads(l) for l in open(d["salvage"]).read().splitlines() if l.strip()]
    assert_(len(lines) == 2, f"expected 2 salvage rows (1 tamper + 1 unexpected), got {len(lines)}")
    required_keys = {"kind", "generated_at", "run_id", "path", "finding_type", "reason",
                      "baseline_sha256", "current_sha256", "quarantine_dir", "quarantine_file",
                      "diff_excerpt", "diff_truncated"}
    types_by_path = {row["path"]: row["finding_type"] for row in lines}
    assert_(types_by_path.get("DNA/compiled-rules-shared.md") == "tamper",
            "the registry file should be typed tamper")
    assert_(types_by_path.get("someones-notes.md") == "unexpected",
            "the hand-edited non-registry file should be typed unexpected")
    for row in lines:
        missing = required_keys - set(row.keys())
        assert_(not missing, f"salvage row for {row.get('path')} is missing keys: {missing}")
        assert_(row["kind"] == "vault-drift-salvage", "kind must be vault-drift-salvage")
        assert_(isinstance(row["diff_truncated"], bool), "diff_truncated must be a bool")
        assert_(isinstance(row["diff_excerpt"], str) and row["diff_excerpt"],
                "diff_excerpt must be a non-empty string")
        assert_(row["quarantine_file"] == row["path"],
                "quarantine_file should match path when the file still exists on disk")


@case("--check and --rebaseline together are rejected (exit 1), never silently one-or-the-other")
def _(assert_):
    d = fresh_dirs("mutex")
    write(d["root"], "hello.md", "# hello\n")
    rc, out, err = run(d, extra_args=["--rebaseline"])  # run() already passes --check
    assert_(rc == 1, f"passing both mode flags should exit 1, got {rc}")
    assert_("mutually exclusive" in err, "the error should say the flags are mutually exclusive")


# ---------------------------------------------------------------- v3 cases
# (Graph-System/ classification + partial --rebaseline --only. Both added
# 2026-08-13 after run 20260813T201608Z reported 3 TAMPER + 99 UNEXPECTED and
# every one of the 102 turned out to be a known writer or a deliberate
# retirement.)

@case("Graph-System/ is a known derived writer, not UNEXPECTED")
def _(assert_):
    d = fresh_dirs("derived")
    write(d["root"], "Graph-System/\U0001f4c1 DNA.md", "# derived\n")
    write(d["root"], "hand-authored.md", "# mine\n")
    rc, out, err = run(d)
    assert_(rc == 0, f"seed run should exit 0, got {rc}\nstderr={err}")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 0, f"second run should exit 0, got {rc2}\nstderr={err2}")
    graph_line = [ln for ln in out.splitlines() if "Graph-System/" in ln]
    assert_(graph_line, "the Graph-System file should appear in the report")
    assert_(all("UNEXPECTED" not in ln for ln in graph_line),
            f"Graph-System/ must not classify as UNEXPECTED: {graph_line}")
    assert_(any("derived" in ln for ln in graph_line),
            f"Graph-System/ should classify as a derived writer: {graph_line}")
    hand = [ln for ln in out.splitlines() if "hand-authored.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in hand),
            f"an ordinary hand-authored .md must still be UNEXPECTED: {hand}")


@case("Graph/ is a derived writer too, so a new lead note is not drift")
def _(assert_):
    # WHY THIS CASE EXISTS. On 2026-08-14 the nightly check failed exit 2 on
    # 0 TAMPER + 4 UNEXPECTED, and two of the four were lead notes under Graph/
    # for two leads added that day. pipelines/build-graph-notes.py WIPES AND
    # REBUILDS Graph/ on every run — run.sh's own comment beside the graph
    # target says exactly that — which is the same relationship
    # build-system-graph.py has to Graph-System/. Only the latter was
    # registered, so ADDING A LEAD, ordinary business activity, failed the
    # nightly chain, and would have failed it again for every future lead.
    #
    # The fixture name is deliberately not a real person: a selftest that
    # carries live lead names puts client-identifying data in the repo for no
    # test benefit.
    d = fresh_dirs("derived-graph")
    write(d["root"], "Graph/leads/Example Lead (lead).md", "# lead\n")
    write(d["root"], "hand-authored.md", "# mine\n")
    rc, out, err = run(d)
    assert_(rc == 0, f"seed run should exit 0, got {rc}\nstderr={err}")
    lead = [ln for ln in out.splitlines() if "Graph/leads/" in ln]
    assert_(lead, "the Graph/ lead note should appear in the report")
    assert_(all("UNEXPECTED" not in ln for ln in lead),
            f"Graph/ must not classify as UNEXPECTED: {lead}")
    assert_(any("derived" in ln for ln in lead),
            f"Graph/ should classify as a derived writer: {lead}")
    # The blanket stays narrow: Graph/ is regenerated wholesale, the vault is not.
    hand2 = [ln for ln in out.splitlines() if "hand-authored.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in hand2),
            f"a hand-authored .md outside Graph/ must still be UNEXPECTED: {hand2}")


# ---------------------------------------------------------------- v4 cases
# (portability-mirror hash verification. 2026-08-14: the mirror's own 224
# regenerated .md files were landing as UNEXPECTED every night — the tamper
# fix above had already stopped calling that a tamper, but nothing yet told
# the structural pass a sanctioned regeneration from a foreign write parked
# in the same Backups/ path. Rather than a blanket DERIVED_DIRS exclusion —
# which would blind this watch to exactly that foreign write — the mirror
# step (pipelines/doctrine_mirror.py) now writes its own manifest.json
# {relpath: sha256} at the end of every run, and these cases prove the four
# ways a file under the mirror can be classified against it.)

def mirror_manifest_json(files):
    return json.dumps({"schema": 1, "generated_at": "2026-08-14T00:00:00Z",
                        "file_count": len(files), "files": files})


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@case("mirror: sha256 matches the mirror's own manifest.json -> mirror-verified, not UNEXPECTED")
def _(assert_):
    d = fresh_dirs("mirror_verified")
    v1 = "# v1\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v1)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v1)}))
    rc1, _, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0, got {rc1}\nstderr={err1}")

    # A sanctioned regeneration: new bytes (the banner's timestamp moved, as
    # it does every night) AND a manifest that was regenerated in lockstep,
    # exactly what pipelines/doctrine_mirror.py's build_mirror() guarantees.
    v2 = "# v2 — regenerated tonight\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v2)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v2)}))
    rc2, out2, err2 = run(d)
    assert_(rc2 == 0, f"a hash-verified mirror regeneration should exit 0, got {rc2}\nstderr={err2}")
    mirror_lines = [ln for ln in out2.splitlines() if "md/policy/a.md" in ln]
    assert_(mirror_lines, "the mirror file should appear in the report")
    assert_(all("UNEXPECTED" not in ln for ln in mirror_lines),
            f"a hash-verified mirror file must not be UNEXPECTED: {mirror_lines}")
    assert_(any("mirror-verified" in ln for ln in mirror_lines),
            f"the mirror file should classify as mirror-verified: {mirror_lines}")
    assert_("1 mirror-verified" in out2,
            f"the summary line should count exactly 1 mirror-verified file, got:\n{out2}")
    summary = json.loads(open(d["summary"]).read())
    assert_(summary["mirror_verified_count"] == 1 and summary["unexpected_count"] == 0,
            f"summary should show 1 mirror-verified, 0 unexpected, got {summary}")


@case("mirror: sha256 diverges from the mirror's own manifest.json -> still UNEXPECTED (real tamper signal)")
def _(assert_):
    d = fresh_dirs("mirror_divergent")
    v1 = "# v1\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v1)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v1)}))
    rc1, _, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0, got {rc1}\nstderr={err1}")

    # Something outside the pipeline rewrote the file; the manifest was NOT
    # regenerated to match (unlike the sanctioned case above).
    write(d["root"], "Backups/portability-mirror/md/policy/a.md",
          "# hand-edited outside the pipeline\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"a mirror file whose hash diverges from its own manifest should exit 2, "
                       f"got {rc2}\nstderr={err2}")
    mirror_lines = [ln for ln in out2.splitlines() if "md/policy/a.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in mirror_lines),
            f"a divergent mirror file must still be UNEXPECTED: {mirror_lines}")
    salvage_rows = [json.loads(l) for l in open(d["salvage"]).read().splitlines() if l.strip()]
    row = next((r for r in salvage_rows if r["path"].endswith("md/policy/a.md")), None)
    assert_(row is not None, "the divergent mirror file should produce a salvage row")
    assert_("does not match the mirror's own manifest.json" in row["reason"],
            f"the reason should name the sha256 mismatch, got: {row['reason']}")


@case("mirror: extra file with no manifest.json entry -> still UNEXPECTED (foreign writer parked it)")
def _(assert_):
    d = fresh_dirs("mirror_extra")
    v1 = "# v1\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v1)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v1)}))
    rc1, _, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0, got {rc1}\nstderr={err1}")

    # A new file appears under the mirror, but manifest.json (unchanged) never
    # claimed it — exactly what a foreign writer parking content there looks
    # like.
    write(d["root"], "Backups/portability-mirror/md/policy/surprise.md", "# not mine\n")
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"an unmanifested mirror file should exit 2, got {rc2}\nstderr={err2}")
    mirror_lines = [ln for ln in out2.splitlines() if "surprise.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in mirror_lines),
            f"an unmanifested mirror file must be UNEXPECTED: {mirror_lines}")
    salvage_rows = [json.loads(l) for l in open(d["salvage"]).read().splitlines() if l.strip()]
    row = next((r for r in salvage_rows if r["path"].endswith("surprise.md")), None)
    assert_(row is not None, "the unmanifested file should produce a salvage row")
    assert_("no entry in its own manifest.json" in row["reason"],
            f"the reason should say there is no manifest entry, got: {row['reason']}")


@case("mirror: manifest.json entry whose file is missing -> still UNEXPECTED")
def _(assert_):
    d = fresh_dirs("mirror_missing")
    v1 = "# v1\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v1)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v1)}))
    rc1, _, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0, got {rc1}\nstderr={err1}")

    # The file is deleted but manifest.json is left exactly as it was — the
    # manifest still promises this path exists.
    os.remove(os.path.join(d["root"], "Backups/portability-mirror/md/policy/a.md"))
    rc2, out2, err2 = run(d)
    assert_(rc2 == 2, f"a manifest-promised but missing mirror file should exit 2, got {rc2}\n"
                       f"stderr={err2}")
    mirror_lines = [ln for ln in out2.splitlines() if "md/policy/a.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in mirror_lines),
            f"a manifest-promised missing file must be UNEXPECTED: {mirror_lines}")
    salvage_rows = [json.loads(l) for l in open(d["salvage"]).read().splitlines() if l.strip()]
    row = next((r for r in salvage_rows if r["path"].endswith("md/policy/a.md")), None)
    assert_(row is not None, "the missing-but-manifested file should produce a salvage row")
    assert_("lists this file but it is missing on disk" in row["reason"],
            f"the reason should say the manifest lists a file that is gone, got: {row['reason']}")


@case("mirror: a file AND its manifest entry both retired together -> not flagged (structural removal, not tamper)")
def _(assert_):
    d = fresh_dirs("mirror_retired")
    v1 = "# v1\n"
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", v1)
    write(d["root"], "Backups/portability-mirror/manifest.json",
          mirror_manifest_json({"md/policy/a.md": sha(v1)}))
    rc1, _, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0, got {rc1}\nstderr={err1}")

    # A legitimate next regeneration: the underlying doctrine document was
    # retired, so this run's manifest.json simply no longer mentions the
    # path, and neither does the tree.
    os.remove(os.path.join(d["root"], "Backups/portability-mirror/md/policy/a.md"))
    write(d["root"], "Backups/portability-mirror/manifest.json", mirror_manifest_json({}))
    rc2, out2, err2 = run(d)
    assert_(rc2 == 0, f"a retirement reflected consistently in both the tree and the manifest "
                       f"should exit 0, got {rc2}\nstderr={err2}")
    mirror_lines = [ln for ln in out2.splitlines() if "md/policy/a.md" in ln]
    assert_(all("UNEXPECTED" not in ln for ln in mirror_lines),
            f"a consistent retirement must not be UNEXPECTED: {mirror_lines}")
    assert_(any("mirror-regenerated" in ln for ln in mirror_lines),
            f"a consistent retirement should classify as mirror-regenerated: {mirror_lines}")


@case("mirror: no manifest.json at all -> fails visibly (UNEXPECTED), never silently trusted")
def _(assert_):
    d = fresh_dirs("mirror_no_manifest")
    write(d["root"], "Backups/portability-mirror/md/policy/a.md", "# v1\n")
    rc1, out1, err1 = run(d)
    assert_(rc1 == 0, f"seed run should exit 0 (seed never trips exit 2), got {rc1}\nstderr={err1}")
    assert_("no Backups/portability-mirror/manifest.json on disk" in out1,
            f"a missing mirror manifest should say so plainly in the report, got:\n{out1}")
    mirror_lines = [ln for ln in out1.splitlines() if "md/policy/a.md" in ln]
    assert_(any("UNEXPECTED" in ln for ln in mirror_lines),
            f"with no manifest to verify against, the mirror file must read UNEXPECTED rather "
            f"than being silently trusted: {mirror_lines}")


@case("mirror: DERIVED_DIRS wholesale trust must never cover the mirror prefix")
def _(assert_):
    # Guards the actual design decision: unlike Graph-System/, the mirror is
    # NOT in DERIVED_DIRS. A blanket exclusion here would blind the watch to
    # exactly the foreign write the earlier mirror cases prove get caught.
    d = fresh_dirs("mirror_not_derived")
    write(d["root"], "Backups/portability-mirror/md/policy/surprise.md", "# not from the pipeline\n")
    # No manifest.json at all — if the mirror prefix were wholesale-trusted
    # the way Graph-System/ is, this would read as "expected-writer: derived"
    # with no verification. It must not.
    rc, out, err = run(d)
    assert_(rc == 0, f"seed run should exit 0, got {rc}\nstderr={err}")
    mirror_lines = [ln for ln in out.splitlines() if "surprise.md" in ln]
    assert_(all("expected-writer: derived" not in ln for ln in mirror_lines),
            f"the mirror prefix must never classify as blanket 'derived': {mirror_lines}")


# PR #57 ("the drift watch stops reporting the chain that runs it") added a
# case here asserting the mirror classifies "expected-writer: derived" —
# the blanket DERIVED_DIRS exclusion this merge deliberately supersedes with
# hash verification (see the DERIVED_DIRS comment in ops/vault-drift-watch.py
# and the v4 cases above, especially "DERIVED_DIRS wholesale trust must never
# cover the mirror prefix" just above this note). That case asserted the
# opposite of the design this branch ships and is dropped rather than kept
# disabled, so no test in this file contradicts another.


@case("--rebaseline --only re-snapshots the named paths and carries the rest forward")
def _(assert_):
    d = fresh_dirs("partial")
    joe = "00_Context/compiled-rules-joe.md"
    shared = "DNA/compiled-rules-shared.md"
    write(d["root"], joe, "31 rules\n")
    write(d["root"], shared, "151 rules\n")
    rc, _, err = run(d, mode="--rebaseline")
    assert_(rc == 0, f"full rebaseline should exit 0, got {rc}\nstderr={err}")
    before = json.loads(open(os.path.join(d["baseline"], "manifest.json")).read())
    shared_sha_before = before["files"][shared]["sha256"]

    # Only joe moves, exactly as an hourly refresh would move it.
    write(d["root"], joe, "33 rules\n")
    rc2, out2, err2 = run(d, mode="--rebaseline", extra_args=["--only", joe])
    assert_(rc2 == 0, f"partial rebaseline should exit 0, got {rc2}\nstderr={err2}")
    assert_("partial rebaseline" in out2, "a partial run should say so")
    after = json.loads(open(os.path.join(d["baseline"], "manifest.json")).read())
    assert_(after["files"][joe]["sha256"] != before["files"][joe]["sha256"],
            "the named path's baseline hash should have moved")
    assert_(after["files"][shared]["sha256"] == shared_sha_before,
            "an unnamed path's baseline must be carried forward untouched")
    assert_(after["file_count"] == before["file_count"],
            "a merge must not drop registry entries")
    assert_(after.get("partial_rebaseline", {}).get("prefixes") == [joe],
            "the manifest should record which prefixes were partially rebaselined")

    # The whole point: the moved file must no longer read as TAMPER.
    rc3, _, err3 = run(d)
    assert_(rc3 == 0, f"check after partial rebaseline should exit 0, got {rc3}\nstderr={err3}")
    tampered = []
    if os.path.exists(d["salvage"]):
        tampered = [json.loads(l)["path"] for l in open(d["salvage"])
                    if l.strip() and json.loads(l)["finding_type"] == "tamper"]
    assert_(joe not in tampered,
            f"the rebaselined path must not be reported as tampered: {tampered}")


@case("--rebaseline --only refuses a prefix that matches nothing, rather than emptying the baseline")
def _(assert_):
    d = fresh_dirs("partial-nomatch")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules\n")
    rc, _, _ = run(d, mode="--rebaseline")
    assert_(rc == 0, "full rebaseline should exit 0")
    before = open(os.path.join(d["baseline"], "manifest.json")).read()
    rc2, _, err2 = run(d, mode="--rebaseline",
                       extra_args=["--only", "No/Such/Path.md"])
    assert_(rc2 == 1, f"a no-match --only should exit 1, got {rc2}")
    assert_("matched no registered path" in err2,
            f"the error should say the prefix matched nothing: {err2}")
    after = open(os.path.join(d["baseline"], "manifest.json")).read()
    assert_(after == before, "a refused partial run must leave the baseline byte-identical")


@case("--only-target covers EVERY path its export selector writes, not a hand-kept subset")
def _(assert_):
    # The regression this exists for: refresh-rules.sh hardcoded the 3 render
    # paths, but `export --only compiled-rules` writes 5 (it also matches
    # compiled-rules-gist-index -> CLAUDE.md and compiled-rules-intro ->
    # DNA/Network/introduction-rules.md). Assert against the live registry
    # rather than a literal 5, so a new target is covered automatically.
    # Resolve through the venv interpreter, not this process: importing
    # exporters.targets needs psycopg, which a bare system python3 lacks.
    probe = subprocess.run(
        [VENV_PYTHON, "-c",
         "import json,sys; sys.path.insert(0, %r);"
         "from exporters import targets as t;"
         "print(json.dumps(sorted({rel for k,(rel,_b) in t.TARGETS.items()"
         " if k.startswith('compiled-rules')})))" % REPO],
        capture_output=True, text=True, timeout=60, cwd=REPO)
    assert_(probe.returncode == 0,
            f"could not resolve the compiled-rules targets: {probe.stderr}")
    want = set(json.loads(probe.stdout))
    assert_(len(want) > 3,
            f"fixture check: the compiled-rules selector should span more than the 3 "
            f"render paths that were hardcoded, got {sorted(want)}")

    d = fresh_dirs("only-target")
    for rel in want:
        write(d["root"], rel, "v1\n")
    rc, _, err = run(d, mode="--rebaseline")
    assert_(rc == 0, f"full rebaseline should exit 0, got {rc}\nstderr={err}")
    for rel in want:
        write(d["root"], rel, "v2\n")  # the hourly export moves ALL of them
    rc2, out2, err2 = run(d, mode="--rebaseline",
                          extra_args=["--only-target", "compiled-rules"])
    assert_(rc2 == 0, f"--only-target rebaseline should exit 0, got {rc2}\nstderr={err2}")

    rc3, _, err3 = run(d)
    assert_(rc3 == 0, f"check should be clean after the rebaseline, got {rc3}\nstderr={err3}")
    tampered = []
    if os.path.exists(d["salvage"]):
        tampered = [json.loads(l)["path"] for l in open(d["salvage"])
                    if l.strip() and json.loads(l)["finding_type"] == "tamper"]
    assert_(not tampered,
            f"no compiled-rules path should read as tampered after --only-target: {tampered}")


@case("BOTH modes fail closed when the TARGETS registry cannot be imported")
def _(assert_):
    # Exercised with a REAL interpreter that cannot import the registry (the
    # system python3 has no psycopg), because that is the actual cause every
    # time this has happened -- a caller reaching for `python3` instead of the
    # venv. Skips rather than lies if that interpreter can import it.
    probe = subprocess.run(
        ["/usr/bin/python3", "-c",
         "import sys; sys.path.insert(0, %r); import exporters.targets" % REPO],
        capture_output=True, text=True, timeout=60, cwd=REPO)
    if probe.returncode == 0:
        print("    (skipped: /usr/bin/python3 can import the registry here)")
        return

    d = fresh_dirs("registry-fail-closed")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules\n")
    base = ["--root", d["root"], "--manifest", d["manifest"],
            "--baseline-dir", d["baseline"], "--quarantine-dir", d["quarantine"],
            "--salvage-manifest", d["salvage"], "--summary", d["summary"]]
    for mode in ("--rebaseline", "--check"):
        p = subprocess.run(["/usr/bin/python3", SCRIPT, mode] + base,
                           capture_output=True, text=True, timeout=60)
        assert_(p.returncode == 1,
                f"{mode} should exit 1 without the registry, got {p.returncode}")
        assert_("FATAL" in p.stderr,
                f"{mode} should say FATAL, not warn and continue: {p.stderr[:200]}")
        assert_(".venv/bin/python" in p.stderr,
                f"{mode}'s error should name the interpreter to use: {p.stderr[:200]}")
    assert_(not os.path.exists(d["salvage"]),
            "a refused check must not write salvage rows")
    assert_(not os.path.exists(os.path.join(d["baseline"], "manifest.json")),
            "a refused rebaseline must not write a baseline")


@case("--only and --only-target together are rejected, rather than one silently winning")
def _(assert_):
    d = fresh_dirs("only-both")
    write(d["root"], "DNA/compiled-rules-shared.md", "# rules\n")
    rc, _, err = run(d, mode="--rebaseline",
                     extra_args=["--only", "DNA/", "--only-target", "compiled-rules"])
    assert_(rc == 1, f"passing both selectors should exit 1, got {rc}")
    assert_("pass one" in err, f"the error should say to pass one: {err}")


@case("--only is rejected with --check, which always scans the whole registry")
def _(assert_):
    d = fresh_dirs("partial-mode")
    write(d["root"], "hello.md", "# hello\n")
    rc, _, err = run(d, extra_args=["--only", "DNA/"])  # run() already passes --check
    assert_(rc == 1, f"--only with --check should exit 1, got {rc}")
    assert_("--rebaseline only" in err, f"the error should name the right mode: {err}")


# ── RETIRED-INTO-_to_delete, added 2026-08-16 ────────────────────────────────
# THE FALSE POSITIVE THIS CLOSES. The 2026-08-16 nightly reported three DELETED
# files as UNEXPECTED: 00_Context/sweep-sop.md, Automation/local-tasks/
# review-task.md and DNA/Network/briefs/2026-07-04-network-brief.md. None had
# been deleted. All three had been MOVED to _to_delete/ by the monthly sweep,
# each into a dated folder naming its reason, exactly as Joe's never-delete
# rule requires (faae6748: never delete, always stage into _to_delete).
#
# The cost was not the alarm, it was the reading: the session on duty took the
# report at face value and told Joe his monthly sweep procedure might have been
# deleted by something unknown. Filed as a defect against the class that has
# now failed 17 times. A checker that cries wolf every time the sweep does its
# job correctly trains its reader to be wrong.
#
# The rule: a vault .md that left its home and now exists under _to_delete/ was
# RETIRED by a sanctioned process. Deliberately narrow — it applies to DELETED
# paths only. An ADDED or MODIFIED file is untouched by this, and a file that
# simply vanished with no copy in _to_delete is still UNEXPECTED, because that
# is the real signal the alarm exists for.

@case("DELETED but staged in _to_delete is RETIRED, not UNEXPECTED, exit 0")
def _(assert_):
    d = fresh_dirs("retired-staged")
    write(d["root"], "00_Context/sweep-sop.md", "# sweep sop\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    os.remove(os.path.join(d["root"], "00_Context/sweep-sop.md"))
    write(d["root"], "_to_delete/store-duplicates-2026-08-15/sweep-sop.md", "# sweep sop\n")
    rc2, out2, err2 = run(d)
    assert_("RETIRED" in out2, f"a file staged in _to_delete should read RETIRED\n{out2}")
    assert_(rc2 == 0, f"a sanctioned retirement must not fail the chain, got {rc2}\nstderr={err2}")


@case("DELETED with NO copy in _to_delete is still UNEXPECTED, exit 2")
def _(assert_):
    d = fresh_dirs("retired-absent")
    write(d["root"], "00_Context/vanished.md", "# gone\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    os.remove(os.path.join(d["root"], "00_Context/vanished.md"))
    rc2, out2, _ = run(d)
    assert_("UNEXPECTED" in out2, "a file that vanished with no staged copy is the real alarm")
    assert_(rc2 == 2, f"an unstaged deletion must still fail the chain, got {rc2}")


@case("a DIFFERENT filename in _to_delete does not excuse a deletion")
def _(assert_):
    d = fresh_dirs("retired-mismatch")
    write(d["root"], "00_Context/vanished.md", "# gone\n")
    write(d["root"], "_to_delete/older-batch/unrelated.md", "# unrelated\n")
    rc1, _, _ = run(d)
    assert_(rc1 == 0, "seed run should exit 0")
    os.remove(os.path.join(d["root"], "00_Context/vanished.md"))
    rc2, out2, _ = run(d)
    assert_(rc2 == 2, f"an unrelated staged file must not excuse this deletion, got {rc2}")


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        status = "PASS" if not errors else "FAIL"
        if VERBOSE or errors:
            print(f"[{status}] {name}")
            for e in errors:
                print(f"    - {e}")
        if errors:
            failures.append(name)

    shutil.rmtree(SCRATCH_BASE, ignore_errors=True)

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
