#!/usr/bin/env python3
"""Hermetic contract test for the room-bridge LaunchAgent rendering.

The bridge runs under launchd, not an interactive shell.  A scheduled Hermes
lookup therefore needs both the user-local bin directory and a fully rendered
home/repository path.  This test reads only repository files and renders a
temporary fixture; it never invokes launchctl, Hermes, the Worker, or a room
verb.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLIST = REPO / "ops" / "launchd" / "com.carr.room-bridge.plist"
INSTALLER = REPO / "bin" / "install-room-bridge.sh"
TOKEN = re.compile(r"\{\{[^}]+\}\}")
CONCRETE_HOME = re.compile(r"(?:/Users|/home)/[^/\s\"']+")


def check(label: str, condition: bool, failures: list[str]) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        failures.append(label)


def run_installer(home: str, *arguments: str, validator: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real zsh installer after zsh startup has seen its HOME.

    zsh may normalize HOME during process startup when invoked directly with a
    fixture environment. Assigning it inside `-c`, after startup, makes the
    fixture value the one the installer actually renders.
    """
    env = dict(os.environ)
    if validator is None:
        env.pop("CARR_ROOM_BRIDGE_PLIST_VALIDATOR", None)
    else:
        env["CARR_ROOM_BRIDGE_PLIST_VALIDATOR"] = validator
    return subprocess.run(
        ["/bin/zsh", "-c", 'HOME="$1"; export HOME; shift; exec "$@"',
         "room-bridge-selftest", home, str(INSTALLER), *arguments],
        cwd=REPO, env=env, capture_output=True, text=True,
    )


def process_check(label: str, process: subprocess.CompletedProcess[str],
                  predicate, failures: list[str]) -> None:
    ok = predicate(process.returncode)
    if not ok:
        stdout = (process.stdout or "")[:240].replace("\n", "\\n")
        stderr = (process.stderr or "")[:240].replace("\n", "\\n")
        print(f"  detail {label}: rc={process.returncode} stdout={stdout!r} stderr={stderr!r}")
    check(label, ok, failures)


