# Stox 🍕

Every quarter, each of us invests 50 EUR in one stock. Whoever has the lowest value
at the end of the quarter pays for pizza and drinks. This repo fetches the prices
four times a day and publishes a chart on GitHub Pages. Finished games are frozen
and stay browsable. See [PLAN.md](PLAN.md) for the design.

## Rules

1. **Start price:** closing price of the last trading day before `start`.
2. **End price:** closing price of the last trading day on or before `end`.
3. **Currency:** everything in EUR, including exchange-rate changes (ECB reference rates).
4. **Splits** are accounted for; **dividends** with an ex-date during the game count as cash.
5. **Tie** for last place (to the cent): the tied players split the bill.
6. **Data source:** Yahoo Finance's data counts, even if it's slightly off.

## Starting a new game

Create `games/<id>/game.json`, e.g. in the GitHub web UI (*Add file → Create new file*).
The folder name must equal `id`. Tickers are Yahoo Finance symbols: look the stock up on
[finance.yahoo.com](https://finance.yahoo.com) (e.g. `SAP.DE`, `ASML.AS`, `MC.PA`, `NVDA`).

```json
{
  "id": "2027-Q1",
  "start": "2027-01-01",
  "end": "2027-03-31",
  "invested_eur": 50,
  "picks": [
    {"player": "Alice", "ticker": "SAP.DE", "name": "SAP"},
    {"player": "Bob", "ticker": "NVDA", "name": "Nvidia"}
  ]
}
```

Committing it starts the update workflow. Prices appear from the start date on.
Keep player names identical across games: the hall of shame and the colors go by name.

## How a game ends

On the first run after `end`, `scripts/update.py` writes `games/<id>/result.json`
and the workflow tags the commit as `game/<id>`. From then on the game is frozen:

- `update.py` never writes to a folder that has a `result.json`.
- `check_frozen.py` fails the workflow if a closed game differs from its tag or
  from the previous commit, so nothing is deployed.
- The *Checks* workflow shows a red ❌ on any push or PR that touches a closed game.

If data is missing for a player on that day, closing waits for the next run.

## One-time setup on GitHub

1. Create a **public** repository and push this folder.
2. *Settings → Pages → Build and deployment → Source:* **GitHub Actions**.
3. *Settings → Actions → General → Workflow permissions:* **Read and write**.
4. *Settings → Rules → Rulesets*:
   - Branch ruleset for `main`: block force pushes and deletion.
   - Tag ruleset for `game/*`: restrict updates and deletions.
5. Optional custom domain: *Settings → Pages → Custom domain*, plus a `CNAME` DNS
   record pointing to `<user>.github.io`.
6. *Actions → Update prices → Run workflow* to run it once by hand.

## Local development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/update.py --games /tmp/stox-games    # try it on a copy
.venv/bin/python scripts/update.py --now 2026-12-15T12:00:00Z # pretend it's another time
```

To view the site, put `site/` and `games/` together and serve them:

```sh
mkdir -p _site && cp -r site/. _site/ && cp -r games _site/ && python3 -m http.server -d _site
```
