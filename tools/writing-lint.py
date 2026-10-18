#!/usr/bin/env python3
"""
writing-lint.py — DNA/writing-rules.md as an executable gate (2026-07-25).

The zero-tolerance AI-tell list has been a document Claude was supposed to
remember. This turns the mechanically checkable part of it into a deterministic
check that runs BEFORE a draft reaches Joe. It does not replace the
writing-audit skill's judgment pass; it precedes it, the same way the ban list
precedes the judgment checks inside writing-rules.md itself.

  run.sh lint <file> [--surface email|social|proposal|web]
  pbpaste | run.sh lint - --surface social

Severity:
  HARD    banned item with a per-instance signature. Any hit = do not ship.
  REVIEW  the rule IS a hard ban, but the pattern has legitimate uses in CRE
          prose (land, leverage, landscape, shift). A human decides. These
          never auto-pass.
  RATION  the rationed filler adverbs: legal once, a finding at two or more.

Exit 0 = no HARD findings. Exit 1 = at least one HARD finding.
Read-only; writes nothing. Source of truth stays DNA/writing-rules.md — when
that file gains a rule, add it here in the same edit.
"""
import re, sys, os

SURFACES = ("email", "social", "proposal", "web", "unknown")

# ---------------------------------------------------------------- masking
def mask(text):
    """Blank out code, URLs and link targets so they stop tripping the rules.
    Same-length replacement so every span still maps back to the original."""
    out = list(text)

    def blank(m):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "

    for pat in (r"```.*?```", r"`[^`\n]*`", r"https?://\S+", r"\]\([^)]*\)",
                r"^\s*\*.*?\*\s*$"):  # italic editorial note lines
        for m in re.finditer(pat, text, re.S | re.M):
            blank(m)
    return "".join(out)

# ---------------------------------------------------------------- rule table
# (id, severity, regex, what it is, what to do instead)
W = r"\b{}\b"

