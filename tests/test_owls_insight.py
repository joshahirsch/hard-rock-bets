"""Tests for src/fetch/owls_insight.py.

The load-bearing test here is the merge: Owls' v1 API groups events by
bookmaker, and each bookmaker group's event list carries ONLY that bookmaker's
price nested inside. The fixture below is built exactly that way -- the same
event appears once per bookmaker key with a single nested book each -- so a
naive "read one key" implementation would produce a one-book view and fail.
"""

import pytest

from src.fetch.owls_insight import (
    DEFAULT_BOOKMAKER_PREFERENCE,
    DEFAULT_KEEP_BOOKS,
    HARD_ROCK_BOOK_KEY,
    OwlsInsightError,
    find_event,
    normalize_odds_response,
)

EVENT_ID = "evt-blue-jays-red-sox"
BOOK_PRICES = {
    "hardrock": (-118, 100),
    "pinnacle": (-125, 105),
    "draftkings": (-120, 102),
    "fanduel": (-122, 103),
    "betmgm": (-119, 101),
    "caesars": (-121, 102),
    # Books deliberately outside DEFAULT_KEEP_BOOKS.
    "novig": (-115, 98),
    "circa": (-124, 104),
}


def _event_for_book(book_key, home_price, away_price):
    """One event object as it appears INSIDE a single bookmaker's group."""
    return {
        "id": EVENT_ID,
        "sport_key": "mlb",
        "commence_time": "2026-08-12T23:07:00Z",
        "home_team": "Boston Red Sox",
        "away_team": "Toronto Blue Jays",
        "bookmakers": [
            {
                "key": book_key,
                "title": book_key.title(),
                "last_update": "2026-08-12T17:29:40Z",
                "event_link": f"https://example.invalid/{book_key}/{EVENT_ID}",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Red Sox", "price": home_price},
                            {"name": "Toronto Blue Jays", "price": away_price},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 8.5},
                            {"name": "Under", "price": -110, "point": 8.5},
                        ],
                    },
                ],
            }
        ],
    }


@pytest.fixture
def raw_payload():
    return {
        "success": True,
        "data": {
            book: [_event_for_book(book, home, away)]
            for book, (home, away) in BOOK_PRICES.items()
        },
    }


def test_hard_rock_key_on_this_vendor_is_hardrock_not_hardrockbet():
    assert HARD_ROCK_BOOK_KEY == "hardrock"
    assert "hardrockbet" not in DEFAULT_KEEP_BOOKS


def test_merge_across_bookmaker_keys_produces_one_multi_book_event(raw_payload):
    events = normalize_odds_response(raw_payload)
    assert len(events) == 1, "the same event repeated per book must collapse to one"
    ev = events[0]
    assert ev.event_id == EVENT_ID
    assert ev.home_team == "Boston Red Sox"
    assert ev.books_for("h2h") == sorted(DEFAULT_KEEP_BOOKS)


def test_reading_a_single_bookmaker_key_would_have_lost_every_other_book(raw_payload):
    # Demonstrates the failure mode the merge exists to prevent.
    only_pinnacle = {"success": True, "data": {"pinnacle": raw_payload["data"]["pinnacle"]}}
    events = normalize_odds_response(only_pinnacle)
    assert events[0].books_for("h2h") == ["pinnacle"]
    assert len(events[0].books_for("h2h")) < len(DEFAULT_KEEP_BOOKS)


def test_books_outside_keep_list_are_dropped(raw_payload):
    ev = normalize_odds_response(raw_payload)[0]
    assert "novig" not in ev.books_for("h2h")
    assert "circa" not in ev.books_for("h2h")


def test_each_books_own_prices_survive_the_merge_unmixed(raw_payload):
    ev = normalize_odds_response(raw_payload)[0]
    for book, (home, away) in BOOK_PRICES.items():
        if book not in DEFAULT_KEEP_BOOKS:
            continue
        assert ev.two_sided_h2h(book) == (home, away)


def test_market_filter_keeps_only_requested_markets(raw_payload):
    ev = normalize_odds_response(raw_payload, markets=("h2h",))[0]
    assert set(ev.markets) == {"h2h"}
    both = normalize_odds_response(raw_payload, markets=None)[0]
    assert set(both.markets) == {"h2h", "totals"}


def test_reference_book_prefers_hardrock(raw_payload):
    ev = normalize_odds_response(raw_payload)[0]
    assert ev.reference_book(DEFAULT_BOOKMAKER_PREFERENCE) == "hardrock"


def test_reference_book_falls_through_the_preference_order(raw_payload):
    del raw_payload["data"]["hardrock"]
    ev = normalize_odds_response(raw_payload)[0]
    assert ev.reference_book(DEFAULT_BOOKMAKER_PREFERENCE) == "draftkings"


def test_normalized_event_feeds_the_math_layer_directly(raw_payload):
    from src.math.novig import no_vig_probability

    ev = normalize_odds_response(raw_payload)[0]
    home, away = ev.two_sided_h2h("hardrock")
    assert 0.0 < no_vig_probability(home, away) < 1.0


def test_malformed_price_is_left_none_never_coerced():
    payload = {
        "success": True,
        "data": {
            "hardrock": [
                {
                    "id": "e1",
                    "home_team": "A",
                    "away_team": "B",
                    "bookmakers": [
                        {
                            "key": "hardrock",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "A", "price": "not-a-number"},
                                        {"name": "B", "price": 130.5},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }
    ev = normalize_odds_response(payload)[0]
    assert ev.two_sided_h2h("hardrock") is None


def test_bad_payload_shape_raises():
    with pytest.raises(OwlsInsightError):
        normalize_odds_response({"success": True})


def test_find_event_by_team_names(raw_payload):
    events = normalize_odds_response(raw_payload)
    assert find_event(events, "Blue Jays", "Red Sox") is not None
    assert find_event(events, "Dodgers") is None


def test_client_requires_an_api_key(monkeypatch):
    from src.fetch.owls_insight import OwlsInsightClient

    monkeypatch.delenv("OWLS_INSIGHT_API_KEY", raising=False)
    with pytest.raises(OwlsInsightError):
        OwlsInsightClient()
