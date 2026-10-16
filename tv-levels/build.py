#!/usr/bin/env python3
"""Build proflex-levels.pine from levels.csv. Edit the CSV (or let parse.py write it), run this, paste/publish the .pine."""
import csv, pathlib
here = pathlib.Path(__file__).parent
FIELDS = ["symbol", "run", "worry", "dir", "clear", "take_profit", "note"]


def clean(s):
    return (s or "").strip().replace("|", " ").replace(";", ",").replace('"', "'")


def build():
    rows = [r for r in csv.DictReader(open(here / "levels.csv")) if (r.get("symbol") or "").strip()]
    data = ";".join("|".join([clean(r["symbol"]).upper(), clean(r["run"]), clean(r["worry"]),
                              clean(r["dir"]) or "None", clean(r["clear"]), clean(r.get("take_profit")),
                              clean(r["note"]) or "-"]) for r in rows)
    tpl = (here / "template.pine").read_text()
    (here / "proflex-levels.pine").write_text(tpl.replace("__LEVELS__", data))
    return f"Built proflex-levels.pine: {len(rows)} symbols"


if __name__ == "__main__":
    print(build())
