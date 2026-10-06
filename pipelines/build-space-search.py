#!/usr/bin/env python3
"""
build-space-search.py, CARR client-facing space-search report generator.

Reads a normalized properties.json (schema v1, see DNA/Deal Management/space-search-sop.md)
and emits ONE self-contained HTML file: CARR brand fonts, logo, and photos all inlined as
data URIs, no external requests, works offline and as an email attachment.

Source-agnostic by design. CoStar, Moody's/Catylist, Crexi, and ECAR all normalize into the
same record shape, so the report looks identical no matter which platform supplied a field.
That consistency is the whole point of generating our own detail sheets instead of stapling
four different MLS PDF formats together.

HARD RULE: no listing-agent or brokerage contact information EVER reaches a client-facing
report (Joe, 2026-07-22). Broker data lives in the internal xlsx only. This generator has no
code path that renders it, the normalized schema simply has no field for it.

Usage:
    python3 build-space-search.py <search-folder>

Expects inside that folder:
    properties.json          the normalized records
    photos/<file>.jpg        any photos referenced by a record's "photo" key (optional)

Writes:
    <client-slug>-space-search-<date>.html
"""

import argparse
import base64, json, os, re, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.client_asset_controls import (AssetControlRefusal, require_asset_tier,
                                       require_declined_and_why, require_search_commentary,
                                       write_artifact_atomically)
from lib.drive_recovery import add_recovery_arguments, require_recovery

ASSETS: dict[str, str] = {}


def configure_recovery_assets(vault: str) -> None:
    """Set legacy assets only after the recovery boundary is selected."""
    brand = os.path.join(vault, "DNA", "Marketing", "Brand Assets")
    ASSETS.update({
        "OSWALD": f"{brand}/fonts/Oswald.ttf",
        "MONTSERRAT": f"{brand}/fonts/Montserrat.ttf",
        "LOGO_BLUE": f"{brand}/Logos/CARR_Solo_Blue_Logo.png",
        "LOGO_WHITE": f"{brand}/Logos/CARR_White_Logo.png",
    })


def b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def money(n):
    return "" if n is None else f"${n:,.0f}"


def sf(n):
    return "" if n is None else f"{n:,}"


NOT_PUBLISHED = '<span class="np">Not published</span>'

# The floor a printed photograph has to clear to look like a photograph. Listing
# sources cap out at wildly different sizes -- a CBRE memorandum carries 4500px
# originals, CoStar 650, and an MLS quick report only a 300px thumbnail -- and the
# report used to stretch every one of them across the full column regardless. A 300px
# image over a 7in column is 43 DPI, which is what "blurry" means. Photos are sized
# from their own pixels now: a small one prints small and SHARP rather than big and
# soft. Varying widths down the packet are the honest signal that the sources vary.
MIN_PHOTO_DPI = 150


def jpeg_size(path):
    """Pixel dimensions from the JPEG header. Deliberately no Pillow import: this
    generator has never needed an imaging dependency and should not grow one to read
    four bytes."""
    with open(path, "rb") as fh:
        data = fh.read()
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF15, excluding the non-frame markers DHT/JPG/DAC
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None, None


def val(v, fmt=str):
    """Render a value, or a visible 'Not published' when the source did not carry it.
    Gaps must stay visible, a blank cell reads as an oversight, a labelled gap reads
    as a known open item and becomes a question for the landlord."""
    return NOT_PUBLISHED if v in (None, "") else fmt(v)


def spec(label, value, em=False, note=None):
    cls = ' class="em"' if em else ""
    sub = f"<small>{esc(note)}</small>" if note else ""
    return f'<div><dt>{esc(label)}</dt><dd{cls}>{value}{sub}</dd></div>'


def band(lo, hi, unit="SF"):
    """A min-to-max span, or the visible gap when the span was never established."""
    if lo is None and hi is None:
        return NOT_PUBLISHED
    if lo is None or hi is None:
        return f'{sf(lo if lo is not None else hi)} {unit}'
    if lo == hi:
        return f'{sf(lo)} {unit}'
    return f'{sf(lo)} to {sf(hi)} {unit}'


def size_line(s):
    # A listing that never published a suite size gets the visible gap, same as any
    # other unknown. " SF" with nothing in front of it reads as a broken template.
    if s["min_sf"] is None:
        return NOT_PUBLISHED
    if s["min_sf"] == s["max_sf"] or s["max_sf"] is None:
        return f'{sf(s["min_sf"])} SF'
    return f'{sf(s["min_sf"])}&ndash;{sf(s["max_sf"])} SF'


