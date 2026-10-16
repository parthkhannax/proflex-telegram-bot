#!/usr/bin/env python3
"""Cross-SOURCE sanity check — does the FEED itself hold up?

verify.py and verify_dots.py recompute every number independently, but they
refetch from the same provider, so a bad Yahoo series would fool both of them.
This script pulls the same names from providers with no relationship to Yahoo:

  * stockanalysis.com  — adjusted daily history (1 year)
  * cnbc.com           — live last trade
  * api.nasdaq.com     — live last trade

and diffs three things against the running terminal:

  1. today's price
  2. the DOT SERIES — every settled session's stock-minus-SPY move, day by day.
     This is the input the streaks and percentiles are built from, so agreement
     here is agreement on everything downstream.
  3. the 5-day percentile, rebuilt from scratch on the foreign feed

Run:  python3 sanity_crosssource.py
"""
import json
import ssl
import urllib.request

ctx = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
NAMES = ["AAPL", "MSFT", "NVDA", "AMD", "MU", "SKHY", "SPCX", "XLE", "XLU", "SMH"]
ETFLIKE = {"SPY", "QQQ", "IGV", "SMH", "XLE", "XLU", "XLK", "XLC", "XLY",
           "XLF", "XLV", "XLI", "XLP", "XLB", "XLRE"}
DOT_DAYS = 30          # settled sessions of the dot series to compare


def _get(u):
    return urllib.request.urlopen(
        urllib.request.Request(u, headers=UA), timeout=30, context=ctx).read().decode()


def sa_history(sym):
    """stockanalysis.com adjusted daily closes -> {date: adj_close}."""
    kind = "e" if sym in ETFLIKE else "s"
    d = json.loads(_get(f"https://stockanalysis.com/api/symbol/{kind}/"
                        f"{sym.lower()}/history?range=1Y&period=Daily"))
    return {r["t"]: float(r["a"]) for r in d.get("data", [])}


def cnbc(sym):
    d = json.loads(_get("https://quote.cnbc.com/quote-html-webservice/quote.htm"
                        f"?symbols={sym}&output=json"))
    return float(d["QuickQuoteResult"]["QuickQuote"][0]["last"])


def nasdaq(sym):
    cls = "etf" if sym in ETFLIKE else "stocks"
    d = json.loads(_get(f"https://api.nasdaq.com/api/quote/{sym}/info?assetclass={cls}"))
    return float(d["data"]["primaryData"]["lastSalePrice"].replace("$", "").replace(",", ""))


api = json.loads(urllib.request.urlopen(
    "http://localhost:11001/api/rotation", timeout=300).read())
BY = {s["ticker"]: s for s in api["stocks"] + api["sector_etfs"]}
live = api["live"]
print(f"terminal is on a {'LIVE' if live else 'SETTLED'} session\n")

spy = sa_history("SPY")
bad = warn = 0

# ---------- 1. price, against two live quote feeds
print("PRICE — terminal vs two independent live quote feeds")
print(f"{'':7}{'terminal':>11}{'cnbc':>10}{'nasdaq':>10}{'max diff%':>11}")
for t in NAMES:
    a = BY[t]
    others = []
    for f in (cnbc, nasdaq):
        try:
            others.append(f(t))
        except Exception:                                   # noqa: BLE001
            others.append(None)
    got = [o for o in others if o]
    if not got:
        warn += 1
        print(f"{t:7}{a['price']:11.2f}{'—':>10}{'—':>10}{'no quote':>11}")
        continue
    diff = max(abs(o - a["price"]) / a["price"] * 100 for o in got)
    tol = 0.5 if live else 0.15        # a live tape moves between the two calls
    if diff >= tol:
        bad += 1
    print(f"{t:7}{a['price']:11.2f}"
          f"{(f'{others[0]:.2f}' if others[0] else '—'):>10}"
          f"{(f'{others[1]:.2f}' if others[1] else '—'):>10}"
          f"{diff:11.3f}  {'' if diff < tol else '!'}")

# ---------- 2. the dot series itself, day by day
print(f"\nDOT SERIES — last {DOT_DAYS} settled sessions, stock minus SPY, "
      "recomputed on stockanalysis.com data")
