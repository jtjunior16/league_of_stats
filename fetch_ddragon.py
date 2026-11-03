"""Download League of Legends static data from Data Dragon.

Data Dragon is Riot's public CDN for static game data (champions, items,
runes, summoner spells, ...). It needs no API key and has no rate limit.

Usage (from the project folder, with the venv active):
    python fetch_ddragon.py                    # latest patch, en_US, JSON only
    python fetch_ddragon.py --version 14.1.1   # a specific patch
    python fetch_ddragon.py --lang ko_KR       # another language
    python fetch_ddragon.py --images           # also download icons
    python fetch_ddragon.py --force            # re-download existing files

Output layout:
    data/ddragon/
        versions.json, languages.json, realms_<realm>.json
        queues.json, maps.json, gameModes.json, gameTypes.json, seasons.json
        <version>/<lang>/champion.json, item.json, ...
        <version>/<lang>/champion/<ChampionId>.json   (one file per champion)
        <version>/img/...                              (only with --images)
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

DDRAGON = "https://ddragon.leagueoflegends.com"
STATIC_DOCS = "https://static.developer.riotgames.com/docs/lol"
OUT_DIR = Path(__file__).parent / "data" / "ddragon"

# Per-language data files. Not every file exists for every patch, so a missing
# one (403/404) is reported and skipped rather than treated as an error.
DATA_FILES = [
    "champion.json",       # summary of every champion
    "championFull.json",   # every champion with spells, skins, lore, stats
    "item.json",
    "summoner.json",       # summoner spells (Flash, Ignite, ...)
    "runesReforged.json",
    "profileicon.json",
    "map.json",
    "language.json",       # UI string translations
    "mission-assets.json",
    "sticker.json",
    "feats.json",
    "challenges.json",
]

# Lookup tables for IDs used in Match-V5 (queueId, mapId, gameMode, ...).
# These are not on Data Dragon but are published by Riot alongside it.
STATIC_DOC_FILES = ["queues.json", "maps.json", "gameModes.json", "gameTypes.json", "seasons.json"]

session = requests.Session()


def get_json(url):
    """Return parsed JSON from url, or None if it does not exist."""
    resp = session.get(url, timeout=30)
    if resp.status_code in (403, 404):  # the CDN returns 403 for missing files
        return None
    resp.raise_for_status()
    return resp.json()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_json(url, path, force):
    """Download url to path unless it already exists. Returns 'ok', 'skip' or 'missing'."""
    if path.exists() and not force:
        return "skip"
    data = get_json(url)
    if data is None:
        return "missing"
    save_json(path, data)
    return "ok"


def fetch_file(url, path, force):
    """Download a binary file (image) to path unless it already exists."""
    if path.exists() and not force:
        return "skip"
    resp = session.get(url, timeout=30)
    if resp.status_code in (403, 404):  # the CDN returns 403 for missing files
        return "missing"
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return "ok"


def run_all(jobs, func, force, label):
    """Run (url, path) download jobs in parallel and print a summary."""
    counts = {"ok": 0, "skip": 0, "missing": 0}
    missing = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = pool.map(lambda job: (job, func(job[0], job[1], force)), jobs)
        for (url, _), status in results:
            counts[status] += 1
            if status == "missing":
                missing.append(url)
    print(f"  {label}: {counts['ok']} downloaded, {counts['skip']} already present, "
          f"{counts['missing']} not available")
    for url in missing:
        print(f"    not available: {url}")


def image_jobs(version, lang_dir, img_dir):
    """Build download jobs for champion, item, spell, rune and passive icons."""
    jobs = []
    base = f"{DDRAGON}/cdn/{version}/img"

    def icons(data_file, group):
        path = lang_dir / data_file
        if not path.exists():
            return
        for entry in json.loads(path.read_text(encoding="utf-8"))["data"].values():
            name = entry["image"]["full"]
            jobs.append((f"{base}/{group}/{name}", img_dir / group / name))

    icons("champion.json", "champion")
    icons("item.json", "item")
    icons("summoner.json", "spell")

    # Champion ability and passive icons come from the per-champion files.
    full = lang_dir / "championFull.json"
    if full.exists():
        for champ in json.loads(full.read_text(encoding="utf-8"))["data"].values():
            for spell in champ["spells"]:
                name = spell["image"]["full"]
                jobs.append((f"{base}/spell/{name}", img_dir / "spell" / name))
            name = champ["passive"]["image"]["full"]
            jobs.append((f"{base}/passive/{name}", img_dir / "passive" / name))

    # Rune icons live outside the versioned folder.
    runes = lang_dir / "runesReforged.json"
    if runes.exists():
        for tree in json.loads(runes.read_text(encoding="utf-8")):
            paths = [tree["icon"]] + [r["icon"] for slot in tree["slots"] for r in slot["runes"]]
            for icon in paths:
                jobs.append((f"{DDRAGON}/cdn/img/{icon}", img_dir / "runes" / icon))

    return list(dict(jobs).items())  # de-duplicate by URL


def main():
    parser = argparse.ArgumentParser(description="Download static data from Data Dragon.")
    parser.add_argument("--version", help="patch version, e.g. 14.1.1 (default: latest)")
    parser.add_argument("--lang", default="en_US", help="language code (default: en_US)")
    parser.add_argument("--images", action="store_true", help="also download icons")
    parser.add_argument("--force", action="store_true", help="re-download files that already exist")
    args = parser.parse_args()

    print("Fetching version and language lists...")
    versions = get_json(f"{DDRAGON}/api/versions.json")
    languages = get_json(f"{DDRAGON}/cdn/languages.json")
    save_json(OUT_DIR / "versions.json", versions)
    save_json(OUT_DIR / "languages.json", languages)

    version = args.version or versions[0]
    if version not in versions:
        sys.exit(f"Unknown version {version!r}. Recent versions: {', '.join(versions[:5])}")
    if args.lang not in languages:
        sys.exit(f"Unknown language {args.lang!r}. Options: {', '.join(languages)}")
    print(f"Patch {version}, language {args.lang}")

    lang_dir = OUT_DIR / version / args.lang

    # Static lookup tables and realm info are small and change, so always refresh them.
    run_all([(f"{STATIC_DOCS}/{f}", OUT_DIR / f) for f in STATIC_DOC_FILES],
            fetch_json, True, "ID lookup tables")
    run_all([(f"{DDRAGON}/realms/{r}.json", OUT_DIR / f"realms_{r}.json") for r in ("na", "euw", "kr")],
            fetch_json, True, "realm info")

    run_all([(f"{DDRAGON}/cdn/{version}/data/{args.lang}/{f}", lang_dir / f) for f in DATA_FILES],
            fetch_json, args.force, "data files")

    # One detailed file per champion (same content as championFull.json, split up).
    champion_list = lang_dir / "champion.json"
    if champion_list.exists():
        ids = json.loads(champion_list.read_text(encoding="utf-8"))["data"].keys()
        run_all([(f"{DDRAGON}/cdn/{version}/data/{args.lang}/champion/{c}.json",
                  lang_dir / "champion" / f"{c}.json") for c in ids],
                fetch_json, args.force, "per-champion files")

    if args.images:
        run_all(image_jobs(version, lang_dir, OUT_DIR / version / "img"),
                fetch_file, args.force, "images")

    print(f"Done. Files are in {OUT_DIR}")


if __name__ == "__main__":
    main()