def rate_line(r):
    if r["min"] is None:
        return NOT_PUBLISHED
    if r["max"] is None or r["min"] == r["max"]:
        return f'${r["min"]:,.2f} /SF/yr'
    return f'${r["min"]:,.2f}&ndash;{r["max"]:,.2f} /SF/yr'


def monthly_note(s, r):
    """Qualify the monthly figure with the area it is based on, and with what it leaves
    out. A listing with no published service type gets no clause rather than the word
    None, which is how an unset field used to reach the page."""
    if not r["monthly_at_contig"]:
        return None
    base = f'at {sf(s["contig_sf"])} SF' if s.get("contig_sf") else None
    if r["services"] == "Triple Net":
        return f'{base}, before NNN' if base else "before NNN"
    if r["services"]:
        return f'{base}, {r["services"]}' if base else str(r["services"])
    return base




# ---------------------------------------------------------------------------
# PURCHASE / INVESTMENT SUPPORT (added 2026-07-31, Le C-063 30/70 search)
#
# The generator was lease-only: every headline figure was rent per square foot
# per year. An owner-occupier or investment BUY answers different questions --
# what does it cost, what does it yield, who is in it, and how much of it can
# the client actually occupy -- so a purchase record carries a "sale" block and
# these renderers run instead of the rate ones.
#
# ADDITIVE BY DESIGN. Nothing fires unless a record carries "sale", so every
# existing lease search still renders byte for byte. Verified against Ferris.
# ---------------------------------------------------------------------------

def price_line(x):
    if x.get("price") is None:
        return NOT_PUBLISHED
    psf = f' &middot; ${x["price_psf"]:,.0f} /SF' if x.get("price_psf") else ""
    return f'{money(x["price"])}{psf}'


def yield_line(x):
    """Cap rate and NOI travel together or the number means nothing on its own."""
    cap = f'{x["cap_rate"]:.2f}%' if x.get("cap_rate") else None
    noi = money(x["noi"]) if x.get("noi") else None
    if cap and noi:
        return f'{cap} on {noi} NOI'
    return cap or (f'{noi} NOI' if noi else NOT_PUBLISHED)


def occupancy_line(x):
    """What the buyer can actually take, stated as a share of the building, because
    that share is the whole deal on an owner-occupier purchase.

    Returns (value, note). Splitting the two is the fix for a duplication that showed
    on every record without an own_sf -- land, and any building whose split is still
    an open question. The note was being returned AS the value and then passed to
    spec() as the note as well, so the same sentence printed twice, the second time in
    small grey type directly under itself. Land records are most of a bank's search,
    so it was on most of the pages."""
    own, bldg = x.get("own_sf"), x.get("building_sf")
    note = x.get("occupancy_note")
    if not own:
        return NOT_PUBLISHED, note
    pct = f' ({own / bldg:.0%} of the building)' if bldg else ""
    # square feet are whole numbers; a float reaching sf() prints "10,422.0 SF"
    return f'{sf(int(round(own)))} SF{pct}', note


def sale_specs(p, s, b):
    x = p["sale"]
    # "He could occupy" was written for one client, a single male buyer, and then read
    # every search after it. A bank, a partnership, or a woman buying a building all
    # got the same pronoun. The label is the client-neutral one now.
    occupies, occ_note = occupancy_line(x)
    return [
        spec("Price", price_line(x), em=True),
        spec("Yield", yield_line(x)),
        spec("Building", f'{sf(b["building_sf"])} SF' if b.get("building_sf") else NOT_PUBLISHED),
        spec("Could occupy", occupies, em=True),
        spec("Tenancy", val(b.get("tenancy"))),
        spec("Status", val(p["timing"]["status"])),
    ]


# ---------------------------------------------------------------------------
# TOUR-PACKET SUPPORT (added 2026-08-20, Harbor State Bank C-200 US-98 search)
#
# Three things a packet that gets CARRIED needs and a report that gets READ
# does not: who is meeting us at the door, somewhere to write while standing in
# the building, and the drawings for an option where we hold them.
#
# ADDITIVE BY DESIGN, exactly like the "sale" block. None of this fires unless a
# record carries "listing_agent", "notes", or "plans", so every prior search
# renders byte for byte unchanged. Proven by rendering this search's own records
# with the three blocks stripped against the pre-change generator.
# ---------------------------------------------------------------------------

