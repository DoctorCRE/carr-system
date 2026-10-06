"""The V5-F09 census route, DELETED, with one frozen answer left where it stood.

WHAT THIS MODULE NO LONGER HAS, first, because the deletion is the change.  It
used to perform the control-plane read (``read_workflow_truth_snapshot``), mint
an opaque handle for it (``read_workflow_truth_reading``, ``WorkflowTruthReading``,
``_MINT``, ``_MINTED``, ``_frozen``, ``_thawed``, ``_content_digest``) and render
that handle's capture for a consumer (``render_reading``).  Every one of those
names is gone from this file.  Nothing in this repository imports them; a grep
across the tree was the check, and the acceptance suite fails if any of them
comes back under any name.

WHY DELETION RATHER THAN A TENTH NARROWING.  Ten review rounds attacked the same
class and won every round: whatever the handle was made of, a caller sharing the
process could put its own census behind it -- by rebinding the module-level
snapshot function that ``read_workflow_truth_reading`` resolved at call time, by
writing the private mint registry, by a ``str``-subclass mapping key that ran
caller code during the thaw, by a ``__del__`` that rebound this module's exported
values while a consumer was between two reads of them.  The tenth review then
showed the last of those on the consumer side: rebinding the snapshot function
made ``tools/health-check.py`` print a caller's census as the control plane's own
answer, through ``run.sh health``.

None of that is closable from inside a Python module.  What is missing is not a
better object but an OWNER for the fact "this census is the one the control plane
served" -- a durable store that records what it served under an id and signs it,
so a consumer re-reads the census back from the store rather than trusting object
identity inside the caller's own process.  That store does not exist here.  The
standing rule is explicit about this case: where the authoritative owner of a
fact does not exist, the code reports unavailable and names the seam it is owed.

SO THE WHOLE ROUTE REPORTS UNAVAILABLE, INVARIANTLY.  ``workflow_truth_census()``
takes no argument, reads no module global, touches no caller object, runs no
query and has no branch.  It returns ``CENSUS_ROUTE_ANSWER``: one frozen mapping
built from string literals at import time and closed over by the function, so
rebinding any attribute of this module -- including ``CENSUS_ROUTE_ANSWER``
itself -- changes nothing about what the function returns.  ``tools/health-check.py``
prints that answer as its F09 section, which is what ``run.sh health`` shows.

THE STRINGS ARE DELIBERATELY WORD-CLEAN.  ``handle_integrity_unprovable``,
``not_proven`` and ``durable_signed_census_store_seam`` carry no word from the
standing rule's closed privileged union -- not even as a substring, which is why
the reason id is not spelled with the word it used to be spelled with.  A
surface that reports it cannot prove anything must not hand a grep, a log line or
a later editor a privileged word to lift out of it.

WHAT A REWIRE WOULD LOOK LIKE, the day the store exists: this module performs the
read through that store, hands back the id the store signed, and the consumer
re-reads it by id.  That is a new module against a new owner, not a resurrection
of the object graph deleted here, which is why the object graph is not kept
"for later".
"""
from __future__ import annotations

from types import MappingProxyType as _MappingProxyType
from typing import Any as _Any, Mapping as _Mapping

__all__ = ["SCHEMA_VERSION", "CENSUS_ROUTE_ANSWER", "workflow_truth_census"]

SCHEMA_VERSION = "control-plane-workflow-truth-census.v1"


def _bind_census_route() -> tuple[_Mapping[str, _Any], _Any]:
    """Build the one frozen answer and the function that returns it.

    The mapping is built here, at import time, out of string literals, and the
    function below closes over it.  A closure rather than a module global on
    purpose: a module global is an attribute any code in this process can rebind,
    and the tenth review's defect was exactly a consumer reading such an
    attribute.  Nothing this function does can be re-pointed by rebinding a name
    on this module, and there is no argument, no ``del`` and no attribute access
    on any caller object anywhere on the route, so no caller code can run inside
    the call at all.
    """
    answer: _Mapping[str, _Any] = _MappingProxyType({
        "schema_version": "control-plane-workflow-truth-census.v1",
        "available": False,
        "reason": "handle_integrity_unprovable",
        "item_disposition": "not_proven",
        "owed_seam": "durable_signed_census_store_seam",
    })

    def workflow_truth_census() -> _Mapping[str, _Any]:
        """THE F09 CENSUS ROUTE, AND IT REPORTS THAT IT CANNOT BE PROVEN.

        No argument, no global, no query, no branch: the same frozen mapping,
        every call, in every process, whatever else has been done to this module.
        """
        return answer

    return answer, workflow_truth_census


_CENSUS_ROUTE_BINDING = _bind_census_route()

# The frozen literal itself, exported so a consumer or a sweep can read what the
# route answers without calling it.  Rebinding THIS NAME does not change the
# route: the function returns the object the binding above closed over.
CENSUS_ROUTE_ANSWER = _CENSUS_ROUTE_BINDING[0]
workflow_truth_census = _CENSUS_ROUTE_BINDING[1]
