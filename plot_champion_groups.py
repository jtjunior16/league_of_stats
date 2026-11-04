"""Charts for the champion pairs analysis.

Run analyze_champion_groups.py first. Then:
    python plot_champion_groups.py

Writes PNGs to analyses/champion_groups/figures/ (in git):
    1_pairs_expected_vs_actual.png   every pair: win rate expected from its champions' own strength vs. actual
    2_pairs_top_bottom.png           top and bottom 10 pairs: expected -> actual, with partner tags

"Expected" = the win rate if the two champions' strengths simply add up (log-odds), with no synergy.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.transforms import Bbox
from matplotlib.ticker import FuncFormatter

from analyze_champion_groups import RESULTS_DIR
from plot_team_comps import (AXIS, BLUE, GRID, INK, INK_2, MUTED, PCT, RED, SURFACE,
                             fifty_line, finish, style)

FIG_DIR = RESULTS_DIR / "figures"
TABLES = RESULTS_DIR / "tables"


def load(name):
    path = TABLES / name
    if not path.exists():
        raise SystemExit(f"{path} not found. Run analyze_champion_groups.py first.")
    return pd.read_csv(path, encoding="utf-8-sig")


def place_labels(fig, ax, rows, text_of, obstacles=None):
    """Label points, nudging a label further out (with a leader line) if it overlaps a placed label or a dot."""
    renderer = fig.canvas.get_renderer()
    placed = []
    if obstacles is not None:  # keep labels off these points (display-space boxes ~10 px wide)
        for x, y in ax.transData.transform(obstacles):
            placed.append(Bbox.from_extents(x - 6, y - 6, x + 6, y + 6))
    for _, r in rows.iterrows():
        up = r["win_rate"] > r["expected_win_rate"]
        left = r["expected_win_rate"] > np.mean(ax.get_xlim())
        for step in range(14):
            dy = (6 + 11 * step) * (1 if up else -1)
            label = ax.annotate(
                text_of(r), (r["expected_win_rate"], r["win_rate"]),
                xytext=(-7 if left else 7, dy), textcoords="offset points",
                ha="right" if left else "left", va="bottom" if up else "top", fontsize=8, color=INK_2,
                arrowprops=dict(arrowstyle="-", color=AXIS, linewidth=0.7, shrinkA=0, shrinkB=4) if step else None)
            label.update_positions(renderer)  # apply the offset before measuring
            box = Text.get_window_extent(label, renderer).expanded(1.02, 1.2)
            inside = ax.get_window_extent(renderer).contains(box.x0, box.y0) and                 ax.get_window_extent(renderer).contains(box.x1, box.y1)
            if inside and not any(box.overlaps(b) for b in placed):
                placed.append(box)
                break
            label.remove()


# ---------------------------------------------------------------- 1. scatter

def plot_expected_vs_actual(pairs, n_labels_per_side=4):
    d = pairs.copy()
    e = d["expected_win_rate"]
    d["z"] = (d["win_rate"] - e) / np.sqrt(e * (1 - e) / d["games"])
    colours = np.where(d["z"] > 1.96, BLUE, np.where(d["z"] < -1.96, RED, MUTED))
    sizes = 12 + 0.25 * d["games"]

    fig, ax = plt.subplots(figsize=(11, 7.4))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.86, bottom=0.12)
    x0, x1 = e.min() - 0.01, e.max() + 0.01
    ax.set_xlim(x0, x1)
    ax.set_ylim(min(d["win_rate"].min(), x0) - 0.03, max(d["win_rate"].max(), x1) + 0.03)

    # Chance range around "no synergy" for a pair with the median number of games.
    n_mid = int(d["games"].median())
    xs = np.linspace(x0, x1, 100)
    band = 1.96 * np.sqrt(xs * (1 - xs) / n_mid)
    ax.fill_between(xs, xs - band, xs + band, color=GRID, alpha=0.55, linewidth=0, zorder=0)
    ax.plot(xs, xs, color=INK_2, linewidth=0.9, zorder=1)
    ax.text(x0 + 0.004, x0 + 0.004 - 0.012, "no synergy (actual = expected)", ha="left", va="top",
            fontsize=8.5, color=INK_2, rotation=0)
    fifty_line(ax)
    ax.axvline(0.5, color=AXIS, linewidth=0.7, zorder=1)

    order = np.argsort(colours != MUTED)  # grey first, coloured on top
    ax.scatter(e.iloc[order], d["win_rate"].iloc[order], s=sizes.iloc[order], c=colours[order],
               edgecolor=SURFACE, linewidth=1.2, alpha=0.9, zorder=3)

    labelled = pd.concat([d[d["z"] > 1.96].nlargest(n_labels_per_side, "z"),
                          d[d["z"] < -1.96].nsmallest(n_labels_per_side, "z")])
    coloured = d.loc[colours != MUTED, ["expected_win_rate", "win_rate"]].to_numpy()
    place_labels(fig, ax, labelled, lambda r: f"{r['group']}  {r['synergy']:+.0%}", obstacles=coloured)

    ax.xaxis.set_major_formatter(PCT)
    ax.yaxis.set_major_formatter(PCT)
    ax.set_xlabel("Expected win rate from the two champions' own win rates")
    ax.set_ylabel("Actual win rate as a pair")
    handles = [Line2D([], [], marker="o", linestyle="", color=c, markersize=7, label=l) for c, l in
               [(BLUE, "Wins more than expected"), (RED, "Wins less than expected"), (MUTED, "Within chance")]]
    ax.legend(handles=handles, loc="upper left", fontsize=9, labelcolor=INK_2)
    n_out = int((colours != MUTED).sum())
    finish(fig, "1_pairs_expected_vs_actual.png", "Champion pairs: expected vs. actual win rate",
           f"{len(d)} pairs with at least {d['games'].min()} games. Above the line = wins more than the champions' "
           f"own strength explains. Dot size = games.",
           f"Shaded: chance range for a pair with {n_mid} games (the median). Coloured: outside its own 95% chance range "
           f"({n_out} pairs; about {0.05 * len(d):.0f} expected by chance alone). Labels show synergy.",
           folder=FIG_DIR)


# ---------------------------------------------------------------- 2. top / bottom

def plot_top_bottom(tb):
    fig, axes = plt.subplots(2, 1, figsize=(14, 10.5), sharex=True)
    fig.subplots_adjust(left=0.17, right=0.56, top=0.87, bottom=0.08, hspace=0.28)
    lo = min(tb["ci_low"].min(), tb["expected_win_rate"].min()) - 0.02
    hi = max(tb["ci_high"].max(), tb["expected_win_rate"].max()) + 0.02

    for ax, kind, title in [(axes[0], "top", "Top 10 by win rate"), (axes[1], "bottom", "Bottom 10 by win rate")]:
        d = tb[tb["rank_type"] == kind].sort_values("rank", ascending=False)
        y = np.arange(len(d))
        colours = [BLUE if s > 0 else RED for s in d["synergy"]]
        ax.hlines(y, d["ci_low"], d["ci_high"], color=GRID, linewidth=5, zorder=1)
        ax.hlines(y, d["expected_win_rate"], d["win_rate"], color=colours, linewidth=2, zorder=2)
        ax.scatter(d["expected_win_rate"], y, s=48, facecolor=SURFACE, edgecolor=INK_2, linewidth=1.5, zorder=3)
        ax.scatter(d["win_rate"], y, s=55, color=colours, edgecolor=SURFACE, linewidth=1.5, zorder=4)
        fifty_line(ax, "x")
        ax.set_yticks(y, [f"{g}  ({n})" for g, n in zip(d["group"], d["games"])], fontsize=9)
        ax.set_ylim(-0.7, len(d) - 0.3)
        ax.set_xlim(lo, hi)
        ax.grid(axis="y", visible=False)
        ax.xaxis.set_major_formatter(PCT)
        ax.set_title(title, pad=10)
        ax.tick_params(labelbottom=True)

        # Text columns to the right of the plot: synergy, then the two most common partner-tag combos.
        trans = ax.get_yaxis_transform()
        columns = [(1.03, "Synergy"), (1.15, "Most common partner tags"), (1.62, "2nd most common")]
        for x, head in columns:
            ax.text(x, len(d) - 0.3, head, transform=trans, fontsize=8.5, color=MUTED, va="bottom")
        for yi, (_, r) in zip(y, d.iterrows()):
            ax.text(1.03, yi, f"{r['synergy']:+.1%}", transform=trans, va="center", fontsize=9,
                    color=INK)
            for x, i in [(1.15, 1), (1.62, 2)]:
                if isinstance(r[f"partner_tags_{i}"], str) and r[f"partner_tags_{i}"]:
                    ax.text(x, yi, f"{r[f'partner_tags_{i}']}  {r[f'partner_win_rate_{i}']:.0%} "
                                   f"({int(r[f'partner_games_{i}'])})",
                            transform=trans, va="center", fontsize=8.5, color=INK_2)
    axes[1].set_xlabel("Win rate")

    handles = [Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE, markeredgecolor=INK_2,
                      markersize=7, label="Expected (champions' own strength)"),
               Line2D([], [], marker="o", linestyle="", color=BLUE, markersize=7, label="Actual, above expected"),
               Line2D([], [], marker="o", linestyle="", color=RED, markersize=7, label="Actual, below expected"),
               Line2D([], [], color=GRID, linewidth=5, label="95% interval of actual")]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.01, 0.935), ncol=4, fontsize=9,
               labelcolor=INK_2)
    finish(fig, "2_pairs_top_bottom.png", "Best and worst champion pairs",
           "Pairs with at least 50 games. Games in brackets. Partner tags = primary tags of the other 3 champions: "
           "win rate (games).",
           "Synergy = actual minus expected. A top 10 picked from 414 small samples overstates the true win rates; "
           "check the interval.",
           folder=FIG_DIR)


def main():
    style()
    print(f"Writing charts to {FIG_DIR}:")
    plot_expected_vs_actual(load("groups_size2.csv"))
    plot_top_bottom(load("top_bottom_size2.csv"))


if __name__ == "__main__":
    main()
