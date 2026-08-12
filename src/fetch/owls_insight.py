"""Owls Insight v1 odds fetch + normalization.

Background: ``claude/owls-insight-integration-2026-08-11.md``.

This module talks to ``https://api.owlsinsight.com`` **directly**, server-side,
with a Bearer token read from the environment. It deliberately does NOT go
through the v1 Zapier relay: that relay existed only because the v1 pipeline
ran inside a scheduled Claude session with no server of its own. This service
IS the server side, so it holds the key itself and calls the vendor directly.
Two consequences that were real problems for the relay simply vanish here:

  * the relay's 25,000-byte Storage-by-Zapier value cap (which forced dropping
    spreads and totals entirely) does not apply; and
  * there is no asynchronous fire-then-read-a-key-later dance.

CRITICAL STRUCTURAL NOTE (discovered by testing, not documented by Owls):
``response["data"]`` is keyed by **bookmaker**, and each top-level bookmaker's
event list contains only THAT ONE bookmaker's price nested inside each event's
``bookmakers`` array. It is NOT the case that every bookmaker-keyed array
carries the full multi-book event data. To build a multi-book view of one game
(which the consensus / no-vig math needs) you MUST iterate every kept bookmaker
key and merge by event ``id``. Reading a single key -- even ``pinnacle`` --
silently loses every other book's price for that event.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:  # httpx is the runtime dependency; import lazily so tests can run without it
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

DEFAULT_BASE_URL = "https://api.owlsinsight.com"
API_KEY_ENV_VAR = "OWLS_INSIGHT_API_KEY"

#: Hard Rock Bet's key on THIS vendor is ``hardrock``, NOT ``hardrockbet``
#: (``hardrockbet`` was the-odds-api.com's key under ``regions=us2``).
HARD_ROCK_BOOK_KEY = "hardrock"

#: Bookmaker keys confirmed live on the v1 MLB feed:
#: pinnacle, fanduel, draftkings, novig, caesars, betmgm, hardrock, circa,
#: south_point, wynn, stations.
DEFAULT_KEEP_BOOKS: Sequence[str] = (
    "hardrock",
    "pinnacle",
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
)

#: Preference order when picking the single reference price for a game.
DEFAULT_BOOKMAKER_PREFERENCE: Sequence[str] = (
    "hardrock",
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
    "pinnacle",
)

#: Only ``mlb`` has been live-confirmed. Guessing a slug is exactly the kind of
#: silent substitution this project's conventions prohibit -- test each new one.
CONFIRMED_SPORT_KEYS = ("mlb",)


class OwlsInsightError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Internal normalized schema
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    name: str
    price: Optional[int]
    point: Optional[float] = None


@dataclass
class MarketQuote:
    """One bookmaker's quote on one market for one event."""

    book_key: str
    market_key: str  # "h2h" | "spreads" | "totals"
    outcomes: List[Outcome]
    last_update: Optional[str] = None
    event_link: Optional[str] = None

    def price_for(self, name: str) -> Optional[int]:
        for o in self.outcomes:
            if o.name == name:
                return o.price
        return None


