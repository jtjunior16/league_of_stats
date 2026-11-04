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
- Dependencies: `requests`, `pandas`, `numpy`, `matplotlib`.
- GitHub: `jtjunior16/league_of_stats`. **All work is committed directly to `main`** (no feature branches or pull requests).

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
| `analyze_team_comps.py` | Team-comp win rates from champion primary/secondary tags. Options `--tier`, `--patch`, `--min-games`. Results in `analyses/team_comps/tables/`. |
| `analyze_team_stats.py` | Win rate vs. team stats (sum and average of the 5 champions), for base stats and at levels 6/12/18 (`--levels`). Reads `team_comps.csv`. Options `--tier`, `--patch`. |
| `plot_team_comps.py` | Charts (PNG) of the team-comp analysis into `analyses/team_comps/figures/`: tag-count panels, tag effects, comp win rate vs. games, comp ranking, two-tag heatmaps (`--heatmaps A:B`). |
| `analyze_champion_groups.py` | Top/bottom 10 champion pairs and trios (`--size 2 3`), with the 2 most common partner-tag combos and synergy vs. individual win rates. Output in `analyses/champion_groups/`. |
| `plot_champion_groups.py` | Pair charts: expected vs. actual win rate (all pairs), top/bottom 10 with partner tags. Output in `analyses/champion_groups/figures/`. |
| `PROJECT_LOG.md` | This file. |

All of `data/` is gitignored. Everything in it can be rebuilt with the scripts above.
**`analyses/` is in git:** one folder per analysis with a README write-up (findings plus embedded charts), `tables/` and `figures/`.
Large intermediate tables (`team_comps.csv`, `team_stats*.csv`, 20k rows each) stay in `data/analysis/` (gitignored).
When adding an analysis, give it its own `analyses/<name>/` folder and README, and add a row to `analyses/README.md`.

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

### Team-comp analysis (`analyses/team_comps/`, first run 2026-11-03, all 10k matches)
- Comp = count of primary tags per team (e.g. `Assassin1-Fighter2-Mage1-Marksman1`). There are 200 distinct comps, and 47 of them have at least 100 games (81% of teams).
- Tables: `team_comps.csv` (20k team rows), `comp_winrates.csv`, `tag_count_winrates.csv`, `matchup_winrates.csv`, `tag_regression.csv`.
- Regression: one row per match, blue win ~ (blue minus red) weighted tag counts (primary 1, secondary 0.5).
- First results: **red side wins 52.7%** (Challenger only: 54.4%). Tag effects are small. Only Marksman is significant (p=0.04, about -2% per extra Marksman-weighted unit). Teams with 0 marksmen win 52.1%, teams with 3+ win 35% (only 108 games). Fighter 3+ wins 53.3%.
  Most single comps have 95% intervals that include 50%, so more matches are needed to rank comps.

### Team base-stat analysis (`analyses/team_stats/`, 2026-11-03)
- Tables: `team_stats.csv` (sum_/avg_ per stat per team), `stat_winrates.csv`, `stat_quintile_winrates.csv`, `stat_regression.csv`.
- Left out: crit and crit_per_level (all 0) and `ad_per_level_source`. Resource stats count Mana champions only (Viego's resource is 10000).
  Locke's AD per level is filled with the Assassin median.
- Results: every effect is about 1 percentage point or less per standard deviation. The smallest p-value of the 19 tests is 0.01, which isn't significant
  after a multiple-testing correction. In the combined model only side matters. Weak trend: more attack range
  (and so less MR growth, correlation 0.86) is slightly worse, which matches the Marksman result.
  Base stats basically don't predict wins at Master+.
- **By level (6 / 12 / 18)**: files with a `_lvl<N>` suffix, plus `stat_level_comparison.csv`. Both teams are assumed to be the same level.
  Still about 1 percentage point per SD at every level. Trends: the effect of HP and HP regen fades with level (+1.0 -> +0.6 points per SD), and MR grows
  (+1.0 -> +1.25, p=0.013 at 18). Mana is slightly negative and attack range -1.2 at all levels (range doesn't grow). Nothing is significant
  after a multiple-testing correction. Mainly this reflects melee/tanky champions vs. ranged mana champions.

### Champion pairs and trios (`analyses/champion_groups/`, 2026-11-03)
- A group = 2 or 3 champions on the same team. Min games 50 for pairs, 10 for trios. Synergy = win rate minus the rate expected from the champions' own
  win rates (log-odds added). `top_bottom.md` is generated by the script.
- **Trios are too sparse in 10k matches.** 143,596 distinct trios, only 148 with 10+ games, max 31. 7 are outside the 95% band, about 7 expected by chance.
  The user chose to keep the trio option for when there's more data (roughly 10 times more matches needed).
- Pairs: 414 with 50+ games, 32 outside the band against 21 expected. Alistar (55.1% on its own) is in 4 of the top 10.
  Against the expected win rate (synergy), only 14 pairs are outside the chance range, against about 21 expected, so there's **no clear evidence of pair synergy**.

## Decisions
- Master+ only, split 5k / 3k / 2k: every tier gets enough matches to analyse on its own.
- Timelines are a separate crawl step: needed for build order, skill order and gold/XP over time.
- Crawl files are gzipped compact JSON (`fetch_riot.py` single-player pulls stay as indented JSON).
- CSV for tables for now (SQLite could come later).
- Work directly on `main` (decided 2026-11-03; all old branches merged and deleted).
- Analysis results and charts go in git (`analyses/`), raw data doesn't (`data/`).

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
- [ ] Analyses: champion win/pick rates, builds, matchups. (Done: team-comp tags, team stats, champion pairs/trios.)
- [ ] **More matches** (roughly 10 times more) to make champion trios usable, then re-run `analyze_champion_groups.py`. Riot keeps match data
  for a limited time, and Master+ in a 14-day window may not have that many games, so a longer window or Diamond+ might be needed.
- [ ] Pairs: a role-aware version (e.g. bot lane duos, jungle + mid), since synergy may only show within lanes.
- [ ] Team comps: try role-aware comps (tag per `teamPosition`), and check the red-side advantage.
- [ ] Make the retry handle read timeouts (issue 5).

## Session history
- **2026-11-02/03**: Set up the repo and venv. Wrote `fetch_ddragon.py` and `fetch_riot.py` (merged via PR #1).
  Wrote `crawl_matches.py` (branch `match_crawler`) and started the 10k Master+ crawl.
  Built the champion tables (branch `champion_tables`, created from `match_crawler`). Started this log.
- **2026-11-03**: Added `analyze_team_comps.py` (team-comp win rate by champion tags).
- **2026-11-03**: Added `analyze_team_stats.py` (team base stats vs. win rate).
- **2026-11-03**: Added level 6/12/18 comparison to `analyze_team_stats.py`.
- **2026-11-03**: Added `plot_team_comps.py` (7 charts for the tag win-rate analysis).
- **2026-11-03**: Moved results and charts to `analyses/` (tracked), with a README write-up per analysis.
- **2026-11-03**: Merged `team_analysis` into `main`, deleted all other branches. From now on all work goes directly on `main`.
- **2026-11-03**: Added `analyze_champion_groups.py` (champion pairs and trios with partner tags).
- **2026-11-03**: Added `plot_champion_groups.py` (2 pair charts).