def main() -> int:
    failures: list[str] = []
    template = PLIST.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    try:
        parsed = plistlib.loads(template.encode("utf-8"))
        parsed_ok = True
    except Exception as exc:  # pragma: no cover - reported as a named check
        parsed = {}
        parsed_ok = False
        print(f"  detail plist parse: {type(exc).__name__}: {exc}")
    check("room-bridge plist parses before rendering", parsed_ok, failures)

    environment = parsed.get("EnvironmentVariables", {}) if parsed_ok else {}
    launch_path = environment.get("PATH", "")
    check("template PATH includes the home-relative Hermes bin",
          "{{HOME}}/.local/bin:" in launch_path, failures)
    check("template explicitly polls the visible Model Room",
          environment.get("CARR_ROOM_BRIDGE_ROOM") == "model-room", failures)
    check("template explicitly enables the lease-bound Engineering controller",
          environment.get("CARR_ENGINEERING_DISPATCH_ENABLED") == "true", failures)
    check("template has no concrete user home", not CONCRETE_HOME.search(template), failures)
    check("template retains the repository placeholder", "{{REPO}}" in template, failures)
    check("installer renders {{REPO}}", "s|{{REPO}}|$REPO_REPLACEMENT|g" in installer, failures)
    check("installer renders {{HOME}}", "s|{{HOME}}|$HOME_REPLACEMENT|g" in installer, failures)
    check("installer has no concrete user home", not CONCRETE_HOME.search(installer), failures)
    check("installer refuses unresolved tokens", r"grep -Eq '\{\{[^}]+\}\}'" in installer, failures)
    check("installer exposes a render-only seam", "--render-only" in installer, failures)
    check("normal room-bridge activation bootstraps the dedicated Engineering desk",
          '"$REPO/bin/install-engineering-codex-desk.sh"' in installer, failures)
    check("normal room-bridge activation bootstraps the Claude Desktop desk",
          '"$REPO/bin/install-claude-desktop-desk.sh"' in installer, failures)
    check("installer exposes a named plist validator", "validate_plist()" in installer, failures)
    portable_mktemp = 'mktemp "${TMPDIR:-/tmp}/carr-room-bridge-plist.XXXXXX"'
    check("installer pins a GNU/macOS portable mktemp template",
          portable_mktemp in installer, failures)
    check("installer has no BSD-only mktemp prefix",
          'mktemp -t carr-room-bridge-plist' not in installer, failures)

    # Exercise the same placeholder contract with paths containing spaces. The
    # installer separately escapes sed replacement metacharacters; plist XML
    # itself still correctly rejects an unescaped ampersand before installation.
    fixture_repo = "/tmp/CARR checkout staging"
    fixture_home = "/tmp/Joe local home"
    rendered = template.replace("{{REPO}}", fixture_repo).replace("{{HOME}}", fixture_home)
    check("fixture rendering leaves no template tokens", not TOKEN.search(rendered), failures)
    try:
        concrete = plistlib.loads(rendered.encode("utf-8"))
        concrete_ok = True
    except Exception as exc:  # pragma: no cover - reported as a named check
        concrete = {}
        concrete_ok = False
        print(f"  detail rendered plist parse: {type(exc).__name__}: {exc}")
    check("rendered fixture plist parses", concrete_ok, failures)
    concrete_path = concrete.get("EnvironmentVariables", {}).get("PATH", "")
    check("rendered PATH points at the fixture home-local bin",
          concrete_path.startswith(f"{fixture_home}/.local/bin:"), failures)
    check("rendered controller enablement remains explicit",
          concrete.get("EnvironmentVariables", {}).get("CARR_ENGINEERING_DISPATCH_ENABLED") == "true", failures)
    check("rendered room selection remains the visible Model Room",
          concrete.get("EnvironmentVariables", {}).get("CARR_ROOM_BRIDGE_ROOM") == "model-room", failures)
    arguments = concrete.get("ProgramArguments", [])
    check("rendered program arguments point at the fixture checkout",
          any(fixture_repo in str(arg) for arg in arguments), failures)
    rendered_text = rendered
    check("rendered plist carries no local or Hermes token variable",
          "CARR_MCP_LOCAL_TOKEN" not in rendered_text and
          "CARR_HERMES_MCP_TOKEN" not in rendered_text, failures)
    check("rendered plist carries no client identity selector",
          "CARR_MCP_CLIENT_PROFILE" not in rendered_text, failures)

    # The unresolved-token refusal must happen before install can touch the
    # user's LaunchAgents directory.
    refusal = installer.index("grep -Eq")
    install = installer.index("/usr/bin/install")
    check("unresolved-token refusal precedes destination install",
          refusal >= 0 and refusal < install, failures)
    validator = installer.index("validate_plist \"$TMP\"")
    check("plist validation precedes destination install",
          validator >= 0 and validator < install, failures)
    engineering_bootstrap = installer.index('"$REPO/bin/install-engineering-codex-desk.sh"')
    check("Engineering desk bootstrap precedes destination install",
          engineering_bootstrap >= 0 and engineering_bootstrap < install, failures)
    claude_desktop_bootstrap = installer.index('"$REPO/bin/install-claude-desktop-desk.sh"')
    check("Claude Desktop desk bootstrap precedes destination install",
          claude_desktop_bootstrap >= 0 and claude_desktop_bootstrap < install, failures)

    # The installer lane is only truthful if this non-CI activation path is
    # visible to the repository reachability audit, not just to this test.
    reachability = subprocess.run(
        [sys.executable, str(REPO / "ops" / "reachability-check.py"), "--repo", str(REPO), "--json"],
        capture_output=True, text=True,
    )
    try:
        reachability_payload = json.loads(reachability.stdout or "{}")
        reachability_rows = reachability_payload.get("findings", []) if isinstance(reachability_payload, dict) else []
    except json.JSONDecodeError:
        reachability_rows = [{"entry": "unparseable"}]
    check("Engineering desk installer has a live room-bridge activation door",
          not any(row.get("lane") == "installer" and row.get("entry") == "bin/install-engineering-codex-desk.sh"
                  for row in reachability_rows), failures)
    check("Claude Desktop desk installer has a live room-bridge activation door",
          not any(row.get("lane") == "installer" and row.get("entry") == "bin/install-claude-desktop-desk.sh"
                  for row in reachability_rows), failures)

    # Exercise the actual zsh/sed renderer, not a Python reimplementation.
    # A valid spaced HOME must produce a parseable plist and never touch the
    # user's LaunchAgents directory.
    with tempfile.TemporaryDirectory(prefix="room bridge ") as valid_home:
        rendered_run = run_installer(valid_home, "--render-only")
        process_check("actual zsh/sed render succeeds for spaced HOME",
                      rendered_run, lambda code: code == 0, failures)
        try:
            actual = plistlib.loads(rendered_run.stdout.encode("utf-8"))
            actual_ok = True
        except Exception as exc:  # pragma: no cover - named below
            actual = {}
            actual_ok = False
            print(f"  detail actual render parse: {type(exc).__name__}: {exc}")
            print(f"  detail actual render output: {(rendered_run.stdout or '')[:240]!r}")
        check("actual render-only output parses", actual_ok, failures)
        actual_path = actual.get("EnvironmentVariables", {}).get("PATH", "")
        check("actual render carries spaced HOME local bin",
              actual_path.startswith(f"{valid_home}/.local/bin:"), failures)
        check("render-only does not create LaunchAgents",
              not (Path(valid_home) / "Library" / "LaunchAgents").exists(), failures)

        # Force the portable Python validator even on macOS, where plutil is
        # present, so hosted Linux and local runs exercise the same seam.
        python_run = run_installer(valid_home, "--render-only", validator="python")
        process_check("forced Python plist fallback succeeds",
                      python_run, lambda code: code == 0, failures)
        try:
            plistlib.loads(python_run.stdout.encode("utf-8"))
            python_output_ok = True
        except Exception:
            python_output_ok = False
            print(f"  detail Python fallback output: {(python_run.stdout or '')[:240]!r}")
        check("forced Python fallback emits valid plist only", python_output_ok, failures)

        # An explicitly empty override is not the same as an unset variable:
        # only the named auto/python choices are accepted, so configuration
        # corruption fails closed before the destination can be created.
        empty_run = run_installer(valid_home, validator="")
        process_check("empty validator override fails closed",
                      empty_run, lambda code: code != 0, failures)
        check("empty validator override emits no plist", empty_run.stdout == "", failures)
        check("empty validator override names validation refusal",
              "failed validation; refusing installation" in empty_run.stderr, failures)
        check("empty validator override leaves LaunchAgents untouched",
              not (Path(valid_home) / "Library" / "LaunchAgents").exists(), failures)

    # An ampersand is legal in a filesystem path but needs XML escaping before
    # it can become a plist string. The current smallest safe behavior is an
    # explicit validation refusal before mkdir/install/launchctl. Include the
    # other sed-sensitive characters in the same fixture so they cannot be
    # silently dropped or interpreted by the renderer.
    bad_home = tempfile.mkdtemp(prefix="room bridge &| \\")
    try:
        refused = run_installer(bad_home, validator="python")
        process_check("metacharacter HOME fails closed before install",
                      refused, lambda code: code != 0, failures)
        check("metacharacter refusal names validation",
              "failed validation; refusing installation" in refused.stderr, failures)
        check("metacharacter refusal leaves LaunchAgents untouched",
              not (Path(bad_home) / "Library" / "LaunchAgents").exists(), failures)
    finally:
        # The installer must not create anything beneath this fixture on the
        # refusal path. The temporary root itself is disposable test state.
        try:
            Path(bad_home).rmdir()
        except OSError:
            pass

    print(f"room-bridge launchd selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
