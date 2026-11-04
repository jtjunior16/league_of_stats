# Project Log: league_of_stats

Running log of what's been built, decisions made, known issues and next steps.
**To start a new Claude session:** ask it to read this file first, then continue from "Next steps".
Update this file at the end of each work session.

---

## Goal
League of Legends data analysis, for fun and for practice: champion stats, item builds,
matchups, and so on, using the Riot API (match data) and Data Dragon (static game data).

## Environment
- Windows 10, PowerShell, VS Code. Python 3.15 in `.venv` (setup steps are in README.md).
- Only dependency so far: `requests`.
- GitHub: `jtjunior16/league_of_stats`. Work happens on feature branches, merged to `main` by pull request.

## Secrets and config (`.env`, gitignored, never commit)
- `RIOT_API_KEY`: development key. **Expires every 24 h**; regenerate at developer.riotgames.com.
  A new key can return 401 "Unknown apikey" for about a minute before it activates.
- Routing: `RIOT_REGION=americas`, `RIOT_PLATFORM=na1`.
- `RIOT_ID`: the default player for `fetch_riot.py`.
- `MATCH_*` filters for `fetch_riot.py`; `CRAWL_DAYS=14` and `CRAWL_MATCHES_PER_PLAYER=20` for the crawler.
- `RIOT_RATE_LIMITS=20:1,100:120` (development and personal key limits).

## Files
| File | What it does |
|---|---|
| `fetch_ddragon.py` | Downloads all Data Dragon JSON (+ optional icons with `--images`) to `data/ddragon/<version>/<lang>/`, plus queue, map and mode lookup tables. No key needed. |
| `fetch_riot.py` | `RiotClient` (key loading, routing, rate limiter, retries) + a script that pulls one player's account, rank, mastery and matches to `data/riot/`. |
| `crawl_matches.py` | Master+ match crawler. Steps: `matches`, `timelines`, `status`. Resumable. Output in `data/riot/crawl/` (gzipped JSON + `index.csv`). |
| `build_champion_tables.py` | Builds champion CSV tables from Data Dragon into `data/tables/`. |
| `PROJECT_LOG.md` | This file. |

All of `data/` is gitignored. Everything in it can be rebuilt with the scripts above.

## Data built so far
### Match crawl (`data/riot/crawl/`)
- Ranked solo (queue 420), NA, **Master+ only**, 14-day window **2026-10-20 to 2026-11-03**.
- Quotas: Challenger 2,000 / Grandmaster 3,000 / Master 5,000 valid matches (10k total).
  Tiers are crawled in that order. Each match is tagged with the tier of the player it was found through,
  so the tag is approximate, since Master+ games mix tiers.
- Remakes (under 5 min or early surrender) are excluded and replaced.
- Patches in the sample: almost all 16.21, about 2% on 16.20.
- **Matches step complete (2026-11-03):** 10,000 valid matches (2,000 / 3,000 / 5,000) + 120 remakes excluded.
  842 seed players used (Challenger 197/300, Grandmaster 308/700, Master 358/10,000). No second pass needed.
  Game lengths 5-58 min (median 27). Patch 16.21: 9,824, patch 16.20: 176. Size 0.12 GB.
  **Timelines deliberately deferred (user decision, 2026-11-03).** Fetch within a few weeks: Riot keeps match data for a limited time.
- Files: `matches/<id>.json.gz` (~12 KB each), `timelines/<id>.json.gz` (~68 KB each), `index.csv`.

### Champion tables (`data/tables/`, from Data Dragon 16.22.1)
| Table | Rows | Contents |
|---|---|---|
| `champion_tags.csv` | 173 | key, id, name, primary_tag, secondary_tag, 20 base stats, ad_per_level_source |
| `champion_spells.csv` | 865 | key, id, slot (P/Q/W/E/R), spell_id, name, max_rank, cost_type, cooldown/cost/range per rank (r1-r6), description |
| `champion_stats_by_level.csv` | 3,114 | key, id, level 1-18, hp, regen, resource, AD, armor, MR, attack speed, move speed, range, crit |

- `key` = `championId` in match data, so it's the join column.
- Stats by level use League's growth formula: `base + per_level * (n-1) * (0.7025 + 0.0175*(n-1))`.
  Attack speed: `base * (1 + pct/100 * same multiplier)`. This is approximate, because the true formula
  uses an "attack speed ratio" that Data Dragon doesn't provide.

## Decisions
- Master+ only, split 5k / 3k / 2k: every tier gets enough matches to analyse on its own.
- Timelines are a separate crawl step: needed for build order, skill order and gold/XP over time.
- Crawl files are gzipped compact JSON (`fetch_riot.py` single-player pulls stay as indented JSON).
- CSV for tables for now (SQLite could come later).
- Commit to `main` only for small changes; use branches for multi-session work.

## Known issues / data gaps
1. **Data Dragon `attackdamageperlevel` = 0 for every champion** in 2026 patches (checked 16.10 and 16.22;
   15.24 is fine). `build_champion_tables.py` fills values from 15.24.1 and marks them in `ad_per_level_source`.
   **Locke** (newer than 15.24) has no value.
2. **No ability scaling or damage numbers in Data Dragon.** `datavalues`, `effect` and `vars` are empty for
   all spells, and tooltips only contain placeholders like `{{ totaldamage }}`.
3. Data Dragon `recommended` builds are empty for every champion.
4. The Master ladder endpoint returned exactly 10,000 players, so it's probably capped. That may bias which
   Master players get sampled.
5. `RiotClient.get` retries connection errors but **not read timeouts**, so the crawl can crash after the
   PC wakes from sleep. Re-running resumes it safely. Worth fixing.
6. The PC must not sleep during crawls (`powercfg /change standby-timeout-ac 0`, then restore it afterwards).

## Next steps / future development
- [x] Finish the match crawl (10k matches).
- [ ] (Deferred) Run `python crawl_matches.py timelines` (~3.5 h) when build order or skill order analysis starts.
- [ ] **Ability scaling data** (AP/AD ratios, base damage per rank) to add to `champion_spells`.
  Likely source: CommunityDragon (`raw.communitydragon.org/latest/game/data/characters/<name>/<name>.bin.json`).
  A first look showed spell entries with `DataValues`, but the format needs investigating.
  CommunityDragon may also fix the attack-damage-per-level gap.
- [ ] Match tables: one row per player per match (champion, role, items, runes, KDA, damage, gold, win),
  joined to the champion tables on `key`.
- [ ] Item, rune and summoner spell lookup tables from Data Dragon.
- [ ] Timeline tables: item purchase order, skill order, gold/XP at 10 and 15 min.
- [ ] Analyses: champion win/pick rates, builds, matchups.
- [ ] Make the retry handle read timeouts (issue 5).

## Session history
- **2026-11-02/03**: Set up the repo and venv. Wrote `fetch_ddragon.py` and `fetch_riot.py` (merged via PR #1).
  Wrote `crawl_matches.py` (branch `match_crawler`) and started the 10k Master+ crawl.
  Built the champion tables (branch `champion_tables`, created from `match_crawler`). Started this log.
