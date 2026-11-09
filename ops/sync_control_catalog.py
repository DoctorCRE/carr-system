"""Compile the enforcement control catalog from the repository's own map.

WHY THIS EXISTS. `approve-rule` refuses unless every control a rule names is
registered in ops.enforcement_control_catalog and verified. Registration was a
hand-written production migration per control. So on 2026-08-22 the repository's
enforcement map described 59 controls and the database catalog held THREE —
and approving any rule enforced by the other 56 meant writing and shipping a
migration first. That is the red tape: not the requirement that a rule be
enforced before it binds, which is right and stays, but the ceremony of
restating in SQL something the repository already declares.

WHAT IS AND IS NOT RELAXED. The trust boundary is unchanged. A control could
only ever be registered by a merged, reviewed repository change; a migration was
one such change and the map is another. What is removed is the second
transcription, which could drift from the first and did.

VERIFIED MEANS SOMETHING HERE. The catalog's own constraint refuses an installed
row with no verified_at, so this refuses to mark a control installed unless:

  * every implementation path it names exists and is tracked by git, and
  * every test path it names exists and is tracked by git, and
  * at least one of those tests is one CI ACTUALLY RUNS — ops/*-selftest.py,
    which ops/ci.sh's gates class executes by glob on every push with zero
    quarantined entries, or a node test under mcp-server/test/.

That last condition is the one that matters. A control whose test nothing runs
is a control with no enforcement point, and rule ab814a26 is explicit that
recitation is not enforcement. Such a control is still written to the catalog —
hiding it would be worse — but as installed=false with no verified_at, so
approve-rule refuses it and says why.

DETERMINISTIC BY CONSTRUCTION. No model, no judgment, no network: a pure
function of three files on disk plus `git ls-files`. It is the compiled-check
shape the control-plane architecture contract asks for.
"""

from __future__ import annotations

import hashlib
import re
import json
import pathlib
import subprocess
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
UNIT_PATTERN = re.compile(json.loads((REPO / "mcp-server/suite-inventory.json").read_text())["unitPattern"])
MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"

# THE CLASS LIVES BESIDE THE MAP, NOT INSIDE IT, and that is deliberate.
# audits/guidance-situation-curation-review.v1.json pins rule-enforcement-map.json
# by sha256 as the base inventory of a curation review still in review_state
# 'proposed'. Adding a field to the map would invalidate a pending human review,
# and updating the recorded digest to match would assert a review that never
# happened. This file also carries the two controls that have been live in the
# catalog since migration 0194 and were declared in no inventory at all until the
# parity gate caught them. Fold both back into the map once that review settles.
CLASSES = REPO / "ops" / "config" / "control-enforcement-classes.v1.json"

VALID_CLASSES = frozenset({
    "deny_gate", "stop_gate", "schema", "surfacing",
    "transactional_schema", "judgment_ambient",
})


class CatalogError(ValueError):
    pass


def tracked_files() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, text=True, capture_output=True, check=True
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


# The map writes references in five shapes. Four name something real in the
# repository; `external:` is a deliberate admission that no repository artifact
# backs the claim, and it is treated as exactly that rather than quietly passed.
def _resolve(ref: str, tracked: set[str]) -> tuple[str, str | None]:
    """Return (kind, concrete_path_or_None). kind is one of
    path | glob | command | external | missing."""
    if ref.startswith("external:"):
        return ("external", None)
    if ref.startswith("glob:"):
        pattern = ref.split(":", 1)[1].strip()
        hit = any(pathlib.PurePosixPath(t).match(pattern) for t in tracked)
        return ("glob", pattern) if hit else ("missing", pattern)
    if ref.startswith("command:"):
        # The first token that looks like a repo path is the artifact to check;
        # an interpreter name and its flags are not files this repo owns.
        body = ref.split(":", 1)[1].strip()
        for token in body.split():
            if "/" in token and not token.startswith("-"):
                return ("command", token) if token in tracked else ("missing", token)
        return ("missing", body)
    path = ref.split(":", 1)[1].strip() if ref.startswith("path:") else ref.strip()
    return ("path", path) if path in tracked else ("missing", path)


