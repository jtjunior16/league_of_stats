"""Crawl ~10,000 recent ranked solo matches from Master+ players.

Two separate steps, plus a progress report:
    python crawl_matches.py matches     # step 1: find and download matches (~3.5 hours)
    python crawl_matches.py timelines   # step 2: timelines for those matches (~3.5 hours)
    python crawl_matches.py status      # show progress so far

Both steps can be stopped at any time (Ctrl+C, expired key, closed laptop).
Run the same command again and it continues where it left off.

How matches are chosen:
    1. Download the Challenger, Grandmaster and Master ladders and shuffle them.
    2. For each tier (Challenger first), go through its players in random order,
       taking up to CRAWL_MATCHES_PER_PLAYER ranked solo games from the last
       CRAWL_DAYS days, until that tier's quota of valid matches is reached.
    3. Each match is tagged with the tier of the player it was found through.
       A match found by an earlier tier keeps that tier's tag.
    4. Remakes (under 5 minutes or ended by early surrender) are excluded and
       replaced, so each tier ends up with its full quota of real games.
    If a tier runs out of players, it does a second pass at 40 games per player.

Output in data/riot/crawl/:
    state.json              progress (the time window is fixed when the crawl starts)
    index.csv               one row per match: tier, patch, start time, duration, ...
    players_<TIER>.json     shuffled ladder snapshot
    matches/<id>.json.gz    match data (gzipped JSON)
    timelines/<id>.json.gz  timeline data

Read a file in Python with:
    import gzip, json
    with gzip.open("data/riot/crawl/matches/NA1_123.json.gz", "rt", encoding="utf-8") as f:
        match = json.load(f)

To start over with a fresh 14-day window, delete the data/riot/crawl folder.
"""

import argparse
import csv
import gzip
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from fetch_riot import RiotClient, load_settings

CRAWL_DIR = Path(__file__).parent / "data" / "riot" / "crawl"

# Valid matches wanted per tier, crawled in this order.
QUOTAS = {"CHALLENGER": 2000, "GRANDMASTER": 3000, "MASTER": 5000}
MAX_MATCHES_PER_PLAYER = 40  # second pass, used only if a tier runs out of players
QUEUE_ID = 420               # ranked solo/duo
REMAKE_SECONDS = 300
SAVE_EVERY = 25              # save progress after this many downloads


# ---------------------------------------------------------------- files

def paths():
    return {
        "state": CRAWL_DIR / "state.json",
        "index": CRAWL_DIR / "index.csv",
        "matches": CRAWL_DIR / "matches",
        "timelines": CRAWL_DIR / "timelines",
    }


def write_json_gz(path, data):
    """Write compact gzipped JSON. Writes to a temp file first so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def read_json_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- state

def new_state(settings):
    days = int(settings.get("CRAWL_DAYS") or 14)
    per_player = int(settings.get("CRAWL_MATCHES_PER_PLAYER") or 20)
    end = int(time.time())
    return {
        "start_time": end - days * 86400,
        "end_time": end,
        "tiers": {t: {"next_player": 0, "per_player": per_player} for t in QUOTAS},
        "matches": {},           # match_id -> {tier, seed, status, patch, start, duration}
        "timeline_missing": [],  # matches Riot had no timeline for
    }


def load_state(settings=None):
    p = paths()["state"]
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return new_state(settings) if settings is not None else None


def save_state(state):
    p = paths()["state"]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, p)


def write_index(state):
    """One CSV row per match, the starting table for analysis."""
    timeline_dir = paths()["timelines"]
    with open(paths()["index"], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["match_id", "tier", "status", "patch", "game_start_utc", "duration_s",
                    "has_timeline", "seed_puuid"])
        for mid, r in state["matches"].items():
            w.writerow([mid, r["tier"], r["status"], r.get("patch", ""),
                        iso(r["start"]) if r.get("start") else "", r.get("duration", ""),
                        (timeline_dir / f"{mid}.json.gz").exists(), r["seed"]])


def tier_counts(state, tier):
    ok, pending = 0, []
    for mid, r in state["matches"].items():
        if r["tier"] == tier:
            if r["status"] == "ok":
                ok += 1
            elif r["status"] == "pending":
                pending.append(mid)
    return ok, pending


# ---------------------------------------------------------------- progress

class Progress:
    """Prints progress with an ETA based on the speed so far in this run."""

    def __init__(self, remaining):
        self.start = time.monotonic()
        self.done = 0
        self.remaining = remaining

    def tick(self):
        self.done += 1

    def eta(self):
        elapsed = time.monotonic() - self.start
        if self.done == 0 or elapsed < 1:
            return "?"
        minutes = (self.remaining - self.done) / (self.done / elapsed) / 60
        return f"{minutes / 60:.1f} h" if minutes >= 90 else f"{max(minutes, 0):.0f} min"


# ---------------------------------------------------------------- step 1: matches

def load_players(client, tier):
    """Shuffled list of PUUIDs on a tier's ladder, saved so a resumed crawl uses the same order."""
    path = CRAWL_DIR / f"players_{tier}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    league = client.apex_league(tier) or {}
    puuids = [e["puuid"] for e in league.get("entries", []) if e.get("puuid")]
    if not puuids:
        raise SystemExit(f"No players with a PUUID came back for the {tier} ladder.")
    random.shuffle(puuids)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(puuids), encoding="utf-8")
    print(f"  {tier}: {len(puuids)} players on the ladder")
    return puuids


