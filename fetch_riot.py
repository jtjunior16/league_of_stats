"""Pull player and match data from the Riot API.

Settings come from .env (see .env.example). Command-line options override them.

Usage (from the project folder, with the venv active):
    python fetch_riot.py                          # uses RIOT_ID and filters from .env
    python fetch_riot.py --riot-id "Name#NA1"     # a different player
    python fetch_riot.py --count 300 --queue 450  # 300 ARAM games
    python fetch_riot.py --start-date 2026-09-01 --timelines

Output layout:
    data/riot/
        players/<Name_TAG>/account.json, summoner.json, league.json,
                           mastery.json, match_ids.json
        matches/<matchId>.json      shared by all players, never downloaded twice
        timelines/<matchId>.json    only with --timelines

The RiotClient class can also be imported from other scripts:
    from fetch_riot import RiotClient, load_settings
    client = RiotClient.from_settings(load_settings())
"""

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "data" / "riot"

# Which platforms (servers) belong to which regional host.
PLATFORMS_BY_REGION = {
    "americas": ["na1", "br1", "la1", "la2"],
    "europe": ["euw1", "eun1", "tr1", "ru", "me1"],
    "asia": ["kr", "jp1"],
    "sea": ["oc1", "sg2", "tw2", "vn2"],
}


# ---------------------------------------------------------------- settings

def load_settings(path=ROOT / ".env"):
    """Read KEY=VALUE lines from .env. Real environment variables take precedence.

    Lines starting with # are comments. Inline comments are not supported,
    because Riot IDs contain '#'.
    """
    settings = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip('"').strip("'")
    for key in settings:
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def parse_rate_limits(text):
    """'20:1,100:120' -> [(20, 1.0), (100, 120.0)]"""
    limits = []
    for pair in text.split(","):
        count, seconds = pair.split(":")
        limits.append((int(count), float(seconds)))
    return limits


def date_to_epoch(text):
    """'2026-09-01' -> Unix seconds at midnight UTC."""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------- client

class RateLimiter:
    """Waits before each request so we never exceed any (count, seconds) window."""

    def __init__(self, limits):
        self.limits = limits
        self.calls = deque()

    def wait(self):
        while True:
            now = time.monotonic()
            longest = max(seconds for _, seconds in self.limits)
            while self.calls and now - self.calls[0] >= longest:
                self.calls.popleft()

            delay = 0.0
            for count, seconds in self.limits:
                recent = [t for t in self.calls if now - t < seconds]
                if len(recent) >= count:
                    # Wait until the oldest call that counts against this window expires.
                    delay = max(delay, seconds - (now - recent[-count]))
            if delay <= 0:
                self.calls.append(now)
                return
            if delay > 2:
                print(f"    rate limit: waiting {delay:.0f}s...")
            time.sleep(delay + 0.05)


class RiotClient:
    def __init__(self, api_key, region="americas", platform="na1", rate_limits=((20, 1), (100, 120))):
        if not api_key:
            sys.exit("RIOT_API_KEY is empty. Add your key to .env.")
        if region not in PLATFORMS_BY_REGION:
            sys.exit(f"Unknown region {region!r}. Options: {', '.join(PLATFORMS_BY_REGION)}")
        if platform not in PLATFORMS_BY_REGION[region]:
            print(f"Warning: platform {platform!r} is not usually in region {region!r} "
                  f"(expected one of {', '.join(PLATFORMS_BY_REGION[region])}).")
        self.region = region
        self.platform = platform
        self.limiter = RateLimiter(list(rate_limits))
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = api_key

    @classmethod
    def from_settings(cls, settings):
        return cls(
            api_key=settings.get("RIOT_API_KEY", ""),
            region=settings.get("RIOT_REGION") or "americas",
            platform=settings.get("RIOT_PLATFORM") or "na1",
            rate_limits=parse_rate_limits(settings.get("RIOT_RATE_LIMITS") or "20:1,100:120"),
        )

    def get(self, host, path, params=None):
        """GET a Riot API path. Returns parsed JSON, or None if not found (404)."""
        url = f"https://{host}.api.riotgames.com{path}"
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        for attempt in range(5):
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.ConnectionError:
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            if resp.status_code in (401, 403):
                sys.exit(f"Riot API rejected the request ({resp.status_code}) for {url}\n"
                         "Development keys expire every 24 hours. Generate a new one at "
                         "https://developer.riotgames.com and update RIOT_API_KEY in .env.")
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"    429 rate limited by Riot: waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)  # Riot server hiccup: back off and retry
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Giving up on {url} after 5 attempts")

    def regional(self, path, params=None):
        return self.get(self.region, path, params)

    def on_platform(self, path, params=None):
        return self.get(self.platform, path, params)

    # --- Account-V1 (regional)
    def account_by_riot_id(self, game_name, tag_line):
        return self.regional(f"/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}")

    def account_by_puuid(self, puuid):
        return self.regional(f"/riot/account/v1/accounts/by-puuid/{puuid}")

    # --- Summoner-V4, League-V4, Champion-Mastery-V4 (platform)
    def summoner_by_puuid(self, puuid):
        return self.on_platform(f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    def league_entries(self, puuid):
        return self.on_platform(f"/lol/league/v4/entries/by-puuid/{puuid}")

    def apex_league(self, tier, queue="RANKED_SOLO_5x5"):
        """Whole ladder for an apex tier: 'CHALLENGER', 'GRANDMASTER' or 'MASTER'."""
        return self.on_platform(f"/lol/league/v4/{tier.lower()}leagues/by-queue/{queue}")

    def league_entries_page(self, tier, division, page=1, queue="RANKED_SOLO_5x5"):
        """One page (~205 players) of a non-apex rank, e.g. tier='DIAMOND', division='II'."""
        return self.on_platform(f"/lol/league/v4/entries/{queue}/{tier}/{division}", {"page": page})

    def champion_mastery(self, puuid):
        return self.on_platform(f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}")

    # --- Match-V5 (regional)
    def match_ids(self, puuid, queue=None, match_type=None, start_time=None, end_time=None, start=0, count=20):
        """One page (at most 100) of match IDs, newest first."""
        return self.regional(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", {
            "queue": queue, "type": match_type, "startTime": start_time,
            "endTime": end_time, "start": start, "count": count,
        })

    def all_match_ids(self, puuid, total, **filters):
        """Page through match IDs until `total` are collected or history runs out."""
        ids = []
        while len(ids) < total:
            page = self.match_ids(puuid, start=len(ids), count=min(100, total - len(ids)), **filters)
            if not page:
                break
            ids.extend(page)
        return ids

    def match(self, match_id):
        return self.regional(f"/lol/match/v5/matches/{match_id}")

    def timeline(self, match_id):
        return self.regional(f"/lol/match/v5/matches/{match_id}/timeline")


