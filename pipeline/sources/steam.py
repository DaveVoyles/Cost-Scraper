"""Steam search + installed-game detection.

- `search_appid(title)` calls Steam's public `storesearch` endpoint to resolve a
  title to {appid, name, price_now}.
- `installed_appids(steam_root)` reads `steamapps/libraryfolders.vdf` to find all
  Steam library folders on the box, then parses every `appmanifest_*.acf` for
  appid + installdir.
- `app_msrp(appid)` calls `appdetails` for the canonical MSRP price.

All HTTP results are cached on disk by (endpoint, key).
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from rapidfuzz import fuzz

from ..matching import normalize_for_search, base_token_coverage

STORESEARCH = "https://store.steampowered.com/api/storesearch/"
APPDETAILS = "https://store.steampowered.com/api/appdetails"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


@dataclass
class SteamMatch:
    appid: int
    name: str
    url: str
    price_now: Optional[float]
    msrp: Optional[float]
    match_score: int

    def as_dict(self) -> dict:
        return {
            "appid": self.appid,
            "name": self.name,
            "url": self.url,
            "price_now": self.price_now,
            "msrp": self.msrp,
            "match_score": self.match_score,
        }


class SteamClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = UA

    def _cache_path(self, key: str) -> Path:
        slug = hashlib.md5(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{slug}.json"

    def _get(self, url: str, params: dict, cache_key: str) -> dict:
        cf = self._cache_path(cache_key)
        if cf.exists():
            return json.loads(cf.read_text(encoding="utf-8"))
        for attempt in range(4):
            r = self._session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                cf.write_text(json.dumps(data), encoding="utf-8")
                return data
            if r.status_code in (429, 503):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        return {}

    def search_appid(self, title: str) -> Optional[SteamMatch]:
        query = normalize_for_search(title)
        data = self._get(STORESEARCH, {"term": query, "cc": "us", "l": "en"},
                         f"search:{query.lower()}")
        items = data.get("items", []) or []
        best = None
        best_score = -1
        for it in items:
            name = it.get("name", "")
            score = int(fuzz.token_set_ratio(query.lower(), name.lower()))
            coverage = base_token_coverage(query, name)
            if score < 70 or coverage < 0.8:
                continue
            if score > best_score:
                best_score = score
                best = (it, score)
        if not best:
            return None
        it, score = best
        appid = it.get("id")
        if not appid:
            return None
        price = None
        if isinstance(it.get("price"), dict):
            # storesearch returns integer cents
            final = it["price"].get("final")
            if final is not None:
                try: price = round(int(final) / 100.0, 2)
                except (TypeError, ValueError): pass
        url = f"https://store.steampowered.com/app/{appid}/"
        # MSRP via appdetails (small extra call, cached)
        msrp = self._fetch_msrp(appid)
        time.sleep(0.25)
        return SteamMatch(appid=int(appid), name=it.get("name", ""), url=url,
                          price_now=price, msrp=msrp, match_score=score)

    def _fetch_msrp(self, appid: int) -> Optional[float]:
        data = self._get(APPDETAILS, {"appids": appid, "cc": "us", "l": "en"},
                         f"appdetails:{appid}")
        node = (data or {}).get(str(appid), {}) if isinstance(data, dict) else {}
        if not isinstance(node, dict) or not node.get("success"):
            return None
        po = (node.get("data") or {}).get("price_overview") or {}
        # `initial` is the MSRP in integer cents in the requested currency.
        initial = po.get("initial")
        if initial is None:
            return None
        try:
            return round(int(initial) / 100.0, 2)
        except (TypeError, ValueError):
            return None

    def cache_age(self, title: str) -> Optional[str]:
        query = normalize_for_search(title)
        cf = self._cache_path(f"search:{query.lower()}")
        if not cf.exists():
            return None
        return datetime.fromtimestamp(cf.stat().st_mtime, tz=timezone.utc).isoformat()


# ---------- installed games (local) ----------
_KV_STRING = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def _parse_libraryfolders(vdf_path: Path) -> list[Path]:
    """Return all Steam library paths found in libraryfolders.vdf."""
    out: list[Path] = []
    if not vdf_path.exists():
        return out
    text = vdf_path.read_text(encoding="utf-8", errors="replace")
    # libraryfolders.vdf has blocks like:  "0" { "path" "C:\\..." ... }
    for m in re.finditer(r'"\d+"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', text):
        block = m.group(1)
        pm = re.search(r'"path"\s+"([^"]+)"', block)
        if pm:
            p = Path(pm.group(1).replace("\\\\", "\\"))
            if p.exists():
                out.append(p / "steamapps")
    return out


def installed_appids(steam_root: Optional[str]) -> set[int]:
    """Scan all Steam libraries and return the set of installed appids."""
    if not steam_root:
        return set()
    root = Path(steam_root)
    steamapps_dirs = [root / "steamapps"]
    extra = _parse_libraryfolders(root / "steamapps" / "libraryfolders.vdf")
    for d in extra:
        if d not in steamapps_dirs:
            steamapps_dirs.append(d)
    appids: set[int] = set()
    for d in steamapps_dirs:
        if not d.exists():
            continue
        for acf in d.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r'"appid"\s+"(\d+)"', text)
            if m:
                appids.add(int(m.group(1)))
    return appids
