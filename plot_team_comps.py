"""Charts for the team-comp (champion tag) win-rate analysis.

Run analyze_team_comps.py first. Then:
    python plot_team_comps.py
    python plot_team_comps.py --tier CHALLENGER   # uses the _challenger tables (run the analysis with --tier first)

Writes PNGs to analyses/team_comps/figures/ (in git):
    1_tag_count_winrates.png   win rate by number of champions with each tag, one panel per tag
    2_tag_effects.png          regression: win-rate change per extra tag, with 95% intervals
    3_comp_funnel.png          comp win rate vs. games, with the 95% band expected from chance
    4_comp_ranking.png         ranked comps (>= min games) with 95% intervals
    5_heatmap_<a>_<b>.png      win rate for each combination of two tag counts

Every chart is centred on 50% and shows 95% intervals: almost every effect here is
within a few points of 50%, so small samples would otherwise look like real effects.
"""

import argparse
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.text import Text
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.ticker import FuncFormatter, LogLocator

from analyze_team_comps import ANALYSIS_DIR, RESULTS_DIR, ROOT, TAGS, wilson

FIG_DIR = RESULTS_DIR / "figures"
RESULTS_TABLES = RESULTS_DIR / "tables"

# Colours: the dataviz reference palette (light mode).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"     # single series; "wins more than 50%"
RED = "#e34948"      # "wins less than 50%"
NEUTRAL = "#f0efec"  # diverging midpoint (50%)
NO_DATA = "#e1e0d9"

DIVERGING = LinearSegmentedColormap.from_list("winrate", [RED, NEUTRAL, BLUE])
PCT = FuncFormatter(lambda v, _: f"{v:.0%}")
MIN_TAG_GAMES = 30  # tag-count points with fewer games are left out of chart 1


def style():
    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans"],
        "font.size": 10,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.labelcolor": INK_2, "axes.titlecolor": INK, "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "xtick.major.size": 0, "ytick.major.size": 0,
        "legend.frameon": False,
    })


def fifty_line(ax, axis="y"):
    (ax.axhline if axis == "y" else ax.axvline)(0.5, color=INK_2, linewidth=0.9, zorder=1)


def finish(fig, name, title, subtitle, note=None):
    height = fig.get_figheight()
    fig.text(0.01, 1 - 0.1 / height, title, ha="left", va="top", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 1 - 0.42 / height, subtitle, ha="left", va="top", fontsize=10, color=INK_2)
    if note:
        fig.text(0.01, 0.005, note, ha="left", va="bottom", fontsize=8.5, color=MUTED)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path.relative_to(ROOT)}")


def comp_label(key):
    """'Fighter2-Mage1-Marksman1-Support1' -> '2 Fighter · Mage · Marksman · Support'."""
    parts = []
    for part in key.split("-"):
        tag, n = part.rstrip("0123456789"), part[len(part.rstrip("0123456789")):]
        parts.append(f"{n} {tag}" if n != "1" else tag)
    return " · ".join(parts)


def colour_by_ci(low, high):
    return BLUE if low > 0.5 else RED if high < 0.5 else MUTED


def load(name, suffix, folder=RESULTS_TABLES):
    path = folder / f"{name}{suffix}.csv"
    if not path.exists():
        sys.exit(f"{path} not found. Run analyze_team_comps.py (with the same --tier/--patch) first.")
    return pd.read_csv(path, encoding="utf-8-sig")


# ---------------------------------------------------------------- 1. tag counts

