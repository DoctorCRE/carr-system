#!/usr/bin/env python3
"""test-schema-snapshot-grants.py — db/schema.sql must carry the app roles'
PRIVILEGES, not just the roles.

WHY THIS EXISTS. The roles trap (test-schema-snapshot-roles.py) had a second
half. The snapshot is pg_dump --no-acl, so every GRANT from an already-applied
migration is absent from it — and ops/ci.sh's migration class builds its
throwaway database from the snapshot. The roles exist there (the preamble
creates them) but hold NOTHING: has_table_privilege() answers false for every
table and every role, and a green migration class proves nothing about grants.

Found 2026-08-14 while building the update-lead verb (PR #75): verifying that
carr_writer may insert into lead required replaying migrations 0001-0004 by
hand against an empty database, because the environment CI actually builds
could not answer the question.

The fix is a generated CARR GRANTS section in the snapshot: the app roles'
ACLs, read from production's catalogs by bin/schema-snapshot.sh and emitted as
plain GRANT statements after the structure they attach to. This test pins the
shapes that section must carry — each anchor below is a grant idiom the
migrations actually use, chosen because a naive emitter would drop it:

  * plain table grants        (0004: writer on public tables — the PR #75 case)
  * column-scoped grants      (0021 selects; 0117's update list, where the
                               ABSENCE of resolved_at is the interlock)
  * function execute grants   (0094, 0106)
  * schema usage              (0004 public, 0115 ops)
  * role membership bundles   (0006: exporter is reader-plus-a-little)

And the guard that matters as much as any anchor: the section must never widen
beyond the app roles. A grantee outside the closed set means production ACLs
for some other principal were swept into a tracked file.
"""
import hashlib
import os
import re
import sys