# Joe's 2026-07-22 rule bars listing-agent and brokerage CONTACT detail from anything
# a client sees. Dell asked for the NAME on the River Bank packet, which is a different
# fact: it is who to expect at the door. The name is allowed; the contact detail is not,
# and this is what keeps the two apart. A whole source record pasted into the field
# fails loudly here rather than leaking a phone number onto a printed page.
_CONTACT = re.compile(
    r"""\d{3}      # any three consecutive digits: catches every phone shape
      | @          # email
      | https?://  # url
      | \bwww\.    # url without scheme
      | \.(?:com|net|org|us|biz|co)\b
    """, re.I | re.X)


class ContactLeak(ValueError):
    """A listing-agent field carried contact detail, which never reaches a client page."""


def agent_line(p):
    """Name and firm only. Either may be absent; a record with neither renders nothing."""
    a = p.get("listing_agent")
    if not a:
        return ""
    name, firm = (a.get("name") or "").strip(), (a.get("firm") or "").strip()
    for label, value in (("name", name), ("firm", firm)):
        if _CONTACT.search(value):
            raise ContactLeak(
                f'{p["address"]}: listing_agent.{label} contains contact detail '
                f'({value!r}). Names and firms only, no phone, email, or web address.')
    if not (name or firm):
        return ""
    # Where only the brokerage is published, "Listing agent: Somers & Company" names a
    # firm under a person's label. The listing carries the office, so say office.
    if not name:
        return f'<p class="agent">Listing office: {esc(firm)}</p>'
    who = f"<b>{esc(name)}</b>"
    at = f'{who}, {esc(firm)}' if firm else who
    # Name and firm, full stop. The line used to end "Contact details are in the
    # internal sheet, not here", which announced to the client that we were withholding
    # something, drawing attention to an omission they had no reason to notice and
    # would never have asked about. (Dell, 2026-08-20.) The withholding itself is
    # unchanged and still enforced above; it just does not narrate itself on the page.
    return f'<p class="agent">Listing agent: {at}</p>'


class MissingAddress(ValueError):
    """A card was built with no physical address. Never allowed on a tour packet."""


def address_line(p):
    """The physical address, labelled, on EVERY card, with no exceptions.

    Dell, 2026-08-20: "you left out the most important part, the physical address.
    never ever never leave out the address."

    The card heading is a DISPLAY TITLE, and on a land listing the display title is
    whatever the broker called it: "1.3 Acres Emerald Coast Parkway", "Hwy 98 E,
    Sandbar outparcel". Those are not addresses. Nobody can navigate to them, hand
    them to a title company, or look them up in a county GIS. Five of the eleven
    River Bank records were in that state and the packet shipped anyway, because the
    heading LOOKED like an address on the six that had street numbers.

    So the address is its own field and its own line. Where a parcel has no street
    number assigned, which is normal for raw land, the line carries the road, the
    parcel number, and a landmark, because that IS the address of that dirt. What it
    never carries is nothing.
    """
    loc = p.get("location") or {}
    street, parcel = (loc.get("street") or "").strip(), (loc.get("parcel") or "").strip()
    near = (loc.get("near") or "").strip()
    if not (street or parcel):
        raise MissingAddress(
            f'{p["address"]}: no location.street and no location.parcel. Every card '
            f'carries a physical address; raw land with no street number carries its '
            f'road, its parcel number, and a landmark instead.')
    bits = [street] if street else []
    if parcel:
        bits.append(f'Parcel {parcel}' if street else
                    f'No street number assigned. Parcel {parcel}')
    if near:
        bits.append(near)
    return (f'<p class="addr"><span class="addrlab">Address</span>'
            f'{esc(". ".join(b.rstrip(".") for b in bits))}.</p>')


def notes_box(p):
    """Ruled space for our own notes on the property. Ours, not the client's."""
    if not p.get("notes"):
        return ""
    label = p["notes"] if isinstance(p["notes"], str) else "Notes"
    return (f'<div class="notes"><h4>{esc(label)}</h4>'
            f'<div class="nlines" role="presentation"></div></div>')


