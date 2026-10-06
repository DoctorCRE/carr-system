#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Role and principal boundary proof for the DoctorCRE v5 portfolio hierarchy.

The transaction-scoped proof in mcp-server/test/work-portfolio-postgres.sql runs
on one connection and can show the digest, structure and append-only rules. It
cannot show the boundary that actually decides WHO may propose, review and
accept, because that boundary is the database session itself.

Two facts make this a separate file. `SET ROLE` moves current_user and leaves
session_user alone, so a superuser pretending to be a partner is still not one --
the acceptance guard reads session_user through ops.authority_actor_slug() and
would see the superuser. And table privileges are checked against the role in
effect, so the direct-INSERT refusals need a connection that genuinely is the
writer bundle. Both need real logins and separate connections.

Fixture roles are production-shaped: app_writer is a login member of the
carr_writer bundle, exactly as the deployed writer credential is, and the two
partner authority logins mirror carr_authority_joe / carr_authority_dell. The
bundle roles themselves are never made LOGIN: doing that removes them from the
SCAC role-authority projection and breaks the schema seal, which cost one
rebuild while this gate was being written.

Every fixture row is rolled back.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import uuid

import psycopg
from psycopg import sql

PARTNER_LOGINS = {"carr_authority_joe": "joe", "carr_authority_dell": "dell"}
WRITER_LOGIN = "app_writer"
FIXTURE_PASSWORD = "portfolio-gate-fixture"  # pragma: allowlist secret
CHILDREN = ("foundation-and-control-plane", "assurance-fabric",
            "product-journeys", "rollout-and-retirement")
PLACEHOLDER = "sha256:" + "0" * 64


def one(cur) -> tuple:
    """The single row a statement must have returned.

    psycopg types fetchone() as optional because a statement need not produce a
    row. Every use below follows a query that must produce exactly one, so the
    absence is a broken assumption rather than a value to handle: naming it here
    turns a bare subscript of None into a sentence that says what went wrong.
    """
    value = cur.fetchone()
    if value is None:
        raise AssertionError("a statement that must return exactly one row returned none")
    return value


