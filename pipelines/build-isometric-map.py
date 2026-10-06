#!/usr/bin/env python3
"""
build-isometric-map.py — the CARR system as a runtime topology you can look at.

WHAT THIS IS
  A single self-contained HTML page: an isometric map of ~/carr-system where each
  MODULE is a building on a grid, each named FLOW is a traced path across those
  buildings, and animated payloads travel the paths. Every module and every flow
  cites the real files it is made of, and the citations are CHECKED AT BUILD TIME.

WHERE IT CAME FROM (provenance, 2026-08-14)
  Joe handed over an X post by @JayScambler quoting @fleetingbits. The outer post
  was a prompt, verbatim:

      "Analyze [repo] at latest main. Create an isometric system map with legend
       and explainer panel. Show infrastructure as varied 3D buildings on a grid,
       with dependencies and payloads tracing real control/data paths. Cite files."

  The quoted post named the actual purpose: turning a codebase into a visual so
  you can DISCUSS it with an assistant more easily. That purpose, not the
  prettiness, is what this file is built for.

WHY IT IS NOT `run.sh graph-system`
  `pipelines/build-system-graph.py` already renders the vault's folder and
  document relationships for Obsidian. That is a useful document graph, but it
  is not a runtime topology: it has no transport, authority, write envelope,
  record, or derived-surface path. This map deliberately stays at that runtime
  altitude, with a small number of named buildings and traced flows rather than
  re-deriving the document graph.

THE HONEST SPLIT — read this before trusting anything on the page
  DERIVED (recomputed every build, cannot go stale silently):
    · the file counts on every module, from real globs over the working tree
    · the existence of every cited file — a missing one renders as a red MISSING
      chip on the page instead of quietly reading as fine
    · the commit the map was built at
  CURATED (written down by a human/session, and therefore CAN go stale):
    · which modules exist and what they mean
    · the four flows and the order of their steps
  This split is printed on the page itself, in the HOW IT'S BUILT tab. A map that
  hides which half is which is worse than no map (rule d5dcfe26 — a dated
  artifact is not self-updating; rule b01edd26 — no hardcoded count a later edit
  can falsify).

PLACEMENT (rule c1547ed1)
  Code lives here, in the repo, versioned. Output is derived and goes to out/ by
  default. Nothing is written into the vault by this script.

USAGE
  python3 pipelines/build-isometric-map.py [--out PATH]
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# THE TOPOLOGY — the curated half. Grid coords are (col, row) on the iso plane.
#
#   glob      : how the module's file count is DERIVED. Never a hardcoded number.
#   shape     : block | stack | prisms. Drives the 3-D form, and the mapping is
#               stated in the legend so the shape is readable, not decorative.
#               block  = one coherent surface        (a single dispatcher)
#               stack  = an accumulator, layered     (a store, a render engine)
#               prisms = many small independent units (a directory of gates or jobs)
#   cites     : real repo-relative paths. CHECKED at build time.
# ─────────────────────────────────────────────────────────────────────────────

GROUPS: list[tuple[str, list[str]]] = [
    ("EDGE AND ENTRY", ["HK", "WS", "RS"]),
    ("AUTHORITY AND RECORD", ["MC", "AU", "RG", "DB", "RU"]),
    ("RUNTIME AND SURFACES", ["CT", "PX", "EX"]),
]

MODULES: dict[str, dict[str, Any]] = {
    "HK": dict(name="Hooks & gates", grid=(0, 0.6), foot=(1.15, 1.15), shape="prisms",
        glob=["hooks/*.py"],
        blurb="The harness edge: session, tool-use and stop hooks can inject context, deny unsafe actions, and record evidence before the model reaches a handler.",
        cites=["hooks/session-brief.py", "hooks/map-architecture-gate.py", "hooks/gate-integrity.py", "hooks/gate-edit-gate.py", "hooks/record-home-gate.py"],
        note="The directory includes helpers as well as blocking gates; registration and enforcement are defined by tracked hook configuration, not by filename alone."),
    "WS": dict(name="Workspace contracts", grid=(2.3, 0.2), foot=(1.1, 1.1), shape="block",
        glob=["workspace/contracts/*.json", "workspace/public/*"],
        blurb="The typed workspace surface: versioned contracts and a read-only prototype server keep client-facing structures explicit and portable.",
        cites=["workspace/contracts/market-map-route-planning.v1.json", "workspace/contracts/state-machines.v1.json", "workspace/public/server.mjs", "workspace/public/index.html"],
        note="These are contracts and prototypes, not canonical record storage or production authority."),
    "RS": dict(name="run.sh", grid=(0, 3.6), foot=(1.0, 1.0), shape="block",
        glob=["run.sh"],
        blurb="The operator front door for repository jobs and checks that are not MCP verb calls.",
        cites=["run.sh", "bin/refresh-rules.sh", "bin/worktree.sh", "bin/nightly.sh"],
        note="The case block and usage output are the executable surface; prose summaries are not the command registry."),
    "MC": dict(name="MCP transport", grid=(4.2, 0.3), foot=(1.2, 1.2), shape="block",
        glob=["mcp-server/src/mcp.js", "mcp-server/src/index.js"],
        blurb="The streamable JSON-RPC edge. Verified identity arrives from the OAuth provider; dispatch selects a profile and invokes the registered tool surface.",
        cites=["mcp-server/src/mcp.js", "mcp-server/src/index.js"],
        note="Transport is not the authority boundary by itself; actor identity and profile policy are explicit downstream stages."),
    "AU": dict(name="Identity & authority", grid=(5.8, 1.2), foot=(1.15, 1.15), shape="prisms",
        glob=["mcp-server/src/identity.js", "mcp-server/src/partner-authority.js", "mcp-server/src/agent-profiles.js"],
        blurb="Resolves tenant, personal scope, partner authority and capability profile from the authenticated actor. Caller-supplied authority selectors are rejected.",
        cites=["mcp-server/src/identity.js", "mcp-server/src/partner-authority.js", "mcp-server/src/agent-profiles.js", "mcp-server/src/mcp.js"],
        note="Profiles reduce blast radius; they do not replace the authenticated grant or human-only checks."),
    "RG": dict(name="Tool registry", grid=(4.0, 2.8), foot=(1.3, 1.3), shape="stack",
        glob=["mcp-server/src/tools.js", "mcp-server/src/*.js"],
        blurb="The registry binds verb names to schemas and handlers, then composes doctrine, investigation, work-shape, room, memory and engineering tool packs into one surface.",
        cites=["mcp-server/src/tools.js", "mcp-server/src/doctrine.js", "mcp-server/src/situation-retrieval.js", "mcp-server/src/engineering-runtime.js"],
        note="The broad module count is source files only; dependencies and build output are excluded by the glob."),
    "DB": dict(name="Record layer", grid=(6.2, 4.0), foot=(1.4, 1.4), shape="stack",
        glob=["migrations/*.sql"],
        blurb="Postgres is the canonical source for parties, deals, loops, events, doctrine and operational ledgers. Writes arrive through verbs and their shared envelope.",
        cites=["migrations/0001_init.sql", "db/schema.sql", "lib/record_sources.py"],
        note="Height tracks the live migration count; it is intentionally compressed so the map remains readable."),
    "RU": dict(name="Doctrine & guidance", grid=(2.5, 5.2), foot=(1.2, 1.2), shape="stack",
        glob=["mcp-server/src/doctrine.js", "lib/guidance_registry.py"],
        blurb="The rule and guidance path: read doctrine from the store, validate typed guidance, and project scoped delivery without treating a markdown render as authority.",
        cites=["mcp-server/src/doctrine.js", "lib/guidance_registry.py", "hooks/session-brief.py"],
        note="The store is database-backed; the file count describes implementation code, not rows."),
    "CT": dict(name="Control plane", grid=(7.5, 2.5), foot=(1.15, 1.15), shape="block",
        glob=["lib/control_plane*.py", "mcp-server/src/engineering-runtime.js"],
        blurb="Deterministic scheduling, budgets, proposal validation and the Engineering Passport admission seam keep runtime work bounded and auditable.",
        cites=["lib/control_plane.py", "lib/control_plane_runner.py", "mcp-server/src/engineering-runtime.js"],
        note="Provider adapters propose; the control plane owns validation and state boundaries."),
    "PX": dict(name="Pipelines & generators", grid=(2.1, 7.4), foot=(1.3, 1.3), shape="prisms",
        glob=["pipelines/*.py", "generators/*.py"],
        blurb="Batch consumers read canonical records and build derived boards, briefs, feeds and queues; they do not become a second source of truth.",
        cites=["pipelines/review_queue.py", "pipelines/brief_pack.py", "generators/build-deal-room.py", "lib/record_sources.py"],
        note="Counts exclude nested radar jobs; this building is the top-level batch surface shown at map altitude."),
    "EX": dict(name="Exporters", grid=(5.4, 6.3), foot=(1.1, 1.1), shape="stack",
        glob=["exporters/*.py"],
        blurb="Validated derived-output writers select targets, build temporary artifacts and publish only through the configured export boundary.",
        cites=["exporters/run_exports.py", "exporters/targets.py", "exporters/common.py"],
        note="The exporter is a projection; the database remains canonical."),
}

# ─────────────────────────────────────────────────────────────────────────────
# THE FLOWS — traced control paths. `steps` are module ids in execution order.
# `kind` per leg: flow (solid, normal control) · retry (dashed, refusal or
# re-entry) · feedback (solid, closes a loop back upstream).
# ─────────────────────────────────────────────────────────────────────────────

FLOWS: list[dict[str, Any]] = [
    dict(
        id="boot", name="Session boot", view="boot",
        tagline="Hook fires → authenticated dispatch → doctrine read → binding brief.",
        body="A session-start hook directs the session to standing-context. The MCP transport dispatches only an authenticated actor; identity and profile policy are resolved before the tool registry calls the doctrine surface, which reads the canonical store and returns the session brief.",
        steps=[("HK", "flow"), ("MC", "flow"), ("AU", "flow"), ("RG", "flow"),
               ("RU", "flow"), ("DB", "flow")],
        back=[("DB", "HK", "feedback")],
        sources=["hooks/session-brief.py", "mcp-server/src/mcp.js",
                 "mcp-server/src/identity.js", "mcp-server/src/doctrine.js"],
        payload="standing-context response — JSON object assembled from store queries",
        caveat="The hook directs the call; the map does not claim that every session complies or recites the returned brief.",
    ),
    dict(
        id="write", name="Record write", view="write",
        tagline="Verb call → authority/profile checks → shared envelope → record and event.",
        body="A write request enters through the transport, resolves actor scope and capability profile, then reaches the composed registry. The shared envelope canonicalizes arguments, replays identical idempotency keys, and commits the record plus its event together.",
        steps=[("MC", "flow"), ("AU", "flow"), ("RG", "flow"), ("DB", "flow")],
        back=[("RG", "MC", "retry")],
        sources=["mcp-server/src/mcp.js", "mcp-server/src/identity.js",
                 "mcp-server/src/tools.js", "migrations/0001_init.sql"],
        payload="tool_call envelope → record row + event row",
        caveat="An ok:true result proves parsing and acceptance by the verb, not that a human reviewed every value.",
    ),
    dict(
        id="control", name="Bounded runtime job", view="control",
        tagline="Operator or schedule → pipeline → deterministic control plane → ledger.",
        body="An operator or scheduled entrypoint launches a pipeline. The control-plane kernel selects due work, validates proposals and budgets, and keeps provider adapters from becoming a second authority before recording the outcome.",
        steps=[("RS", "flow"), ("PX", "flow"), ("CT", "flow"), ("DB", "flow")],
        back=[("CT", "DB", "retry")],
        sources=["run.sh", "lib/control_plane.py", "lib/control_plane_runner.py",
                 "migrations/0149_control_plane_jobs.sql"],
        payload="validated proposal envelope → job ledger row",
        caveat="Provider adapters propose; the control plane owns validation and state transitions. The map does not imply a specific installed scheduler.",
    ),
    dict(
        id="render", name="Derived render", view="render",
        tagline="Job fires → canonical read → pipeline → validated exporter.",
        body="A job or operator runs a pipeline, which reads canonical records through the shared record-source path and builds a derived board, brief, feed or document. The exporter validates a temporary artifact before publishing it to its configured target.",
        steps=[("RS", "flow"), ("PX", "flow"), ("DB", "flow"), ("PX", "feedback"),
               ("EX", "flow")],
        back=[],
        sources=["run.sh", "pipelines/review_queue.py", "lib/record_sources.py",
                 "exporters/run_exports.py"],
        payload="projected rows → temporary artifact → published render",
        caveat="The output is a projection; rerunning the pipeline may replace it. The database remains the canonical source.",
    ),
]

HONESTY: list[str] = [
    "The topology block is curated: it names the runtime buildings and steps. Globs, cited-file existence, commit and dirty state are derived on every build.",
    "The hook directory contains both helpers and blocking gates. Registration is a separate configuration fact, so a filename alone is not evidence that a hook runs.",
    "The MCP transport, identity layer and tool registry are separate buildings even though they are shipped in one Worker bundle; this keeps authority and dispatch visible.",
    "Doctrine and guidance are store-backed. A markdown export or a prototype contract is a projection and must not be read as canonical state.",
    "The control-plane runtime validates schedules and proposals, but a provider adapter or local script is not thereby a production scheduler.",
    "This is a runtime map, not the vault document graph emitted by pipelines/build-system-graph.py. Its altitude is intentionally small enough to discuss with an assistant.",
]
# ─────────────────────────────────────────────────────────────────────────────
# Isometric geometry
# ─────────────────────────────────────────────────────────────────────────────

TILE_W, TILE_H = 128.0, 74.0
ORIGIN_X, ORIGIN_Y = 660.0, 74.0
FLOOR = 18.0          # px of building height per "storey" unit
GRID_N = 9

# Height is deliberately COMPRESSED. A migration-heavy module drawn linearly
# becomes a tower that occludes
# everything behind it and the map stops being readable. Log-shaped with a
# shallow coefficient keeps the ordering honest and the scene legible.
HEIGHT_COEF = 0.45


def iso(gx: float, gy: float) -> tuple[float, float]:
    """Grid coords to screen coords. Classic 2:1 isometric."""
    return (ORIGIN_X + (gx - gy) * TILE_W / 2.0,
            ORIGIN_Y + (gx + gy) * TILE_H / 2.0)


def height_units(n_files: int) -> float:
    """Storeys from file count, log-shaped so a large module does not tower
    twenty times over a 3-file one. Floor of 1.1 so nothing is flat."""
    if n_files <= 0:
        return 1.1
    import math
    return 1.1 + math.log(1 + n_files, 2) * HEIGHT_COEF


def count_files(globs: list[str]) -> int:
    seen: set[Path] = set()
    for pat in globs:
        for p in REPO.glob(pat):
            if p.is_file() and "__pycache__" not in p.parts:
                seen.add(p)
    return len(seen)


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), *args],
                              capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Build the model
# ─────────────────────────────────────────────────────────────────────────────

def build_model() -> dict[str, Any]:
    mods: dict[str, Any] = {}
    missing_total = 0
    for mid, spec in MODULES.items():
        n = count_files(spec["glob"]) if spec["glob"] else 0
        cites: list[dict[str, Any]] = []
        for c in spec["cites"]:
            ok = (REPO / c).exists()
            if not ok:
                missing_total += 1
            cites.append({"path": c, "ok": ok})
        gx, gy = spec["grid"]
        fw, fh = spec["foot"]
        h = height_units(n) * FLOOR if n else 1.35 * FLOOR
        cx, cy = iso(gx + fw / 2.0, gy + fh / 2.0)
        mods[mid] = {
            "id": mid, "name": spec["name"], "grid": [gx, gy], "foot": [fw, fh],
            "shape": spec["shape"], "files": n, "blurb": spec["blurb"],
            "note": spec.get("note", ""), "cites": cites,
            "h": round(h, 1), "cx": round(cx, 1), "cy": round(cy, 1),
        }

    flows: list[dict[str, Any]] = []
    for f in FLOWS:
        legs: list[dict[str, Any]] = []
        seq = f["steps"]
        for i in range(len(seq) - 1):
            a, _ = seq[i]
            b, kind = seq[i + 1]
            legs.append({"a": a, "b": b, "kind": kind})
        for (a, b, kind) in f["back"]:
            legs.append({"a": a, "b": b, "kind": kind})
        srcs = [{"path": s, "ok": (REPO / s).exists()} for s in f["sources"]]
        missing_total += sum(1 for s in srcs if not s["ok"])
        flows.append({**{k: f[k] for k in
                         ("id", "name", "tagline", "body", "payload", "caveat")},
                      "legs": legs,
                      "nodes": [s[0] for s in seq],
                      "sources": srcs})

    return {
        "modules": mods,
        "flows": flows,
        "groups": GROUPS,
        "honesty": HONESTY,
        "missing": missing_total,
        "commit": (git("rev-parse", "--short", "HEAD") or "unknown").upper(),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "dirty": bool(git("status", "--porcelain")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SVG
# ─────────────────────────────────────────────────────────────────────────────

def svg_grid() -> str:
    out = []
    for i in range(GRID_N + 1):
        x1, y1 = iso(i, 0); x2, y2 = iso(i, GRID_N)
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                   f'y2="{y2:.1f}" class="g"/>')
        x1, y1 = iso(0, i); x2, y2 = iso(GRID_N, i)
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                   f'y2="{y2:.1f}" class="g"/>')
    return "".join(out)


def _prism(cx: float, cy: float, w: float, h: float) -> str:
    """One narrow diamond-topped spike, for the `prisms` form."""
    return (f'<polygon class="f-l" points="{cx-w:.1f},{cy-h*0.55:.1f} '
            f'{cx:.1f},{cy-h:.1f} {cx:.1f},{cy-h*0.16:.1f} '
            f'{cx:.1f},{cy:.1f} {cx-w:.1f},{cy-h*0.30:.1f}"/>'
            f'<polygon class="f-r" points="{cx+w:.1f},{cy-h*0.55:.1f} '
            f'{cx:.1f},{cy-h:.1f} {cx:.1f},{cy:.1f} '
            f'{cx+w:.1f},{cy-h*0.30:.1f}"/>')


def svg_building(m: dict[str, Any]) -> str:
    gx, gy = m["grid"]; fw, fh = m["foot"]; h = m["h"]
    n = iso(gx, gy); e = iso(gx + fw, gy)
    s = iso(gx + fw, gy + fh); w = iso(gx, gy + fh)
    parts = [f'<g class="bld" data-id="{m["id"]}">']

    # ground shadow, so a building reads as sitting on the plane
    parts.append(f'<polygon class="shadow" points="{n[0]:.1f},{n[1]:.1f} '
                 f'{e[0]:.1f},{e[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                 f'{w[0]:.1f},{w[1]:.1f}"/>')

    if m["shape"] == "prisms":
        # many small independent units — a cluster, not one mass
        cols = [(-0.62, -0.30), (-0.20, 0.10), (0.24, -0.16), (0.62, 0.26)]
        for k, (ox, oy) in enumerate(cols):
            px, py = iso(gx + fw / 2 + ox, gy + fh / 2 + oy)
            parts.append(_prism(px, py, 15.0, h * (0.72 + 0.14 * (k % 3))))
    else:
        top = [(p[0], p[1] - h) for p in (n, e, s, w)]
        parts.append('<polygon class="f-r" points="'
                     f'{e[0]:.1f},{e[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                     f'{top[2][0]:.1f},{top[2][1]:.1f} '
                     f'{top[1][0]:.1f},{top[1][1]:.1f}"/>')
        parts.append('<polygon class="f-l" points="'
                     f'{w[0]:.1f},{w[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                     f'{top[2][0]:.1f},{top[2][1]:.1f} '
                     f'{top[3][0]:.1f},{top[3][1]:.1f}"/>')
        if m["shape"] == "stack":
            # layered slabs — the accumulator read
            bands = max(2, min(6, int(h / FLOOR)))
            for b in range(1, bands):
                dy = h * b / bands
                parts.append(f'<line class="band" x1="{w[0]:.1f}" '
                             f'y1="{w[1]-dy:.1f}" x2="{s[0]:.1f}" '
                             f'y2="{s[1]-dy:.1f}"/>')
                parts.append(f'<line class="band" x1="{s[0]:.1f}" '
                             f'y1="{s[1]-dy:.1f}" x2="{e[0]:.1f}" '
                             f'y2="{e[1]-dy:.1f}"/>')
        parts.append('<polygon class="f-t" points="'
                     + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in top) + '"/>')

    tx, ty = iso(gx + fw / 2.0, gy + fh / 2.0)
    parts.append(f'<text class="blab" x="{tx:.1f}" y="{ty - h - 9:.1f}">'
                 f'{m["id"]}</text>')
    parts.append("</g>")
    return "".join(parts)


def elbow(a: dict[str, Any], b: dict[str, Any],
          occupied: list[tuple[float, float, float, float, str]],
          ) -> list[tuple[float, float]]:
    """L-shaped route in grid space, choosing the variant that crosses fewer
    building footprints. Paths run on the ground plane, under the buildings."""
    ax, ay = a["grid"][0] + a["foot"][0] / 2, a["grid"][1] + a["foot"][1] / 2
    bx, by = b["grid"][0] + b["foot"][0] / 2, b["grid"][1] + b["foot"][1] / 2

    def crossings(pts):
        c = 0
        for (ox, oy, ow, oh, oid) in occupied:
            if oid in (a["id"], b["id"]):
                continue
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]; x2, y2 = pts[i + 1]
                for t in range(1, 12):
                    px = x1 + (x2 - x1) * t / 12.0
                    py = y1 + (y2 - y1) * t / 12.0
                    if ox - .2 <= px <= ox + ow + .2 and oy - .2 <= py <= oy + oh + .2:
                        c += 1
                        break
        return c

    v1 = [(ax, ay), (ax, by), (bx, by)]
    v2 = [(ax, ay), (bx, ay), (bx, by)]
    pts = v1 if crossings(v1) <= crossings(v2) else v2
    return [iso(x, y) for (x, y) in pts]


def build_paths(model: dict[str, Any]) -> dict[str, Any]:
    occupied = [(m["grid"][0], m["grid"][1], m["foot"][0], m["foot"][1], m["id"])
                for m in model["modules"].values()]
    out: dict[str, Any] = {}
    for f in model["flows"]:
        legs = []
        for leg in f["legs"]:
            a = model["modules"][leg["a"]]; b = model["modules"][leg["b"]]
            pts = elbow(a, b, occupied)
            legs.append({"kind": leg["kind"], "a": leg["a"], "b": leg["b"],
                         "pts": [[round(x, 1), round(y, 1)] for x, y in pts]})
        out[f["id"]] = legs
    return out


# ─────────────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render(model: dict[str, Any], paths: dict[str, Any]) -> str:
    m = model["modules"]

    rail = []
    for title, ids in model["groups"]:
        rail.append(f'<div class="grp">{esc(title)}</div>')
        for mid in ids:
            mod = m[mid]
            cnt = mod["files"] if mod["files"] else "—"
            rail.append(
                f'<button class="mrow" data-mod="{mid}">'
                f'<span class="mcode">{mid}</span>'
                f'<span class="mname">{esc(mod["name"])}</span>'
                f'<span class="mcount">{cnt}</span></button>')

    # depth order: farther (smaller gx+gy) drawn first
    order = sorted(m.values(), key=lambda x: x["grid"][0] + x["grid"][1])
    bldgs = "".join(svg_building(b) for b in order)

    warn = ""
    if model["missing"]:
        warn = (f'<div class="warn">{model["missing"]} cited file(s) '
                f'DID NOT RESOLVE at build time — shown in red below.</div>')

    dirty = ' · <span class="dirty">UNCOMMITTED</span>' if model["dirty"] else ""

    data = json.dumps({"modules": m, "flows": model["flows"],
                       "paths": paths, "honesty": model["honesty"]},
                      separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARR System — Runtime Topology</title>
<style>
:root{{
  --bg:#0a0b0e; --panel:#0d0f13; --line:#1e232c; --grid:#232935;
  --face-t:#2b3444; --face-l:#161c27; --face-r:#1f2735; --band:#39445a;
  --ink:#eef1f6; --dim:#828c9e; --accent:#3b82f6; --accent-dim:#1d4ed8;
  --bad:#f0616d; --good:#3ddc97;
}}
*{{box-sizing:border-box}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--ink);
  font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}}
body{{display:flex;flex-direction:column;overflow:hidden}}

.top{{display:flex;border-bottom:1px solid var(--line);flex:0 0 auto;
  overflow-x:auto}}
.cell{{padding:12px 20px;border-right:1px solid var(--line);white-space:nowrap}}
.cell .k{{color:var(--dim);font-size:10px;letter-spacing:.13em}}
.cell .v{{font-size:17px;margin-top:3px}}
.cell .sub{{color:var(--dim);font-size:10.5px;margin-top:3px}}
.ctrls{{margin-left:auto;display:flex;align-items:center;gap:9px;padding:0 16px}}
select,button.btn{{background:#12161d;color:var(--ink);border:1px solid #2b323f;
  border-radius:3px;padding:7px 12px;font:inherit;font-size:11px;cursor:pointer}}
button.btn:hover,select:hover{{border-color:#41506b}}
button.btn[aria-pressed=true]{{background:var(--accent);border-color:var(--accent);
  color:#fff}}

.main{{flex:1;display:flex;min-height:0}}

.rail{{width:270px;flex:0 0 270px;border-right:1px solid var(--line);
  overflow-y:auto;padding:16px 14px}}
.rail h2{{font-size:10px;letter-spacing:.16em;color:var(--dim);margin:0 0 14px;
  font-weight:400}}
.grp{{font-size:9.5px;letter-spacing:.15em;color:var(--dim);margin:18px 0 8px}}
.mrow{{display:flex;align-items:center;gap:10px;width:100%;text-align:left;
  background:#10141a;border:1px solid #222836;border-radius:3px;padding:9px 11px;
  margin-bottom:6px;color:var(--ink);font:inherit;cursor:pointer}}
.mrow:hover{{border-color:#3d4a63}}
.mrow[aria-current=true]{{border-color:var(--accent);background:#101a2c}}
.mcode{{font-size:9.5px;color:var(--dim);width:20px}}
.mname{{flex:1;font-size:12px}}
.mcount{{font-size:11px;color:var(--dim)}}

.stage{{flex:1;display:flex;flex-direction:column;min-width:0;position:relative}}
.stagehead{{padding:16px 24px 0}}
.stagehead .k{{font-size:9.5px;letter-spacing:.16em;color:var(--dim)}}
.stagehead h1{{font-size:25px;font-weight:400;margin:6px 0 0}}
.live{{position:absolute;right:26px;top:20px;font-size:10px;letter-spacing:.12em;
  color:var(--dim);display:flex;align-items:center;gap:7px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--accent)}}
.dot.off{{background:#3a4356}}
svg{{flex:1;width:100%;min-height:0;display:block}}
.g{{stroke:var(--grid);stroke-width:.6}}
.shadow{{fill:#000;opacity:.34}}
.f-t{{fill:var(--face-t);stroke:#48566e;stroke-width:.7}}
.f-l{{fill:var(--face-l);stroke:#333d4f;stroke-width:.7}}
.f-r{{fill:var(--face-r);stroke:#333d4f;stroke-width:.7}}
.band{{stroke:var(--band);stroke-width:.8;opacity:.55}}
.blab{{fill:#fff;font-size:11.5px;text-anchor:middle;letter-spacing:.06em}}
.bld{{cursor:pointer}}
.bld.dim{{opacity:.42}}
.bld.hot .f-t{{fill:#31456a;stroke:var(--accent)}}
.bld.hot .f-l,.bld.hot .f-r{{stroke:var(--accent-dim)}}
.edge{{fill:none;stroke:var(--accent);stroke-width:1.7}}
.edge.retry{{stroke:#93a0b5;stroke-dasharray:6 5;stroke-width:1.3}}
.edge.feedback{{stroke:#7aa2f7;stroke-width:1.7}}
.pay{{fill:var(--accent)}}
.payring{{fill:none;stroke:var(--accent);stroke-width:1.4;opacity:.8}}

.legend{{display:flex;gap:26px;padding:12px 24px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11px;flex-wrap:wrap;flex:0 0 auto}}
.legend b{{color:var(--ink);font-weight:400}}
.swatch{{display:inline-block;width:22px;height:0;border-top:2px solid var(--accent);
  vertical-align:middle;margin-right:6px}}
.swatch.d{{border-top:2px dashed #93a0b5}}
.swatch.dot{{width:8px;height:8px;border:0;border-radius:50%;
  background:var(--accent)}}
.foot{{padding:9px 24px 12px;color:#5d6678;font-size:10.5px;
  border-top:1px solid var(--line);flex:0 0 auto}}

.side{{width:390px;flex:0 0 390px;border-left:1px solid var(--line);
  display:flex;flex-direction:column;min-height:0}}
.tabs{{display:flex;border-bottom:1px solid var(--line);flex:0 0 auto}}
.tab{{flex:1;padding:13px 8px;text-align:center;font-size:10.5px;
  letter-spacing:.12em;color:var(--dim);cursor:pointer;background:none;
  border:0;font-family:inherit}}
.tab[aria-selected=true]{{background:#fff;color:#000}}
.pane{{padding:20px 22px;overflow-y:auto;flex:1}}
.pane[hidden]{{display:none}}
.lbl{{font-size:9.5px;letter-spacing:.15em;color:var(--dim);margin:22px 0 8px}}
.lbl:first-child{{margin-top:0}}
.pane h3{{font-size:21px;font-weight:400;margin:0 0 8px}}
.tag{{color:#9fb0c8;margin:0 0 16px}}
.body{{color:#d3dae6;margin:0 0 4px}}
.chip{{display:inline-block;background:#12161d;border:1px solid #2b323f;
  border-radius:3px;padding:5px 10px;margin:0 6px 6px 0;font-size:11px}}
.chip.bad{{border-color:var(--bad);color:var(--bad)}}
.chip.bad::after{{content:" — MISSING"}}
.caveat{{border-left:2px solid #4a5568;padding:9px 0 9px 13px;color:#9aa6b8;
  margin-top:8px}}
.warn{{background:#2a1216;border:1px solid var(--bad);color:#ffb3b9;
  padding:10px 13px;border-radius:3px;margin-bottom:16px;font-size:11.5px}}
.dirty{{color:#f0c674}}
ul.h{{margin:0;padding-left:17px;color:#aab4c4}}
ul.h li{{margin-bottom:11px}}
.split{{display:flex;gap:8px;margin-bottom:14px}}
.split div{{flex:1;background:#10141a;border:1px solid #222836;border-radius:3px;
  padding:11px}}
.split .t{{font-size:9.5px;letter-spacing:.13em;color:var(--dim);
  margin-bottom:6px}}
.split .d{{font-size:11px;color:#c2cbd8}}
@media (max-width:1200px){{.rail{{display:none}}.side{{width:320px;flex-basis:320px}}}}
</style></head><body>

<div class="top">
  <div class="cell"><div class="k">REPOSITORY</div>
    <div class="v">jbookout/carr-system</div>
    <div class="sub">{esc(model["branch"]).upper()} · {esc(model["commit"])}{dirty}</div></div>
  <div class="cell"><div class="k">RUNTIME FLOWS</div>
    <div class="v">{len(model["flows"])}</div></div>
  <div class="cell"><div class="k">MODULES</div>
    <div class="v">{len(m)}</div></div>
  <div class="cell"><div class="k">CITED FILES</div>
    <div class="v" id="citecount">—</div>
    <div class="sub">checked at build</div></div>
  <div class="ctrls">
    <select id="flowsel"></select>
    <button class="btn" id="pause" aria-pressed="false">PAUSE FLOW</button>
    <button class="btn" id="step">TRACE ONE STEP</button>
    <button class="btn" id="reset">RESET VIEW</button>
  </div>
</div>

<div class="main">
  <div class="rail"><h2>THE SYSTEM</h2>{"".join(rail)}</div>

  <div class="stage">
    <div class="stagehead">
      <div class="k">RUNTIME TOPOLOGY</div>
      <h1 id="stagetitle">—</h1>
    </div>
    <div class="live"><span class="dot" id="livedot"></span>
      <span id="livetext">PAYLOADS IN MOTION</span></div>
    <svg id="map" viewBox="0 0 1320 780" preserveAspectRatio="xMidYMid meet">
      <g id="grid">{svg_grid()}</g>
      <g id="edges"></g>
      <g id="blds">{bldgs}</g>
      <g id="pays"></g>
    </svg>
    <div class="legend">
      <span><i class="swatch"></i><b>flow</b> control passes</span>
      <span><i class="swatch d"></i><b>retry</b> refusal or re-entry</span>
      <span><i class="swatch dot"></i><b>payload</b> in transit</span>
      <span><b>height</b> = files in module</span>
      <span><b>stack</b> = accumulator ·
            <b>cluster</b> = many small units ·
            <b>block</b> = one surface</span>
    </div>
    <div class="foot">click a module for its files · change the flow to
      retrace · pause or step the payload · every path here is a real
      control path, not an illustration</div>
  </div>

  <div class="side">
    <div class="tabs">
      <button class="tab" id="t1" aria-selected="true">WHAT IT DOES</button>
      <button class="tab" id="t2" aria-selected="false">HOW IT'S BUILT</button>
    </div>
    <div class="pane" id="p1">{warn}<div id="paneA"></div></div>
    <div class="pane" id="p2" hidden>
      <div class="lbl">WHAT IS TRUE HERE</div>
      <div class="split">
        <div><div class="t">DERIVED</div><div class="d">File counts, every
          citation's existence, and the commit. Recomputed each build, so these
          cannot go stale without the page changing.</div></div>
        <div><div class="t">CURATED</div><div class="d">Which modules exist and
          the order of steps in each flow. Written by hand, so these CAN go
          stale.</div></div>
      </div>
      <div class="body">The page is generated by
        <span class="chip">pipelines/build-isometric-map.py</span>
        Re-run it and the derived half re-derives; the curated half changes only
        when someone edits the topology block at the top of that file.</div>
      <div class="lbl">WHAT A NAIVE READER WOULD GET WRONG</div>
      <ul class="h">{"".join(f"<li>{esc(x)}</li>" for x in model["honesty"])}</ul>
      <div class="lbl">WHAT THIS IS NOT</div>
      <div class="body">Not a dependency graph. The repository's document graph
        remains a separate surface emitted by <span class="chip">pipelines/build-system-graph.py</span>.
        This map is deliberately at an altitude a person can hold in their head —
        eleven buildings and four paths.</div>
    </div>
  </div>
</div>

<script>
const D = {data};
let cur = D.flows[0].id, paused = false, t = 0, stepMode = false;

const sel = document.getElementById('flowsel');
D.flows.forEach(f => {{ const o = document.createElement('option');
  o.value = f.id; o.textContent = f.name; sel.appendChild(o); }});

document.getElementById('citecount').textContent =
  Object.values(D.modules).reduce((a,m)=>a+m.cites.length,0) +
  D.flows.reduce((a,f)=>a+f.sources.length,0);

function flow(id) {{ return D.flows.find(f => f.id === id); }}
const chips = a => a.map(c =>
  `<span class="chip${{c.ok?'':' bad'}}">${{c.path}}</span>`).join('');

function paneFlow(f) {{
  document.getElementById('paneA').innerHTML = `
    <div class="lbl">SELECTED FLOW</div>
    <h3>${{f.name}}</h3>
    <p class="tag">${{f.tagline}}</p>
    <p class="body">${{f.body}}</p>
    <div class="caveat">${{f.caveat}}</div>
    <div class="lbl">PATH</div>
    <div>${{f.nodes.map(n=>`<span class="chip">${{n}} ${{D.modules[n].name}}</span>`).join('')}}</div>
    <div class="lbl">SOURCE</div><div>${{chips(f.sources)}}</div>
    <div class="lbl">PAYLOAD</div><div><span class="chip">${{f.payload}}</span></div>`;
}}

function paneMod(id) {{
  const m = D.modules[id];
  document.getElementById('paneA').innerHTML = `
    <div class="lbl">SELECTED MODULE</div>
    <h3>${{m.name}}</h3>
    <p class="tag">${{m.files ? m.files + ' file(s) in the working tree'
      : 'no file count — lives outside the repo'}}</p>
    <p class="body">${{m.blurb}}</p>
    ${{m.note ? `<div class="caveat">${{m.note}}</div>` : ''}}
    <div class="lbl">FILES</div><div>${{chips(m.cites)}}</div>
    <div class="lbl">FORM</div>
    <div><span class="chip">${{m.shape}}</span>
      <span class="chip">height = ${{m.files||'n/a'}} files</span></div>`;
}}

function drawEdges() {{
  const legs = D.paths[cur], g = document.getElementById('edges');
  g.innerHTML = legs.map(l => {{
    const d = 'M' + l.pts.map(p=>p.join(' ')).join(' L');
    return `<path class="edge ${{l.kind}}" d="${{d}}"/>`;
  }}).join('');
  const on = new Set(flow(cur).nodes);
  document.querySelectorAll('.bld').forEach(b =>
    b.classList.toggle('dim', !on.has(b.dataset.id)));
}}

function lerp(legs, u) {{
  const total = legs.length; if (!total) return null;
  const i = Math.min(total - 1, Math.floor(u * total));
  const local = u * total - i, pts = legs[i].pts;
  const segs = pts.length - 1;
  const j = Math.min(segs - 1, Math.floor(local * segs));
  const lu = local * segs - j;
  return [pts[j][0] + (pts[j+1][0]-pts[j][0])*lu,
          pts[j][1] + (pts[j+1][1]-pts[j][1])*lu, i];
}}

function tick() {{
  if (!paused) t = (t + 0.0022) % 1;
  const legs = D.paths[cur], p = lerp(legs, t);
  const g = document.getElementById('pays');
  if (p) {{
    g.innerHTML = `<circle class="payring" cx="${{p[0]}}" cy="${{p[1]}}" r="7"/>
                   <circle class="pay" cx="${{p[0]}}" cy="${{p[1]}}" r="3.2"/>`;
    document.querySelectorAll('.bld').forEach(b =>
      b.classList.toggle('hot', b.dataset.id === legs[p[2]].b));
  }}
  requestAnimationFrame(tick);
}}

function setFlow(id) {{
  cur = id; sel.value = id; t = 0;
  document.getElementById('stagetitle').textContent = flow(id).name;
  drawEdges(); paneFlow(flow(id));
  document.querySelectorAll('.mrow').forEach(r =>
    r.setAttribute('aria-current','false'));
}}

sel.onchange = e => setFlow(e.target.value);
document.getElementById('pause').onclick = e => {{
  paused = !paused; e.target.setAttribute('aria-pressed', paused);
  document.getElementById('livedot').style.opacity = paused ? .3 : 1;
  document.getElementById('livetext').textContent =
    paused ? 'PAUSED' : 'PAYLOADS IN MOTION';
}};
document.getElementById('step').onclick = () => {{
  paused = true;
  document.getElementById('pause').setAttribute('aria-pressed', true);
  document.getElementById('livetext').textContent = 'PAUSED';
  const n = D.paths[cur].length;
  t = (Math.floor(t * n) + 1) % n / n + 0.0001;
}};
document.getElementById('reset').onclick = () => setFlow(D.flows[0].id);

document.querySelectorAll('.mrow').forEach(r => r.onclick = () => {{
  document.querySelectorAll('.mrow').forEach(x =>
    x.setAttribute('aria-current', String(x === r)));
  paneMod(r.dataset.mod); tab(1);
}});
document.querySelectorAll('.bld').forEach(b => b.onclick = () => {{
  paneMod(b.dataset.id); tab(1);
}});

function tab(n) {{
  document.getElementById('t1').setAttribute('aria-selected', n===1);
  document.getElementById('t2').setAttribute('aria-selected', n===2);
  document.getElementById('p1').hidden = n!==1;
  document.getElementById('p2').hidden = n!==2;
}}
document.getElementById('t1').onclick = () => tab(1);
document.getElementById('t2').onclick = () => tab(2);

setFlow(cur); tick();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "out" / "isometric-map.html"))
    args = ap.parse_args()

    model = build_model()
    paths = build_paths(model)
    doc = render(model, paths)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")

    print(f"wrote {out}  ({len(doc):,} bytes)")
    print(f"  commit   {model['branch']} {model['commit']}"
          f"{' DIRTY' if model['dirty'] else ''}")
    print(f"  modules  {len(model['modules'])}   flows {len(model['flows'])}")
    for mid, mod in model["modules"].items():
        print(f"    {mid}  {mod['files']:>4} files  {mod['name']}")
    if model["missing"]:
        print(f"  !! {model['missing']} cited path(s) DID NOT RESOLVE")
        for mod in model["modules"].values():
            for c in mod["cites"]:
                if not c["ok"]:
                    print(f"       MISSING {c['path']}  (module {mod['id']})")
        for f in model["flows"]:
            for c in f["sources"]:
                if not c["ok"]:
                    print(f"       MISSING {c['path']}  (flow {f['id']})")
        return 1
    print("  all cited paths resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
