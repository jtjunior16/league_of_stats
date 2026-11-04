"""Win rate by team composition, using champion class tags.

Run crawl_matches.py and build_champion_tables.py first. Then:
    python analyze_team_comps.py                       # all matches
    python analyze_team_comps.py --tier CHALLENGER     # one tier (tier of the seed player)
    python analyze_team_comps.py --patch 16.21 --min-games 50

A team's "comp" is the count of its champions' primary tags, for example
Assassin1-Fighter2-Mage1-Marksman1. Secondary tags are used in the regression
as weighted counts (primary tag = 1.0, secondary tag = 0.5).

Writes data/analysis/team_comps.csv (one row per team per match, all matches; gitignored),
and results to analyses/team_comps/tables/ (in git; filtered runs get a suffix, e.g. comp_winrates_challenger.csv):
    comp_winrates.csv       win rate per comp, with Wilson 95% interval
    tag_count_winrates.csv  win rate by number of champions with each primary tag
    matchup_winrates.csv    comp vs. comp win rate (sparse; mirror matchups dropped)
    tag_regression.csv      logistic regression of blue win on tag differences

Every match has one winner and one loser, so the overall team win rate is 50%
by construction. In the regression each match is one row (blue minus red features),
so the two mirrored team rows aren't counted as independent samples.
"""

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from crawl_matches import read_json_gz, paths

ROOT = Path(__file__).parent
TABLES_DIR = ROOT / "data" / "tables"
ANALYSIS_DIR = ROOT / "data" / "analysis"            # large intermediate tables (gitignored)
RESULTS_DIR = ROOT / "analyses" / "team_comps"       # results, charts and write-up (in git)

TAGS = ["Assassin", "Fighter", "Mage", "Marksman", "Support", "Tank"]
SECONDARY_WEIGHT = 0.5
BLUE, RED = 100, 200


# ---------------------------------------------------------------- helpers

def wilson(wins, games, z=1.96):
    """Wilson score interval for a win rate. Works better than +/- 2 SE for small samples."""
    if games == 0:
        return float("nan"), float("nan")
    p = wins / games
    denom = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / denom
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denom
    return centre - half, centre + half


def save(df, name, suffix="", folder=RESULTS_DIR / "tables"):
    folder.mkdir(parents=True, exist_ok=True)
    stem, ext = name.rsplit(".", 1)
    path = folder / f"{stem}{suffix}.{ext}"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  {path.relative_to(ROOT)}: {len(df)} rows")


def comp_key(primary_tags):
    counts = Counter(primary_tags)
    return "-".join(f"{tag}{counts[tag]}" for tag in TAGS if counts[tag])


# ---------------------------------------------------------------- team table

def load_champions():
    champs = pd.read_csv(TABLES_DIR / "champion_tags.csv", encoding="utf-8-sig",
                         usecols=["key", "name", "primary_tag", "secondary_tag"])
    champs["secondary_tag"] = champs["secondary_tag"].fillna("")
    return {int(r.key): r for r in champs.itertuples(index=False)}


def build_team_table():
    """One row per team per valid match."""
    p = paths()
    index = pd.read_csv(p["index"])
    index = index[index["status"] == "ok"]
    champs = load_champions()
    unknown = Counter()
    rows = []

    for n, m in enumerate(index.itertuples(index=False), 1):
        data = read_json_gz(p["matches"] / f"{m.match_id}.json.gz")
        teams = {BLUE: [], RED: []}
        wins = {}
        for part in data["info"]["participants"]:
            teams[part["teamId"]].append(part["championId"])
            wins[part["teamId"]] = part["win"]

        for team_id, champ_ids in teams.items():
            known = [champs[c] for c in champ_ids if c in champs]
            unknown.update(c for c in champ_ids if c not in champs)
            row = {
                "match_id": m.match_id, "tier": m.tier, "patch": m.patch,
                "team_id": team_id, "blue_side": int(team_id == BLUE),
                "win": int(wins[team_id]), "n_champs": len(champ_ids),
                "champions": "|".join(c.name for c in known),
                "comp_key": comp_key(c.primary_tag for c in known),
            }
            for tag in TAGS:
                row[f"n_{tag}"] = sum(c.primary_tag == tag for c in known)
                row[f"sec_{tag}"] = sum(c.secondary_tag == tag for c in known)
                row[f"w_{tag}"] = row[f"n_{tag}"] + SECONDARY_WEIGHT * row[f"sec_{tag}"]
            rows.append(row)

        if n % 2000 == 0:
            print(f"  loaded {n}/{len(index)} matches")

    if unknown:
        print(f"  WARNING: champion IDs not in champion_tags.csv: {dict(unknown)}")
    return pd.DataFrame(rows)


