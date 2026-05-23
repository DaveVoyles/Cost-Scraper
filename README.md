# Cost Scraper

A small toolkit that pulls your Playnite library, looks up each title across
multiple PC-game storefronts and price aggregators, and renders a single
sortable HTML view of the cheapest current prices, historical lows, and what
you already own.

> Live site (after `git push`):
> **https://davevoyles.github.io/Cost-Scraper/**

## Features

- **Playnite library** as the source of truth for "what do I want to track"
- **loaded.com** product/price lookup (Algolia)
- **Steam** AppID + MSRP lookup, and *owned-on-Steam* detection via local
  `appmanifest_*.acf` files
- **IsThereAnyDeal (ITAD)** aggregator — current best price across ~40 stores
  and historical low per game
- **Per-title price alerts** (`pipeline/user_alerts.json` — local, not
  committed) flag any game that hits your target price
- **Run-over-run diff** so you can see what dropped since your last refresh
- **Sortable / filterable HTML frontend** (Tabulator + Alpine, no build step)

## Quick start

```powershell
# 1. clone
git clone https://github.com/DaveVoyles/Cost-Scraper.git
cd Cost-Scraper

# 2. set up Python deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r pipeline\requirements.txt

# 3. configure (creds + paths — NEVER committed)
copy pipeline\config.example.json pipeline\config.local.json
# edit pipeline\config.local.json and add:
#   - itad_app_id (free, https://isthereanydeal.com/dev/app/)
#   - playnite_db_dir   (default: %APPDATA%\Playnite\library)
#   - steam_library_dirs (default auto-detected from libraryfolders.vdf)

# 4. close Playnite (it locks games.db), then run the pipeline
python pipeline\build_data.py

# 5. open docs\index.html in your browser, or commit + push and view on Pages
```

## Repo layout

```
pipeline/
├── build_data.py            orchestrator — run this
├── config.example.json      committed template
├── config.local.json        YOUR secrets/paths (gitignored)
├── requirements.txt
├── sources/
│   ├── playnite.py          LiteDB extract
│   ├── loaded.py            loaded.com Algolia client
│   ├── steam.py             storesearch + appmanifest reader
│   └── itad.py              IsThereAnyDeal v2 API client
├── alerts.py                applies user_alerts.json
├── diff.py                  builds data/changes.json
├── matching.py              fuzzy title normalization (shared)
└── cache/                   per-source on-disk caches (gitignored)

docs/                        GitHub Pages root
├── index.html               main table
├── changes.html             diff view
├── app.js                   Tabulator + Alpine logic
├── styles.css
├── lib/                     vendored Tabulator + Alpine (offline-safe)
└── data/                    JSON produced by the pipeline
    ├── games.json           current snapshot (committed)
    ├── meta.json            last_run + source versions (committed)
    ├── changes.json         run-over-run diff (committed)
    ├── history/             timestamped game.json snapshots (committed)
    └── playnite_games.json  raw Playnite dump (gitignored — may contain
                             unreleased / NDA titles)
```

## Privacy

This repo is public, so:
- Credentials (ITAD app id, client secret) live in `pipeline/config.local.json`
  which is gitignored.
- Your raw Playnite dump lives in `data/playnite_games.json` which is also
  gitignored (it can include hidden / pre-release titles you may not want
  public).
- The committed `data/games.json` is the *processed* view and it's fine to
  share, but if you'd rather keep the whole `data/` folder private, fork to a
  private repo (GitHub Pages still works on private with a Pro subscription).
