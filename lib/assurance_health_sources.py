"""The A01 assurance-health label route, WHICH REPORTS THAT IT CANNOT BE PROVEN.

WHAT THIS IS NOW, AND WHY, BEFORE ANYTHING ELSE.  ``assurance_health_census`` is
the public route by which a health LABEL -- a state, a capability stage, a flag
-- could be derived for a workflow scope.  It derives none.  It takes NO
ARGUMENT, reads no module global, touches no caller object, and returns one
frozen mapping built from string literals at import time:

    available = False, reason = "handle_integrity_unprovable"

with the disposition ``not_proven`` and the seam that is owed.  The A01 item is
carried as ``not_proven`` in the pull request, the slice report and any catalog or
receipt row.

THE TEN ROUNDS THAT PRODUCED THAT ANSWER, compressed, because the shape of the
failure is the reason for the fallback.  Round after round the census could be
forged through a different door -- a caller-supplied census; a caller-supplied
receipt; a payload attribute on the reading handle; a stateful mapping honest
during verification and forged during rendering; an opaque registry key swapped
from inside its own ``__hash__``; a raw base-descriptor write to ``__class__``
that re-pointed method dispatch; rebinding the reader's module-level snapshot
function; writing its private mint registry -- and each round closed the exact
door used and left the CLASS open.

THE TENTH ROUND CLOSED THE LAST OF THEM BY DELETION, and it is worth naming the
defect it found, because it is the narrowest one yet.  The ninth cut took a
reading handle it did not use, wrote ``del reading`` to say so, and THEN read
three module-level strings to build its answer.  ``del`` on the only reference to
a caller-supplied object runs that object's ``__del__`` -- caller code, inside
this function, between entry and those reads -- and a review used it to rebind
the three strings and make this route answer ``available=false,
reason=caller_reason, item_disposition=verified``.  The lever was not the handle.
The lever was that there was ANY mutable state on the route and ANY window in
which caller code could run.

SO BOTH ARE GONE RATHER THAN HARDENED.  There is no parameter, so no caller
object is ever touched, so no ``__del__``, ``__hash__``, ``__eq__`` or descriptor
of a caller's can run inside this call.  There are no separate mutable exported
strings: the answer is ONE frozen ``MappingProxyType``, built at import time out
of literals, and the function closes over it rather than reading a module global
-- so rebinding ``LABEL_ROUTE_ANSWER``, or any other name on this module, changes
nothing about what the route returns.  The route has no branch, so there is
nothing for any input to select.

THE F09 CENSUS ROUTE WENT THE SAME WAY, in the same correction.
``lib/control_plane_workflow_truth_reader`` no longer performs a read, mints a
handle or renders one: the tenth review showed that rebinding its snapshot
function made ``tools/health-check.py`` print a caller's census as the control
plane's own answer through ``run.sh health``.  That module is now one frozen
unavailable answer too, and the health surface prints it.  Neither route on this
slice reports anything but unavailable.

THE STRINGS ARE DELIBERATELY WORD-CLEAN.  ``handle_integrity_unprovable``,
``not_proven`` and ``durable_signed_census_store_seam`` carry no word from the
standing rule's closed privileged union, not even as a substring -- which is why
the reason id is no longer spelled with the word "reading" in it.  A surface that
reports it cannot prove anything must not leave a privileged word for a grep, a
log line or a later editor to lift out of it.

WHAT IS MISSING, NAMED.  Not a better object: an OWNER for the fact "this reading
is the one the control plane served".  That owner is a durable store that records
what it served under an id and signs it, so a consumer re-reads the census back
from the store rather than trusting object identity inside the caller's own
process.  It does not exist in this repository.  ``_OWED_LABEL_AUTHORITY_SEAM``
below carries the long form of that sentence for a reader; the short seam NAME is
what the route returns.

WHAT IS KEPT, AND WHERE IT CAN BE REACHED FROM.  The classification logic that
would turn a reading into bound scopes is intact and MODULE-PRIVATE
(``_assurance_health_scopes``, ``_project``).  The acceptance suite and the
``--fixture`` test door reach it only through
``_would_be_assurance_health_if_authoritative``, whose answer comes back under a
hypothetical name no consumer can read as a state.  It is kept, unreached by any
public route, so the day the store exists this seam is a wiring job rather than a
rebuild.  ``ops/assurance-health-selftest.py :: sources_public_surface_guard_checks``
parses this file with ``ast`` and fails if any of that becomes public again.

THE ONE RULE THE PRIVATE PROJECTION ENFORCES, unchanged and still accurate.  A
LAYER A READING DID NOT CONTAIN IS DECLARED UNREAD, NEVER DEFAULTED.  The F09
census covered the control-plane workflow rows, the scheduler observation
receipts and the Completion Register.  It never covered independent artifact
reviews, attempt receipts, candidate outcome oracles, activation readbacks or
accepted outcome feedback.  Each of those is handed to the projection as
``UNREADABLE`` together with the name of the surface that would have to supply it
-- so a projected row can reach ``unknown``, ``disabled`` or
``not-yet-operational`` and can never reach green.  The gap is a named gap
instead of a silence.

AND THE CONTROLLER READBACK IS ONE OF THEM.  Collecting the observation receipts
is not the same act as ADMITTING one as current controller evidence: the
admitting surface -- whoever vouches that this receipt is the live state of that
scheduler -- does not exist in this repository.  So the decision procedure is the
same one every other layer gets, and it still distinguishes the cases a reader
needs to see:

  1. Collect the registered scheduler surfaces of the exact workflow identity
     that carry BOTH a ``scheduler_state`` and an ``observed_at``.
  2. Whatever the count, hand the layer over as ``UNREADABLE``: no evidence owner
     for it exists in this repository, so nothing here can make it current.
  3. Say in the notes WHAT the projected input held -- no receipt, exactly one, or
     two that would have been ambiguous anyway -- and name the seam that is owed
     before any of them could become evidence.  The count is diagnostic, and it is
     never the difference between unread and passing.

WHAT IT REFUSES TO INVENT.  An owner is taken from the checked-in workflow
manifest (``inventory.owner``); a workflow that declares none is reported as
unprojectable, naming the exact missing field, rather than being given a
placeholder owner so that a row can be printed.  A Work Request identity is never
synthesised: the census carries none, so every scope it binds is a workflow-only
scope whose business-outcome layer is ``unbindable`` and whose green state is
therefore unreachable by construction.
"""
from __future__ import annotations