RULES = [
    # --- the one exact, zero-argument ban -------------------------------
    ("em-dash", "HARD", r"—",
     "Em dash. Hard ban on every surface a prospect could see.",
     "Recast as two sentences, or use a comma, colon or parentheses."),
    ("em-dash-ascii", "REVIEW", r"(?<=\S) -- (?=\S)",
     "Double hyphen doing an em dash's job.",
     "Same fix as an em dash: split the sentence or use a comma."),

    # --- the healthcare-audience spelling that must never slip ----------
    ("hipaa-spelling", "HARD", W.format("hippa"),
     "HIPAA misspelled (rule 412d37d3: it is always HIPAA — Health Insurance "
     "Portability and Accountability Act). To a healthcare audience the "
     "misspelling reads as not knowing the industry.",
     "Spell it HIPAA."),

    # --- flagged vocabulary, unambiguous senses -------------------------
    ("vocab", "HARD",
     W.format("(?:delve|delves|delved|delving|unlock|unlocks|unlocked|unlocking"
              "|unleash|unleashes|unleashed|unleashing|harness|harnesses|harnessed|harnessing"
              "|elevate|elevates|elevated|elevating|unveil|unveils|unveiled|unveiling"
              "|revolutionize|revolutionizes|revolutionized|revolutionizing|revolutionary"
              "|supercharge|supercharged|supercharging|turbocharge|turbocharged"
              "|skyrocket|skyrockets|skyrocketed|skyrocketing|catapult|catapults|catapulted"
              "|seamless|seamlessly|cutting-edge|game-changing|game-changer|transformative"
              "|holistic|holistically|tapestry|realm|realms|testament|myriad|plethora"
              "|treasure trove|foster|fosters|fostered|fostering|facilitate|facilitates"
              "|facilitated|facilitating|empower|empowers|empowered|empowering"
              "|streamline|streamlines|streamlined|streamlining|robust|paradigm shift"
              "|beacon|multifaceted|meticulous|meticulously|intricate|paramount"
              "|embark|embarks|embarked|embarking|ever-evolving|quietly|hold space"
              "|built different|actually)"),
     "Flagged AI-tell vocabulary (writing-rules.md hard-ban list).",
     "Use the plain word. If no plain word fits, the sentence is doing too little."),

    ("corporate-cliche", "HARD",
     W.format("(?:synergy|synergies|utilize|utilizes|utilized|utilizing|touch base"
              "|circle back|value-add|drive impact|unlock value|strategic alignment"
              "|operational excellence)"),
     "Corporate cliché (banned in templates.md and restated in writing-rules.md).",
     "Say the concrete thing that actually happens."),

    ("leverage-verb", "HARD",
     r"\b(?:leverages|leveraged|leveraging)\b|\bleverage (?:the|our|your|their|its|a|an|this)\b",
     "'Leverage' as a verb. (The noun, as in negotiating leverage, is fine and stays legal.)",
     "Use, apply, or name the specific action."),

    ("earn-abstraction", "HARD",
     r"\bearn(?:s|ed|ing)?\s+(?:trust|credibility|respect|the right|its keep|their trust)\b",
     "'Earn' applied to an abstraction.",
     "State what was actually done that a reader can verify."),

    # --- openers, setups, endings ---------------------------------------
    ("generic-opener", "HARD",
     r"(?:in today's fast-paced world|in an era of|in a world where|ever wondered"
     r"|picture this|here's the deal|here's the truth|here's the kicker"
     r"|let's dive in|welcome to the world of)",
     "Generic AI opener.",
     "Open on the specific fact, name, number or observation."),

    ("faux-insight", "HARD",
     r"(?:what nobody tells you|the part everyone misses|what most people get wrong"
     r"|what if I told you|plot twist:|think about it:)",
     "Faux-insight rhetorical setup.",
     "Make the claim directly. If it needs a drumroll it isn't insight."),

    ("empty-phrase", "HARD",
     r"(?:it'?s worth noting|it is worth noting|it'?s important to note"
     r"|it is important to note|at the end of the day|the reality is"
     r"|the truth is|at its core)",
     "Empty phrase.",
     "Delete it. The sentence after it is the sentence."),

    ("puffery", "HARD",
     r"(?:marks a pivotal moment|plays a vital role|stands as a testament"
     r"|solidifies its position|underscores the significance|a pivotal moment"
     r"|speaks volumes)",
     "Importance puffery.",
     "State the fact and let the reader decide whether it matters."),

    ("weasel-attribution", "HARD",
     r"(?:experts agree|studies show|research shows|industry reports suggest"
     r"|many argue|widely regarded as|it is widely believed)",
     "Weasel attribution with no named source.",
     "Name the source and date, or cut the claim (cite-or-cut)."),

    ("trailing-ing", "HARD",
     r",\s+(?:highlighting|underscoring|showcasing|reflecting|signaling|signalling"
     r"|demonstrating|cementing|reinforcing|marking|solidifying)\b",
     "Trailing -ing clause asserting meaning the facts have not established.",
     "State the actual consequence as its own sentence, or cut the clause."),

    # --- constructions ---------------------------------------------------
    ("contrast-reframe", "HARD",
     r"(?:it'?s not about .{1,60}?,\s*it'?s about|that'?s not .{1,60}?,\s*that'?s"
     r"|it'?s not .{1,60}?,\s*it'?s|this isn'?t .{1,60}?,\s*it'?s"
     r"|not just .{1,60}?,\s*(?:but|it'?s))",
     "Contrast-reframe construction (the false-dichotomy shape).",
     "Drop the setup and assert the second half on its own."),

    ("contrast-reframe-split", "HARD",
     r"\b(?:isn'?t|is not|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t)\s+[^.!?\n]{1,55}[.!?]\s+"
     r"(?:It'?s|That'?s|They'?re|This is|It is|Those are)\b",
     "Contrast-reframe split across two sentences ('X isn't A. It's B.'). Same "
     "false-dichotomy shape as the comma form, just with a period in it.",
     "Cut the negation and assert the second half on its own."),

    ("contrast-compressed", "REVIEW",
     r"\b\w+,\s+not\s+\w+\b",
     "Possible compressed contrast-reframe ('X, not Y' dropped into a sentence). "
     "Joe caught this live on 2026-07-14; same tell, shorter setup.",
     "If it is the false-dichotomy shape, rewrite as a plain assertion. "
     "A genuine correction of fact is fine."),

    ("no-x-no-y", "HARD",
     r"\bNo [A-Za-z][\w' -]{1,25}\.\s+No [A-Za-z][\w' -]{1,25}\.",
     "'No X. No Y. Just Z.' construction.",
     "Say what the thing is, not the list of things it isn't."),

    ("colon-reveal", "REVIEW",
     r"(?m)(?:^|(?<=[.!?]\s))[A-Z][^.!?:\n]{4,45}:[ \t]+[a-z]",
     "Possible colon reveal (noun phrase, colon, dramatic payoff).",
     "Colons are for lists, labels and quotes. Write the sentence plainly."),

    # --- narrowed 2026 additions (real CRE words, so REVIEW not HARD) ----
    ("navigate", "REVIEW", W.format("(?:navigate|navigates|navigated|navigating)"),
     "'Navigate' is banned as a metaphor for handling a topic.",
     "Literal navigation is fine. Metaphorical use: name the actual task."),
    ("landscape", "REVIEW", W.format("(?:landscape|landscapes)"),
     "'Landscape' is banned as a metaphor.",
     "Literal landscaping is fine. Metaphorical use: say market, or the specific thing."),
    ("craft-verb", "REVIEW",
     r"\bcraft(?:ed|ing)\b|\bcraft (?:a|an|the|your|our)\b",
     "'Craft' as a verb for writing or building something.",
     "Write, build, put together."),
    ("vague-shift", "REVIEW", W.format("(?:shift|shifts|shifted|shifting)"),
     "'Shift' as a vague verb standing in for a specific change.",
     "Name the change: rents rose, the tenant left, the lender repriced."),
    ("vague-shape", "REVIEW", W.format("(?:shape|shapes|shaping)"),
     "'Shape' as a vague verb.",
     "Name what it actually does."),
    ("vague-land", "REVIEW",
     r"\b(?:will|would|might|could|really|doesn'?t|didn'?t|won'?t)\s+land\b|\blands differently\b",
     "'Land' in the 'this will land differently' sense. (Literal land is fine.)",
     "Say what the reader will do or think."),
    ("abstract-signal", "REVIEW",
     r"\b(?:a|the|clear|strong|early|real)\s+signals?\b",
     "'Signal' as an abstract noun for evidence.",
     "Name the evidence itself."),
    ("compound-metaphor", "REVIEW",
     W.format("(?:compound|compounds|compounding)"),
     "'Compound' as a growth metaphor.",
     "Financial compounding is legitimate. Metaphorical use: say what grows and how fast."),
    ("the-work", "REVIEW", r"\bthe work\b",
     "'The work' as a vague reverent noun.",
     "Name the work."),
    ("unearned-real", "REVIEW",
     r"\ba real (?:difference|impact|advantage|edge|problem|change|opportunity|shot|cost)\b",
     "'Real' as an unearned intensifier.",
     "Delete 'real' or replace it with the number."),

    # --- branding --------------------------------------------------------
    ("carr-caps", "HARD", r"(?<![\w./-])(?:Carr|carr)(?![\w./-])",
     "CARR is always all caps. It is the brand.",
     "Write CARR."),
    ("ai-caps", "REVIEW",
     r"(?<!CARR )(?<![\w./-])AI(?![\w./-])",
     "In our own copy A.I. is written with periods. (Folder names, paths and "
     "slugs are the documented exception.)",
     "Write A.I. unless this is a path, slug, or the 'CARR AI' folder name."),

    # --- Joe / Dell attribution -----------------------------------------
    ("dell-attribution", "HARD",
     r"(?<![\w'’])Dell(?:['’]?s?\s+"
     r"(?:\d+\+?\s*years|network|experience|tenure|background|relationships)\b"
     r"|\s+brings\b)",
     "Banned Dell attribution. Third correction on this theme; hard ban since 2026-07-16.",
     "Call it 'our network' or 'my network' in the first person."),
    ("dell-mention", "REVIEW", r"\bDell\b",
     "Joe's marketing defaults to solo-Joe. Partner framing is opt-in per piece.",
     "Remove the mention unless Joe directed partner framing for this specific piece."),
]

