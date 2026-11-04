"""Build champion tables (CSV) from downloaded Data Dragon files.

Run fetch_ddragon.py first. Then:
    python build_champion_tables.py                  # latest downloaded patch
    python build_champion_tables.py --version 16.22.1

Writes to data/tables/:
    champion_tags.csv            one row per champion: key, id, name, classes, base stats
    champion_spells.csv          one row per ability (P, Q, W, E, R) per champion
    champion_stats_by_level.csv  one row per champion per level 1-18

`key` is the number match data uses (participants[].championId), so join on it.
Files are UTF-8 with a BOM so Excel opens them correctly.

Known Data Dragon gaps (see PROJECT_LOG.md):
  * attackdamageperlevel is 0 for every champion since early 2026. When that
    happens, values are filled from FALLBACK_VERSION and marked in the
    ad_per_level_source column. Champions newer than that patch get no value.
  * Ability damage values and AP/AD scaling are not in Data Dragon.
"""

import argparse
import csv
import html
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent
DDRAGON_DIR = ROOT / "data" / "ddragon"
TABLES_DIR = ROOT / "data" / "tables"

FALLBACK_VERSION = "15.24.1"  # last patch checked to have real attackdamageperlevel values
MAX_LEVEL = 18
SLOTS = ["Q", "W", "E", "R"]

# Base stat columns: (output name, Data Dragon field).
BASE_STATS = [
    ("hp", "hp"), ("hp_per_level", "hpperlevel"),
    ("hp_regen", "hpregen"), ("hp_regen_per_level", "hpregenperlevel"),
    ("resource", "mp"), ("resource_per_level", "mpperlevel"),
    ("resource_regen", "mpregen"), ("resource_regen_per_level", "mpregenperlevel"),
    ("attack_damage", "attackdamage"), ("attack_damage_per_level", "attackdamageperlevel"),
    ("attack_speed", "attackspeed"), ("attack_speed_per_level_pct", "attackspeedperlevel"),
    ("armor", "armor"), ("armor_per_level", "armorperlevel"),
    ("magic_resist", "spellblock"), ("magic_resist_per_level", "spellblockperlevel"),
    ("crit", "crit"), ("crit_per_level", "critperlevel"),
    ("move_speed", "movespeed"), ("attack_range", "attackrange"),
]

# Stats that grow per level: (output name, base field, growth field).
GROWING_STATS = [
    ("hp", "hp", "hpperlevel"),
    ("hp_regen", "hpregen", "hpregenperlevel"),
    ("resource", "mp", "mpperlevel"),
    ("resource_regen", "mpregen", "mpregenperlevel"),
    ("attack_damage", "attackdamage", "attackdamageperlevel"),
    ("armor", "armor", "armorperlevel"),
    ("magic_resist", "spellblock", "spellblockperlevel"),
]


# ---------------------------------------------------------------- helpers

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(name, header, rows):
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    path = TABLES_DIR / name
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name}: {len(rows)} rows, {len(header)} columns")