# EVERY IMPORT IS UNDERSCORED, AND THAT IS THE POINT.  An imported name is a
# PUBLIC name of the importing module: re-exporting ``assurance_health`` here
# would put a second callable that accepts caller input on this module's surface,
# and re-exporting the reader would invite a consumer to perform the read and
# hand the result back in.  Aliasing them out makes the claim in ``__all__``
# literally true -- this module's public surface is one callable, and the one
# argument it takes is a reading no caller can build -- and lets the acceptance
# suite assert exactly that.  A caller that wants the domain or the reader
# imports it from the module that owns it.
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any as _Any

# The domain module's own surface is one schema id now (its eleventh-correction
# note says why), so these four names are its module-private bindings, imported
# under the same names they carry there.  ``closed_union_sweep_checks`` records
# this file as their consumer, name by name.
from lib.assurance_health import (
    _EVIDENCE_SLOTS,
    _OWED_EVIDENCE_OWNER_SEAMS,
    _AssuranceHealthContractError,
    _assurance_health,
)
from lib.control_plane_workflow_truth import (
    SCHEMA_VERSION as _WORKFLOW_TRUTH_SCHEMA_VERSION,
    UNREADABLE as _UNREADABLE,
)
# NOT ONE NAME FROM THE READER, NOT EVEN A TYPE.  The handle type used to be
# imported here to annotate the parameter this route no longer has.  There is no
# parameter, the reader mints nothing any more, and an import of a module this one
# derives nothing from would be a claim about a relationship that no longer exists.
from types import MappingProxyType as _MappingProxyType

SCHEMA_VERSION = "assurance-health-sources.v1"

# THE PUBLIC SURFACE, EXHAUSTIVELY.  One callable that accepts NOTHING, the frozen
# answer it returns, and the schema id.  No census, row set, clock, path or handle
# can arrive through this surface because there is no parameter anywhere on it,
# and everything that turns a reading into a state is absent from this list.
# ``UNREAD_LAYER_SOURCE`` was public here and is private now: its prose describes
# what each layer WOULD be read from, and an exported string is a string a sweep
# must clear.
__all__ = [
    "SCHEMA_VERSION",
    "LABEL_ROUTE_ANSWER",
    "assurance_health_census",
]

