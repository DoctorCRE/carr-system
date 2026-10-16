"""Refuse dispatch to a host the tailnet reports offline."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "ops"))
import host_liveness


def refusal(entry: dict, config: dict, live: dict):
    host = (host_liveness.resolve_host(entry.get("host"), config)
            if entry.get("kind") == "claude-remote" else live.get("self"))
    state = live["hosts"].get(host, {}) if host else {}
    if state.get("state") == "offline":
        name = entry.get("name") or "this desk"
        return ("seat_host_offline",
                f"dispatch refused: {name} runs on host {host}, which the tailnet reports offline "
                f"since {state.get('last_seen') or 'never seen'}. Wake {host} or use a desk on a live Mac.")
    return None


def check(entry: dict):
    try:
        config = host_liveness.load_config()
        return refusal(entry, config, host_liveness.observe(config=config))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return ("seat_gate_unreadable",
                f"dispatch refused: could not read {host_liveness.CONFIG} or connectivity "
                f"({type(exc).__name__}). Restore the host configuration and rerun.")
