"""Best and worst champion groups (pairs or trios on the same team), and what they're played with.

Run analyze_team_comps.py first (it builds data/analysis/team_comps.csv). Then:
    python analyze_champion_groups.py                 # pairs and trios
    python analyze_champion_groups.py --size 3 --min-games 15

A group is any 2 or 3 champions on the same team (each team of 5 has 10 pairs and 10 trios).
For each group: games, win rate (95% interval), share of teams that had it, and "synergy":
group win rate minus the win rate expected from its champions' individual win rates. The
expected rate adds up each champion's edge over 50% on the log-odds scale, so a 55% champion
with a 50% champion is expected to win 55% together. High synergy = the group wins more than
its champions' own strength explains.

For the top and bottom 10 groups, the 2 most common "partner tags" are listed: the primary
tags of the team's other champions (3 for pairs, 2 for trios), each with games and win rate.

Trios are very sparse in 10k matches (only about 150 have 10+ games), so the trio results are
mostly noise; the script supports them so they can be re-run when there's more data.

Writes to analyses/champion_groups/ (in git):
    tables/groups_size<N>.csv          every group with at least --min-games games
    tables/top_bottom_size<N>.csv      top and bottom 10 with their top 2 partner tags
    top_bottom.md                      the same tables as Markdown (for GitHub)
"""

import argparse
import math
from collections import Counter, defaultdict
from itertools import combinations

import pandas as pd

from analyze_team_comps import ANALYSIS_DIR, ROOT, TABLES_DIR, save, wilson

RESULTS_DIR = ROOT / "analyses" / "champion_groups"
DEFAULT_MIN_GAMES = {2: 50, 3: 10}
N_TOP = 10
N_PARTNER_TAGS = 2


def load_teams():
    path = ANALYSIS_DIR / "team_comps.csv"
    if not path.exists():
        raise SystemExit("data/analysis/team_comps.csv not found. Run analyze_team_comps.py first.")
    teams = pd.read_csv(path, encoding="utf-8-sig")
    teams["champ_list"] = teams["champions"].str.split("|")
    return teams


def primary_tags():
    champs = pd.read_csv(TABLES_DIR / "champion_tags.csv", encoding="utf-8-sig")
    return dict(zip(champs["name"], champs["primary_tag"]))


def champion_winrates(teams):
    games, wins = Counter(), Counter()
    for champs, win in zip(teams["champ_list"], teams["win"]):
        for c in champs:
            games[c] += 1
            wins[c] += win
    return {c: wins[c] / games[c] for c in games}


def expected_win_rate(group, champ_wr):
    """Win rate if the champions' strengths simply add up (log-odds), with no synergy."""
    logit = sum(math.log(champ_wr[c] / (1 - champ_wr[c])) for c in group)
    return 1 / (1 + math.exp(-logit))


def count_groups(teams, size, tags):
    """Per group: games, wins, and per partner-tag combo: games, wins."""
    games, wins = Counter(), Counter()
    partner = defaultdict(Counter)
    partner_wins = defaultdict(Counter)
    for champs, win in zip(teams["champ_list"], teams["win"]):
        champs = sorted(champs)
        for group in combinations(champs, size):
            games[group] += 1
            wins[group] += win
            others = " + ".join(sorted(tags[c] for c in champs if c not in group))
            partner[group][others] += 1
            partner_wins[group][others] += win
    return games, wins, partner, partner_wins


def group_table(games, wins, champ_wr, n_teams, min_games):
    rows = []
    for group, n in games.items():
        if n < min_games:
            continue
        w = wins[group]
        low, high = wilson(w, n)
        expected = expected_win_rate(group, champ_wr)
        rows.append({"group": " + ".join(group), "games": n, "wins": w, "win_rate": w / n,
                     "ci_low": low, "ci_high": high, "team_share": n / n_teams,
                     "expected_win_rate": expected, "synergy": w / n - expected,
                     "_key": group})
    df = pd.DataFrame(rows)
    return df.sort_values(["win_rate", "games"], ascending=[False, False]).reset_index(drop=True)


