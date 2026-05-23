"""Cost Scraper — main orchestrator.

Run with:   python -m pipeline.build_data        (from repo root)
        or  python pipeline/build_data.py
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python pipeline/build_data.py` by tweaking sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config, load_user_alerts, ensure_dirs, DATA_DIR, CACHE_DIR
from pipeline.sources import playnite as src_playnite
from pipeline.sources.loaded import LoadedClient
from pipeline.sources.steam import SteamClient, installed_appids
from pipeline.sources.itad import ITADClient
from pipeline.alerts import apply_alerts
from pipeline.diff import build_diff
from pipeline.matching import is_full_game, EDITION_ORDER

OVERRIDE_NOT_FOUND = {
    "Jackal",          # actual = https://store.steampowered.com/app/3124230/Jackal/
    "Fear Effect",     # wrongly matches Fear Effect 2: Retro Helix
    "Hunter's Moon",   # wrongly matches Moon Hunters
    "The Last Faith",  # wrongly matches SpellForce 2 Faith in Destiny
}


def _pick_best_loaded_edition(eds: list[dict]):
    """Cheapest in-stock full-game edition among loaded.com results, if any.

    Out-of-stock listings (``in_stock is False``) are excluded so the "best
    price" column never advertises something the user can't actually buy.
    Editions with unknown stock (``None``) are kept eligible — loaded.com
    occasionally omits the field and we'd rather show a maybe-buyable price
    than nothing.
    """
    full = [
        e for e in eds
        if is_full_game(e["edition"])
        and e["price"] is not None
        and e.get("in_stock") is not False
    ]
    if not full:
        return None
    full.sort(key=lambda e: e["price"])
    return full[0]


def _compute_best_price(g: dict) -> dict:
    """Pick cheapest across {loaded full game, ITAD best now}. Steam too if it has a final price."""
    candidates = []
    le = _pick_best_loaded_edition(g.get("loaded", {}).get("editions", []))
    if le:
        candidates.append({"value": le["price"], "source": "loaded.com",
                           "url": le["url"], "detail": le["edition"]})
    itad = g.get("itad") or {}
    bn = itad.get("best_now")
    if bn and bn.get("price") is not None:
        candidates.append({"value": bn["price"], "source": f"ITAD: {bn.get('store') or 'best'}",
                           "url": bn.get("url"), "detail": f"cut {bn.get('cut') or 0}%"})
    s = g.get("steam") or {}
    if s.get("price_now") is not None:
        candidates.append({"value": s["price_now"], "source": "Steam",
                           "url": s.get("url"), "detail": "current"})
    if not candidates:
        return {"value": None, "source": None, "url": None, "detail": None}
    candidates.sort(key=lambda c: c["value"])
    best = candidates[0]
    # discount_pct based on MSRP from Steam (most authoritative) or loaded msrp.
    msrp = s.get("msrp")
    if msrp is None and le:
        msrp = le.get("msrp")
    if msrp and best["value"] is not None and msrp > best["value"]:
        best["discount_pct"] = round((1 - best["value"] / msrp) * 100)
    else:
        best["discount_pct"] = None
    best["msrp"] = msrp
    return best


def main():
    cfg = load_config()
    ensure_dirs()
    print(f"== Cost Scraper @ {datetime.now().isoformat(timespec='seconds')} ==")

    # --- 1. Playnite library ---
    print("[1/6] Extracting Playnite library...")
    playnite_dump = DATA_DIR / "playnite_games.json"
    playnite_games = src_playnite.extract(cfg["playnite_db_dir"], playnite_dump)
    titles = [g["Name"] for g in playnite_games]

    # --- 2. loaded.com ---
    print(f"[2/6] Looking up {len(titles)} titles on loaded.com...")
    loaded_client = LoadedClient(CACHE_DIR / "loaded")
    loaded_by_title: dict[str, list] = {}
    for i, t in enumerate(titles, 1):
        if t in OVERRIDE_NOT_FOUND:
            loaded_by_title[t] = []
            continue
        eds = loaded_client.lookup(t)
        loaded_by_title[t] = [e.as_dict() for e in eds]
        if i % 25 == 0 or i == len(titles):
            print(f"      {i}/{len(titles)}  ({sum(1 for v in loaded_by_title.values() if v)} matched)")

    # --- 3. Steam ---
    print(f"[3/6] Looking up Steam AppIDs + MSRPs ({len(titles)} titles)...")
    steam_client = SteamClient(CACHE_DIR / "steam")
    steam_by_title: dict[str, dict] = {}
    for i, t in enumerate(titles, 1):
        m = steam_client.search_appid(t)
        steam_by_title[t] = m.as_dict() if m else {}
        if i % 25 == 0 or i == len(titles):
            print(f"      {i}/{len(titles)}  ({sum(1 for v in steam_by_title.values() if v)} matched)")

    print("[3b] Detecting installed Steam appids...")
    installed = installed_appids(cfg.get("steam_root"))
    print(f"      {len(installed)} games installed on disk")
    for t, s in steam_by_title.items():
        if s:
            s["installed"] = s.get("appid") in installed

    # --- 4. ITAD ---
    print(f"[4/6] Querying IsThereAnyDeal aggregator (batched)...")
    itad_client = ITADClient(cfg.get("itad_app_id", ""), CACHE_DIR / "itad")
    # Batch of 200 at a time to be polite.
    itad_by_title: dict[str, dict] = {}
    B = 200
    for i in range(0, len(titles), B):
        chunk = titles[i:i + B]
        results = itad_client.enrich(chunk)
        for t, r in results.items():
            itad_by_title[t] = r.as_dict()
        print(f"      {min(i+B, len(titles))}/{len(titles)}  "
              f"({sum(1 for v in itad_by_title.values() if v.get('game_id'))} resolved)")

    # --- 5. Assemble + alerts + best-price ---
    print("[5/6] Assembling game records...")
    now = datetime.now(timezone.utc).isoformat()
    games: list[dict] = []
    for g in playnite_games:
        title = g["Name"]
        eds = loaded_by_title.get(title, [])
        dlc_count = sum(1 for e in eds if e["edition"] in ("DLC", "Bundle", "Soundtrack", "Upgrade"))
        rec = {
            "title": title,
            "playnite_platform": g.get("Platforms", ""),
            "playnite_source": g.get("Source"),
            "playnite_installed": g.get("IsInstalled", False),
            "loaded": {
                "editions": sorted(eds, key=lambda e: (EDITION_ORDER.get(e["edition"], 99),
                                                       e["price"] if e["price"] is not None else 99999)),
                "checked_at": loaded_client.cache_age(title),
            },
            "steam": steam_by_title.get(title) or {},
            "itad": itad_by_title.get(title) or {},
            "dlc_count": dlc_count,
        }
        rec["best_price"] = _compute_best_price(rec)
        rec["discount_pct"] = rec["best_price"].get("discount_pct")
        games.append(rec)

    print("[5b] Applying price alerts...")
    apply_alerts(games, load_user_alerts())
    n_alerts = sum(1 for g in games if g.get("alert_hit"))
    print(f"      {n_alerts} alert(s) hit")

    # --- 6. Persist snapshot + diff ---
    print("[6/6] Writing data/ snapshots...")
    payload = {
        "generated_at": now,
        "currency": "USD",
        "country": "US",
        "totals": {
            "playnite_titles": len(games),
            "matched_loaded": sum(1 for g in games if g["loaded"]["editions"]),
            "matched_steam": sum(1 for g in games if g["steam"]),
            "matched_itad": sum(1 for g in games if g["itad"].get("game_id")),
            "installed_on_steam": sum(1 for g in games if g["steam"].get("installed")),
            "alerts_hit": n_alerts,
        },
        "games": games,
    }
    out = DATA_DIR / "games.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"      wrote {out} ({len(games)} games)")

    # Snapshot today's run into history (idempotent within a day)
    today = datetime.now().strftime("%Y-%m-%d")
    hist = DATA_DIR / "history" / f"{today}.json"
    shutil.copy2(out, hist)
    print(f"      snapshot -> {hist}")

    # Diff vs previous run
    changes_path = DATA_DIR / "changes.json"
    diff = build_diff(games, DATA_DIR / "history", changes_path, today)
    n_diff = sum(1 for c in diff["changes"] if c.get("delta") is not None)
    print(f"      diff: {n_diff} title(s) with comparable prior price -> {changes_path}")

    # Meta file (for frontend display)
    meta = {
        "generated_at": now,
        "totals": payload["totals"],
        "previous_snapshot": diff.get("previous_snapshot"),
    }
    (DATA_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Done.")


if __name__ == "__main__":
    main()
