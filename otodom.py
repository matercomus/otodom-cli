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


def fetch_page(url: str, params: dict) -> dict:
    """Fetch a search page and return its parsed searchAds block."""
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if tag is None:
        raise RuntimeError(f"No __NEXT_DATA__ on page (blocked or layout changed): {r.url}")
    data = json.loads(tag.text)["props"]["pageProps"].get("data") or {}
    sa = data.get("searchAds")
    if sa is None:
        raise RuntimeError(f"No searchAds in page data (no results?): {r.url}")
    return sa


def search(args) -> list:
    path = (f"{BASE}/pl/wyniki/{TRANSACTIONS[args.transaction]}/"
            f"{PROPERTY_TYPES[args.property_type]}/{slugify(args.province)}/"
            f"{slugify(args.city)}")
    if args.district:
        path += f"/{slugify(args.district)}"

    params: dict = {"limit": 72}
    if args.min is not None:
        params["priceMin"] = args.min
    if args.max is not None:
        params["priceMax"] = args.max
    if args.rooms is not None:
        # Otodom expects the worded enum, e.g. roomsNumber=[THREE]
        words = {v: k for k, v in ROOMS.items()}
        params["roomsNumber"] = f"[{words.get(args.rooms, args.rooms)}]"
    for kv in args.query or []:
        k, _, v = kv.partition("=")
        params[k] = v

    first = fetch_page(path, params)
    total_pages = (first.get("pagination") or {}).get("totalPages", 1)
    pages = min(total_pages, args.pages)
    print(f"[otodom] {first['pagination']['totalItems']} listings, "
          f"{total_pages} pages; fetching {pages}", file=sys.stderr)

    items = list(first.get("items") or [])
    for page in range(2, pages + 1):
        time.sleep(args.delay)
        sa = fetch_page(path, {**params, "page": page})
        items += sa.get("items") or []
        print(f"[otodom] page {page}/{pages} ({len(items)} so far)", file=sys.stderr)

    return [parse_item(it) for it in items]


def main(argv=None):
    p = argparse.ArgumentParser(prog="otodom", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search listings and emit JSON")
    s.add_argument("--city", required=True, help="e.g. warszawa, czestochowa")
    s.add_argument("--province", default="mazowieckie", help="voivodeship slug")
    s.add_argument("--district", help="optional district within the city")
    s.add_argument("--transaction", choices=TRANSACTIONS, default="sale")
    s.add_argument("--property-type", choices=PROPERTY_TYPES, default="flat")
    s.add_argument("--min", type=int, help="min price")
    s.add_argument("--max", type=int, help="max price")
    s.add_argument("--rooms", type=int, help="exact number of rooms")
    s.add_argument("--pages", type=int, default=3, help="max pages to fetch (72/page)")
    s.add_argument("--delay", type=float, default=0.5, help="seconds between pages")
    s.add_argument("--query", action="append", metavar="KEY=VAL",
                   help="extra raw Otodom query params (repeatable), e.g. areaMin=40")
    s.add_argument("-o", "--output", help="write JSON here instead of stdout")
    s.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = p.parse_args(argv)

    results = search(args)
    out = json.dumps(results, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"[otodom] wrote {len(results)} listings to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
