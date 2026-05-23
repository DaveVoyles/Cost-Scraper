"""Apply user_alerts.json to game records and mark hits."""
from __future__ import annotations
from rapidfuzz import fuzz


def apply_alerts(games: list[dict], alerts: list[dict]) -> None:
    """Mutate `games` in place, setting alert_hit + alert_target for matches."""
    if not alerts:
        for g in games:
            g["alert_hit"] = False
            g["alert_target"] = None
        return
    norm_alerts = []
    for a in alerts:
        title = (a.get("title") or "").strip()
        target = a.get("target")
        if not title or target is None:
            continue
        norm_alerts.append((title.lower(), float(target), a.get("note", "")))
    for g in games:
        g_title = (g.get("title") or "").lower()
        hit = False
        target = None
        note = None
        for at, tv, tn in norm_alerts:
            score = fuzz.token_set_ratio(at, g_title)
            if score < 85:
                continue
            best = g.get("best_price", {}).get("value")
            if best is None:
                continue
            try:
                if float(best) <= tv:
                    hit = True
                    if target is None or tv < target:
                        target = tv
                        note = tn
            except (TypeError, ValueError):
                continue
        g["alert_hit"] = hit
        g["alert_target"] = target
        g["alert_note"] = note