def build_plans_sheet(p, plans):
    """A second sheet for one option, carrying the drawings we hold for it.

    Each plan names what the drawing IS and where it came from, because an
    undated drawing of unknown provenance is not evidence, several of these
    are twenty-year-old construction documents and the packet has to say so.
    """
    items = []
    for pl in p.get("plans") or []:
        if pl["file"] not in plans:
            continue
        src = f' <b>{esc(pl["source"])}</b>' if pl.get("source") else ""
        note = f' {esc(pl["note"])}' if pl.get("note") else ""
        items.append(
            f'<div class="plan"><figure>'
            f'<img src="data:image/png;base64,{plans[pl["file"]]}" '
            f'alt="{esc(pl["title"])} for {esc(p["address"])}">'
            f'<figcaption><b>{esc(pl["title"])}</b>{note}{src}</figcaption>'
            f'</figure></div>')
    if not items:
        return ""
    return f"""
      <article class="prop plans" id="p{p["rank"]:02d}plans">
        <div class="idx">{p["rank"]:02d}</div>
        <div class="pbody">
          <div class="phead">
            <div><h3 class="paddr">{esc(p["address"])}</h3>
                 <p class="pcity">{esc(p["city"])} &middot; Plans</p></div>
            <span class="chip b">Drawings</span>
          </div>
          <div class="plansheet">{"".join(items)}</div>
          <p class="plansrc">{esc(p.get("plans_note") or
             "Drawings supplied with the listing. Dimensions and areas are to be "
             "verified against a current measured survey before anything is signed.")}</p>
        </div>
      </article>"""