# ---------------------------------------------------------------- structural
FILLER = ("just", "literally", "honestly", "simply", "truly", "fundamentally",
          "importantly", "crucially", "inherently", "inevitably")


def sentences(masked):
    """(text, offset) per sentence. Crude on purpose; good enough for shape rules."""
    out, start = [], 0
    for m in re.finditer(r"[.!?]+[\s\n]+", masked):
        out.append((masked[start:m.end()].strip(), start))
        start = m.end()
    tail = masked[start:].strip()
    if tail:
        out.append((tail, start))
    return out


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def structural(text, masked, surface):
    """Rules that need counts or sentence shape rather than a single match."""
    found = []
    sents = sentences(masked)

    # rationed filler adverbs: legal once, a finding at two or more
    hits = [(m.start(), m.group(0)) for m in
            re.finditer(W.format("(?:" + "|".join(FILLER) + ")"), masked, re.I)]
    if len(hits) >= 2:
        found.append(("RATION", "filler-adverbs", hits[0][0],
                      ", ".join(h[1] for h in hits),
                      f"{len(hits)} rationed filler adverbs. The cap is one per piece.",
                      "Keep at most the one carrying real emphasis; cut the rest."))

    # rule of three used as a device more than once
    triads = list(re.finditer(r"\b[\w'-]+,\s+[\w'-]+,?\s+and\s+[\w'-]+\b", masked))
    if len(triads) >= 2:
        found.append(("REVIEW", "rule-of-three", triads[0].start(),
                      triads[0].group(0),
                      f"{len(triads)} three-item cadences. One is normal prose; "
                      "a piece leaning on the rhythm is the tell.",
                      "Break at least one into a different shape or cut an item."))

    # mirrored sentence skeletons: 3+ nearby sentences on the same frame
    for i in range(len(sents) - 2):
        heads = []
        for s, _ in sents[i:i + 3]:
            w = re.findall(r"[\w']+", s.lower())
            heads.append(" ".join(w[:2]) if len(w) >= 2 else None)
        if heads[0] and len(set(heads)) == 1:
            found.append(("REVIEW", "mirrored-skeletons", sents[i][1],
                          sents[i][0][:70],
                          f"Three consecutive sentences built on the same frame ('{heads[0]}...').",
                          "One deliberate contrast pair is fine. Stacked mirroring is the tell."))
            break

    # dramatic fragmentation: 3+ consecutive punch fragments
    run = 0
    for s, off in sents:
        if 0 < len(re.findall(r"[\w']+", s)) <= 4:
            run += 1
            if run == 3:
                found.append(("REVIEW", "fragmentation", off, s[:70],
                              "Three or more stacked short fragments used as rhythm.",
                              "Rejoin into full sentences unless one fragment is genuinely the point."))
                break
        else:
            run = 0

    # bullets / mid-sentence bold on a one-person-talking surface
    if surface in ("email", "social"):
        for m in re.finditer(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", masked):
            found.append(("HARD", "bullets-on-personal-surface", m.start(),
                          text[m.start():m.start() + 40].strip(),
                          f"Bullets on a {surface} surface, which should read as one person talking.",
                          "Write it as prose. Bullets are fine in a proposal or one-pager."))
            break
        for m in re.finditer(r"(?<=\w[ ,])\*\*[^*\n]+\*\*", masked):
            found.append(("HARD", "bold-on-personal-surface", m.start(),
                          text[m.start():m.start() + 40].strip(),
                          f"Mid-sentence bold on a {surface} surface.",
                          "Drop the emphasis; let the sentence carry it."))
            break

    # fake-profound kicker: the aphorism usually straddles the final two sentences
    # ("The future isn't coming. It's already here.") — check both.
    for last, off in sents[-2:]:
        if re.search(r"^(?:The|That|This|It'?s|We|You)\b.{0,60}"
                     r"\b(?:isn'?t|is not|doesn'?t|won'?t|already here|the whole thing)\b", last) \
                and len(re.findall(r"[\w']+", last)) <= 14:
            found.append(("REVIEW", "kicker-ending", off, last[:70],
                          "Closing line has the manufactured-aphorism shape.",
                          "End on the last concrete point or the next action."))
            break
    return found


# ---------------------------------------------------------------- runner
def lint(text, surface):
    masked = mask(text)
    found = []
    for rid, sev, pat, what, fix in RULES:
        flags = re.I if rid not in ("carr-caps", "ai-caps", "no-x-no-y",
                                    "colon-reveal", "dell-mention",
                                    "dell-attribution") else 0
        for m in re.finditer(pat, masked, flags):
            quote = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ").strip()
            found.append((sev, rid, m.start(), quote, what, fix))
    found += structural(text, masked, surface)

    order = {"HARD": 0, "REVIEW": 1, "RATION": 2}
    found.sort(key=lambda f: (order[f[0]], f[2]))
    return found


def main():
    argv = sys.argv[1:]
    surface = "unknown"
    if "--surface" in argv:
        i = argv.index("--surface")
        surface = argv[i + 1] if i + 1 < len(argv) else "unknown"
        del argv[i:i + 2]
    if surface not in SURFACES:
        sys.exit(f"--surface must be one of: {', '.join(SURFACES)}")
    if not argv:
        sys.exit(__doc__.strip().split("\n\n")[2])

    src = argv[0]
    if src == "-":
        text, label = sys.stdin.read(), "(stdin)"
    else:
        if not os.path.isfile(src):
            sys.exit(f"no such file: {src}")
        text, label = open(src, encoding="utf-8", errors="replace").read(), src

    found = lint(text, surface)
    hard = [f for f in found if f[0] == "HARD"]

    print(f"writing-lint · {label} · surface={surface} · "
          f"{len(hard)} HARD, {len([f for f in found if f[0]=='REVIEW'])} REVIEW, "
          f"{len([f for f in found if f[0]=='RATION'])} RATION")
    print("rules: DNA/writing-rules.md · judgment pass still belongs to the writing-audit skill\n")

    if not found:
        print("  clean on every mechanically checkable rule.")
    for sev, rid, pos, quote, what, fix in found:
        print(f"  [{sev}] {rid}  (line {line_of(text, pos)})")
        print(f"      ...{quote}...")
        print(f"      {what}")
        print(f"      fix: {fix}\n")

    if hard:
        print(f"FAIL · {len(hard)} hard-ban hit(s). Do not ship until these are zero.")
    else:
        print("PASS on hard bans." + ("  REVIEW items still need a human." if found else ""))
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