print(f"{'':7}{'days cmp':>10}{'sign match':>13}{'max diff':>10}{'mean diff':>11}")
for t in NAMES:
    try:
        h = sa_history(t)
    except Exception as e:                                  # noqa: BLE001
        warn += 1
        print(f"{t:7}  history unavailable ({type(e).__name__})")
        continue
    days = sorted(set(h) & set(spy))
    if len(days) < 10:
        warn += 1
        print(f"{t:7}  only {len(days)} overlapping sessions — skipped")
        continue
    mine = {days[i]: ((h[days[i]] / h[days[i - 1]] - 1)
                      - (spy[days[i]] / spy[days[i - 1]] - 1)) * 100
            for i in range(1, len(days))}
    # the terminal's last dot is today and still moving on a live tape; drop it
    dots = BY[t]["dots"][:-1] if live else BY[t]["dots"]
    pairs = [(d["rel"], mine[d["d"]]) for d in dots[-DOT_DAYS:] if d["d"] in mine]
    if not pairs:
        warn += 1
        print(f"{t:7}  no overlapping dot dates — skipped")
        continue
    signs = sum(1 for a_, b_ in pairs if (a_ > 0) == (b_ > 0))
    mx = max(abs(a_ - b_) for a_, b_ in pairs)
    mean = sum(abs(a_ - b_) for a_, b_ in pairs) / len(pairs)
    ok = signs == len(pairs) and mx < 0.15
    if not ok:
        bad += 1
    print(f"{t:7}{len(pairs):>10}{signs:>9}/{len(pairs):<3}{mx:>10.3f}{mean:>11.3f}"
          f"  {'' if ok else '!'}")

# ---------- 3. one percentile, rebuilt end to end on the foreign feed
#
# The two feeds must be pinned to the SAME five dates. The foreign feed's last
# settled bar is normally yesterday while the terminal is quoting a live today,
# and with single-day relative moves of several percent in this universe, a
# one-day shift changes the five-day sum enough to look like a data fault when
# it is only a calendar offset. So the window ends on the last date BOTH sides
# have, and the terminal's own figure for that window is rebuilt from its dots.
print("\n5-DAY WINDOW + PERCENTILE — rebuilt on stockanalysis.com, pinned to the "
      "same five dates the terminal used")
print(f"{'':7}{'window ends':>13}{'5d here':>10}{'5d terminal':>13}{'diff':>8}"
      f"{'pct here':>10}{'pct term':>10}")
for t in ["AAPL", "MSFT", "NVDA", "XLU", "AMD"]:
    h = sa_history(t)
    days = sorted(set(h) & set(spy))
    rel = {days[i]: ((h[days[i]] / h[days[i - 1]] - 1)
                     - (spy[days[i]] / spy[days[i - 1]] - 1)) * 100
           for i in range(1, len(days))}
    a = BY[t]
    dot = {d["d"]: d["rel"] for d in a["dots"]}
    shared = [d for d in sorted(rel) if d in dot]
    win = shared[-5:]
    mine = sum(rel[d] for d in win)
    theirs = sum(dot[d] for d in win)
    # percentile of that same window value, ranked in the foreign feed's own year
    seq = [rel[d] for d in sorted(rel)]
    end = sorted(rel).index(win[-1])
    roll = [sum(seq[i:i + 5]) for i in range(end - 3)]
    p = (sum(1 for v in roll[:-1] if v < roll[-1]) + 0.5) / len(roll) * 100
    live_shift = win[-1] != a["dots"][-1]["d"]
    av = a["pctl"]["windows"]["5"]
    ok = abs(mine - theirs) < 0.05
    if not ok:
        bad += 1
    print(f"{t:7}{win[-1]:>13}{mine:+10.2f}{theirs:+13.2f}{mine - theirs:+8.3f}"
          f"{p:10.1f}{av['pct']:10.1f}  {'' if ok else '!'}"
          f"{'  (terminal pct includes today, ranked over 2y)' if live_shift else ''}")

print(f"\n=== {'CROSS-SOURCE SANITY OK' if bad == 0 else str(bad) + ' HARD DISAGREEMENT(S)'}"
      f"{f' · {warn} soft flag(s)' if warn else ''} ===")
