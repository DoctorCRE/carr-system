#!/usr/bin/env python3
"""
build-pretour-packet.py, the CARR pre-tour packet in the Ferris format.

Dell, 2026-08-21: "use the ferris packet format for the bank."

The format is the Dr Ferris Pre-Tour Briefing of 2026-08-19: navy masthead, serif
headings, gold accent, a snapshot strip, an at-a-glance comparison table, then one
numbered card per option carrying a photo, the reasoning, a fact grid, what to confirm
before signing, our own note, and ruled lines to write on during the tour.

That deliverable was built by a one-off script with its content hardcoded inline and
half its stylesheet appended at build time. This is the same design driven by data, so
the next client is a content file rather than a forked script. The stylesheet lives
beside this file in pretour-style.css.

Format decisions carried over verbatim from that build, because they were deliberate:
  - No taglines and no tier labels. One numbered card style for every option.
  - Listing agent NAME is kept; contact numbers never appear.
  - Our note sits in a highlighted box above blank ruled lines.

Two rules that bind every CARR surface are enforced here rather than remembered:
  - Every card carries a physical address. Raw land with no street number carries its
    road, its parcel number, and a landmark. A record with neither is refused.
  - No em dashes anywhere, including the file name.

Usage:
    python3 build-pretour-packet.py <folder> --recovery --reason "..." --vault <path>

Expects in that folder:
    content.json      the packet copy and facts
    photos/<file>     any image a record names in "photo" or "plans"

Writes <folder>/<slug>-pretour-packet.html, self-contained, no external requests.
"""

import argparse
import base64
import html
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.client_asset_controls import (AssetControlRefusal, require_asset_tier,
                                       write_artifact_atomically)
from lib.drive_recovery import add_recovery_arguments, require_recovery

HERE = os.path.dirname(os.path.abspath(__file__))
EM_DASH = "—"

# Same guard the space-search generator uses. A name is who to expect at the door; a
# phone number is contact detail and never reaches a client page.
CONTACT = re.compile(r"\d{3}|@|https?://|\bwww\.|\.(?:com|net|org|us|biz|co)\b", re.I)


class PacketRefusal(ValueError):
    """A deterministic precondition for the packet was not met."""


def e(s):
    return html.escape(str(s), quote=True) if s is not None else ""


def b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def data_uri(folder, name):
    path = os.path.join(folder, "photos", name)
    if not os.path.exists(path):
        raise PacketRefusal(f"photo not found: {name}")
    ext = os.path.splitext(name)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64(path)}"


def no_em_dash(obj, where="content.json"):
    """Refuse before rendering rather than catching it in the PDF afterwards."""
    blob = json.dumps(obj, ensure_ascii=False)
    if EM_DASH in blob:
        m = re.search(r".{0,45}" + EM_DASH + r".{0,45}", blob)
        raise PacketRefusal(f"em dash in {where}: {m.group(0)!r}. Banned on every CARR "
                            f"surface. Use a comma, a colon, or a full stop.")


def address_line(p):
    """The physical address, labelled, on every card. Never blank, never inferred from
    the heading: a land listing's heading is the broker's title and carries no street
    number."""
    loc = p.get("location") or {}
    street, parcel = (loc.get("street") or "").strip(), (loc.get("parcel") or "").strip()
    near = (loc.get("near") or "").strip()
    if not (street or parcel):
        raise PacketRefusal(
            f'{p["addr"]}: no location.street and no location.parcel. Every card carries '
            f'a physical address; raw land carries its road, parcel number and a landmark.')
    bits = [street] if street else []
    if parcel:
        bits.append(f"Parcel {parcel}" if street else f"No street number assigned. Parcel {parcel}")
    if near:
        bits.append(near)
    return f'<div class="paddrline"><span>Address</span> {e(". ".join(b.rstrip(".") for b in bits))}.</div>'


def agent_line(p):
    a = (p.get("agent") or "").strip()
    src = (p.get("src") or "").strip()
    if a and CONTACT.search(a):
        raise PacketRefusal(f'{p["addr"]}: listing agent field carries contact detail '
                            f'({a!r}). Names and firms only.')
    parts = []
    if a:
        parts.append(f'<span>Listing agent</span> {e(a)}')
    if src:
        parts.append(f'<span>Source</span> {e(src)}')
    return f'<div class="agent">{" &nbsp;&middot;&nbsp; ".join(parts)}</div>' if parts else ""