# THE INVARIANT ANSWER OF THE PUBLIC LABEL ROUTE, FROZEN, AND THE ONLY STATE ON
# THE ROUTE AT ALL.
#
# WHY A SINGLE FROZEN MAPPING RATHER THAN THREE STRINGS.  The ninth cut exported
# ``LABEL_ROUTE_UNAVAILABLE_REASON``, ``LABEL_ITEM_DISPOSITION`` and
# ``OWED_LABEL_AUTHORITY_SEAM`` as three ordinary module globals and READ THEM
# INSIDE THE ROUTE, after a ``del`` that could run caller code.  A review rebound
# all three from a ``__del__`` and made this route answer ``item_disposition:
# verified``.  Three mutable names plus a window is all a forgery ever needed.
#
# WHAT REPLACES THEM.  One ``MappingProxyType`` over a dict of string literals,
# built once at import time.  It cannot be mutated: ``answer["reason"] = ...``
# raises ``TypeError``.  And the route does not reach it by NAME -- the factory
# below closes over it -- so rebinding ``LABEL_ROUTE_ANSWER`` on this module
# leaves what the route returns untouched, byte for byte.
#
# THE SEAM VALUE IS A NAME, NOT PROSE.  The long sentence lives in
# ``_OWED_LABEL_AUTHORITY_SEAM`` below for a human reader; what the route returns
# is the seam's short identifier, so that no exported string on this surface
# carries a word from the standing rule's privileged union.
_OWED_LABEL_AUTHORITY_SEAM = (
    "a durable store that proves a census's integrity itself: it records what it "
    "served under an id and signs it, and a consumer re-reads that id back from the "
    "store before deriving anything from it, so 'is this the census the control "
    "plane performed' is answered by the store rather than by Python object "
    "identity inside the caller's own process. No such store exists in this "
    "repository, so no label derived in this process is proven, and this route "
    "reports that instead of a state.")


def _bind_label_route():
    """Build the one frozen answer and the function that returns it.

    A closure, not a module global, and that is the whole point: a module global
    is an attribute any code in this process can rebind, and the tenth review's
    defect was a route that read three of them.  The function below has no
    parameter, no ``del``, no attribute access on anything a caller owns, and no
    branch -- so there is no window in which caller code can run inside the call,
    and nothing for an input to select.
    """
    answer = _MappingProxyType({
        "schema_version": "assurance-health-sources.v1",
        "available": False,
        "reason": "handle_integrity_unprovable",
        "item_disposition": "not_proven",
        "owed_seam": "durable_signed_census_store_seam",
    })

    def assurance_health_census():
        """THE PUBLIC LABEL ROUTE, AND IT REPORTS THAT IT CANNOT BE PROVEN.

        ONE OUTCOME, INVARIANT, AND NOTHING TO HAND IT.  This function takes no
        argument at all, so there is no census, receipt, handle, clock or path a
        caller can put into it and no caller object for it to touch.  It reads no
        module global; ``answer`` is the frozen mapping the factory above closed
        over.  It returns that mapping: ``available`` is ``False``, ``reason`` is
        ``handle_integrity_unprovable``, the disposition is ``not_proven``, and
        ``owed_seam`` names the durable store this repository does not have.

        WHY IT TAKES NOTHING ANY MORE.  It used to take the reading the F09 reader
        minted -- first to project it, then (after the projection was withdrawn)
        merely to keep the shape of the owed seam visible at the call site, with
        ``del reading`` to mark it unused.  That ``del`` ran the caller's
        ``__del__`` inside this function and a review used it to rebind the three
        strings this route then read.  A parameter that is never used is not
        documentation; it is a window.  The seam is documented in the module
        docstring, where it costs nothing.

        WHAT A CONSUMER SHOULD DO WITH THIS.  Print it as unavailable and carry the
        A01 item as ``not_proven`` naming the owed seam, which is what
        ``tools/health-check.py`` does.  Do not fall back to the private
        projection: it is reachable only through an unexported hypothetical hook
        whose answer is named for what it is worth, and a consumer that reads that
        name as a state has reintroduced exactly the defect this fallback closes.
        """
        return answer

    return answer, assurance_health_census


