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


class _Resp:
    def __init__(self, status, url="http://x", content=b""):
        self.status_code, self.ok, self.url, self.content = status, status < 400, url, content


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
    test_fetch_page_404_is_clean()
    print("ok")