def fail(message: str) -> int:
    print(f"work-portfolio-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refused(cur, sql: str, params=(), *, expect: str) -> tuple[bool, str]:
    """Run a statement that must be refused, and say whether it was refused for
    the stated reason. A refusal for some other reason is not a pass: it would
    let a guard rot behind an unrelated error."""
    try:
        cur.execute(sql, params)
    except psycopg.Error as error:  # noqa: PERF203 - one statement per assertion
        text = str(error)
        return (expect in text, text.strip().splitlines()[0])
    return (False, "the statement was NOT refused")


def synthetic_payload(portfolio_ref: str):
    nodes, edges = [], []
    for index in range(1, 22):
        ceiling = 12.5 if index == 3 else (0.0001 if index == 5 else index * 1000)
        nodes.append({
            "node_ref": f"step:gate-milestone-{index:02d}", "node_kind": "milestone",
            "ordinal": index, "parent_ref": portfolio_ref,
            "child_ref": CHILDREN[(index - 1) % 4],
            "authority_class": "synthetic_authority", "effect_class": "synthetic_no_effect",
            "data_class": "synthetic_record_layer",
            "budget_identity": f"synthetic:budget-{index}", "budget_ceiling": ceiling,
            "model_floor": {"provider": "synthetic", "model": "synthetic",
                            "version": "1", "effort": "high"},
            "recovery_ref": f"recovery:synthetic-{index}",
            "terminal_predicate": "synthetic terminal",
        })
        if index > 1:
            edges.append({"from_node_ref": f"step:gate-milestone-{index - 1:02d}",
                          "to_node_ref": f"step:gate-milestone-{index:02d}"})
    children = [{"child_ref": ref, "child_ordinal": position, "child_version": 1,
                 "child_digest": PLACEHOLDER, "accepted_plan_ref": None}
                for position, ref in enumerate(CHILDREN)]
    sources = {key: "sha256:" + str(number) * 64 for number, key in
               enumerate(("constitution", "design", "integration", "requirements"), start=1)}
    return nodes, edges, children, sources


def ensure_fixture_logins(dsn: str) -> None:
    """Create the production-shaped login roles when they are absent.

    Committed on purpose: a separate connection cannot see an uncommitted role.
    This only ever ADDS login roles, which the SCAC role-authority projection
    excludes, so the schema seal is unaffected. It never alters a bundle role.
    """
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # ALTER/CREATE ROLE ... PASSWORD takes no bound parameter, so the value
        # is composed as a literal rather than interpolated by hand.
        for login in (*PARTNER_LOGINS, WRITER_LOGIN):
            cur.execute("select 1 from pg_roles where rolname=%s", (login,))
            verb = "create role" if cur.fetchone() is None else "alter role"
            cur.execute(sql.SQL("{} {} login password {}").format(
                sql.SQL(verb), sql.Identifier(login), sql.Literal(FIXTURE_PASSWORD)))
        for login in PARTNER_LOGINS:
            cur.execute(sql.SQL("grant carr_authority to {}").format(sql.Identifier(login)))
        cur.execute(sql.SQL("grant carr_writer to {}").format(sql.Identifier(WRITER_LOGIN)))
        cur.execute(sql.SQL("grant usage on schema ops, public to {}").format(
            sql.SQL(", ").join(sql.Identifier(r) for r in (*PARTNER_LOGINS, WRITER_LOGIN))))


def login_dsn(dsn: str, role: str) -> str:
    parsed = psycopg.conninfo.conninfo_to_dict(dsn)
    parsed.update({"user": role, "password": FIXTURE_PASSWORD})
    parsed.pop("passfile", None)
    # conninfo_to_dict widens values to str | int | None while make_conninfo
    # takes strings; a None here means the key was never set, so it is dropped
    # rather than passed through as the literal "None".
    return psycopg.conninfo.make_conninfo(
        **{key: str(value) for key, value in parsed.items() if value is not None})


FIXTURE_PATH = (pathlib.Path(__file__).resolve().parent.parent
                / "mcp-server" / "test" / "fixtures" / "doctorcre-portfolio-21-node.json")


def cross_layer_parity(dsn: str, check) -> None:
    """Prove PostgreSQL reproduces the JavaScript canonicalizer exactly.

    Every other proof in this tree learns its digests FROM PostgreSQL, so a
    PostgreSQL/ECMAScript divergence would satisfy all of them while binding
    different bytes than the module a reviewer reads. This one never lets the
    database supply its own expectations: mcp-server/src/work-portfolio.js is
    the canonical producer, its output is recorded in the fixture's cross_layer
    block, work-portfolio.test.mjs proves that block is still exactly what the
    module emits, and here the identical payload goes into PostgreSQL and the
    answers must match byte for byte.

    The payload deliberately spans the whole accepted budget domain -- the
    subnormal minimum, both sides of the JavaScript exponent threshold, both
    sides of the PostgreSQL float8 text threshold, and the finite ceiling -- and
    every one of its twenty-one nodes renders differently, so no collision can
    hide a mismatch.
    """
    fixture = json.loads(FIXTURE_PATH.read_text())
    cross = fixture["cross_layer"]
    expected = cross["js_expected"]
    payload_nodes = [dict(node, **cross["node_metadata"][node["node_ref"]],
                          child_ref=cross["node_child_refs"][node["node_ref"]])
                     for node in fixture["revision"]["nodes"]]
    payload_children = [
        {"child_ref": binding["child_ref"], "child_ordinal": binding["child_ordinal"],
         "child_version": binding["child_version"], "child_digest": binding["child_digest"],
         "accepted_plan_ref": binding["accepted_plan_ref"]}
        for binding in expected["child_bindings"]]
    # The fixture's own portfolio reference is used verbatim, so the recorded
    # bytes and digests are compared exactly as JavaScript emitted them with no
    # substitution step that could itself paper over a difference. Nothing is
    # committed: the whole proof, plans included, rolls back.
    portfolio = fixture["revision"]["portfolio_ref"]
    graph_bytes = expected["graph_canonical_bytes"]
    accepted_bytes = expected["accepted_canonical_bytes"]
    graph_digest = expected["graph_digest"]
    accepted_digest = expected["accepted_digest"]

    def propose(cur, nodes, children):
        cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
        cur.execute("set constraints all deferred")
        cur.execute(
            "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
            "%s::jsonb,%s::jsonb,%s::jsonb)",
            (portfolio, json.dumps(fixture["revision"]["source_digests"]),
             graph_digest, accepted_digest, json.dumps(children),
             json.dumps(nodes), json.dumps(fixture["revision"]["edges"])))
        return one(cur)[0]

    def seed_accepted_plans(cur):
        """Make the plan references the fixture binds real and accepted.

        Three of the four children name an accepted plan, and that reference is
        inside the accepted digest, so the cross-layer comparison only covers it
        if PostgreSQL can actually resolve those plans. They are created inside
        this transaction with the append-only triggers suspended -- the real
        intake path is a whole Work Request lifecycle and this proof is about
        the digest, not about re-creating that -- and they die with the
        rollback.
        """
        cur.execute("set session_replication_role = replica")
        for binding in expected["child_bindings"]:
            if binding["accepted_plan_ref"] is None:
                continue
            plan_id, plan_hash = str(uuid.uuid4()), "sha256:" + uuid.uuid4().hex * 2
            cur.execute(
                "insert into ops.sourced_work_request_plan(id,work_request_id,plan_version,"
                "idempotency_key,work_request_version,preimage,scope_summary,runbook_ref,"
                "runbook_section_id,runbook_revision_id,runbook_content_hash,dependency_refs,"
                "recovery_ref,observability_ref,caps,plan_hash,plan_ref) values "
                "(%s,%s,1,gen_random_uuid(),1,'{}'::jsonb,'cross layer fixture',"
                "'doctrine:runbook#portfolio-gate-fixture',%s,%s,%s,'[]'::jsonb,"
                "'safe:portfolio-gate','safe:portfolio-gate',"
                "'{\"max_steps\":1,\"max_duration_minutes\":1}'::jsonb,%s,%s)",
                (plan_id, str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()),
                 "f" * 64, plan_hash, binding["accepted_plan_ref"]))
            cur.execute(
                "insert into ops.sourced_work_request_plan_acceptance_receipt(work_request_id,"
                "plan_id,idempotency_key,base_version,plan_hash,accepted_by_actor_id,"
                "result_version,shape_fixed_surface_ref,shape_rationale) values "
                "(%s,%s,gen_random_uuid(),1,%s,(select id from public.actor where slug='joe'),"
                "2,'surface','cross layer fixture')",
                (str(uuid.uuid4()), plan_id, plan_hash))
        cur.execute("set session_replication_role = origin")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Accepting the JavaScript digests is itself the parity assertion: the
        # proposal guard recomputes both from the persisted rows and refuses
        # anything that does not match, so a successful insert can only mean
        # PostgreSQL derived identical bytes.
        seed_accepted_plans(cur)
        try:
            revision_id = propose(cur, payload_nodes, payload_children)
            # The digest comparison is a DEFERRED constraint trigger: it fires at
            # commit, once the children and nodes of the revision are all present.
            # Forcing it immediate here is what actually runs it, and is why this
            # assertion means something rather than merely reporting that an
            # INSERT was syntactically accepted.
            cur.execute("set constraints all immediate")
            check("PostgreSQL accepts the JavaScript-computed graph and accepted digests", True)
        except psycopg.Error as error:
            check("PostgreSQL accepts the JavaScript-computed graph and accepted digests",
                  False, str(error).strip().splitlines()[0])
            conn.rollback()
            return
        cur.execute("set constraints all deferred")

        cur.execute("select ops.portfolio_canonical_json(ops.portfolio_graph_preimage(%s))",
                    (revision_id,))
        check("the graph canonical bytes are identical across the two layers",
              one(cur)[0] == graph_bytes)
        cur.execute("select ops.portfolio_canonical_json(ops.portfolio_accepted_preimage(%s))",
                    (revision_id,))
        check("the accepted canonical bytes are identical across the two layers",
              one(cur)[0] == accepted_bytes)
        for binding in expected["child_bindings"]:
            cur.execute("select ops.portfolio_child_digest(%s,%s)",
                        (revision_id, binding["child_ref"]))
            got = one(cur)[0]
            check(f"the {binding['child_ref']} child digest matches JavaScript",
                  got == binding["child_digest"], f"{got} != {binding['child_digest']}")
        # Each budget must appear in the database's own bytes with the exact
        # spelling JavaScript produced for it.
        cur.execute("select ops.portfolio_canonical_json(ops.portfolio_graph_preimage(%s))",
                    (revision_id,))
        rendered = one(cur)[0]
        for node_ref, spelling in cross["js_expected"]["budget_ceiling_canonical_text"].items():
            check(f"PostgreSQL renders the budget of {node_ref} as {spelling}",
                  f'"budget_ceiling":{spelling}' in rendered)
        conn.rollback()

    # A DELIBERATE cross-layer tamper must fail. Without this the assertions
    # above would also pass against a canonicalizer that ignored the payload.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        tampered = [dict(node) for node in payload_nodes]
        tampered[0]["budget_ceiling"] = 1e-323  # one representable step from 5e-324
        seed_accepted_plans(cur)
        propose(cur, tampered, payload_children)
        # Same forced evaluation as the positive case, so the two differ in the
        # payload alone and not in how hard they were checked.
        ok, detail = refused(cur, "set constraints all immediate", expect="digest")
        check("one changed budget makes the JavaScript digest refuse in PostgreSQL", ok, detail)
        conn.rollback()


