#!/usr/bin/env python3
"""Regression fixtures for non-destructive Codex global-hook reconciliation."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# THE ONE SCRUBBER, not a local copy (rule a8c55a47). The hooksPath fixture below
# runs `git init`, and ops/selftest-git-isolation-check.py requires every selftest
# that builds a git repository to reach git through ops/git_env.py: an inherited
# GIT_DIR outranks both cwd and -C, which is how a fixture destroyed local main on
# 2026-08-14. fixture_env() additionally hides system and global config, so the
# core.hooksPath this fixture reads back can only be the one it set itself.
sys.path.insert(0, os.path.join(REPO, "ops"))
from git_env import fixture_env  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "config_as_code", os.path.join(REPO, "ops", "config-as-code.py")
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NIGHTLY_SOURCE = (Path(REPO) / "ops/scheduled-tasks/nightly-record-layer.SKILL.md").read_text(
    encoding="utf-8")
# The fixture below tests config reconciliation. Machine dependencies have their
# own hermetic suite and must not be inferred from a temporary HOME.
setattr(mod, "PREREQUISITE_CHECK", lambda _repo: [])


def copy_continuity_contract(repo: Path) -> None:
    for relative in (
        "ops/config/claude-continuity-hooks.json",
        "ops/claude-continuity-hook.py",
        "mcp-server/continuity-stdio-proxy.mjs",
    ):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((Path(REPO) / relative).read_bytes())

DESIRED = {"hooks": {
    "Stop": [{"hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}],
}}
LIVE = {
    "hooks": {
        "Stop": [
            {"hooks": [{"type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5}]},
            {"hooks": [{"type": "command", "command": "/Users/booko/carr-system/hooks/old.py", "timeout": 10}]},
            {"hooks": [
                {"type": "command", "command": "/Users/booko/other/hooks/mixed.py", "timeout": 5},
                {"type": "command", "command": "/Users/booko/carr-system/hooks/old2.py", "timeout": 10},
            ]},
        ],
        "PostToolUse": [{"matcher": "Other", "hooks": [{
            "type": "command", "command": "/Users/booko/other/hooks/post.py", "timeout": 5,
        }]}],
    },
    "user_setting": {"keep": True},
}


def commands(doc):
    return [hook.get("command") for groups in doc["hooks"].values()
            for group in groups for hook in group.get("hooks", []) if isinstance(hook, dict)]


def permission_review_regressions():
    """Every review fixture checks semantic preservation and repeat-run repair."""
    import tomllib
    default, body = mod.codex_permissions_source()
    expected = tomllib.loads(default + "\n" + body)
    fixtures = {
        "1-marker-in-string": default + "\nnotes = '''\n" + mod.CODEX_PERMISSIONS_BEGIN
            + '\nexample="keep"\n' + mod.CODEX_PERMISSIONS_END
            + "\n'''\n[permissions.carr_unattended]\nextends=\":workspace\"\n",
        "2-header-in-string": "notes = '''\n[example]\nx=1\n'''\nmodel=\"fixture\"\n"
            + '[permissions.carr_unattended]\nextends=":workspace"\n',
        "2-indented-header": '  [permissions.carr_unattended]\nextends=":workspace"\n',
        "2-quoted-default": '\'default_permissions\' = \'old\'\nmodel="keep"\n',
        "3-nested-array": default + '\n[permissions.carr_unattended]\nvalues=[\n'
            + '["a", "b"],\n["c"], # continuation\n]\n[other]\nvalue=42\n',
        "4-inline-profile": default + '\n[permissions]\n'
            + 'carr_unattended={extends=":read-only",network={enabled=false}}\n'
            + 'custom={description="keep"}\n[other]\nvalue=42\n',
        "4-root-inline": default + '\npermissions={carr_unattended={extends=":workspace"},'
            + 'custom={description="keep"}}\n[other]\nvalue=42\n',
        "4-dotted-profile": default + '\n[permissions]\ncarr_unattended.extends=":workspace"\n'
            + 'custom.description="keep"\n[other]\nvalue=42\n',
        "4-root-dotted": default + '\npermissions.carr_unattended.extends=":workspace"\n'
            + 'permissions.custom.description="keep"\n[other]\nvalue=42\n',
    }
    # These valid documents must never be accepted from marker text alone.
    examples = default + "\nnotes = '''\n" + mod.CODEX_PERMISSIONS_BEGIN + "\n" + body + mod.CODEX_PERMISSIONS_END + "\n'''\n"
    assert mod.canonical_codex_permissions(examples) is None
    missing_default = mod.CODEX_PERMISSIONS_BEGIN + "\n" + body + mod.CODEX_PERMISSIONS_END + "\n"
    assert mod.canonical_codex_permissions(missing_default) is None
    fixtures['1-marker-with-user-data'] = (default + "\n" + mod.CODEX_PERMISSIONS_BEGIN
        + '\nnotes="keep" # user comment\n' + mod.CODEX_PERMISSIONS_END
        + '\n[permissions.carr_unattended]\nextends=":workspace"\n[other]\nvalue=42\n')
    fixtures['3-user-nested-array'] = (default + '\n[permissions.carr_unattended]\nextends=":workspace"\n'
        + '[permissions.custom]\nvalues=[\n["a", "b"],\n["c"],\n] # keep array\n'
        + "notes=\"\"\"\n[not.a.header]\n\"\"\"\n[other]\nvalue=42\n")
    failures = []
    for name, raw in fixtures.items():
        try:
            before = tomllib.loads(raw)
            result = mod.install_codex_permissions(raw, default, body)
            after = tomllib.loads(result)
            assert after['default_permissions'] == expected['default_permissions']
            for profile, value in expected['permissions'].items():
                assert after['permissions'][profile] == value
            def unrelated(doc):
                doc.pop('default_permissions', None)
                permissions = doc.get('permissions', {})
                for profile in expected['permissions']:
                    permissions.pop(profile, None)
                if not permissions:
                    doc.pop('permissions', None)
                return doc
            assert unrelated(before) == unrelated(after), 'unrelated values changed'
            assert mod.install_codex_permissions(result, default, body) == result, 'not idempotent'
            assert mod.canonical_codex_permissions(result) == mod.portable(default + "\n\n" + body)
            print(f"PASS permission review {name}")
        except Exception as exc:
            failures.append(name)
            print(f"FAIL permission review {name}: {type(exc).__name__}: {exc}")
    assert not failures, f"permission review regressions: {failures}"


def main():
    permission_review_regressions()
    import tomllib
    default, body = mod.codex_permissions_source()
    # Codex may rewrite TOML syntax and discard comments without changing the
    # installed permissions. Read the same reserved paths the installer owns.
    import tomlkit
    expected_permissions = mod.portable(default + "\n\n" + body)
    markerless = mod.concrete(default + "\n\n" + body)
    assert mod.canonical_codex_permissions(markerless) == expected_permissions
    values = tomllib.loads(markerless)
    rewritten = tomlkit.dumps({
        'notes': mod.CODEX_PERMISSIONS_BEGIN + '\nexample only',
        'default_permissions': values['default_permissions'],
        'permissions': {
            'custom': {'description': 'keep me'},
            **{name: dict(reversed(list(profile.items())))
               for name, profile in reversed(list(values['permissions'].items()))},
        },
    })
    assert mod.canonical_codex_permissions(rewritten) == expected_permissions
    quoted = markerless.replace('[permissions.carr_unattended]',
                                "[ 'permissions' . 'carr_unattended' ]")
    assert mod.canonical_codex_permissions(quoted) == expected_permissions
    values['permissions']['carr_unattended']['network']['enabled'] = False
    drifted = mod.canonical_codex_permissions(tomlkit.dumps(values))
    assert isinstance(drifted, str) and drifted != expected_permissions
    assert tomllib.loads(drifted)['permissions']['carr_unattended']['network']['enabled'] is False
    values['default_permissions'] = 'carr_drive_readonly'
    assert tomllib.loads(mod.canonical_codex_permissions(tomlkit.dumps(values)))[
        'default_permissions'] == 'carr_drive_readonly'
    del values['permissions']['carr_drive_readonly']
    assert mod.canonical_codex_permissions(tomlkit.dumps(values)) is None
    print('PASS semantic permission reads survive comment/syntax rewrites and detect drift')
    raw = ('model = "fixture"\n' + default + '\n'
           '[permissions.carr_unattended]\nextends = ":read-only"\n'
           '[permissions.carr_unattended.network]\nenabled = false\n'
           '[permissions.custom]\ndescription = "keep me"\n'
           '[permissions.carr_drive_readonly]\nextends = ":workspace"\n'
           '[permissions.carr_drive_readonly.filesystem]\n"." = "write"\n'
           '[other]\nvalue = 42\n')
    planned = mod.install_codex_permissions(raw, default, body)
    parsed = tomllib.loads(planned)
    assert parsed['permissions']['custom']['description'] == 'keep me'
    assert parsed['other']['value'] == 42
    assert parsed['model'] == 'fixture'
    assert parsed['permissions']['carr_unattended']['network']['enabled'] is True
    assert mod.install_codex_permissions(planned, default, body) == planned
    quoted = raw.replace('[permissions.carr_unattended]', '[ "permissions" . "carr_unattended" ]')
    assert tomllib.loads(mod.install_codex_permissions(quoted, default, body))['other']['value'] == 42
    # Recover a config already damaged by an older nightly append.
    duplicated = raw + "\n" + mod.CODEX_PERMISSIONS_BEGIN + "\n" + body + mod.CODEX_PERMISSIONS_END + "\n"
    repaired = mod.install_codex_permissions(duplicated, default, body)
    tomllib.loads(repaired)
    assert mod.install_codex_permissions(repaired, default, body) == repaired
    multiline = 'notes = \'\'\'\n[permissions.carr_unattended]\nexample = "keep"\n\'\'\'\n' + raw
    fixed = mod.install_codex_permissions(multiline, default, body)
    assert tomllib.loads(fixed)['notes'] == tomllib.loads(multiline)['notes']
    print('markerless permissions reconciled; valid TOML; repeat unchanged')
    merged = mod.merge_codex_carr_hooks(LIVE, DESIRED)
    names = commands(merged)
    again = mod.merge_codex_carr_hooks(merged, DESIRED)
    live_permissions = (
        'default_permissions = "carr_unattended"\n\n'
        f'{mod.CODEX_PERMISSIONS_BEGIN}\n'
        '[permissions.carr_unattended]\n'
        'extends = ":workspace"\n\n'
        '[permissions.carr_unattended.workspace_roots]\n'
        f'"{mod.REPO}" = true\n\n'
        '[permissions.carr_unattended.network]\n'
        'enabled = true\n\n'
        '[permissions.carr_drive_readonly.filesystem]\n'
        f'"{mod.REPO}" = "write"\n'
        f'{mod.CODEX_PERMISSIONS_END}\n'
    )
    portable_permissions = mod.canonical_codex_permissions(live_permissions)
    empty_codex_snapshot = mod.carr_owned_hooks_document({}, ["Stop"])
    call_mode_source = mod.launchd_repo_path("com.carr.call-mode.plist")
    ordinary_launchd_source = mod.launchd_repo_path("com.carr.example.plist")
    with tempfile.TemporaryDirectory(prefix="carr-claude-only-") as temp_home:
        home = Path(temp_home)
        repo = home / "carr-system"
        config = repo / "ops" / "config"
        launchd = repo / "ops" / "launchd"
        tasks = repo / "ops" / "scheduled-tasks"
        config.mkdir(parents=True)
        launchd.mkdir(parents=True)
        tasks.mkdir(parents=True)
        copy_continuity_contract(repo)
        hooks_source = {"PreToolUse": []}
        (config / "hooks.json").write_text(
            json.dumps(hooks_source, indent=2) + "\n", encoding="utf-8"
        )
        mod.REPO = str(repo)
        mod.SETTINGS = str(home / ".claude" / "settings.json")
        mod.CLAUDE_CONTINUITY_MODE_FILE = str(
            home / ".config/carr/claude-continuity-mode.json")
        mod.CLAUDE_MCP_CONFIG = str(home / ".claude.json")
        mod.TASKS_SRC = str(home / ".claude" / "scheduled-tasks")
        mod.TASKS_REPO = str(tasks)
        mod.TASKS_QUARANTINE = str(
            home / ".claude" / "scheduled-tasks-quarantine" / "carr-primary-only"
        )
        mod.LAUNCHD_SRC = str(home / "Library" / "LaunchAgents")
        mod.LAUNCHD_REPO = str(launchd)
        mod.LAUNCHD_ALT_REPO = {}
        mod.HOOKS_REPO = str(config / "hooks.json")
        mod.CODEX_HOOKS_SRC = str(home / ".codex" / "hooks.json")
        mod.CODEX_CONFIG = str(home / ".codex" / "config.toml")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            claude_only_rc = mod.cmd_install(True)
            claude_only_check = mod.cmd_check()
        claude_only_output = output.getvalue()
        settings_created = Path(mod.SETTINGS).is_file()
        installed_settings = json.loads(Path(mod.SETTINGS).read_text(encoding="utf-8"))
        codex_created = (home / ".codex").exists()
        Path(mod.CODEX_HOOKS_SRC).parent.mkdir(parents=True)
        Path(mod.CODEX_HOOKS_SRC).write_text("{}\n", encoding="utf-8")
        partial_state = mod.codex_configuration_state()
        with contextlib.redirect_stdout(io.StringIO()):
            partial_install_rc = mod.cmd_install(False)
            partial_check_rc = mod.cmd_check()
            partial_pull_rc = mod.cmd_pull(False)
        desired_codex = {"hooks": {"Stop": []}}
        (config / "codex-hooks.json").write_text(
            json.dumps(desired_codex, indent=2) + "\n", encoding="utf-8"
        )
        (config / "codex-permissions.toml").write_text(
            'default_permissions = "carr_unattended"\n\n'
            '[permissions.carr_unattended]\n'
            'extends = ":workspace"\n\n'
            '[permissions.carr_unattended.workspace_roots]\n'
            '"{{REPO}}" = true\n\n'
            '[permissions.carr_unattended.network]\n'
            'enabled = true\n\n'
            '[permissions.carr_drive_readonly.filesystem]\n'
            '"{{REPO}}" = "write"\n',
            encoding="utf-8",
        )
        Path(mod.CODEX_CONFIG).write_text(
            'model = "test"\n'
            'default_permissions = "carr_drive_readonly"\n',
            encoding="utf-8",
        )
        mod.CODEX_HOOKS_REPO = str(config / "codex-hooks.json")
        mod.CODEX_PERMISSIONS_REPO = str(config / "codex-permissions.toml")
        with contextlib.redirect_stdout(io.StringIO()):
            configured_rc = mod.cmd_install(True)
            configured_check = mod.cmd_check()
        configured_hooks = json.loads(Path(mod.CODEX_HOOKS_SRC).read_text(encoding="utf-8"))
        configured_toml = Path(mod.CODEX_CONFIG).read_text(encoding="utf-8")

        # Scheduled task *definitions* are CARR-managed only when their name
        # exists in the repo registry.  Primary install must render every
        # tracked definition; secondary install must move an exact tracked
        # render out of Claude's active directory without touching a personal
        # task.  The 16-task fixture mirrors Dell's reproduced current state.
        primary_tasks = {
            f"task-{number:02d}": f"---\nname: task-{number:02d}\n---\nCARR managed {number}\n"
            for number in range(1, 17)
        }
        primary_tasks["nightly-record-layer"] = NIGHTLY_SOURCE
        for name, body in primary_tasks.items():
            (tasks / f"{name}.SKILL.md").write_text(body, encoding="utf-8")
        original_primary = mod.IS_PRIMARY
        mod.IS_PRIMARY = True
        with contextlib.redirect_stdout(io.StringIO()) as primary_task_out:
            primary_task_install_rc = mod.cmd_install(True)
            primary_task_check_rc = mod.cmd_check()
        primary_task_rendered = all(
            (Path(mod.TASKS_SRC) / name / "SKILL.md").read_text(encoding="utf-8")
            == mod.concrete(body)
            for name, body in primary_tasks.items()
        )
        nightly_task_rendered = (
            Path(mod.TASKS_SRC) / "nightly-record-layer" / "SKILL.md"
        ).read_text(encoding="utf-8") == mod.concrete(NIGHTLY_SOURCE)

        mod.IS_PRIMARY = False
        secondary_task_source = Path(mod.TASKS_SRC)
        with contextlib.redirect_stdout(io.StringIO()) as secondary_dry_out:
            secondary_dry_rc = mod.cmd_install(False)
        secondary_dry_output = secondary_dry_out.getvalue()
        secondary_dry_preserves_active = all(
            (secondary_task_source / name / "SKILL.md").is_file()
            for name in primary_tasks
        ) and not Path(mod.TASKS_QUARANTINE).exists()
        with contextlib.redirect_stdout(io.StringIO()) as secondary_clean_out:
            secondary_install_rc = mod.cmd_install(True)
            secondary_clean_check_rc = mod.cmd_check()
        secondary_clean_output = secondary_clean_out.getvalue()
        secondary_quarantine = Path(mod.TASKS_QUARANTINE)
        active_carr_tasks_after = [
            path for path in secondary_task_source.glob("*/SKILL.md")
            if path.parent.name in primary_tasks
        ]
        quarantined_carr_tasks = [
            secondary_quarantine / name / "SKILL.md" for name in primary_tasks
        ]
        secondary_quarantine_complete = (
            secondary_install_rc == 0 and len(active_carr_tasks_after) == 0
            and all(path.is_file() for path in quarantined_carr_tasks)
            and "QUARANTINE  scheduled task task-01" in secondary_clean_output
        )
        with contextlib.redirect_stdout(io.StringIO()) as secondary_retry_out:
            secondary_retry_rc = mod.cmd_install(True)
        secondary_retry_output = secondary_retry_out.getvalue()

        # Personal task names do not belong to CARR's registry: neither the
        # health denominator nor apply is permitted to claim or move them.
        personal_task = secondary_task_source / "personal-reminder" / "SKILL.md"
        personal_task.parent.mkdir(parents=True)
        personal_task.write_text("personal task\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as personal_task_out:
            personal_task_check_rc = mod.cmd_check()
            personal_task_install_rc = mod.cmd_install(True)
        personal_task_output = personal_task_out.getvalue()
        personal_task_preserved = personal_task.is_file()

        # A tracked CARR name with a changed body is potentially user work.
        # It blocks safely and remains active rather than being overwritten or
        # quarantined.
        modified_task = secondary_task_source / "task-01" / "SKILL.md"
        modified_task.parent.mkdir(parents=True)
        modified_task.write_text("modified CARR task\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as secondary_bad_out:
            secondary_bad_check_rc = mod.cmd_check()
            secondary_bad_pull_rc = mod.cmd_pull(True)
            secondary_bad_install_rc = mod.cmd_install(True)
        secondary_bad_output = secondary_bad_out.getvalue()
        modified_task_preserved = modified_task.read_text(encoding="utf-8") == "modified CARR task\n"
        # A launchd body that contains a literal token string and /Users path in a
        # comment must still compare equal after install-style concrete rendering.
        # A shared helper, not a duplicated ad hoc compare, now owns that rule.
        token_comment_name = "com.carr.literal-comment-token.plist"
        token_comment_body = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<!--\n"
            "  NOTE: this comment keeps the literal text {{REPO}} while documenting the\n"
            "  same home path as /Users/booko for human readability.\n"
            "-->\n"
            "<plist version=\"1.0\">\n"
            "<dict>\n"
            "<key>Label</key>\n"
            "<string>com.carr.literal-comment-token</string>\n"
            "<key>ProgramArguments</key>\n"
            "<array>\n"
            "  <string>/usr/bin/env</string>\n"
            "  <string>zsh</string>\n"
            "</array>\n"
            "<key>RunAtLoad</key>\n"
            "<false/>\n"
            "</dict>\n"
            "</plist>\n"
        )
        token_comment_case_home = home / "launchd-token-comment-case"
        token_comment_case_repo = token_comment_case_home / "carr-system"
        token_comment_case_config = token_comment_case_repo / "ops" / "config"
        token_comment_case_launchd = token_comment_case_repo / "ops" / "launchd"
        for p in [token_comment_case_config, token_comment_case_launchd]:
            p.mkdir(parents=True, exist_ok=True)
        original_state = {name: getattr(mod, name) for name in [
            "REPO", "SETTINGS", "TASKS_SRC", "TASKS_REPO", "TASKS_QUARANTINE",
            "LAUNCHD_SRC", "LAUNCHD_REPO", "HOOKS_REPO", "CODEX_HOOKS_SRC",
            "CODEX_HOOKS_REPO", "CODEX_CONFIG", "CODEX_PERMISSIONS_REPO",
            "CLAUDE_CONTINUITY_MODE_FILE", "CLAUDE_MCP_CONFIG",
        ]}
        token_comment_home = token_comment_case_home / "home"
        token_comment_case_settings = token_comment_home / ".claude" / "settings.json"
        hooks = {"PreToolUse": []}
        (token_comment_home / ".claude").mkdir(parents=True, exist_ok=True)
        token_comment_case_settings.write_text(json.dumps({"hooks": hooks}, indent=2) + "\n", encoding="utf-8")
        copy_continuity_contract(token_comment_case_repo)
        (token_comment_case_config / "hooks.json").write_text(
            json.dumps(hooks, indent=2) + "\n", encoding="utf-8"
        )
        mod.REPO = str(token_comment_case_repo)
        mod.SETTINGS = str(token_comment_case_settings)
        mod.CLAUDE_CONTINUITY_MODE_FILE = str(
            token_comment_home / ".config/carr/claude-continuity-mode.json")
        mod.CLAUDE_MCP_CONFIG = str(token_comment_home / ".claude.json")
        mod.TASKS_SRC = str(token_comment_home / ".claude" / "scheduled-tasks")
        mod.TASKS_REPO = str(token_comment_case_repo / "ops" / "scheduled-tasks")
        mod.TASKS_QUARANTINE = str(token_comment_home / ".claude" / "scheduled-tasks-quarantine" / "carr-primary-only")
        mod.LAUNCHD_SRC = str(token_comment_home / "Library" / "LaunchAgents")
        mod.LAUNCHD_REPO = str(token_comment_case_launchd)
        mod.HOOKS_REPO = str(token_comment_case_config / "hooks.json")
        mod.CODEX_HOOKS_SRC = str(token_comment_home / ".codex" / "hooks.json")
        mod.CODEX_HOOKS_REPO = str(token_comment_case_config / "codex-hooks.json")
        mod.CODEX_CONFIG = str(token_comment_home / ".codex" / "config.toml")
        mod.CODEX_PERMISSIONS_REPO = str(token_comment_case_config / "codex-permissions.toml")
        (Path(mod.LAUNCHD_SRC)).mkdir(parents=True, exist_ok=True)
        token_comment_source = token_comment_case_launchd / token_comment_name
        token_comment_source.write_text(token_comment_body, encoding="utf-8")
        token_comment_live = Path(mod.LAUNCHD_SRC) / token_comment_name
        token_comment_live.write_text(mod.concrete(token_comment_body), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as token_comment_out:
            token_comment_clean_check_rc = mod.cmd_check()
        token_comment_clean_output = token_comment_out.getvalue()
        token_comment_live.write_text(
            mod.concrete(token_comment_body).replace("<string>zsh</string>", "<string>bash</string>"),
            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as token_comment_drift_out:
            token_comment_drift_check_rc = mod.cmd_check()
        token_comment_drift_output = token_comment_drift_out.getvalue()
        for name, value in original_state.items():
            setattr(mod, name, value)
        token_comment_source.unlink(missing_ok=True)

        # PIN THE MACHINE ROLE FOR THE LAUNCHD CASES. This used to restore the
        # real machine's role here, which silently made the three launchd
        # assertions below mean different things on different Macs: the synthetic
        # plists are primary-scope, so on a SECONDARY machine (Dell's, actor slug
        # dell) they are never loaded, load_attempts stays empty, and two cases
        # fail for a reason that has nothing to do with what they test. They pass
        # on Joe's Mac and on the GitHub runner, both of which resolve primary, so
        # the split stayed invisible until pre-push CI was run on the secondary.
        # A selftest must assert the same thing everywhere; the real role is
        # restored after the block instead.
        mod.IS_PRIMARY = True
        # The definition-only mechanism is pinned with a SYNTHETIC entry since
        # the 2026-08-26 cutover released the real tick plist from the hold
        # (decision f4af0c87); tick_released below pins that release itself.
        tick_released = "com.carr.control-plane-tick.plist" not in mod.launchd_hold.DEFINITION_ONLY
        original_definition_only = dict(mod.launchd_hold.DEFINITION_ONLY)
        mod.launchd_hold.DEFINITION_ONLY["com.carr.synthetic-definition-only.plist"] = (
            "synthetic hold for the selftest"
        )
        definition_only_plist = {
            "Label": "com.carr.synthetic-definition-only",
            "ProgramArguments": ["/usr/bin/true"],
            "RunAtLoad": False,
        }
        (launchd / "com.carr.synthetic-definition-only.plist").write_bytes(
            plistlib.dumps(definition_only_plist)
        )
        failing_plist = {
            "Label": "com.carr.synthetic-load-failure",
            "ProgramArguments": ["/usr/bin/true"],
            "RunAtLoad": False,
        }
        (launchd / "com.carr.synthetic-load-failure.plist").write_bytes(
            plistlib.dumps(failing_plist)
        )
        real_run = mod.subprocess.run
        load_attempts = []

        def fail_launchctl(args, *call_args, **call_kwargs):
            if args[0] == "launchctl" and "synthetic-definition-only" in args[-1]:
                if args[1] == "print":
                    return SimpleNamespace(returncode=113, stderr='Could not find service "com.carr.synthetic-definition-only"', stdout="")
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            if args[:2] == ["launchctl", "print-disabled"]:
                return SimpleNamespace(returncode=0, stderr="", stdout='"com.carr.synthetic-definition-only" => disabled')
            if args[:2] == ["launchctl", "unload"]:
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            if args[:2] == ["launchctl", "load"]:
                load_attempts.append(args[-1])
                return SimpleNamespace(returncode=1, stderr="synthetic load failure", stdout="")
            return real_run(args, *call_args, **call_kwargs)

        mod.subprocess.run = fail_launchctl
        try:
            with contextlib.redirect_stdout(io.StringIO()) as launchd_out:
                launchd_failure_rc = mod.cmd_install(True)
                launchd_retry_rc = mod.cmd_install(True)
        finally:
            mod.subprocess.run = real_run
            # THE FIXTURE MUST NOT OUTLIVE THE BLOCK THAT NEEDS IT (loop 503,
            # item 6). Every assertion about this plist is already made above,
            # under the stub that keeps `launchctl load` from really running.
            # Left on disk it is loaded FOR REAL by the next cmd_install in this
            # file — which runs with the stub reverted — registering
            # com.carr.synthetic-load-failure in the live gui domain from a temp
            # HOME that is deleted moments later. That is precisely a job with
            # no plist behind it: ops/launchd-plist-parity.py found exactly this
            # one on Joe's Mac on 2026-08-22, nine days after the leak began,
            # and it was the only thing standing between that detector and being
            # wired into the nightly chain. A test that leaves a live job behind
            # is not isolated, however green it reports.
            # BOTH synthetic labels are cleaned up, not just the load-failure
            # one. The definition-only fixture was added 2026-08-27 when the
            # real control-plane tick was released from the hold, and it leaked
            # the same way the comment above describes: on 2026-08-28
            # scheduler-truth found com.carr.synthetic-definition-only loaded
            # with no plist behind it, from a temp HOME deleted moments later.
            # A fixture that can be loaded must be booted out by the same block
            # that created it, whatever the assertions did.
            for _label in ("com.carr.synthetic-load-failure",
                           "com.carr.synthetic-definition-only"):
                (launchd / f"{_label}.plist").unlink(missing_ok=True)
                mod.subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{_label}"],
                    capture_output=True, text=True, check=False)
        mod.IS_PRIMARY = original_primary
        mod.launchd_hold.DEFINITION_ONLY.clear()
        mod.launchd_hold.DEFINITION_ONLY.update(original_definition_only)
        launchd_dir_created = Path(mod.LAUNCHD_SRC).is_dir()
        definition_only_absent = not (
            Path(mod.LAUNCHD_SRC) / "com.carr.synthetic-definition-only.plist"
        ).exists()

        # THE TWO SCHEDULED-TASK GUARDS LANDED WITHOUT COVERAGE (PR 430). Both
        # decide whether a file is CARR configuration at all, so a silent
        # regression in either does not fail loudly — it re-arms the chronically
        # red pre-push gate those guards were written to stop, and that failure
        # arrives on somebody else's unrelated push.
        #
        # The frontmatter restriction is the part worth pinning. The marker is
        # honoured ONLY inside the opening frontmatter block, so a task cannot
        # exempt itself by mentioning the phrase in prose, and a genuine CARR
        # task that someone forgot to commit still shows up as drift.
        ephemeral_cases = {
            "spaced marker in frontmatter": "---\nname: t\nephemeral: true\n---\nbody\n",
            "unspaced marker in frontmatter": "---\nname: t\nephemeral:true\n---\nbody\n",
            "capitalised marker in frontmatter": "---\nname: t\nEphemeral: True\n---\nbody\n",
        }
        non_ephemeral_cases = {
            # THE CASE THE GUARD EXISTS TO GET RIGHT, and it has to be a line
            # that is EXACTLY the marker. The comparison is line equality, so a
            # marker used mid-sentence never matches and would pass even with
            # the frontmatter boundary deleted — such a fixture tests nothing.
            # A task documenting the convention is the realistic shape.
            "marker alone on a prose line below frontmatter":
                "---\nname: t\n---\nTo mark scaffolding, add to the frontmatter:\n\n"
                "ephemeral: true\n\nThat is the only place it counts.\n",
            "marker in prose mid-sentence":
                "---\nname: t\n---\nThis task is ephemeral: true only in spirit.\n",
            "marker with no frontmatter at all": "ephemeral: true\n",
            "marker before the frontmatter opens": "ephemeral: true\n---\nname: t\n---\n",
            "ordinary tracked task": "---\nname: t\n---\nCARR managed\n",
            "empty body": "",
        }
        ephemeral_marker_honoured = all(
            mod.is_ephemeral_scheduled_task(body) for body in ephemeral_cases.values()
        )
        ephemeral_prose_ignored = not any(
            mod.is_ephemeral_scheduled_task(body) for body in non_ephemeral_cases.values()
        )
        definition_only_detected = (
            mod.is_definition_only_task(
                "---\nname: t\n---\nThis definition is disabled. Do not create it.\n")
            and not mod.is_definition_only_task("---\nname: t\n---\nCARR managed\n")
            and not mod.is_definition_only_task("")
        )

        # END TO END, because a correct predicate wired into the wrong place is
        # the failure this pair actually had: the checker honoured
        # "definition only" while the installer did not, and would have written
        # four contracts whose own text forbids creating them.
        mod.IS_PRIMARY = True
        machine_ephemeral = Path(mod.TASKS_SRC) / "handoff-continuation" / "SKILL.md"
        machine_ephemeral.parent.mkdir(parents=True, exist_ok=True)
        machine_ephemeral.write_text(
            "---\nname: handoff-continuation\nephemeral: true\n---\n"
            "Session scaffolding, not tracked configuration.\n",
            encoding="utf-8",
        )
        (tasks / "calendar-prebrief-am.SKILL.md").write_text(
            "---\nname: calendar-prebrief-am\n---\n"
            "This definition is disabled. Do not create, enable, or invoke any scheduler.\n",
            encoding="utf-8",
        )
        guard_labels = [label for label, _live, _repo in mod.pairs()]
        with contextlib.redirect_stdout(io.StringIO()) as guard_out:
            guard_install_rc = mod.cmd_install(True)
        guard_output = guard_out.getvalue()
        mod.IS_PRIMARY = original_primary
        ephemeral_excluded_from_drift = (
            not any("handoff-continuation" in label for label in guard_labels)
            and machine_ephemeral.is_file()
        )
        definition_only_excluded_from_drift = not any(
            "calendar-prebrief-am" in label for label in guard_labels
        )
        definition_only_not_installed = (
            guard_install_rc == 0
            and "SKIP  scheduled task calendar-prebrief-am (definition only:" in guard_output
            and not (Path(mod.TASKS_SRC) / "calendar-prebrief-am" / "SKILL.md").exists()
        )

        # THE REPO-HYGIENE JANITOR IS HELD AS A DEFINITION, AND BOTH HALVES OF
        # THAT MEAN SOMETHING. Its absence from the machine is the intended
        # state, so check must stay silent about it; a copy that HAS been
        # installed is a live-effect gate that was skipped, so check must fail
        # even though the installed bytes are the repo's own. The second half is
        # the one that would rot quietly: an install performed from this very
        # repo produces byte-identical output, so an equality-based comparison
        # would have called it clean.
        janitor_plist = "com.carr.repo-hygiene-janitor.plist"
        janitor_held = janitor_plist in mod.launchd_hold.DEFINITION_ONLY
        janitor_repo_body = (
            Path(REPO) / "ops" / "launchd" / janitor_plist
        ).read_text(encoding="utf-8")
        janitor_src = home / "definition-only-launchagents"
        janitor_src.mkdir()
        original_launchd_src = mod.LAUNCHD_SRC
        mod.LAUNCHD_SRC = str(janitor_src)
        try:
            janitor_absence_silent = (
                mod.definition_only_installed_plists() == []
                and not any(janitor_plist in label for label, _, _ in mod.pairs())
            )
            # Byte-identical to the repo definition, which is exactly what an
            # unauthorized `launchctl load` of this file would leave behind.
            (janitor_src / janitor_plist).write_text(janitor_repo_body, encoding="utf-8")
            janitor_installed_detected = (
                mod.definition_only_installed_plists() == [janitor_plist]
                # Still not an ordinary tracked pair: it is reported by presence.
                and not any(janitor_plist in label for label, _, _ in mod.pairs())
            )
            janitor_check_out = io.StringIO()
            with contextlib.redirect_stdout(janitor_check_out):
                janitor_check_rc = mod.cmd_check()
            janitor_check_output = janitor_check_out.getvalue()
        finally:
            mod.LAUNCHD_SRC = original_launchd_src
        janitor_install_rejected = (
            janitor_check_rc == 1
            and "DEFINITION ONLY, MUST NOT BE INSTALLED" in janitor_check_output
            and janitor_plist in janitor_check_output
        )

        # A hooks block that invokes a script the machine does not have must
        # refuse to install. Applied anyway, it blocks EVERY session at its
        # next prompt — the 2026-08-24 overnight outage, where settings were
        # installed while the checkout they point into was one commit behind
        # the merged hook wrapper they reference.
        mod.IS_PRIMARY = True
        hooks_dir = repo / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        present_script = hooks_dir / "present-gate.py"
        present_script.write_text("# present\n", encoding="utf-8")
        absent_script = hooks_dir / "meter-run.py"
        (config / "hooks.json").write_text(json.dumps({
            "UserPromptSubmit": [{"hooks": [{
                "type": "command",
                "command": f"/usr/bin/env python3 {absent_script} {present_script}",
                "timeout": 10,
            }]}],
        }, indent=2) + "\n", encoding="utf-8")
        settings_before_refusal = Path(mod.SETTINGS).read_text(encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as absent_out:
            absent_install_rc = mod.cmd_install(True)
        absent_output = absent_out.getvalue()
        absent_refused = (
            absent_install_rc == 1
            and str(absent_script) in absent_output
            and str(present_script) not in absent_output
            and Path(mod.SETTINGS).read_text(encoding="utf-8") == settings_before_refusal
        )
        absent_script.write_text("# restored\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            restored_install_rc = mod.cmd_install(True)
        restored_settings = json.loads(Path(mod.SETTINGS).read_text(encoding="utf-8"))
        restored_installed = (
            restored_install_rc == 0
            and str(absent_script) in json.dumps(restored_settings.get("hooks", {}))
        )
        mod.IS_PRIMARY = original_primary

        # THE GATE ZERO SCHEDULER CANARY IS EXPECTED INSTALLED, and the point of
        # asserting it is that the opposite was true yesterday. The plist was
        # held in DEFINITION_ONLY from 2026-09-11 so that STARTING A SCHEDULE
        # stayed a human act; Joe's activation approval took that act on
        # 2026-09-12, and `step:scheduler-active-receipt` — the Gate Zero
        # predecessor the canary exists for — can only be answered by launchd
        # firing on its own, never by a hand dispatch through the wrapper. So a
        # revert that quietly put the canary back on the hold list would leave
        # that predecessor permanently unanswerable while every check stayed
        # green. This pins the release the way tick_released pins the 2026-08-26
        # control-plane cutover.
        #
        # TWO HALVES, because the flag alone proves nothing about the installer:
        # the plist must also be PLANNED as an ordinary write. The plan is taken
        # from a DRY RUN deliberately — the fixtures above record twice over what
        # happens when a selftest lets `launchctl load` really run against a temp
        # HOME that is deleted moments later, and the question here is what the
        # reconciler INTENDS, which the dry run answers in full.
        canary_plist = "com.carr.gate-zero-canary.plist"
        canary_released = canary_plist not in mod.launchd_hold.DEFINITION_ONLY
        (launchd / canary_plist).write_text(
            (Path(REPO) / "ops" / "launchd" / canary_plist).read_text(encoding="utf-8"),
            encoding="utf-8")
        # The wrapper and the canary script itself: missing_targets() skips any
        # agent whose program was never built, so without these the plan would
        # read SKIP for a reason that has nothing to do with the hold.
        for program in ("bin/run-scheduled.sh", "bin/gate-zero-canary.sh"):
            built = repo / program
            built.parent.mkdir(parents=True, exist_ok=True)
            built.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        mod.IS_PRIMARY = True
        with contextlib.redirect_stdout(io.StringIO()) as canary_out:
            canary_plan_rc = mod.cmd_install(False)
        canary_output = canary_out.getvalue()
        # AND THE MIRROR OF THE JANITOR CASE BELOW: an installed copy of a HELD
        # agent is a refusal ("DEFINITION ONLY, MUST NOT BE INSTALLED") however
        # exactly its bytes match the repo, because the gate on it had not
        # passed. A released agent's installed copy is ordinary configuration,
        # so the same fixture — the concrete render sitting in LaunchAgents —
        # must now be an ordinary tracked pair and no refusal at all. Nothing is
        # loaded to prove this: cmd_check only reads.
        live_canary = Path(mod.LAUNCHD_SRC) / canary_plist
        live_canary.write_text(
            mod.concrete((launchd / canary_plist).read_text(encoding="utf-8")),
            encoding="utf-8")
        canary_labels = [label for label, _live, _repo in mod.pairs()]
        canary_installed_allowed = mod.definition_only_installed_plists()
        with contextlib.redirect_stdout(io.StringIO()) as canary_check_out:
            mod.cmd_check()
        canary_check_output = canary_check_out.getvalue()
        mod.IS_PRIMARY = original_primary
        canary_planned = (
            canary_plan_rc == 0
            and f"would write  {live_canary}" in canary_output
            and f"SKIP  {canary_plist}" not in canary_output
            and canary_installed_allowed == []
            and f"launchd {canary_plist}" in canary_labels
            and "DEFINITION ONLY, MUST NOT BE INSTALLED" not in canary_check_output
        )
        live_canary.unlink()
        (launchd / canary_plist).unlink()

        # core.hooksPath IS NO-TOUCH, and this is the one setting in this file
        # whose blast radius is the whole machine rather than the repository it
        # is read from: it lives in the single .git/config every worktree shares.
        # Install used to write the relative "ops/githooks" whenever the value
        # was unset or non-default, so an apply run for two plists silently
        # re-pointed hook resolution in ~50 worktrees from canonical's hooks to
        # each worktree's own — the state ops/prepush-floor-selftest.py relies on
        # being canonical's. It had to be undone by hand during the 2026-09-12
        # Gate Zero activation, which is not a repair whoever runs the installer
        # next will know to perform.
        #
        # THE CONTRACT IS UNCONDITIONAL, so the fixture is a sweep rather than a
        # pair: whatever the value is when apply starts — unset, a relative
        # value that is not the default, the absolute canonical path, an
        # absolute path pointing at some other hooks directory — .git/config is
        # byte-identical when apply finishes. Three of these four the old code
        # would have rewritten, so this is not a restatement of what already
        # passed. What install may still do is make the hooks executable, and
        # what it may NOT do instead is left to `check` to report.
        subprocess.run(["git", "init", "-q", str(repo)],
                       env=fixture_env(), check=True, capture_output=True)
        fixture_hooks = repo / "ops" / "githooks"
        fixture_hooks.mkdir(parents=True, exist_ok=True)
        (fixture_hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        foreign_hooks = repo / "ops" / "not-githooks"
        foreign_hooks.mkdir(parents=True, exist_ok=True)
        local_git_config = repo / ".git" / "config"

        def preset_hooks_path(value: str | None) -> None:
            """Set the fixture's starting value through the REAL subprocess."""
            if value is None:
                subprocess.run(
                    ["git", "-C", str(repo), "config", "--local",
                     "--unset-all", "core.hooksPath"],
                    env=fixture_env(), capture_output=True)
            else:
                subprocess.run(
                    ["git", "-C", str(repo), "config", "--local",
                     "core.hooksPath", value],
                    env=fixture_env(), check=True, capture_output=True)

        def fixture_hooks_path() -> str:
            return subprocess.run(
                ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
                env=fixture_env(), capture_output=True, text=True).stdout.strip()

        # EVERY git ARGV THE INSTALLER USES, teed off as it runs. A final-value
        # assertion cannot tell "did not write" from "wrote the same string
        # back", and the second is a defect under a contract that forbids the
        # write itself — under an inherited GIT_DIR a write aimed at REPO lands
        # in another repository entirely, where this fixture would never see it.
        installer_argv: list[list[str]] = []

        class RecordingSubprocess:
            """subprocess for the module under test, with argv recorded.

            __getattr__ delegates everything else (SubprocessError, DEVNULL,
            CalledProcessError, …) so swapping this in changes what is OBSERVED
            and nothing about what runs.
            """

            def run(self, args, *positional, **keyword):
                if isinstance(args, (list, tuple)):
                    installer_argv.append([str(part) for part in args])
                return subprocess.run(args, *positional, **keyword)

            def __getattr__(self, name):
                return getattr(subprocess, name)

        mod.IS_PRIMARY = True
        original_module_subprocess = mod.subprocess
        mod.subprocess = RecordingSubprocess()
        hooks_path_untouched: dict[str, bool] = {}
        try:
            for label, preset in (
                ("unset", None),
                ("relative", "ops/other-hooks"),
                ("absolute", str(fixture_hooks)),
                ("foreign", str(foreign_hooks)),
            ):
                preset_hooks_path(preset)
                config_before = local_git_config.read_bytes()
                value_before = fixture_hooks_path()
                with contextlib.redirect_stdout(io.StringIO()) as hooks_out:
                    hooks_rc = mod.cmd_install(True)
                hooks_output = hooks_out.getvalue()
                hooks_path_untouched[label] = (
                    hooks_rc == 0
                    and local_git_config.read_bytes() == config_before
                    and fixture_hooks_path() == value_before
                    and "core.hooksPath untouched" in hooks_output
                    and "-> ops/githooks" not in hooks_output
                )
            install_argv = list(installer_argv)
            # LIVENESS, because the assertion below is that a list is EMPTY and
            # an empty list is also what a recorder wired to nothing produces.
            # `check` reads core.hooksPath through the very module attribute a
            # write would go out through, so this proves a core.hooksPath argv
            # reaches the recorder whenever one is issued — and therefore that
            # the installer's silence is the installer's, not the spy's.
            with contextlib.redirect_stdout(io.StringIO()):
                mod.cmd_check()
            reporter_argv = installer_argv[len(install_argv):]
        finally:
            mod.subprocess = original_module_subprocess
        # The executable bit is the half install may still do, and dropping the
        # whole block would satisfy every assertion above.
        hooks_stay_executable = os.access(fixture_hooks / "pre-push", os.X_OK)

        def names_hooks_path(argv: list[str]) -> bool:
            return (argv[:1] == ["git"] and "config" in argv
                    and any("core.hooksPath" in part for part in argv))

        installer_never_names_hooks_path = (
            [argv for argv in install_argv if names_hooks_path(argv)] == []
            and [argv for argv in reporter_argv if names_hooks_path(argv)] != []
        )

        # WHAT INSTALL MAY NOT REPAIR, `check` REPORTS. The report is
        # informational in both directions: it names the observed value, and it
        # moves neither the exit code nor the first line the health row reads,
        # so a machine whose hooks are off is visible without this tool ever
        # touching the setting or going chronically red over it.
        preset_hooks_path(str(foreign_hooks))
        with contextlib.redirect_stdout(io.StringIO()) as foreign_check_out:
            foreign_check_rc = mod.cmd_check()
        foreign_check_output = foreign_check_out.getvalue()
        preset_hooks_path(str(fixture_hooks))
        with contextlib.redirect_stdout(io.StringIO()) as resolving_check_out:
            resolving_check_rc = mod.cmd_check()
        resolving_check_output = resolving_check_out.getvalue()
        mod.IS_PRIMARY = original_primary
        hooks_path_reported_informationally = (
            f"git core.hooksPath: {foreign_hooks} —" in foreign_check_output
            and "[informational]" in foreign_check_output
            and f"git core.hooksPath: {fixture_hooks} —" in resolving_check_output
            # Same verdict either way: the value is reported, never judged.
            and foreign_check_rc == resolving_check_rc
            # And it is never the headline the health row prints.
            and "core.hooksPath" not in foreign_check_output.splitlines()[0]
            and fixture_hooks_path() == str(fixture_hooks)
        )
        # The predicate's own boundary, so the report above cannot be reading a
        # helper that says yes to everything: a relative value that is NOT the
        # default names no single directory (git resolves it per worktree), and
        # an absolute path somewhere else is a different hooks directory.
        conformance_boundary = (
            mod.git_hooks_path_conformant(mod.GIT_HOOKS_RELATIVE, str(fixture_hooks))
            and mod.git_hooks_path_conformant(str(fixture_hooks), str(fixture_hooks))
            and not mod.git_hooks_path_conformant("", str(fixture_hooks))
            and not mod.git_hooks_path_conformant("ops/other-hooks", str(fixture_hooks))
            and not mod.git_hooks_path_conformant(
                str(repo / "ops" / "not-githooks"), str(fixture_hooks))
        )
    cases = [
        ("ephemeral marker is honoured inside frontmatter",
         ephemeral_marker_honoured),
        ("ephemeral marker in prose does not exempt a tracked task",
         ephemeral_prose_ignored),
        ("a disabled definition is recognised, an ordinary body is not",
         definition_only_detected),
        ("an ephemeral machine task is excluded from drift and left alone",
         ephemeral_excluded_from_drift),
        ("a definition-only task is not reported as missing from the machine",
         definition_only_excluded_from_drift),
        ("the installer skips a definition-only scheduled task",
         definition_only_not_installed),
        ("install refuses a hooks block whose script is missing, names only the "
         "missing path, and leaves settings untouched", absent_refused),
        ("install proceeds once the missing hook script exists", restored_installed),
        ("unrelated top-level key preserved", merged.get("user_setting") == {"keep": True}),
        ("unrelated event preserved", "PostToolUse" in merged["hooks"] and "/Users/booko/other/hooks/post.py" in commands(merged)),
        ("unrelated Stop hook preserved", "/Users/booko/other/hooks/keep.py" in names),
        ("mixed group keeps unrelated hook", "/Users/booko/other/hooks/mixed.py" in names),
        ("stale CARR hook removed", all("/carr-system/hooks/old" not in name for name in names)),
        ("desired CARR hook installed once", names.count("/Users/booko/carr-system/hooks/completion-evidence-gate.py") == 1),
        ("second merge is idempotent", again == merged),
        ("live Codex permission paths become portable tokens",
         portable_permissions is not None
         and '"{{REPO}}" = "write"' in portable_permissions
         and 'default_permissions = "carr_unattended"' in portable_permissions
         and '[permissions.carr_unattended.network]' in portable_permissions
         and 'enabled = true' in portable_permissions),
        ("Call Mode LaunchAgent resolves to its tracked tool source",
         call_mode_source.endswith(
             "/tools/dictation-rig/launchd/com.carr.call-mode.plist")),
        ("ordinary LaunchAgent resolves to ops launchd",
         ordinary_launchd_source.endswith(
             "/ops/launchd/com.carr.example.plist")),
        ("missing Codex hooks document is an empty managed snapshot",
         empty_codex_snapshot == {"hooks": {"Stop": []}}),
        ("fresh Claude-only apply creates settings and succeeds",
         claude_only_rc == 0 and settings_created),
        ("fresh Claude-only apply installs the tracked Claude hooks",
         installed_settings.get("hooks") == hooks_source),
        ("Claude-only post-install drift check is clean",
         claude_only_check == 0),
        ("Claude-only install explicitly skips Codex",
         "SKIP  Codex configuration" in claude_only_output
         and "Codex was not configured and was left absent" in claude_only_output),
        ("Claude-only install does not create a Codex home",
         not codex_created),
        ("partial Codex configuration is distinguished from absence",
         partial_state == "partial"),
        ("partial Codex configuration fails install and check",
         partial_install_rc == 1 and partial_check_rc == 1 and partial_pull_rc == 1),
        ("configured Codex install remains supported and drift-clean",
         configured_rc == 0 and configured_check == 0),
        ("configured Codex writes only the desired managed hooks",
         configured_hooks == desired_codex),
        ("configured Codex preserves existing config and adds managed permissions",
         'model = "test"' in configured_toml
         and mod.CODEX_PERMISSIONS_BEGIN in configured_toml
         and mod.CODEX_PERMISSIONS_END in configured_toml),
        ("configured Codex replaces the restrictive default with scoped unattended access",
         configured_toml.count('default_permissions = "carr_unattended"') == 1
         and 'default_permissions = "carr_drive_readonly"' not in configured_toml),
        ("fresh primary install renders all tracked scheduled-task definitions",
         primary_task_install_rc == 0 and primary_task_rendered
         and "WRITE  scheduled task task-01" in primary_task_out.getvalue()),
        ("nightly consumer installs as an exact concrete render of canonical source",
         nightly_task_rendered),
        ("fresh primary scheduled-task install leaves check clean",
         primary_task_check_rc == 0),
        ("secondary dry run names quarantine work without moving active tasks",
         secondary_dry_rc == 0 and secondary_dry_preserves_active
         and "would quarantine  scheduled task task-01" in secondary_dry_output),
        ("secondary quarantines every exact managed definition",
         secondary_quarantine_complete),
        ("secondary exact-task retry is idempotent",
         secondary_retry_rc == 0 and "QUARANTINE" not in secondary_retry_output),
        ("secondary ignores absent primary scheduled-task definitions as drift",
         secondary_clean_check_rc == 0),
        ("unrelated personal scheduled tasks are preserved and excluded",
         personal_task_check_rc == 0 and personal_task_install_rc == 0
         and personal_task_preserved and "personal-reminder" not in personal_task_output),
        ("modified CARR secondary scheduled task fails visibly",
         secondary_bad_check_rc == 1
         and "NOT ALLOWED ON SECONDARY" in secondary_bad_output),
        ("secondary refuses to pull a modified CARR scheduled task into the repo",
         secondary_bad_pull_rc == 1
         and "refusing to capture" in secondary_bad_output
         and modified_task_preserved),
        ("secondary refuses to quarantine or overwrite a modified CARR task",
         secondary_bad_install_rc == 1 and "refusing to move or overwrite" in secondary_bad_output
         and modified_task_preserved),
        ("installed launchd comment tokens compare clean when the live copy differs only by concrete expansion",
         token_comment_clean_check_rc == 0),
        ("launchd comment-token case reports a true byte drift",
         token_comment_drift_check_rc == 1 and "TRACKED BUT DIFFERENT from the live copy" in token_comment_drift_output
         and "launchd com.carr.literal-comment-token.plist" in token_comment_drift_output),
        ("LaunchAgent load failure and idempotent retry both stay nonzero",
         launchd_failure_rc == 1 and launchd_retry_rc == 1 and len(load_attempts) == 2),
        ("definition-only hold skips a held plist without installing it",
         definition_only_absent
         and "DEFINITION ONLY com.carr.synthetic-definition-only:" in launchd_out.getvalue()),
        ("control-plane tick released from definition-only hold (cutover 2026-08-26)",
         tick_released),
        ("the Gate Zero scheduler canary is released from the definition-only "
         "hold (Joe's activation approval, 2026-09-12)", canary_released),
        ("the installer plans the canary as an ordinary launchd write, and "
         "reports it neither skipped nor wrongly installed", canary_planned),
        ("apply leaves an UNSET core.hooksPath byte-identical",
         hooks_path_untouched["unset"]),
        ("apply leaves a non-default RELATIVE core.hooksPath byte-identical",
         hooks_path_untouched["relative"]),
        ("apply leaves an ABSOLUTE core.hooksPath that resolves to these hooks "
         "byte-identical", hooks_path_untouched["absolute"]),
        ("apply leaves a FOREIGN core.hooksPath byte-identical",
         hooks_path_untouched["foreign"]),
        ("apply still makes the hooks executable", hooks_stay_executable),
        ("the installer issues no `git config core.hooksPath` call at all, "
         "under any starting value", installer_never_names_hooks_path),
        ("check reports the observed core.hooksPath without changing it, "
         "without moving the exit code, and never as the headline",
         hooks_path_reported_informationally),
        ("the hooksPath conformance test accepts only the default and an "
         "absolute path that resolves here", conformance_boundary),
        ("the repo-hygiene janitor agent is held as a definition only",
         janitor_held),
        ("check accepts the janitor agent's intended absence in silence",
         janitor_absence_silent),
        ("check rejects an installed janitor agent even byte-identical to the repo",
         janitor_installed_detected and janitor_install_rejected),
        ("fresh install creates the LaunchAgents directory",
         launchd_dir_created),
        # THE PLIST PARSE CHECK MOVED OUT, to ops/launchd-plist-portable-selftest.py.
        # It lived here and could not catch the bug it was written for: this
        # file is declared local_only in ops/config/ci-check-scope.json — for a
        # true and evidenced reason about the LIVE settings comparison below —
        # so on every hosted run the parse check was skipped along with it, and
        # the room-bridge job merged unparseable on 2026-08-22. An exemption is
        # declared per FILE and earned per ASSERTION, so a portable check must
        # not sit in a file with a machine-shaped reason. One home, and it is
        # the one that runs everywhere.
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