def top_bottom(df, partner, partner_wins):
    top = df.head(N_TOP).assign(rank_type="top", rank=range(1, N_TOP + 1))
    bottom = (df.sort_values(["win_rate", "games"], ascending=[True, False]).head(N_TOP)
                .assign(rank_type="bottom", rank=range(1, N_TOP + 1)))
    out = pd.concat([top, bottom]).reset_index(drop=True)
    for i in range(1, N_PARTNER_TAGS + 1):
        out[f"partner_tags_{i}"], out[f"partner_games_{i}"], out[f"partner_win_rate_{i}"] = "", 0, float("nan")
    for idx, group in out["_key"].items():
        for i, (tags, n) in enumerate(partner[group].most_common(N_PARTNER_TAGS), 1):
            out.loc[idx, f"partner_tags_{i}"] = tags
            out.loc[idx, f"partner_games_{i}"] = n
            out.loc[idx, f"partner_win_rate_{i}"] = partner_wins[group][tags] / n
    return out


def markdown(tb, size, min_games, n_groups, n_total):
    name = {2: "pairs", 3: "trios"}[size]
    lines = [f"## Champion {name} (at least {min_games} games: {n_groups:,} of {n_total:,} distinct {name})", ""]
    if size == 3:
        lines += ["> **Exploratory.** Trios are very rare in 10k matches, so these rankings are mostly chance. "
                  "Check the 95% interval before reading anything into them.", ""]
    for rank_type, title in [("top", "Top 10"), ("bottom", "Bottom 10")]:
        lines += [f"### {title}", "",
                  "| # | Champions | Games | Win rate (95% interval) | Synergy | Most common partner tags | 2nd most common |",
                  "|---|---|---|---|---|---|---|"]
        for _, r in tb[tb["rank_type"] == rank_type].iterrows():
            partners = []
            for i in (1, 2):
                if r[f"partner_tags_{i}"]:
                    partners.append(f"{r[f'partner_tags_{i}']}: {r[f'partner_win_rate_{i}']:.0%} "
                                    f"({int(r[f'partner_games_{i}'])})")
                else:
                    partners.append("")
            lines.append(f"| {r['rank']} | {r['group']} | {r['games']} | {r['win_rate']:.1%} "
                         f"({r['ci_low']:.0%}-{r['ci_high']:.0%}) | {r['synergy']:+.1%} | "
                         f"{partners[0]} | {partners[1]} |")
        lines.append("")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--size", type=int, choices=[2, 3], nargs="+", default=[2, 3])
    parser.add_argument("--min-games", type=int, help="default: 50 for pairs, 10 for trios")
    args = parser.parse_args()

    teams = load_teams()
    tags = primary_tags()
    champ_wr = champion_winrates(teams)
    md = ["# Champion pairs and trios: top and bottom 10", "",
          f"Generated by `analyze_champion_groups.py` from {len(teams):,} teams. "
          "Synergy = group win rate minus the rate expected from its champions' own win rates "
          "(their edges over 50% added on the log-odds scale). "
          "Partner tags = primary tags of the team's other champions: win rate (games).", ""]

    for size in args.size:
        min_games = args.min_games or DEFAULT_MIN_GAMES[size]
        print(f"Size {size} (min {min_games} games):")
        games, wins, partner, partner_wins = count_groups(teams, size, tags)
        df = group_table(games, wins, champ_wr, len(teams), min_games)
        tb = top_bottom(df, partner, partner_wins)
        tables = RESULTS_DIR / "tables"
        save(df.drop(columns="_key"), f"groups_size{size}.csv", folder=tables)
        save(tb.drop(columns="_key"), f"top_bottom_size{size}.csv", folder=tables)
        md += markdown(tb, size, min_games, len(df), len(games))

        z = (df["win_rate"] - 0.5).abs() / (0.25 / df["games"]) ** 0.5
        print(f"  {len(games):,} distinct groups, {len(df):,} with >= {min_games} games; "
              f"{(z > 1.96).sum()} outside the 95% band (about {0.05 * len(df):.0f} expected by chance)")

    path = RESULTS_DIR / "top_bottom.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
