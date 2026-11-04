# Win rate vs. team champion stats

**Question:** do teams whose champions have better stats (more HP, armor, attack range and so on) win more?

**Data:** the same 10,000 NA Master+ ranked solo matches (20,000 teams) as the
[team composition analysis](../team_comps/README.md).

**Method:** for each team, the **sum** and **average** of each stat across its 5 champions. Since every team has
5 champions, these give the same results (average = sum / 5). Each stat is compared with win rate in four ways:
- winners vs. losers
- how often the team with the higher total wins
- win-rate change per 1 standard deviation of difference between the teams (logistic regression, one row per match)
- quintiles

This was done for the base-stat columns, and for each champion's actual stats at **levels 6, 12 and 18**
(League's growth formula; both teams assumed to be the same level).

## Key findings

1. **Base stats barely predict wins.** Every effect is about 1 percentage point or less per standard deviation. The team with the
   higher total of any stat wins between 49% and 52% of the time.
2. **Nothing passes a correction for testing many stats.** The best single p-value is 0.01 (19 base stats tested).
3. **The weak trends match the [Marksman result](../team_comps/README.md):** more attack range and mana is slightly
   worse (about −1.2 points per SD), and more HP and MR is slightly better.
4. **By level:** the small HP effect fades from level 6 to 18 (+1.0 to +0.6 points per SD), while MR grows (+1.0 to +1.25, p = 0.013 at 18).

| Stat | Lvl 6 | Lvl 12 | Lvl 18 |
|---|---|---|---|
| HP | +1.0 pts | +0.75 | +0.6 |
| Magic resist | +1.0 | +1.2 | +1.25 |
| Armor | +0.2 | −0.4 | −0.7 |
| Attack damage | +0.1 | −0.4 | −0.6 |
| Mana | −0.8 | −1.0 | −1.1 |
| Attack range | −1.2 | −1.2 | −1.2 |

*Win-rate change per 1 standard deviation of difference in the team total.*

## Tables (`tables/`)

| File | Contents |
|---|---|
| `stat_winrates[_lvlN].csv` | Per stat: winner/loser sum and average, "higher total wins" rate with 95% interval, effect per SD, p-value |
| `stat_quintile_winrates[_lvlN].csv` | Team win rate per quintile of each stat |
| `stat_regression[_lvlN].csv` | All stats together in one model |
| `stat_level_comparison.csv` | Each stat at levels 6, 12 and 18 side by side |

No suffix = the base-stat columns of `champion_tags.csv` (base values and per-level growth as separate stats).

## Data adjustments

- `crit` and `crit_per_level` are 0 for every champion, so they're left out.
- Resource stats only count **Mana** champions (others use energy, fury and so on; Viego's value is 10000).
- Locke has no attack damage per level in Data Dragon, so it's filled with the Assassin median.

## Reproduce

```
python analyze_team_comps.py     # builds the team table first
python analyze_team_stats.py     # base + levels 6/12/18; --levels 1 9 18 for others
```
