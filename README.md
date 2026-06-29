# otodom-cli

A small, agent-friendly CLI that pulls apartment/house listings from the Polish
property site [otodom.pl](https://www.otodom.pl) and prints them as JSON.

It reads the structured data Otodom already embeds in each search page
(`__NEXT_DATA__`), so one HTTP request returns ~72 fully-parsed listings — no
per-listing fetches, no database, no browser.

## Setup

Needs [uv](https://docs.astral.sh/uv/) and Python 3.14 (uv installs it for you):

```bash
uv sync
```

## Usage

```bash
# Flats for sale in Częstochowa under 400k PLN, 3 rooms, first 2 pages
uv run otodom.py search --city czestochowa --province slaskie \
    --max 400000 --rooms 3 --pages 2 -o listings.json

# Rentals in Warszawa, pipe straight into jq
uv run otodom.py search --city warszawa --transaction rent --pretty | jq '.[].price'
```

#### Nested locations (districts / separate towns)

`--city` (and `--district`) are joined into the URL path as-is, so pass the full
Otodom path segment when a place nests deeper than `province/city`:

```bash
# Wawer is a district of Warszawa (province/powiat/gmina/city/district all "warszawa")
uv run otodom.py search --city warszawa/warszawa/warszawa --district wawer \
    --query "roomsNumber=[THREE,FOUR]"

# Ząbki is a separate town in powiat wołomiński
uv run otodom.py search --city wolominski/zabki/zabki \
    --query "roomsNumber=[THREE,FOUR]"
```

### Enriching with extras / bath tub / garden

Search JSON has no amenities or free text. `details` fetches each ad's own page
and returns `extras` (slugs like `garden`, `balcony`, `terrace`), plus
`has_bathtub` (Polish stem `wann` in the description) and `has_garden`
(`garden` extras slug or `ogród`/`ogrod` in the text), and the plain-text
`description`.

```bash
uv run otodom.py details -i listings.json -o enriched.json   # urls from a search file
uv run otodom.py details https://www.otodom.pl/pl/oferta/...  # or pass URLs directly
```

### Storing & ranking (`store.py`)

`store.py` keeps enriched listings in a local SQLite DB (`listings.db`,
gitignored) so you can search and compare them later without re-scraping.
Re-running ingest UPSERTs by otodom `id`. `prefs_score` ranks by the soft
preferences **3-4 rooms > bath tub > garden**.

```bash
uv run store.py -i enriched.json --candidates listings.json   # ingest (+area tag)
uv run store.py --top 20                                       # markdown report
# ad-hoc queries are just SQL:
sqlite3 listings.db "SELECT title,price FROM listings WHERE bathtub='yes' AND rooms IN (3,4)"
```

Listings print as a JSON array on stdout (progress goes to stderr, so pipes stay
clean). Each record is flat and ready to filter:

```json
{
  "id": 68151985,
  "title": "Trzech Wieszczów | Trzy pokoje idealne dla Ciebie!",
  "url": "https://www.otodom.pl/pl/oferta/trzech-wieszczow-...",
  "price": 348000, "currency": "PLN", "rent": 750, "price_per_m2": 7404,
  "area_m2": 47, "rooms": 3, "floor": "NINTH",
  "type": "FLAT", "transaction": "SELL", "is_private": false,
  "city": "Częstochowa", "province": "śląskie", "district": "Trzech Wieszczów",
  "street": null, "agency": "Marvest Market Nieruchomości"
}
```

### Options

| Flag | Default | Notes |
|------|---------|-------|
| `--city` | (required) | e.g. `warszawa`, `czestochowa` (Polish chars auto-stripped) |
| `--province` | `mazowieckie` | voivodeship slug, e.g. `slaskie` |
| `--district` | — | optional district within the city |
| `--transaction` | `sale` | `sale` or `rent` |
| `--property-type` | `flat` | `flat studio house investment room plot venue magazine garage` |
| `--min` / `--max` | — | price range |
| `--rooms` | — | exact room count |
| `--pages` | `3` | max search pages to fetch (72 listings each) |
| `--delay` | `0.5` | seconds between page requests |
| `--query KEY=VAL` | — | pass any extra raw Otodom query param (repeatable), e.g. `--query areaMin=40` |
| `-o, --output` | stdout | write JSON to a file |
| `--pretty` | off | indent the JSON |

## Tests

```bash
uv run test_otodom.py
uv run test_store.py
```

## Notes

Scraping depends on Otodom's page structure; if the `__NEXT_DATA__` layout
changes, `parse_item`/`fetch_page` in `otodom.py` are where to look.
Be considerate with `--pages` and `--delay`.

## Credits

The original idea comes from
[TheRealSeber/Otodom-Listings-Scraper](https://github.com/TheRealSeber/Otodom-Listings-Scraper).
This is a distinct, independent rewrite — a single-file CLI built around
Otodom's embedded JSON, with no database and an agent-friendly JSON interface —
rather than a fork of that project.