def plot_tag_counts(tags, sub):
    fig, axes = plt.subplots(2, 3, figsize=(12, 7.2), sharey=True)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.84, bottom=0.1, hspace=0.45, wspace=0.08)
    order = ["0", "1", "2", "3+"]
    dropped = []
    for ax, tag in zip(axes.flat, TAGS):
        d = tags[tags["tag"] == tag].set_index("count").reindex(order).dropna(subset=["games"])
        small = d[d["games"] < MIN_TAG_GAMES]
        dropped += [f"{tag} {c} ({int(g)} games)" for c, g in small["games"].items()]
        d = d[d["games"] >= MIN_TAG_GAMES]
        x = [order.index(c) for c in d.index]
        ax.vlines(x, d["ci_low"], d["ci_high"], color=BLUE, linewidth=2, zorder=2)
        ax.scatter(x, d["win_rate"], s=50, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
        for xi, (c, r) in zip(x, d.iterrows()):
            ax.annotate(f"{int(r['games']):,}", (xi, r["ci_high"]), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=8, color=MUTED)
        fifty_line(ax)
        ax.set_title(tag)
        ax.set_xticks(range(4), order)
        ax.set_xlim(-0.5, 3.5)
        ax.grid(axis="x", visible=False)
        ax.yaxis.set_major_formatter(PCT)
    for ax in axes[1]:
        ax.set_xlabel("Champions with this primary tag on the team")
    for ax in axes[:, 0]:
        ax.set_ylabel("Team win rate")
    note = "Dots: win rate. Lines: 95% interval. Grey numbers: games."
    if dropped:
        note += f" Left out (< {MIN_TAG_GAMES} games): {', '.join(dropped)}."
    finish(fig, "1_tag_count_winrates.png", "Win rate by number of champions with each tag",
           f"Each panel: team win rate for 0, 1, 2 and 3+ champions of that primary tag {sub}", note)


# ---------------------------------------------------------------- 2. regression

def plot_tag_effects(reg, sub):
    d = reg.copy()
    d["label"] = d["term"].str.replace("w_", "", regex=False)
    d.loc[d["term"].str.startswith("blue_side"), "label"] = "Blue side (vs. red)"
    # Logistic slope near 50% is about coef / 4 per unit -> percentage points.
    d["pts"] = d["coef"] / 4
    d["low"] = (d["coef"] - 1.96 * d["std_err"]) / 4
    d["high"] = (d["coef"] + 1.96 * d["std_err"]) / 4
    side = d[d["label"].str.startswith("Blue")]
    tags = d[~d["label"].str.startswith("Blue")].sort_values("pts")
    d = pd.concat([side, tags])
    y = np.arange(len(d))[::-1]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    fig.subplots_adjust(left=0.2, right=0.96, top=0.8, bottom=0.17)
    colours = [BLUE if lo > 0 else RED if hi < 0 else MUTED for lo, hi in zip(d["low"], d["high"])]
    ax.hlines(y, d["low"], d["high"], color=colours, linewidth=2, zorder=2)
    ax.scatter(d["pts"], y, s=55, color=colours, edgecolor=SURFACE, linewidth=2, zorder=3)
    for yi, (_, r), c in zip(y, d.iterrows(), colours):
        ax.annotate(f"{r['pts']:+.1%}".replace("%", " pts"), (r["high"], yi), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.5,
                    color=INK if c != MUTED else MUTED)
    ax.axvline(0, color=INK_2, linewidth=0.9, zorder=1)
    ax.axhline(y[0] - 0.5, color=GRID, linewidth=0.8)
    ax.set_yticks(y, d["label"])
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v * 100:+.0f} pts" if v else "0"))
    ax.set_xlabel("Change in win chance (percentage points, approximate)")
    finish(fig, "2_tag_effects.png", "Effect of one extra tag on win chance",
           f"Per extra champion of that tag (secondary tag = half), compared with the other team {sub}",
           "Logistic regression, one row per match: blue win ~ (blue minus red) tag counts, primary = 1, "
           "secondary = 0.5. Lines: 95% interval. Coloured = interval excludes 0.")


# ---------------------------------------------------------------- 3. funnel

