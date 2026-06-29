#!/usr/bin/env python3
"""Persist enriched Otodom listings to SQLite so they can be searched and
compared later without re-scraping. UPSERT by otodom id.

    uv run otodom.py details -i candidates.json -o enriched.json
    uv run store.py -i enriched.json --candidates candidates.json        # ingest
    uv run store.py --top 20                                             # markdown report

Listings live in listings.db (gitignored). Re-running ingest updates rows in
place. prefs_score ranks by the soft preferences: 3-4 rooms, a bath tub, a
garden (in that priority order).
"""
import argparse
import json
import sqlite3
import sys

DB = "listings.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    url TEXT, title TEXT, area_tag TEXT,
    rooms INTEGER, area_m2 REAL, price REAL,
    bathtub TEXT, garden TEXT, reason TEXT,
    extras TEXT, prefs_score INTEGER,
    scraped_at TEXT DEFAULT (datetime('now'))
)
"""
COLS = ["id", "url", "title", "area_tag", "rooms", "area_m2", "price",
        "bathtub", "garden", "reason", "extras", "prefs_score"]


def score(r: dict) -> int:
    """Soft prefs, priority order: 3-4 rooms (+2) > bath tub (+2) > garden (+1).
    'unknown'/'no' add nothing; we rank, not exclude."""
    s = 0
    if r.get("rooms") in (3, 4):
        s += 2
    if r.get("bathtub") == "yes":
        s += 2
    if r.get("garden") == "yes":
        s += 1
    return s


def to_record(r: dict) -> dict:
    """Map a search/details record onto store's schema: has_bathtub/has_garden
    bools -> bathtub/garden yes/no/unknown strings. Idempotent (reads has_*)."""
    def yn(key):
        v = r.get(key)
        return "yes" if v is True else "no" if v is False else "unknown"
    return {**r, "bathtub": yn("has_bathtub"), "garden": yn("has_garden")}


def ingest(records: list, area_by_id: dict, db_path=DB):
    con = sqlite3.connect(db_path)
    con.execute(SCHEMA)
    rows = []
    for r in records:
        r = to_record(r)
        r = {**r, "area_tag": area_by_id.get(r["id"]),
             "extras": json.dumps(r.get("extras") or [], ensure_ascii=False),
             "prefs_score": score(r)}
        rows.append([r.get(c) for c in COLS])
    placeholders = ",".join("?" * len(COLS))
    updates = ",".join(f"{c}=excluded.{c}" for c in COLS if c != "id")
    con.executemany(
        f"INSERT INTO listings ({','.join(COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}, scraped_at=datetime('now')",
        rows)
    con.commit()
    n = con.total_changes
    con.close()
    print(f"[store] upserted {len(rows)} listings ({n} row changes)", file=sys.stderr)


def report(limit: int, db_path=DB):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM listings ORDER BY prefs_score DESC, rooms DESC, price ASC "
        "LIMIT ?", (limit,)).fetchall()
    con.close()
    print(f"# Top {len(rows)} matches (by prefs: 3-4 rooms > bath tub > garden)\n")
    print("| # | score | area | rooms | m² | price PLN | bath | garden | title |")
    print("|---|------|------|-------|----|-----------|------|--------|-------|")
    for i, r in enumerate(rows, 1):
        price = f"{int(r['price']):,}".replace(",", " ") if r["price"] else "?"
        print(f"| {i} | {r['prefs_score']} | {r['area_tag'] or '?'} | "
              f"{r['rooms'] or '?'} | {r['area_m2'] or '?'} | {price} | "
              f"{r['bathtub']} | {r['garden']} | [{(r['title'] or '')[:48]}]({r['url']}) |")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-i", "--input", help="enriched JSON (array or {records:[...]}); default stdin")
    p.add_argument("--candidates", help="search JSON to map id->area_tag")
    p.add_argument("--db", default=DB, help=f"SQLite store path (default {DB})")
    p.add_argument("--top", type=int, help="print markdown report of top N and exit")
    args = p.parse_args(argv)

    if not args.input:  # nothing to ingest -> report-only
        report(args.top if args.top is not None else 20, args.db)
        return

    raw = json.load(open(args.input))
    records = raw["records"] if isinstance(raw, dict) else raw
    area_by_id = {}
    if args.candidates:
        for c in json.load(open(args.candidates)):
            area_by_id[c["id"]] = c.get("area_tag")
    ingest(records, area_by_id, args.db)
    if args.top is not None:
        report(args.top, args.db)


if __name__ == "__main__":
    main()