def check(teams):
    """Sanity checks: 5 champs per team, exactly one winner per match."""
    bad_size = (teams["n_champs"] != 5).sum()
    winners = teams.groupby("match_id")["win"].sum()
    bad_win = (winners != 1).sum()
    blue = teams[teams["blue_side"] == 1]
    print(f"  {len(teams)} team rows from {teams['match_id'].nunique()} matches. "
          f"Teams without 5 champs: {bad_size}. Matches without exactly 1 winner: {bad_win}.")
    print(f"  Overall win rate {teams['win'].mean():.3f}, blue side win rate {blue['win'].mean():.3f}")


# ---------------------------------------------------------------- analyses

def comp_winrates(teams, min_games):
    g = teams.groupby("comp_key")["win"].agg(games="count", wins="sum").reset_index()
    g["win_rate"] = g["wins"] / g["games"]
    ci = [wilson(w, n) for w, n in zip(g["wins"], g["games"])]
    g["ci_low"] = [c[0] for c in ci]
    g["ci_high"] = [c[1] for c in ci]
    g["pick_share"] = g["games"] / len(teams)
    g["enough_games"] = g["games"] >= min_games
    return g.sort_values(["enough_games", "ci_low"], ascending=False)


def tag_count_winrates(teams):
    rows = []
    for tag in TAGS:
        counts = teams[f"n_{tag}"].clip(upper=3)
        for count, group in teams.groupby(counts):
            wins, games = int(group["win"].sum()), len(group)
            low, high = wilson(wins, games)
            rows.append({"tag": tag, "count": "3+" if count == 3 else str(count),
                         "games": games, "wins": wins, "win_rate": wins / games,
                         "ci_low": low, "ci_high": high, "pick_share": games / len(teams)})
    return pd.DataFrame(rows)


def matchup_winrates(teams, min_games):
    blue = teams[teams["blue_side"] == 1].set_index("match_id")
    red = teams[teams["blue_side"] == 0].set_index("match_id")
    opp = {"comp_key": "comp_key_opp"}
    pairs = blue[["comp_key", "win"]].join(red[["comp_key"]].rename(columns=opp))
    flipped = red[["comp_key", "win"]].join(blue[["comp_key"]].rename(columns=opp))
    both = pd.concat([pairs, flipped])
    both = both[both["comp_key"] != both["comp_key_opp"]]  # mirrors are 50% by definition
    g = (both.groupby(["comp_key", "comp_key_opp"])["win"]
             .agg(games="count", wins="sum").reset_index()
             .rename(columns={"comp_key_opp": "opponent_comp_key"}))
    g = g[g["games"] >= min_games].copy()
    g["win_rate"] = g["wins"] / g["games"]
    ci = [wilson(w, n) for w, n in zip(g["wins"], g["games"])]
    g["ci_low"] = [c[0] for c in ci]
    g["ci_high"] = [c[1] for c in ci]
    return g.sort_values("ci_low", ascending=False)


