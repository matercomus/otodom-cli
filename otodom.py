#!/usr/bin/env python3
"""Otodom.pl listing scraper — agent-friendly CLI.

Reads the structured listing data Otodom embeds in each search page's
`__NEXT_DATA__` JSON (Next.js). One request per page gives ~72 fully-parsed
listings, so there's no need to fetch each ad individually or run a database.

Usage:
    otodom search --city czestochowa --province slaskie --max 400000 -o out.json
    otodom search --city warszawa --transaction rent --rooms 2 --pretty | jq .

Output is a JSON array of listings on stdout (or --output file).
"""
import argparse
import json
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.otodom.pl"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
RETRY_STATUS = {429, 500, 502, 503, 504}


class OtodomError(Exception):
    """A clean, agent-readable failure — printed as one stderr line, no traceback."""


def _get(url, params=None, tries=4, backoff=1.0):
    """GET with bounded exponential backoff on 429/5xx and connection errors.
    Returns the final Response; raises OtodomError if every attempt fails."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as e:  # connection reset/timeout
            last = e
        else:
            if r.status_code not in RETRY_STATUS:
                return r
            last = OtodomError(f"HTTP {r.status_code} from {r.url}")
        if attempt < tries - 1:
            time.sleep(backoff * 2 ** attempt)
    raise OtodomError(f"giving up after {tries} tries: {url} ({last})")

# English CLI value -> Otodom URL slug (Polish)
TRANSACTIONS = {"sale": "sprzedaz", "rent": "wynajem"}
PROPERTY_TYPES = {
    "flat": "mieszkanie",
    "studio": "kawalerka",
    "house": "dom",
    "investment": "inwestycja",
    "room": "pokoj",
    "plot": "dzialka",
    "venue": "lokal",
    "magazine": "haleimagazyny",
    "garage": "garaz",
}
# Otodom enum -> number, for the few worded fields agents filter on.
ROOMS = {w: i for i, w in enumerate(
    ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN"], 1)}
PL_CHARS = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def slugify(text: str) -> str:
    return text.strip().lower().translate(PL_CHARS).replace(" ", "-")


AUTOSUGGEST = f"{BASE}/ajax/geo6/autosuggest/"


def _location_path(text: str, level: str) -> str:
    """Build the canonical Otodom slug path from an autosuggest breadcrumb.

    Breadcrumb `text` runs specific->broad ("Ząbki, wołomiński, mazowieckie").
    Otodom's URL is province/powiat/gmina/city[/district]; the breadcrumb omits
    the gmina, which we synthesise as the city slug — verified working for towns
    (Ząbki), county-rights cities (Warszawa) and big-city districts (Wawer)."""
    p = [slugify(x) for x in text.split(",")]
    if level == "DISTRICT" and len(p) >= 3:  # name, city, region
        return f"{p[-1]}/{p[1]}/{p[1]}/{p[1]}/{p[0]}"
    if level == "CITY" and len(p) == 2:  # county-rights city: name, region
        return f"{p[-1]}/{p[0]}/{p[0]}/{p[0]}"
    if level == "CITY" and len(p) >= 3:  # town: name, powiat, region
        return f"{p[-1]}/{p[1]}/{p[0]}/{p[0]}"
    if level == "SUBREGION" and len(p) >= 2:  # powiat: name, region
        return f"{p[-1]}/{p[0]}"
    return "/".join(reversed(p))  # ponytail: REGION/unknown — best-effort, may 404


def resolve_location(name: str) -> list:
    """Resolve a free-text place name to ranked candidates via Otodom's live
    autosuggest, each carrying the canonical search slug `path` + a `level`
    type so an agent can disambiguate (e.g. Wawer district vs same-named towns)."""
    r = _get(AUTOSUGGEST, {"data": name})
    if not r.ok:
        raise OtodomError(f"location lookup failed (HTTP {r.status_code}) for {name!r}")
    return [{"label": c.get("text"), "level": c.get("level"), "name": c.get("name"),
             "path": _location_path(c.get("text") or "", c.get("level") or ""),
             "id": c.get("id")}
            for c in r.json()]


def _money(m):
    return m.get("value") if isinstance(m, dict) else None


def parse_item(it: dict) -> dict:
    """Flatten one search-result item into a flat, agent-friendly record."""
    addr = (it.get("location") or {}).get("address") or {}
    # district lives in reverseGeocoding, deepest location level
    district = None
    for loc in (((it.get("location") or {}).get("reverseGeocoding") or {})
                .get("locations") or []):
        if loc.get("locationLevel") == "district":
            district = loc.get("name")
    return {
        "id": it.get("id"),
        "title": it.get("title"),
        "url": f"{BASE}/pl/oferta/{it.get('slug')}" if it.get("slug") else None,
        "price": _money(it.get("totalPrice")),
        "currency": (it.get("totalPrice") or {}).get("currency"),
        "rent": _money(it.get("rentPrice")),
        "price_per_m2": _money(it.get("pricePerSquareMeter")),
        "area_m2": it.get("areaInSquareMeters"),
        "rooms": ROOMS.get(it.get("roomsNumber") or "", it.get("roomsNumber")),
        "floor": it.get("floorNumber"),
        "type": it.get("estate"),
        "transaction": it.get("transaction"),
        "is_private": it.get("isPrivateOwner"),
        "is_promoted": it.get("isPromoted"),
        "date_created": it.get("dateCreated"),
        "city": (addr.get("city") or {}).get("name"),
        "province": (addr.get("province") or {}).get("name"),
        "district": district,
        "street": (addr.get("street") or {}).get("name") if isinstance(
            addr.get("street"), dict) else addr.get("street"),
        "agency": (it.get("agency") or {}).get("name"),
    }


def _strip_html(html) -> str:
    """Description comes as HTML; flatten to plain text for storage/keyword search."""
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def _first(v):
    """target fields are often single-element lists; unwrap them."""
    return v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)


def amenities(extras: list, desc: str) -> tuple:
    """(has_bathtub, has_garden). Bathtub lives only in free text (`wanna`);
    garden is a reliable extras slug but also turns up in the description."""
    # Fold case + diacritics so stems match every declension: wanna/wanną/wannie,
    # ogród/ogrodu/ogródek. PL_CHARS maps ó->o, ą->a, etc.
    dl = (desc or "").lower().translate(PL_CHARS)
    has_bathtub = "wann" in dl
    has_garden = ("garden" in (extras or [])) or "ogrod" in dl
    return has_bathtub, has_garden


def fetch_ad(url: str) -> dict:
    """Fetch one ad page and enrich it with bathtub/garden flags.

    Search JSON lacks extras and free text, so this fetches the ad's own
    `__NEXT_DATA__` (props.pageProps.ad) for `target` + `description`.
    """
    r = _get(url)
    if r.status_code == 404:
        raise OtodomError(f"ad not found (404): {r.url}")
    if not r.ok:
        raise OtodomError(f"HTTP {r.status_code} fetching ad: {r.url}")
    soup = BeautifulSoup(r.content, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if tag is None:
        raise OtodomError(f"blocked or layout changed (no __NEXT_DATA__): {r.url}")
    ad = json.loads(tag.text)["props"]["pageProps"].get("ad") or {}
    t = ad.get("target") or {}
    extras = t.get("Extras_types") or []
    desc = _strip_html(ad.get("description"))
    has_bathtub, has_garden = amenities(extras, desc)
    rooms = _first(t.get("Rooms_num"))
    return {
        "id": ad.get("id"),
        "url": url,
        "title": ad.get("title"),
        "price": _first(t.get("Price")),
        "rent": _first(t.get("Rent")),
        "area_m2": t.get("Area"),
        "rooms": int(rooms) if rooms and str(rooms).isdigit() else None,
        "floor": _first(t.get("Floor_no")),
        "city": t.get("City"),
        "extras": extras,
        "has_bathtub": has_bathtub,
        "has_garden": has_garden,
        "description": desc,
    }


def details(args) -> list:
    urls = list(args.urls or [])
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            urls += [it["url"] for it in json.load(f) if it.get("url")]
    if not urls and not sys.stdin.isatty():
        urls += [it["url"] for it in json.load(sys.stdin) if it.get("url")]
    out = []
    for i, u in enumerate(urls):
        if i:
            time.sleep(args.delay)
        try:
            out.append(fetch_ad(u))
            print(f"[otodom] ad {i + 1}/{len(urls)}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — one bad ad shouldn't sink the batch
            print(f"[otodom] skip {u}: {e}", file=sys.stderr)
    return out


def fetch_page(url: str, params: dict) -> dict:
    """Fetch a search page and return its parsed searchAds block."""
    r = _get(url, params)
    if r.status_code == 404:
        raise OtodomError(f"location not found (404) — check the path: {r.url}")
    if not r.ok:
        raise OtodomError(f"HTTP {r.status_code} fetching search: {r.url}")
    soup = BeautifulSoup(r.content, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if tag is None:
        raise OtodomError(f"blocked or layout changed (no __NEXT_DATA__): {r.url}")
    data = json.loads(tag.text)["props"]["pageProps"].get("data") or {}
    sa = data.get("searchAds")
    if sa is None:
        raise OtodomError(f"no results for this search: {r.url}")
    return sa


def _search_targets(args) -> list:
    """Resolve CLI args to a list of (location_path, source_label) to fetch.
    Multiple --location (repeatable) yield multiple targets; --city is one."""
    prefix = (f"{BASE}/pl/wyniki/{TRANSACTIONS[args.transaction]}/"
              f"{PROPERTY_TYPES[args.property_type]}/")
    locations = getattr(args, "location", None) or []
    if locations:
        targets = []
        for name in locations:
            cands = resolve_location(name)
            if not cands:
                print(f"[otodom] no location match for {name!r} — skipping", file=sys.stderr)
                continue
            chosen = cands[0]  # autosuggest ranks best first
            if len(cands) > 1:
                print(f"[otodom] {name!r} -> {chosen['label']} ({chosen['level']}); "
                      f"{len(cands) - 1} other matches — run `locations` to disambiguate",
                      file=sys.stderr)
            targets.append((prefix + chosen["path"], chosen["label"]))
        if not targets:
            raise OtodomError("no --location resolved to anything searchable")
        return targets
    if args.city:
        loc = f"{slugify(args.province)}/{slugify(args.city)}"
        if args.district:
            loc += f"/{slugify(args.district)}"
        return [(prefix + loc, None)]
    raise OtodomError("search needs --location NAME or --city CITY")


def _rooms_enum(spec: str) -> str:
    """'3' -> [THREE], '3-4' -> [THREE,FOUR], '3,4' -> [THREE,FOUR]."""
    spec = spec.strip()
    try:
        if "-" in spec:
            a, b = (int(x) for x in spec.split("-", 1))
            nums = range(a, b + 1)
        else:
            nums = [int(x) for x in spec.split(",")]
        nums = list(nums)
        if not nums:
            raise ValueError
    except ValueError:
        raise OtodomError(f"invalid --rooms {spec!r} (use e.g. 3, 3-4, or 3,4)")
    words = {v: k for k, v in ROOMS.items()}
    bad = [n for n in nums if n not in words]
    if bad:
        raise OtodomError(f"--rooms out of range {bad} (valid: 1-10)")
    return "[" + ",".join(words[n] for n in nums) + "]"


def _build_params(args) -> dict:
    params: dict = {"limit": 72}
    if args.min is not None:
        params["priceMin"] = args.min
    if args.max is not None:
        params["priceMax"] = args.max
    if getattr(args, "rooms", None):
        params["roomsNumber"] = _rooms_enum(args.rooms)
    if getattr(args, "radius", None) is not None:
        params["distanceRadius"] = args.radius
    if getattr(args, "area_min", None) is not None:
        params["areaMin"] = args.area_min
    if getattr(args, "area_max", None) is not None:
        params["areaMax"] = args.area_max
    if getattr(args, "extras", None):
        params["extras"] = "[" + ",".join(
            e.strip().upper() for e in args.extras.split(",")) + "]"
    for kv in args.query or []:  # raw escape hatch — wins on conflict
        k, _, v = kv.partition("=")
        params[k] = v
    return params


def _fetch_listings(path, params, args, source) -> list:
    """Fetch up to args.pages of one location's listings, tagged with source."""
    first = fetch_page(path, params)
    total_items = (first.get("pagination") or {}).get("totalItems", 0)
    if not total_items:
        raise OtodomError(f"0 listings match this search: {path}")
    total_pages = (first.get("pagination") or {}).get("totalPages", 1)
    pages = total_pages if getattr(args, "all", False) else min(total_pages, args.pages)
    if getattr(args, "radius", None) and total_items > 1000:
        # ponytail: flat threshold, no baseline fetch. e.g. Ząbki radius 5 -> ~1745
        print(f"[otodom] WARNING: --radius {args.radius} inflated to {total_items} "
              f"results — likely swallowing neighbouring areas", file=sys.stderr)
    print(f"[otodom] {source or path}: {total_items} listings, "
          f"{total_pages} pages; fetching {pages}", file=sys.stderr)
    items = list(first.get("items") or [])
    for page in range(2, pages + 1):
        time.sleep(args.delay)
        sa = fetch_page(path, {**params, "page": page})
        items += sa.get("items") or []
        print(f"[otodom] page {page}/{pages} ({len(items)} so far)", file=sys.stderr)
    recs = []
    for it in items:
        r = parse_item(it)
        if source is not None:
            r["source_location"] = source
        recs.append(r)
    return recs


