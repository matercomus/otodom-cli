"""Self-check for the parser. Run: uv run test_otodom.py  (or: pytest)"""
import json

import otodom
from otodom import OtodomError, amenities, parse_item, slugify

SAMPLE = {
    "id": 1, "title": "Nice flat", "slug": "nice-flat-ID4BXrz",
    "totalPrice": {"value": 348000, "currency": "PLN"},
    "rentPrice": {"value": 750, "currency": "PLN"},
    "pricePerSquareMeter": {"value": 7404, "currency": "PLN"},
    "areaInSquareMeters": 47, "roomsNumber": "THREE", "floorNumber": "NINTH",
    "estate": "FLAT", "transaction": "SELL", "isPrivateOwner": False,
    "isPromoted": False, "dateCreated": "2026-06-29 07:43:03",
    "agency": {"name": "Some Agency"},
    "location": {
        "address": {"street": None, "city": {"name": "Częstochowa"},
                    "province": {"name": "śląskie"}},
        "reverseGeocoding": {"locations": [
            {"locationLevel": "city_or_village", "name": "Częstochowa"},
            {"locationLevel": "district", "name": "Trzech Wieszczów"}]},
    },
}


def test_parse_item():
    r = parse_item(SAMPLE)
    assert r["url"] == "https://www.otodom.pl/pl/oferta/nice-flat-ID4BXrz"
    assert r["price"] == 348000 and r["currency"] == "PLN"
    assert r["rooms"] == 3  # worded enum -> int
    assert r["district"] == "Trzech Wieszczów"
    assert r["city"] == "Częstochowa"
    assert r["agency"] == "Some Agency"


def test_parse_handles_missing_fields():
    r = parse_item({"id": 2, "slug": "x"})  # almost everything absent
    assert r["price"] is None and r["rooms"] is None and r["district"] is None


def test_slugify():
    assert slugify("Częstochowa") == "czestochowa"
    assert slugify("Warmińsko Mazurskie") == "warminsko-mazurskie"


def test_amenities():
    assert amenities([], "Łazienka z wanną i prysznicem") == (True, False)
    assert amenities(["garden", "garage"], "duży salon") == (False, True)
    assert amenities([], "do mieszkania należy ogródek") == (False, True)
    assert amenities([], "kabina prysznicowa") == (False, False)  # no tub, no garden


def test_amenities_diacritic_stems():
    # the real wanną ∌ wanna bug: declensions + diacritics must still match
    for desc in ("wygodna wanna", "wanną narożną", "w łazience wannie", "WANNĄ"):
        assert amenities([], desc)[0] is True, desc
    assert amenities([], "ogród z tarasem")[1] is True  # ogród -> ogrod


def test_location_path():
    from otodom import _location_path
    # town: name, powiat, region -> region/powiat/city/city (gmina = city slug)
    assert _location_path("Ząbki, wołomiński, mazowieckie", "CITY") == \
        "mazowieckie/wolominski/zabki/zabki"
    # county-rights city: name, region -> region/city/city/city
    assert _location_path("Warszawa, mazowieckie", "CITY") == \
        "mazowieckie/warszawa/warszawa/warszawa"
    # district: name, city, region -> region/city/city/city/district
    assert _location_path("Wawer, Warszawa, mazowieckie", "DISTRICT") == \
        "mazowieckie/warszawa/warszawa/warszawa/wawer"


class _Resp:
    def __init__(self, status, url="http://x", content=b""):
        self.status_code, self.ok, self.url, self.content = status, status < 400, url, content


def test_rooms_enum():
    from otodom import _rooms_enum
    assert _rooms_enum("3") == "[THREE]"
    assert _rooms_enum("3-4") == "[THREE,FOUR]"
    assert _rooms_enum("3,4") == "[THREE,FOUR]"
    for bad in ("0", "11", "abc", "4-2"):  # out of range / non-numeric / empty range
        try:
            _rooms_enum(bad)
            assert False, bad
        except OtodomError:
            pass


def test_meta_payload_shape():
    from otodom import SearchCriteria
    saved = (otodom._search_targets, otodom.fetch_page)
    otodom._search_targets = lambda a: [("https://x/path", "Ząbki")]
    otodom.fetch_page = lambda path, params: {"pagination": {"totalItems": 292, "totalPages": 5}}
    a = SearchCriteria(delay=0)
    try:
        out = otodom.meta(a)
    finally:
        otodom._search_targets, otodom.fetch_page = saved
    m = out[0]
    assert m["total_items"] == 292 and m["total_pages"] == 5
    assert m["resolved_url"].startswith("https://x/path?") and "limit=72" in m["resolved_url"]


def test_persist_upsert_and_rank():
    import os
    import sqlite3
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let store create it fresh
    recs = [
        {"id": 1, "rooms": 3, "has_bathtub": True, "has_garden": False,
         "source_location": "Ząbki", "price": 300000},
        {"id": 2, "rooms": 2, "has_bathtub": False, "has_garden": True,
         "source_location": "Wawer", "price": 250000},
    ]
    otodom.persist(recs, path)
    otodom.persist(recs, path)  # second run must be idempotent
    con = sqlite3.connect(path)
    n = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    top = con.execute(
        "SELECT id, bathtub, garden, area_tag FROM listings "
        "ORDER BY prefs_score DESC").fetchall()
    con.close()
    os.remove(path)
    assert n == 2  # 2 ids despite ingesting 4 records (UPSERT by id)
    # adapter mapped bools->yes/no and source_location->area_tag; id 1 ranks first
    assert top[0] == (1, "yes", "no", "Ząbki")