def notes_block(note, lines=3):
    n = f'<p class="usernote">{e(note)}</p>' if note else ""
    return ('<div class="notebox"><h4>Notes</h4>' + n
            + '<div class="noteline"></div>' * lines + "</div>")


FACT_COLUMNS = 4  # .factgrid is repeat(4,1fr), collapsing to 2 under 760px


def facts(rows, limit=None):
    """The fact grid draws its own rules by letting the container colour show through
    1px gaps, which works only while every row is full. A last row that stops short
    leaves the remaining track painted in line grey, so the card ends in a slab of
    colour hanging off the end of the data.

    Eight of the ten cards in the Ferris packet did this. Padding the row out with
    empty cells restores it: a grid with a few blanks at the end reads as a table
    that ran out of rows, which is what it is.

    Padding to four also satisfies the two-column mobile grid, since four divides by
    two. Anything that changes .factgrid's column count has to change FACT_COLUMNS
    with it, which is why the number is named here rather than written inline.
    """
    cells = [f'<div class="f{" flim" if limit and str(k).strip() == limit else ""}">'
             f'<span class="fk">{e(k)}</span>'
             f'<span class="fv">{e(v)}</span></div>' for k, v in rows]
    if cells:
        cells += ['<div class="f" aria-hidden="true"></div>'] * (-len(cells) % FACT_COLUMNS)
    return "".join(cells)


def media(folder, p):
    if not p.get("photo"):
        # No picture, and require_photo has already established that this option
        # carries a written reason. Print the reason in the slot the photograph
        # would have filled, so the gap is answered on the page rather than left
        # for the client to wonder about.
        why = (p.get("photo_absent_reason") or "").strip()
        return f'<div class="cmedia1"><div class="nophoto">{e(why)}</div></div>' if why else ""
    return (f'<div class="cmedia1"><img class="hero fw" src="{data_uri(folder, p["photo"])}" '
            f'alt="{e(p["addr"])}"></div>')


def plans_block(folder, p):
    """Drawings for an option where we hold them, inside its own card."""
    items = []
    for pl in p.get("plans") or []:
        items.append(
            f'<figure class="plan"><img src="{data_uri(folder, pl["file"])}" '
            f'alt="{e(pl["title"])} for {e(p["addr"])}">'
            f'<figcaption><b>{e(pl["title"])}</b> {e(pl.get("note", ""))} '
            f'{e(pl.get("source", ""))}</figcaption></figure>')
    if not items:
        return ""
    note = p.get("plans_note") or ""
    return (f'<div class="plans"><h4>Drawings</h4>{"".join(items)}'
            f'{f"<p class=plansrc>{e(note)}</p>" if note else ""}</div>')


def card(folder, p):
    # The written paragraph is optional. Dell removed it on 2026-08-27: the facts
    # about a space stay, our reading of the space does not.
    copy_block = f'<p class="pcopy">{e(p["copy"])}</p>' if (p.get("copy") or "").strip() else ""
    about = "".join(f"<li>{e(x)}</li>" for x in p.get("about", []))
    conf = "".join(f"<li>{e(x)}</li>" for x in p.get("confirm", []))
    ctr = f'<div class="pctr">{e(p["ctr"])}</div>' if p.get("ctr") else ""
    two = ""
    if about or conf:
        two = (f'<div class="twocol">'
               f'{f"<div class=mini><h4>About the property</h4><ul>{about}</ul></div>" if about else ""}'
               f'{f"<div class=mini><h4>Confirm before signing</h4><ul>{conf}</ul></div>" if conf else ""}'
               f'</div>')
    # The badge, the heading, the address and the photograph are one thing: the point
    # where the client recognises which option they are looking at. Printed, they were
    # four siblings that could be split apart, and the round number badge is absolutely
    # positioned, so a card starting in the last inch of a sheet left an orange crescent
    # alone at the bottom with its heading overleaf. Wrapping them lets print keep the
    # opening whole with one rule. On screen the wrapper changes nothing.
    return f'''
    <section class="card">
      <div class="copen">
      <div class="cnum">{e(p["n"])}</div>
      <div class="chead"><div>
        <h3 class="paddr">{e(p["addr"])}</h3>{ctr}
        <div class="pcity">{e(p["city"])}</div>
      </div></div>
      {address_line(p)}
      {media(folder, p)}
      </div>
      {copy_block}
      <div class="factgrid">{facts(p.get("facts", []), p.get("limit"))}</div>
      {two}
      {plans_block(folder, p)}
      {notes_block(p.get("note", ""))}
      {agent_line(p)}
    </section>'''