def search(args) -> list:
    """Fetch every target location, merge into one array deduped by Otodom id
    (first source wins). A single bad location is warned and skipped."""
    targets = _search_targets(args)
    params = _build_params(args)
    by_id: dict = {}
    order: list = []
    for path, source in targets:
        try:
            recs = _fetch_listings(path, params, args, source)
        except OtodomError as e:  # one location failing shouldn't sink the run
            print(f"[otodom] {source or path}: {e} — skipping", file=sys.stderr)
            continue
        for r in recs:
            if r["id"] not in by_id:  # keep first occurrence (+ its source tag)
                by_id[r["id"]] = r
                order.append(r["id"])
    if not by_id:
        raise OtodomError("no listings found across all locations")
    return [by_id[i] for i in order]


def meta(args) -> list:
    """Machine-readable run metadata per target (no listing scrape): one object
    each with total_items, total_pages and the resolved_url."""
    params = _build_params(args)
    out = []
    for i, (path, source) in enumerate(_search_targets(args)):
        if i:
            time.sleep(args.delay)
        pag = (fetch_page(path, params).get("pagination") or {})
        out.append({
            "source": source,
            "total_items": pag.get("totalItems"),
            "total_pages": pag.get("totalPages"),
            "resolved_url": requests.Request("GET", path, params=params).prepare().url,
        })
    return out