def ambiguous_ancestor(dsn, portfolio, probe_ref, nodes, edges, children, sources, check) -> None:
    """Two accepted portfolios naming one slice must refuse, never pick one.

    Node reference uniqueness is enforced per revision, so nothing in the schema
    stops a second portfolio from accepting a revision that names this same
    slice. Both can be intact and both can bind the same child plan while
    disagreeing on budget, authority class and model floor -- and every one of
    those fields rides into the envelope. Returning either would let a scan
    order decide which portfolio governs, so the collision is refused.

    The twin is built through the real propose/review/accept path rather than by
    tampering, because this collision needs no corruption at all.
    """
    def accept(name, payload_nodes):
        """Propose, review and accept one portfolio; returns its revision id."""
        digests = {}
        with psycopg.connect(dsn) as learn, learn.cursor() as cur:
            for field, fn in (("graph", "ops.portfolio_graph_digest"),
                              ("accepted", "ops.portfolio_accepted_digest")):
                cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
                cur.execute("set constraints all deferred")
                cur.execute(
                    "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
                    "%s::jsonb,%s::jsonb,%s::jsonb)",
                    (name, json.dumps(sources), digests.get("graph", PLACEHOLDER), PLACEHOLDER,
                     json.dumps(digests.get("children", children)),
                     json.dumps(payload_nodes), json.dumps(edges)))
                rev = one(cur)[0]
                if field == "graph":
                    cur.execute(f"select {fn}(%s)", (rev,))
                    digests["graph"] = one(cur)[0]
                    learned = []
                    for position, ref in enumerate(CHILDREN):
                        cur.execute("select ops.portfolio_child_digest(%s,%s)", (rev, ref))
                        learned.append(dict(children[position], child_digest=one(cur)[0]))
                    digests["children"] = learned
                else:
                    cur.execute(f"select {fn}(%s)", (rev,))
                    digests["accepted"] = one(cur)[0]
                learn.rollback()

        conn = psycopg.connect(dsn)
        with conn.cursor() as cur:
            cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
            cur.execute("set constraints all deferred")
            cur.execute(
                "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
                "%s::jsonb,%s::jsonb,%s::jsonb)",
                (name, json.dumps(sources), digests["graph"], digests["accepted"],
                 json.dumps(digests["children"]), json.dumps(payload_nodes), json.dumps(edges)))
            rev = one(cur)[0]
        conn.commit()
        conn.close()
        return rev, digests["accepted"]

    # An UNACCEPTED proposal governs nothing. Proposed first, so the assertion
    # below proves the slice still resolves to exactly one accepted ancestor
    # while a second portfolio merely proposes it.
    pending = f"{portfolio}-PENDING"
    pending_nodes = [dict(node, parent_ref=pending) for node in nodes]
    accept(pending, pending_nodes)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select ops.portfolio_descendant_binding(%s)", (probe_ref,))
        binding = one(cur)[0]
        check("a proposed but unaccepted portfolio does not govern the slice",
              binding.get("governed") is True and binding.get("portfolio_ref") == portfolio,
              str(binding.get("portfolio_ref")))
        conn.rollback()

    # Now a SECOND portfolio genuinely accepts a revision naming the same slice,
    # with the same child plan binding and different governing metadata.
    twin = f"{portfolio}-TWIN"
    twin_nodes = []
    for node in nodes:
        clone = dict(node, parent_ref=twin)
        if clone["node_ref"] == probe_ref:
            clone["budget_ceiling"] = 999999
            clone["authority_class"] = "twin_authority"
        if clone["node_ref"] == "step:gate-milestone-01":
            clone["node_ref"] = "step:gate-twin-only-01"
        twin_nodes.append(clone)
    twin_edges = [{"from_node_ref": e["from_node_ref"].replace("step:gate-milestone-01",
                                                               "step:gate-twin-only-01"),
                   "to_node_ref": e["to_node_ref"].replace("step:gate-milestone-01",
                                                           "step:gate-twin-only-01")}
                  for e in edges]
    saved_edges, edges[:] = list(edges), twin_edges
    try:
        twin_revision, twin_accepted = accept(twin, twin_nodes)
    finally:
        edges[:] = saved_edges

    with psycopg.connect(login_dsn(dsn, WRITER_LOGIN)) as writer, writer.cursor() as cur:
        cur.execute("select set_config('carr.acting_actor_slug','dell',true)")
        cur.execute("select set_config('carr.verified_human_actor_slug','dell',true)")
        cur.execute("select ops.portfolio_review_revision(%s,gen_random_uuid(),%s,'pass',"
                    "'twin gate review')", (twin_revision, twin_accepted))
        twin_review = one(cur)[0]
        writer.commit()
    with psycopg.connect(login_dsn(dsn, "carr_authority_joe")) as joe, joe.cursor() as cur:
        cur.execute("select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)",
                    (twin_revision, twin_accepted, twin_review))
        joe.commit()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        ok, detail = refused(cur, "select ops.portfolio_descendant_binding(%s)", (probe_ref,),
                             expect="ambiguous governing ancestor")
        check("two accepted portfolios naming one slice refuse instead of ranking", ok, detail)
        conn.rollback()
        # The refusal must be about AMBIGUITY, not about a second portfolio
        # existing: a slice only one of them names still resolves, and it
        # resolves to that one.
        cur.execute("select ops.portfolio_descendant_binding('step:gate-twin-only-01')")
        only = one(cur)[0]
        check("a slice named by exactly one accepted portfolio still resolves to it",
              only.get("governed") is True and only.get("portfolio_ref") == twin,
              str(only.get("portfolio_ref")))
        cur.execute("select ops.portfolio_descendant_binding('slice:ordinary-attended-source')")
        check("ordinary work stays ungoverned while two portfolios are accepted",
              one(cur)[0] == {"governed": False})
        conn.rollback()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("work-portfolio-local-pg-gate: no DATABASE_URL", file=sys.stderr)
        return 78
    with psycopg.connect(dsn) as probe, probe.cursor() as cur:
        cur.execute("select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                    "where n.nspname='ops' and p.proname='portfolio_propose_revision'")
        if cur.fetchone() is None:
            print("work-portfolio-local-pg-gate: SKIP — ops.portfolio_propose_revision is "
                  "absent; the portfolio migration has not been applied to this database.")
            return 78

    ensure_fixture_logins(dsn)
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{label}: {detail}")

    portfolio = f"WR-GATE-{uuid.uuid4().hex[:8]}"
    nodes, edges, children, sources = synthetic_payload(portfolio)

    # --- one owner connection builds an accepted revision, then rolls back ----
    with psycopg.connect(dsn) as owner, owner.cursor() as cur:
        cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
        cur.execute("set constraints all deferred")
        cur.execute(
            "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
            "%s::jsonb,%s::jsonb,%s::jsonb)",
            (portfolio, json.dumps(sources), PLACEHOLDER, PLACEHOLDER,
             json.dumps(children), json.dumps(nodes), json.dumps(edges)))
        revision_id = one(cur)[0]
        cur.execute("select ops.portfolio_graph_digest(%s)", (revision_id,))
        graph_digest = one(cur)[0]
        for position, ref in enumerate(CHILDREN):
            cur.execute("select ops.portfolio_child_digest(%s,%s)", (revision_id, ref))
            children[position]["child_digest"] = one(cur)[0]
        owner.rollback()

        # Re-propose with the real child digests to learn the accepted digest.
        cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
        cur.execute("set constraints all deferred")
        cur.execute(
            "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
            "%s::jsonb,%s::jsonb,%s::jsonb)",
            (portfolio, json.dumps(sources), graph_digest, PLACEHOLDER,
             json.dumps(children), json.dumps(nodes),
             json.dumps(edges)))
        revision_id = one(cur)[0]
        cur.execute("select ops.portfolio_accepted_digest(%s)", (revision_id,))
        accepted_digest = one(cur)[0]
        owner.rollback()

    # The committed fixture the boundary tests act against. It is removed in the
    # finally block; nothing about it is left behind.
    committed = psycopg.connect(dsn, autocommit=False)
    try:
        with committed.cursor() as cur:
            cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
            cur.execute("set constraints all deferred")
            cur.execute(
                "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
                "%s::jsonb,%s::jsonb,%s::jsonb)",
                (portfolio, json.dumps(sources), graph_digest, accepted_digest,
                 json.dumps(children), json.dumps(nodes),
                 json.dumps(edges)))
            revision_id = one(cur)[0]
        committed.commit()

        # --- direct table writes are granted to nobody ----------------------
        with psycopg.connect(login_dsn(dsn, WRITER_LOGIN)) as writer, writer.cursor() as cur:
            ok, detail = refused(
                cur,
                "insert into ops.portfolio_revision_review(portfolio_revision_id,"
                "idempotency_key,reviewed_digest,verdict,review_summary,reviewer_actor_id) "
                "values (%s,gen_random_uuid(),%s,'pass','direct',"
                "(select id from public.actor where slug='dell'))",
                (revision_id, accepted_digest), expect="permission denied")
            check("writer direct INSERT into review", ok, detail)
            writer.rollback()
            ok, detail = refused(
                cur, "select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)",
                (revision_id, accepted_digest, revision_id), expect="permission denied for function")
            check("writer executes the accept function", ok, detail)
            writer.rollback()

            # Authorship is derived, so a writer cannot claim a partner without
            # the verified-partner context the server sets only for one.
            cur.execute("select set_config('carr.acting_actor_slug','dell',true)")
            ok, detail = refused(
                cur, "select ops.portfolio_review_revision(%s,gen_random_uuid(),%s,'pass','x')",
                (revision_id, accepted_digest), expect="verified-partner context")
            check("writer claims a partner without verified-human context", ok, detail)
            writer.rollback()

            cur.execute("select set_config('carr.acting_actor_slug','dell',true)")
            cur.execute("select set_config('carr.verified_human_actor_slug','joe',true)")
            ok, detail = refused(
                cur, "select ops.portfolio_review_revision(%s,gen_random_uuid(),%s,'pass','x')",
                (revision_id, accepted_digest), expect="verified-partner context")
            check("writer names one partner while verified as another", ok, detail)
            writer.rollback()

        with psycopg.connect(login_dsn(dsn, "carr_authority_joe")) as joe, joe.cursor() as cur:
            ok, detail = refused(
                cur,
                "insert into ops.portfolio_revision_acceptance_receipt(portfolio_revision_id,"
                "portfolio_ref,idempotency_key,accepted_digest,review_id,accepted_by_actor_id) "
                "values (%s,%s,gen_random_uuid(),%s,gen_random_uuid(),"
                "(select id from public.actor where slug='joe'))",
                (revision_id, portfolio, accepted_digest), expect="permission denied")
            check("authority direct INSERT into the receipt", ok, detail)
            joe.rollback()

        # --- an independent review, then the acceptance boundary ------------
        with psycopg.connect(login_dsn(dsn, WRITER_LOGIN)) as writer, writer.cursor() as cur:
            cur.execute("select set_config('carr.acting_actor_slug','dell',true)")
            cur.execute("select set_config('carr.verified_human_actor_slug','dell',true)")
            cur.execute("select ops.portfolio_review_revision(%s,gen_random_uuid(),%s,'pass',"
                        "'independent gate review')", (revision_id, accepted_digest))
            review_id = one(cur)[0]
            writer.commit()

        with psycopg.connect(login_dsn(dsn, "carr_authority_dell")) as dell, dell.cursor() as cur:
            ok, detail = refused(
                cur, "select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)",
                (revision_id, accepted_digest, review_id),
                expect="may not also be the independent reviewer")
            check("the reviewer accepts their own pass", ok, detail)
            dell.rollback()

        with psycopg.connect(login_dsn(dsn, "carr_authority_joe")) as joe, joe.cursor() as cur:
            ok, detail = refused(
                cur, "select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)",
                (revision_id, PLACEHOLDER, review_id), expect="digest is stale")
            check("acceptance with a digest that is not current", ok, detail)
            joe.rollback()

            cur.execute("select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)",
                        (revision_id, accepted_digest, review_id))
            check("the verified partner accepts", one(cur)[0] is not None)
            joe.commit()

        # --- after acceptance the structure is closed to inserts too --------
        with psycopg.connect(dsn) as owner, owner.cursor() as cur:
            cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
            for table, statement, params in (
                ("edge",
                 "insert into ops.portfolio_node_edge(portfolio_revision_id,from_node_ref,"
                 "to_node_ref) values (%s,'step:gate-milestone-21','step:gate-milestone-01')",
                 (revision_id,)),
                ("node",
                 "insert into ops.portfolio_node(portfolio_revision_id,node_ref,node_kind,"
                 "ordinal,parent_ref,child_ref,authority_class,effect_class,data_class,"
                 "budget_identity,budget_ceiling,model_floor,recovery_ref,terminal_predicate) "
                 "values (%s,'step:gate-appended','milestone',21,%s,'assurance-fabric',"
                 "'a_class','b_class','c_class','budget',1,"
                 "'{\"provider\":\"p\",\"model\":\"m\",\"version\":\"1\",\"effort\":\"high\"}'::jsonb,"
                 "'recovery:x','terminal')",
                 (revision_id, portfolio)),
            ):
                ok, detail = refused(cur, statement, params,
                                     expect="closed to further")
                check(f"appending a {table} after acceptance", ok, detail)
                owner.rollback()
                cur.execute("select set_config('carr.acting_actor_slug','codex',true)")

            # --- and the acceptance produced no executable effect ----------
            cur.execute("select (select count(*) from ops.job),"
                        "(select count(*) from ops.engineering_execution_envelope),"
                        "(select count(*) from ops.capability_agent_session)")
            jobs, envelopes, sessions = one(cur)
            cur.execute("select ops.portfolio_readback(%s)", (portfolio,))
            readback = one(cur)[0]
            check("the readback reports the revision accepted", readback.get("accepted") is True,
                  str(readback.get("accepted")))
            check("the readback reports no effect",
                  readback["effects"]["creates_effect"] is False
                  and all(value == 0 for key, value in readback["effects"].items()
                          if key != "creates_effect"),
                  str(readback["effects"]))
            cur.execute("select ops.portfolio_descendant_binding('step:gate-milestone-09')")
            binding = one(cur)[0]
            check("the descendant binding is governed and names its child",
                  binding.get("governed") is True and binding.get("child_ref") in CHILDREN,
                  str(binding)[:160])
            cur.execute("select ops.portfolio_descendant_binding('slice:ordinary-attended-source')")
            unnamed = one(cur)[0]
            check("ordinary attended source work is not portfolio-governed",
                  unnamed == {"governed": False}, str(unnamed))
            owner.rollback()

        # --- a child may bind an ACCEPTED plan and nothing else --------------
        # A sourced plan row is a proposal until a human acceptance receipt
        # exists for it. Binding a child to a merely proposed plan would let
        # unaccepted source inherit governing authority through the portfolio.
        # The fixture plan is built with the replication role suspended because
        # the real intake path is a whole Work Request lifecycle and this gate
        # is about the portfolio's check, not about re-creating that; it is
        # removed the same way at the end.
        plan_ref = f"PLAN-{uuid.uuid4().hex[:12]}-v1"
        plan_hash = "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex
        plan_id = str(uuid.uuid4())
        with psycopg.connect(dsn, autocommit=True) as fixture, fixture.cursor() as cur:
            cur.execute("set session_replication_role = replica")
            cur.execute(
                "insert into ops.sourced_work_request_plan(id,work_request_id,plan_version,"
                "idempotency_key,work_request_version,preimage,scope_summary,runbook_ref,"
                "runbook_section_id,runbook_revision_id,runbook_content_hash,dependency_refs,"
                "recovery_ref,observability_ref,caps,plan_hash,plan_ref) values "
                "(%s,%s,1,gen_random_uuid(),1,'{}'::jsonb,'portfolio gate fixture',"
                "'doctrine:runbook#portfolio-gate-fixture',%s,%s,%s,'[]'::jsonb,"
                "'safe:portfolio-gate','safe:portfolio-gate',"
                "'{\"max_steps\":1,\"max_duration_minutes\":1}'::jsonb,%s,%s)",
                (plan_id, str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()),
                 "f" * 64, plan_hash, plan_ref))
            cur.execute("set session_replication_role = origin")

        def propose_with_plan(bound_plan_ref, portfolio_name):
            bindings = [dict(child, accepted_plan_ref=(bound_plan_ref
                        if child["child_ref"] == "assurance-fabric" else None))
                        for child in children]
            payload_nodes = [dict(node, parent_ref=portfolio_name) for node in nodes]
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
                cur.execute("set constraints all deferred")
                result = refused(
                    cur,
                    "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,"
                    "%s::jsonb,%s::jsonb,%s::jsonb)",
                    (portfolio_name, json.dumps(sources), PLACEHOLDER, PLACEHOLDER,
                     json.dumps(bindings), json.dumps(payload_nodes), json.dumps(edges)),
                    expect="which is not an accepted plan")
                conn.rollback()
                return result

        ok, detail = propose_with_plan(plan_ref, f"{portfolio}-UNACCEPTED")
        check("a child bound to a plan with no acceptance receipt refuses", ok, detail)

        # The same reference, once genuinely accepted, is admitted. Without this
        # the negative above would also pass if the check simply refused every
        # plan reference.
        with psycopg.connect(dsn, autocommit=True) as fixture, fixture.cursor() as cur:
            cur.execute("set session_replication_role = replica")
            cur.execute(
                "insert into ops.sourced_work_request_plan_acceptance_receipt(work_request_id,"
                "plan_id,idempotency_key,base_version,plan_hash,accepted_by_actor_id,"
                "result_version,shape_fixed_surface_ref,shape_rationale) values "
                "(%s,%s,gen_random_uuid(),1,%s,(select id from public.actor where slug='joe'),"
                "2,'surface','portfolio gate fixture')",
                (str(uuid.uuid4()), plan_id, plan_hash))
            cur.execute("set session_replication_role = origin")
        ok, detail = propose_with_plan(plan_ref, f"{portfolio}-ACCEPTED")
        check("the same plan, once accepted, is admitted as a child binding",
              not ok and "NOT refused" in detail, detail)

        # --- integrity failure must REFUSE, never downgrade or fall back ----
        # Tampering needs the append-only trigger suspended, which is exactly
        # what a corrupted database would look like from here: rows that no
        # longer produce the digest their acceptance receipt names.
        with psycopg.connect(dsn, autocommit=True) as tamper, tamper.cursor() as cur:
            cur.execute("set session_replication_role = replica")
            cur.execute("update ops.portfolio_child_revision set child_version = 99 "
                        "where portfolio_revision_id = %s and child_ref = 'assurance-fabric'",
                        (revision_id,))
            cur.execute("set session_replication_role = origin")
        try:
            with psycopg.connect(dsn) as broken, broken.cursor() as cur:
                cur.execute("select ops.portfolio_revision_integrity_error(%s)", (revision_id,))
                detail = one(cur)[0]
                check("integrity error names the tampered child", bool(detail) and "child" in detail,
                      str(detail))
                broken.rollback()
                ok, detail = refused(cur, "select ops.portfolio_accepted_revision(%s)", (portfolio,),
                                     expect="failed integrity")
                check("a tampered current accepted revision refuses", ok, detail)
                broken.rollback()
                ok, detail = refused(cur, "select ops.portfolio_descendant_binding(%s)",
                                     ("step:gate-milestone-09",), expect="failed integrity")
                check("a tampered portfolio does not downgrade to ungoverned", ok, detail)
                broken.rollback()
                # While ANY accepted portfolio is unreadable, no slice can be
                # called ungoverned: the corrupt one might be the portfolio that
                # names it. Refusing every answer is the honest state, and it is
                # loud rather than silent.
                ok, detail = refused(cur, "select ops.portfolio_descendant_binding(%s)",
                                     ("slice:ordinary-attended-source",),
                                     expect="failed integrity")
                check("no slice is called ungoverned while a portfolio is corrupt", ok, detail)
                broken.rollback()
        finally:
            with psycopg.connect(dsn, autocommit=True) as repair, repair.cursor() as cur:
                cur.execute("set session_replication_role = replica")
                cur.execute("update ops.portfolio_child_revision set child_version = 1 "
                            "where portfolio_revision_id = %s and child_ref = 'assurance-fabric'",
                            (revision_id,))
                cur.execute("set session_replication_role = origin")

        # --- a node REMOVED or RENAMED from the accepted revision -----------
        # This is the case that made discovery-before-integrity fail open: the
        # lookup joined portfolio_node first, so deleting the row hid the very
        # record needed to notice the corruption and the answer came back
        # "ungoverned". A rename is exercised too, because it leaves the row
        # count intact and so passes any check that only counts. The row is
        # captured whole and restored verbatim, so neither case perturbs the
        # graph for the assertions that follow.
        probe_ref = "step:gate-milestone-09"
        for label, statement in (
            ("DELETED", "delete from ops.portfolio_node "
                        "where portfolio_revision_id = %(rev)s and node_ref = %(ref)s"),
            ("RENAMED", "update ops.portfolio_node set node_ref = 'step:gate-renamed-away' "
                        "where portfolio_revision_id = %(rev)s and node_ref = %(ref)s"),
        ):
            with psycopg.connect(dsn, autocommit=True) as t, t.cursor() as tc:
                tc.execute("select to_jsonb(n) from ops.portfolio_node n "
                           "where portfolio_revision_id = %s and node_ref = %s",
                           (revision_id, probe_ref))
                saved = one(tc)[0]
                tc.execute("set session_replication_role = replica")
                tc.execute(statement, {"rev": revision_id, "ref": probe_ref})
                tc.execute("set session_replication_role = origin")
            try:
                with psycopg.connect(dsn) as broken, broken.cursor() as bc:
                    ok, detail = refused(bc, "select ops.portfolio_descendant_binding(%s)",
                                         (probe_ref,), expect="failed integrity")
                    check(f"a node {label} from the accepted revision refuses, never downgrades",
                          ok, detail)
                    broken.rollback()
            finally:
                with psycopg.connect(dsn, autocommit=True) as t, t.cursor() as tc:
                    tc.execute("set session_replication_role = replica")
                    tc.execute("delete from ops.portfolio_node where portfolio_revision_id = %s "
                               "and node_ref in (%s, 'step:gate-renamed-away')",
                               (revision_id, probe_ref))
                    tc.execute("insert into ops.portfolio_node select * from "
                               "jsonb_populate_record(null::ops.portfolio_node, %s::jsonb)",
                               (json.dumps(saved),))
                    tc.execute("set session_replication_role = origin")
            with psycopg.connect(dsn) as verify, verify.cursor() as vc:
                vc.execute("select ops.portfolio_revision_integrity_error(%s)", (revision_id,))
                check(f"the revision is intact again after the {label} case",
                      one(vc)[0] is None)
                verify.rollback()

        cross_layer_parity(dsn, check)
        ambiguous_ancestor(dsn, portfolio, probe_ref, nodes, edges, children, sources, check)
    finally:
        committed.close()
        # The fixture had to be COMMITTED for separate connections to see it, and
        # these tables refuse DELETE by design. Removing it therefore needs the
        # trigger suspended for exactly this cleanup, on a disposable database,
        # so the gate leaves nothing behind for the next gate to trip over.
        with psycopg.connect(dsn, autocommit=True) as cleanup, cleanup.cursor() as cur:
            cur.execute("set session_replication_role = replica")
            try:
                cur.execute("delete from ops.portfolio_revision_acceptance_receipt a "
                            "using ops.portfolio_revision r "
                            "where a.portfolio_revision_id=r.id and r.portfolio_ref like %s", (portfolio + "%",))
                cur.execute("delete from ops.portfolio_revision_review v "
                            "using ops.portfolio_revision r "
                            "where v.portfolio_revision_id=r.id and r.portfolio_ref like %s", (portfolio + "%",))
                for table in ("portfolio_node_edge", "portfolio_node", "portfolio_child_revision"):
                    cur.execute(f"delete from ops.{table} t using ops.portfolio_revision r "
                                f"where t.portfolio_revision_id=r.id and r.portfolio_ref like %s",
                                (portfolio + "%",))
                cur.execute("delete from ops.portfolio_revision where portfolio_ref like %s",
                            (portfolio + "%",))
                cur.execute("delete from ops.sourced_work_request_plan_acceptance_receipt "
                            "where plan_id in (select id from ops.sourced_work_request_plan "
                            "where runbook_ref='doctrine:runbook#portfolio-gate-fixture')")
                cur.execute("delete from ops.sourced_work_request_plan "
                            "where runbook_ref='doctrine:runbook#portfolio-gate-fixture'")
            finally:
                cur.execute("set session_replication_role = origin")

    if failures:
        for item in failures:
            print(f"work-portfolio-local-pg-gate: {item}", file=sys.stderr)
        return fail(f"{len(failures)} boundary assertion(s) did not hold")
    print("work-portfolio-local-pg-gate: OK — direct writes denied to every role, authorship "
          "derived from the authenticated principal, reviewer cannot accept their own pass, "
          "stale digest refused, structure closed after acceptance, integrity failure refuses instead of downgrading, zero executable effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
