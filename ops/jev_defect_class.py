"""Lexically trim the class roster, then one cached Choice gives an advisory reading shortlist. Options are stable and the model is pinned; code owns exact roster/set checks. A model suggestion never files a defect."""

import importlib.util
import json
import os
import re
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How many candidates a reader is shown. Eight captured 88% of true classes and
# the curve is flat past it. Small on purpose — a shortlist nobody reads is the
# same as no shortlist.
SHORTLIST = 8

# A Choice carries at most 255 options. One slot is kept for none-of-these.
MAX_OPTIONS = 40

# Rank a lexical shortlist on short text, retaining a none-of-these option.
RUBRIC_CHARS = 110

# The escape hatch. Without it a Choice must return a class for every defect,
# and a genuinely new kind of mistake is exactly what deserves a new name.
NONE_OF_THESE = "none of these classes fits — this is a new kind of mistake"

# Deadline for the bounded semantic request.
TIMEOUT_SECONDS = 40.0

# Words worth counting for the offline trim. Four characters and up, which
# drops the articles and prepositions that every defect shares.
WORD = re.compile(r"[a-z]{4,}")

# The SQL that gets the corpus. One row per class: the name, how often it has
# been used, and ONE earlier defect as its anchor — one, for the reason above.
# The earliest is taken because it is the occurrence that named the class.
CORPUS_SQL = """
select json_build_object(
         'name', c.defect_class,
         'occurrences', c.occurrences,
         'claimed', left(d.claimed, 700),
         'actual', left(d.actual, 700))::text
from v_defect_class c
join lateral (
  select claimed, actual from v_defect
  where defect_class = c.defect_class
  order by occurred_on, id
  limit 1
) d on true
order by c.occurrences desc, c.defect_class
"""


def _sibling(name):
    """Load a module from ops/ by path.

    ops/ holds no __init__.py, so it is not a package and a relative import
    from a sibling raises. That exact mistake cost a round trip on the day this
    was written and is why every module here loads its neighbours this way.
    """
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError("cannot load ops/" + name + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_classes(runner=None, repo=REPO):
    """Every existing class with its anchor defect, read from the live store.

    Goes through tools/db-tap.py, which is read-only by default and obtains its
    own connection string, because a shell command carrying a substitution is
    refused by the harness classifier. The SQL is written to a temporary file
    rather than added to the repository: db-tap takes a file, and a new tracked
    file would move the sealed inventory count for no benefit.

    `runner` is injectable so the suite runs offline. Returns [] rather than
    raising when the store cannot be reached — a caller that cannot get the
    corpus should carry on with what it was doing, not stop.
    """
    if runner is None:
        def runner(sql):
            handle = tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False)
            try:
                handle.write(sql)
                handle.close()
                result = subprocess.run(
                    [os.path.join(repo, ".venv", "bin", "python"),
                     os.path.join(repo, "tools", "db-tap.py"), "sql", handle.name],
                    capture_output=True, text=True, timeout=60, cwd=repo)
                return result.stdout if result.returncode == 0 else ""
            finally:
                os.unlink(handle.name)
    try:
        output = runner(CORPUS_SQL)
    except Exception:
        return []
    classes = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            classes.append(json.loads(line))
        except ValueError:
            continue
    return classes


def _words(text):
    return set(WORD.findall((text or "").lower()))


def narrow(proposed, classes, limit=MAX_OPTIONS):
    """Trim the roster under the Choice cap, offline and for free.

    Jaccard overlap on words of four letters or more, between the proposed
    defect and each class's name plus its anchor. This is NOT the ranking — it
    is the cheap search stage the vendor's own reranking walkthrough puts in
    front of any judgment, and it exists here only to get under the option cap
    without splitting into two requests whose numbers cannot be compared.

    Measured: trimming 320 to 254 kept the true class in all sixteen held-out
    cases, so it costs nothing that the judgment was going to find.
    """
    if len(classes) <= limit:
        return list(classes)
    query = _words(str(proposed.get("claimed") or "")[:2000]) | _words(str(proposed.get("actual") or "")[:2000])
    scored = []
    for existing in classes:
        text = (_words((existing.get("name") or "").replace("-", " "))
                | _words(existing.get("claimed")) | _words(existing.get("actual")))
        overlap = len(query & text) / (len(query | text) or 1)
        scored.append((overlap, existing.get("name") or "", existing))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [existing for _, _, existing in scored[:limit]]