def logistic_irls(X, y, iterations=50):
    """Plain logistic regression by iteratively reweighted least squares. Returns (beta, standard errors)."""
    beta = np.zeros(X.shape[1])
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-X @ beta))
        w = p * (1 - p)
        hessian = X.T @ (X * w[:, None])
        step = np.linalg.solve(hessian, X.T @ (y - p))
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = 1 / (1 + np.exp(-X @ beta))
    cov = np.linalg.inv(X.T @ (X * (p * (1 - p))[:, None]))
    return beta, np.sqrt(np.diag(cov))


def tag_regression(teams):
    """One row per match: blue win ~ intercept (blue side) + (blue - red) weighted tag counts.

    Coefficients are the change in log-odds of winning per extra weighted tag
    (an extra primary champion of that tag = +1, an extra secondary = +0.5)
    compared with the other team.
    """
    cols = [f"w_{tag}" for tag in TAGS]
    blue = teams[teams["blue_side"] == 1].set_index("match_id")
    red = teams[teams["blue_side"] == 0].set_index("match_id").loc[blue.index]
    diff = blue[cols].to_numpy(float) - red[cols].to_numpy(float)
    X = np.column_stack([np.ones(len(diff)), diff])
    y = blue["win"].to_numpy(float)
    if np.linalg.matrix_rank(X) < X.shape[1]:
        print("  WARNING: regression features are collinear; results unreliable")
    beta, se = logistic_irls(X, y)
    names = ["blue_side (intercept)"] + cols
    out = pd.DataFrame({"term": names, "coef": beta, "std_err": se})
    out["z"] = out["coef"] / out["std_err"]
    out["p_value"] = [math.erfc(abs(z) / math.sqrt(2)) for z in out["z"]]
    out["odds_ratio"] = np.exp(out["coef"])
    out["n_matches"] = len(y)
    return out


# ---------------------------------------------------------------- main

def print_comps(comps, label, n=10):
    print(f"\n{label}")
    print(comps.to_string(index=False, columns=["comp_key", "games", "win_rate", "ci_low", "ci_high"],
                          formatters={c: "{:.3f}".format for c in ["win_rate", "ci_low", "ci_high"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tier", choices=["CHALLENGER", "GRANDMASTER", "MASTER"])
    parser.add_argument("--patch", help="e.g. 16.21")
    parser.add_argument("--min-games", type=int, default=100, help="min games for a comp to be ranked")
    parser.add_argument("--min-matchup-games", type=int, default=30)
    args = parser.parse_args()

    print("Building team table...")
    teams = build_team_table()
    save(teams, "team_comps.csv", folder=ANALYSIS_DIR)

    suffix = ""
    if args.tier:
        teams = teams[teams["tier"] == args.tier]
        suffix += f"_{args.tier.lower()}"
    if args.patch:
        teams = teams[teams["patch"].astype(str) == args.patch]
        suffix += f"_{args.patch.replace('.', '-')}"
    if teams.empty:
        sys.exit("No matches left after filtering.")
    check(teams)

    print("Writing analysis tables...")
    comps = comp_winrates(teams, args.min_games)
    save(comps, "comp_winrates.csv", suffix)
    save(tag_count_winrates(teams), "tag_count_winrates.csv", suffix)
    save(matchup_winrates(teams, args.min_matchup_games), "matchup_winrates.csv", suffix)
    regression = tag_regression(teams)
    save(regression, "tag_regression.csv", suffix)

    ranked = comps[comps["enough_games"]]
    print(f"\n{comps['comp_key'].nunique()} distinct comps, {len(ranked)} with >= {args.min_games} games "
          f"(covering {ranked['games'].sum() / len(teams):.0%} of teams).")
    print_comps(ranked.head(10), "Best comps (sorted by lower bound of 95% interval):")
    print_comps(ranked.sort_values("ci_high").head(10), "Worst comps (sorted by upper bound):")
    print("\nTag regression (blue minus red weighted tag counts):")
    print(regression.to_string(index=False, float_format="{:.4f}".format))


if __name__ == "__main__":
    main()
