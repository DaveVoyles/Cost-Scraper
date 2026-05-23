"""IsThereAnyDeal v2 API client.

Docs: https://docs.isthereanydeal.com/

We use the "app key" auth model (no OAuth needed for read-only data):
  - POST /games/lookup/v1?key=...    resolve titles -> ITAD game ids
  - POST /games/prices/v3?key=...    current best price across stores
  - POST /games/storelow/v2?key=...  historical low per game

All responses cached on disk by call signature so reruns are free.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

BASE = "https://api.isthereanydeal.com"
WEB_BASE = "https://isthereanydeal.com"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


@dataclass
class ITADBest:
    price: Optional[float]
    store: Optional[str]
    url: Optional[str]
    cut: Optional[int]

    def as_dict(self) -> dict:
        return {"price": self.price, "store": self.store, "url": self.url, "cut": self.cut}


@dataclass
class ITADLow:
    price: Optional[float]
    store: Optional[str]
    date: Optional[str]

    def as_dict(self) -> dict:
        return {"price": self.price, "store": self.store, "date": self.date}


@dataclass
class ITADResult:
    game_id: Optional[str]
    slug: Optional[str]
    title: Optional[str]
    url: Optional[str]
    best_now: Optional[ITADBest]
    historical_low: Optional[ITADLow]

    def as_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "slug": self.slug,
            "title": self.title,
            "url": self.url,
            "best_now": self.best_now.as_dict() if self.best_now else None,
            "historical_low": self.historical_low.as_dict() if self.historical_low else None,
        }


class ITADClient:
    def __init__(self, app_id: str, cache_dir: Path, country: str = "US", currency: str = "USD"):
        self.key = app_id
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.country = country
        self.currency = currency
        self._session = requests.Session()
        self._session.headers["User-Agent"] = UA
        self._session.headers["Content-Type"] = "application/json"
        self._slug_cache = self._load_slug_cache()

    def _cache_path(self, key: str) -> Path:
        slug = hashlib.md5(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{slug}.json"

    # Endpoints that don't need an API key. Keep the set small and explicit.
    PUBLIC_PATHS = {"/lookup/id/title/v1"}

    def _post(self, path: str, params: dict, body, cache_key: str):
        cf = self._cache_path(cache_key)
        if cf.exists():
            return json.loads(cf.read_text(encoding="utf-8"))
        url = BASE + path
        send_params = dict(params)
        needs_key = path not in self.PUBLIC_PATHS
        if needs_key:
            if not self.key or self.key.startswith("PASTE_"):
                return None
            send_params["key"] = self.key
        for attempt in range(4):
            r = self._session.post(url, params=send_params, json=body, timeout=30)
            if r.status_code == 200:
                data = r.json()
                cf.write_text(json.dumps(data), encoding="utf-8")
                return data
            if r.status_code in (429, 503):
                time.sleep(2 ** attempt)
                continue
            # 401/403 → silently skip; user may not have set the key yet.
            if r.status_code in (401, 403):
                print(f"  !! ITAD {r.status_code} ({path}) — {r.text[:160]}")
                return None
            print(f"  !! ITAD {r.status_code} ({path}) — {r.text[:160]}")
            return None
        return None

    def lookup_titles(self, titles: list[str]) -> dict[str, str]:
        """Resolve a batch of titles to ITAD game ids. Returns {title: id}.

        Uses /lookup/games/id/by-title/v1 which accepts a JSON array of titles
        and returns a flat {title: id_or_null} map.
        """
        if not titles:
            return {}
        batch_key = hashlib.md5("|".join(sorted(titles)).encode()).hexdigest()[:16]
        body = titles
        data = self._post("/lookup/id/title/v1", {}, body, f"lookup:{batch_key}")
        out: dict[str, str] = {}
        if not isinstance(data, dict):
            return out
        for title, gid in data.items():
            if gid:
                out[title] = gid
        return out

    def info(self, game_ids: list[str]) -> dict[str, dict]:
        """DEPRECATED — /games/info/v2 requires an API key we don't have.

        Kept as a no-op stub so older callers don't crash. Use
        ``resolve_slug()`` for URL-building instead.
        """
        return {}

    # ----- slug resolution (no-API-key, HTML probe) -----
    #
    # ITAD's SPA only renders game pages for slug-based URLs. The slug-resolving
    # API endpoints (/games/info/v2, /games/lookup/v1, /games/search/v1) all
    # require a paid API key. As a workaround we slugify the title locally,
    # fetch /game/<slug>/info/, and verify it actually rendered the right game
    # by looking for an ``og:title`` meta tag (the broken UUID/missing-slug
    # variant returns a generic ~12KB SPA shell with no ``og:`` metadata).
    #
    # Results are cached in ``slug_cache.json`` so subsequent runs make zero
    # network calls. The cache is keyed by game_id.
    _SLUG_CACHE_NAME = "slug_cache.json"

    def _slug_cache_path(self) -> Path:
        return self.cache_dir / self._SLUG_CACHE_NAME

    def _load_slug_cache(self) -> dict:
        p = self._slug_cache_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_slug_cache(self, cache: dict) -> None:
        self._slug_cache_path().write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )

    @staticmethod
    def _slug_candidates(title: str) -> list[str]:
        """Generate ITAD-style slug candidates for a title, in priority order."""
        if not title:
            return []
        # Normalize unicode -> ascii, strip accents.
        norm = unicodedata.normalize("NFKD", title)
        norm = norm.encode("ascii", "ignore").decode("ascii")
        norm = norm.lower()
        # ITAD spells "&" as "and" in slugs (e.g. "monsters and dragons").
        norm = norm.replace("&", " and ")
        # Drop characters that ITAD removes outright rather than turning into
        # hyphens (apostrophes, commas inside numbers like "40,000" -> "40000",
        # periods, trademark glyphs).
        for ch in ("'", "\u2019", ",", ".", "\u2122", "\u00ae", "\u00a9"):
            norm = norm.replace(ch, "")
        # Replace any remaining non-alnum run with a single hyphen.
        slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
        slug = re.sub(r"-+", "-", slug)
        if not slug:
            return []
        candidates = [slug]
        # Fallback variants — try without leading "the-" or trailing edition
        # qualifiers, and without subtitle (everything after a colon/dash).
        if slug.startswith("the-"):
            candidates.append(slug[4:])
        for sep in (":", " - "):
            head = title.split(sep, 1)[0].strip()
            if head and head != title:
                alt = ITADClient._slug_candidates(head)
                if alt and alt[0] not in candidates:
                    candidates.append(alt[0])
        return candidates

    def _verify_slug(self, slug: str) -> bool:
        """Return True if /game/<slug>/info/ renders an actual game page.

        ITAD returns HTTP 200 for both real and missing slugs (it's an SPA),
        so we have to look at the body. Real game pages include an ``og:title``
        meta tag; the empty SPA shell does not.
        """
        url = f"{WEB_BASE}/game/{slug}/info/"
        try:
            # Use a clean request — session has Content-Type: application/json
            # which some CDNs reject on GETs.
            r = requests.get(url, timeout=20, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
            })
        except requests.RequestException:
            return False
        if r.status_code != 200:
            return False
        body = r.text or ""
        return "og:title" in body

    def resolve_slug(self, game_id: str, title: str) -> tuple[Optional[str], str]:
        """Resolve (slug, url) for an ITAD game.

        Returns the best URL we can construct: a verified slug URL when
        available, otherwise the UUID URL (still 200 OK, but renders an
        empty shell — used purely as a fallback).
        """
        cache = self._slug_cache
        entry = cache.get(game_id)
        if entry and entry.get("checked"):
            slug = entry.get("slug")
            if slug:
                return slug, f"{WEB_BASE}/game/{slug}/info/"
            return None, f"{WEB_BASE}/game/{game_id}/info/"
        # Probe candidates.
        for cand in self._slug_candidates(title):
            time.sleep(0.2)  # be polite
            if self._verify_slug(cand):
                cache[game_id] = {"slug": cand, "title": title, "checked": True}
                self._save_slug_cache(cache)
                return cand, f"{WEB_BASE}/game/{cand}/info/"
        # No candidate verified — record the miss so we don't re-probe next run.
        cache[game_id] = {"slug": None, "title": title, "checked": True}
        self._save_slug_cache(cache)
        return None, f"{WEB_BASE}/game/{game_id}/info/"

    def prices(self, game_ids: list[str]) -> dict[str, ITADBest]:
        """Return {game_id: best_current_price}. Skips unknown ids."""
        if not game_ids:
            return {}
        batch_key = hashlib.md5("|".join(sorted(game_ids)).encode()).hexdigest()[:16] + f":{self.country}"
        body = game_ids
        data = self._post("/games/prices/v3",
                          {"country": self.country, "deals": "true"},
                          body, f"prices:{batch_key}")
        out: dict[str, ITADBest] = {}
        if not data:
            return out
        for entry in data:
            gid = entry.get("id")
            deals = entry.get("deals") or []
            if not gid or not deals:
                continue
            # deals are sorted by price ascending in v3 by default; take first.
            best = deals[0]
            price = ((best.get("price") or {}).get("amount"))
            cut = best.get("cut")
            shop = (best.get("shop") or {}).get("name")
            url = best.get("url")
            out[gid] = ITADBest(price=price, store=shop, url=url, cut=cut)
        return out

    def lows(self, game_ids: list[str]) -> dict[str, ITADLow]:
        if not game_ids:
            return {}
        batch_key = hashlib.md5("|".join(sorted(game_ids)).encode()).hexdigest()[:16] + f":{self.country}"
        body = game_ids
        data = self._post("/games/storelow/v2",
                          {"country": self.country},
                          body, f"storelow:{batch_key}")
        out: dict[str, ITADLow] = {}
        if not data:
            return out
        for entry in data:
            gid = entry.get("id")
            lows = entry.get("lows") or []
            if not gid or not lows:
                continue
            # Find absolute minimum across all stores
            best = None
            for row in lows:
                price = ((row.get("price") or {}).get("amount"))
                if price is None:
                    continue
                if best is None or price < best[0]:
                    best = (price, row)
            if best:
                price, row = best
                shop = (row.get("shop") or {}).get("name")
                date = row.get("added")
                out[gid] = ITADLow(price=price, store=shop, date=date)
        return out

    def enrich(self, titles: list[str]) -> dict[str, ITADResult]:
        """Convenience: titles -> ITADResult for each. Single batched call set."""
        id_map = self.lookup_titles(titles)
        ids = list(id_map.values())
        price_map = self.prices(ids)
        low_map = self.lows(ids)
        out: dict[str, ITADResult] = {}
        for title in titles:
            gid = id_map.get(title)
            if not gid:
                out[title] = ITADResult(game_id=None, slug=None, title=title,
                                        url=None, best_now=None, historical_low=None)
                continue
            best = price_map.get(gid)
            low = low_map.get(gid)
            slug, url = self.resolve_slug(gid, title)
            out[title] = ITADResult(
                game_id=gid,
                slug=slug,
                title=title,
                url=url,
                best_now=best,
                historical_low=low,
            )
        return out
