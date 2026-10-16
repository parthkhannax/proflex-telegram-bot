#!/usr/bin/env python3
"""Independent re-derivation of the terminal's headline numbers.

Deliberately does NOT import engine.py — it refetches from Yahoo and recomputes
with plain Python so a mistake in the engine cannot reproduce itself here.
Run:  python3 verify.py
"""
import json
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import median

ctx = ssl.create_default_context()
ET = timezone(timedelta(hours=-4))
UA = {"User-Agent": "Mozilla/5.0"}


def bars(t, interval="1d", rng="2y"):
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{t}"
         f"?interval={interval}&range={rng}")
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers=UA), timeout=25, context=ctx).read())
    return d["chart"]["result"][0]


def _repair(t, adjm, rawm):
    """Yahoo intermittently drops a whole session from the 1d feed for every
    symbol while the intraday feeds still carry it. Rebuild any such session
    from the 60m bars so this check compares against the same calendar the
    terminal uses. Written from the definition here on purpose — this file must
    not import engine.py, or it would stop being an independent check."""
    r = bars(t, "60m", "60d")
    q = r["indicators"]["quote"][0]["close"]
    by_day = {}
    for ts, c in zip(r["timestamp"], q):
        if c is None:
            continue
        d = datetime.fromtimestamp(ts, ET)
        by_day.setdefault(d.strftime("%Y-%m-%d"), []).append((ts, c))
    have = {datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d") for ts in adjm}
    added = 0
    for day, rows in by_day.items():
        if day in have or not rows:
            continue
        d = datetime.fromtimestamp(rows[0][0], ET)
        key = int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp())
        close = rows[-1][1]
        adjm[key] = close
        rawm[key] = close
        added += 1
    if added:
        print(f"  [repair] {t}: rebuilt {added} session(s) missing from the 1d feed")
    return adjm, rawm


def daily(t):
    r = bars(t)
    q = r["indicators"]["quote"][0]["close"]
    adj = r.get("indicators", {}).get("adjclose")
    c = adj[0]["adjclose"] if adj else q
    adjm = {ts: v for ts, v in zip(r["timestamp"], c) if v is not None}
    rawm = {ts: v for ts, v in zip(r["timestamp"], q) if v is not None}
    adjm, _ = _repair(t, adjm, rawm)
    return adjm


def pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


# SPCX and SKHY are deliberately absent: this script aligns on the INTERSECTION
# of every ticker's calendar, and two names with weeks of history would truncate
# the whole two-year panel. verify_dots.py covers them with a per-ticker spine.
TOP = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B",
       "LLY", "AMD", "MU"]
data = {t: daily(t) for t in TOP + ["SPY", "SMH"]}
common = sorted(set.intersection(*[set(d) for d in data.values()]))
print(f"aligned sessions: {len(common)}   last bar: "
      f"{datetime.fromtimestamp(common[-1], ET):%Y-%m-%d %H:%M ET}\n")

# percent returns
ret = {}
for t, d in data.items():
    s = [d[k] for k in common]
    ret[t] = [(s[i] / s[i - 1] - 1) * 100 for i in range(1, len(s))]

spy = ret["SPY"]
rel = {t: [ret[t][i] - spy[i] for i in range(len(spy))] for t in TOP}
rel_smh = [ret["SMH"][i] - spy[i] for i in range(len(spy))]

print(f"SPY today: {spy[-1]:+.2f}%   (raw close {data['SPY'][common[-1]]:.2f})\n")
print(f"{'':6}{'raw%':>8}{'rel_y':>8}{'rel_t':>8}{'corr10':>9}{'corr63':>9}")
for t in TOP:
    print(f"{t:6}{ret[t][-1]:+8.2f}{rel[t][-2]:+8.2f}{rel[t][-1]:+8.2f}"
          f"{pearson(rel[t][-10:], rel_smh[-10:]):9.2f}"
          f"{pearson(rel[t][-63:], rel_smh[-63:]):9.2f}")

# sector aggregate
sec = {"AI / Semis": ["NVDA", "AVGO", "AMD"],
       "Memory": ["MU"],                        # SKHY excluded, see TOP above
       "Big Tech": ["MSFT", "AAPL", "GOOGL", "AMZN", "META"],
       "Space": [],                             # SPCX excluded, see TOP above
       "Defensive / Value": ["BRK-B", "LLY"]}
