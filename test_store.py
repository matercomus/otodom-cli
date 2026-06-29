"""Self-check for the prefs ranking. Run: uv run test_store.py (or: pytest)"""
import os
import sqlite3
import tempfile

import store
from store import score


def test_score():
    # priority order: 3-4 rooms (+2) > bath tub (+2) > garden (+1)
    assert score({"rooms": 3, "bathtub": "yes", "garden": "yes"}) == 5
    assert score({"rooms": 4, "bathtub": "yes", "garden": "no"}) == 4
    assert score({"rooms": 4, "bathtub": "unknown", "garden": "yes"}) == 3
    assert score({"rooms": 2, "bathtub": "no", "garden": "unknown"}) == 0
    assert score({}) == 0


def test_ingest_maps_enriched_bools_and_scores():
    # #15: details emits has_bathtub/has_garden bools; ingest must map them to
    # the stored yes/no columns so prefs_score actually sees them (not None/0).
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let ingest create it
    store.ingest([{"id": 1, "rooms": 4, "has_bathtub": True, "has_garden": False}], {}, path)
    con = sqlite3.connect(path)
    bathtub, garden, prefs = con.execute(
        "SELECT bathtub, garden, prefs_score FROM listings WHERE id=1").fetchone()
    con.close()
    os.remove(path)
    assert (bathtub, garden) == ("yes", "no")  # not None
    assert prefs == 4  # rooms 4 (+2) + bath tub (+2); garden no (+0)


if __name__ == "__main__":
    test_score()
    test_ingest_maps_enriched_bools_and_scores()
    print("ok")