def describe_match(data):
    info = data["info"]
    duration = info.get("gameDuration", 0)
    remake = duration < REMAKE_SECONDS or any(
        p.get("gameEndedInEarlySurrender") for p in info.get("participants", []))
    if remake:
        status = "remake"
    elif info.get("queueId") != QUEUE_ID:
        status = "wrong_queue"
    else:
        status = "ok"
    return {
        "status": status,
        "patch": ".".join(info.get("gameVersion", "").split(".")[:2]),
        "start": info.get("gameStartTimestamp", 0) // 1000,
        "duration": duration,
    }


def download_pending(client, state, tier, pending, progress):
    match_dir = paths()["matches"]
    ok, _ = tier_counts(state, tier)
    for mid in pending:
        path = match_dir / f"{mid}.json.gz"
        if path.exists():  # downloaded before a crash, but state wasn't saved yet
            data = read_json_gz(path)
        else:
            data = client.match(mid)
            if data is not None:
                write_json_gz(path, data)
        record = state["matches"][mid]
        if data is None:
            record["status"] = "missing"
        else:
            record.update(describe_match(data))
            ok += record["status"] == "ok"
        progress.tick()
        if progress.done % SAVE_EVERY == 0:
            save_state(state)
            print(f"    {tier}: {ok}/{QUOTAS[tier]} valid | {progress.done} downloaded this run "
                  f"| ~{progress.eta()} left")


def crawl_tier(client, state, tier, progress):
    quota = QUOTAS[tier]
    t = state["tiers"][tier]
    players = load_players(client, tier)

    while True:
        ok, pending = tier_counts(state, tier)
        if ok >= quota:
            print(f"  {tier}: done, {ok} valid matches")
            return
        if pending:
            download_pending(client, state, tier, pending, progress)
            continue

        if t["next_player"] >= len(players):
            if t["per_player"] < MAX_MATCHES_PER_PLAYER:
                print(f"  {tier}: ran out of players at {ok}/{quota}. Second pass at "
                      f"{MAX_MATCHES_PER_PLAYER} games per player.")
                t["per_player"] = MAX_MATCHES_PER_PLAYER
                t["next_player"] = 0
                continue
            print(f"  {tier}: only {ok}/{quota} valid matches exist for these players "
                  "in this time window. Moving on.")
            return

        seed = players[t["next_player"]]
        ids = client.match_ids(seed, queue=QUEUE_ID, start_time=state["start_time"],
                               end_time=state["end_time"], count=t["per_player"]) or []
        t["next_player"] += 1
        need = quota - ok
        for mid in ids:
            if need <= 0:
                break
            if mid not in state["matches"]:
                state["matches"][mid] = {"tier": tier, "seed": seed, "status": "pending"}
                need -= 1


