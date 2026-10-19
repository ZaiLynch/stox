# Plan: Stock-picking game tracker

Every quarter, each of four friends invests 50 EUR in one stock. Whoever has the
lowest value at the end of the quarter pays for pizza and drinks. This app plots
each pick's value over time and keeps a browsable, unchangeable history of past games.

**Cost:** free. GitHub Actions has no minute limit for public repos, and GitHub
Pages on the free plan needs a public repo anyway.

---

## 1. Repository layout

```
stox/
├── games/
│   ├── index.json            # generated: list of all games plus a summary of each
│   ├── 2026-Q3/
│   │   ├── game.json         # hand-written: players, tickers, dates
│   │   ├── prices.json       # generated: time series of each player's EUR value
│   │   └── result.json       # generated once at close: ranking and loser → game is frozen
│   └── 2026-Q4/
│       ├── game.json
│       └── prices.json       # no result.json yet → game is still running
├── scripts/
│   ├── update.py             # fetches prices, closes finished games, rebuilds index.json
│   └── check_frozen.py       # fails if a closed game was changed
├── tests/                    # unit tests for the value, ranking and freezing logic
├── site/
│   ├── index.html            # one page: game picker, chart, table, hall of shame
│   ├── app.js
│   └── style.css
├── requirements.txt
└── .github/workflows/
    ├── update.yml            # cron 4×/day + on push: update, commit, tag, deploy Pages
    └── guard.yml             # on every push and PR: check_frozen.py
```

**Main rule:** a game counts as closed when its `result.json` exists. There's no
separate status field that could get out of sync with it.

## 2. Data model

**`game.json`** (written by hand, e.g. in the GitHub web UI):
```json
{
  "id": "2026-Q4",
  "start": "2026-10-01",
  "end": "2026-12-31",
  "invested_eur": 50,
  "picks": [
    {"player": "Alice", "ticker": "SAP.DE", "name": "SAP"},
    {"player": "Bob",   "ticker": "NVDA",   "name": "Nvidia"}
  ]
}
```

**`prices.json`** (generated): the reference price of each pick plus the time
series of EUR values.

**`result.json`** (generated once): final value of each player, ranking, loser(s),
closing time, script version.

**`index.json`** (generated): `[{id, start, end, status, players, last, ...}]`.
The frontend reads only this file to build the game picker.

## 3. `update.py` logic

For each game folder:

1. **Closed** (`result.json` exists): skip it. Never opened for writing.
2. **Upcoming** (today < `start`): skip it, list it as "upcoming".
3. **Running**: fetch hourly prices (yfinance, `auto_adjust=False`, split-adjusted
   by Yahoo) from `start` until now, plus EUR exchange rates from the Frankfurter
   (ECB) API. Rewrite `prices.json` completely, so a failed run fixes itself on the
   next one.
4. **Finished** (UTC date > `end`): do one last full update, write `result.json`.

Then rebuild `index.json`. If one ticker fails, keep the old data for that player
and log a warning; for a finished game, wait with closing until all tickers succeed.

## 4. Keeping past games unchanged

| Layer | What it does |
|---|---|
| **The script** | `update.py` never writes into a folder that has `result.json`. |
| **Check before commit** | `update.yml` runs `check_frozen.py` before committing and aborts on any change under a closed game. |
| **Guard workflow** | `guard.yml` runs the same check on every push and PR; a red ❌ shows on a commit that edits an old game. |
| **Git tag at close** | The workflow creates tag `game/<id>` when a game closes; `check_frozen.py` compares each closed game against its tag. |
| **Repository rulesets** | Block force-pushes and deletion on `main`; protect tags `game/*` from deletion/moving. |
| **Checksum** | `index.json` stores a SHA-256 of each closed game; the frontend shows it. |

## 5. Workflows

**`update.yml`**: cron `15 9,13,17,21 * * 1-5` (UTC; covers the EU close at
~17:30 CET and the US close at 22:00 CET), `workflow_dispatch`, and push to `main`.
`concurrency: update`. Steps: checkout (full history and tags) → Python →
`update.py` → `check_frozen.py` → commit/push if changed → tag newly closed games →
upload Pages artifact (`site/` + `games/`) → deploy Pages.

**`guard.yml`**: on `push` and `pull_request`, runs `check_frozen.py` against the
previous commit / PR base and against all `game/*` tags.

## 6. Frontend (static, no build step)

- **Game picker** filled from `index.json`; opens on the running game; link per game (`#2026-Q3`).
- **Chart** (Chart.js, pinned version from a CDN): one line per player, dashed line at 50 EUR, tooltips with EUR and %.
- **Table**: player, stock, value, %, 🍕 for last place / loser.
- **Status**: *running · updated 2 h ago*, *upcoming*, *closed · Bob paid*.
- **Hall of shame**: how often each player has paid, across all closed games.
- Works on phones, follows the system's dark mode, shows times in the viewer's time zone.

## 7. Game rules (also in README)

1. **Start price:** closing price of the last trading day **before** `start`
   (you "bought" at the close before the game began, so the reference is known
   on day 1).
2. **End price:** closing price of the last trading day on or before `end`.
3. **Currency:** everything in EUR, including exchange-rate changes (ECB reference rates).
4. **Splits** are accounted for; **dividends** with an ex-date during the game
   count as cash added to the value.
5. **Tie** for last place (to the cent): the tied players split the bill.
6. **Data source:** Yahoo Finance's data counts, even if it's slightly off.

## 8. Phases

| Phase | Work | Done when |
|---|---|---|
| **0. Setup** (you) | Public repo, Pages source = "GitHub Actions", rulesets, CNAME | Empty page loads on your domain |
| **1. Script** | `update.py` + unit tests, `--now` to simulate a date | Correct JSON for a test game locally |
| **2. Freezing** | `check_frozen.py` + tagging | Editing a closed game fails the check |
| **3. Workflows** | `update.yml`, `guard.yml` | A manual run commits data and deploys |
| **4. Frontend** | Picker, chart, table, hall of shame | All games browsable on phone and desktop |
| **5. Go live** (you) | Add the real `game.json`; optionally a past game | Cron runs on its own for a few days |
| **6. Optional** | Issue template "new game", notification on close | – |
