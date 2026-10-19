#!/usr/bin/env python3
"""Fetch prices for running games, close finished ones and rebuild games/index.json.

A game lives in games/<id>/ and consists of
  game.json    hand-written: dates, invested amount, picks
  prices.json  generated: reference price and EUR value series per pick
  result.json  generated once when the game is over; from then on the game is frozen

Usage: update.py [--games DIR] [--now 2026-10-20T12:00:00Z]
"""

import argparse
import datetime as dt
import hashlib
import json
import logging
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SCRIPT_VERSION = "1"
ROOT = Path(__file__).resolve().parent.parent
FX_URL = "https://api.frankfurter.dev/v1/{since}..{until}?base={currency}&symbols=EUR"
# Yahoo quotes some exchanges in minor units; the ratio cancels them, but FX needs the major one.
MINOR_CURRENCIES = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
# Time of the closing point when there is no hourly bar on the last day (after EU and US close).
CLOSE_FALLBACK_UTC = dt.time(22, 0)
GAME_FILES = ("game.json", "prices.json", "result.json")

log = logging.getLogger("update")


class GameError(Exception):
    """A game.json is invalid; needs a human to fix it."""


@dataclass
class Quote:
    currency: str
    daily: list[tuple[dt.date, float]]  # closing prices, ascending
    hourly: list[tuple[dt.datetime, dt.date, float]]  # (UTC time, exchange-local date, close)
    dividends: list[tuple[dt.date, float]]  # (ex-date, amount per share)


# --- fetching ---------------------------------------------------------------


def fetch_quote(ticker: str, since: dt.date, until: dt.date) -> Quote:
    """Prices from Yahoo Finance for [since, until]. Splits are already applied by Yahoo."""
    import yfinance as yf

    tk = yf.Ticker(ticker)
    end = until + dt.timedelta(days=1)
    daily = tk.history(start=since, end=end, interval="1d", auto_adjust=False, actions=True)
    if daily.empty:
        raise LookupError(f"no daily prices for {ticker}")
    currency = tk.history_metadata.get("currency")
    if not currency:
        raise LookupError(f"no currency for {ticker}")
    hourly = tk.history(start=since, end=end, interval="1h", auto_adjust=False)

    closes = daily["Close"].dropna()
    divs = daily["Dividends"][daily["Dividends"] > 0]
    return Quote(
        currency=currency,
        daily=[(ts.date(), float(v)) for ts, v in closes.items()],
        hourly=[
            (ts.tz_convert("UTC").to_pydatetime(), ts.date(), float(v))
            for ts, v in hourly["Close"].dropna().items()
        ],
        dividends=[(ts.date(), float(v)) for ts, v in divs.items()],
    )


def fetch_fx(currency: str, since: dt.date, until: dt.date) -> list[tuple[dt.date, float]]:
    """ECB reference rates (EUR per unit of `currency`) from the Frankfurter API."""
    url = FX_URL.format(since=since.isoformat(), until=until.isoformat(), currency=currency)
    # The API rejects Python's default User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": f"stox/{SCRIPT_VERSION}"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        data = json.load(resp)
    rates = sorted((dt.date.fromisoformat(d), r["EUR"]) for d, r in data["rates"].items())
    if not rates:
        raise LookupError(f"no EUR rates for {currency}")
    return rates


# --- pure computation -------------------------------------------------------


def last_on_or_before(points, day):
    """Last (date, value) with date <= day, or None. `points` must be ascending."""
    found = None
    for d, v in points:
        if d > day:
            break
        found = (d, v)
    return found


def last_before(points, day):
    return last_on_or_before(points, day - dt.timedelta(days=1))


def eur_rate(rates, day: dt.date) -> float:
    if rates is None:
        return 1.0
    found = last_on_or_before(rates, day)
    if found is None:
        raise LookupError(f"no EUR rate on or before {day}")
    return found[1]


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_pick(quote: Quote, rates, game: dict, now: dt.datetime, final: bool) -> dict:
    """EUR value series of one pick.

    The reference is the close of the last trading day before the start. Dividends
    with an ex-date during the game count as cash. With `final`, the series ends on
    the official close of the last trading day on or before the end.
    """
    start, end = game["start"], game["end"]
    invested = game["invested_eur"]
    ref = last_before(quote.daily, start)
    if ref is None:
        raise LookupError(f"no closing price before {start}")
    ref_day, p0 = ref
    fx0 = eur_rate(rates, ref_day)
    divs = [(d, a) for d, a in quote.dividends if start <= d <= end]

    def value(day, price):
        cash = sum(a for d, a in divs if d <= day)
        return round(invested * (price + cash) * eur_rate(rates, day) / (p0 * fx0), 4)

    start_ts = dt.datetime.combine(start, dt.time(), dt.timezone.utc)
    series = [[iso(start_ts), float(invested)]]
    last_day = None
    for ts, day, close in quote.hourly:
        if start <= day <= end and ts <= now:
            series.append([iso(ts), value(day, close)])
            last_day = day

    if final:
        close_day, close = last_on_or_before(quote.daily, end)
        final_value = value(close_day, close) if close_day >= start else float(invested)
        if close_day >= start and last_day == close_day:
            series[-1][1] = final_value
        elif close_day >= start:
            series.append([iso(dt.datetime.combine(close_day, CLOSE_FALLBACK_UTC, dt.timezone.utc)), final_value])

    return {
        "currency": quote.currency,
        "reference": {"date": ref_day.isoformat(), "close": p0, "eur_rate": fx0},
        "dividends": [[d.isoformat(), a] for d, a in divs],
        "value": series[-1][1],
        "series": series,
    }


