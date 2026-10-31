#!/usr/bin/env python3
"""Run the migration CI class against disposable PostgreSQL and offline fixtures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


@dataclass(frozen=True)
class Proof:
    file: str
    error: str
    log: str = ""
    env: dict[str, str] = field(default_factory=dict)
    optional: bool = False
    tail: int = 30
    csv: bool = False


ACCEPTANCE_ENV = {
    "CARR_V5_A03_DB_REQUIRED": "1",
    "CARR_DOC_CONVERSATION_DB_REQUIRED": "1",
    "CARR_SESSION_IDENTITY_DB_REQUIRED": "1",
    "DATABASE_URL": "{dsn}",
    "CARR_CORRESPONDENCE_DB_REQUIRED": "1",
    "CARR_RELATIONSHIP_DB_REQUIRED": "1",
    "CARR_VENDOR_DIRECTORY_DB_REQUIRED": "1",
    "CARR_DISPATCH_SPINE_DB_REQUIRED": "1",
    "CARR_RW02_DB_REQUIRED": "1",
    "CARR_A02_RULE_COVERAGE_DB_REQUIRED": "1",
    "CARR_AMEND_CLOSED_LOOP_DB_REQUIRED": "1",
    "CARR_MEETING_MODE_DB_REQUIRED": "1",
    "CARR_R03_DB_REQUIRED": "1",
}

PROOFS = (
    Proof(
        "mcp-server/test/fact-review-installed-postgres.test.mjs",
        "installed fact-review behavior regressed; reconcile the later function body with the earlier behavior and update the proof",
        log="migration-fact-review",
        env={"CARR_FACT_REVIEW_DATABASE_URL": "{dsn}"},
        tail=80,
    ),
    Proof(
        "mcp-server/test/find-rule-supersedes.test.mjs",
        "rule lookup or historical supersession SQL database proof failed",
        log="migration-rule-supersession",
        env={"CARR_RULE_TEST_DATABASE_URL": "{dsn}"},
    ),
    Proof(
        "mcp-server/test/catch-me-up-writer-route.test.mjs",
        "timeline verbs cannot read their views as carr_writer",
        log="migration-writer-read-route",
        env={"CARR_WRITER_READ_TEST_DATABASE_URL": "{dsn}"},
    ),
    Proof(
        "mcp-server/test/national-account-classification-correction.test.mjs",
        "the national-account correction or linked-work rollback proof failed",
        log="migration-national-account-correction",
        env={"CARR_NATIONAL_ACCOUNT_TEST_REQUIRED": "1"},
    ),
    Proof(
        "mcp-server/test/security-definer-search-path-postgres.sql",
        "SECURITY DEFINER PostgreSQL acceptance failed: mcp-server/test/security-definer-search-path-postgres.sql",
    ),
    Proof(
        "mcp-server/test/definer-temp-substitution-postgres.sql",
        "SECURITY DEFINER PostgreSQL acceptance failed: mcp-server/test/definer-temp-substitution-postgres.sql",
    ),
    Proof(
        "mcp-server/test/completion-tenant-barriers-postgres.sql",
        "SECURITY DEFINER PostgreSQL acceptance failed: mcp-server/test/completion-tenant-barriers-postgres.sql",
    ),
    Proof(
        "@definers",
        "SECURITY DEFINER PostgreSQL acceptance failed: definer-hardening-catalog or dot-security-definers (named above)",
    ),
    Proof(
        "mcp-server/test/invoice-tracker-transaction.test.mjs",
        "the registered invoice receipt and atomic rollback proof failed",
        env={"CARR_INVOICE_TEST_REQUIRED": "1", "CARR_INVOICE_TEST_DATABASE_URL": "{dsn}"},
    ),
    Proof(
        "mcp-server/test/tour-operations-slice2-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/tour-operations-slice2-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/tour-property-identity-jurisdiction-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/tour-property-identity-jurisdiction-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/tour-domain-route-cheat-sheet-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/tour-domain-route-cheat-sheet-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/tour-delivery-data-plane-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/tour-delivery-data-plane-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/tour-client-share-allowlist-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/tour-client-share-allowlist-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/assurance-health-store-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/assurance-health-store-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/work-portfolio-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/work-portfolio-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/local-deals-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/local-deals-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/invoice-tracker-postgres.sql",
        "Tour Operations PostgreSQL acceptance failed: mcp-server/test/invoice-tracker-postgres.sql",
        optional=True,
    ),
    Proof(
        "mcp-server/test/governed-correspondence-store-postgres.sql",
        "V5-J103 governed correspondence store PostgreSQL acceptance failed",
    ),
    Proof(
        "mcp-server/test/record-source-authority-live-pg.v5.test.mjs",
        "V5-F01 record-source-authority PostgreSQL acceptance failed",
        log="record-source-authority-live-pg",
        env={"PSQL": "{psql}", "CARR_F01_DB_REQUIRED": "1", "DATABASE_URL": "{dsn}"},
        optional=True,
        tail=40,
    ),
    Proof(
        "mcp-server/test/f01-login-bundle-membership-postgres.sql",
        "the F01 login-bundle membership proof (0732) failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/rule-context-runtime-postgres.test.mjs",
        "V5-F05 rule-context PostgreSQL acceptance failed",
        log="rule-context-runtime-postgres",
        env={"CARR_F05_DB_REQUIRED": "1", "DATABASE_URL": "{dsn}"},
        optional=True,
        tail=40,
    ),
    Proof("mcp-server/test/codex-continuity-postgres.sql", "Codex continuity PostgreSQL acceptance failed"),
    Proof(
        "mcp-server/test/program-controller-census-role-boundary.v5.test.mjs",
        "the program-controller seam connection-role boundary proof failed",
        log="program-controller-role-boundary",
        env={"DATABASE_URL": "{dsn}", "CARR_PROGRAM_CONTROLLER_DB_REQUIRED": "1"},
        optional=True,
    ),
    Proof(
        "mcp-server/test/program-controller-census-postgres.sql",
        "the program-controller seam catalog and ledger proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/doc-conversation.test.mjs",
        "the doc-conversation database acceptance proof failed",
        log="doc-conversation-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/notifications.test.mjs",
        "the notifications database acceptance proof failed",
        log="notifications-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/session-identity.test.mjs",
        "the session-identity database acceptance proof failed",
        log="session-identity-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/dispatch-spine.test.mjs",
        "the dispatch-spine database acceptance proof failed",
        log="dispatch-spine-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/governed-correspondence-thread-postgres.test.mjs",
        "the governed-correspondence-thread-postgres database acceptance proof failed",
        log="governed-correspondence-thread-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/meeting-mode.test.mjs",
        "the meeting-mode database acceptance proof failed",
        log="meeting-mode-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/amend-closed-loop-postgres.test.mjs",
        "the amend-closed-loop-postgres database acceptance proof failed",
        log="amend-closed-loop-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/independent-review-cycle-postgres.test.mjs",
        "the independent-review-cycle-postgres database acceptance proof failed",
        log="independent-review-cycle-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/a02-rule-enforcement-postgres.test.mjs",
        "the a02-rule-enforcement-postgres database acceptance proof failed",
        log="a02-rule-enforcement-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/salesforce-reconciliation-rw02-postgres.test.mjs",
        "the salesforce-reconciliation-rw02-postgres database acceptance proof failed",
        log="salesforce-reconciliation-rw02-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/salesforce-read-run-store-rw02-postgres.test.mjs",
        "the salesforce-read-run-store-rw02-postgres database acceptance proof failed",
        log="salesforce-read-run-store-rw02-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/vendor-directory-postgres.test.mjs",
        "the vendor-directory-postgres database acceptance proof failed",
        log="vendor-directory-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/relationship-network-postgres.test.mjs",
        "the relationship-network-postgres database acceptance proof failed",
        log="relationship-network-postgres-db",
        env=ACCEPTANCE_ENV,
        optional=True,
    ),
    Proof(
        "mcp-server/test/doc-conversation-postgres.sql",
        "the Doc conversation attribution and access-list proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/doc-conversation-write-doors-postgres.sql",
        "the Doc conversation write-door creator and grant proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/doc-conversation-list-postgres.sql",
        "the Doc conversation list attribution, paging and grant proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/r03-notifications-postgres.sql",
        "the R03 notification mint, recipient and status proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/r03-loop-queue-postgres.sql",
        "the R03 loop producer and dedupe proof failed",
        optional=True,
        csv=True,
    ),
    Proof(
        "mcp-server/test/notification-preferences-postgres.sql",
        "the notification preference read, write and quiet-hours proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/session-identity-postgres.sql",
        "the session identity projection, filter and dispatch proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/dispatch-spine-postgres.sql",
        "the dispatch spine link, acknowledgement and per-dispatch reason proof failed",
        optional=True,
    ),
    Proof(
        "mcp-server/test/foundation-assurance-minimum-postgres.sql",
        "the foundation-assurance candidate/evidence atomic binding proof failed",
        optional=True,
    ),
)


GRANTS = """
    do $$ begin
      if not has_table_privilege('carr_writer', 'public.lead', 'insert') then
        raise exception 'carr_writer cannot insert into lead (the PR #75 case)'; end if;
      if not has_schema_privilege('carr_jobs', 'ops', 'usage') then
        raise exception 'carr_jobs lacks usage on schema ops (0115)'; end if;
      if not has_column_privilege('carr_jobs', 'ops.incident', 'state', 'update') then
        raise exception 'carr_jobs cannot update incident.state (0117)'; end if;
      if has_column_privilege('carr_jobs', 'ops.incident', 'resolved_at', 'update') then
        raise exception 'carr_jobs can update incident.resolved_at — 0117 interlock broken'; end if;
      if not has_function_privilege('carr_reader', 'public.state_as_of(text, uuid, timestamptz)', 'execute') then
        raise exception 'carr_reader cannot execute state_as_of (0106)'; end if;
      if not pg_has_role('carr_exporter', 'carr_reader', 'member') then
        raise exception 'carr_exporter lost its carr_reader bundle (0006)'; end if;
    end $$;"""


class MigrationFailure(Exception):
    pass


class Migration:
    def __init__(self, args):
        self.logdir = Path(args.logdir)
        self.python = args.python
        self.result = Path(args.result)
        self.part = args.part
        self.strict = args.strict
        self.prior_failed = args.prior_failed
        self.dsn = os.environ.get("CARR_CI_DATABASE_URL", "")
        self.psql = ""
        self.timings = []
        self.last_step = int(time.time())

    def emit(self, kind, text):
        with self.result.open("a") as result:
            result.write(f"{kind}\t{text}\n")

    def step(self, name):
        now = int(time.time())
        self.timings.append(f"{name}={now - self.last_step}s")
        self.last_step = now

    def run(self, name, command, env=None, record=True):
        log = self.logdir / f"{name}.log"
        start = time.time()
        with log.open("wb") as output:
            try:
                rc = subprocess.run(
                    command, env=dict(os.environ, **(env or {})), stdout=output, stderr=subprocess.STDOUT
                ).returncode
            except OSError as error:
                output.write(f"{command[0]}: {error.strerror}\n".encode())
                rc = 127
        if record:
            Path(str(log) + ".rc").write_text(f"{rc}\n")
            with (self.logdir / "programs.tsv").open("a") as records:
                records.write(f"migration\t{name}\t{rc}\t{start:.6f}\t{time.time():.6f}\n")
        return rc

    def fail(self, message, name=None, tail=30):
        if name:
            lines = (self.logdir / f"{name}.log").read_bytes().splitlines(keepends=True)
            sys.stderr.buffer.write(b"".join(lines if tail is None else lines[-tail:]))
            sys.stderr.flush()
        raise MigrationFailure(message)

    def checked(self, name, command, message, env=None, tail=30):
        if self.run(name, command, env):
            self.fail(message, name, tail)

    def py(self, name, script, message, *args, env=None, tail=30):
        self.checked(name, [self.python, script, *args], message, env, tail)

    def fixtures(self):
        if self.part in ("all", "fixtures", "reconstruction"):
            self.py(
                "migration-reconstruction",
                "ops/schema-reconstruction-selftest.py",
                "snapshot reconstruction did not reach the exact source migration ledger",
                "--postgres",
            )
        if self.part in ("all", "fixtures", "equivalence"):
            self.py(
                "migration-production-proof-fixture",
                "ops/migration-production-proof-selftest.py",
                "production equivalence proof failed against the offline database fixture",
                "--postgres",
            )
            self.py(
                "migration-restore-security",
                "ops/restore-rehearse-record-selftest.py",
                "restore security parity failed against the disposable PostgreSQL fixture",
                "--postgres",
            )
        note = ""
        if self.part in ("all", "fixtures", "shadow"):
            command = [self.python, "ops/migration-shadow.py"]
            if os.environ.get("CARR_SHADOW_ARTIFACT_DIR"):
                command += ["--artifact-dir", os.environ["CARR_SHADOW_ARTIFACT_DIR"]]
            rc = self.run("migration-shadow", command, record=False)
            if rc == 0:
                self.py(
                    "schema-snapshot-rebuild",
                    "ops/schema-snapshot-rebuild-test.py",
                    "db/schema.sql loses an applied seed update during snapshot refresh and rebuild",
                )
                text = (self.logdir / "migration-shadow.log").read_text()
                found = re.search(r"^migration-shadow: ([0-9]* pending migration\(s\) applied).*", text, re.M)
                note = f'; {found[1] if found else ""} on a PostgreSQL 18 shadow; refreshed snapshot restores with reviewed seeds intact'
            elif rc == 3:
                if self.strict:
                    self.fail(
                        "the migration shadow needs PostgreSQL 18 server binaries (CI installs postgresql-18)",
                        "migration-shadow",
                        None,
                    )
                note = "; migration shadow not run (no PostgreSQL 18 here)"
            else:
                self.fail(
                    "migration shadow: pending migrations failed, or a changed db/schema.sql does not match their result",
                    "migration-shadow",
                    60,
                )
        self.emit("ok", f"migration fixture partition {self.part} passes{note}")

    def proofs(self):
        for proof in PROOFS:
            if proof.file == "@definers":
                command = [
                    self.python,
                    "ops/ci-migration-gates.py",
                    "--logdir",
                    str(self.logdir),
                    "--psql",
                    self.psql,
                    "--sql-proof",
                    "mcp-server/test/definer-hardening-catalog-postgres.sql",
                    "--sql-proof",
                    "mcp-server/test/dot-security-definers-postgres.sql:"
                    + (os.environ.get("CARR_DOT_PROOF_SHARDS") or "3"),
                ]
                if subprocess.run(command, env=dict(os.environ, DATABASE_URL=self.dsn)).returncode:
                    self.fail(proof.error)
                continue
            if proof.optional and not Path(proof.file).is_file():
                continue
            if proof.file.endswith(".sql"):
                command = [self.psql, "-X", "-v", "ON_ERROR_STOP=1"]
                if proof.csv:
                    command += ["-P", "format=csv"]
                command += ["-d", self.dsn, "-f", proof.file]
            else:
                command = ["node", "--test", proof.file]
            env = {key: value.format(dsn=self.dsn, psql=self.psql) for key, value in proof.env.items()}
            name = proof.log or Path(proof.file).name.removesuffix(".sql").removesuffix(".test.mjs")
            self.checked(name, command, proof.error, env, proof.tail)
            if proof.file.endswith("national-account-classification-correction.test.mjs"):
                self.step("migrate")
            if proof.csv and not report_health((self.logdir / f"{name}.log").read_text()):
                self.fail("R03 standing report conflates historical and unresolved failures", name)

    def gates(self):
        self.step("sync")
        gates, unmarked, declared = [], [], []
        dsn_read = re.compile(
            r'environ\.get\("(CARR_CI_)?DATABASE_URL"|environ\["(CARR_CI_)?DATABASE_URL"\]|getenv\("(CARR_CI_)?DATABASE_URL"'
        )
        for gate in sorted(Path("ops").glob("*-gate.py")):
            if not gate.is_file():
                continue
            text = gate.read_text()
            if re.search(r"^# ci: db-gate", text, re.M):
                gates.append(str(gate))
            elif dsn_read.search(text):
                unmarked.append(gate.name)
            elif re.search(r"^# ci: runs-outside-ci", text, re.M):
                why = re.search(r"^# ci: runs-outside-ci *— *(.*)", text, re.M)
                declared.append(f'            {gate.name}: {why[1] if why else ""}')
        rc = subprocess.run(
            [self.python, "ops/ci-migration-gates.py", "--logdir", str(self.logdir), *gates],
            env=dict(os.environ, DATABASE_URL=self.dsn),
        ).returncode
        self.step("gates")
        if declared:
            print(
                "        \033[33moutside CI\033[0m  gate(s) declared to run elsewhere:\n"
                + "\n".join(declared),
                file=sys.stderr,
            )
        if unmarked:
            self.fail(
                "gate(s) read a database URL but carry no `# ci: db-gate` marker, so nothing runs them: "
                + " ".join(unmarked)
                + " (add the marker, or declare `# ci: runs-outside-ci — why`)"
            )
        if rc:
            self.fail(
                "database acceptance gate(s) failed:isolated/serial DB gate runner (see named failures above)"
            )

    def execute(self):
        if self.part in ("fixtures", "reconstruction", "equivalence", "shadow"):
            self.fixtures()
            return
        self.py(
            "migration-open-pr",
            "ops/migration-open-pr-gate.py",
            "an earlier PR or main owns a migration slot; choose a forward filename and update its references",
            tail=None,
        )
        if self.part in ("all", "core", "proofs"):
            self.py(
                "migration-baseline",
                "-m",
                "migration baseline identity or historical source checks failed before database work",
                "unittest",
                "discover",
                "-s",
                "tools",
                "-p",
                "test_migr*.py",
            )
        self.py(
            "migration-structure",
            "ops/migration-order-gate.py",
            "migration ordering or slot collision failed before database work; reserve a forward number",
            tail=15,
        )
        if not self.dsn:
            self.emit("skip", "no CARR_CI_DATABASE_URL (CI provides a throwaway Postgres)")
            return
        if not any(
            fnmatch.fnmatchcase(self.dsn, pattern)
            for pattern in ("*@localhost:*", "*@localhost/*", "*@127.0.0.1:*", "*@127.0.0.1/*")
        ):
            self.fail(
                "REFUSED: CARR_CI_DATABASE_URL is not loopback. This applies every migration; it runs against a throwaway only."
            )
        self.psql = next(
            (
                name
                for name in ("psql", "/opt/homebrew/opt/libpq/bin/psql", "/usr/local/opt/libpq/bin/psql")
                if shutil.which(name)
            ),
            "",
        )
        if not self.psql:
            self.emit("skip", "no psql on PATH to load db/schema.sql")
            return
        if not Path("db/schema.sql").is_file():
            self.emit("skip", "db/schema.sql absent — regenerate with bin/schema-snapshot.sh")
            return
        self.last_step = int(time.time())
        if self.run(
            "migration-load",
            [self.psql, "-v", "ON_ERROR_STOP=1", "-q", "-d", self.dsn, "-f", "db/schema.sql"],
            {"PGOPTIONS": "--client-min-messages=warning"},
        ):
            message = "db/schema.sql did not load — the committed structure is not loadable"
            if "already exists" in (self.logdir / "migration-load.log").read_text():
                message = (
                    "this database already carries the schema — it is not a fresh one. "
                    "This class loads db/schema.sql from scratch, so it needs a database nobody has loaded yet. "
                    "The supported lane builds and removes one for you: ./run.sh local-db-ci --class migration "
                    "(add --port N if the default is taken by another session)."
                )
            self.fail(message, "migration-load", 25)
        self.step("load")
        self.py(
            "migration",
            "tools/migrate.py",
            "a pending migration did not apply to the committed schema",
            "--apply",
            "--yes",
            env={"DATABASE_URL": self.dsn},
        )
        if self.part not in ("gates", "acceptance"):
            self.proofs()
        self.step("tour")
        self.checked(
            "migration-grants",
            [self.psql, "-X", "-v", "ON_ERROR_STOP=1", "-Atq", "-d", self.dsn, "-c", GRANTS],
            "the app roles' grants did not survive into the built database",
            tail=15,
        )
        n = sum(
            line.startswith("applying ") for line in (self.logdir / "migration.log").read_text().splitlines()
        )
        self.step("grants")
        self.py(
            "migration-triggers",
            "ops/trigger-grant-check.py",
            "a trigger reads a table its firing role cannot select",
            env={"CARR_CI_DATABASE_URL": self.dsn},
            tail=25,
        )
        self.step("triggers")
        self.py(
            "migration-control-plane-sync",
            "tools/workflow-registry.py",
            "control-plane registry sync failed after migrations",
            "sync",
            env={"DATABASE_URL": self.dsn},
        )
        self.py(
            "migration-service-registry-sync",
            "tools/ops-record.py",
            "service registry sync failed after migrations",
            "sync-registry",
            env={"DATABASE_URL": self.dsn},
        )
        if self.part not in ("proofs", "acceptance"):
            self.gates()
        self.emit("timing", "migration-step-timing: " + " ".join(self.timings))
        if self.part == "all":
            self.fixtures()
            if self.prior_failed:
                return
        self.emit(
            "ok",
            f"committed schema loads; {n} pending migration(s) apply; app-role grants verified live; trigger reads granted; partition {self.part} passes (programs report their own assertions)",
        )


def report_health(text):
    phase, results = "", {}
    for line in text.splitlines():
        if line in ("r03_recovered_health", "r03_actor_mismatch_health"):
            phase = line
        elif phase and line.split(",")[0] == "joe":
            fields = line.split(",")
            numbers = (
                [3, 3, 3, 0, 0, 0, 1, 0] if phase == "r03_recovered_health" else [4, 3, 3, 1, 1, 1, 1, 0]
            )
            try:
                values = [float(fields[i]) for i in (1, 2, 3, 5, 6, 7, 8, 9)]
            except (ValueError, IndexError):
                values = []
            results[phase] = (
                len(fields) >= 15
                and values == numbers
                and fields[14].startswith("No open" if phase == "r03_recovered_health" else "On missing")
            )
            phase = ""
    return all(results.get(name) for name in ("r03_recovered_health", "r03_actor_mismatch_health"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logdir", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--python", default=os.environ.get("CARR_CI_PYTHON", sys.executable))
    parser.add_argument(
        "--part",
        choices=(
            "all",
            "core",
            "proofs",
            "gates",
            "acceptance",
            "fixtures",
            "reconstruction",
            "equivalence",
            "shadow",
        ),
        default="all",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--prior-failed", type=int, default=0)
    args = parser.parse_args()
    runner = Migration(args)
    try:
        runner.execute()
    except MigrationFailure as error:
        runner.emit("bad", str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