def plain_text(text):
    """Strip Data Dragon's HTML-style tags: '<br>' -> space, '<magicDamage>x</...>' -> 'x'."""
    text = re.sub(r"<br\s*/?>", " ", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def growth(level):
    """League's per-level growth multiplier: stats don't grow linearly.

    stat at level n = base + per_level * (n - 1) * (0.7025 + 0.0175 * (n - 1))
    """
    n = level - 1
    return n * (0.7025 + 0.0175 * n)


def ad_growth_fallback(champions):
    """Return {champion id: attackdamageperlevel} from FALLBACK_VERSION if the current
    patch has the all-zero bug, else None."""
    if any(c["stats"]["attackdamageperlevel"] for c in champions.values()):
        return None
    path = DDRAGON_DIR / FALLBACK_VERSION / "en_US" / "champion.json"
    if not path.exists():
        print(f"  attack_damage_per_level is 0 for every champion; downloading {FALLBACK_VERSION} values...")
        url = f"https://ddragon.leagueoflegends.com/cdn/{FALLBACK_VERSION}/data/en_US/champion.json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text, encoding="utf-8")
    old = load_json(path)["data"]
    return {cid: c["stats"]["attackdamageperlevel"] for cid, c in old.items()}


def cost_type(spell, partype):
    """'{{ cost }} {{ abilityresourcename }}' -> the champion's resource (e.g. 'Mana')."""
    text = spell.get("resource") or spell.get("costType") or ""
    text = text.replace("{{ abilityresourcename }}", partype or "")
    text = re.sub(r"\{\{[^}]*\}\}", "", text)  # drop remaining placeholders
    return re.sub(r"\s+", " ", text).strip(" +()") or "No Cost"


# ---------------------------------------------------------------- tables

def build_tags(champions, ad_fallback):
    header = ["key", "id", "name", "primary_tag", "secondary_tag"] + [n for n, _ in BASE_STATS] \
        + ["ad_per_level_source"]
    rows = []
    for c in champions.values():
        stats = dict(c["stats"])
        source = "ddragon"
        if ad_fallback is not None:
            if c["id"] in ad_fallback:
                stats["attackdamageperlevel"] = ad_fallback[c["id"]]
                source = f"fallback {FALLBACK_VERSION}"
            else:
                stats["attackdamageperlevel"] = None
                source = "missing"
        tags = c["tags"] + [""] * (2 - len(c["tags"]))
        rows.append([int(c["key"]), c["id"], c["name"], tags[0], tags[1]]
                    + [stats.get(field) for _, field in BASE_STATS] + [source])
    rows.sort(key=lambda r: r[2])
    return header, rows, {r[1]: dict(zip(header, r)) for r in rows}


def build_spells(champions):
    max_rank = max(s["maxrank"] for c in champions.values() for s in c["spells"])
    ranks = range(1, max_rank + 1)
    header = (["key", "id", "slot", "spell_id", "name", "max_rank", "cost_type"]
              + [f"cooldown_r{r}" for r in ranks] + [f"cost_r{r}" for r in ranks]
              + [f"range_r{r}" for r in ranks] + ["description"])
    blank = [""] * max_rank
    rows = []
    for c in sorted(champions.values(), key=lambda c: c["name"]):
        key = int(c["key"])
        p = c["passive"]
        rows.append([key, c["id"], "P", "", p["name"], "", "Passive"]
                    + blank + blank + blank + [plain_text(p["description"])])
        for slot, s in zip(SLOTS, c["spells"]):
            pad = lambda values: list(values) + [""] * (max_rank - len(values))
            rows.append([key, c["id"], slot, s["id"], s["name"], s["maxrank"], cost_type(s, c["partype"])]
                        + pad(s["cooldown"]) + pad(s["cost"]) + pad(s["range"])
                        + [plain_text(s["description"])])
    return header, rows


def build_stats_by_level(tag_rows):
    header = ["key", "id", "level"] + [n for n, _, _ in GROWING_STATS] \
        + ["attack_speed", "move_speed", "attack_range", "crit"]
    out_name = dict((field, name) for name, field in BASE_STATS)
    rows = []
    for r in tag_rows.values():
        for level in range(1, MAX_LEVEL + 1):
            g = growth(level)
            values = []
            for name, base_field, growth_field in GROWING_STATS:
                base, per = r[out_name[base_field]], r[out_name[growth_field]]
                values.append("" if per is None else round(base + per * g, 3))
            # Attack speed grows by a percentage of the base value.
            attack_speed = r["attack_speed"] * (1 + r["attack_speed_per_level_pct"] / 100 * g)
            rows.append([r["key"], r["id"], level] + values
                        + [round(attack_speed, 3), r["move_speed"], r["attack_range"], r["crit"]])
    return header, rows


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Build champion CSV tables from Data Dragon.")
    parser.add_argument("--version", help="patch folder in data/ddragon (default: newest downloaded)")
    parser.add_argument("--lang", default="en_US")
    args = parser.parse_args()

    version = args.version
    if version is None:
        versions = load_json(DDRAGON_DIR / "versions.json") if (DDRAGON_DIR / "versions.json").exists() else []
        version = next((v for v in versions if (DDRAGON_DIR / v / args.lang).exists()), None)
    path = DDRAGON_DIR / str(version) / args.lang / "championFull.json"
    if not path.exists():
        sys.exit(f"{path} not found. Run fetch_ddragon.py first.")

    champions = load_json(path)["data"]
    print(f"Building champion tables from patch {version} ({len(champions)} champions)...")

    ad_fallback = ad_growth_fallback(champions)
    header, rows, by_id = build_tags(champions, ad_fallback)
    write_csv("champion_tags.csv", header, rows)
    write_csv("champion_spells.csv", *build_spells(champions))
    write_csv("champion_stats_by_level.csv", *build_stats_by_level(by_id))

    if ad_fallback is not None:
        missing = [r[1] for r in rows if r[-1] == "missing"]
        print(f"  Note: attack_damage_per_level filled from {FALLBACK_VERSION}."
              + (f" No value for: {', '.join(missing)}" if missing else ""))
    print(f"Done. Tables are in {TABLES_DIR}")


if __name__ == "__main__":
    main()
