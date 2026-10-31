"""Offline stand-in for Jev in the Call Mode rehearsal (CARR_POST_CALL_JEV_FIXTURE).

Hosted CI has no Jev key. Without a deal check every change stays a card, so
the rehearsal answers RIGHT DEAL the way Jev would for this synthetic call: the
candidate deal whose distinctive name word the quoted evidence says.
"""
import re
from types import SimpleNamespace


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def _deal(check):
    said = _words(check["item"]["evidence_excerpt"])
    for deal in check["candidate_deals"]:
        if _words(deal["name"]) - {"synthetic", "dental", "clinic", "lab", "lease", "relocation", "expansion"} & said:
            return deal["id"]
    return None


class _Client:
    def system_one(self, *, state, questions, model=None, retry=None):
        answers = {}
        for key in questions:
            if key == "deal_match":
                answers[key] = SimpleNamespace(choice=_deal(state), confidence=0.9)
            else:
                answers[key] = SimpleNamespace(noul=0.9 if key != "deal_in_candidates" else
                                               0.9 if _deal(state) else 0.1)
        return SimpleNamespace(answers=answers)


def client():
    return _Client()