def rank(values: dict[str, float]) -> tuple[list[dict], list[str]]:
    """Standings from best to worst, and the losers (everyone tied for last, to the cent)."""
    standings = sorted(values.items(), key=lambda kv: (-round(kv[1], 2), kv[0]))
    worst = min(round(v, 2) for v in values.values())
    losers = sorted(p for p, v in values.items() if round(v, 2) == worst)
    return [{"player": p, "value": round(v, 2)} for p, v in standings], losers


def game_status(game: dict, closed: bool, today: dt.date) -> str:
    if closed:
        return "closed"
    if today < game["start"]:
        return "upcoming"
    if today > game["end"]:
        return "finished"
    return "running"


# --- files ------------------------------------------------------------------


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def load_game(folder: Path) -> dict:
    """Read and validate game.json; dates are returned as date objects."""
    try:
        raw = read_json(folder / "game.json")
        game = dict(raw)
        game["start"] = dt.date.fromisoformat(raw["start"])
        game["end"] = dt.date.fromisoformat(raw["end"])
        picks = raw["picks"]
        invested = raw["invested_eur"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise GameError(f"{folder.name}: cannot read game.json: {e!r}") from e
    if raw.get("id") != folder.name:
        raise GameError(f"{folder.name}: id {raw.get('id')!r} does not match the folder name")
    if game["start"] > game["end"]:
        raise GameError(f"{folder.name}: start is after end")
    if not isinstance(invested, (int, float)) or invested <= 0:
        raise GameError(f"{folder.name}: invested_eur must be a positive number")
    if not picks or any(not p.get("player") or not p.get("ticker") for p in picks):
        raise GameError(f"{folder.name}: every pick needs a player and a ticker")
    if len({p["player"] for p in picks}) != len(picks):
        raise GameError(f"{folder.name}: a player appears twice")
    return game


def checksum(folder: Path) -> str:
    h = hashlib.sha256()
    for name in GAME_FILES:
        h.update(name.encode() + b"\0" + (folder / name).read_bytes() + b"\0")
    return h.hexdigest()


def update_game(folder: Path, game: dict, now: dt.datetime, get_quote=fetch_quote, get_fx=fetch_fx) -> str:
    """Refresh prices.json of a running or finished game; close it if finished. Returns the status."""
    today = now.date()
    status = game_status(game, (folder / "result.json").exists(), today)
    if status in ("closed", "upcoming"):
        return status

    final = status == "finished"
    since = game["start"] - dt.timedelta(days=14)  # room for the reference close before a holiday
    until = min(game["end"], today)
    prices_path = folder / "prices.json"
    old = read_json(prices_path)["picks"] if prices_path.exists() else {}
    fx_cache = {}
    picks, failed = {}, []
    for pick in game["picks"]:
        player, ticker = pick["player"], pick["ticker"]
        try:
            quote = get_quote(ticker, since, until)
            currency = MINOR_CURRENCIES.get(quote.currency, quote.currency)
            if currency not in fx_cache:
                fx_cache[currency] = None if currency == "EUR" else get_fx(currency, since, until)
            picks[player] = {"ticker": ticker, **compute_pick(quote, fx_cache[currency], game, now, final)}
        except Exception as e:  # keep the last good data, try again next run
            log.warning("%s: %s (%s): %r", game["id"], player, ticker, e)
            failed.append(player)
            if player in old:
                picks[player] = old[player]

    write_json(prices_path, {"updated": iso(now), "picks": picks})
    if not final:
        return status
    if failed:
        log.warning("%s: not closing yet, missing data for %s", game["id"], ", ".join(failed))
        return status

    standings, losers = rank({p: d["value"] for p, d in picks.items()})
    write_json(folder / "result.json", {
        "id": game["id"],
        "closed_at": iso(now),
        "script_version": SCRIPT_VERSION,
        "standings": standings,
        "losers": losers,
    })
    log.info("%s: closed, %s pays", game["id"], " and ".join(losers))
    return "closed"


def index_entry(folder: Path, game: dict, status: str) -> dict:
    entry = {
        "id": game["id"],
        "start": game["start"].isoformat(),
        "end": game["end"].isoformat(),
        "status": status,
        "invested_eur": game["invested_eur"],
        "picks": [{k: p[k] for k in ("player", "ticker", "name") if k in p} for p in game["picks"]],
    }
    if status == "closed":
        result = read_json(folder / "result.json")
        entry.update(standings=result["standings"], losers=result["losers"], checksum=checksum(folder))
    elif (folder / "prices.json").exists():
        prices = read_json(folder / "prices.json")
        values = {p: d["value"] for p, d in prices["picks"].items()}
        if values:
            entry["standings"], entry["last"] = rank(values)
        entry["updated"] = prices["updated"]
    return entry


def game_folders(games_dir: Path) -> list[Path]:
    return sorted(
        (p for p in games_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))),
        key=lambda p: p.name,
        reverse=True,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=Path, default=ROOT / "games", help="games directory")
    parser.add_argument("--now", type=dt.datetime.fromisoformat, help="pretend it is this time (UTC)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    now = args.now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    args.games.mkdir(exist_ok=True)

    errors, index = 0, []
    for folder in game_folders(args.games):
        try:
            game = load_game(folder)
        except GameError as e:
            log.error("%s", e)
            errors += 1
            continue
        status = update_game(folder, game, now)
        index.append(index_entry(folder, game, status))
    write_json(args.games / "index.json", {"games": index})
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
