"""Self-check for the prefs ranking. Run: uv run test_store.py (or: pytest)"""
from store import score


def test_score():
    # priority order: 3-4 rooms (+2) > bath tub (+2) > garden (+1)
    assert score({"rooms": 3, "bathtub": "yes", "garden": "yes"}) == 5
    assert score({"rooms": 4, "bathtub": "yes", "garden": "no"}) == 4
    assert score({"rooms": 4, "bathtub": "unknown", "garden": "yes"}) == 3
    assert score({"rooms": 2, "bathtub": "no", "garden": "unknown"}) == 0
    assert score({}) == 0


if __name__ == "__main__":
    test_score()
    print("ok")