@dataclass
class NormalizedEvent:
    """One game, merged across every kept bookmaker key."""

    event_id: str
    sport_key: str
    commence_time: Optional[str]
    home_team: str
    away_team: str
    #: {market_key: {book_key: MarketQuote}}
    markets: Dict[str, Dict[str, MarketQuote]] = field(default_factory=dict)

    # -- convenience ------------------------------------------------------
    def books_for(self, market_key: str = "h2h") -> List[str]:
        return sorted(self.markets.get(market_key, {}).keys())

    def quote(self, book_key: str, market_key: str = "h2h") -> Optional[MarketQuote]:
        return self.markets.get(market_key, {}).get(book_key)

    def two_sided_h2h(self, book_key: str):
        """Return ``(home_price, away_price)`` for a book, or ``None``."""
        q = self.quote(book_key, "h2h")
        if q is None:
            return None
        home = q.price_for(self.home_team)
        away = q.price_for(self.away_team)
        if home is None or away is None:
            return None
        return home, away

    def reference_book(
        self, preference: Sequence[str] = DEFAULT_BOOKMAKER_PREFERENCE, market_key: str = "h2h"
    ) -> Optional[str]:
        """Preferred reference book: ``hardrock`` -> ... -> first listed."""
        available = self.markets.get(market_key, {})
        for key in preference:
            if key in available:
                return key
        return next(iter(sorted(available)), None)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "sport_key": self.sport_key,
            "commence_time": self.commence_time,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "markets": {
                mkey: {
                    bkey: {
                        "book_key": q.book_key,
                        "market_key": q.market_key,
                        "last_update": q.last_update,
                        "event_link": q.event_link,
                        "outcomes": [
                            {"name": o.name, "price": o.price, "point": o.point}
                            for o in q.outcomes
                        ],
                    }
                    for bkey, q in books.items()
                }
                for mkey, books in self.markets.items()
            },
        }


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_odds_response(
    payload: Dict[str, Any],
    keep_books: Sequence[str] = DEFAULT_KEEP_BOOKS,
    markets: Optional[Sequence[str]] = None,
) -> List[NormalizedEvent]:
    """Merge Owls' bookmaker-keyed v1 response into one record per event.

    ``payload`` is the raw decoded JSON body: ``{"success": bool, "data": {...}}``
    where ``data`` is keyed by bookmaker.

    ``markets`` filters by market key (e.g. ``("h2h",)``); ``None`` keeps all.

    This is the merge that the structural note at the top of this module makes
    mandatory -- every kept bookmaker key is iterated and results are joined by
    event ``id``.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise OwlsInsightError(
            "unexpected Owls Insight payload: 'data' is missing or not an object"
        )
    keep = {k.lower() for k in keep_books}
    merged: Dict[str, NormalizedEvent] = {}

    for book_group_key, events in data.items():
        if book_group_key.lower() not in keep:
            continue
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            event_id = ev.get("id")
            if event_id is None:
                continue
            record = merged.get(event_id)
            if record is None:
                record = NormalizedEvent(
                    event_id=event_id,
                    sport_key=ev.get("sport_key") or ev.get("sport") or "",
                    commence_time=ev.get("commence_time"),
                    home_team=ev.get("home_team", ""),
                    away_team=ev.get("away_team", ""),
                )
                merged[event_id] = record

            # Each event's own `bookmakers` array carries only this group's book.
            for bm in ev.get("bookmakers") or []:
                if not isinstance(bm, dict):
                    continue
                bkey = (bm.get("key") or "").lower()
                if bkey not in keep:
                    continue
                for mkt in bm.get("markets") or []:
                    if not isinstance(mkt, dict):
                        continue
                    mkey = mkt.get("key")
                    if not mkey or (markets is not None and mkey not in markets):
                        continue
                    outcomes = [
                        Outcome(
                            name=o.get("name", ""),
                            price=_coerce_price(o.get("price")),
                            point=o.get("point"),
                        )
                        for o in (mkt.get("outcomes") or [])
                        if isinstance(o, dict)
                    ]
                    if not outcomes:
                        continue
                    record.markets.setdefault(mkey, {})[bkey] = MarketQuote(
                        book_key=bkey,
                        market_key=mkey,
                        outcomes=outcomes,
                        last_update=bm.get("last_update"),
                        event_link=bm.get("event_link"),
                    )

    return list(merged.values())


def _coerce_price(value: Any) -> Optional[int]:
    """American prices arrive as ints; anything non-integral is left as None.

    market-math-spec.md §1: malformed odds are rejected, never coerced. A
    ``None`` price here means downstream math will simply skip that book rather
    than silently rounding a bad value into a plausible one.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return None
        return int(f) if f.is_integer() else None
    return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OwlsInsightClient:
    """Thin, direct, server-side client for the Owls Insight v1 odds endpoint.

    Auth is ``Authorization: Bearer <key>`` ONLY -- a ``?apiKey=`` query
    parameter returns 403 on this vendor.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        keep_books: Sequence[str] = DEFAULT_KEEP_BOOKS,
        timeout_seconds: float = 20.0,
        transport: Any = None,
    ) -> None:
        self.api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not self.api_key:
            raise OwlsInsightError(
                f"missing API key: set {API_KEY_ENV_VAR} in the environment "
                "(never commit it -- see .env.example)"
            )
        self.base_url = base_url.rstrip("/")
        self.keep_books = tuple(keep_books)
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def fetch_raw(self, sport: str) -> Dict[str, Any]:
        """``GET /api/v1/{sport}/odds`` and return the decoded JSON body."""
        if httpx is None:  # pragma: no cover
            raise OwlsInsightError("httpx is not installed; `pip install httpx`")
        url = f"{self.base_url}/api/v1/{sport}/odds"
        client_kwargs: Dict[str, Any] = {"timeout": self.timeout_seconds}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(url, headers=self._headers())
        if resp.status_code != 200:
            raise OwlsInsightError(
                f"Owls Insight returned HTTP {resp.status_code} for {url}: "
                f"{resp.text[:200]}"
            )
        return resp.json()

    def fetch_events(
        self, sport: str, markets: Optional[Sequence[str]] = ("h2h",)
    ) -> List[NormalizedEvent]:
        """Fetch and normalize one sport's slate into merged, multi-book events."""
        return normalize_odds_response(
            self.fetch_raw(sport), keep_books=self.keep_books, markets=markets
        )


def find_event(
    events: Iterable[NormalizedEvent], *team_names: str
) -> Optional[NormalizedEvent]:
    """Locate a game by (partial, case-insensitive) team names."""
    wanted = [t.lower() for t in team_names if t]
    for ev in events:
        haystack = f"{ev.home_team} {ev.away_team}".lower()
        if all(w in haystack for w in wanted):
            return ev
    return None
