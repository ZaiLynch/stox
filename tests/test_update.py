import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update  # noqa: E402
from update import Quote  # noqa: E402

D = dt.date
UTC = dt.timezone.utc


def hourly(day, hour, close):
    return (dt.datetime(day.year, day.month, day.day, hour, tzinfo=UTC), day, close)


GAME = {
    "id": "2026-Q1",
    "start": "2026-01-01",
    "end": "2026-03-31",
    "invested_eur": 50,
    "picks": [
        {"player": "Alice", "ticker": "AAA.DE", "name": "Aaa"},
        {"player": "Bob", "ticker": "BBB", "name": "Bbb"},
    ],
}

QUOTES = {
    # EUR stock: +10 % at the end, one dividend of 1 EUR.
    "AAA.DE": Quote(
        currency="EUR",
        daily=[(D(2025, 12, 30), 100.0), (D(2026, 1, 2), 104.0), (D(2026, 3, 31), 109.0), (D(2026, 4, 1), 200.0)],
        hourly=[hourly(D(2026, 1, 2), 10, 102.0), hourly(D(2026, 1, 2), 16, 104.0), hourly(D(2026, 3, 31), 15, 108.0)],
        dividends=[(D(2025, 12, 1), 5.0), (D(2026, 2, 1), 1.0)],
    ),
    # USD stock: price flat, the dollar loses 10 % against the euro.
    "BBB": Quote(
        currency="USD",
        daily=[(D(2025, 12, 31), 20.0), (D(2026, 3, 30), 20.0)],
        hourly=[hourly(D(2026, 1, 2), 15, 20.0), hourly(D(2026, 3, 30), 20, 20.0)],
        dividends=[],
    ),
}
USD_EUR = [(D(2025, 12, 31), 0.90), (D(2026, 3, 1), 0.81)]


def fake_quote(ticker, since, until):
    if ticker not in QUOTES:
        raise LookupError(ticker)
    return QUOTES[ticker]


def fake_fx(currency, since, until):
    assert currency == "USD"
    return USD_EUR


@pytest.fixture
def games(tmp_path):
    folder = tmp_path / GAME["id"]
    folder.mkdir()
    (folder / "game.json").write_text(json.dumps(GAME))
    return tmp_path


def run(games, now):
    folder = games / GAME["id"]
    game = update.load_game(folder)
    return update.update_game(folder, game, now, fake_quote, fake_fx)


def read(games, name):
    return json.loads((games / GAME["id"] / name).read_text())


def test_upcoming_writes_nothing(games):
    assert run(games, dt.datetime(2025, 12, 31, 12, tzinfo=UTC)) == "upcoming"
    assert not (games / GAME["id"] / "prices.json").exists()


def test_running_game_values(games):
    assert run(games, dt.datetime(2026, 2, 15, 12, tzinfo=UTC)) == "running"
    picks = read(games, "prices.json")["picks"]
    alice = picks["Alice"]
    assert alice["reference"] == {"date": "2025-12-30", "close": 100.0, "eur_rate": 1.0}
    assert alice["series"][0] == ["2026-01-01T00:00:00Z", 50.0]
    # Dividend before the start is ignored; the one on 2026-02-01 is not reached by Jan 2.
    assert [v for _, v in alice["series"][1:]] == [51.0, 52.0]
    # The 2026-03-31 bar lies after "now" and is not included.
    assert alice["value"] == 52.0
    assert not (games / GAME["id"] / "result.json").exists()


def test_finished_game_closes_with_official_close(games):
    assert run(games, dt.datetime(2026, 4, 1, 9, tzinfo=UTC)) == "closed"
    picks = read(games, "prices.json")["picks"]
    # (109 + 1 dividend) / 100 * 50; the last hourly bar is replaced by the daily close.
    assert picks["Alice"]["value"] == 55.0
    assert picks["Alice"]["series"][-1] == ["2026-03-31T15:00:00Z", 55.0]
    # 50 * 0.81 / 0.90
    assert picks["Bob"]["value"] == 45.0
    result = read(games, "result.json")
    assert result["losers"] == ["Bob"]
    assert result["standings"] == [{"player": "Alice", "value": 55.0}, {"player": "Bob", "value": 45.0}]


def test_closed_game_is_never_touched(games):
    run(games, dt.datetime(2026, 4, 1, 9, tzinfo=UTC))
    folder = games / GAME["id"]
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert run(games, dt.datetime(2026, 5, 1, 9, tzinfo=UTC)) == "closed"
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_failed_ticker_keeps_old_data_and_delays_closing(games, monkeypatch):
    run(games, dt.datetime(2026, 2, 15, 12, tzinfo=UTC))
    old_bob = read(games, "prices.json")["picks"]["Bob"]
    monkeypatch.delitem(QUOTES, "BBB")
    assert run(games, dt.datetime(2026, 4, 1, 9, tzinfo=UTC)) == "finished"
    assert read(games, "prices.json")["picks"]["Bob"] == old_bob
    assert not (games / GAME["id"] / "result.json").exists()


def test_rank_ties_share_the_bill():
    standings, losers = update.rank({"A": 48.001, "B": 47.996, "C": 60.0})
    assert losers == ["A", "B"]
    assert standings[0] == {"player": "C", "value": 60.0}


@pytest.mark.parametrize("change, message", [
    ({"id": "other"}, "does not match"),
    ({"start": "2026-05-01"}, "start is after end"),
    ({"invested_eur": 0}, "positive"),
    ({"picks": [{"player": "A", "ticker": "X"}, {"player": "A", "ticker": "Y"}]}, "twice"),
    ({"picks": [{"player": "A"}]}, "ticker"),
])
def test_invalid_game(tmp_path, change, message):
    folder = tmp_path / GAME["id"]
    folder.mkdir()
    (folder / "game.json").write_text(json.dumps({**GAME, **change}))
    with pytest.raises(update.GameError, match=message):
        update.load_game(folder)


def test_index(games):
    run(games, dt.datetime(2026, 4, 1, 9, tzinfo=UTC))
    folder = games / GAME["id"]
    entry = update.index_entry(folder, update.load_game(folder), "closed")
    assert entry["losers"] == ["Bob"]
    assert len(entry["checksum"]) == 64