def run_matches(client, state):
    remaining = sum(max(q - tier_counts(state, t)[0], 0) for t, q in QUOTAS.items())
    progress = Progress(remaining)
    print(f"Step 1: matches. {remaining} still needed. Loading ladders...")
    for tier in QUOTAS:
        crawl_tier(client, state, tier, progress)
    print("Matches done. Next: python crawl_matches.py timelines")


# ---------------------------------------------------------------- step 2: timelines

def run_timelines(client, state):
    timeline_dir = paths()["timelines"]
    missing = set(state["timeline_missing"])
    tier_order = {t: i for i, t in enumerate(QUOTAS)}
    ok_ids = sorted((mid for mid, r in state["matches"].items() if r["status"] == "ok"),
                    key=lambda m: tier_order[state["matches"][m]["tier"]])
    todo = [m for m in ok_ids if m not in missing and not (timeline_dir / f"{m}.json.gz").exists()]

    if any(r["status"] == "pending" for r in state["matches"].values()) or \
            sum(tier_counts(state, t)[0] for t in QUOTAS) < sum(QUOTAS.values()):
        print("Note: step 1 isn't finished. Downloading timelines for the matches so far; "
              "run this again after step 1 to get the rest.")
    print(f"Step 2: timelines. {len(ok_ids) - len(todo)} already downloaded, {len(todo)} to fetch.")

    progress = Progress(len(todo))
    for mid in todo:
        data = client.timeline(mid)
        if data is None:
            state["timeline_missing"].append(mid)
        else:
            write_json_gz(timeline_dir / f"{mid}.json.gz", data)
        progress.tick()
        if progress.done % SAVE_EVERY == 0 or progress.done == len(todo):
            save_state(state)
            print(f"    {progress.done}/{len(todo)} | ~{progress.eta()} left")
    print("Timelines done.")


# ---------------------------------------------------------------- status

def show_status(state):
    if state is None:
        print("No crawl yet. Start one with: python crawl_matches.py matches")
        return
    timeline_dir = paths()["timelines"]
    print(f"Window: {iso(state['start_time'])} to {iso(state['end_time'])}\n")
    print(f"{'Tier':<12}{'Valid':>12}{'Pending':>9}{'Remakes':>9}{'Missing':>9}"
          f"{'Timelines':>11}{'Players used':>14}")
    for tier, quota in QUOTAS.items():
        recs = [(m, r) for m, r in state["matches"].items() if r["tier"] == tier]
        count = lambda s: sum(r["status"] == s for _, r in recs)
        timelines = sum((timeline_dir / f"{m}.json.gz").exists() for m, r in recs if r["status"] == "ok")
        players_file = CRAWL_DIR / f"players_{tier}.json"
        total_players = len(json.loads(players_file.read_text())) if players_file.exists() else "?"
        print(f"{tier:<12}{count('ok'):>6}/{quota:<5}{count('pending'):>9}{count('remake'):>9}"
              f"{count('missing'):>9}{timelines:>11}"
              f"{state['tiers'][tier]['next_player']:>8}/{total_players}")

    patches = {}
    for r in state["matches"].values():
        if r["status"] == "ok":
            patches[r["patch"]] = patches.get(r["patch"], 0) + 1
    if patches:
        print("\nValid matches by patch: " + ", ".join(f"{p}: {n}" for p, n in sorted(patches.items())))
    size = sum(f.stat().st_size for f in CRAWL_DIR.rglob("*") if f.is_file())
    print(f"Disk used: {size / 1e9:.2f} GB")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Crawl Master+ ranked solo matches.")
    parser.add_argument("step", choices=["matches", "timelines", "status"])
    args = parser.parse_args()
    settings = load_settings()

    if args.step == "status":
        show_status(load_state())
        return

    client = RiotClient.from_settings(settings)
    state = load_state(settings)
    print(f"Window: {iso(state['start_time'])} to {iso(state['end_time'])}")
    try:
        if args.step == "matches":
            run_matches(client, state)
        else:
            run_timelines(client, state)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        save_state(state)
        write_index(state)
        print("Progress saved. Run the same command again to continue.")


if __name__ == "__main__":
    main()