from schema_snapshot_grants import (
    SECTION_MARKER,
    SnapshotGrantError,
    _snapshot_function_identity,
    acl_facts,
    carr_grants_section_lines,
    compose_grants_to_role,
    grants_to_role,
    match_generated_grant,
    render_acl_facts,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SNAPSHOT = os.path.join(REPO, "db", "schema.sql")
GENERATOR = os.path.join(REPO, "bin", "schema-snapshot.sh")

# The app roles the migrations grant to. neondb_owner may appear as a membership
# grantee only (0005/0006 bundle it the roles), never as an ACL grantee: its own
# ACLs are Neon's business, not this repo's.
#
# carr_authority joined on 2026-08-19, with the snapshot's role preamble and for
# the same reason. 0161 creates it, and once a refresh carried the ledger past
# 0161 that migration stopped creating anything, so the snapshot has to carry
# both the role and its 24 grants. This list and the preamble in
# bin/schema-snapshot.sh are the same set and have to move together — leaving it
# out here reports the role's own grants as strays.
#
# carr_device_evidence joined the same day, by way of 0163 — the fourth role to
# age out of the snapshot. This list and the preamble in bin/schema-snapshot.sh
# are the same set written twice, so they have to move together: a role in one
# and not the other makes its own grants report as strays.
#
# The role preamble deliberately creates bundles before their migration runs so
# a pending migration may grant to them on a fresh cluster. Their ACLs, however,
# may appear only after the source snapshot ledger has absorbed that migration.
# Keep this mapping explicit so a production-truth pre-release snapshot does not
# pretend a pending bundle already has privileges.
ROLE_GRANT_MIGRATIONS = {
    "dot_reader": "0756_dot_reader.sql",
    "carr_calendar_prebrief_jobs": "0229_calendar_prebrief_projection.sql",
    "carr_calendar_prebrief_canary_jobs": "0229_calendar_prebrief_projection.sql",
    "carr_calendar_prebrief_attestors": "0229_calendar_prebrief_projection.sql",
    "carr_calendar_prebrief_email_resolver": "0229_calendar_prebrief_projection.sql",
    "carr_program5_forward_fix_verifiers": "0315_program5_forward_fix_rehearsal.sql",
    "carr_renewal_source_attestors": "0249_renewal_signed_source_ingress.sql",
    "carr_gate_zero_producer": "0502_gate_zero_read_only_outcome.sql",
    "carr_foundation_assurance_oracle": "0511_foundation_assurance_minimum_outcome.sql",
    "carr_ownership_issuer": "0532a_canonical_ownership_lease_activation.sql",
}
APP_ROLES = ["carr_reader", "carr_writer", "carr_jobs", "carr_exporter",
             "carr_authority", "carr_device_evidence",
             "carr_calendar_prebrief_jobs", "carr_calendar_prebrief_canary_jobs",
             "carr_calendar_prebrief_attestors", "carr_calendar_prebrief_email_resolver",
             "carr_program5_forward_fix_verifiers",
             "carr_renewal_source_attestors",
             "carr_gate_zero_producer", "carr_foundation_assurance_oracle",
             "carr_ownership_issuer", "dot_reader"]
MEMBERSHIP_ONLY = ["neondb_owner", "carr_ownership_issuer_g1",
                   "carr_ownership_issuer_g2"]

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def parse_snapshot(argv):
    if not argv:
        return DEFAULT_SNAPSHOT
    if len(argv) == 2 and argv[0] == "--snapshot":
        snapshot = argv[1]
        if not os.path.isabs(snapshot):
            raise ValueError("--snapshot must be an absolute path")
        return snapshot
    raise ValueError("usage: test-schema-snapshot-grants.py [--snapshot /absolute/candidate.sql]")


def main(argv):
    try:
        snapshot = parse_snapshot(argv)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 2
    if not os.path.exists(snapshot):
        print(f"FAIL: {snapshot} not present")
        return 1
    sql = open(snapshot).read()
    lines = sql.split("\n")

    check("the snapshot carries a CARR GRANTS section",
          "CARR GRANTS" in sql)

    # Statement lines only — the section's own comment explains itself in
    # prose that mentions grants, and the roles test already learned that
    # matching inside comments fails a correct file.
    grant_lines = [(i, ln.strip()) for i, ln in enumerate(lines)
                   if re.match(r"\s*grant\s", ln, re.I)
                   and not ln.lstrip().startswith("--")]

    canonical_section = carr_grants_section_lines(sql)
    raw_writer_grants = [ln for ln in canonical_section
                         if ln.endswith(" to carr_writer;")]
    check("the shared staging provisioner extractor equals the generated writer ACLs",
          grants_to_role(sql, "carr_writer") == raw_writer_grants
          and len(raw_writer_grants) >= 170)

    destructive = sql.replace(
        SECTION_MARKER,
        SECTION_MARKER
        + "\ngrant select on table public.actor to carr_writer; "
          "drop table public.actor; -- to carr_writer;",
        1,
    )
    try:
        grants_to_role(destructive, "carr_writer")
    except SnapshotGrantError:
        destructive_refused = True
    else:
        destructive_refused = False
    check("multi-statement/comment SQL disguised as a writer GRANT is refused",
          destructive_refused)

    # 0557's ops.meeting_mode_actor() takes only OUT arguments, and
    # pg_get_function_identity_arguments() prints them with their mode. The
    # grammar refused that line, so every snapshot refreshed past 0557 failed
    # here; Postgres ignores OUT arguments in a function's identity.
    out_only = match_generated_grant(
        "revoke all on function ops.meeting_mode_actor(OUT actor_id uuid, "
        "OUT actor_slug text, OUT is_partner boolean, OUT tenant text) from public;")
    check("an OUT-only function revoke parses to its argument-free identity",
          _snapshot_function_identity(out_only["schema"], out_only["object"],
                                       out_only["function_args"])
          == "ops.meeting_mode_actor()")

    # NAMELESS MULTI-WORD ARGUMENT TYPES (2026-09-26, PR #1297). An argument
    # identity with no name is textually "name type" when its type has a space
    # in it, and render_acl_facts() writes identities WITHOUT names. The old
    # parser read its own rendered `ops.f01_instant_text(timestamp with time
    # zone)` back as `(with time zone)`, so the login gate failed a correct
    # database the first time a refreshed snapshot carried that grant. Every
    # case below must survive render -> parse unchanged, named or not.
    multiword_cases = {
        # identity (as the catalog's oidvectortypes spells it): generated form
        "ops.f01_instant_text(timestamp with time zone)":
            "ops.f01_instant_text(p_at timestamp with time zone)",
        "ops.list_shipped_releases(timestamp with time zone)":
            "ops.list_shipped_releases(p_since timestamp with time zone)",
        "ops.mw_one(character varying)": "ops.mw_one(p_label character varying)",
        "ops.mw_one(double precision)": "ops.mw_one(p_score double precision)",
        "ops.mw_one(time without time zone)": "ops.mw_one(p_at time without time zone)",
        "ops.mw_one(bit varying[])": "ops.mw_one(p_bits bit varying[])",
        "ops.mw_many(timestamp with time zone, character varying, double precision)":
            "ops.mw_many(p_at timestamp with time zone, p_label character varying, "
            "p_score double precision)",
        "ops.mw_mixed(text, timestamp without time zone, uuid)":
            "ops.mw_mixed(p_key text, p_at timestamp without time zone, p_id uuid)",
        "ops.mw_variadic(time with time zone[])":
            "ops.mw_variadic(VARIADIC p_times time with time zone[])",
    }
    roundtrip_bad = []
    for identity, named in multiword_cases.items():
        expected = {("function", identity, "execute", False)}
        rendered = render_acl_facts(expected, "carr_reader")
        named_line = f"grant execute on function {named} to carr_reader;"
        if set(acl_facts(rendered)) != expected:
            roundtrip_bad.append(("nameless", identity, acl_facts(rendered)))
        if set(acl_facts([named_line])) != expected:
            roundtrip_bad.append(("named", named, acl_facts([named_line])))
    check("nameless and named multi-word argument types survive render -> parse",
          not roundtrip_bad, repr(roundtrip_bad[:3]))

    # The exact PR #1297 shape, end to end: a snapshot that already carries the
    # named grant composes to a plan whose facts are the catalog's identity.
    ledger_sql = "begin; commit;\n"
    composed_schema = (
        "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"
        f"0001_base.sql\t{hashlib.sha256(ledger_sql.encode()).hexdigest()}"
        "\t2026-09-26 00:00:00+00\n\\.\n"
        f"{SECTION_MARKER}\n"
        "grant execute on function ops.f01_instant_text(p_at timestamp with time zone) "
        "to carr_reader;\n"
        "grant execute on function ops.mw_many(p_at timestamp with time zone, "
        "p_label character varying) to carr_reader;\n"
        "-- PostgreSQL database dump\n"
    )
    composed_facts = set(acl_facts(compose_grants_to_role(
        composed_schema, (("0001_base.sql", ledger_sql),), "carr_reader")))
    catalog_facts = {
        ("function", "ops.f01_instant_text(timestamp with time zone)", "execute", False),
        ("function", "ops.mw_many(timestamp with time zone, character varying)",
         "execute", False),
    }
    check("a composed plan's multi-word function facts equal the catalog's",
          composed_facts == catalog_facts, repr(sorted(composed_facts)))
    # The comparison the login gate makes stays exact in both directions: a
    # catalog missing a planned grant, or holding one more, is not equal.
    dropped = ("function", "ops.f01_instant_text(timestamp with time zone)",
               "execute", False)
    planted = ("function", "ops.mw_one(double precision)", "execute", False)
    check("a missing multi-word function grant still breaks exactness",
          composed_facts - (catalog_facts - {dropped}) == {dropped})
    check("an extra multi-word function grant still breaks exactness",
          (catalog_facts | {planted}) - composed_facts == {planted})
    # The parser did not get looser: a name followed by a one-word type is still
    # read as name + type, even when the name happens to be a type word.
    check("a named argument whose type is one word keeps its name split off",
          set(acl_facts([
              "grant execute on function ops.mw_one(timestamp text) to carr_reader;"
          ])) == {("function", "ops.mw_one(text)", "execute", False)})

    applied_migrations = {
        migration for migration in ROLE_GRANT_MIGRATIONS.values()
        if re.search(rf"^{re.escape(migration)}\t", sql, re.M)
    }
    pending_grant_roles = {
        role for role, migration in ROLE_GRANT_MIGRATIONS.items()
        if migration not in applied_migrations
    }
    for role in APP_ROLES:
        if role in pending_grant_roles:
            check(f"pending role {role} has no premature grant",
                  not any(re.search(rf"\bto {role}\b", ln) for _, ln in grant_lines))
            continue
        check(f"at least one grant names {role}",
              any(re.search(rf"\bto {role}\b", ln) for _, ln in grant_lines))

    # The PR #75 question, answerable at last: may the writer insert into
    # lead? The emitter aggregates privileges per (table, grantee), so insert
    # may share its statement with select and update.
    check("carr_writer holds insert on public.lead (the PR #75 case)",
          any(re.match(r"grant [a-z, ]*\binsert\b[a-z, ]* on table "
                       r"public\.lead to carr_writer;", ln)
              for _, ln in grant_lines))

    # 0117's column-scoped update. The columns INSIDE the parens are the
    # grant; resolved_at staying OUTSIDE them is the interlock the migration
    # exists for. An emitter that flattens column grants to whole-table
    # grants would pass a naive "update is granted" check and silently hand
    # the collector the human-conclusion columns.
    col_update = next((ln for _, ln in grant_lines
                       if re.match(r"grant update \([^)]*\bstate\b[^)]*\) "
                                   r"on table ops\.incident to carr_jobs;", ln)),
                      None)
    check("0117's column-scoped update on ops.incident survives",
          col_update is not None)
    check("resolved_at stays OUTSIDE the update column list — the interlock",
          col_update is not None and "resolved_at" not in col_update)

    check("0021's column-scoped selects for carr_jobs survive",
          any(re.match(r"grant select \([^)]*\) on table public\.client "
                       r"to carr_jobs;", ln)
              for _, ln in grant_lines))

    check("function execute grants survive (0106's state_as_of)",
          any(re.match(r"grant execute on function public\.state_as_of\(.*\) "
                       r"to carr_reader;", ln)
              for _, ln in grant_lines))

    for schema, role in [("public", "carr_writer"), ("ops", "carr_jobs")]:
        check(f"schema usage on {schema} for {role}",
              any(re.match(rf"grant [a-z, ]*\busage\b[a-z, ]* on schema "
                           rf"{schema} to {role};", ln)
                  for _, ln in grant_lines))

    check("0006's membership bundle survives (exporter is reader-plus)",
          any(ln == "grant carr_reader to carr_exporter;"
              for _, ln in grant_lines))

    ownership_activation_applied = (
        "0532a_canonical_ownership_lease_activation.sql\t" in sql
    )
    for principal in ("carr_ownership_issuer_g1", "carr_ownership_issuer_g2"):
        expected = f"grant carr_ownership_issuer to {principal};"
        check(f"ownership issuer membership for {principal} is ledger-appropriate",
              (expected in canonical_section) if ownership_activation_applied
              else (expected not in canonical_section))

    generator = open(GENERATOR).read()
    check("function ACL renderer uses a qualification-neutral search path",
          re.search(r"cat > \"\$GRANTS_SQL\" <<'GRANTSQL'\n.*?set search_path = '';",
                    generator, re.S) is not None)
    renewal_delivery_applied = bool(re.search(
        r"^0230_renewal_decision_delivery\.sql\t", sql, re.M))
    canonical_grants = "\n".join(canonical_section)
    check("composite function arguments stay schema-qualified in ACL statements",
          (not renewal_delivery_applied)
          or ("ops.renewal_decision_candidate_digest(p_candidate public.candidate_pool)" in canonical_grants
              and "ops.renewal_decision_candidate_digest(p_candidate candidate_pool)" not in canonical_grants))
    membership_query = re.search(
        r"select distinct format\('grant %s to %s;', gr\.rolname, mem\.rolname\)"
        r".*?from pg_auth_members m.*?order by 1;",
        generator,
        re.S,
    )
    check("membership renderer de-duplicates only identical rendered lines",
          membership_query is not None)
    check("membership renderer excludes LOGIN roles from bundle replay",
          membership_query is not None
          and "and not gr.rolcanlogin" in membership_query.group(0))

    for direct_login in ("carr_jobs", "carr_gate_zero_producer",
                         "carr_foundation_assurance_oracle"):
        check(f"snapshot does not replay {direct_login} as a membership bundle",
              f"grant {direct_login} to neondb_owner;" not in canonical_section)

    check("all five ACL renderers retain the foundation-assurance oracle",
          generator.count("('carr_foundation_assurance_oracle')") == 5)
    check("all five ACL renderers retain the ownership issuer bundle",
          generator.count("('carr_ownership_issuer')") == 5)
    check("ownership issuer generations are membership-only renderer targets",
          "('carr_ownership_issuer_g1'), ('carr_ownership_issuer_g2')" in generator)

    # THE WIDENING GUARD. Every grantee in the file must be an app role —
    # or neondb_owner, on membership lines only. Anything else means some
    # other principal's production ACLs were swept into a tracked file.
    allowed = set(APP_ROLES)
    dot_applied = bool(re.search(r"^0756_dot_reader\.sql\t", sql, re.M))
    # This administrative membership is part of the released role preamble,
    # never an object ACL or a generally permitted option-bearing membership.
    dot_admin = "grant dot_reader to neondb_owner with admin true, inherit false, set false;"
    membership = re.compile(
        rf"grant ({'|'.join(APP_ROLES)}) to ({'|'.join(APP_ROLES + MEMBERSHIP_ONLY)});")
    strays = []
    for _, ln in grant_lines:
        if dot_applied and ln == dot_admin:
            continue
        if membership.fullmatch(ln):
            continue
        m = re.search(r"\bto ([a-z0-9_, ]+);", ln)
        grantees = [g.strip() for g in m.group(1).split(",")] if m else ["<unparsed>"]
        strays.extend(g for g in grantees if g not in allowed)
    check("no grantee outside the app-role set",
          not strays, f"strays: {sorted(set(strays))[:5]}")

    # Ordering: a grant that runs before its object exists aborts the load,
    # so the whole section must follow the structure. Compared by line
    # number over statement lines, the roles test's hard-won idiom.
    last_create = max((i for i, ln in enumerate(lines)
                       if re.match(r"\s*CREATE (TABLE|.*VIEW|SEQUENCE|FUNCTION)\b", ln)),
                      default=None)
    object_grants = [(i, ln) for i, ln in grant_lines
                     if not (dot_applied and ln == dot_admin)]
    first_grant = object_grants[0][0] if object_grants else None
    check("every grant follows the structure it attaches to",
          first_grant is not None and last_create is not None
          and first_grant > last_create,
          f"first grant at line {first_grant}, last create at line {last_create}")

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
