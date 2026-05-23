"""Playnite library extractor.

Reads %APPDATA%\\Playnite\\library\\{games,platforms,sources}.db (LiteDB v5) and
emits a list of {Name, Platforms, Source, IsInstalled, Hidden, InstallDirectory}.

Playnite holds an exclusive lock on these files while running. To work around
this without requiring the user to close Playnite, we make a temp copy first via
PowerShell + LiteDB.dll loaded from the user's Playnite install.

If the import-time copy fails (Playnite running with the file locked), we fall
back to the most-recent committed dump at data/playnite_games.json.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path


_PS_EXTRACT = r'''
$ErrorActionPreference = "Stop"
$liteDbDll = Join-Path $env:LOCALAPPDATA "Playnite\LiteDB.dll"
if (-not (Test-Path $liteDbDll)) { Write-Error "LiteDB.dll not found at $liteDbDll"; exit 2 }
Add-Type -Path $liteDbDll

$libDir = $args[0]
$outFile = $args[1]
$tmp = Join-Path $env:TEMP ("playnite_dbs_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
foreach ($n in 'games.db','platforms.db','sources.db') {
    Copy-Item (Join-Path $libDir $n) (Join-Path $tmp $n) -Force
}

function LoadLookup($file, $coll) {
    $db = New-Object LiteDB.LiteDatabase("Filename=$(Join-Path $tmp $file);ReadOnly=true;Upgrade=true")
    $h = @{}
    try {
        foreach ($d in $db.GetCollection($coll).FindAll()) {
            $h[$d['_id'].AsGuid.ToString()] = $d['Name'].AsString
        }
    } finally { $db.Dispose() }
    return $h
}

$plat = LoadLookup 'platforms.db' 'Platform'
$src  = LoadLookup 'sources.db'   'GameSource'

$db = New-Object LiteDB.LiteDatabase("Filename=$(Join-Path $tmp 'games.db');ReadOnly=true;Upgrade=true")
$games = @()
try {
    foreach ($g in $db.GetCollection('Game').FindAll()) {
        $platNames = @()
        if ($g.ContainsKey('PlatformIds')) {
            foreach ($p in $g['PlatformIds'].AsArray) {
                $k = $p.AsGuid.ToString()
                if ($plat.ContainsKey($k)) { $platNames += $plat[$k] }
            }
        }
        $srcName = $null
        if ($g.ContainsKey('SourceId')) {
            $sk = $g['SourceId'].AsGuid.ToString()
            if ($src.ContainsKey($sk)) { $srcName = $src[$sk] }
        }
        $games += [PSCustomObject]@{
            Name = $g['Name'].AsString
            Platforms = ($platNames -join '; ')
            Source = $srcName
            IsInstalled = if ($g.ContainsKey('IsInstalled')) { $g['IsInstalled'].AsBoolean } else { $false }
            Hidden = if ($g.ContainsKey('Hidden')) { $g['Hidden'].AsBoolean } else { $false }
            InstallDirectory = if ($g.ContainsKey('InstallDirectory')) { $g['InstallDirectory'].AsString } else { $null }
        }
    }
} finally { $db.Dispose() }
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$games | Where-Object { -not $_.Hidden } | Sort-Object Name | ConvertTo-Json -Depth 4 |
    Out-File $outFile -Encoding utf8
'''


def extract(playnite_db_dir: str | None, dump_path: Path) -> list[dict]:
    """Try to dump from live Playnite DB; on failure return the last committed dump.

    If `playnite_db_dir` is falsy, skip extraction entirely and use the cached
    dump as the source of truth. This is the recommended steady-state mode —
    you only need Playnite for the very first run (or when refreshing).
    """
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    if not playnite_db_dir:
        if dump_path.exists():
            games = json.loads(dump_path.read_text(encoding="utf-8-sig"))
            if isinstance(games, dict):
                games = [games]
            print(f"  [playnite] using cached dump ({len(games)} titles) — playnite_db_dir disabled in config")
            return games
        raise RuntimeError(
            f"playnite_db_dir is disabled and no cached dump at {dump_path}. "
            f"Set playnite_db_dir in config.local.json once to seed, then disable again."
        )
    try:
        with tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8") as tf:
            tf.write(_PS_EXTRACT)
            ps_script = tf.name
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps_script, playnite_db_dir, str(dump_path)],
                check=True, capture_output=True, timeout=60,
            )
        finally:
            try: os.unlink(ps_script)
            except OSError: pass
        games = json.loads(dump_path.read_text(encoding="utf-8-sig"))
        # PowerShell emits a single object (not an array) when only 1 game; normalize.
        if isinstance(games, dict):
            games = [games]
        print(f"  [playnite] dumped {len(games)} titles from {playnite_db_dir}")
        return games
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or b"").decode(errors="replace")
        print(f"  !! playnite extract failed: {msg.strip()[:200]}")
    except Exception as e:
        print(f"  !! playnite extract failed: {e}")

    if dump_path.exists():
        games = json.loads(dump_path.read_text(encoding="utf-8-sig"))
        if isinstance(games, dict):
            games = [games]
        print(f"  [playnite] using cached dump ({len(games)} titles) — close Playnite for a fresh extract")
        return games
    raise RuntimeError(
        f"No Playnite dump available. Close Playnite and rerun, or place a "
        f"hand-written games list at {dump_path}."
    )