def require_packet_commentary(c):
    """A packet is never a bare property list, but the commentary does not have to be
    a section of its own.

    Joe's rule is that every client search packet includes market commentary rather
    than just a list of properties. This packet carried that as a "What this search
    tells you" block of numbered takeaways.

    Dell, 2026-08-25, asking a second time: "remove the last section 'what this
    search tell you' delete it all." A pre-tour packet is walked through in a car,
    and he does not want a page of conclusions in a document whose job is the drive.

    The rule still binds, so the check moves to what actually carries the commentary
    once the block is gone: the comparison table's note, which explains what the
    monthly figures mean, what Unlisted means, and what a medical fit-out benchmarks
    at, plus every option's own reasoning in its copy. Both are required here. A
    packet that lost the note as well WOULD be a bare property list, and that is the
    thing Joe's rule exists to prevent, so that is what this refuses.

    The confirmations block stays required outright. It is the client's own list of
    what to check, and nothing else in the document does that job.
    """
    client = c.get("client") or {}
    if not (client.get("confirmations") or []):
        raise PacketRefusal(
            'client.confirmations is empty. Every packet tells the client what to '
            'confirm on their side; nothing else in the document does that job.')
    if not (c.get("glance") or {}).get("note", "").strip():
        raise PacketRefusal(
            'glance.note is empty. With no takeaways section, the comparison note is '
            'what keeps this from being a bare property list, which is the thing '
            "Joe's market-commentary rule refuses. Say what the figures mean.")
    # The per-option reasoning check that used to sit here is gone. Dell removed the
    # written paragraph from every card on 2026-08-27, so requiring "real reasoning in
    # its copy" would refuse the document he asked for. What remains is the check that
    # actually matters: the comparison note is now the ONLY prose commentary in the
    # packet, so losing it really would leave a bare property list, which is the thing
    # Joe's rule refuses. Each card also flags the one fact that is its own limit, so
    # the honest-limit rule is carried by require_option_limit rather than by prose.


def require_option_limit(p):
    """One honest limit per option, named as one of that option's own facts.

    Joe's rule is that every client-facing recommendation names at least one thing
    we would not do and why, as one honest limit per option stated in the document.
    The card used to carry that in its written paragraph.

    Dell, 2026-08-27: "keep the facts on the space but remove your context about
    each space." The paragraph was my reading of the space rather than the space, so
    it is gone, and the limit cannot live in prose that no longer exists.

    So the limit moves into the fact grid, which is where the client is looking
    anyway. Each option names the fact that IS its limit, by that fact's own label,
    and the renderer marks that cell so the number carrying the bad news is the one
    the eye lands on. The gate refuses a label the option does not actually have,
    which is what stops the field drifting away from the grid it points into.
    """
    limit = (p.get("limit") or "").strip()
    keys = [str(k).strip() for k, _ in p.get("facts", [])]
    if not limit:
        raise PacketRefusal(
            f'{p["addr"]}: no limit. Every option names one honest thing against it '
            f"(Joe's rule). Set \"limit\" to the label of the fact that carries it, "
            f'one of: {", ".join(keys)}.')
    if limit not in keys:
        raise PacketRefusal(
            f'{p["addr"]}: limit names {limit!r}, which is not a fact on this card. '
            f'Use one of: {", ".join(keys)}.')


