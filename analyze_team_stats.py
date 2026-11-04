"""Win rate vs. team stats (sum and average of the 5 champions' stats), at base and at set levels.

Run analyze_team_comps.py first (it builds data/analysis/team_comps.csv). Then:
    python analyze_team_stats.py                      # base stats + levels 6, 12, 18
    python analyze_team_stats.py --levels 1 9 18      # other levels
    python analyze_team_stats.py --tier CHALLENGER --patch 16.21

Base run: the raw columns of champion_tags.csv (base values and per-level growth as separate stats).
Level runs: each champion's actual stats at that level, using League's growth formula
(same as build_champion_tables.py): hp, hp_regen, resource, resource_regen, attack_damage,
armor, magic_resist, attack_speed. move_speed and attack_range don't grow, so they're the
same at every level. Both teams are assumed to be the same level, so this compares champion
scaling, not who is ahead in levels.

Stats come from data/tables/champion_tags.csv. Adjustments:
  * crit and crit_per_level are 0 for every champion, and ad_per_level_source is a
    text label, so these are left out.
  * Resource stats (resource, resource_per_level, resource_regen, resource_regen_per_level)
    mix mana, energy, fury and so on (Viego's value is 10000). They only count Mana
    champions: sum = the team's total mana, avg = average over its mana users.
    n_mana_users is added as a stat of its own.
  * Locke has no attack_damage_per_level, so it's filled with his primary tag's median.

Every team has 5 champions, so avg = sum / 5 for all stats except the resource ones.
Sum and average give the same results apart from those; both are written so either can be used.

Writes to data/analysis/ (level runs get _lvl<N>, filtered runs get a tier/patch suffix):
    team_stats.csv               one row per team: sum_<stat>, avg_<stat>, win
    stat_winrates.csv            per stat: winners vs. losers, "higher stat wins" rate, 1-SD effect
    stat_quintile_winrates.csv   team win rate per quintile of each stat (avg)
    stat_regression.csv          all stats together (blue minus red, standardized)
    stat_level_comparison.csv    one row per stat per level, side by side
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_team_comps import ANALYSIS_DIR, TABLES_DIR, logistic_irls, save, wilson
from build_champion_tables import growth

ROOT = Path(__file__).parent
DDRAGON_DIR = ROOT / "data" / "ddragon"

STATS = [
    "hp", "hp_per_level", "hp_regen", "hp_regen_per_level",
    "resource", "resource_per_level", "resource_regen", "resource_regen_per_level",
    "attack_damage", "attack_damage_per_level", "attack_speed", "attack_speed_per_level_pct",
    "armor", "armor_per_level", "magic_resist", "magic_resist_per_level",
    "move_speed", "attack_range",
]
RESOURCE_STATS = [s for s in STATS if s.startswith("resource")]
ALL_STATS = STATS + ["n_mana_users"]

# Stats at a given level: (name, base column, per-level column). attack_speed is handled separately.
LEVEL_GROWTH = [
    ("hp", "hp", "hp_per_level"),
    ("hp_regen", "hp_regen", "hp_regen_per_level"),
    ("resource", "resource", "resource_per_level"),
    ("resource_regen", "resource_regen", "resource_regen_per_level"),
    ("attack_damage", "attack_damage", "attack_damage_per_level"),
    ("armor", "armor", "armor_per_level"),
    ("magic_resist", "magic_resist", "magic_resist_per_level"),
]
LEVEL_STATS = [n for n, _, _ in LEVEL_GROWTH] + ["attack_speed", "move_speed", "attack_range", "n_mana_users"]
DEFAULT_LEVELS = [6, 12, 18]


# ---------------------------------------------------------------- champion stats

def resource_types():
    """championId -> partype ('Mana', 'Energy', ...) from the latest downloaded Data Dragon."""
    files = sorted(DDRAGON_DIR.glob("*/en_US/champion.json"),
                   key=lambda p: [int(x) for x in p.parts[-3].split(".")])
    if not files:
        sys.exit("No Data Dragon champion.json found. Run fetch_ddragon.py first.")
    data = json.loads(files[-1].read_text(encoding="utf-8"))["data"]
    return {int(c["key"]): c["partype"] for c in data.values()}


def load_champion_stats():
    champs = pd.read_csv(TABLES_DIR / "champion_tags.csv", encoding="utf-8-sig")
    champs["partype"] = champs["key"].map(resource_types())
    missing = champs["attack_damage_per_level"].isna()
    if missing.any():
        medians = champs.groupby("primary_tag")["attack_damage_per_level"].median()
        champs.loc[missing, "attack_damage_per_level"] = champs.loc[missing, "primary_tag"].map(medians)
        print(f"  Filled attack_damage_per_level with tag median for: {', '.join(champs.loc[missing, 'name'])}")
    mana = champs["partype"] == "Mana"
    champs.loc[~mana, RESOURCE_STATS] = np.nan
    champs["n_mana_users"] = mana.astype(int)
    return champs.set_index("name")[ALL_STATS]


def stats_at_level(base, level):
    """Champion stats at one level from base + per-level columns. NaN (non-mana resource) stays NaN."""
    g = growth(level)
    out = pd.DataFrame(index=base.index)
    for name, base_col, per_col in LEVEL_GROWTH:
        out[name] = base[base_col] + base[per_col] * g
    out["attack_speed"] = base["attack_speed"] * (1 + base["attack_speed_per_level_pct"] / 100 * g)
    for col in ["move_speed", "attack_range", "n_mana_users"]:
        out[col] = base[col]
    return out


def build_team_stats(teams, champ_stats):
    stats = list(champ_stats.columns)
    """Explode each team to its 5 champions, attach stats, then sum and average per team."""
    long = teams[["match_id", "team_id", "champions"]].assign(
        champion=teams["champions"].str.split("|")).explode("champion")
    unknown = set(long["champion"]) - set(champ_stats.index)
    if unknown:
        print(f"  WARNING: no stats for {sorted(unknown)}")
    long = long.join(champ_stats, on="champion")
    g = long.groupby(["match_id", "team_id"])[stats]
    sums = g.sum(min_count=0).add_prefix("sum_")       # NaN resource of non-mana champs counts as 0
    avgs = g.mean().add_prefix("avg_")                 # resource avg is over mana users only
    keep = ["match_id", "team_id", "tier", "patch", "blue_side", "win", "comp_key"]
    return teams[keep].join(pd.concat([sums, avgs], axis=1), on=["match_id", "team_id"])


# ---------------------------------------------------------------- analyses

def blue_red(team_stats):
    blue = team_stats[team_stats["blue_side"] == 1].set_index("match_id")
    red = team_stats[team_stats["blue_side"] == 0].set_index("match_id").loc[blue.index]
    return blue, red


def p_value(z):
    return math.erfc(abs(z) / math.sqrt(2))


def stat_winrates(team_stats, stats):
    """Per stat: winner vs. loser means, 'team with the higher stat wins' rate, and a 1-SD logistic effect."""
    blue, red = blue_red(team_stats)
    winners = team_stats[team_stats["win"] == 1]
    losers = team_stats[team_stats["win"] == 0]
    rows = []
    for stat in stats:
        s, a = f"sum_{stat}", f"avg_{stat}"
        row = {"stat": stat,
               "winner_sum": winners[s].mean(), "loser_sum": losers[s].mean(),
               "winner_avg": winners[a].mean(), "loser_avg": losers[a].mean()}

        # Match level: does the team with the higher sum win more?
        diff = blue[s].to_numpy(float) - red[s].to_numpy(float)
        blue_win = blue["win"].to_numpy(float)
        decided = diff != 0
        higher_wins = int(((diff > 0) & (blue_win == 1)).sum() + ((diff < 0) & (blue_win == 0)).sum())
        n = int(decided.sum())
        low, high = wilson(higher_wins, n)
        row.update({"matches_with_difference": n,
                    "higher_sum_win_rate": higher_wins / n if n else float("nan"),
                    "higher_ci_low": low, "higher_ci_high": high})

        # Logistic: blue win ~ blue side + (blue - red) sum, scaled to 1 SD of the difference.
        sd = diff.std()
        if sd > 0:
            X = np.column_stack([np.ones(len(diff)), diff / sd])
            beta, se = logistic_irls(X, blue_win)
            row.update({"coef_per_sd": beta[1], "std_err": se[1], "p_value": p_value(beta[1] / se[1]),
                        "win_rate_change_per_sd": beta[1] / 4,  # logistic slope near 50%
                        "sd_of_difference": sd})
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values("p_value")


def stat_quintiles(team_stats, stats):
    rows = []
    for stat in stats:
        col = f"avg_{stat}"
        values = team_stats[col]
        bins = pd.qcut(values, 5, duplicates="drop")
        for interval, group in team_stats.groupby(bins, observed=True):
            wins, games = int(group["win"].sum()), len(group)
            low, high = wilson(wins, games)
            rows.append({"stat": stat, "bin": str(interval), "bin_low": interval.left,
                         "bin_high": interval.right, "games": games, "win_rate": wins / games,
                         "ci_low": low, "ci_high": high})
    return pd.DataFrame(rows)


def stat_regression(team_stats, stats):
    """All stats in one logistic model, one row per match, standardized blue-minus-red sums."""
    blue, red = blue_red(team_stats)
    cols = [f"sum_{s}" for s in stats]
    diff = blue[cols].to_numpy(float) - red[cols].to_numpy(float)
    sd = diff.std(axis=0)
    keep = sd > 0
    X = np.column_stack([np.ones(len(diff)), diff[:, keep] / sd[keep]])
    rank = np.linalg.matrix_rank(X)
    if rank < X.shape[1]:
        print(f"  WARNING: stat features are collinear (rank {rank} of {X.shape[1]})")
    beta, se = logistic_irls(X, blue["win"].to_numpy(float))
    out = pd.DataFrame({"term": ["blue_side (intercept)"] + [c for c, k in zip(cols, keep) if k],
                        "coef_per_sd": beta, "std_err": se})
    out["z"] = out["coef_per_sd"] / out["std_err"]
    out["p_value"] = [p_value(z) for z in out["z"]]
    out["n_matches"] = len(blue)
    corr = np.abs(np.corrcoef(diff[:, keep], rowvar=False))
    np.fill_diagonal(corr, 0)
    out["max_abs_corr_with_other_stat"] = [float("nan")] + list(corr.max(axis=0))
    return out


# ---------------------------------------------------------------- main

def filter_teams(team_stats, args):
    suffix = ""
    if args.tier:
        team_stats = team_stats[team_stats["tier"] == args.tier]
        suffix += f"_{args.tier.lower()}"
    if args.patch:
        team_stats = team_stats[team_stats["patch"].astype(str) == args.patch]
        suffix += f"_{args.patch.replace('.', '-')}"
    if team_stats.empty:
        sys.exit("No matches left after filtering.")
    return team_stats, suffix


def run(teams, champ_stats, label, file_suffix, args):
    """Build team stats for one stat set, write the 4 analysis tables, return the per-stat table."""
    stats = list(champ_stats.columns)
    team_stats = build_team_stats(teams, champ_stats)
    save(team_stats, "team_stats.csv", file_suffix)
    team_stats, filter_suffix = filter_teams(team_stats, args)
    suffix = file_suffix + filter_suffix
    single = stat_winrates(team_stats, stats)
    save(single, "stat_winrates.csv", suffix)
    save(stat_quintiles(team_stats, stats), "stat_quintile_winrates.csv", suffix)
    save(stat_regression(team_stats, stats), "stat_regression.csv", suffix)
    return single.assign(level=label), filter_suffix


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tier", choices=["CHALLENGER", "GRANDMASTER", "MASTER"])
    parser.add_argument("--patch", help="e.g. 16.21")
    parser.add_argument("--levels", type=int, nargs="+", default=DEFAULT_LEVELS,
                        help="champion levels to compare (default: 6 12 18)")
    args = parser.parse_args()
    if any(not 1 <= lv <= 18 for lv in args.levels):
        sys.exit("Levels must be between 1 and 18.")

    comps_path = ANALYSIS_DIR / "team_comps.csv"
    if not comps_path.exists():
        sys.exit("data/analysis/team_comps.csv not found. Run analyze_team_comps.py first.")
    teams = pd.read_csv(comps_path, encoding="utf-8-sig")
    base = load_champion_stats()

    print("Base stats (champion_tags.csv columns):")
    results = [run(teams, base, "base", "", args)[0]]
    for level in args.levels:
        print(f"Level {level}:")
        single, filter_suffix = run(teams, stats_at_level(base, level), str(level), f"_lvl{level}", args)
        results.append(single)

    # Side by side: each stat at each level.
    compare = pd.concat(results[1:])
    cols = ["stat", "level", "winner_avg", "loser_avg", "higher_sum_win_rate",
            "higher_ci_low", "higher_ci_high", "coef_per_sd", "win_rate_change_per_sd", "p_value"]
    compare = compare[cols].sort_values(["stat", "level"], key=lambda c: c.astype(int) if c.name == "level" else c)
    save(compare, "stat_level_comparison.csv", filter_suffix)

    pd.set_option("display.width", 200)
    print(f"\nWin rate change per 1 SD of team difference (p-value in brackets), {len(args.levels)} levels:")
    pivot = compare.assign(cell=[f"{d:+.4f} ({p:.3f})" for d, p in
                                 zip(compare["win_rate_change_per_sd"], compare["p_value"])])
    print(pivot.pivot(index="stat", columns="level", values="cell")[[str(lv) for lv in args.levels]]
          .loc[LEVEL_STATS].to_string())
    print("\nTeam with the higher stat total wins:")
    print(compare.pivot(index="stat", columns="level", values="higher_sum_win_rate")
          [[str(lv) for lv in args.levels]].loc[LEVEL_STATS].to_string(float_format="{:.3f}".format))


if __name__ == "__main__":
    main()
