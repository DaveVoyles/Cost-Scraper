"""Shared utilities: config loader + paths."""
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"
DOCS_DIR = REPO_ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"   # lives under docs/ so GitHub Pages serves it
CACHE_DIR = PIPELINE_DIR / "cache"


def load_config() -> dict:
    """Load pipeline/config.local.json; fall back to config.example.json with a warning."""
    local = PIPELINE_DIR / "config.local.json"
    example = PIPELINE_DIR / "config.example.json"
    if local.exists():
        cfg = json.loads(local.read_text(encoding="utf-8"))
    else:
        print(f"!! {local.name} not found, falling back to {example.name} (no creds).")
        cfg = json.loads(example.read_text(encoding="utf-8"))
    # Resolve defaults
    # playnite_db_dir: explicit null in JSON means "use cached dump only" (steady state).
    # Use a sentinel: only auto-detect if the key is missing entirely from config.
    if "playnite_db_dir" not in cfg:
        cfg["playnite_db_dir"] = os.path.expandvars(r"%APPDATA%\Playnite\library")
    if not cfg.get("steam_root"):
        # Sensible default; sources.steam will probe further if missing.
        for cand in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
            if Path(cand).exists():
                cfg["steam_root"] = cand
                break
    return cfg


def load_user_alerts() -> list:
    """Load pipeline/user_alerts.json if present; otherwise empty list."""
    p = PIPELINE_DIR / "user_alerts.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_dirs():
    for sub in ("loaded", "steam", "itad"):
        (CACHE_DIR / sub).mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "history").mkdir(parents=True, exist_ok=True)
