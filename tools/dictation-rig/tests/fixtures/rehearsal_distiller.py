#!/usr/bin/env python3
"""Deterministic stand-in for headless Sonnet in the Call Mode rehearsal.

It reads the same request the real distiller receives on stdin and returns the
review pack a careful model should produce for the synthetic weekly call in
mcp-server/test/call-mode-rehearsal.test.mjs. Deal and party IDs come from the
request's CONTEXT by exact synthetic name, as the real model is told to do.
"""
import json
import sys
from typing import TypedDict


class RehearsalPack(TypedDict):
    schema_version: int
    report: dict[str, object]
    joe_tasks: list[dict[str, object]]
    dell_tasks: list[dict[str, object]]
    deal_updates: list[dict[str, object]]
    draft_proposals: list[dict[str, object]]
    review_questions: list[str]


request = json.load(sys.stdin)
if request.get("stage") == "coverage_history":
    print(json.dumps({"dispositions": [{"fact_id": f["fact_id"], "disposition": None} for f in request["facts"]]}))
    raise SystemExit(0)
deals = {deal["name"]: deal for deal in request["context"]["deals"]}
bayside = deals["Synthetic Bayside Dental Relocation"]
harbor = deals["Synthetic Harbor Clinic Expansion"]
pinecrest = deals["Synthetic Pinecrest Lab Lease"]
gulf = deals["Synthetic Gulf Imaging Center"]
magnolia = deals["Synthetic Magnolia Surgery Center"]
landlord = next(p for p in bayside["participants"] if p["name"] == "Synthetic Bayside Landlord")


def update(deal, summary, evidence, next_step=None, phase_move=None):
    return {"deal_id": deal["id"], "summary": summary, "participant_ids": [],
            "evidence": evidence, "next_step": next_step, "phase_move": phase_move}


pack: RehearsalPack = {
    "schema_version": 1,
    "report": {"summary": "Bayside moved to negotiation; Harbor booked a second walkthrough; Pinecrest phase is unresolved.",
               "decisions": ["Bayside is in negotiation."], "open_questions": []},
    "joe_tasks": [],
    "dell_tasks": [{"title": "Call the Harbor lender", "due_on": None, "deal_id": harbor["id"],
                    "participant_ids": [], "evidence": "I will call the Harbor lender this week"}],
    "deal_updates": [
        update(bayside, "LOI signed; Bayside is in negotiation.",
               "The LOI was signed on October 20, 2026, so Bayside moves to negotiation",
               phase_move={"value": "negotiation", "started_on": "2026-10-20", "needs_review": None}),
        update(bayside, "The lease draft goes to the landlord next.",
               "Next step for Bayside: send the lease draft to the landlord by November 6, 2026",
               next_step={"text": "Send the lease draft to the landlord", "next_date": "2026-11-06", "needs_review": None}),
        update(harbor, "Client toured both sites and preferred the second.",
               "Next step for Harbor: schedule the second walkthrough on November 3, 2026",
               next_step={"text": "Schedule the second walkthrough", "next_date": "2026-11-03", "needs_review": None}),
        update(gulf, "The floor plan goes to the architect.",
               "Next step for Gulf Imaging: confirm the floor plan with the architect by November 10, 2026",
               next_step={"text": "Confirm the floor plan with the architect", "next_date": "2026-11-10", "needs_review": None}),
        update(magnolia, "The space plan is next.",
               "Next step for Magnolia Surgery: order the space plan from the designer by November 14, 2026",
               next_step={"text": "Order the space plan from the designer", "next_date": "2026-11-14", "needs_review": None}),
        update(pinecrest, "Joe and Dell disagreed about moving Pinecrest to legal.",
               "I think it goes to legal",
               phase_move={"value": "legal", "started_on": None, "needs_review": "Joe said legal and Dell said keep it in site selection."}),
    ],
    "draft_proposals": [{
        "recipient_party_id": landlord["party_id"], "deal_id": bayside["id"],
        "subject": "Bayside lease update", "body": "The LOI is signed. We will send the lease draft by November 6.",
        "participant_ids": [landlord["party_id"]], "evidence": "email the Bayside landlord contact a short update",
    }],
    "review_questions": ["Pinecrest: Joe said legal and Dell said keep it in site selection. Which phase?"],
}

facts: list[dict[str, object]] = []
topics: dict[str, dict[str, object]] = {}

def link(item, kind, indices, parent=None):
    parent = parent or item
    fact_id = f"rehearsal-{len(facts)}"
    item["fact_ids"] = [fact_id]
    identity = {"deal_id": parent["deal_id"], "participant_ids": parent["participant_ids"]}
    topic_id = json.dumps(identity, sort_keys=True)
    topics[topic_id] = {"topic_id": topic_id, "identity": identity}
    spans = []
    if request.get("stage") == "coverage":
        sources = {source["segment_index"]: source for source in request["sources"]}
        spans = [{"source_id": sources[i]["source_id"], "segment_index": i, "char_start": 0,
                  "char_end": sources[i]["length"], "segment_sha256": sources[i]["sha256"]} for i in indices]
    statement = item.get("title", item.get("summary", item.get("text", item.get("value", item.get("subject")))))
    facts.append({"fact_id": fact_id, "topic_id": topic_id, "kind": kind, "statement": statement,
                  "asserted_value": item.get("value", statement), "source_spans": spans, "disposition": None})

link(pack["dell_tasks"][0], "commitment", [8])
for update_item, indices in zip(pack["deal_updates"], ([0, 1], [2], [3, 4], [9, 10], [11], [5, 6])):
    link(update_item, "status", indices)
    for field, kind in (("phase_move", "phase_change"), ("next_step", "next_step")):
        if update_item.get(field):
            link(update_item[field], kind, indices, parent=update_item)
link(pack["draft_proposals"][0], "communication", [7])
if request.get("stage") == "coverage":
    print(json.dumps({"topics": list(topics.values()), "facts": facts, "non_fact_spans": []}))
else:
    print(json.dumps(pack))
