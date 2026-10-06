"""Is a Flash answer backed by what its scripts printed? Code, not a model, decides; the router hands an "invented"
answer to the Opus desk (ops/jev_model_route.handoff_reason). Measured on the 2026-09-24 Flash script tests.

'printed'  every answer number appears in script output, or is a printed number rounded to the answer's precision
           (revision 3 test, messy averages: the script printed avg 29.7393 and Flash answered 29.74, which the old
           exact-string check called invented and would have sent a right answer to Opus);
'derived'  the remaining whole numbers are a sum or difference of two printed numbers (kids 300 = 276 + 24);
'invented' otherwise (the 600-each and 300-each even splits, the made-up 30.00 average).
"""
from __future__ import annotations

import json
import re

_TIMEOUT = re.compile(r"\[timed out after \d+s\]")
_NUM = r"\d+(?:\.\d+)?"


def _printed(outs):
    """Script output as text, minus harness markers, which are not output ("[timed out after 600s]" once grounded
    a made-up 600-each split)."""
    return _TIMEOUT.sub("", "\n".join(outs)).replace(",", "")


def _appears(x, text):
    return re.search(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", text) is not None


def _rounds_to(x, printed):
    """A decimal answer x is a printed number rounded to x's decimals. The printed number must carry MORE decimals
    than x, so a different number that merely lies nearby (30.004 printed, 30.00 answered) needs that extra
    precision to count, and a whole number is never the source of a decimal answer."""
    places = len(x.split(".")[1])
    want = round(float(x), places)
    return any("." in p and len(p.split(".")[1]) > places and round(float(p), places) == want for p in printed)


def _even_split(answer):
    """Three or more categories with the identical count: the signature of an answer Flash made up when its script
    failed (600 each, 300 each). Checked before 'derived', because a sum or difference of two printed numbers matches
    such round values by coincidence (revision 3 test, varied wording repeat 3: 300 each was graded 'derived')."""
    try:
        a = json.loads(answer)
    except ValueError:
        return False
    vals = list(a.values()) if isinstance(a, dict) else []
    return len(vals) >= 3 and all(isinstance(v, int) for v in vals) and len(set(vals)) == 1


def answer_support(answer, outs):
    if _even_split(answer):
        return "invented"
    text = _printed(outs)
    printed = re.findall(r"(?<![\d.])" + _NUM + r"(?![\d])", text)
    nums = re.findall(_NUM, answer.replace(",", ""))
    missing = [x for x in nums if not _appears(x, text) and not ("." in x and _rounds_to(x, printed))]
    if not missing:
        return "printed"
    if any("." in x for x in missing):
        return "invented"
    vals = {float(p) for p in printed}
    pairs = {a + b for a in vals for b in vals} | {abs(a - b) for a in vals for b in vals}
    return "derived" if all(any(abs(float(m) - p) < 1e-9 for p in pairs) for m in missing) else "invented"
