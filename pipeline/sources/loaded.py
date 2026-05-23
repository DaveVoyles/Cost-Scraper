"""loaded.com Algolia search client.

loaded.com fronts its product catalogue with an Algolia search-only API. The
Algolia app id, api key, and index name are exposed in the page's
window.algoliaConfig blob. We refresh them on every run because the api key has
a baked-in expiry.

The site is protected by a WAF that 403's `requests` + `Invoke-WebRequest`
regardless of UA, but curl.exe sails through, so we shell out for the homepage
fetch only. The Algolia API itself accepts plain `requests` traffic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from rapidfuzz import fuzz

from ..matching import (
    normalize_for_search, base_token_coverage, classify_edition,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


@dataclass
class LoadedEdition:
    product: str
    edition: str
    url: str
    platform: str
    region: str
    price: Optional[float]
    msrp: Optional[float]
    in_stock: Optional[bool]
    match_score: int

    def as_dict(self) -> dict:
        return {
            "product": self.product,
            "edition": self.edition,
            "url": self.url,
            "platform": self.platform,
            "region": self.region,
            "price": self.price,
            "msrp": self.msrp,
            "in_stock": self.in_stock,
            "match_score": self.match_score,
        }


class LoadedClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cfg: Optional[tuple[str, str, str]] = None

    # ---------- credential bootstrap ----------
    def _get_algolia_config(self) -> tuple[str, str, str]:
        if self._cfg:
            return self._cfg
        try:
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tf:
                tmp_path = tf.name
            try:
                subprocess.run(
                    ["curl.exe", "-sL", "-A", UA,
                     "-H", "Accept: text/html,application/xhtml+xml",
                     "-H", "Accept-Language: en-US,en;q=0.9",
                     "https://www.loaded.com/", "-o", tmp_path],
                    check=True, timeout=30, capture_output=True,
                )
                html = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
            finally:
                try: os.unlink(tmp_path)
                except OSError: pass
            m = re.search(r'window\.algoliaConfig\s*=\s*(\{.*?\});', html, re.DOTALL)
            if not m:
                raise RuntimeError("algoliaConfig not in HTML")
            cfg = json.loads(m.group(1))
            app_id = cfg["instant"].get("applicationId") or cfg.get("applicationId")
            api_key = cfg.get("apiKey") or cfg["instant"].get("apiKey")
            index = None
            for s in cfg.get("sortingIndices", []):
                if s.get("label", "").lower() == "relevance":
                    index = s["name"]
                    break
            if not (app_id and api_key and index):
                raise RuntimeError("incomplete algoliaConfig")
            self._cfg = (app_id, api_key, index)
            return self._cfg
        except Exception as e:
            print(f"  !! loaded.com config fetch failed ({e}); cached-only mode")
            self._cfg = ("_cached_", "_cached_", "_cached_")
            return self._cfg

    # ---------- search ----------
    def _search(self, query: str, hits_per_page: int = 24) -> dict:
        slug = hashlib.md5(query.lower().encode()).hexdigest()[:16]
        cache_file = self.cache_dir / f"{slug}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        app_id, api_key, index = self._get_algolia_config()
        if app_id == "_cached_":
            return {"hits": []}
        url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{index}/query"
        headers = {
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key": api_key,
            "Content-Type": "application/json",
            "User-Agent": UA,
        }
        body = {"params": f"query={requests.utils.quote(query)}&hitsPerPage={hits_per_page}"}
        for attempt in range(4):
            r = requests.post(url, headers=headers, json=body, timeout=30)
            if r.status_code == 200:
                data = r.json()
                cache_file.write_text(json.dumps(data), encoding="utf-8")
                # stamp cache file mtime for last-checked tracking
                return data
            if r.status_code in (429, 503):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        return {"hits": []}

    # ---------- public API ----------
    def lookup(self, title: str) -> list[LoadedEdition]:
        """Return all kept editions for a Playnite title. Filters Xbox / EU / UK."""
        query = normalize_for_search(title)
        try:
            data = self._search(query)
        except Exception as e:
            print(f"  !! loaded search failed for {title!r}: {e}")
            return []
        editions: list[LoadedEdition] = []
        for h in data.get("hits", []):
            name = _unwrap(h.get("name", "")) or ""
            score = int(fuzz.token_set_ratio(query.lower(), name.lower()))
            coverage = base_token_coverage(query, name)
            if score < 70 or coverage < 0.8:
                continue
            f = _hit_fields(h)
            plat_l = (f["platform"] or "").lower()
            region_l = (f["region"] or "").lower()
            if "xbox" in plat_l:
                continue
            if "europe" in region_l or re.search(r"\buk\b", region_l):
                continue
            editions.append(LoadedEdition(
                product=f["name"],
                edition=classify_edition(f["name"], query),
                url=f["url"],
                platform=f["platform"],
                region=f["region"],
                price=f["price"],
                msrp=f["msrp"],
                in_stock=f["in_stock"],
                match_score=score,
            ))
        time.sleep(0.35)
        return editions

    def cache_age(self, title: str) -> Optional[str]:
        query = normalize_for_search(title)
        slug = hashlib.md5(query.lower().encode()).hexdigest()[:16]
        f = self.cache_dir / f"{slug}.json"
        if not f.exists():
            return None
        return datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()


def _unwrap(v, locale: str = "default"):
    if isinstance(v, dict):
        if locale in v: return v[locale]
        if "default" in v: return v["default"]
        for x in v.values():
            if not isinstance(x, (dict, list)): return x
        return None
    return v


def _hit_fields(hit: dict) -> dict:
    name = _unwrap(hit.get("name", ""))
    url = _unwrap(hit.get("url", ""))
    plat_attr = _unwrap(hit.get("platforms"))
    if isinstance(plat_attr, list):
        platform = ", ".join(plat_attr)
    else:
        platform = plat_attr or ""
    region = _unwrap(hit.get("region")) or ""
    price = None
    msrp = None
    price_obj = hit.get("price", {})
    if isinstance(price_obj, dict):
        usd = price_obj.get("USD", {})
        if isinstance(usd, dict):
            price = usd.get("default")
            msrp = usd.get("default_original") or None
            try:
                if msrp is not None and price is not None and float(msrp) <= float(price):
                    msrp = None
            except (TypeError, ValueError):
                msrp = None
    in_stock_attr = hit.get("in_stock")
    if isinstance(in_stock_attr, dict):
        in_stock_attr = _unwrap(in_stock_attr)
    if isinstance(in_stock_attr, list):
        in_stock = bool(any(in_stock_attr)) if in_stock_attr else None
    elif isinstance(in_stock_attr, (int, bool)):
        in_stock = bool(in_stock_attr)
    else:
        in_stock = None
    return {"name": name or "", "url": url or "", "platform": platform,
            "region": region, "price": price, "msrp": msrp, "in_stock": in_stock}
