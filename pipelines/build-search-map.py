#!/usr/bin/env python3
"""
build-search-map.py — a static, self-contained map for a CARR space search.

Geocodes every property in a search folder's properties.json, fetches the
OpenStreetMap tiles covering them, stitches those tiles into one image, and
writes map.json holding the image as a data URI plus the pixel position of each
pin. build-space-search.py picks that up and renders the map into the report.

Why build it this way rather than embedding a map library:
  - The report has to work offline and as an email attachment. A Leaflet or
    Mapbox map needs a network at open time, and a strict reader or a printed
    page gets a blank rectangle where the map should be.
  - No external requests at view time means no tracking of the client either.
  - It prints. A stitched raster prints exactly as it looks on screen.

The pins are positioned with the same Web Mercator maths OSM uses, so they land
where they belong on the stitched raster.

⚠️ OpenStreetMap requires attribution wherever this image is shown, and their
tile usage policy asks for a real User-Agent and no bulk scraping. One search is
a few dozen tiles, well inside the policy. Geocoding through Nominatim is capped
at one request per second, which this script honours.

Usage:  python3 build-search-map.py <search-folder>
"""

import json, math, os, re, sys, time, urllib.parse, urllib.request, base64, io

UA = "CARR-space-search/1.0 (joe.bookout@carr.us)"
TILE = 256
NOMINATIM = "https://nominatim.openstreetmap.org/search"
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def get(url, tries=3):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except Exception as exc:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))


# ------------------------------------------------------------ web mercator

def deg2num(lat, lon, z):
    """Fractional tile coordinates. Keeping the fraction is what lets a pin sit
    between tile boundaries instead of snapping to a tile corner."""
    lat_r = math.radians(lat)
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


# The search area. Bounding the geocoder to it is what makes a bare street name
# resolvable; unbounded, Nominatim rejects most of these or wanders out of state.
VIEWBOX = "-86.80,30.65,-86.05,30.25"   # widened 2026-07-31: the old box topped out at
# 30.48N, which sits SOUTH of Niceville (30.52). Every Niceville and mid-bay address in the
# expanded Ferris search failed to geocode because of it, silently, with no pin and no error.
OVERPASS = "https://overpass-api.de/api/interpreter"

# Roads the listings name one way and OpenStreetMap names another, or does not
# carry at all. Each maps to the nearest road OSM DOES have, and every one of them
# is marked approximate so the report can draw it differently. This table is the
# honest alternative to nudging a coordinate until the pin looks right.
OSM_ALIAS = {
    "Mack Bayou Loop Road": "Mack Bayou Road",
    # CoStar files 142 John Galt Rd under building name "Goldsby Road Commerce Park",
    # and Goldsby Road is in OSM. That documented link is the basis for the pin.
    "John Galt Road": "Goldsby Road",
}


def strip_unit(addr):
    """Drop suite and unit qualifiers, which no geocoder resolves."""
    head = addr.split(",")[0]
    for junk in ("Lot 5 ", "A7 and A8 "):
        head = head.replace(junk, "")
    return head.strip()


def nominatim(q, bounded=True):
    params = {"q": q, "format": "json", "limit": 1}
    if bounded:
        params.update({"viewbox": VIEWBOX, "bounded": 1})
    try:
        res = json.loads(get(f"{NOMINATIM}?{urllib.parse.urlencode(params)}"))
    except Exception:
        res = []
    time.sleep(1.1)                           # usage policy: one request per second
    return res[0] if res else None


def overpass_street(name):
    """Ask OSM directly for a road by name inside the search area. Catches roads
    Nominatim will not return for an address query."""
    esc = name.replace('"', "")
    query = (f'[out:json][timeout:40];way["highway"]["name"~"{esc}",i]'
             f'(30.25,-86.80,30.65,-86.05);out tags center 1;')
    try:
        req = urllib.request.Request(
            OVERPASS, data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=50) as r:
            els = json.loads(r.read()).get("elements", [])
    except Exception:
        els = []
    time.sleep(1.0)
    for e in els:
        c = e.get("center")
        if c:
            return {"lat": c["lat"], "lon": c["lon"],
                    "display_name": e["tags"].get("name", name)}
    return None