def _runs_in_ci(kind: str, path: str | None) -> bool:
    """Does anything actually execute this test?

    ops/ci.sh's gates class globs ops/*-selftest.py and tools/test-*.py; its unit
    class runs the node suites under mcp-server/test/. A path outside those is a
    file someone has to remember to run, which is the same as nobody running it.

    A `command:` reference is a real runner someone can invoke, but nothing in
    the check pipeline calls it on a schedule, so it does not count as an
    enforcement point on its own — same standard applied to everything else.
    """
    if kind not in ("path", "glob") or not path:
        return False
    p = pathlib.PurePosixPath(path)
    if p.match("ops/*-selftest.py") or p.match("tools/test-*.py"):
        return True
    prefix = "mcp-server/test/"
    return path.startswith(prefix) and UNIT_PATTERN.fullmatch(path[len(prefix):]) is not None


def load_map(path: pathlib.Path = MAP,
             classes_path: pathlib.Path = CLASSES) -> dict[str, Any]:
    """The declared controls, each carrying the enforcement_class the table needs."""
    data = json.loads(path.read_text(encoding="utf-8"))
    catalog = data.get("control_catalog")
    if not isinstance(catalog, dict) or not catalog:
        raise CatalogError("rule-enforcement-map.json carries no control_catalog")

    side = json.loads(classes_path.read_text(encoding="utf-8"))
    classes = side.get("control_enforcement_class") or {}
    extras = {k: v for k, v in (side.get("controls_absent_from_the_map") or {}).items()
              if not k.startswith("_")}

    merged: dict[str, Any] = {}
    for key, entry in catalog.items():
        if key not in classes:
            raise CatalogError(
                f"control {key} is declared in the map with no enforcement_class in "
                f"{classes_path.name}; every control needs one before it can be registered")
        merged[key] = {**entry, "enforcement_class": classes[key]}
    for key, entry in extras.items():
        if key in merged:
            raise CatalogError(
                f"control {key} is declared twice — in the map and as an extra; "
                "remove the extra now that the map carries it")
        merged[key] = entry

    orphans = sorted(set(classes) - set(catalog))
    if orphans:
        raise CatalogError(
            f"{classes_path.name} assigns a class to control(s) the map does not declare: "
            f"{', '.join(orphans)}. Remove them, or declare them under "
            "controls_absent_from_the_map with their implementation and test.")
    return merged


def compile_rows(catalog: dict[str, Any], tracked: set[str]) -> list[dict[str, Any]]:
    """One row per declared control, with the reason it is or is not installed."""
    rows = []
    for key in sorted(catalog):
        entry = catalog[key]
        impl = [str(x) for x in (entry.get("implementation") or [])]
        test = [str(x) for x in (entry.get("test") or [])]
        klass = entry.get("enforcement_class")

        if not impl or not test:
            raise CatalogError(f"control {key} declares no implementation or no test")
        if klass not in VALID_CLASSES:
            raise CatalogError(
                f"control {key} has enforcement_class {klass!r}; "
                f"expected one of {sorted(VALID_CLASSES)}"
            )

        resolved = {ref: _resolve(ref, tracked) for ref in impl + test}
        missing = [ref for ref, (kind, _) in resolved.items() if kind == "missing"]
        # An implementation that is only ever described in prose is not an
        # implementation; a control needs at least one real file behind it.
        concrete_impl = [r for r in impl if resolved[r][0] in ("path", "glob", "command")]
        runnable = []
        for ref in test:
            kind, path = resolved[ref]
            if kind not in ("path", "glob") or path is None:
                continue
            candidates = (candidate for candidate in tracked
                          if pathlib.PurePosixPath(candidate).match(path)) if kind == "glob" else [path]
            if any(_runs_in_ci("path", candidate) for candidate in candidates):
                runnable.append(ref)

        if missing:
            reason = "declared but not tracked by git: " + ", ".join(sorted(missing))
        elif not concrete_impl:
            reason = "no implementation file in this repository — only external references"
        elif not runnable:
            reason = ("no test that CI runs — none of "
                      + ", ".join(test)
                      + " is an ops/*-selftest.py, tools/test-*.py or mcp-server/test/ suite, "
                        "so nothing executes this control's proof on a push")
        else:
            reason = ""

        rows.append({
            "control_key": key,
            "implementation_ref": "; ".join(impl),
            "test_ref": "; ".join(test),
            "enforcement_class": klass,
            "installed": not reason,
            "not_installed_reason": reason,
        })
    return rows


def compile_catalog(path: pathlib.Path = MAP) -> list[dict[str, Any]]:
    return compile_rows(load_map(path), tracked_files())