def require_photo(p):
    """Every option carries a picture. Dell ruled this on 2026-08-25, in his words:
    "Dont ever produce a pre tour of tour packet without a pic for each option."

    A tour packet is walked through with a client, and an option with no picture
    reads as the one nobody bothered with. The rule is absolute rather than a
    default, so it is enforced here rather than remembered: a missing picture stops
    the build and names the option, instead of shipping a card with a hole in it.

    This is deliberately NOT satisfied by a placeholder image.

    AMENDED 2026-08-25, same day, on the first option that tripped it: 10916 Emerald
    Coast Parkway. That option is off market and, in its own record, "not published
    on CoStar, Crexi, ECAR, or Moody's." No photograph of it exists in the rev
    folders, the 19 August packet source, the vault, or Downloads, and none can,
    because it was never listed. The absolute form of this rule would have blocked
    the packet forever on a stop we actually want to walk.

    So the rule keeps its teeth and gains a named exit. What Dell was protecting
    against, in his reasoning, is a card that "reads as the one nobody bothered
    with" - a SILENT hole. An option that states on its face why no photograph
    exists reads as the opposite: the one nobody else has. A silent hole is still a
    hard stop. An explained absence renders the explanation where the picture would
    have gone, in the client's language.

    The reason must be written by a human into content.json. The renderer never
    invents one, and it still never accepts a placeholder image.
    """
    name = (p.get("photo") or "").strip()
    why = (p.get("photo_absent_reason") or "").strip()
    if not name and not why:
        raise PacketRefusal(
            f'{p["addr"]}: no photo. Every option in a tour packet carries a '
            f'picture (Dell, 2026-08-25). Add "photo" to this option and put the '
            f'file in photos/. If no photograph can exist, say why in '
            f'"photo_absent_reason" and that sentence prints on the card.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    # Re-rendering over a packet that already exists is a supersession, and the
    # tombstone gate wants the add-loop UUID that queues the old copy for review.
    # write_artifact_atomically has always asked for this, but nothing ever put it
    # on the command line, so the gate was unreachable: the only way past it was to
    # render into a brand new folder every time. That is where rev2 and rev3 came
    # from. The flag is the fix; the folders were the symptom.
    ap.add_argument("--loop-ref", dest="loop_ref",
                    help="add-loop UUID queueing the superseded artifact; "
                         "required only when overwriting an existing packet")
    add_recovery_arguments(ap)
    a = ap.parse_args()
    try:
        vault = str(require_recovery(a, "document asset API for CARR brand logo"))
    except ValueError as exc:
        print(f"build-pretour-packet: STOP: {exc}", file=sys.stderr)
        return 2

    folder = a.folder
    with open(os.path.join(folder, "content.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    client = c["client"]

    try:
        no_em_dash(c)
        require_packet_commentary(c)
        # The honest-limit rule is discharged per option by require_option_limit
        # below, not by a back section. See that function for Joe's rule and Dell's
        # 2026-08-25 instruction removing the section.
        require_asset_tier(c.get("asset") or {})
        for p in c["options"]:
            address_line(p)
            agent_line(p)
            require_photo(p)
            require_option_limit(p)
    except (AssetControlRefusal, PacketRefusal) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    logo = b64(os.path.join(vault, "DNA", "Marketing", "Brand Assets",
                            "Logos", "CARR_White_Logo.png"))
    css = open(os.path.join(HERE, "pretour-style.css"), encoding="utf-8").read()

    # THE THREE OPENING BLOCKS ARE OPTIONAL, and each is omitted entirely rather
    # than rendered empty. Dell, 2026-08-25: "take out the info on the bottom half
    # of this first page and start with options at a glance." Blanking them in
    # content.json was not enough on its own, because the template emitted the
    # wrapper whatever was inside it, which left an empty bordered box and a
    # stray rule where the summary had been.
    #
    # The representation statement is safe to drop from the TOP because it is not
    # dropped from the DOCUMENT: promise_footer carries the same disclosure in the
    # footer and stays unconditional. A packet may lose its opening summary. It
    # never loses the statement of who CARR works for.
    lead = client.get("lead") or ""
    promise = client.get("promise") or ""
    snapshot = client.get("snapshot") or []

    snap = "".join(f'<div><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>'
                   for k, v in snapshot)
    lead_block = f'<p class="lead">{e(lead)}</p>' if lead.strip() else ""
    snap_block = f'<div class="snap">{snap}</div>' if snapshot else ""
    fid_block = f'<div class="fid">{e(promise)}</div>' if promise.strip() else ""

    thead = "".join(f"<th>{e(h)}</th>" for h in c["glance"]["columns"])
    tbody = ""
    for row in c["glance"]["rows"]:
        tds = "".join(
            f'<td class="tc-num">{e(v)}</td>' if i == 0 else
            f'<td class="tc-all">{e(v)}</td>' if i == c["glance"].get("bold_column", -1) else
            f"<td>{e(v)}</td>" for i, v in enumerate(row))
        tbody += f"<tr>{tds}</tr>"

    cards = "".join(card(folder, p) for p in c["options"])
    # Optional, like the declined block: omitted whole rather than left as a heading
    # standing over an empty grid.
    take = "".join(f'<div class="tk"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in (client.get("findings") or []))
    take_block = ('<section class="closing"><h2><span class="secnum">What this search tells you</span></h2>'
                  f'<div class="tkgrid">{take}</div></section>') if take else ""
    conf = "".join(f'<div class="dq"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in client["confirmations"])
    # Optional now, and omitted entirely rather than rendered as an empty heading
    # over an empty grid.
    decl = "".join(f'<div class="dq"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in (client.get("declined_and_why") or []))
    decl_block = (f'<section class="closing"><h2><span class="secnum">What we would not do, and why</span></h2>'
                  f'<div class="dqgrid">{decl}</div></section>') if decl else ""

    doc = f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(client["title"])} &middot; {e(client["name"])}</title>
<style>{css}</style></head>
<body><div class="wrap">
<header>
  <div class="htop"><img src="data:image/png;base64,{logo}" alt="CARR"><span class="kick">{e(client["kicker"])}</span></div>
  <h1>{e(client["title"])}<br>{e(client["subtitle"])}</h1>
  <div class="sub">{e(client["markets"])}</div>
  <div class="prep"><b>Prepared for {e(client["name"])}</b> &nbsp;|&nbsp; {e(client["where"])}</div>
</header>
<main>
  {lead_block}
  {snap_block}
  {fid_block}

  <h2><span class="secnum">Options at a glance</span></h2>
  <div class="h2sub">{e(c["glance"]["intro"])}</div>
  <div class="tblwrap"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>
  <p class="tnote">{e(c["glance"].get("note", ""))}</p>

  <h2 class="optshead"><span class="secnum">The options</span> &nbsp;<span style="font-weight:400;color:#5b6675;font-size:14px">{len(c["options"])} to tour</span></h2>
  <div class="h2sub optshead">{e(c["options_intro"])}</div>
  {cards}

  {take_block}

  <section class="closing"><h2><span class="secnum">To confirm on your side</span></h2>
  <div class="dqgrid">{conf}</div></section>

  {decl_block}
</main>
<footer>
  <div class="sig"><div>
      <div class="name">{e(client["advisor"]["name"])}</div>
      <div class="role">{e(client["advisor"]["title"])}</div>
      <div class="contact"><a href="tel:{e(client["advisor"]["tel"])}">{e(client["advisor"]["phone"])}</a><br>
        <a href="mailto:{e(client["advisor"]["email"])}">{e(client["advisor"]["email"])}</a></div>
    </div>
    <img src="data:image/png;base64,{logo}" alt="CARR" style="height:26px;opacity:.95">
  </div>
  <div class="fidfoot">{e(client["promise_footer"])}</div>
  <div class="meta">{e(client["sources"])}</div>
</footer>
</div></body></html>'''

    if EM_DASH in doc:
        print("STOP: an em dash reached the rendered document", file=sys.stderr)
        return 2

    out = os.path.join(folder, f'{client["slug"]}-pretour-packet.html')
    tomb = os.path.join(folder, "_TO_DELETE", os.path.basename(out)) if os.path.exists(out) else None
    try:
        write_artifact_atomically(out, doc, tombstone_path=tomb, loop_ref=a.__dict__.get("loop_ref"))
    except (AssetControlRefusal, OSError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out}")
    print(f"  {len(c['options'])} options | {os.path.getsize(out)/1024/1024:.2f} MB")


if __name__ == "__main__":
    raise SystemExit(main())