def _stub_search(targets, data):
    """Swap search()'s target/param/fetch helpers; returns a restore() callback."""
    saved = (otodom._search_targets, otodom._build_params, otodom._fetch_listings)
    otodom._search_targets = lambda args: targets
    otodom._build_params = lambda args: {}

    def fetch(path, params, c, source):
        v = data[path]
        if isinstance(v, Exception):
            raise v
        return v
    otodom._fetch_listings = fetch
    def restore():
        otodom._search_targets, otodom._build_params, otodom._fetch_listings = saved
    return restore


def test_search_dedup_by_id():
    # id 2 appears in both locations -> one row, first source (Ząbki) wins
    restore = _stub_search(
        [("p1", "Ząbki"), ("p2", "Wawer")],
        {"p1": [{"id": 1, "source_location": "Ząbki"}, {"id": 2, "source_location": "Ząbki"}],
         "p2": [{"id": 2, "source_location": "Wawer"}, {"id": 3, "source_location": "Wawer"}]})
    try:
        out = otodom.search(otodom.SearchCriteria())
    finally:
        restore()
    assert [r["id"] for r in out] == [1, 2, 3]  # no duplicate id 2
    assert next(r for r in out if r["id"] == 2)["source_location"] == "Ząbki"


def test_search_skips_failed_location():
    # one location errors -> warned & skipped, the other still returns
    restore = _stub_search(
        [("bad", "Nowhere"), ("p2", "Wawer")],
        {"bad": OtodomError("404"), "p2": [{"id": 9, "source_location": "Wawer"}]})
    try:
        out = otodom.search(otodom.SearchCriteria())
    finally:
        restore()
    assert [r["id"] for r in out] == [9]


def test_fetch_page_404_is_clean():
    orig = otodom._get
    otodom._get = lambda url, params=None: _Resp(404, url=url)
    try:
        otodom.fetch_page("http://x/badpath", {})
        assert False, "expected OtodomError"
    except OtodomError as e:
        assert "404" in str(e) and "badpath" in str(e)
    finally:
        otodom._get = orig


def _html(ad):
    """Wrap an `ad` dict in a minimal page carrying it as __NEXT_DATA__ —
    a saved fixture, so location tests never touch the live site."""
    blob = json.dumps({"props": {"pageProps": {"ad": ad}}}, ensure_ascii=False)
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            f'{blob}</script></body></html>')


# Precise: exact street address, radius 0 (mapDetails present).
PRECISE_AD = {
    "id": 64939319,
    "location": {
        "address": {"street": {"name": "Krakowska", "number": "10"},
                    "city": {"name": "Częstochowa"},
                    "district": {"name": "Śródmieście"},
                    "province": {"name": "śląskie"}},
        "mapDetails": {"radius": 0, "zoom": 16},
        "coordinates": {"latitude": 50.8118, "longitude": 19.1203},
    },
}
# Approximate: privacy circle (radius 500m), no street, coords as strings.
APPROX_AD = {
    "id": 1,
    "location": {
        "address": {"city": {"name": "Warszawa"}, "province": {"name": "mazowieckie"}},
        "mapDetails": {"radius": 500, "zoom": 13},
        "coordinates": {"latitude": "52.2297", "longitude": "21.0122"},
    },
}


def test_extract_location_precise():
    r = otodom.extract_location(_html(PRECISE_AD),
                                "https://www.otodom.pl/pl/oferta/nice-flat-ID4BSTV")
    assert (r["lat"], r["lng"]) == (50.8118, 19.1203)
    assert r["approximate"] is False and r["radius"] == 0
    assert r["street"] == "Krakowska" and r["city"] == "Częstochowa"
    assert r["district"] == "Śródmieście" and r["region"] == "śląskie"
    assert r["ad_id"] == "4BSTV"


def test_extract_location_approximate():
    r = otodom.extract_location(_html(APPROX_AD),
                                "https://www.otodom.pl/pl/oferta/x-ID9Z")
    assert r["approximate"] is True and r["radius"] == 500
    assert r["lat"] == 52.2297 and r["lng"] == 21.0122  # numeric strings coerced
    assert r["city"] == "Warszawa" and r["street"] is None
    assert r["ad_id"] == "9Z"


def test_extract_location_no_coords():
    ad = {"id": 1, "location": {"address": {"city": {"name": "X"}}}}
    try:
        otodom.extract_location(_html(ad), "u")
        assert False, "expected OtodomError"
    except OtodomError as e:
        assert "no coordinates" in str(e)


def test_extract_location_blocked():
    # bot-challenge page: no __NEXT_DATA__ -> clear error, not empty data
    try:
        otodom.extract_location("<html><body>Access denied</body></html>", "u")
        assert False, "expected OtodomError"
    except OtodomError as e:
        assert "__NEXT_DATA__" in str(e)


def test_extract_location_bad_json():
    html = ('<html><script id="__NEXT_DATA__">{not json}</script></html>')
    try:
        otodom.extract_location(html, "u")
        assert False, "expected OtodomError"
    except OtodomError as e:
        assert "unparseable" in str(e)


if __name__ == "__main__":
    test_parse_item()
    test_parse_handles_missing_fields()
    test_slugify()
    test_amenities()
    test_amenities_diacritic_stems()
    test_location_path()
    test_rooms_enum()
    test_meta_payload_shape()
    test_persist_upsert_and_rank()
    test_search_dedup_by_id()
    test_search_skips_failed_location()
    test_fetch_page_404_is_clean()
    test_extract_location_precise()
    test_extract_location_approximate()
    test_extract_location_no_coords()
    test_extract_location_blocked()
    test_extract_location_bad_json()
    print("ok")
