#!/usr/bin/env python3
# ci: db-gate
"""Disposable local-PG adversarial proof for calendar-prebrief projection 0229."""
import os
import uuid
from datetime import datetime, timedelta
from threading import Thread
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb


def required(cur: psycopg.Cursor[Any], label: str) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return tuple(row)


def refused(cur: psycopg.Cursor[Any], sql: str, args: tuple[Any, ...], state: str, text: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(sql, args)
        raise RuntimeError(f"expected refusal was accepted: {text}")
    except psycopg.Error as exc:
        if exc.sqlstate != state or text not in str(exc):
            raise
        cur.execute("rollback to savepoint expected_refusal")


def job(cur: psycopg.Cursor[Any], definition_key: str, lease: uuid.UUID, scheduled_offset: str = "0 seconds",
        mode: str = "live") -> tuple[uuid.UUID, datetime]:
    identifier = uuid.uuid4()
    cur.execute(
        """insert into ops.job
             (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
              attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds)
           values (%s,%s,1,%s,now()+(%s)::interval,%s,'running',1,1,now(),
                   'calendar-prebrief-local',%s,now()+interval '5 minutes',60)
           returning id,scheduled_for""",
        (identifier, definition_key, str(identifier), scheduled_offset, mode, lease),
    )
    row = required(cur, "calendar prebrief job")
    return row[0], row[1]


def payload_data(calendar_key: str, occurrence_key: str, participant_ref: str, scheduled_for: datetime,
                 *, title: str = "Prebrief acceptance", location: str = "CARR") -> list[dict[str, Any]]:
    starts = scheduled_for + timedelta(hours=1)
    ends = starts + timedelta(hours=1)
    return [{
        "calendar_key": calendar_key,
        "event_key": "e" * 64,
        "occurrence_key": occurrence_key,
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "title": title,
        "location": location,
        "participant_refs": [participant_ref],
    }]


def ingest_sql() -> str:
    return "select (ops.ingest_calendar_prebrief_projection(%s,%s,%s,%s)).id"


def attest_sql() -> str:
    return "select (ops.record_calendar_prebrief_verified_envelope(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).id"


def capture_contract(cur: psycopg.Cursor[Any], sponsor: str, job_id: uuid.UUID, lease: uuid.UUID) -> tuple[Any, ...]:
    cur.execute(f"set session authorization carr_calendar_prebrief_resolver_{sponsor}")
    cur.execute("select * from ops.issue_calendar_prebrief_capture_contract(%s,%s)", (job_id, lease))
    row = required(cur, "DB-issued capture contract")
    cur.execute("reset session authorization")
    return row


def envelope_args(cur: psycopg.Cursor[Any], sponsor: str, args: tuple[Any, ...], destination: str | None = None,
                  fingerprint: str = "f" * 64, signature: str = "d" * 64, version: str = "eventkit-1.0",
                  contract: tuple[Any, ...] | None = None) -> tuple[Any, ...]:
    job_id, lease, observed, events = args
    contract = contract or capture_contract(cur, sponsor, job_id, lease)
    challenge, contract_sponsor, contract_job, attempt, contract_lease, scheduled, window_start, window_end, mode, contract_destination, revision, digest, keys = contract
    if contract_sponsor != sponsor or contract_job != job_id or contract_lease != lease:
        raise RuntimeError("capture contract did not bind exact sponsor/job/lease")
    return (job_id, lease, challenge, scheduled, window_start, window_end, revision, digest, keys, observed, events,
            contract_destination if destination is None else destination, fingerprint, signature, version)


def canary_ingest_sql() -> str:
    return "select (ops.ingest_calendar_prebrief_canary_projection(%s,%s,%s,%s,%s)).id"


def attest(cur: psycopg.Cursor[Any], sponsor: str, args: tuple[Any, ...], destination: str = "live",
           fingerprint: str = "f" * 64, signature: str | None = None, version: str = "eventkit-1.0") -> uuid.UUID:
    # A signature identifies one signed envelope globally.  Fresh test jobs
    # therefore need a fresh digest; exact replay deliberately reuses one.
    signature = signature or uuid.uuid4().hex + uuid.uuid4().hex
    signed = envelope_args(cur, sponsor, args, destination, fingerprint, signature, version)
    return attest_signed(cur, sponsor, signed)


def attest_signed(cur: psycopg.Cursor[Any], sponsor: str, signed: tuple[Any, ...]) -> uuid.UUID:
    cur.execute(f"set session authorization carr_calendar_prebrief_attestor_{sponsor}")
    cur.execute(attest_sql(), signed)
    receipt = required(cur, "verified source envelope receipt")[0]
    cur.execute("reset session authorization")
    return receipt


def attested_ingest(cur: psycopg.Cursor[Any], sponsor: str, args: tuple[Any, ...]) -> uuid.UUID:
    attest(cur, sponsor, args)
    cur.execute(f"set session authorization carr_calendar_prebrief_{sponsor}")
    cur.execute(ingest_sql(), args)
    receipt = required(cur, "attested projection receipt")[0]
    cur.execute("reset session authorization")
    return receipt


dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
parsed = urlparse(dsn)
if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("calendar prebrief projection acceptance requires loopback CARR_LOCAL_PG_DSN or DATABASE_URL")

with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("select current_database(),current_user,current_setting('data_directory'),(select rolsuper from pg_roles where rolname=current_user)")
    database_name, role_name, data_directory, is_superuser = required(cur, "local cluster identity")
    data_path = os.path.realpath(str(data_directory))
    local_disposable = (os.path.isfile(os.path.join(data_path, "PG_VERSION"))
                        and os.path.basename(os.path.dirname(data_path)).startswith("carr-local-pg-ci."))
    hosted_disposable = (os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("CI") == "true"
                         and os.environ.get("CARR_CI_PORTABLE_ONLY") == "1"
                         and os.environ.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
                         and bool(os.environ.get("GITHUB_RUN_ID"))
                         and data_path == "/var/lib/postgresql/data")
    if database_name != "carr_ci" or role_name != "carr_ci" or is_superuser is not True or not (local_disposable or hosted_disposable):
        raise RuntimeError("calendar prebrief acceptance requires a dedicated disposable carr_ci database")
    cur.execute("begin")
    cur.execute(
        """do $$ begin
             if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_joe') then create role carr_calendar_prebrief_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_dell') then create role carr_calendar_prebrief_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_canary_joe') then create role carr_calendar_prebrief_canary_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_canary_dell') then create role carr_calendar_prebrief_canary_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_other') then create role carr_calendar_prebrief_other login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_attestor_joe') then create role carr_calendar_prebrief_attestor_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_attestor_dell') then create role carr_calendar_prebrief_attestor_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_attestor_other') then create role carr_calendar_prebrief_attestor_other login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_resolver_joe') then create role carr_calendar_prebrief_resolver_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_resolver_dell') then create role carr_calendar_prebrief_resolver_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_resolver_other') then create role carr_calendar_prebrief_resolver_other login; end if;
           end $$"""
    )
    cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
    cur.execute("grant carr_calendar_prebrief_jobs to carr_calendar_prebrief_joe,carr_calendar_prebrief_dell,carr_calendar_prebrief_other")
    cur.execute("grant carr_calendar_prebrief_canary_jobs to carr_calendar_prebrief_canary_joe,carr_calendar_prebrief_canary_dell")
    cur.execute("grant carr_calendar_prebrief_attestors to carr_calendar_prebrief_attestor_joe,carr_calendar_prebrief_attestor_dell,carr_calendar_prebrief_attestor_other")
    cur.execute("grant carr_calendar_prebrief_email_resolver to carr_calendar_prebrief_resolver_joe,carr_calendar_prebrief_resolver_dell,carr_calendar_prebrief_resolver_other")
    cur.execute(
        """select key,owner_actor,inventory_contract->>'owner',enabled,recurrence->>'cron',legacy_schedule->>'status'
             from ops.job_definition
             where key in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily') and version=1 order by key"""
    )
    expected_defs = [
        ("calendar-prebrief-projection-dell-daily", "dell", "dell", False, "30 6 * * 1-5", "disabled"),
        ("calendar-prebrief-projection-joe-daily", "joe", "joe", False, "30 6 * * 1-5", "disabled"),
    ]
    actual_defs = cur.fetchall()
    if actual_defs != expected_defs:
        raise RuntimeError(f"calendar prebrief definitions must remain separate, disabled, owner-matched, and before 06:45: {actual_defs!r}")

    # Authority may replace only its own opaque set.  Neither source IDs nor a
    # caller-selected sponsor make the function boundary.
    joe_allowed, dell_allowed, alien = "a" * 64, "b" * 64, "c" * 64
    cur.execute("set session authorization carr_authority_joe")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).sponsor", ([joe_allowed],))
    if required(cur, "Joe allowlist receipt") != ("joe",):
        raise RuntimeError("allowlist did not derive Joe from authority identity")
    refused(cur, "select ops.replace_calendar_prebrief_allowlist(%s)", ([],), "22023", "nonempty distinct opaque")
    refused(cur, "select ops.replace_calendar_prebrief_allowlist(%s)", ([joe_allowed, joe_allowed],), "22023", "nonempty distinct opaque")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).sponsor", ([dell_allowed],))
    if required(cur, "Dell allowlist receipt") != ("dell",):
        raise RuntimeError("allowlist did not derive Dell from authority identity")
    cur.execute("reset session authorization")
    cur.execute("select active_revision_id from ops.calendar_prebrief_allowed_calendar where sponsor='dell'")
    dell_revision_a = required(cur, "initial Dell allowlist revision")[0]

    cur.execute("select id from actor where active order by slug limit 1")
    actor = required(cur, "active acceptance actor")[0]
    cur.execute("select slug from client_status order by sort limit 1")
    status = required(cur, "acceptance client status")[0]
    refs: list[str] = []
    parties: list[uuid.UUID] = []
    for suffix in ("one", "two"):
        ref = f"C-CALENDAR-PREBREF-{suffix.upper()}-{uuid.uuid4().hex[:12]}"
        cur.execute("insert into party(kind,name,created_by,updated_by) values('person',%s,%s,%s) returning id",
                    (f"Calendar Prebrief {suffix}", actor, actor))
        party = required(cur, "acceptance party")[0]
        cur.execute("insert into client(roster_ref,party_id,status,created_by,updated_by) values(%s,%s,%s,%s,%s)",
                    (ref, party, status, actor, actor))
        refs.append(ref)
        parties.append(party)
    ref, second_ref = refs
    cur.execute("update party set email=%s where id=%s", ("prebrief-exact@example.test", parties[0]))
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    cur.execute("select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",))
    if required(cur, "ephemeral exact email resolver") != (ref,):
        raise RuntimeError("device resolver did not return the one live canonical ref")
    # 0735: an address no party row carries is skipped and counted by
    # the caller, so the resolver answers NULL instead of refusing the snapshot.
    cur.execute("select ops.resolve_calendar_prebrief_email_ref(%s)", ("unknown@example.test",))
    if required(cur, "ephemeral unknown email resolver") != (None,):
        raise RuntimeError("device resolver must answer NULL for an address the record does not hold")
    cur.execute("reset session authorization")
    # 0735: "unknown" is decided at the party level. A person the record holds
    # without a canonical ref yet is known, not a stranger, so it still refuses.
    cur.execute("insert into party(kind,name,email,created_by,updated_by) values('person',%s,%s,%s,%s)",
                ("Calendar Prebrief pending", "prebrief-pending@example.test", actor, actor))
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-pending@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    # Two live party rows share the address and only one carries a ref: that is
    # two live contacts, so it refuses rather than attributing to the one with
    # a ref. The twin is soft-deleted again before the later cases.
    cur.execute("insert into party(kind,name,email,created_by,updated_by) values('person',%s,%s,%s,%s) returning id",
                ("Calendar Prebrief twin", "prebrief-exact@example.test", actor, actor))
    twin = required(cur, "ref-less twin party")[0]
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    # The twin stays live and ref-less while the ref-bearing party is
    # tombstoned: the tombstone's live client ref must not answer for it.
    cur.execute("update party set deleted_at=now() where id=%s", (parties[0],))
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    cur.execute("update party set deleted_at=now() where id=%s", (twin,))
    # A soft-deleted party whose client row is still live is a tombstoned
    # identity, not a live contact: alone, it refuses too.
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    cur.execute("update party set deleted_at=null where id=%s", (parties[0],))
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "42501", "permission denied")
    cur.execute("reset session authorization")
    joe_key, dell_key = "calendar-prebrief-projection-joe-daily", "calendar-prebrief-projection-dell-daily"
    first_lease = uuid.uuid4()
    first_job, first_snapshot = job(cur, joe_key, first_lease)
    first_events = Jsonb(payload_data(joe_allowed, "1" * 64, ref, first_snapshot))
    first_args = (first_job, first_lease, [joe_allowed], first_events)
    first_signed = envelope_args(cur, "joe", first_args)

    # The device receipt is mandatory even for an empty snapshot: otherwise a
    # jobs credential could claim false coverage and prune the current view.
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), first_args, "55000", "exact immutable verified source envelope")
    refused(cur, attest_sql(), first_signed, "42501", "permission denied")
    cur.execute("reset session authorization")
    attest_signed(cur, "joe", first_signed)
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    cur.execute(ingest_sql(), first_args)
    first_receipt = required(cur, "first attested projection receipt")[0]
    cur.execute("reset session authorization")
    if attest_signed(cur, "joe", first_signed) is None:
        raise RuntimeError("exact signed envelope replay returned no source receipt")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    cur.execute(ingest_sql(), first_args)
    if required(cur, "exact job-attempt projection replay")[0] != first_receipt:
        raise RuntimeError("exact job-attempt replay did not return its immutable receipt")
    cur.execute("reset session authorization")
    false_empty_lease = uuid.uuid4()
    false_empty_job, _ = job(cur, joe_key, false_empty_lease, "1 second")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (false_empty_job, false_empty_lease, [joe_allowed], Jsonb([])), "55000", "exact immutable verified source envelope")
    cur.execute("reset session authorization")
    cur.execute("select count(*) from ops.calendar_prebrief_projection_event where sponsor='joe'")
    if required(cur, "false-empty refusal preserves live projection") != (1,):
        raise RuntimeError("an unattested false empty snapshot pruned the live projection")
    false_empty_bad_signed = envelope_args(cur, "joe", (false_empty_job, false_empty_lease, [alien], Jsonb([])))
    cur.execute("set session authorization carr_calendar_prebrief_attestor_joe")
    refused(cur, attest_sql(), false_empty_bad_signed, "22023", "exact signed allowlist coverage")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_attestor_joe")
    refused(cur, attest_sql(), (false_empty_job, false_empty_lease) + first_signed[2:], "42501", "signed claim does not match")
    cur.execute("reset session authorization")
    first_signed_for_dell = envelope_args(cur, "joe", first_args)
    cur.execute("set session authorization carr_calendar_prebrief_attestor_dell")
    refused(cur, attest_sql(), first_signed_for_dell, "42501", "signed claim does not match")
    cur.execute("reset session authorization")
    first_signed_for_other = envelope_args(cur, "joe", first_args)
    cur.execute("set session authorization carr_calendar_prebrief_attestor_other")
    refused(cur, attest_sql(), first_signed_for_other, "42501", "exact sponsor-bound verifier identity")
    cur.execute("reset session authorization")
    altered_events = Jsonb(payload_data(joe_allowed, "7" * 64, ref, first_snapshot))
    altered_signed = envelope_args(cur, "joe", (first_job, first_lease, [joe_allowed], altered_events))
    cur.execute("set session authorization carr_calendar_prebrief_attestor_joe")
    refused(cur, attest_sql(), altered_signed, "23505", "replay conflicts with immutable attempt")
    refused(cur, attest_sql(), first_signed[:-2] + ("e" * 64, first_signed[-1]), "23505", "replay conflicts with immutable attempt")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, attest_sql(), first_signed, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_other")
    refused(cur, ingest_sql(), first_args, "42501", "named externally provisioned execution identity")
    cur.execute("reset session authorization")
    with psycopg.connect(dsn) as race_conn, race_conn.cursor() as race_cur:
        race_cur.execute("select pg_try_advisory_xact_lock(hashtextextended('calendar-prebrief-projection:joe',0))")
        if required(race_cur, "advisory race fence") != (False,):
            raise RuntimeError("sponsor replacement is not serialized by its advisory race fence")
        race_conn.rollback()
    cur.execute("select job_id,attempt,snapshot_at,event_count,participant_count from ops.calendar_prebrief_projection_receipt where id=%s", (first_receipt,))
    receipt_row = required(cur, "first immutable receipt")
    if receipt_row[:2] != (first_job, 1) or receipt_row[2] != first_snapshot or receipt_row[3:] != (1, 1):
        raise RuntimeError("receipt is not bound to job attempt, DB-derived snapshot, and counts")

    # P0 failures: wrong identity/lease/clock/allowlist cannot reach replacement.
    dell_lease = uuid.uuid4(); dell_job, dell_snapshot = job(cur, dell_key, dell_lease, "1 second")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (dell_job, dell_lease, [dell_allowed], Jsonb(payload_data(dell_allowed, "2" * 64, second_ref, dell_snapshot))), "42501", "does not match the static job owner")
    refused(cur, ingest_sql(), (first_job, uuid.uuid4(), [joe_allowed], first_events), "55000", "current live job lease")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_dell")
    refused(cur, ingest_sql(), first_args, "42501", "does not match the static job owner")
    cur.execute("reset session authorization")
    far_lease = uuid.uuid4(); far_job, far_snapshot = job(cur, joe_key, far_lease, "31 minutes")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (far_job, far_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, "3" * 64, ref, far_snapshot))), "22023", "DB-clock window")
    refused(cur, ingest_sql(), (first_job, first_lease, [alien], first_events), "22023", "exact DB allowlist coverage")

    # P1 content-shape failures: all are evaluated before any current rows move.
    duplicate_refs = payload_data(joe_allowed, "4" * 64, ref, first_snapshot)
    duplicate_refs[0]["participant_refs"] = [ref, ref]
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(duplicate_refs)), "22023", "duplicate participant refs")
    for index, unsafe in enumerate(("zoommtg://private", "teams:meeting", "sip:private@example.org", "www.private.example", "join meeting id 123456"), start=5):
        refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, f"{index:x}".zfill(64), ref, first_snapshot, title=unsafe))), "22023", "email, URI, www, or join locator")
    unexpected = payload_data(joe_allowed, "a" * 64, ref, first_snapshot)
    unexpected[0]["description"] = "not permitted"
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(unexpected)), "22023", "outside its bounded contract")
    unresolved = payload_data(joe_allowed, "b" * 64, "P-NOT-RESOLVED", first_snapshot)
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(unresolved)), "22023", "does not resolve uniquely")
    too_many = [payload_data(joe_allowed, f"{index:x}".zfill(64), ref, first_snapshot)[0] for index in range(129)]
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(too_many)), "22023", "event count exceeds")
    cur.execute("reset session authorization")
    cur.execute("select count(*) from ops.calendar_prebrief_projection_event where sponsor='joe'")
    if required(cur, "projection retained after all prevalidation failures") != (1,):
        raise RuntimeError("failed prevalidation changed Joe's current projection")

    # A later current projection makes a near-past scheduled job stale; empty
    # snapshots are valid and atomically prune only that sponsor's current rows.
    dell_events = payload_data(dell_allowed, "d" * 64, second_ref, dell_snapshot)
    no_participant = payload_data(dell_allowed, "e" * 64, second_ref, dell_snapshot)[0]
    no_participant["participant_refs"] = []
    attested_ingest(cur, "dell", (dell_job, dell_lease, [dell_allowed], Jsonb(dell_events + [no_participant])))
    dell_canary_lease = uuid.uuid4()
    dell_canary_job, dell_canary_snapshot = job(cur, "calendar-prebrief-canary-dell-daily", dell_canary_lease, "2 seconds", mode="canary")
    dell_canary_events = Jsonb(payload_data(dell_allowed, "c" * 64, second_ref, dell_canary_snapshot))
    attest(cur, "dell", (dell_canary_job, dell_canary_lease, [dell_allowed], dell_canary_events), "calendar-prebrief-canary-dell")
    cur.execute("set session authorization carr_calendar_prebrief_canary_dell")
    cur.execute(canary_ingest_sql(), (dell_canary_job, dell_canary_lease, "calendar-prebrief-canary-dell", [dell_allowed], dell_canary_events))
    required(cur, "Dell isolated canary receipt")
    cur.execute("reset session authorization")
    # Raw email exists only in this device call.  Duplicate and tombstoned
    # matches refuse rather than selecting an arbitrary participant ref.
    cur.execute("update party set email=%s where id=%s", ("prebrief-exact@example.test", parties[1]))
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-exact@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    cur.execute("update party set email=%s where id=%s", ("prebrief-merged@example.test", parties[1]))
    cur.execute("update client set merged_into=(select id from client where roster_ref=%s) where roster_ref=%s", (ref, second_ref))
    cur.execute("set session authorization carr_calendar_prebrief_resolver_joe")
    refused(cur, "select ops.resolve_calendar_prebrief_email_ref(%s)", ("prebrief-merged@example.test",), "22023", "exactly one live unmerged")
    cur.execute("reset session authorization")
    stale_lease = uuid.uuid4(); stale_job, stale_snapshot = job(cur, joe_key, stale_lease, "-10 minutes")
    attest(cur, "joe", (stale_job, stale_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, "f" * 64, ref, stale_snapshot))))
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (stale_job, stale_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, "f" * 64, ref, stale_snapshot))), "22023", "refuses stale snapshot")
    cur.execute("reset session authorization")
    prune_lease = uuid.uuid4(); prune_job, prune_snapshot = job(cur, joe_key, prune_lease, "2 seconds")
    attested_ingest(cur, "joe", (prune_job, prune_lease, [joe_allowed], Jsonb([])))
    cur.execute("select sponsor,count(*) from ops.calendar_prebrief_projection_event group by sponsor order by sponsor")
    if cur.fetchall() != [("dell", 2)]:
        raise RuntimeError("empty Joe snapshot did not prune only Joe's current projection")
    colon_lease = uuid.uuid4(); colon_job, colon_snapshot = job(cur, joe_key, colon_lease, "3 seconds")
    attested_ingest(cur, "joe", (colon_job, colon_lease, [joe_allowed], Jsonb(payload_data(
        joe_allowed, "9" * 64, ref, colon_snapshot, title="Meeting: Dr Smith"))))

    # Canary definitions, receipts, and views are a separate destination.  A
    # canary source cannot be replayed through live ingest and vice versa.
    canary_key, canary_destination = "calendar-prebrief-canary-joe-daily", "calendar-prebrief-canary-joe"
    canary_lease = uuid.uuid4()
    canary_job, canary_snapshot = job(cur, canary_key, canary_lease, "4 seconds", mode="canary")
    canary_events = Jsonb(payload_data(joe_allowed, "8" * 64, ref, canary_snapshot))
    canary_args = (canary_job, canary_lease, [joe_allowed], canary_events)
    attest(cur, "joe", canary_args, canary_destination)
    cur.execute("set session authorization carr_calendar_prebrief_canary_joe")
    cur.execute(canary_ingest_sql(), (canary_job, canary_lease, canary_destination, [joe_allowed], canary_events))
    canary_receipt = required(cur, "isolated canary receipt")[0]
    refused(cur, ingest_sql(), canary_args, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, canary_ingest_sql(), (canary_job, canary_lease, canary_destination, [joe_allowed], canary_events), "42501", "permission denied")
    refused(cur, ingest_sql(), canary_args, "42501", "does not match the static job owner")
    cur.execute("reset session authorization")
    cur.execute("select count(*) from ops.calendar_prebrief_projection_event where sponsor='joe'")
    if required(cur, "canary never mutates live projection") != (1,):
        raise RuntimeError("canary ingest touched the live projection")
    cur.execute("select event_count from ops.calendar_prebrief_canary_receipt where id=%s", (canary_receipt,))
    if required(cur, "canary receipt event count") != (1,):
        raise RuntimeError("canary receipt is not bound to its isolated source")

    # Tables remain invisible; only the exact capabilities and redacted views
    # are reachable to application identities.
    cur.execute(
        """select has_table_privilege(role_name,table_name,'select') or has_table_privilege(role_name,table_name,'insert')
                    or has_table_privilege(role_name,table_name,'update') or has_table_privilege(role_name,table_name,'delete')
              from (values ('carr_reader'),('carr_writer'),('carr_jobs'),('carr_authority'),('carr_calendar_prebrief_jobs'),('carr_calendar_prebrief_canary_jobs'),
                           ('carr_calendar_prebrief_attestors'),('carr_calendar_prebrief_email_resolver')) roles(role_name)
              cross join (values ('ops.calendar_prebrief_allowed_calendar'),('ops.calendar_prebrief_allowlist_receipt'),
                                 ('ops.calendar_prebrief_projection_event'),('ops.calendar_prebrief_projection_participant'),
                                 ('ops.calendar_prebrief_projection_receipt'),('ops.calendar_prebrief_source_attestation_receipt'),('ops.calendar_prebrief_capture_challenge'),
                                 ('ops.calendar_prebrief_canary_event'),('ops.calendar_prebrief_canary_receipt')) tables(table_name)"""
    )
    if any(row[0] for row in cur.fetchall()):
        raise RuntimeError("calendar prebrief base/config table leaked to an application capability")
    cur.execute(
        """select has_function_privilege('carr_reader','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_writer','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_attestors','ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text)','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text)','execute'),
                  has_function_privilege('carr_calendar_prebrief_email_resolver','ops.resolve_calendar_prebrief_email_ref(text)','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_email_resolver','ops.issue_calendar_prebrief_capture_contract(uuid,uuid)','execute'),
                  has_function_privilege('carr_calendar_prebrief_attestors','ops.issue_calendar_prebrief_capture_contract(uuid,uuid)','execute'),
                  has_function_privilege('carr_authority','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_reader','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_jobs','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.replace_calendar_prebrief_allowlist(text[])','execute')"""
    )
    capability_split = required(cur, "function capability split")
    if capability_split != (False, False, False, True, True, False, True, False, True, False, True, False, True, False, False, False):
        raise RuntimeError(f"calendar prebrief function capability grants are wrong: {capability_split!r}")
    cur.execute("set session authorization carr_jobs")
    refused(cur, ingest_sql(), first_args, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_writer")
    refused(cur, ingest_sql(), first_args, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("select column_name from information_schema.columns where table_schema='public' and table_name='v_calendar_prebrief_events' order by ordinal_position")
    expected_events = [(name,) for name in ("sponsor", "occurrence_key", "starts_at", "ends_at", "title", "location", "participant_ref", "participant_display_name", "participant_org_name", "participant_status", "participant_last_touch", "open_owner", "open_action")]
    if cur.fetchall() != expected_events:
        raise RuntimeError("event reader view drifted from brief-pack contract")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("dell", dell_snapshot, 2, 1), ("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("redacted snapshot-status view did not expose the latest per-sponsor counts")
    cur.execute("select sponsor,occurrence_key,title,location,participant_ref from v_calendar_prebrief_events order by sponsor,occurrence_key")
    rows = cur.fetchall()
    if len(rows) != 3 or not any(row[-1] is None for row in rows) or any("@" in str(value) or "://" in str(value) for row in rows for value in row if value is not None):
        raise RuntimeError("reader event view exposed prohibited source data")
    cur.execute("select sponsor,destination,event_count from v_calendar_prebrief_canary_snapshot_status order by sponsor,destination")
    if cur.fetchall() != [("dell", "calendar-prebrief-canary-dell", 1), ("joe", "calendar-prebrief-canary-joe", 1)]:
        raise RuntimeError("isolated canary view is not separately readable")
    if any(row[1] == "8" * 64 for row in rows):
        raise RuntimeError("live reader view joined an isolated canary event")
    refused(cur, "select * from ops.calendar_prebrief_projection_event", (), "42501", "permission denied")
    refused(cur, "select * from ops.calendar_prebrief_allowed_calendar", (), "42501", "permission denied")
    cur.execute("reset session authorization")
    # Capture under revision A, then change the allowlist before isolated
    # ingestion.  The stale source must not become a hidden canary receipt.
    stale_canary_lease = uuid.uuid4()
    stale_canary_job, stale_canary_snapshot = job(cur, "calendar-prebrief-canary-dell-daily", stale_canary_lease, "5 seconds", mode="canary")
    stale_canary_events = Jsonb(payload_data(dell_allowed, "a" * 63 + "1", second_ref, stale_canary_snapshot))
    attest(cur, "dell", (stale_canary_job, stale_canary_lease, [dell_allowed], stale_canary_events), "calendar-prebrief-canary-dell")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).configuration_digest", ([dell_allowed, alien],))
    required(cur, "fresh Dell allowlist receipt")
    cur.execute("reset session authorization")
    cur.execute("select active_revision_id from ops.calendar_prebrief_allowed_calendar where sponsor='dell'")
    dell_revision_b = required(cur, "changed Dell allowlist revision")[0]
    if dell_revision_b == dell_revision_a:
        raise RuntimeError("allowlist change did not mint a new active revision")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("allowlist change did not immediately invalidate Dell's stale snapshot status")
    cur.execute("select sponsor,count(*) from v_calendar_prebrief_events group by sponsor order by sponsor")
    if cur.fetchall() != [("joe", 1)]:
        raise RuntimeError("allowlist change did not immediately hide Dell's stale projection events")
    cur.execute("select sponsor,destination,event_count from v_calendar_prebrief_canary_snapshot_status order by sponsor,destination")
    if cur.fetchall() != [("joe", "calendar-prebrief-canary-joe", 1)]:
        raise RuntimeError("allowlist change did not immediately hide Dell's stale canary status")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_canary_dell")
    refused(cur, canary_ingest_sql(), (stale_canary_job, stale_canary_lease, "calendar-prebrief-canary-dell", [dell_allowed], stale_canary_events), "55000", "exact immutable verified source envelope")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).id", ([dell_allowed],))
    dell_revision_a_again = required(cur, "restored Dell allowlist revision")[0]
    cur.execute("reset session authorization")
    if dell_revision_a_again in (dell_revision_a, dell_revision_b):
        raise RuntimeError("A-to-B-to-A allowlist restoration reused an old revision")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("A-to-B-to-A allowlist restoration resurrected stale Dell status")
    cur.execute("select sponsor,count(*) from v_calendar_prebrief_events group by sponsor order by sponsor")
    if cur.fetchall() != [("joe", 1)]:
        raise RuntimeError("A-to-B-to-A allowlist restoration resurrected stale Dell events")
    cur.execute("select sponsor,destination,event_count from v_calendar_prebrief_canary_snapshot_status order by sponsor,destination")
    if cur.fetchall() != [("joe", "calendar-prebrief-canary-joe", 1)]:
        raise RuntimeError("A-to-B-to-A allowlist restoration resurrected stale Dell canary status")
    cur.execute("reset session authorization")
    refused(cur, "update ops.calendar_prebrief_projection_receipt set event_count=9 where id=%s", (first_receipt,), "P0001", "append-only")
    cur.execute("select id from ops.calendar_prebrief_source_attestation_receipt where job_id=%s and attempt=1", (first_job,))
    first_source_receipt = required(cur, "first device source receipt")[0]
    refused(cur, "update ops.calendar_prebrief_source_attestation_receipt set event_count=9 where id=%s", (first_source_receipt,), "P0001", "append-only")
    cur.execute("select id from ops.calendar_prebrief_allowlist_receipt where sponsor='joe' order by configured_at limit 1")
    first_allowlist_receipt = required(cur, "Joe allowlist receipt id")[0]
    refused(cur, "update ops.calendar_prebrief_allowlist_receipt set configured_by='dell' where id=%s", (first_allowlist_receipt,), "P0001", "append-only")
    # This uses two actual device connections against one committed disposable
    # job.  Both may replay the same immutable attestation; neither may mint a
    # competing receipt for that job/attempt.
    concurrent_lease = uuid.uuid4()
    concurrent_job, concurrent_snapshot = job(cur, joe_key, concurrent_lease, "4 seconds")
    concurrent_events = Jsonb(payload_data(joe_allowed, "6" * 64, ref, concurrent_snapshot))
    concurrent_signed = envelope_args(cur, "joe", (concurrent_job, concurrent_lease, [joe_allowed], concurrent_events), signature="6" * 64)
    conn.commit()
    concurrent_results: list[uuid.UUID] = []

    def concurrent_attest() -> None:
        with psycopg.connect(dsn) as peer_conn, peer_conn.cursor() as peer:
            peer.execute("set session authorization carr_calendar_prebrief_attestor_joe")
            peer.execute(attest_sql(), concurrent_signed)
            concurrent_results.append(required(peer, "concurrent immutable source receipt")[0])
            peer_conn.commit()

    peers = [Thread(target=concurrent_attest), Thread(target=concurrent_attest)]
    for peer in peers:
        peer.start()
    for peer in peers:
        peer.join()
    if len(concurrent_results) != 2 or len(set(concurrent_results)) != 1:
        raise RuntimeError("concurrent device attestations did not converge on one immutable attempt receipt")
    cur.execute("begin")
    cur.execute("select count(*) from ops.calendar_prebrief_source_attestation_receipt where job_id=%s and attempt=1", (concurrent_job,))
    if required(cur, "concurrent source receipt cardinality") != (1,):
        raise RuntimeError("concurrent device attestations created more than one receipt")
    conn.rollback()

print("calendar prebrief projection local acceptance passed")