def plot_funnel(comps, total_teams, sub, n_labels=8, min_label_games=30):
    fig, ax = plt.subplots(figsize=(11, 6.4))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.85, bottom=0.13)
    n = np.logspace(0, math.log10(comps["games"].max() * 1.3), 300)
    for z, alpha in [(1.96, 0.55), (3.0, 0.25)]:
        band = z * np.sqrt(0.25 / n)
        ax.fill_between(n, 0.5 - band, 0.5 + band, color=GRID, alpha=alpha, linewidth=0, zorder=0)

    # Two-sided test against 50% (normal approximation) decides the colour.
    z = (comps["win_rate"] - 0.5) / np.sqrt(0.25 / comps["games"])
    colours = np.where(z > 1.96, BLUE, np.where(z < -1.96, RED, MUTED))
    ax.scatter(comps["games"], comps["win_rate"], s=28, c=colours, edgecolor=SURFACE,
               linewidth=1.5, zorder=3, alpha=0.95)
    fifty_line(ax)

    ax.set_xscale("log")
    ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlim(0.8, comps["games"].max() * 1.6)
    ax.set_ylim(-0.02, 1.02)

    # Label comps outside the band that have enough games to matter. Labels go above dots
    # over 50% and below dots under 50%; near the right edge they're right-aligned.
    labelled = comps[(np.abs(z) > 1.96) & (comps["games"] >= min_label_games)].nlargest(n_labels, "games")
    # If a label overlaps one already placed, it moves further from 50% with a leader line.
    right_edge = comps["games"].max() / 3
    renderer = fig.canvas.get_renderer()
    placed = []
    for _, r in labelled.iterrows():
        up = r["win_rate"] > 0.5
        left = r["games"] > right_edge
        for step in range(12):
            dy = (6 + 11 * step) * (1 if up else -1)
            label = ax.annotate(
                comp_label(r["comp_key"]), (r["games"], r["win_rate"]),
                xytext=(-6 if left else 6, dy), textcoords="offset points",
                ha="right" if left else "left", va="bottom" if up else "top", fontsize=8, color=INK_2,
                arrowprops=dict(arrowstyle="-", color=AXIS, linewidth=0.7, shrinkA=0, shrinkB=3) if step else None)
            box = Text.get_window_extent(label, renderer).expanded(1.02, 1.15)  # text only, not the leader line
            if not any(box.overlaps(b) for b in placed):
                placed.append(box)
                break
            label.remove()

    ax.yaxis.set_major_formatter(PCT)
    ax.set_xlabel("Games played with this comp (log scale)")
    ax.set_ylabel("Win rate")
    ax.scatter([], [], s=28, color=BLUE, label="Wins more than chance")
    ax.scatter([], [], s=28, color=RED, label="Wins less than chance")
    ax.scatter([], [], s=28, color=MUTED, label="Within chance")
    ax.legend(loc="upper right", fontsize=9, labelcolor=INK_2)
    finish(fig, "3_comp_funnel.png", "Comp win rate vs. how often it's played",
           f"Shaded: where 95% (dark) and 99.7% (light) of comps would fall if every comp were really 50%. "
           f"{len(comps)} comps, {total_teams:,} teams {sub}",
           "Extreme win rates come almost entirely from rarely played comps. "
           "Coloured = outside the 95% band (expect about 5% of comps there by chance alone). "
           f"Labelled: coloured comps with at least {min_label_games} games.")


# ---------------------------------------------------------------- 4. ranking