print("\nsector rel today (mean of members):")
for k, mem in sec.items():
    v = sum(rel[m][-1] for m in mem) / len(mem)
    f5 = sum(sum(rel[m][-5:]) for m in mem) / len(mem)
    print(f"  {k:<20}{v:+7.2f}   5d {f5:+7.2f}")

# duration backtest, recomputed independently
TH = 0.15


def quad(p, c):
    if p > TH and c > TH: return "running"
    if p < -TH and c > TH: return "turning"
    if p > TH and c < -TH: return "fading"
    if p < -TH and c < -TH: return "avoid"
    return "quiet"


buck = {}
for t in TOP:
    r = rel[t]
    for i in range(1, len(r) - 5):
        q = quad(r[i - 1], r[i])
        if q == "quiet":
            continue
        up = q in ("running", "turning")
        run = 0
        for k in range(i + 1, len(r)):
            if (r[k] > 0) == up:
                run += 1
            else:
                break
        sg = 1 if up else -1
        b = buck.setdefault(q, {"d": [], "f5": []})
        b["d"].append(run)
        b["f5"].append(sg * sum(r[i + 1:i + 6]))

print("\nstock duration backtest (independent):")
for q in ["running", "turning", "fading", "avoid"]:
    b = buck[q]
    hit = sum(1 for x in b["f5"] if x > 0) / len(b["f5"]) * 100
    print(f"  {q:<9} n={len(b['d']):<5} median {median(b['d']):.0f}d  "
          f"mean {sum(b['d'])/len(b['d']):.2f}d  "
          f"2+ {sum(1 for x in b['d'] if x>=2)/len(b['d'])*100:.1f}%  "
          f"cont5 {sum(b['f5'])/len(b['f5']):+.3f}%  hit5 {hit:.1f}%")

# morning backtest, recomputed independently
print("\nmorning (first 2h vs rest of day), independent:")
intr = {}
for t in TOP + ["SPY"]:
    r = bars(t, "60m", "60d")
    q = r["indicators"]["quote"][0]
    g = {}
    for ts, o, c in zip(r["timestamp"], q["open"], q["close"]):
        if o is None or c is None:
            continue
        g.setdefault(datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d"), []).append((o, c))
    intr[t] = g

sess = sorted(intr["SPY"])
days = sess[1:-1]                      # need a prior session, skip today
prev = {t: {sess[i]: intr[t][sess[i - 1]][-1][1]
            for i in range(1, len(sess))
            if sess[i - 1] in intr[t] and intr[t][sess[i - 1]]}
        for t in TOP + ["SPY"]}
h = n = 0
for t in TOP:
    for d in days:
        if d not in intr[t] or len(intr[t][d]) < 4 or len(intr["SPY"][d]) < 4:
            continue
        if d not in prev[t] or d not in prev["SPY"]:
            continue
        b, s = intr[t][d], intr["SPY"][d]
        # morning leg anchored to the PREVIOUS CLOSE so the overnight gap counts
        m = (b[1][1] / prev[t][d] - 1) - (s[1][1] / prev["SPY"][d] - 1)
        rr = (b[-1][1] / b[1][1] - 1) - (s[-1][1] / s[1][1] - 1)
        if abs(m * 100) < 0.1:
            continue
        n += 1
        if (m > 0) == (rr > 0):
            h += 1
print(f"  sessions {len(days)}  n={n}  hit {h/n*100:.1f}%")

# gap check: today's move measured two ways
print("\ngap check (prev close vs open baseline):")
print(f"{'':7}{'fromPrevC':>11}{'fromOpen':>10}{'gap%':>8}")
i5 = {t: bars(t, '1d', '5d') for t in ['AVGO', 'NVDA', 'SPY']}
for t, r in i5.items():
    q = r['indicators']['quote'][0]
    # Drop Yahoo's null sessions before indexing. Taking close[-2] blindly is
    # the exact bug this check exists to catch: when a session comes back all
    # null, [-2] silently points two sessions back and every number here turns
    # into a multi-day return.
    rows = [(o, c) for o, c in zip(q['open'], q['close'])
            if o is not None and c is not None]
    if len(rows) < 2:
        print(f"{t:7}{'no usable sessions':>29}")
        continue
    o, c = rows[-1]
    pc = rows[-2][1]
    print(f"{t:7}{(c/pc-1)*100:+11.3f}{(c/o-1)*100:+10.3f}{(o/pc-1)*100:+8.3f}")