def geocode(addr, city, cache):
    """Resolve one property to a coordinate, or to nothing.

    Order: the full address, then the address without its unit, then the bare
    street name, then OSM's own road geometry, then a known alias. A property
    that survives all of that unresolved gets NO pin. We never fall back to a
    city centroid and call it a location."""
    key = f"{addr}|{city}"
    # Serve cached SUCCESSES only (fixed 2026-07-31): a cached failure replayed
    # forever, so a fix upstream (like the widened VIEWBOX) could never rescue a
    # pin without hand-purging the cache. Failures now retry each run — bounded
    # cost, only the unresolved addresses re-query.
    if key in cache and cache[key] is not None:
        return cache[key]

    head = strip_unit(addr)
    street = head.lstrip("0123456789 ").strip()      # street name with no number

    # The city as OSM would name it: "Santa Rosa Beach, FL 32459" -> "Santa Rosa Beach".
    town = city.split(",")[0].strip()
    # The house number, when the listing has one. Nominatim answers an address query it
    # cannot satisfy by returning the STREET, and the returned display_name is the only
    # place that shows it: an exact hit reads "4635, Gulfstarr Drive, Destin", a road
    # centre reads "Emerald Coast Parkway, Destin". Same query, same shape of response,
    # completely different precision.
    number = re.match(r"\s*(\d+)", head)
    number = number.group(1) if number else None

    out = None
    # Only the FIRST query form — the full address with its town — can produce an exact
    # coordinate. The other two drop the town, then the house number, so a hit on either
    # is a road somewhere in the viewbox and nothing stronger. All three used to be
    # written with approx=False, which is how a road centre reached a client map drawn
    # as an exact building pin. (River Bank US-98 search, 2026-08-20, defect 03d959bf:
    # 13331 and 13381 Emerald Coast Pkwy both landed on one shared road centre and both
    # printed as exact.)
    for n, q in enumerate((f"{head}, {town}, FL", head, street)):
        hit = nominatim(q)
        if not hit:
            continue
        matched = hit.get("display_name", "")
        # A match that does not name the town we asked for is not this property. The
        # viewbox spans the whole panhandle, so "4400 W US Hwy 98" without a town
        # cheerfully returned Harbor Boulevard in Destin for a Santa Rosa Beach site —
        # twenty miles away, in another county, recorded as exact. Downgrading that to
        # "approximate" would not help: a pin twenty miles out is wrong, not fuzzy.
        if town and town.lower() not in matched.lower():
            continue
        # Exact only when the full-address query came back carrying the house number.
        # Anything else is a road, and a road is approximate however confidently the
        # geocoder phrased it.
        #
        # The number must be a WHOLE comma-delimited token, not a substring. Nominatim
        # formats a rooftop as "4635, Gulfstarr Drive, Destin, ..., 32541" and sometimes
        # leads with a POI name, "Southern Vacation Rentals, 4608, Opa-Locka Lane, ...".
        # A substring test passes "2 Industrial Park Lane" against a match that carries
        # no house number at all, because the 2 is sitting inside the zip code 32540,
        # and a road centre then prints as an exact pin. Short house numbers are common.
        parts = {seg.strip() for seg in matched.split(",")}
        exact = n == 0 and bool(number) and number in parts
        out = {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
               "matched": matched, "approx": not exact, "query": q}
        break

    if out is None:
        hit = overpass_street(street)
        if hit:
            # A road centre rather than the building. Close, not exact.
            out = {"lat": hit["lat"], "lon": hit["lon"],
                   "matched": hit["display_name"], "approx": True, "query": f"osm:{street}"}

    if out is None and street in OSM_ALIAS:
        hit = overpass_street(OSM_ALIAS[street])
        if hit:
            out = {"lat": hit["lat"], "lon": hit["lon"],
                   "matched": f'{hit["display_name"]} (nearest road to {street})',
                   "approx": True, "query": f"alias:{OSM_ALIAS[street]}"}

    cache[key] = out
    return out


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    with open(os.path.join(folder, "properties.json"), encoding="utf-8") as fh:
        data = json.load(fh)

    cache_path = os.path.join(folder, "_geocode-cache.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)

    props = data["properties"]
    located, missing = [], []
    for p in props:
        g = geocode(p["address"], p["city"], cache)
        if g:
            located.append((p, g))
        else:
            missing.append(p)

    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1, ensure_ascii=False)

    if not located:
        print("nothing geocoded; no map written")
        return

    lats = [g["lat"] for _, g in located]
    lons = [g["lon"] for _, g in located]

    # Pad the bounds so no pin sits on the edge of the image.
    pad_lat = max((max(lats) - min(lats)) * 0.14, 0.006)
    pad_lon = max((max(lons) - min(lons)) * 0.14, 0.006)
    north, south = max(lats) + pad_lat, min(lats) - pad_lat
    west, east = min(lons) - pad_lon, max(lons) + pad_lon

    # Largest zoom whose tile grid stays under a sane pixel budget. Higher zoom is
    # more detail; the cap keeps the embedded image from bloating the report.
    MAX_PX = 2400
    zoom = 16
    while zoom > 8:
        x0, y0 = deg2num(north, west, zoom)
        x1, y1 = deg2num(south, east, zoom)
        if (math.ceil(x1) - math.floor(x0)) * TILE <= MAX_PX and \
           (math.ceil(y1) - math.floor(y0)) * TILE <= MAX_PX:
            break
        zoom -= 1

    x0, y0 = deg2num(north, west, zoom)
    x1, y1 = deg2num(south, east, zoom)
    tx0, ty0 = math.floor(x0), math.floor(y0)
    tx1, ty1 = math.ceil(x1), math.ceil(y1)
    cols, rows = tx1 - tx0, ty1 - ty0

    from PIL import Image
    canvas = Image.new("RGB", (cols * TILE, rows * TILE), "#e8e4dd")
    fetched = 0
    for cx in range(cols):
        for cy in range(rows):
            url = TILE_URL.format(z=zoom, x=tx0 + cx, y=ty0 + cy)
            try:
                tile = Image.open(io.BytesIO(get(url))).convert("RGB")
                canvas.paste(tile, (cx * TILE, cy * TILE))
                fetched += 1
            except Exception as exc:
                print(f"  tile {zoom}/{tx0+cx}/{ty0+cy} failed: {exc}")
            time.sleep(0.12)

    # Crop to the padded bounds so the image is exactly the area we asked for.
    left = int((x0 - tx0) * TILE)
    top = int((y0 - ty0) * TILE)
    right = int((x1 - tx0) * TILE)
    bottom = int((y1 - ty0) * TILE)
    canvas = canvas.crop((left, top, right, bottom))
    W, H = canvas.size

    pins = []
    for p, g in located:
        px, py = deg2num(g["lat"], g["lon"], zoom)
        pins.append({
            "rank": p["rank"],
            "tier": p["tier"],
            "address": p["address"],
            "city": p["city"],
            # An approximate pin sits on the road rather than the building, because
            # OSM does not carry that address. The report says so on the pin.
            "approx": g["approx"],
            # Percentages, so the map scales with the page and prints correctly.
            "x": round(((px - tx0) * TILE - left) / W * 100, 3),
            "y": round(((py - ty0) * TILE - top) / H * 100, 3),
        })

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=82, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()

    # ---- per-property locator maps -------------------------------------------
    # A close-up cut for each card, so the reader sees the immediate surroundings
    # (which road, which corner, what is next door) without leaving the property.
    # Only the tiers a client will actually consider get one; 18 ruled-out rows do
    # not need a map and would add weight to the file for nothing.
    MINI_Z = 16
    MINI_W, MINI_H = 460, 260
    minis = {}
    for p, g in located:
        if p["tier"] == "out":
            continue
        cx, cy = deg2num(g["lat"], g["lon"], MINI_Z)
        px, py = cx * TILE, cy * TILE                  # pixel centre at MINI_Z
        x0p, y0p = px - MINI_W / 2, py - MINI_H / 2
        t_x0, t_y0 = math.floor(x0p / TILE), math.floor(y0p / TILE)
        t_x1, t_y1 = math.ceil((x0p + MINI_W) / TILE), math.ceil((y0p + MINI_H) / TILE)

        strip = Image.new("RGB", ((t_x1 - t_x0) * TILE, (t_y1 - t_y0) * TILE), "#e8e4dd")
        for ax in range(t_x1 - t_x0):
            for ay in range(t_y1 - t_y0):
                url = TILE_URL.format(z=MINI_Z, x=t_x0 + ax, y=t_y0 + ay)
                try:
                    strip.paste(Image.open(io.BytesIO(get(url))).convert("RGB"),
                                (ax * TILE, ay * TILE))
                except Exception:
                    pass
                time.sleep(0.12)

        crop = strip.crop((int(x0p - t_x0 * TILE), int(y0p - t_y0 * TILE),
                           int(x0p - t_x0 * TILE) + MINI_W, int(y0p - t_y0 * TILE) + MINI_H))
        b = io.BytesIO()
        crop.save(b, "JPEG", quality=72, optimize=True)
        minis[str(p["rank"])] = {
            "image": f"data:image/jpeg;base64,{base64.b64encode(b.getvalue()).decode()}",
            "w": MINI_W, "h": MINI_H, "approx": g["approx"],
        }
        print(f"  mini {p['rank']:02d} {p['address'][:38]}")

    out = {
        "image": f"data:image/jpeg;base64,{b64}",
        "width": W, "height": H, "zoom": zoom,
        "pins": pins,
        "minis": minis, "mini_zoom": MINI_Z,
        "missing": [{"rank": p["rank"], "address": p["address"]} for p in missing],
        "attribution": "Map data © OpenStreetMap contributors",
    }
    with open(os.path.join(folder, "map.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh)

    print(f"wrote map.json  zoom {zoom}  {W}x{H}px  {fetched}/{cols*rows} tiles")
    n_approx = sum(1 for p in pins if p.get("approx"))
    print(f"  {len(pins)} pins placed ({n_approx} approximate), {len(missing)} not located")
    # Print what each address actually MATCHED, not just that it matched something.
    # The old summary reported counts only, so "9 pins placed" read as nine correct
    # locations while three of them sat in the wrong county. A count cannot be checked;
    # a name can, and this is the line that gets read before a map goes to a client.
    for p, g in located:
        flag = "~" if g.get("approx") else " "
        print(f"   {flag}{p['rank']:02d} {p['address']}, {p['city']}"
              f"\n        -> {g.get('matched', '')}")
    for p in missing:
        print(f"    no pin: {p['rank']:02d} {p['address']}")
    print(f"  image {len(b64)/1024/1024:.2f} MB base64")
    print("  CHECK EVERY '->' LINE ABOVE against the address beside it before this map "
          "reaches a client. A wrong pin is worse than a missing one.")


if __name__ == "__main__":
    main()