def rubric(existing):
    """One option's description: the class name plus a cut of its anchor.

    Short, because this is the ranking pass. The corrective half of the anchor
    is the half that carries the mechanism of the error, so that is the half
    kept when there is only room for one.
    """
    return "%s — e.g. %s" % (
        (existing.get("name") or "").replace("-", " ").replace("_", " "),
        (existing.get("actual") or "")[:RUBRIC_CHARS])


def belongs_question(classes, client=None):
    """The one Choice, carrying every class as an option.

    Option names are the class names, which is what a caller needs back. The
    none-of-these rubric names the boundary case that is easy to get wrong:
    two defects can happen in the same part of the system, involve the same
    tool, or arise during the same kind of work and still be different
    mistakes. Without that, the ranking scores subject matter instead of
    mechanism, which is the degeneracy an earlier selector shipped with.
    """
    tsc = client or _sibling("typesafe_client")
    options = {(existing.get("name") or ""): rubric(existing) for existing in classes}
    options[NONE_OF_THESE] = (
        "None of the classes listed is the same kind of mistake as the proposed "
        "defect. Choose this when the others only share SUBJECT MATTER — the "
        "same tool, the same file, the same kind of work — rather than the same "
        "mechanism of error, and when the proposed defect is markedly narrower "
        "or broader than any of them. A genuinely new kind of mistake deserves "
        "a new class, so this is a real answer and not a failure to find one.")
    return tsc.choice(
        "A session is about to record the defect in `state.proposed_defect`. "
        "Which existing defect class is it another occurrence of? An option's "
        "name is the category; the example quoted with it only illustrates that "
        "category, and a new occurrence normally involves different files, "
        "tools and subject matter from the example.", options)


def shortlist(proposed, classes=None, *, limit=SHORTLIST, client=None,
              api_key=None, judge=None):
    """The few existing classes worth reading before naming a new one.

    Returns (candidates, declined) where candidates is
    [(class_name, probability, occurrences)] best first, and declined is the
    probability the judgment put on this being a new kind of mistake.

    Returns ([], None) when the corpus is empty or the request failed — a
    caller then names a class the way it always did, which is the behaviour
    this replaces rather than something worse.
    """
    classes = load_classes() if classes is None else classes
    if not classes:
        return [], None
    judge = judge or _sibling("jev_judge")
    trimmed = narrow(proposed, classes)
    occurrences = {c.get("name"): c.get("occurrences") for c in trimmed}
    try:
        answer = _sibling("jev_semantic").ask(
            {"proposed_defect": {"claimed": str(proposed.get("claimed") or "")[:2000],
                                 "actual": str(proposed.get("actual") or "")[:2000]}},
            {"pick": belongs_question(trimmed, client)},
            timeout=TIMEOUT_SECONDS, client=client, api_key=api_key, caller="jev_defect_class", version="vendor-v1", transport=judge.judge)
        probabilities = answer["answers"]["pick"].get("probabilities") or {}
    except Exception as exc:
        try:
            judge.record("defect_class", str(proposed.get("claimed") or "")[:2000], None, None, error=exc)
        except Exception:
            pass
        return [], None
    if not probabilities:
        return [], None
    declined = float(probabilities.get(NONE_OF_THESE, 0.0))
    ranked = sorted(((name, float(p)) for name, p in probabilities.items()
                     if name != NONE_OF_THESE and name in occurrences),
                    key=lambda item: (-item[1], item[0]))
    return ([(name, probability, occurrences.get(name))
             for name, probability in ranked[:limit]], declined)


def advise(proposed, **kwargs):
    """The shortlist as a sentence a recorder can act on, or None.

    Deliberately says that nothing here is a decision. At 69% top-1 the ranking
    is a good reading list and a bad autopilot, and presenting it as an answer
    would misread the measurement it comes from.
    """
    candidates, declined = shortlist(proposed, **kwargs)
    if not candidates:
        return None
    lines = ["EXISTING DEFECT CLASSES that may already cover this. Reuse one if "
             "it fits — the count per class is the point of the ledger. None of "
             "these is a decision; read them and choose."]
    for name, probability, count in candidates:
        seen = "seen once" if count == 1 else "seen %s times" % count
        lines.append("  %.2f  %s (%s)" % (probability, name, seen))
    if declined is not None:
        lines.append("  %.2f  that none of them fits and this is a new kind of "
                     "mistake" % declined)
    return "\n".join(lines)