_LABEL_ROUTE_BINDING = _bind_label_route()

# The frozen answer itself, exported so a consumer or a sweep can read what the
# route says without calling it.  Rebinding THIS NAME does not change the route.
LABEL_ROUTE_ANSWER = _LABEL_ROUTE_BINDING[0]
assurance_health_census = _LABEL_ROUTE_BINDING[1]

# The exact source each layer this reading does NOT contain would have to come
# from.  Naming it is the whole difference between an unread layer and a silence.
_UNREAD_LAYER_SOURCE = {
    "artifact_assessment":
        "an independent artifact review bound to this scope's exact repository commit "
        "and tree; the F09 census reads no reviewed-artifact surface",
    "execution_assessment":
        "an attempt receipt with its envelope digest and plan hash; the F09 census "
        "reads job rows, which are not that receipt",
    "candidate_outcome_oracle":
        "a preactivation candidate-outcome oracle receipt; the F09 census reads no "
        "oracle surface",
    "activation_readback":
        "a controller readback taken after activation, carrying its activation id; the "
        "F09 census reads no activation surface",
    "actual_business_outcome":
        "an accepted sourced outcome-feedback receipt joined through a Work Request "
        "identity; the F09 census reads no outcome-feedback surface",
    "controller_assessment":
        "a surface that ADMITS a reading of ops.legacy_schedule_observation_receipt as "
        "the live state of that scheduler; reading the receipt rows, which this seam "
        "does, is not the same act as vouching that one of them is current",
}


def _workflow_identity(key: _Any, version: _Any) -> str:
    return f"{key}@v{version}"


def _observation_records(surfaces: _Any, key: _Any, version: _Any) -> tuple[
        list[tuple[dict[str, _Any], dict[str, _Any]]], list[str]]:
    """Every readback this reading holds for one exact workflow identity."""
    if not isinstance(surfaces, list):
        return [], []
    found: list[tuple[dict[str, _Any], dict[str, _Any]]] = []
    ids: list[str] = []
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        if surface.get("workflow_key") != key or surface.get("workflow_version") != version:
            continue
        observation = surface.get("observation")
        if not isinstance(observation, dict):
            continue
        if not observation.get("scheduler_state") or not observation.get("observed_at"):
            continue
        found.append((surface, observation))
        ids.append(str(surface.get("surface_id")))
    return found, sorted(ids)


def _controller_evidence(surfaces: _Any, key: _Any, version: _Any,
                         notes: list[str]) -> _Any:
    """Always UNREAD, and specific about what this reading actually held.

    There is no evidence owner for this layer in this repository, so no count of
    receipts -- read here or supplied by anybody -- can make it current.  What
    the count still earns is an accurate note: a reader learns whether the
    reading held no receipt, one, or two that no admission could have chosen
    between.
    """
    _, ids = _observation_records(surfaces, key, version)
    if not ids:
        held = "this reading holds no scheduler observation receipt for this workflow"
    elif len(ids) == 1:
        held = f"this reading holds one scheduler observation receipt ({ids[0]})"
    else:
        held = (f"this reading holds {len(ids)} scheduler observation receipts "
                f"({', '.join(ids)}), which one controller assessment could not have "
                "chosen between in any case")
    notes.append(f"controller_assessment: {held}; it is UNREAD, not current — "
                 f"{_OWED_EVIDENCE_OWNER_SEAMS['controller_assessment']}")
    return _UNREADABLE


def _owner(owners: _Any, key: str, version: int) -> str | None:
    if not isinstance(owners, dict):
        return None
    owner = owners.get(_workflow_identity(key, version))
    return owner if isinstance(owner, str) and owner.strip() else None