def build_tour_card(p, photos, minis=None, photo_px=None):
    """Tier 1 and 2: a full card with photo, reasoning, specs, and an expandable detail panel.

    NO loading="lazy" on these images (removed 2026-07-29, Joe's go). Every byte is already
    inlined as a data URI, so there is no network request to defer and lazy loading buys nothing.
    What it DID cost: 11 of 16 images stayed blank until a human scrolled them into view, so every
    screenshot, thumbnail and print-to-PDF captured empty photo frames and an empty map. Dell's
    side read the Ferris report as having no photos and no map. Do not add it back.
    """
    img = ""
    if p.get("photo") and p["photo"] in photos:
        # Cap the printed width at what this photograph's own pixels can carry. Without
        # it a 300px MLS thumbnail is stretched to the full column and prints at 43 DPI.
        px = photo_px.get(p["photo"], (None, None))[0] if photo_px else None
        cap = f' style="max-width:{px / MIN_PHOTO_DPI:.2f}in"' if px else ""
        img = (f'<div class="shot"{cap}><img src="data:image/jpeg;base64,{photos[p["photo"]]}" '
               f'alt="{esc(p["address"])}"></div>')

    # A locator cut centred on this property. The pin sits dead centre because the
    # image was cropped around the coordinate, so no positioning maths is needed.
    mini = ""
    m = (minis or {}).get(str(p["rank"]))
    if m:
        cap = ("Approximate position. The mapping data does not carry this address, "
               "so the pin sits on the road." if m.get("approx") else
               "Immediate surroundings.")
        mini = (f'<figure class="mini"><div class="miniwrap">'
                f'<img src="{m["image"]}" width="{m["w"]}" height="{m["h"]}" '
                f'alt="Close-up map showing the location of {esc(p["address"])}">'
                f'<span class="minipin" aria-hidden="true"></span></div>'
                f'<figcaption>{esc(cap)} Map data &copy; OpenStreetMap contributors</figcaption>'
                f'</figure>')
        img = img + mini

    s, r, t, b, site = p["size"], p["rate"], p["timing"], p["building_info"], p["site"]

    # The occupancy note is PROSE and belongs in body type under the grid, not inside a
    # spec cell. A spec cell is a short value in tabular figures, and `em` paints it
    # orange, so a 40-word sentence landed as a wall of orange monospace that blew out
    # the row height and pushed the rest of the grid sideways. Brand doctrine is
    # explicit that orange is an accent and never body text, and this was body text.
    # Nine of the eleven River Bank pages looked broken because of it.
    occ = ""
    if p.get("sale"):
        specs = sale_specs(p, s, b)
        _, occ_note = occupancy_line(p["sale"])
        if occ_note:
            occ = f'<p class="onote">{esc(occ_note)}</p>'
    else:
      specs = [
        spec("Available", size_line(s)),
          spec("Contiguous", f'{sf(s["contig_sf"])} SF', em=True) if s.get("contig_sf") else "",
          spec("Asking", rate_line(r)),
          spec("Base monthly", money(r["monthly_at_contig"]), em=True,
               note=monthly_note(s, r))
               if r["monthly_at_contig"] else spec("Base monthly", NOT_PUBLISHED),
          spec("Structure", val(r["services"])),
          spec("Available from", val(t["occupancy"])),
          spec("Term", val(t["term"])),
        spec("Status", val(t["status"])),
      ]

    # The second panel is the detail sheet: everything we know, plus what we do not.
    detail = [
        spec("Building class", val(b["class"])),
        spec("Year built", val(b["year_built"])),
        spec("Building size", f'{sf(b["building_sf"])} SF' if b["building_sf"] else NOT_PUBLISHED),
        spec("Storeys", val(b.get("stories"))),
        spec("Tenancy", val(b["tenancy"])),
        spec("Property type", val(b["primary_type"])),
        spec("Spaces in building", val(s.get("space_count"))),
        spec("Floor", val(s.get("floor"))),
        spec("Divisible", "Yes" if s.get("divisible") else "No"),
        spec("Parking", val(site["parking_spaces"])),
        spec("Zoning", val(site["zoning"])),
        spec("Operating expenses", f'${r["opex_psf"]:,.2f} /SF' if r.get("opex_psf") else NOT_PUBLISHED),
        spec("Total annual rent",
             f'{money(r["total_year_min"])}&ndash;{money(r["total_year_max"])[1:]}'
             if r.get("total_year_min") and r.get("total_year_max") and r["total_year_min"] != r["total_year_max"]
             else (money(r["total_year_min"]) if r.get("total_year_min") else NOT_PUBLISHED)),
        spec("On market", val(t["on_market"])),
    ]

    hl = ""
    if p.get("highlights"):
        items = "".join(f"<li>{esc(h)}</li>" for h in p["highlights"])
        hl = f'<div class="sub about"><h4>About the building</h4><ul>{items}</ul></div>'

    vf = ""
    if p.get("verify"):
        items = "".join(f"<li>{esc(v)}</li>" for v in p["verify"])
        vf = (f'<div class="sub"><h4>What we still need to confirm</h4><ul>{items}</ul>'
              f'<p class="vnote">These are open questions for the landlord, not problems with the space. '
              f'We get answers on all of them before you sign anything.</p></div>')

    src = ""
    if p.get("sources"):
        src = f'<p class="src">Listing data: {esc(", ".join(p["sources"]))}</p>'

    # UX doctrine law 8 (one card grammar) + law 3 (one action, the literal words).
    # This used to be a mailto per card, which fired a separate email on every click
    # (Joe, 2026-07-22). It is now a selection: pick as many as you like, and send one
    # message at the end. On paper the button prints as a tick box, which is what a
    # tour packet actually needs.
    act = (f'<div class="act">'
           f'<button type="button" class="pick" data-rank="{p["rank"]}" '
           f'data-addr="{esc(p["address"])}" data-city="{esc(p["city"])}" aria-pressed="false">'
           f'<span class="box" aria-hidden="true"></span>'
           f'<span class="lbl-off">Add to my tour list</span>'
           f'<span class="lbl-on">On your tour list</span>'
           f'</button>'
           f'<span class="hint">Pick as many as you want. Your list builds at the bottom of '
           f'this page and goes to me in one message.</span>'
           f'</div>')

    building = f' <span class="bname">{esc(p["building"])}</span>' if p.get("building") else ""

    return f"""
      <article class="prop" id="p{p["rank"]:02d}">
        <div class="idx">{p["rank"]:02d}</div>
        <div class="pbody">
          <div class="phead">
            <div><h3 class="paddr">{esc(p["address"])}{building}</h3>
                 <p class="pcity">{esc(p["city"])}</p></div>
            <span class="chip">{esc(p["chip"])}</span>
          </div>
          {address_line(p)}
          {img}
          <p class="why">{esc(p["why"])}</p>
          <dl class="specs">{"".join(x for x in specs if x)}</dl>
          {occ}
          <details class="more">
            <summary><span class="sopen">Full detail</span><span class="sclose">Hide detail</span></summary>
            <div class="mbody">
              <dl class="specs dspecs">{"".join(detail)}</dl>
              {hl}{vf}{src}
            </div>
          </details>
          {agent_line(p)}
          {act}
          {notes_box(p)}
        </div>
      </article>"""


def empty_state(title, body):
    """UX doctrine law 11b: a section that can be empty gets an authored state naming
    what happened and the next step, never a silent blank."""
    return f'<div class="empty"><h4>{esc(title)}</h4><p>{esc(body)}</p></div>'


