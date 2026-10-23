#!/usr/bin/env python3
"""Exercise the retained exact Quill tree proof in disposable repositories."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from fleet_sync_safety import submodule_tree_is_exact_patch  # noqa: E402
from health_submodule import patches_dir_for, submodule_dirt_is_tracked_patch  # noqa: E402

ENV = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr}")
    return r.stdout.strip()


def build(tmp):
    """origin + clone A (pushes) + clone B (the machine under test, falls behind)."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    git(origin, "init", "-q", "--bare")
    a = os.path.join(tmp, "A")
    git(tmp, "clone", "-q", origin, "A")
    open(os.path.join(a, "f.txt"), "w").write("base\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "base")
    git(a, "branch", "-M", "main")
    git(a, "push", "-q", "origin", "main")
    # Bare repositories inherit the host default branch unless it is set.
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    b = os.path.join(tmp, "B")
    git(tmp, "clone", "-q", origin, "B")
    open(os.path.join(a, "f.txt"), "a").write("newer\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "newer")
    git(a, "push", "-q", "origin", "main")
    return b


def add_expected_quill_patch(tmp, clone):
    """Add the exact permanent Quill-style patch state to ``clone``.

    The submodule is locally seeded so the test exercises the same classifier
    path as production rather than merely faking a porcelain row.
    """
    quill_remote = os.path.join(tmp, "quill-origin")
    quill_source = os.path.join(tmp, "quill-source")
    os.makedirs(quill_remote)
    git(quill_remote, "init", "-q", "--bare")
    git(tmp, "clone", "-q", quill_remote, "quill-source")
    open(os.path.join(quill_source, "quill.txt"), "w").write("base\n")
    open(os.path.join(quill_source, "other.txt"), "w").write("base\n")
    open(os.path.join(quill_source, "blob.bin"), "wb").write(b"\x00base\n")
    open(os.path.join(quill_source, "script.sh"), "w").write("#!/bin/sh\nexit 0\n")
    git(quill_source, "add", "quill.txt", "other.txt", "blob.bin", "script.sh")
    git(quill_source, "commit", "-qm", "quill base")
    git(quill_source, "branch", "-M", "main")
    git(quill_source, "push", "-q", "origin", "main")
    git(quill_remote, "symbolic-ref", "HEAD", "refs/heads/main")

    # Rebuild the fixture's remote from a parent containing the submodule, then
    # clone it exactly as the machine under test would.
    parent = os.path.join(tmp, "parent")
    git(tmp, "clone", "-q", os.path.join(tmp, "origin"), "parent")
    os.makedirs(os.path.join(parent, "tools", "dictation-rig", "patches"), exist_ok=True)
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        quill_remote, "tools/dictation-rig/vendor/quill")
    patch = os.path.join(parent, "tools", "dictation-rig", "patches", "0001.patch")
    open(patch, "w").write("--- a/quill.txt\n+++ b/quill.txt\n@@ -1 +1 @@\n-base\n+patched\n")
    git(parent, "add", ".gitmodules", "tools/dictation-rig/vendor/quill", patch)
    git(parent, "commit", "-qm", "add quill patch")
    git(parent, "push", "-q", "origin", "main")

    # Advance B with the parent contents and initialise its submodule.  The
    # expected dirty state is then a real diff equal to the tracked patch.
    git(clone, "pull", "-q", "--ff-only", "origin", "main")
    git(clone, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    quill = os.path.join(clone, "tools", "dictation-rig", "vendor", "quill")
    open(os.path.join(quill, "quill.txt"), "w").write("patched\n")
    return quill, parent


def line_classifier_accepts(quill):
    diff = git(quill, "diff")
    return submodule_dirt_is_tracked_patch(diff, patches_dir_for(quill))


def require_exact_refusal(repo, expected_reason="tracked Quill tree differs"):
    exact, reason = submodule_tree_is_exact_patch(repo)
    assert not exact, "exact Git tree proof accepted a non-canonical Quill tree"
    assert expected_reason in reason, reason


def add_fixture_patch(repo, name, body):
    patch = os.path.join(repo, "tools", "dictation-rig", "patches", name)
    open(patch, "w").write(body)


def test_exact_tree_refuses_duplicate_changed_line():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("patched\npatched\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier accepts duplicate"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses duplicate changed line")


def test_exact_tree_refuses_wrong_file_relocation():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("base\n")
        open(os.path.join(quill, "other.txt"), "w").write("patched\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses paths"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses changed lines relocated to wrong file")


def test_exact_tree_refuses_binary_edit():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "blob.bin"), "wb").write(b"\x00changed\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses binary dirt"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses binary edit")


def test_exact_tree_refuses_executable_mode_edit():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        os.chmod(os.path.join(quill, "script.sh"), 0o755)
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses mode dirt"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses executable-mode edit")


def test_exact_tree_refuses_local_submodule_head_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "local-head.txt"), "w").write("local commit\n")
        git(quill, "add", "local-head.txt")
        git(quill, "commit", "-qm", "move local submodule head")
        require_exact_refusal(b, "checked-out Quill HEAD differs")
    print("PASS  exact Git tree refuses local submodule HEAD mismatch")


def test_exact_tree_ignores_untracked_submodule_scratch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        scratch = os.path.join(quill, "untracked-build-output.bin")
        open(scratch, "wb").write(b"scratch")
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree intentionally ignores untracked submodule scratch")


def test_exact_tree_refuses_deleted_file_resurrection():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-delete-other.patch",
            "diff --git a/other.txt b/other.txt\n"
            "deleted file mode 100644\n"
            "--- a/other.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-base\n",
        )
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses deletion path"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses deleted-file resurrection")


def test_exact_tree_refuses_rename_old_path_resurrection():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-rename-other.patch",
            "diff --git a/other.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from other.txt\n"
            "rename to renamed.txt\n",
        )
        open(os.path.join(quill, "renamed.txt"), "w").write("base\n")
        assert line_classifier_accepts(quill), "precondition: line-set classifier loses rename paths"
        require_exact_refusal(b)
    print("PASS  exact Git tree refuses rename old-path resurrection")


def test_exact_tree_accepts_canonical_rename():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-rename-other.patch",
            "diff --git a/other.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from other.txt\n"
            "rename to renamed.txt\n",
        )
        open(os.path.join(quill, "renamed.txt"), "w").write("base\n")
        os.unlink(os.path.join(quill, "other.txt"))
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree accepts canonical rename")


def test_exact_tree_accepts_canonical_new_file_patch():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, _ = add_expected_quill_patch(tmp, b)
        add_fixture_patch(
            b, "0002-add-generated-source.patch",
            "diff --git a/new-source.txt b/new-source.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new-source.txt\n"
            "@@ -0,0 +1 @@\n"
            "+canonical addition\n",
        )
        open(os.path.join(quill, "new-source.txt"), "w").write("canonical addition\n")
        exact, reason = submodule_tree_is_exact_patch(b)
        assert exact, reason
    print("PASS  exact Git tree accepts canonical patch-added file")


def main():
    test_exact_tree_refuses_duplicate_changed_line()
    test_exact_tree_refuses_wrong_file_relocation()
    test_exact_tree_refuses_binary_edit()
    test_exact_tree_refuses_executable_mode_edit()
    test_exact_tree_refuses_local_submodule_head_mismatch()
    test_exact_tree_ignores_untracked_submodule_scratch()
    test_exact_tree_refuses_deleted_file_resurrection()
    test_exact_tree_refuses_rename_old_path_resurrection()
    test_exact_tree_accepts_canonical_rename()
    test_exact_tree_accepts_canonical_new_file_patch()
    print("Exact Quill tree cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