def _assurance_health_scopes(workflows: _Any) -> dict[str, _Any]:
    """Turn one workflow-truth reading into bound scopes.  MODULE-PRIVATE.

    Private because its ``workflow_truth`` output is its input row travelling
    onward: a caller that could reach this could choose what the projection then
    classifies.  What it is given is the reading the F09 reader performed --
    ``assurance_health_census`` thaws it out of the handle that reader minted --
    and nothing else supplies it at all.

    Returns ``{"available": False, "reason": ...}`` when the reading itself is
    absent -- an unavailable reading is reported as unavailable and never as an
    empty census.
    """
    if not isinstance(workflows, dict):
        return {"available": False, "reason": "no workflow-truth reading was supplied"}
    if not workflows.get("available"):
        return {"available": False,
                "reason": str(workflows.get("reason") or "the workflow census is unavailable")}
    census = workflows.get("census")
    if not isinstance(census, dict) or census.get("schema_version") != \
            _WORKFLOW_TRUTH_SCHEMA_VERSION:
        return {"available": False,
                "reason": "the workflow census is not a row set projected by "
                          f"lib/control_plane_workflow_truth ({_WORKFLOW_TRUTH_SCHEMA_VERSION})"}
    rows = census.get("rows")
    if not isinstance(rows, list):
        return {"available": False, "reason": "the workflow census carries no rows"}

    surfaces = workflows.get("surfaces")
    owners = workflows.get("owners")

    scopes, unprojectable, notes = [], [], {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, version = row.get("workflow_key"), row.get("workflow_version")
        if not isinstance(key, str) or not isinstance(version, int) or isinstance(version, bool):
            continue
        identity = _workflow_identity(key, version)
        owner = _owner(owners, key, version)
        if owner is None:
            unprojectable.append({
                "workflow": identity,
                "reason": "ops/config/control-plane-workflows.v1.json declares no "
                          "inventory.owner for this workflow, and an owner is never invented"})
            continue
        row_notes: list[str] = []
        # EVERY layer is unread on this surface, so every slot is named and none
        # is defaulted.  The controller layer gets its own note because this
        # reading may actually be holding receipts that still are not evidence.
        evidence: dict[str, _Any] = {slot: _UNREADABLE for slot in _EVIDENCE_SLOTS}
        for slot, source in _UNREAD_LAYER_SOURCE.items():
            if slot == "controller_assessment":
                continue
            row_notes.append(f"{slot}: not read by this surface — it would come from {source}")
        evidence["controller_assessment"] = _controller_evidence(
            surfaces, key, version, row_notes)
        scopes.append({
            # No work_request_id: the census carries none, so this is a workflow-only
            # scope and its business-outcome layer is unbindable by construction.
            "scope": {"workflow_key": key, "workflow_version": version, "owner": owner},
            "workflow_truth": row,
            "evidence": evidence,
        })
        notes[identity] = row_notes

    return {"available": True, "scopes": scopes, "unprojectable": unprojectable,
            "input_notes": notes}


def _project(workflows: _Any, *, now: _Any) -> dict[str, _Any]:
    """Project the assurance-health census for one reading.  MODULE-PRIVATE."""
    bound = _assurance_health_scopes(workflows)
    if not bound["available"]:
        return {"schema_version": SCHEMA_VERSION, "available": False,
                "reason": bound["reason"]}
    try:
        projection = _assurance_health(scopes=bound["scopes"], now=now)
    except _AssuranceHealthContractError as exc:
        # The domain refused an input this adapter built. That is this adapter's
        # defect, and it is reported as one rather than printed as a health state.
        return {"schema_version": SCHEMA_VERSION, "available": False,
                "reason": f"the assurance-health projection refused these inputs ({exc})"}
    return {
        "schema_version": SCHEMA_VERSION,
        "available": True,
        "projection": projection,
        "unprojectable": bound["unprojectable"],
        "input_notes": bound["input_notes"],
    }


def _would_be_assurance_health_if_authoritative(workflows: _Any, *, now: _Any) -> dict[str, _Any]:
    """TEST-ONLY, MODULE-PRIVATE, and named for what its answer is worth.

    The acceptance suite has to drive the classification over fixture censuses --
    an ownerless workflow, a disabled one, two receipts for one identity, a
    receipt outside the observation window -- and it cannot read a control plane
    to get them.  So it reaches the private projection through this hook, whose
    result is returned under ``would_be_census_if_authoritative``: a name no
    consumer can read as a state this surface actually holds, because no surface
    holds it.  The hook is absent from ``__all__``, starts with an underscore,
    and is called from nowhere but ``ops/assurance-health-selftest.py``.

    What it does NOT do is widen the public surface.  A caller reaching a private
    name is not an exported route, and the guard in the acceptance suite fails
    the moment this name loses its underscore or enters ``__all__``.
    """
    return {"schema_version": SCHEMA_VERSION,
            "authoritative": False,
            "would_be_census_if_authoritative": _project(workflows, now=now)}