# ---------------------------------------------------------------- pull script

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def download_matches(client, match_ids, folder, fetch):
    """Download each match into folder, skipping ones already on disk."""
    todo = [m for m in match_ids if not (folder / f"{m}.json").exists()]
    print(f"  {len(match_ids) - len(todo)} already downloaded, {len(todo)} to fetch")
    for i, match_id in enumerate(todo, 1):
        data = fetch(match_id)
        if data is not None:
            save_json(folder / f"{match_id}.json", data)
        if i % 10 == 0 or i == len(todo):
            print(f"    {i}/{len(todo)}")


def main():
    settings = load_settings()
    s = settings.get

    parser = argparse.ArgumentParser(description="Pull player and match data from the Riot API.")
    parser.add_argument("--riot-id", default=s("RIOT_ID"), help='e.g. "Name#NA1" (default: RIOT_ID in .env)')
    parser.add_argument("--region", default=s("RIOT_REGION") or "americas")
    parser.add_argument("--platform", default=s("RIOT_PLATFORM") or "na1")
    parser.add_argument("--queue", default=s("MATCH_QUEUE"), help="queue ID, e.g. 420 (blank = all)")
    parser.add_argument("--type", default=s("MATCH_TYPE"), help="ranked, normal, tourney, tutorial")
    parser.add_argument("--count", type=int, default=int(s("MATCH_COUNT") or 20), help="matches to pull")
    parser.add_argument("--start-date", default=s("MATCH_START_DATE"), help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=s("MATCH_END_DATE"), help="YYYY-MM-DD")
    parser.add_argument("--timelines", action="store_true",
                        default=(s("MATCH_TIMELINES") or "").lower() == "true",
                        help="also download minute-by-minute timelines")
    args = parser.parse_args()

    if not args.riot_id or "#" not in args.riot_id:
        sys.exit('Set RIOT_ID in .env or pass --riot-id "Name#TAG".')
    game_name, tag_line = args.riot_id.rsplit("#", 1)

    client = RiotClient(
        api_key=s("RIOT_API_KEY", ""),
        region=args.region,
        platform=args.platform,
        rate_limits=parse_rate_limits(s("RIOT_RATE_LIMITS") or "20:1,100:120"),
    )

    print(f"Looking up {game_name}#{tag_line} ({args.region} / {args.platform})...")
    account = client.account_by_riot_id(game_name, tag_line)
    if account is None:
        sys.exit(f"No account found for {game_name}#{tag_line} in {args.region}.")
    puuid = account["puuid"]
    player_dir = OUT_DIR / "players" / f"{account['gameName']}_{account['tagLine']}"
    save_json(player_dir / "account.json", account)

    print("Fetching profile, rank and mastery...")
    summoner = client.summoner_by_puuid(puuid)
    if summoner is None:
        print(f"  No summoner on {args.platform}. Is RIOT_PLATFORM set to the player's server?")
    else:
        save_json(player_dir / "summoner.json", summoner)
        save_json(player_dir / "league.json", client.league_entries(puuid))
        save_json(player_dir / "mastery.json", client.champion_mastery(puuid))

    filters = {
        "queue": args.queue,
        "match_type": args.type,
        "start_time": date_to_epoch(args.start_date) if args.start_date else None,
        "end_time": date_to_epoch(args.end_date) if args.end_date else None,
    }
    shown = ", ".join(f"{k}={v}" for k, v in filters.items() if v) or "none"
    print(f"Fetching up to {args.count} match IDs (filters: {shown})...")
    match_ids = client.all_match_ids(puuid, args.count, **filters)
    save_json(player_dir / "match_ids.json", match_ids)
    print(f"  found {len(match_ids)}")

    print("Downloading matches...")
    download_matches(client, match_ids, OUT_DIR / "matches", client.match)
    if args.timelines:
        print("Downloading timelines...")
        download_matches(client, match_ids, OUT_DIR / "timelines", client.timeline)

    print(f"Done. Player files: {player_dir}")


if __name__ == "__main__":
    main()