def build_map(folder):
    """Render map.json, if the map step has been run. A search with no map is a
    complete report; the section simply does not appear."""
    path = os.path.join(folder, "map.json")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)

    pins = []
    for pin in m["pins"]:
        approx = " approx" if pin.get("approx") else ""
        qual = " (approximate location)" if pin.get("approx") else ""
        label = f'{pin["rank"]:02d} {pin["address"]}, {pin["city"]}{qual}'
        pins.append(
            f'<button class="pin t-{pin["tier"]}{approx}" style="left:{pin["x"]}%;top:{pin["y"]}%" '
            f'data-go="p{pin["rank"]:02d}" title="{esc(label)}" aria-label="{esc(label)}">'
            f'<i>{pin["rank"]}</i></button>')

    note = ""
    if m.get("missing"):
        names = ", ".join(f'{x["address"]}' for x in m["missing"])
        note = (f'<p class="mapnote">Not shown on the map: {esc(names)}. '
                f'These are new roads the mapping data does not carry yet, so rather than '
                f'guess at a position we have left them off. Each one is in the list below.</p>')

    approx_n = sum(1 for p in m["pins"] if p.get("approx"))
    if approx_n:
        note += (f'<p class="mapnote">{approx_n} pins sit on the road rather than the exact '
                 f'building, for the same reason. They are marked with a dashed ring.</p>')

    return f"""
  <section>
    <div class="sechead"><h2>Where these are</h2><span class="ct">{len(m["pins"])} of __N_TOTAL__ mapped</span></div>
    <p>Click any pin to jump to that space. The clustering tells you something on its own:
       how far apart your options really are, and how much of one afternoon a tour would take.</p>
    <div class="mapwrap">
      <img src="{m["image"]}" alt="Map of the search area with each available space marked"
           width="{m["width"]}" height="{m["height"]}">
      {"".join(pins)}
    </div>
    <div class="maplegend">
      <span><b class="k-tour"></b>Worth touring</span>
      <span><b class="k-look"></b>Worth a look</span>
      <span><b class="k-out"></b>Ruled out</span>
      <span style="margin-left:auto">{esc(m["attribution"])}</span>
    </div>
    {note}
  </section>"""


