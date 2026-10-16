#!/usr/bin/env python3
"""Turn a free-form Run | Worry | Stock | Dir | Clear | Take Profit dump into levels.csv via headless Claude."""
import csv, io, pathlib, subprocess, sys
from build import FIELDS, here

PROMPT = """You convert a trader's levels list into CSV. Output ONLY CSV, no prose, no code fences.
Header exactly: symbol,run,worry,dir,clear,take_profit,note
Conventions:
- Input columns are Run | Worry | Stock | Dir | Clear | Take Profit (the order may vary; use the header line if present). Older lists may say Conviction for Dir and All Clear for Clear.
- symbol: official US ticker, uppercase (Meta -> META, Google -> GOOG as written).
- run, worry: plain numbers.
- dir: normalise to one of Up, Down, Flat/Down, Flat, Chop, None (keep compound values like Flat/Down; '-' or blank -> None).
- clear, take_profit: the number(s) only; if several ("341 then 348", "till 344"), join with '/' e.g. 341/348. Empty if the cell is blank or '-'.
- note: any extra commentary from the Clear or Take Profit cells, short, fixed spelling, no commas; empty if none.
- Skip divider lines and headers. Never invent levels.

Input:
"""


def parse(text):
    out = subprocess.run(["claude", "-p", PROMPT + text], capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    body = "\n".join(l for l in out.stdout.strip().splitlines() if not l.startswith("```"))
    rows = [r for r in csv.DictReader(io.StringIO(body)) if (r.get("symbol") or "").strip()]
    if not rows or set(FIELDS) - set(rows[0]):
        raise RuntimeError("Claude returned unexpected output:\n" + out.stdout)
    with open(here / "levels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    print(f"Parsed {len(parse(sys.stdin.read()))} symbols")
