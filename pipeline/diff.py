"""Run-over-run diff. Compares current snapshot vs the most recent history file
older than today, emitting data/changes.json with per-title price deltas."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


def _index(games: list[dict]) -> dict[str, dict]:
    return {g["title"]: g for g in games if g.get("title")}


def _best_price(g: dict):
    bp = g.get("best_price") or {}
    return bp.get("value")


def build_diff(current_games: list[dict], history_dir: Path, out_path: Path,
               current_date: str) -> dict:
    """Pick the newest history file dated before `current_date` and diff."""
    history_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted([p for p in history_dir.glob("*.json")
                         if p.stem != current_date])
    prev_path = candidates[-1] if candidates else None
    prev_games: list[dict] = []
    if prev_path and prev_path.exists():
        try:
            prev_games = json.loads(prev_path.read_text(encoding="utf-8")).get("games", [])
        except Exception:
            prev_games = []
    cur_idx = _index(current_games)
    prev_idx = _index(prev_games)
    rows = []
    for title, g in cur_idx.items():
        cur = _best_price(g)
        prev = _best_price(prev_idx.get(title, {}))
        if cur is None and prev is None:
            continue
        delta = None
        pct = None
        if cur is not None and prev is not None:
            delta = round(cur - prev, 2)
            if prev > 0:
                pct = round((cur - prev) / prev * 100, 1)
        rows.append({
            "title": title,
            "price_now": cur,
            "price_prev": prev,
            "delta": delta,
            "delta_pct": pct,
            "source_now": (g.get("best_price") or {}).get("source"),
            "url_now": (g.get("best_price") or {}).get("url"),
            "historical_low": (g.get("itad") or {}).get("historical_low"),
        })
    rows.sort(key=lambda r: (r["delta"] if r["delta"] is not None else 0))
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "previous_snapshot": prev_path.name if prev_path else None,
        "changes": rows,
    }
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