def build_out_row(p):
    return f"""
      <div class="out" id="p{p["rank"]:02d}"><div class="idx">{p["rank"]:02d}</div><div>
        <span class="oaddr">{esc(p["address"])}</span><span class="ocity">{esc(p["city"])}</span>
        <p class="oreason"><b>{esc(p["reason_lead"])}</b> {esc(p["reason"])}</p>
      </div></div>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("--supersede-existing", action="store_true",
                    help="tombstone a same-name prior render after its canonical add-loop receipt exists")
    ap.add_argument("--loop-ref", help="canonical add-loop receipt/reference for the supersession")
    add_recovery_arguments(ap)
    a = ap.parse_args()
    try:
        configure_recovery_assets(str(require_recovery(
            a, "document asset API for CARR brand fonts and logos")))
    except ValueError as exc:
        print(f"build-space-search: STOP: {exc}", file=sys.stderr)
        return 2
    folder = a.folder
    with open(os.path.join(folder, "properties.json"), encoding="utf-8") as fh:
        data = json.load(fh)

    c = data["client"]
    try:
        require_search_commentary(c)
        require_declined_and_why(c)
        require_asset_tier(data.get("asset") or {})
    except AssetControlRefusal as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    props = data["properties"]

    # Refuse the whole render before anything is written, rather than discovering a
    # leaked phone number partway through the tour cards. agent_line() raises the same
    # error at render time as well; this is the one that fires first and names the row.
    try:
        for p in props:
            agent_line(p)
            address_line(p)
    except (ContactLeak, MissingAddress) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    photos, photo_px = {}, {}
    pdir = os.path.join(folder, "photos")
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if fn.lower().endswith((".jpg", ".jpeg")):
                photos[fn] = b64(os.path.join(pdir, fn))
                photo_px[fn] = jpeg_size(os.path.join(pdir, fn))

    # Plans are PNG rather than JPEG on purpose: these are line drawings, and JPEG
    # ringing around thin black linework is exactly what makes a floor plan unreadable
    # once it has been through a printer.
    plans = {}
    ldir = os.path.join(folder, "plans")
    if os.path.isdir(ldir):
        for fn in os.listdir(ldir):
            if fn.lower().endswith(".png"):
                plans[fn] = b64(os.path.join(ldir, fn))

    minis = {}
    mpath = os.path.join(folder, "map.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            minis = json.load(fh).get("minis", {})

    tour = [p for p in props if p.get("tier") == "tour"]
    look = [p for p in props if p.get("tier") == "look"]
    out  = [p for p in props if p.get("tier") == "out"]

    # Asking range across the TOUR set only. Spanning all 33 produced "$12 to $45",
    # which is not a market range, it is the distance between a warehouse bay and a
    # medical suite. Quoting it invites the client to anchor on a number attached to
    # a building we would never put them in. (Joe, 2026-07-22.)
    # The opening sentence names the deal type. It used to be hardcoded to "available
    # for lease", which told a BUYER his purchase search was a rent roll (caught on the
    # Le C-063 report, 2026-07-31, before it left the building).
    #
    # A search that mixes deal types and asset classes -- buildings for sale, one
    # suite for lease, and raw sites -- cannot be described by either sentence
    # below without saying something untrue, so such a search states its own
    # opening line. Absent the key, the two authored defaults are unchanged.
    if c.get("standfirst"):
        standfirst = c["standfirst"]
    elif any(p.get("sale") for p in tour):
        standfirst = (f'{len(props)} buildings are for sale across your search area right now. '
                      f'{len(tour)} of them are worth your time, and the reasons the others are not '
                      f'tell you a great deal about this market.')
    else:
        standfirst = (f'{len(props)} spaces are available for lease across your search area right now. '
                      f'{len(tour)} of them are worth your time, and the reasons the others are not '
                      f'tell you a great deal about this market.')

    if any(p.get("sale") for p in tour):
        # Asking PRICE range across the tour set only, same rule as the rent span:
        # quoting the full search range mixes buildings we would never put him in.
        prices = [p["sale"]["price"] for p in tour if p.get("sale") and p["sale"].get("price")]
        rate_span = (f'${min(prices):,.0f}&ndash;{max(prices):,.0f}' if prices else "n/a")
    else:
        rates = [p["rate"]["min"] for p in tour if p.get("rate") and p["rate"].get("min")]
        rate_span = f'${min(rates):,.0f}&ndash;{max(rates):,.0f}' if rates else "n/a"

    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "space-search-template.html")
    with open(tpl_path, encoding="utf-8") as fh:
        html = fh.read()

    pending = ""
    if c.get("sources_pending"):
        pending = (f'<p class="pending">Still to run: {esc(", ".join(c["sources_pending"]))}. '
                   f'Those sources publish parking counts, zoning, and operating expenses, which is '
                   f'where several of the open questions above get answered.</p>')
    elif c.get("coverage_note"):
        # Every source has been run. Say what each one did and did not cover, so the
        # reader can see the edges of the search rather than assuming there are none.
        pending = f'<p class="pending">{esc(c["coverage_note"])}</p>'

    def bullets(items):
        """Each item is [lead, body]. The lead is bolded; the body carries the point."""
        return "".join(f'<li><b>{esc(lead)}</b> {esc(body)}</li>' for lead, body in items)

    # Whoever ran the search signs it. The defaults are the values that were hardcoded
    # in the template, so a search that names no advisor renders exactly as before.
    adv = c.get("advisor") or {}
    repl = {
        # "packet" prints one listing to a sheet; anything else keeps the full report
        # print, which is the default and what every prior search rendered.
        # The empty-tier attributes ride along so print CSS can drop a section that has
        # no rows. On screen the authored empty state still shows, "nothing was ruled
        # out" is a real finding to a reader. On paper, in a packet, three boxes
        # explaining that three lists are empty is just three sheets to carry.
        "__PRINT_MODE__": (f' data-print="packet" data-look="{len(look)}" data-out="{len(out)}"'
                           if c.get("print_mode") == "packet" else ""),
        "__KICKER__": esc(c.get("kicker") or "Healthcare Real Estate"),
        "__ADVISOR__": esc(adv.get("name") or "Joe Bookout"),
        "__ADVISOR_TITLE__": esc(adv.get("title") or "Healthcare Real Estate Advisor, CARR"),
        "__ADVISOR_EMAIL__": esc(adv.get("email") or "joe.bookout@carr.us"),
        "__ADVISOR_PROMISE__": esc(adv.get("promise") or
            "CARR represents healthcare tenants and buyers only. We never represent "
            "landlords or sellers, so there is no question about whose interests are "
            "being protected in your negotiation."),
        "__MAP__": build_map(folder),
        "__FINDINGS__": bullets(c.get("findings", [])),
        "__CONFIRMATIONS__": bullets(c.get("confirmations", [])),
        "__DECLINED__": bullets(c.get("declined_and_why", [])),
        # The area string doubles as the headline, which works while an area reads like
        # a place name and stops working when it reads like a corridor description. A
        # search may name its own headline; absent that, the split-on-"through" default
        # every prior search used is untouched.
        "__TITLE__": f'{c.get("headline") or c["area"].split(" through ")[0]} Space Search',
        "__CLIENT__": esc(c["name"]),
        "__PREPARED__": esc(c["prepared"]),
        "__PRACTICE__": esc(c["practice"]),
        # A size band nobody has given us yet is an unknown like any other, and it gets
        # the same visible gap rather than a number we invented. " to  SF" -- which is
        # what the old unguarded format produced from a null -- reads as a broken template
        # and hides the fact that the requirement is still an open question for the client.
        # A client with no size band is not the same as a client whose band we failed to
        # ask for. "Flexible" is a REQUIREMENT, and saying "Not published" where the
        # answer is "they'll take what works" tells the reader we did not do our job.
        "__TARGET__": esc(c["target_note"]) if c.get("target_note")
                      else band(c.get("target_min_sf"), c.get("target_max_sf")),
        "__SEARCHED__": band(c.get("searched_min_sf"), c.get("searched_max_sf")),
        "__AREA__": esc(c["area"]),
        "__STRUCTURE__": esc(c["structure"]),
        "__N_TOTAL__": str(len(props)),
        "__N_TOUR__": str(len(tour)),
        "__N_LOOK__": str(len(look)),
        "__N_OUT__": str(len(out)),
        "__RATE_SPAN__": rate_span,
        "__STANDFIRST__": standfirst,
        # A plans sheet follows the card it belongs to, so the drawings sit behind
        # that property in the packet rather than in an appendix nobody turns to.
        "__TOUR_CARDS__": ("".join(build_tour_card(p, photos, minis, photo_px) + build_plans_sheet(p, plans)
                                   for p in tour) or empty_state(
            "Nothing clears the bar yet",
            "Every available space in this size range has a disqualifying problem right now. That is a real "
            "finding rather than a gap in the search, and it usually means the right move is to wait for the "
            "next listing or approach an owner who is not marketing. Joe will explain which.")),
        "__LOOK_CARDS__": ("".join(build_tour_card(p, photos, minis, photo_px) for p in look) or empty_state(
            "No borderline options",
            "Nothing in this search sits on the fence. Every space either fits or clearly does not.")),
        "__OUT_ROWS__": ("".join(build_out_row(p) for p in out) or empty_state(
            "Nothing was ruled out",
            "Every available space in your size range is still in play, which is unusual and worth talking about.")),
        "__SOURCES__": esc(", ".join(c["sources_run"])),
        "__PENDING__": pending,
        "__H1_A__": c.get("headline") or c["area"].split(" through ")[0],
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    for k, path in ASSETS.items():
        html = html.replace(f"__{k}__", b64(path))

    leftover = re.findall(r"__[A-Z_]+__", html)
    assert not leftover, f"unreplaced tokens: {set(leftover)}"

    # The default takes the last word of the client's name, which is a surname for a
    # doctor and a noun for an organisation: "Harbor State Bank" produced
    # trust-space-search.html. A client may name its own slug.
    slug = re.sub(r"[^a-z0-9]+", "-",
                  (c.get("slug") or c["name"].split(",")[0].split()[-1]).lower()).strip("-")
    date = re.sub(r"[^0-9a-zA-Z]+", "-", c["prepared"]).strip("-")
    outp = os.path.join(folder, f"{slug}-space-search-{date}.html")
    tombstone = None
    if os.path.exists(outp):
        tombstone = os.path.join(folder, "_TO_DELETE", os.path.basename(outp))
        if not a.supersede_existing:
            print("STOP: replacement refused: existing client artifact requires "
                  "--supersede-existing and --loop-ref", file=sys.stderr)
            return 2
    try:
        write_artifact_atomically(outp, html, tombstone_path=tombstone,
                                  loop_ref=a.loop_ref)
    except (AssetControlRefusal, OSError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {outp}")
    print(f"  {len(props)} properties: {len(tour)} tour, {len(look)} look, {len(out)} ruled out")
    print(f"  {len(photos)} photos inlined | {os.path.getsize(outp)/1024/1024:.2f} MB")


if __name__ == "__main__":
    raise SystemExit(main())
