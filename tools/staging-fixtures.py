#!/usr/bin/env python3
"""Install the minimal deterministic, invented staging G1 fixture.

Default mode is a read-only plan. ``--apply`` requires an explicit owner DSN
and replacement provider identities. No source row or identifier is printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

FIXTURE_ID = uuid.uuid5(uuid.UUID("80d943c3-53af-4a13-9200-f3d8581a459a"),
                        "carr-clean-staging-replacement-party-v1")
INVOICE_NAMESPACE = uuid.UUID("4acbfa5b-1f38-47af-aac0-1c4688cfc479")
EXCLUDED_TOUR_NAMESPACE = uuid.UUID("61c09cb0-19e3-411c-98d8-ea2c962eb3a1")
RELATIONSHIP_NAMESPACE = uuid.UUID("439206ab-d73a-5c1b-8a20-531e536e5804")
RELATIONSHIP_SCHEMA = "doctorcre-staging-relationship-fixture.v1"
RELATIONSHIP_NORTH = "Synthetic Staging QA North"
RELATIONSHIP_SOUTH = "Synthetic Staging QA South"
TOUR_TENANT = "carr-internal"
EMPTY_ASSERTION_SET_DIGEST = "sha256:" + hashlib.sha256(b"[]").hexdigest()
PLAN_SCHEMA = "doctorcre-staging-records-plan.v1"
STAGING_ORIGIN = "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev"
MAX_PLAN_BYTES = 1_048_576
MAX_PROOF_BYTES = 65_536
PROOF_SCHEMA = "doctorcre-staging-fixture-proof.v1"
VENDOR_REFERENCE_SLUG = "prospect_uncontacted"
VENDOR_REFERENCE_LABEL = "Synthetic staging prospect (uncontacted)"
VENDOR_REFERENCE_SORT = 100
AGED_INVOICE_DAYS_BEFORE_OBSERVATION = 45
AGED_DUE_DAYS_BEFORE_OBSERVATION = 15
PRODUCTION_PROJECT_ID = "steep-field-48688294"
REPO = pathlib.Path(__file__).resolve().parents[1]
NEONCTL = REPO / "mcp-server/node_modules/.bin/neonctl"
NEON_ORG_ID = "org-dry-dew-75906281"
Run = Callable[..., subprocess.CompletedProcess]


class FixtureRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class InvoiceValues:
    commission_id: uuid.UUID
    deal_id: uuid.UUID
    gross_amount: Decimal
    status: str
    invoiced_on: dt.date
    due_on: dt.date | None
    observation_date: dt.date


@dataclass(frozen=True)
class ExcludedTourValues:
    tour_id: uuid.UUID
    name: str
    client_id: uuid.UUID
    creation_key: str
    property_id: uuid.UUID
    stop_id: uuid.UUID


def excluded_tour_values(plan: Mapping[str, Any]) -> ExcludedTourValues:
    records, names, keys = plan.get("records"), plan.get("names"), plan.get("keys")
    if not isinstance(records, dict) or not isinstance(names, dict) or not isinstance(keys, dict):
        raise FixtureRefusal("tour plan lacks the App identity fields")
    tour, client = records.get("tour"), records.get("client")
    if not isinstance(tour, dict) or not isinstance(client, dict):
        raise FixtureRefusal("tour plan lacks the App-created Tour and client")
    expected_name = f"Synthetic staging QA {plan.get('run')} draft tour"
    if names.get("tour") != expected_name or tour.get("name") != expected_name:
        raise FixtureRefusal("tour plan name does not bind to its synthetic run")
    try:
        tour_id = uuid.UUID(tour["id"])
        client_id = uuid.UUID(client["id"])
        creation_key = keys["tour"]
        uuid.UUID(creation_key)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise FixtureRefusal("tour plan has an invalid Tour, client, or creation key") from exc
    property_id = uuid.uuid5(EXCLUDED_TOUR_NAMESPACE, f"property:{plan['run']}:{tour_id}")
    stop_id = uuid.uuid5(EXCLUDED_TOUR_NAMESPACE, f"excluded-stop:{plan['run']}:{tour_id}")
    return ExcludedTourValues(tour_id, expected_name, client_id, creation_key, property_id, stop_id)


def load_private_tour_plan(path: pathlib.Path, expected_app_sha: str,
                           expected_carr_sha: str) -> dict[str, Any]:
    path = _private_plan_path(path)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() \
            or stat.S_IMODE(parent.st_mode) != 0o700:
        raise FixtureRefusal("tour plan parent must be an owned private 0700 directory")
    plan = load_private_plan(path, expected_app_sha, expected_carr_sha)
    excluded_tour_values(plan)
    return plan


def _private_plan_path(path: pathlib.Path) -> pathlib.Path:
    if ".." in pathlib.Path(path).parts:
        raise FixtureRefusal("invoice plan path contains parent traversal")
    path = pathlib.Path(os.path.abspath(path))
    cursor = pathlib.Path(path.anchor)
    try:
        for part in path.parts[1:-1]:
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FixtureRefusal("invoice plan path has an unsafe ancestor")
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not info.st_mode & stat.S_ISVTX:
                raise FixtureRefusal("invoice plan path has an untrusted writable ancestor")
    except OSError as exc:
        raise FixtureRefusal("invoice plan ancestor is unavailable") from exc
    return path


def load_private_plan(path: pathlib.Path, expected_app_sha: str, expected_carr_sha: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_app_sha or "") or not re.fullmatch(r"[0-9a-f]{40}", expected_carr_sha or ""):
        raise FixtureRefusal("invoice mode requires explicit full source pins")
    path = _private_plan_path(path)
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
            raise FixtureRefusal("platform cannot safely open a no-follow nonblocking invoice plan")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() \
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise FixtureRefusal("invoice plan must be an owned private regular file")
            if info.st_size > MAX_PLAN_BYTES:
                raise FixtureRefusal("invoice plan exceeds the bounded size")
            raw = stream.read(MAX_PLAN_BYTES + 1)
        if len(raw) > MAX_PLAN_BYTES:
            raise FixtureRefusal("invoice plan exceeds the bounded size")
        value = json.loads(raw)
    except FixtureRefusal:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise FixtureRefusal("invoice plan is unreadable or malformed") from exc
    if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA or value.get("origin") != STAGING_ORIGIN:
        raise FixtureRefusal("invoice plan schema or staging origin is not exact")
    release = value.get("release")
    if not isinstance(release, dict) or release.get("source_commit") != expected_app_sha or release.get("carr_source_commit") != expected_carr_sha:
        raise FixtureRefusal("invoice plan differs from the explicitly expected source pair")
    if value.get("state") != "complete" or "inflight" not in value or value.get("inflight") is not None:
        raise FixtureRefusal("invoice plan is incomplete or has an unsettled write")
    if value.get("current_step") != "readback" or not isinstance(value.get("keys"), dict) or not isinstance(value.get("receipts"), dict):
        raise FixtureRefusal("invoice plan is missing the app setup completion fields")
    run = value.get("run")
    if not isinstance(run, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", run):
        raise FixtureRefusal("invoice plan run identifier is invalid")
    observed = value.get("observed_at")
    if not isinstance(observed, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", observed):
        raise FixtureRefusal("invoice plan observation timestamp is not canonical")
    try:
        observed_at = dt.datetime.strptime(observed, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise FixtureRefusal("invoice plan observation timestamp is invalid") from exc
    names, records = value.get("names"), value.get("records")
    party = records.get("party") if isinstance(records, dict) else None
    client = records.get("client") if isinstance(records, dict) else None
    invoice = records.get("invoice") if isinstance(records, dict) else None
    if not isinstance(names, dict) or not isinstance(party, dict) or not isinstance(client, dict) or not isinstance(invoice, dict):
        raise FixtureRefusal("invoice plan lacks the app-created fixture identity chain")
    if party.get("id") != str(FIXTURE_ID) or party.get("name") != "Synthetic Staging Fixture" or client.get("name") != "Synthetic Staging Fixture":
        raise FixtureRefusal("invoice plan party is not the exact synthetic QA party")
    try:
        uuid.UUID(client.get("id", ""))
        uuid.UUID(invoice.get("deal_id", ""))
    except (ValueError, AttributeError, TypeError) as exc:
        raise FixtureRefusal("invoice plan client or deal identifier is invalid") from exc
    expected_name = f"Synthetic staging QA {run} closed deal"
    if names.get("invoice") != expected_name or invoice.get("name") != expected_name:
        raise FixtureRefusal("invoice plan deal name does not bind to its run")
    value["_invoice_date"] = observed_at.date()
    return value


def invoice_commission_values(plan: Mapping[str, Any], *, aged: bool = False) -> InvoiceValues:
    run = plan["run"]
    deal_id = uuid.UUID(plan["records"]["invoice"]["deal_id"])
    commission_id = uuid.uuid5(INVOICE_NAMESPACE, f"staging-invoice-commission:{run}")
    observation_date = plan.get("_invoice_date")
    if observation_date is None:
        observation_date = dt.datetime.strptime(plan["observed_at"], "%Y-%m-%dT%H:%M:%S.%fZ").date()
    invoice_date = observation_date - dt.timedelta(days=AGED_INVOICE_DAYS_BEFORE_OBSERVATION) if aged else observation_date
    due_on = observation_date - dt.timedelta(days=AGED_DUE_DAYS_BEFORE_OBSERVATION) if aged else None
    return InvoiceValues(commission_id, deal_id, Decimal("1.00"), "invoiced", invoice_date, due_on,
                         observation_date)


def install_invoice_fixture(conn: Any, plan: Mapping[str, Any], *, aged: bool = False) -> int:
    """Seed and verify a synthetic invoice. Provenance is system attribution plus DB readback, not an MCP event/receipt."""
    values = invoice_commission_values(plan, aged=aged)
    commission_id, deal_id = values.commission_id, values.deal_id
    gross, status, invoiced_on, due_on = values.gross_amount, values.status, values.invoiced_on, values.due_on
    try:
        with conn.cursor() as cur:
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
                raise FixtureRefusal("fixture installation requires exact owner session")
            cur.execute("select id from public.actor where slug='system'")
            actor = cur.fetchone()
            if actor is None:
                raise FixtureRefusal("exact schema seed lacks system actor")
            cur.execute("select current_date")
            today = cur.fetchone()
            if not today or values.observation_date > today[0] or invoiced_on > today[0] \
                    or (due_on is not None and due_on > today[0]):
                raise FixtureRefusal("recorded invoice observation date is after database current date")
            cur.execute("""select d.id::text,d.name,d.phase,d.outcome,d.owner,cl.id::text,cl.merged_into,
                       p.id::text,p.name,p.deleted_at,p.merged_into
                  from public.deal d join public.client cl on cl.id=d.client_id
                  join public.party p on p.id=cl.party_id
                 where d.id=%s for update of d,cl,p""", (deal_id,))
            deal = cur.fetchone()
            expected_name = plan["records"]["invoice"]["name"]
            if not deal or len(deal) != 11 or tuple(deal[:3]) != (str(deal_id), expected_name, "closed") \
                    or deal[3] not in (None, "won") or deal[4] != "joe" or tuple(deal[5:]) != (
                        str(uuid.UUID(plan["records"]["client"]["id"])), None, str(FIXTURE_ID),
                        "Synthetic Staging Fixture", None, None):
                raise FixtureRefusal("app-owned closed QA deal and active fixture-party lineage did not read back exactly")
            cur.execute("""select id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by,due_on
                           from public.commission where deal_id=%s for update""", (deal_id,))
            existing = cur.fetchall()
            expected_without_actor = (commission_id, deal_id, gross, status, invoiced_on, None, 1, due_on)
            if len(existing) > 1:
                raise FixtureRefusal("QA deal has ambiguous existing commission rows")
            if existing:
                row = tuple(existing[0])
                if row[:7] != expected_without_actor[:7] or row[7] != actor[0] or row[8] != due_on:
                    raise FixtureRefusal("existing commission differs from the exact unpaid synthetic invoice; no row was changed")
                inserted = 0
            else:
                cur.execute("""insert into public.commission
                    (id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by,due_on)
                    values (%s,%s,%s,%s,%s,null,1,%s,%s) on conflict (id) do nothing returning id""",
                    (commission_id, deal_id, gross, status, invoiced_on, actor[0], due_on))
                inserted = int(cur.fetchone() is not None)
                cur.execute("""select id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by,due_on
                               from public.commission where id=%s""", (commission_id,))
                row = cur.fetchone()
                if not row or tuple(row) != (*expected_without_actor[:7], actor[0], due_on):
                    raise FixtureRefusal("deterministic invoice commission collision or readback mismatch")
            cur.execute("""select deal_id,commission_id,gross_amount,status,commission_invoiced_on,received_on,due_on
                           from public.v_invoice_tracker where deal_id=%s and commission_id=%s""",
                        (deal_id, commission_id))
            tracker = cur.fetchone()
            if not tracker or tuple(tracker) != (deal_id, commission_id, gross, status, invoiced_on, None, due_on):
                raise FixtureRefusal("invoice tracker projection did not read back the exact unpaid fixture")
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise


def read_invoice_fixture(conn: Any, plan: Mapping[str, Any], *, aged: bool = False) -> dict[str, Any]:
    """Read committed invoice state on a separate connection without writing."""
    values = invoice_commission_values(plan, aged=aged)
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute("select session_user,current_user,current_database()")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner", "neondb"):
                raise FixtureRefusal("independent fixture readback reached an unexpected database identity")
            cur.execute("select id from public.actor where slug='system'")
            actor = cur.fetchone()
            if actor is None:
                raise FixtureRefusal("exact schema seed lacks system actor")
            cur.execute("""select d.id::text,d.name,d.phase,d.outcome,d.owner,cl.id::text,cl.merged_into,
                       p.id::text,p.name,p.deleted_at,p.merged_into
                  from public.deal d join public.client cl on cl.id=d.client_id
                  join public.party p on p.id=cl.party_id
                 where d.id=%s""", (values.deal_id,))
            deal = cur.fetchone()
            expected_deal = (str(values.deal_id), plan["records"]["invoice"]["name"], "closed",
                             "joe", plan["records"]["client"]["id"], None, str(FIXTURE_ID),
                             "Synthetic Staging Fixture", None, None)
            if not deal or tuple(deal[:3]) != expected_deal[:3] or deal[3] not in (None, "won") \
                    or tuple(deal[4:]) != expected_deal[3:]:
                raise FixtureRefusal("independent app-owned deal and fixture-party readback did not match")
            cur.execute("""select id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by,due_on
                           from public.commission where id=%s""", (values.commission_id,))
            commission = cur.fetchone()
            expected_commission = (values.commission_id, values.deal_id, values.gross_amount, values.status,
                                   values.invoiced_on, None, 1, actor[0], values.due_on)
            if not commission or tuple(commission) != expected_commission:
                raise FixtureRefusal("independent committed commission readback did not match the fixture")
            cur.execute("""select deal_id,commission_id,gross_amount,status,commission_invoiced_on,received_on,due_on
                           from public.v_invoice_tracker where deal_id=%s and commission_id=%s""",
                        (values.deal_id, values.commission_id))
            tracker = cur.fetchone()
            expected_tracker = (values.deal_id, values.commission_id, values.gross_amount, values.status,
                                values.invoiced_on, None, values.due_on)
            if not tracker or tuple(tracker) != expected_tracker:
                raise FixtureRefusal("independent invoice tracker readback did not match the fixture")
        conn.rollback()
        return {
            "party": {"id": str(FIXTURE_ID), "name": "Synthetic Staging Fixture"},
            "deal": {"id": str(deal[0]), "name": deal[1], "phase": deal[2], "outcome": deal[3],
                     "owner": deal[4], "client_id": deal[5]},
            "commission": {"id": str(commission[0]), "deal_id": str(commission[1]),
                           "gross_amount": str(commission[2]), "status": commission[3],
                           "invoiced_on": commission[4].isoformat(),
                           "received_on": commission[5].isoformat() if commission[5] else None,
                           "base_version": commission[6],
                           "due_on": commission[8].isoformat() if commission[8] else None},
            "tracker": {"deal_id": str(tracker[0]), "commission_id": str(tracker[1]),
                        "gross_amount": str(tracker[2]), "status": tracker[3],
                        "invoiced_on": tracker[4].isoformat(),
                        "received_on": tracker[5].isoformat() if tracker[5] else None,
                        "due_on": tracker[6].isoformat() if tracker[6] else None},
        }
    except Exception:
        conn.rollback()
        raise


def _checked_tour_state(cur: Any, values: ExcludedTourValues, *, lock: bool) -> tuple[uuid.UUID, bool]:
    suffix = " for update" if lock else ""
    cur.execute("select session_user,current_user,current_database()")
    if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner", "neondb"):
        raise FixtureRefusal("Tour fixture requires the exact owner database session")
    cur.execute("select slug from public.actor where slug in ('joe','system') and active order by slug")
    if [row[0] for row in cur.fetchall()] != ["joe", "system"]:
        raise FixtureRefusal("Tour fixture requires active Joe and system actors")
    cur.execute("""select 1 from public.tool_call tc join public.actor a on a.id=tc.actor_id
                    where tc.idempotency_key=%s and tc.verb='create-tour-domain'
                      and tc.organization_tenant_id=%s and a.slug='joe' and a.kind='human' and a.active
                      and tc.response=jsonb_build_object('ok',true,'tour_id',%s::text)"""
                + (" for share of tc,a" if lock else ""),
                (values.creation_key, TOUR_TENANT, str(values.tour_id)))
    if cur.fetchone() != (1,):
        raise FixtureRefusal("Tour creation key lacks Joe's exact successful canonical tool call")
    cur.execute("""select cl.id::text,cl.status,cl.merged_into,p.id::text,p.name,p.deleted_at,p.merged_into
                     from public.client cl join public.party p on p.id=cl.party_id
                    where cl.id=%s""" + suffix + (" of cl,p" if lock else ""), (values.client_id,))
    client = cur.fetchone()
    if not client or tuple(client) != (str(values.client_id), "engaged", None, str(FIXTURE_ID),
                                     "Synthetic Staging Fixture", None, None):
        raise FixtureRefusal("Tour client is not the active synthetic fixture party")
    cur.execute("""select id,tour_name,tour_status,route_version,canonical_dataset_version,
                          subject_type,subject_id,subject_bound_at
                     from ops.tour where id=%s and organization_tenant_id=%s""" + suffix,
                (values.tour_id, TOUR_TENANT))
    tour = cur.fetchone()
    if not tour or tuple(tour[:7]) != (values.tour_id, values.name, "draft", 1,
                                      "synthetic-staging-qa-v1", "client", str(values.client_id)) \
            or tour[7] is None:
        raise FixtureRefusal("App-created Tour is missing, changed, or not a client draft")
    cur.execute("""select id,route_version,base_route_version_id,start_point,end_point,
                          routing_source,routing_provider,routing_rights_receipt_id,routing_request,
                          routing_response_digest,created_by_actor_id,routing_policy_key
                     from ops.tour_route_version where organization_tenant_id=%s and tour_id=%s""" + suffix,
                (TOUR_TENANT, values.tour_id))
    routes = cur.fetchall()
    point = {"latitude": 30.4, "longitude": -87.2,
             "precision_class": "approximate", "source_ref": "synthetic-staging-qa-point-v1"}
    if len(routes) != 1 or tuple(routes[0][1:]) != (1, None, {**point, "position_role": "start"},
                                             {**point, "position_role": "end"},
                                             "manual", None, None, {}, None, "joe", None):
        raise FixtureRefusal("Tour route drifted from Joe-created unaccepted manual version one")
    route_id = routes[0][0]
    cur.execute("select count(*) from ops.tour_route_version_acceptance where organization_tenant_id=%s and tour_id=%s",
                (TOUR_TENANT, values.tour_id))
    if cur.fetchone()[0]:
        raise FixtureRefusal("Tour route has acceptance state")
    for table in ("tour_property_membership", "tour_public_projection", "tour_selection_cart_version"):
        cur.execute(f"select count(*) from ops.{table} where organization_tenant_id=%s and tour_id=%s",
                    (TOUR_TENANT, values.tour_id))
        if cur.fetchone()[0]:
            raise FixtureRefusal("Tour has selected, projected, or shared state")
    cur.execute("select id,organization_tenant_id,property_status,retired_at from ops.tour_property where id=%s" + suffix,
                (values.property_id,))
    property_row = cur.fetchone()
    if property_row and tuple(property_row) != (values.property_id, TOUR_TENANT, "unknown", None):
        raise FixtureRefusal("deterministic Tour property identifier collides or changed")
    cur.execute("""select id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,
                          stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,
                          buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id
                     from ops.tour_route_stop where route_version_id=%s or id=%s""" + suffix,
                (route_id, values.stop_id))
    stops = cur.fetchall()
    expected_stop = (values.stop_id, TOUR_TENANT, route_id, values.property_id, None, None,
                     "excluded", None, None, False, 0, 0, "unknown", EMPTY_ASSERTION_SET_DIGEST, "system")
    if len(stops) > 1 or (stops and tuple(stops[0]) != expected_stop):
        raise FixtureRefusal("Tour has another stop or deterministic stop collision")
    if bool(property_row) != bool(stops):
        raise FixtureRefusal("Tour property and excluded stop are not an exact atomic fixture")
    if property_row:
        cur.execute("""select count(*) from ops.tour_route_stop where property_id=%s and id<>%s""",
                    (values.property_id, values.stop_id))
        if cur.fetchone()[0]:
            raise FixtureRefusal("synthetic Tour property is used by another route")
        for table in ("tour_field_assertion", "tour_property_coordinate_candidate",
                      "tour_property_membership"):
            cur.execute(f"select count(*) from ops.{table} where property_id=%s", (values.property_id,))
            if cur.fetchone()[0]:
                raise FixtureRefusal("synthetic Tour property has assertions, coordinates, or accepted membership")
    return route_id, bool(stops)


def install_excluded_tour_fixture(conn: Any, plan: Mapping[str, Any]) -> int:
    values = excluded_tour_values(plan)
    try:
        with conn.cursor() as cur:
            route_id, exists = _checked_tour_state(cur, values, lock=True)
            if not exists:
                cur.execute("""insert into ops.tour_property
                    (id,organization_tenant_id,property_status) values (%s,%s,'unknown')""",
                    (values.property_id, TOUR_TENANT))
                cur.execute("""insert into ops.tour_route_stop
                    (id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,
                     stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,
                     buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
                    values (%s,%s,%s,%s,null,null,'excluded',null,null,false,0,0,'unknown',%s,'system')""",
                    (values.stop_id, TOUR_TENANT, route_id, values.property_id, EMPTY_ASSERTION_SET_DIGEST))
                _checked_tour_state(cur, values, lock=False)
        conn.commit()
        return int(not exists)
    except Exception:
        conn.rollback()
        raise


def read_excluded_tour_fixture(conn: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    values = excluded_tour_values(plan)
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            route_id, exists = _checked_tour_state(cur, values, lock=False)
            if not exists:
                raise FixtureRefusal("independent Tour readback has no excluded stop")
        conn.rollback()
        return {"tour_id": str(values.tour_id), "tour_name": values.name,
                "client_id": str(values.client_id), "route_version_id": str(route_id),
                "route_version": 1, "route_accepted": False, "active_stop_count": 0,
                "excluded_stop_count": 1, "property_id": str(values.property_id),
                "property_status": "unknown", "stop_id": str(values.stop_id),
                "stop_state": "excluded", "route_sequence": None, "route_label": None,
                "access_coordinate_status": "unknown"}
    except Exception:
        conn.rollback()
        raise


def _private_proof_destination(path: pathlib.Path) -> tuple[pathlib.Path, str, int]:
    if ".." in pathlib.Path(path).parts:
        raise FixtureRefusal("proof path contains parent traversal")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise FixtureRefusal("platform cannot safely open a private proof directory")
    path = pathlib.Path(os.path.abspath(path))
    parent = path.parent
    try:
        cursor = pathlib.Path(path.anchor)
        for part in path.parts[1:-1]:
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FixtureRefusal("proof path has an unsafe ancestor")
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not info.st_mode & stat.S_ISVTX:
                raise FixtureRefusal("proof path has an untrusted writable ancestor")
        parent_info = parent.lstat()
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) \
                or parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) != 0o700:
            raise FixtureRefusal("proof parent must be an owned private 0700 directory")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory_fd = os.open(parent, flags)
        opened_info = os.fstat(directory_fd)
        if (opened_info.st_dev, opened_info.st_ino) != (parent_info.st_dev, parent_info.st_ino):
            os.close(directory_fd)
            raise FixtureRefusal("proof parent changed during validation")
        if opened_info.st_uid != os.geteuid() or stat.S_IMODE(opened_info.st_mode) != 0o700:
            os.close(directory_fd)
            raise FixtureRefusal("proof parent must remain an owned private 0700 directory")
    except FixtureRefusal:
        raise
    except OSError as exc:
        raise FixtureRefusal("proof parent is unavailable or unsafe") from exc
    return path, path.name, directory_fd


def _read_private_existing_proof(directory_fd: int, name: str) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                     dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FixtureRefusal("existing proof path is not a safe regular file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() \
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise FixtureRefusal("existing proof must be an owned private regular file")
        if info.st_size > MAX_PROOF_BYTES:
            raise FixtureRefusal("existing proof exceeds the bounded size")
        chunks: list[bytes] = []
        remaining = MAX_PROOF_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > MAX_PROOF_BYTES:
            raise FixtureRefusal("existing proof exceeds the bounded size")
        return value
    finally:
        os.close(fd)


def validate_proof_destination(path: pathlib.Path) -> None:
    """Check output directory and any existing file before opening the database."""
    _, name, directory_fd = _private_proof_destination(path)
    try:
        _read_private_existing_proof(directory_fd, name)
    finally:
        os.close(directory_fd)


def publish_private_proof(path: pathlib.Path, proof: Mapping[str, Any]) -> str:
    """Atomically create a bounded 0600 proof; exact reruns validate but never replace it."""
    encoded = (json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    if len(encoded) > MAX_PROOF_BYTES:
        raise FixtureRefusal("proof exceeds the bounded size")
    path, name, directory_fd = _private_proof_destination(path)
    temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        existing = _read_private_existing_proof(directory_fd, name)
        if existing is not None:
            if existing == encoded:
                return "already-exact"
            raise FixtureRefusal("existing proof does not exactly match this fixture readback")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise FixtureRefusal("could not safely create proof temporary file") from exc
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise FixtureRefusal("proof write did not complete")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(temp_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError:
            existing = _read_private_existing_proof(directory_fd, name)
            if existing == encoded:
                return "already-exact"
            raise FixtureRefusal("existing proof does not exactly match this fixture readback")
        except OSError as exc:
            raise FixtureRefusal("could not atomically publish proof without overwrite") from exc
        os.unlink(temp_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return "created"
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def build_fixture_proof(plan: Mapping[str, Any], target: Mapping[str, Any],
                        readback: Mapping[str, Any], *, aged: bool) -> dict[str, Any]:
    values = invoice_commission_values(plan, aged=aged)
    tool_sha = hashlib.sha256(pathlib.Path(__file__).resolve().read_bytes()).hexdigest()
    return {
        "schema": PROOF_SCHEMA,
        "provenance": {"kind": "raw-sql-fixture"},
        "fixture_run": plan["run"],
        "source_pair": {
            "source_commit": plan["release"]["source_commit"],
            "carr_source_commit": plan["release"]["carr_source_commit"],
        },
        "recorded_plan_observed_at": plan["observed_at"],
        "verified_target": dict(target),
        "tool_source_sha256": tool_sha,
        "fixture": {
            "mode": "aged-invoice" if aged else "invoice",
            "date_offsets_days": ({
                "invoiced_on_before_observation": AGED_INVOICE_DAYS_BEFORE_OBSERVATION,
                "due_on_before_observation": AGED_DUE_DAYS_BEFORE_OBSERVATION,
            } if aged else {"invoiced_on_before_observation": 0, "due_on_before_observation": None}),
            "observation_date": values.observation_date.isoformat(),
            "party": readback["party"],
            "deal": readback["deal"],
            "commission_id": str(values.commission_id),
            "base_version": readback["commission"]["base_version"],
            "commission_readback": readback["commission"],
            "tracker_readback": readback["tracker"],
        },
    }


def build_tour_fixture_proof(plan: Mapping[str, Any], target: Mapping[str, Any],
                             readback: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PROOF_SCHEMA,
        "provenance": {"kind": "raw-sql-fixture", "readback": "separate-committed-readonly-connection"},
        "fixture_run": plan["run"],
        "source_pair": {"source_commit": plan["release"]["source_commit"],
                        "carr_source_commit": plan["release"]["carr_source_commit"]},
        "recorded_plan_observed_at": plan["observed_at"],
        "verified_target": dict(target),
        "tool_source_sha256": hashlib.sha256(pathlib.Path(__file__).resolve().read_bytes()).hexdigest(),
        "fixture": {"mode": "excluded-tour-stop", "tour_readback": dict(readback)},
    }


def validate_target(dsn: str, project_id: str, endpoint_id: str, *,
                    environ: Mapping[str, str] = os.environ) -> None:
    parsed = urlsplit(dsn)
    host = (parsed.hostname or "").lower().rstrip(".")
    # libpq permits URI query parameters and PG* environment defaults to
    # select a different socket, address, port, service or database than the
    # endpoint host/path this installer reviewed. Reject routing inputs before
    # provider verification or opening a database connection; SSL and auth
    # parameters remain available.
    routing_options = {"host", "hostaddr", "service", "servicefile", "port", "dbname"}
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    routing_environment = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE")
    if query_keys & routing_options or any(environ.get(name) for name in routing_environment):
        raise FixtureRefusal("owner DSN uses an unsupported libpq target override")
    if project_id in {"", PRODUCTION_PROJECT_ID}:
        raise FixtureRefusal("replacement project identity is absent or forbidden")
    if not endpoint_id.startswith("ep-") or not host.startswith(endpoint_id + ".") \
            or not host.endswith(".neon.tech") or parsed.scheme not in {"postgres", "postgresql"} \
            or not parsed.password or parsed.port not in {None, 5432} or parsed.fragment \
            or unquote(parsed.path.lstrip("/")) != "neondb" \
            or unquote(parsed.username or "") != "neondb_owner":
        raise FixtureRefusal("owner DSN does not match the replacement endpoint")


def validate_provider_binding(dsn: str, project_id: str, endpoint_id: str, *,
                              run: Run = subprocess.run,
                              environ: Mapping[str, str] = os.environ) -> dict[str, Any]:
    """Bind caller declarations to the provider before any fixture write."""
    source_env = dict(environ)
    if not source_env.get("NEON_API_KEY"):
        raise FixtureRefusal("NEON_API_KEY is required for provider-bound fixture installation")
    env = {name: source_env[name] for name in
           ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL")
           if source_env.get(name)}
    env["NEON_API_KEY"] = source_env["NEON_API_KEY"]
    env["PATH"] = "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    def provider(args: list[str], label: str) -> Any:
        try:
            result = run(args, capture_output=True, text=True, timeout=120, env=env)
            if result.returncode:
                raise FixtureRefusal(f"provider {label} failed; output suppressed")
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise FixtureRefusal(f"provider {label} is unavailable; output suppressed") from exc
    projects_raw = provider([str(NEONCTL), "projects", "list", "--org-id", NEON_ORG_ID,
                             "--output", "json"], "project readback")
    projects = projects_raw if isinstance(projects_raw, list) else projects_raw.get("projects", []) \
        if isinstance(projects_raw, dict) else []
    matches = [row for row in projects if isinstance(row, dict) and row.get("id") == project_id]
    if len(matches) != 1 or not str(matches[0].get("name") or "").startswith("carr-staging-replacement-"):
        raise FixtureRefusal("provider project is not one exact replacement candidate")
    branches_raw = provider([str(NEONCTL), "branches", "list", "--project-id", project_id,
                             "--output", "json"], "branch readback")
    branches = branches_raw if isinstance(branches_raw, list) else branches_raw.get("branches", []) \
        if isinstance(branches_raw, dict) else []
    branches = [row for row in branches if isinstance(row, dict) and row.get("name") == "main"
                and row.get("default") is True and row.get("project_id") == project_id]
    if len(branches) != 1 or not branches[0].get("id"):
        raise FixtureRefusal("provider replacement branch is not exact")
    branch_id = str(branches[0]["id"])
    endpoints_raw = provider([str(NEONCTL), "api",
        f"/projects/{project_id}/branches/{branch_id}/endpoints", "--output", "json"],
        "endpoint readback")
    endpoints = endpoints_raw if isinstance(endpoints_raw, list) else endpoints_raw.get("endpoints", []) \
        if isinstance(endpoints_raw, dict) else []
    endpoints = [row for row in endpoints if isinstance(row, dict) and row.get("id") == endpoint_id
                 and row.get("branch_id") == branch_id
                 and row.get("type") in {"read_write", "read-write", "rw"}]
    host = (urlsplit(dsn).hostname or "").lower().rstrip(".")
    if len(endpoints) != 1 or str(endpoints[0].get("host") or "").lower().rstrip(".") != host:
        raise FixtureRefusal("provider endpoint does not own the supplied DSN")
    database = unquote(urlsplit(dsn).path.lstrip("/"))
    project_name = str(matches[0]["name"])
    if len(project_id) > 128 or len(project_name) > 256 or len(branch_id) > 128 \
            or len(endpoint_id) > 128 or len(host) > 253 or database != "neondb":
        raise FixtureRefusal("verified staging target fields exceed the supported proof shape")
    return {
        "project": {"id": project_id, "name": project_name},
        "branch": {"id": branch_id, "name": "main"},
        "endpoint": {"id": endpoint_id, "host": host},
        "database": database,
    }


def install(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
            raise FixtureRefusal("fixture installation requires exact owner session")
        cur.execute("select id from public.actor where slug='system'")
        actor = cur.fetchone()
        if actor is None:
            raise FixtureRefusal("exact schema seed lacks system actor")
        cur.execute("""insert into public.party
            (id,kind,name,created_by,updated_by,contact_state,notes_path)
            values (%s,'org','Synthetic Staging Fixture',%s,%s,'active',
                    'fixtures/clean-staging-replacement-v1')
            on conflict (id) do nothing""", (FIXTURE_ID, actor[0], actor[0]))
        cur.execute("""select count(*) from public.party where id=%s and kind='org'
            and name='Synthetic Staging Fixture'
            and notes_path='fixtures/clean-staging-replacement-v1'""", (FIXTURE_ID,))
        if tuple(cur.fetchone() or ()) != (1,):
            raise FixtureRefusal("deterministic fixture readback is not exact")
    conn.commit()
    return 1


def install_vendor_reference_data(conn: Any) -> int:
    """Install only the one required stage row, refusing any differing row."""
    try:
        with conn.cursor() as cur:
            cur.execute("select session_user,current_user,current_database()")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner", "neondb"):
                raise FixtureRefusal("vendor reference fixture requires the exact owner database session")
            cur.execute("select slug,label,sort from public.vendor_stage where slug=%s for update",
                        (VENDOR_REFERENCE_SLUG,))
            existing = cur.fetchone()
            expected = (VENDOR_REFERENCE_SLUG, VENDOR_REFERENCE_LABEL, VENDOR_REFERENCE_SORT)
            if existing is not None and tuple(existing) != expected:
                raise FixtureRefusal("existing vendor stage differs from the exact synthetic staging row; no row was changed")
            inserted = 0
            if existing is None:
                cur.execute("""insert into public.vendor_stage (slug,label,sort)
                    values (%s,%s,%s) on conflict (slug) do nothing returning slug""", expected)
                inserted = int(cur.fetchone() is not None)
                cur.execute("select slug,label,sort from public.vendor_stage where slug=%s for update",
                            (VENDOR_REFERENCE_SLUG,))
                existing = cur.fetchone()
            if existing is None or tuple(existing) != expected:
                raise FixtureRefusal("vendor stage did not read back as the exact synthetic staging row")
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise


def read_vendor_reference_data(conn: Any) -> dict[str, Any]:
    """Prove the committed stage row on a separate read-only connection."""
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute("select session_user,current_user,current_database()")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner", "neondb"):
                raise FixtureRefusal("vendor reference readback reached an unexpected database identity")
            cur.execute("select slug,label,sort from public.vendor_stage where slug=%s",
                        (VENDOR_REFERENCE_SLUG,))
            row = cur.fetchone()
            expected = (VENDOR_REFERENCE_SLUG, VENDOR_REFERENCE_LABEL, VENDOR_REFERENCE_SORT)
            if row is None or tuple(row) != expected:
                raise FixtureRefusal("independent readback did not find the exact synthetic vendor stage")
        return {"slug": row[0], "label": row[1], "sort": row[2]}
    finally:
        conn.rollback()


def build_vendor_reference_proof(operation_id: uuid.UUID, app_sha: str, carr_sha: str,
                                 target: Mapping[str, Any], readback: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PROOF_SCHEMA,
        "provenance": {"kind": "raw-sql-fixture", "readback": "separate-committed-readonly-connection"},
        "fixture_run": f"vendor-reference-{operation_id}",
        "operation_id": str(operation_id),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source_pair": {"source_commit": app_sha, "carr_source_commit": carr_sha},
        "verified_target": dict(target),
        "tool_source_sha256": hashlib.sha256(pathlib.Path(__file__).resolve().read_bytes()).hexdigest(),
        "fixture": {"mode": "synthetic-vendor-stage-reference", "row_readback": dict(readback)},
    }


def relationship_fixture_values(plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        run = plan["run"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", run):
            raise ValueError("run")
        if plan["schema"] != PLAN_SCHEMA or plan["origin"] != STAGING_ORIGIN \
                or plan["state"] != "complete" or plan["inflight"] is not None or plan["current_step"] != "readback":
            raise ValueError("completion")
        for pin in ("source_commit", "carr_source_commit"):
            if not re.fullmatch(r"[0-9a-f]{40}", plan["release"][pin]):
                raise ValueError("source pair")
        observed = dt.datetime.strptime(plan["observed_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
        records, names = plan["records"], plan["names"]
        ids = {step: str(uuid.UUID(records[step]["id"])) for step in
               ("party", "client", "deal", "vendor_party", "vendor", "vendor_entry")}
        if len(set(ids.values())) != len(ids) or ids["party"] != str(FIXTURE_ID) \
                or records["party"]["name"] != "Synthetic Staging Fixture" \
                or records["client"]["name"] != "Synthetic Staging Fixture":
            raise ValueError("identity chain")
        if records["deal"]["name"] != f"Synthetic staging QA {run} active deal" \
                or names["deal"] != records["deal"]["name"]:
            raise ValueError("deal name")
        keys = {}
        for step, field in (("client", "client_id"), ("deal", "deal_id"), ("vendor_party", "party_id"),
                            ("vendor", "vendor_id"), ("vendor_entry", "activity_id")):
            keys[step] = str(uuid.UUID(plan["keys"][step]))
            if plan["receipts"][step][field] != ids[step]:
                raise ValueError("creation receipt")
        if names["vendor"] != f"QA Vendor {keys['vendor_party']}" \
                or any(records[step]["name"] != names["vendor"] for step in ("vendor", "vendor_party")):
            raise ValueError("vendor name")
        vendor_entry_link = str(uuid.UUID(records["vendor_entry"]["link_id"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise FixtureRefusal("relationship plan lacks an exact complete synthetic ownership chain") from exc
    def identifier(label: str) -> str:
        return str(uuid.uuid5(RELATIONSHIP_NAMESPACE, f"relationship:{label}:{run}"))
    destination = {"id": identifier("destination_deal"), "name": f"Synthetic staging QA {run} referred deal",
                   "client_id": identifier("destination_client"), "party_id": identifier("destination_party")}
    if set(destination[key] for key in ("id", "client_id", "party_id")) & set(ids.values()):
        raise FixtureRefusal("relationship destination collides with an original identity")
    return {"ids": ids, "keys": keys, "destination": destination, "vendor_entry_link": vendor_entry_link,
            "party_name": f"Synthetic staging QA {run} referred client", "vendor_name": names["vendor"],
            "provenance": f"fixtures/staging-relationship/{run}", "occurred_on": observed.date().isoformat(),
            "notes": {role: f"Fictional staging QA {role}_association referral for fixture {run}." for role in ("referral", "vendor")},
            "attribution_ids": {role: identifier(role + "_attribution") for role in ("referral", "vendor")},
            "links": [(identifier("referral_association"), ids["party"], destination["party_id"], destination["id"]),
                      (identifier("vendor_association"), ids["vendor_party"], ids["party"], ids["deal"])]}


def _relationship_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _relationship_select(cur: Any, table: str, fields: Mapping[str, Any], *, lock: bool,
                         either: bool = False) -> list[dict[str, Any]]:
    condition = (" or " if either else " and ").join(f"r.{field}=%s" for field in fields)
    cur.execute(f"select to_jsonb(r) from public.{table} r where {condition}" + (" for update" if lock else ""),
                tuple(fields.values()))
    return [row[0] for row in cur.fetchall()]


def _relationship_exact(rows: list[dict[str, Any]], expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    if len(rows) != 1 or any(key not in rows[0] or rows[0][key] != value for key, value in expected.items()):
        raise FixtureRefusal(f"relationship {label} is absent, ambiguous, or differs from the exact fixture")
    return rows[0]


def _relationship_state(cur: Any, plan: Mapping[str, Any], *, lock: bool) -> dict[str, Any]:
    values = relationship_fixture_values(plan)
    ids, destination = values["ids"], values["destination"]
    cur.execute("select session_user,current_user,current_database()")
    if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner", "neondb"):
        raise FixtureRefusal("relationship fixture requires the exact owner database session")
    actors = []
    for slug in ("system", "joe"):
        cur.execute(f"select id::text from public.actor where slug='{slug}'")
        found = cur.fetchall()
        if len(found) != 1:
            raise FixtureRefusal("relationship fixture requires exact system and Joe actors")
        actors.append(str(found[0][0]))
    system, joe = actors
    def select(table: str, **fields: Any) -> list[dict[str, Any]]:
        return _relationship_select(cur, table, fields, lock=lock)
    live = {"merged_into": None}
    party = _relationship_exact(select("party", id=ids["party"]),
        {"id": ids["party"], "name": "Synthetic Staging Fixture", "kind": "org", "deleted_at": None,
         "notes_path": "fixtures/clean-staging-replacement-v1", "created_by": system, **live}, "fixed party lineage")
    clients = [row for row in select("client", party_id=ids["party"]) if row["merged_into"] is None]
    _relationship_exact(clients, {"id": ids["client"], "party_id": ids["party"], "status": "engaged",
        "acquisition_source": "referral", "acquisition_detail": "Explicitly fictional staging QA fixture only"}, "selected live client")
    if any(row["merged_into"] is None for row in select("vendor", party_id=ids["party"])):
        raise FixtureRefusal("relationship fixed party has vendor precedence")
    if select("vendor", party_id=destination["party_id"]):
        raise FixtureRefusal("relationship destination has vendor precedence or conflicting history")
    _relationship_exact(select("deal", id=ids["deal"]), {"id": ids["deal"], "client_id": ids["client"],
        "name": plan["names"]["deal"], "owner": "joe", "phase": "pending", "lane": "territory", "deal_type": "startup"}, "owned deal")
    _relationship_exact(select("party", id=ids["vendor_party"]), {"id": ids["vendor_party"], "name": values["vendor_name"],
        "kind": "org", "deleted_at": None, **live}, "owned vendor party")
    vendors = [row for row in select("vendor", party_id=ids["vendor_party"]) if row["merged_into"] is None]
    vendor = _relationship_exact(vendors, {"id": ids["vendor"], "party_id": ids["vendor_party"],
        "category": "cpa", "stage": "prospect_uncontacted"}, "owned vendor")
    entry = _relationship_exact(select("activity", id=ids["vendor_entry"]), {"vendor_id": ids["vendor"],
        "kind": "note", "summary": "Synthetic staging QA introduction entry",
        "detail": "Fictional QA evidence only; no contact, referral, or send occurred."}, "vendor original entry")
    _relationship_exact(select("party_link", id=values["vendor_entry_link"]), {"from_party": ids["vendor_party"],
        "to_party": ids["party"], "kind": "can_introduce", "via_party": None,
        "note": "Synthetic staging QA suggested introduction only; no real-world introduction occurred."}, "vendor setup link")
    setup_receipts = []
    for step, verb, table in (("client", "new-client", "client"), ("deal", "new-deal", "deal"),
            ("vendor_party", "add-party", "party"), ("vendor", "new-vendor", "vendor"), ("vendor_entry", "log-activity", "vendor")):
        subject = ids["vendor" if step == "vendor_entry" else step]
        event = _relationship_exact(select("event", idempotency_key=values["keys"][step], verb=verb,
            subject_type=table, subject_id=subject), {"subject_id": subject}, "app creation event")
        if step == "vendor_entry" and event.get("new_value", {}).get("activity") != ids["vendor_entry"]:
            raise FixtureRefusal("relationship vendor original entry lacks its exact creation event")
        setup_receipts.append(event)
    for row, field, allowed in ((party, "city", RELATIONSHIP_NORTH), (vendor, "territory", RELATIONSHIP_SOUTH)):
        if row.get(field) not in (None, allowed):
            raise FixtureRefusal("relationship territory would alter a nonfixture value")
    common = {"created_by": system, "updated_by": system}
    specs = [
        ("destination_party", "party", {"id": destination["party_id"], "kind": "org", "name": values["party_name"],
            "notes_path": values["provenance"], "city": RELATIONSHIP_NORTH, "contact_state": "active", **common},
            {"deleted_at": None, "merged_into": None, "version": 1}),
        ("destination_client", "client", {"id": destination["client_id"], "party_id": destination["party_id"], "status": "engaged",
            "acquisition_source": "referral", "acquisition_detail": values["provenance"],
            "notes_path": values["provenance"], "owner_id": joe, **common}, {"merged_into": None, "version": 1}),
        ("destination_deal", "deal", {"id": destination["id"], "name": destination["name"], "client_id": destination["client_id"],
            "deal_type": "startup", "phase": "pending", "lane": "territory", "owner": "joe",
            "notes_path": values["provenance"], **common}, {"outcome": None, "version": 1}),
    ]
    for role, (link_id, from_party, to_party, deal_id) in zip(("referral", "vendor"), values["links"]):
        specs += [(role + "_association", "party_link", {"id": link_id, "from_party": from_party, "to_party": to_party,
            "kind": "referred", "via_party": None, "occurred_on": values["occurred_on"],
            "note": values["notes"][role], "source": "stated", "created_by": system}, {}),
            (role + "_attribution", "party_link_deal", {"link_id": link_id, "deal_id": deal_id, "referred_by": from_party,
            "occurred_on": values["occurred_on"], "note": values["notes"][role], "created_by": system}, {})]
    existing = []
    for role, table, expected, defaults in specs:
        if table == "party_link_deal":
            rows = select(table, link_id=expected["link_id"])
        elif table == "party_link":
            rows = _relationship_select(cur, table, {"id": expected["id"], "from_party": expected["from_party"],
                "to_party": expected["to_party"]}, lock=lock, either=True)
            rows = [r for r in rows if r["id"] == expected["id"] or
                    (r["from_party"] == expected["from_party"] and r["to_party"] == expected["to_party"] and r["kind"] == "referred")]
        else:
            collision = "party_id" if table == "client" else "name"
            rows = _relationship_select(cur, table, {"id": expected["id"], collision: expected[collision]}, lock=lock, either=True)
        if rows:
            existing.append((role, table, _relationship_exact(rows, {**expected, **defaults}, "destination or referral row")))
    if existing and len(existing) != len(specs):
        raise FixtureRefusal("relationship fixture is partial; reconcile without retrying writes")
    if existing and (party.get("city") != RELATIONSHIP_NORTH or vendor.get("territory") != RELATIONSHIP_SOUTH
                     or vendor.get("deal_history_verified_at") is not None):
        raise FixtureRefusal("relationship fixture has partial territory or vendor coverage state")
    return {"values": values, "system": system, "specs": specs, "existing": existing,
            "party": party, "vendor": vendor, "entry": entry, "setup_receipts": setup_receipts}


def install_relationship_fixture(conn: Any, plan: Mapping[str, Any]) -> int:
    try:
        with conn.cursor() as cur:
            # Prevent new role rows and same-name collisions between census and commit.
            cur.execute("lock table public.party,public.client,public.vendor,public.deal,public.party_link,"
                        "public.party_link_deal,public.activity,public.event in share row exclusive mode")
            state = _relationship_state(cur, plan, lock=True)
            count = 0
            if not state["existing"]:
                for _, table, row, _ in state["specs"]:
                    columns = ",".join(row)
                    cur.execute(f"insert into public.{table} ({columns}) values ({','.join(['%s'] * len(row))})", tuple(row.values()))
                    count += 1
            for table, row, field, value in (("party", state["party"], "city", RELATIONSHIP_NORTH),
                                            ("vendor", state["vendor"], "territory", RELATIONSHIP_SOUTH)):
                if row.get(field) is None or (table == "vendor" and row.get("deal_history_verified_at") is not None):
                    extra = ",deal_history_verified_at=null" if table == "vendor" else ""
                    cur.execute(f"update public.{table} set {field}=%s,updated_by=%s{extra} where id=%s",
                                (value, state["system"], row["id"]))
                    count += 1
            checked = _relationship_state(cur, plan, lock=True)
            if len(checked["existing"]) != 7:
                raise FixtureRefusal("relationship insertion did not read back completely")
            _read_relationship_projection(cur, checked["values"])
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise


def _relationship_projection(operation: str, payload: Mapping[str, Any] | None = None) -> Any:
    script = """import {networkStatement,projectNetwork} from './mcp-server/src/relationship-network.js';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const input=JSON.parse(readFileSync(0,'utf8'));
const network=input.operation==='sql' ? null : projectNetwork(input.snapshot,input.observed_at);
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
process.stdout.write(JSON.stringify(input.operation==='sql' ? networkStatement : {network,response_sha256:digest(network)}));
"""
    try:
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=REPO,
            input=json.dumps({"operation": operation, **(payload or {})}), text=True,
            capture_output=True, timeout=30, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise FixtureRefusal("normal relationship projection is unavailable; output suppressed") from exc


def _relationship_evidence(values: Mapping[str, Any], network: Mapping[str, Any], response_sha256: str) -> dict[str, Any]:
    ids, dest = values["ids"], values["destination"]
    expected_nodes = [("party:" + ids["party"], "client", ids["client"]),
        ("party:" + ids["vendor_party"], "vendor", ids["vendor"]),
        ("party:" + dest["party_id"], "client", dest["client_id"]),
        ("deal:" + ids["deal"], "deal", ids["deal"]), ("deal:" + dest["id"], "deal", dest["id"])]
    nodes = [_relationship_exact([n for n in network["nodes"] if n["id"] == node_id],
        {"kind": kind, "record_id": record_id}, "projected node") for node_id, kind, record_id in expected_nodes]
    if nodes[0].get("territory") != RELATIONSHIP_NORTH or nodes[1].get("territory") != RELATIONSHIP_SOUTH:
        raise FixtureRefusal("relationship projection lacks exact territory contrast")
    expected_edges = [(f"client-deal:{deal}", f"party:{party}", f"deal:{deal}", "client_deal")
        for party, deal in ((ids["party"], ids["deal"]), (dest["party_id"], dest["id"]))]
    for link, from_party, to_party, deal in values["links"]:
        expected_edges += [(f"link:{link}", f"party:{from_party}", f"party:{to_party}", "referred"),
                           (f"evidence:{from_party}:{deal}:referred", f"party:{from_party}", f"deal:{deal}", "referred")]
    edges = []
    for edge_id, from_node, to_node, kind in expected_edges:
        expected = {"from": from_node, "to": to_node, "kind": kind, "via": None}
        if kind == "referred":
            expected["detail"] = values["notes"]["referral" if from_node == "party:" + ids["party"] else "vendor"]
        edges.append(_relationship_exact([e for e in network["edges"] if e["id"] == edge_id], expected, "projected edge"))
    metrics = []
    for party in (ids["party"], ids["vendor_party"]):
        metric = _relationship_exact([r for r in network["referrals"] if r["node_id"] == f"party:{party}"],
            {"deals": 1, "won": 0, "lost": 0, "win_rate": None}, "owned referral metrics")
        metrics.append(metric)
    territories = {}
    for label, territory in (("inside", RELATIONSHIP_NORTH), ("outside", RELATIONSHIP_SOUTH)):
        member_ids = sorted(n["id"] for n in network["nodes"] if n.get("territory") == territory)
        referral_ids = sorted(e["id"] for e in network["edges"] if e["kind"] == "referred" and e["from"] in member_ids and e["id"].startswith("evidence:"))
        if not member_ids or not referral_ids:
            raise FixtureRefusal("relationship territory lacks referral membership")
        territories[label] = {"territory": territory, "node_ids": member_ids, "node_count": len(member_ids),
            "referral_ids": referral_ids, "referral_count": len(referral_ids),
            "territory_sha256": hashlib.sha256(territory.encode()).hexdigest(),
            "membership_sha256": _relationship_digest({"node_ids": member_ids, "referral_ids": referral_ids})}
    other_nodes = {n["id"] for n in network["nodes"] if isinstance(n.get("territory"), str)
                   and n["territory"].strip() and n["territory"] != RELATIONSHIP_NORTH}
    return {"schema": network["schema"], "observed_at": network["observed_at"],
            "valid_until": network["valid_until"], "response_sha256": response_sha256,
            "network_response_sha256": response_sha256, "content_sha256": _relationship_digest(network),
            "client_deal_edge_id": expected_edges[0][0], "evidence_edge_id": expected_edges[3][0],
            "evidence_detail_sha256": hashlib.sha256(values["notes"]["referral"].encode()).hexdigest(),
            "referral_metrics": metrics[0], "territory_sha256": hashlib.sha256(RELATIONSHIP_NORTH.encode()).hexdigest(),
            "total_referrals": len(network["referrals"]),
            "same_territory_referrals": sum(r["node_id"] in territories["inside"]["node_ids"] for r in network["referrals"]),
            "other_territory_referrals": sum(r["node_id"] in other_nodes for r in network["referrals"]),
            "nodes": nodes, "edges": edges,
            "metrics": metrics, "territories": territories}


def _read_relationship_projection(cur: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    cur.execute(_relationship_projection("sql"))
    snapshot = cur.fetchone()
    if not snapshot:
        raise FixtureRefusal("normal relationship snapshot is absent")
    observed = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    projected = _relationship_projection("project", {"snapshot": snapshot[0], "observed_at": observed})
    return {"response": projected["network"], "response_sha256": _relationship_digest(projected["network"]),
            "evidence": _relationship_evidence(values, projected["network"], projected["response_sha256"])}


def _relationship_logical_rows(state: Mapping[str, Any]) -> dict[str, Any]:
    stored = {role: row for role, _, row in state["existing"]}
    fields = {"destination_party": ("id", "name", "kind", "city"),
        "destination_client": ("id", "party_id", "status"),
        "destination_deal": ("id", "name", "client_id", "owner", "phase", "lane", "deal_type")}
    rows = {role: {key: stored[role][key] for key in keys} for role, keys in fields.items()}
    rows["fixed_party"] = {key: state["party"][key] for key in ("id", "city")}
    rows["owned_vendor"] = {key: state["vendor"][key] for key in ("id", "party_id", "territory")}
    for prefix in ("referral", "vendor"):
        association, attribution = stored[prefix + "_association"], stored[prefix + "_attribution"]
        common = {"kind": "referred", "via_party_id": None, "occurred_on": association["occurred_on"],
                  "note": association["note"], "attribution": "system"}
        rows[prefix + "_association"] = {**common, "id": association["id"],
            "from_party_id": association["from_party"], "to_party_id": association["to_party"]}
        # party_link_deal has a composite physical key; the app binds a logical UUIDv5 attribution ID.
        rows[prefix + "_attribution"] = {**common, "id": state["values"]["attribution_ids"][prefix],
            "party_id": attribution["referred_by"], "deal_id": attribution["deal_id"], "note": attribution["note"]}
    return rows


def read_relationship_fixture(conn: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read read only")
            state = _relationship_state(cur, plan, lock=False)
            if len(state["existing"]) != 7 or state["party"]["city"] != RELATIONSHIP_NORTH \
                    or state["vendor"]["territory"] != RELATIONSHIP_SOUTH \
                    or state["vendor"].get("deal_history_verified_at") is not None:
                raise FixtureRefusal("relationship committed readback is incomplete")
            projection = _read_relationship_projection(cur, state["values"])
        def receipt(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
            identity = {key: row[key] for key in (("link_id", "deal_id") if table == "party_link_deal" else ("id",))}
            key = str(uuid.uuid5(RELATIONSHIP_NAMESPACE, f"{plan['run']}:{table}:{_relationship_digest(identity)}"))
            return {"table": table, "row_ids": identity, "fixture_key": key, "row": dict(row), "row_sha256": _relationship_digest(row)}
        rows = _relationship_logical_rows(state)
        row_hashes = {role: _relationship_digest(row) for role, row in rows.items()}
        logical_tables = {"destination_party": "party", "destination_client": "client", "destination_deal": "deal",
            "fixed_party": "party", "owned_vendor": "vendor", "referral_association": "party_association",
            "vendor_association": "party_association", "referral_attribution": "deal_attribution", "vendor_attribution": "deal_attribution"}
        receipts = {role: {"operation": "update" if role in ("fixed_party", "owned_vendor") else "insert",
            "table": logical_tables[role], "row_id": row["id"],
            "before_sha256": row_hashes[role] if role in ("fixed_party", "owned_vendor") else None,
            "after_sha256": row_hashes[role]} for role, row in rows.items()}
        return {"rows": rows, "row_sha256": row_hashes, "receipts": receipts,
            "creation_receipts": [dict(receipt(table, row), role=role) for role, table, row in state["existing"]],
            "update_receipts": [dict(receipt(table, row), field=field, value=value,
                accepted_previous_values=[None, value]) for table, row, field, value in
                (("party", state["party"], "city", RELATIONSHIP_NORTH), ("vendor", state["vendor"], "territory", RELATIONSHIP_SOUTH))],
            "setup_creation_receipts": state["setup_receipts"], "vendor_original_entry": state["entry"],
            "projection": {key: projection[key] for key in ("response", "response_sha256")}, "projection_evidence": projection["evidence"]}
    finally:
        conn.rollback()


def build_relationship_fixture_proof(plan: Mapping[str, Any], target: Mapping[str, Any],
                                     readback: Mapping[str, Any]) -> dict[str, Any]:
    values = relationship_fixture_values(plan)
    return {"schema": RELATIONSHIP_SCHEMA, "fixture_run": plan["run"], "source_pair": dict(plan["release"]),
        "provenance": {"kind": "raw-sql-fixture", "readback": "separate-committed-readonly-connection"},
        "verified_target": dict(target), "recorded_plan_observed_at": plan["observed_at"],
        "tool_source_sha256": hashlib.sha256(pathlib.Path(__file__).resolve().read_bytes()).hexdigest(),
        "party_id": values["ids"]["party"], "client_id": values["ids"]["client"], "deal_id": values["ids"]["deal"],
        "relationship_referral_deal": values["destination"],
        "original_note_sha256": {role: hashlib.sha256(note.encode()).hexdigest() for role, note in values["notes"].items()}, **readback}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--invoice-plan", type=pathlib.Path)
    parser.add_argument("--tour-plan", type=pathlib.Path)
    parser.add_argument("--relationship-plan", type=pathlib.Path)
    parser.add_argument("--vendor-reference-data", action="store_true",
                        help="install the single synthetic staging vendor-stage reference row")
    parser.add_argument("--expected-app-sha")
    parser.add_argument("--expected-carr-sha")
    parser.add_argument("--proof-file", type=pathlib.Path)
    parser.add_argument("--aged-invoice", action="store_true")
    args = parser.parse_args()
    if args.relationship_plan:
        if args.invoice_plan or args.tour_plan or args.vendor_reference_data or args.aged_invoice:
            parser.error("relationship mode cannot be combined with another fixture mode")
        if not args.expected_app_sha or not args.expected_carr_sha:
            parser.error("relationship mode requires both explicit full source pins")
        if args.apply and not args.proof_file:
            parser.error("relationship apply requires --proof-file")
        if args.proof_file and not args.apply:
            parser.error("relationship proof requires --apply")
        try:
            plan = load_private_plan(args.relationship_plan, args.expected_app_sha, args.expected_carr_sha)
            relationship_fixture_values(plan)
            if not args.apply:
                print("staging-fixtures: PLAN relationship_rows=7 territory_updates=2 mutated=false")
                return 0
            validate_proof_destination(args.proof_file)
            dsn = os.environ.get("DATABASE_URL", "")
            project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
            endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
            validate_target(dsn, project, endpoint)
            target = validate_provider_binding(dsn, project, endpoint)
            import psycopg
            with psycopg.connect(dsn) as conn:
                count = install_relationship_fixture(conn, plan)
            with psycopg.connect(dsn) as conn:
                readback = read_relationship_fixture(conn, plan)
            proof = build_relationship_fixture_proof(plan, target, readback)
            result = publish_private_proof(args.proof_file, proof)
            print(f"staging-fixtures: PASS relationship_rows_changed={count} proof={result}")
            return 0
        except (FixtureRefusal, ImportError) as exc:
            print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("staging-fixtures: REFUSED — relationship fixture failed; details suppressed", file=sys.stderr)
            return 2
    if args.vendor_reference_data:
        if args.tour_plan or args.invoice_plan or args.aged_invoice:
            parser.error("vendor reference mode cannot be combined with invoice or Tour mode")
        if not re.fullmatch(r"[0-9a-f]{40}", args.expected_app_sha or "") \
                or not re.fullmatch(r"[0-9a-f]{40}", args.expected_carr_sha or ""):
            parser.error("vendor reference mode requires both explicit full source pins")
        if args.apply and not args.proof_file:
            parser.error("vendor reference apply requires --proof-file for durable fixture evidence")
        if args.proof_file and not args.apply:
            parser.error("--proof-file is valid only with vendor reference apply")
        try:
            if not args.apply:
                print("staging-fixtures: PLAN vendor_reference_rows=1 mutated=false")
                return 0
            validate_proof_destination(args.proof_file)
            dsn = os.environ.get("DATABASE_URL", "")
            project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
            endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
            validate_target(dsn, project, endpoint)
            target = validate_provider_binding(dsn, project, endpoint)
            import psycopg
            with psycopg.connect(dsn) as conn:
                count = install_vendor_reference_data(conn)
            with psycopg.connect(dsn) as conn:
                readback = read_vendor_reference_data(conn)
            proof = build_vendor_reference_proof(uuid.uuid4(), args.expected_app_sha,
                                                 args.expected_carr_sha, target, readback)
            proof_state = publish_private_proof(args.proof_file, proof)
            print(f"staging-fixtures: PASS vendor_reference_rows={count} proof={proof_state}")
            return 0
        except (FixtureRefusal, ImportError) as exc:
            print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("staging-fixtures: REFUSED — vendor reference fixture operation failed; details suppressed", file=sys.stderr)
            return 2
    if args.tour_plan and (args.invoice_plan or args.aged_invoice):
        parser.error("tour mode cannot be combined with invoice mode")
    if args.tour_plan:
        if not args.expected_app_sha or not args.expected_carr_sha:
            parser.error("tour mode requires both explicit full source pins")
        if args.apply and not args.proof_file:
            parser.error("tour apply requires --proof-file for durable fixture evidence")
        if args.proof_file and not args.apply:
            parser.error("--proof-file is valid only with tour-plan apply")
        try:
            plan = load_private_tour_plan(args.tour_plan, args.expected_app_sha, args.expected_carr_sha)
            if not args.apply:
                print("staging-fixtures: PLAN excluded_tour_stops=1 mutated=false")
                return 0
            validate_proof_destination(args.proof_file)
            dsn = os.environ.get("DATABASE_URL", "")
            project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
            endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
            validate_target(dsn, project, endpoint)
            target = validate_provider_binding(dsn, project, endpoint)
            import psycopg
            with psycopg.connect(dsn) as conn:
                count = install_excluded_tour_fixture(conn, plan)
            with psycopg.connect(dsn) as conn:
                readback = read_excluded_tour_fixture(conn, plan)
            proof_state = publish_private_proof(args.proof_file, build_tour_fixture_proof(plan, target, readback))
            print(f"staging-fixtures: PASS excluded_tour_stops={count} proof={proof_state}")
            return 0
        except (FixtureRefusal, ImportError) as exc:
            print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("staging-fixtures: REFUSED — Tour fixture operation failed; details suppressed", file=sys.stderr)
            return 2
    invoice_args = (args.expected_app_sha, args.expected_carr_sha)
    if bool(args.invoice_plan) != all(invoice_args) or (any(invoice_args) and not args.invoice_plan):
        parser.error("invoice mode requires --invoice-plan and both explicit full source pins")
    if args.invoice_plan and args.apply and not args.proof_file:
        parser.error("invoice apply requires --proof-file for durable fixture evidence")
    if args.proof_file and (not args.invoice_plan or not args.apply):
        parser.error("--proof-file is valid only with invoice-plan apply")
    if args.aged_invoice and not args.invoice_plan:
        parser.error("--aged-invoice is valid only with invoice-plan")
    if args.invoice_plan:
        try:
            plan = load_private_plan(args.invoice_plan, args.expected_app_sha, args.expected_carr_sha)
            if not args.apply:
                mode = " aged=true" if args.aged_invoice else ""
                print(f"staging-fixtures: PLAN invoice_rows=1 mutated=false{mode}")
                return 0
            validate_proof_destination(args.proof_file)
            dsn = os.environ.get("DATABASE_URL", "")
            project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
            endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
            validate_target(dsn, project, endpoint)
            target = validate_provider_binding(dsn, project, endpoint)
            import psycopg
            with psycopg.connect(dsn) as conn:
                count = install_invoice_fixture(conn, plan, aged=args.aged_invoice)
            with psycopg.connect(dsn) as conn:
                readback = read_invoice_fixture(conn, plan, aged=args.aged_invoice)
            proof = build_fixture_proof(plan, target, readback, aged=args.aged_invoice)
            proof_state = publish_private_proof(args.proof_file, proof)
            print(f"staging-fixtures: PASS invoice_rows={count} proof={proof_state}")
            return 0
        except (FixtureRefusal, ImportError) as exc:
            print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("staging-fixtures: REFUSED — invoice fixture operation failed; details suppressed", file=sys.stderr)
            return 2
    if not args.apply:
        print("staging-fixtures: PLAN synthetic_rows=1 mutated=false")
        return 0
    dsn = os.environ.get("DATABASE_URL", "")
    project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
    endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
    try:
        validate_target(dsn, project, endpoint)
        validate_provider_binding(dsn, project, endpoint)
        import psycopg
        with psycopg.connect(dsn) as conn:
            count = install(conn)
        print(f"staging-fixtures: PASS synthetic_rows={count}")
        return 0
    except (FixtureRefusal, ImportError) as exc:
        print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