# %s, not $1. psycopg3 binds by %s and this module always used the numbered form,
# so --apply raised "the query has 0 placeholders but 5 parameters were passed" on
# its first statement and had never once written a row — while
# ops/control-catalog-parity-gate.py names this exact command as the remedy for the
# drift it reports. The advertised fix was broken for as long as it existed.
UPSERT = """
insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at, updated_at)
values (%s, %s, %s, %s, %s, case when %s then now() else null end, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now()
where (enforcement_control_catalog.implementation_ref,
       enforcement_control_catalog.test_ref,
       enforcement_control_catalog.enforcement_class,
       enforcement_control_catalog.installed) is distinct from
      (excluded.implementation_ref, excluded.test_ref,
       excluded.enforcement_class, excluded.installed)
"""


def sql_literal(value: str) -> str:
    """Render one PostgreSQL text literal without consulting a database."""
    return "'" + value.replace("'", "''") + "'"


def render_control_upsert_sql(row: dict[str, Any]) -> str:
    """Render the same compiled control row used by --apply as migration SQL."""
    if not row["installed"]:
        raise CatalogError(
            f"cannot render held control {row['control_key']}: {row['not_installed_reason']}"
        )
    values = ", ".join(
        [
            sql_literal(row["control_key"]),
            sql_literal(row["implementation_ref"]),
            sql_literal(row["test_ref"]),
            sql_literal(row["enforcement_class"]),
            "true",
        ]
    )
    return f"""-- GENERATED by ops/sync_control_catalog.py; never hand-edit.
insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at, updated_at)
values ({values}, now(), now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();
"""


def install_projection(conn: Any, revision: str) -> dict[str, Any]:
    """Install one clean checked-out revision atomically; verification never calls this."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, check=True,
                              capture_output=True, text=True).stdout.strip()

    def check_revision() -> None:
        if revision != git("rev-parse", "HEAD"):
            raise CatalogError("catalog revision differs from checked-out HEAD")
        if git("status", "--porcelain", "--untracked-files=no"):
            raise CatalogError("catalog installation requires a clean revision")

    check_revision()
    rows = compile_catalog()
    missing = [r["control_key"] for r in rows
               if r["not_installed_reason"].startswith("declared but not tracked")]
    if missing:
        raise CatalogError("unresolved catalog bindings: " + ", ".join(missing))
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    check_revision()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("set local lock_timeout = '5s'")
            cur.execute("set local statement_timeout = '60s'")
            cur.execute("lock table ops.enforcement_control_catalog in share row exclusive mode")
            cur.execute("select control_key from ops.enforcement_control_catalog")
            stranded = {r[0] for r in cur.fetchall()} - {r["control_key"] for r in rows}
            if stranded:
                raise CatalogError("undeclared controls require explicit retirement: "
                                   + ", ".join(sorted(stranded)))
            for row in rows:
                cur.execute(UPSERT, (row["control_key"], row["implementation_ref"],
                            row["test_ref"], row["enforcement_class"],
                            row["installed"], row["installed"]))
    return {"version": 1, "revision": revision, "sha256": digest, "controls": len(rows)}


def main() -> int:
    import argparse
    import os
    import sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the compiled rows to the database named by DATABASE_URL")
    ap.add_argument("--render-control",
                    help="print deterministic migration SQL for one installed control")
    args = ap.parse_args()

    try:
        rows = compile_catalog()
    except CatalogError as exc:
        print(f"sync-control-catalog: FAIL — {exc}", file=sys.stderr)
        return 1

    if args.render_control:
        selected = next((row for row in rows if row["control_key"] == args.render_control), None)
        if selected is None:
            print(f"sync-control-catalog: unknown control {args.render_control}", file=sys.stderr)
            return 1
        print(render_control_upsert_sql(selected), end="")
        return 0

    installed = [r for r in rows if r["installed"]]
    held = [r for r in rows if not r["installed"]]
    print(f"control catalog compiled from {MAP.relative_to(REPO)}: "
          f"{len(rows)} declared, {len(installed)} verifiable, {len(held)} held back")
    for r in held:
        print(f"  HELD  {r['control_key']}: {r['not_installed_reason']}")

    if not args.apply:
        print("\n(dry run — pass --apply with DATABASE_URL set to write)")
        return 0

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("sync-control-catalog: --apply needs DATABASE_URL", file=sys.stderr)
        return 1

    import psycopg

    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                              text=True, capture_output=True, check=True).stdout.strip()
    with psycopg.connect(dsn) as conn:
        receipt = install_projection(conn, revision)
    print("control-catalog-projection: " + json.dumps(receipt, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
