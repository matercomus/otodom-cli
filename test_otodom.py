"""Self-check for the parser. Run: uv run test_otodom.py  (or: pytest)"""
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
    import types
    saved = (otodom._search_targets, otodom.fetch_page)
    otodom._search_targets = lambda a: [("https://x/path", "Ząbki")]
    otodom.fetch_page = lambda path, params: {"pagination": {"totalItems": 292, "totalPages": 5}}
    a = types.SimpleNamespace(min=None, max=None, rooms=None, radius=None,
                              area_min=None, area_max=None, extras=None, query=None, delay=0)
    try:
        out = otodom.meta(a)
    finally:
        otodom._search_targets, otodom.fetch_page = saved
    m = out[0]
    assert m["total_items"] == 292 and m["total_pages"] == 5
    assert m["resolved_url"].startswith("https://x/path?") and "limit=72" in m["resolved_url"]


def _stub_search(targets, data):
    """Swap search()'s target/param/fetch helpers; returns a restore() callback."""
    saved = (otodom._search_targets, otodom._build_params, otodom._fetch_listings)
    otodom._search_targets = lambda args: targets
    otodom._build_params = lambda args: {}

    def fetch(path, params, args, source):
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
        out = otodom.search(object())
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
        out = otodom.search(object())
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


if __name__ == "__main__":
    test_parse_item()
    test_parse_handles_missing_fields()
    test_slugify()
    test_amenities()
    test_amenities_diacritic_stems()
    test_location_path()
    test_rooms_enum()
    test_meta_payload_shape()
    test_search_dedup_by_id()
    test_search_skips_failed_location()
    test_fetch_page_404_is_clean()
    print("ok")