def persist(records: list, db_path: str):
    """UPSERT records into the SQLite store by Otodom id (reuses store.ingest).
    Idempotent — re-running the same search updates rows in place."""
    import store
    area_by_id = {r["id"]: r.get("source_location") for r in records}
    store.ingest(records, area_by_id, db_path)


def main(argv=None):
    p = argparse.ArgumentParser(prog="otodom", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search listings and emit JSON")
    s.add_argument("--location", action="append", metavar="NAME",
                   help='free-text place, resolved via autosuggest, e.g. "Ząbki" '
                   '(repeatable — multiple are merged & deduped by id; '
                   'alternative to --city/--province/--district)')
    s.add_argument("--city", help="city slug, e.g. warszawa, czestochowa")
    s.add_argument("--province", default="mazowieckie", help="voivodeship slug")
    s.add_argument("--district", help="optional district within the city")
    s.add_argument("--transaction", choices=TRANSACTIONS, default="rent")
    s.add_argument("--property-type", choices=PROPERTY_TYPES, default="flat")
    s.add_argument("--min", type=int, help="min price")
    s.add_argument("--max", type=int, help="max price")
    s.add_argument("--rooms", help="rooms count or range, e.g. 3, 3-4, or 3,4")
    s.add_argument("--radius", type=int, metavar="KM",
                   help="distanceRadius — also pulls in neighbouring areas")
    s.add_argument("--area-min", type=int, help="min area m²")
    s.add_argument("--area-max", type=int, help="max area m²")
    s.add_argument("--extras", help="comma-separated extras, e.g. garden,terrace")
    s.add_argument("--pages", type=int, default=3, help="max pages to fetch (72/page)")
    s.add_argument("--all", action="store_true", help="fetch every page (ignores --pages)")
    s.add_argument("--meta", action="store_true",
                   help="emit run metadata (total_items/total_pages/resolved_url) as JSON, no scrape")
    s.add_argument("--delay", type=float, default=0.5, help="seconds between pages")
    s.add_argument("--query", action="append", metavar="KEY=VAL",
                   help="extra raw Otodom query params (repeatable), e.g. areaMin=40")
    s.add_argument("--db", metavar="PATH",
                   help="also UPSERT results into this SQLite store (read back: store.py --top)")
    s.add_argument("-o", "--output", help="write JSON here instead of stdout")
    s.add_argument("--pretty", action="store_true", help="indent JSON output")

    d = sub.add_parser("details", help="enrich ad URLs with extras/bathtub/garden")
    d.add_argument("urls", nargs="*", help="ad URLs (or pipe/-i a search JSON array)")
    d.add_argument("-i", "--input", help="search JSON file to read URLs from")
    d.add_argument("--delay", type=float, default=0.5, help="seconds between ads")
    d.add_argument("--db", metavar="PATH",
                   help="also UPSERT enriched ads into this SQLite store (read back: store.py --top)")
    d.add_argument("-o", "--output", help="write JSON here instead of stdout")
    d.add_argument("--pretty", action="store_true", help="indent JSON output")

    loc = sub.add_parser("locations", help="resolve a place name to candidate slug paths")
    loc.add_argument("name", help='free-text place name, e.g. "Ząbki"')
    loc.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = p.parse_args(argv)

    cmds = {"search": search, "details": details,
            "locations": lambda a: resolve_location(a.name)}
    if args.cmd == "search" and args.meta:
        cmds["search"] = meta
    try:
        results = cmds[args.cmd](args)
    except OtodomError as e:  # one clean stderr line, nonzero exit, no traceback
        print(f"[otodom] error: {e}", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "db", None) and not getattr(args, "meta", False):
        persist(results, args.db)
    out = json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None)
    if getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[otodom] wrote {len(results)} listings to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
