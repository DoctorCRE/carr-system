#!/usr/bin/env python3
"""Deterministic stand-in for headless Sonnet in the Call Mode rehearsal.

It reads the same request the real distiller receives on stdin and returns the
review pack a careful model should produce for the synthetic weekly call in
mcp-server/test/call-mode-rehearsal.test.mjs. Deal and party IDs come from the
request's CONTEXT by exact synthetic name, as the real model is told to do.
"""
import json
import sys

request = json.load(sys.stdin)
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


print(json.dumps({
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
}))