def plot_ranking(comps, min_games, sub):
    d = comps[comps["games"] >= min_games].sort_values("win_rate")
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(10, 0.24 * len(d) + 2))
    fig.subplots_adjust(left=0.36, right=0.9, top=1 - 1.15 / (0.24 * len(d) + 2), bottom=0.9 / (0.24 * len(d) + 2))
    colours = [colour_by_ci(lo, hi) for lo, hi in zip(d["ci_low"], d["ci_high"])]
    ax.hlines(y, d["ci_low"], d["ci_high"], color=colours, linewidth=1.6, zorder=2)
    ax.scatter(d["win_rate"], y, s=30, color=colours, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    xmax = d["ci_high"].max() + 0.01
    for yi, (_, r) in zip(y, d.iterrows()):
        ax.text(xmax + 0.005, yi, f"{r['win_rate']:.1%}  ({int(r['games']):,})", va="center",
                fontsize=8, color=INK_2)
    fifty_line(ax, "x")
    ax.set_yticks(y, [comp_label(k) for k in d["comp_key"]], fontsize=8.5)
    ax.set_ylim(-0.7, len(d) - 0.3)
    ax.set_xlim(d["ci_low"].min() - 0.01, xmax + 0.06)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(PCT)
    ax.set_xlabel("Win rate (lines: 95% interval)")
    finish(fig, "4_comp_ranking.png", f"Comps with at least {min_games} games, ranked by win rate",
           f"Right column: win rate (games). Coloured = interval excludes 50% {sub}")


# ---------------------------------------------------------------- 5. heatmaps

def plot_heatmap(teams, tag_a, tag_b, min_games, sub):
    cap = lambda s: s.clip(upper=3)
    g = (teams.assign(a=cap(teams[f"n_{tag_a}"]), b=cap(teams[f"n_{tag_b}"]))
              .groupby(["a", "b"])["win"].agg(games="count", wins="sum"))
    labels = ["0", "1", "2", "3+"]
    rate = np.full((4, 4), np.nan)
    games = np.zeros((4, 4), int)
    for (a, b), r in g.iterrows():
        games[a, b] = r["games"]
        rate[a, b] = r["wins"] / r["games"]
    shown = np.where(games >= min_games, rate, np.nan)

    fig, ax = plt.subplots(figsize=(7.4, 6.4))
    fig.subplots_adjust(left=0.15, right=0.9, top=0.8, bottom=0.13)
    spread = 0.08
    norm = TwoSlopeNorm(vcenter=0.5, vmin=0.5 - spread, vmax=0.5 + spread)
    ax.set_facecolor(SURFACE)
    for a in range(4):
        for b in range(4):
            if games[a, b] == 0:
                continue
            ok = games[a, b] >= min_games
            colour = DIVERGING(norm(shown[a, b])) if ok else NO_DATA
            ax.add_patch(plt.Rectangle((b - 0.5 + 0.03, a - 0.5 + 0.03), 0.94, 0.94,
                                       facecolor=colour, edgecolor="none"))
            if ok:
                low, high = wilson(rate[a, b] * games[a, b], games[a, b])
                strong = abs(shown[a, b] - 0.5) > spread * 0.6
                text_colour = "#ffffff" if strong else INK
                weight = "bold" if (low > 0.5 or high < 0.5) else "normal"
                ax.text(b, a - 0.08, f"{rate[a, b]:.1%}", ha="center", va="center", fontsize=11,
                        color=text_colour, fontweight=weight)
                ax.text(b, a + 0.2, f"{games[a, b]:,} game{'s' if games[a, b] != 1 else ''}", ha="center", va="center", fontsize=8,
                        color=text_colour)
            else:
                ax.text(b, a, f"{games[a, b]:,} game{'s' if games[a, b] != 1 else ''}", ha="center", va="center", fontsize=8, color=MUTED)
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(3.5, -0.5)
    ax.set_xticks(range(4), labels)
    ax.set_yticks(range(4), labels)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel(f"{tag_b} champions on the team")
    ax.set_ylabel(f"{tag_a} champions on the team")
    sm = plt.cm.ScalarMappable(norm=norm, cmap=DIVERGING)
    cbar = fig.colorbar(sm, ax=ax, fraction=0.04, pad=0.03, format=PCT)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=MUTED, labelcolor=INK_2, size=0)
    cbar.set_label(f"Win rate (capped at 50% ± {spread:.0%})", color=INK_2)
    finish(fig, f"5_heatmap_{tag_a.lower()}_{tag_b.lower()}.png", f"Win rate by {tag_a} and {tag_b} count",
           f"Grey cells: fewer than {min_games} games {sub}",
           "Bold = 95% interval excludes 50%. Blue = above 50%, red = below.")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tier", choices=["CHALLENGER", "GRANDMASTER", "MASTER"])
    parser.add_argument("--patch", help="e.g. 16.21")
    parser.add_argument("--min-games", type=int, default=100, help="for the ranking and heatmap cells")
    parser.add_argument("--heatmaps", nargs="+", default=["Marksman:Fighter", "Marksman:Tank", "Mage:Assassin"],
                        help="tag pairs as A:B (default: Marksman:Fighter Marksman:Tank Mage:Assassin)")
    args = parser.parse_args()

    suffix, scope = "", "Master+"
    if args.tier:
        suffix += f"_{args.tier.lower()}"
        scope = args.tier.title()
    if args.patch:
        suffix += f"_{args.patch.replace('.', '-')}"
        scope += f", patch {args.patch}"
    sub = f"({scope} ranked solo, NA)"

    teams = load("team_comps", "", folder=ANALYSIS_DIR)
    if args.tier:
        teams = teams[teams["tier"] == args.tier]
    if args.patch:
        teams = teams[teams["patch"].astype(str) == args.patch]
    comps = load("comp_winrates", suffix)

    style()
    print(f"Writing charts to {FIG_DIR}:")
    plot_tag_counts(load("tag_count_winrates", suffix), sub)
    plot_tag_effects(load("tag_regression", suffix), sub)
    plot_funnel(comps, len(teams), sub)
    plot_ranking(comps, args.min_games, sub)
    for pair in args.heatmaps:
        a, b = pair.split(":")
        if a not in TAGS or b not in TAGS:
            sys.exit(f"Unknown tag in {pair}. Tags: {', '.join(TAGS)}")
        plot_heatmap(teams, a, b, args.min_games, sub)


if __name__ == "__main__":
    main()
