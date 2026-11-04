# Analyses

Results, charts and write-ups. Each folder has a README with the findings, and `tables/` (CSV) and `figures/` (PNG) where relevant.
The raw match data is not in the repo (see the main README to rebuild it).

| Analysis | Question | Headline |
|---|---|---|
| [team_comps](team_comps/README.md) | Do some class-tag compositions win more? | Barely. Stacking marksmen hurts, and red side (52.7%) matters more than any tag. |
| [team_stats](team_stats/README.md) | Do teams with better champion stats win more? | No: about 1 point per standard deviation at most, at levels 6, 12 and 18 alike. |
| [champion_groups](champion_groups/README.md) | Which champion pairs and trios win most, and with which partner tags? | No clear pair synergy once champion strength is accounted for (Alistar pairs top the list because Alistar is strong). Trios are too rare in 10k matches to rank. |

Data: 10,000 NA Master+ ranked solo matches, 20 Oct to 3 Nov 2026 (patch 16.21).
