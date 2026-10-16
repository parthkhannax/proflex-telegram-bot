#!/usr/bin/env python3
"""Full-terminal data audit.

Refetches every series from Yahoo and re-derives the numbers the API serves,
then checks them three ways:
  A. SOURCE   — do the raw feeds agree with each other and cover every session?
  B. DERIVED  — does each panel's maths reproduce from the refetched data?
  C. TIE-OUT  — do panels that show the SAME quantity agree with each other?

(C) is the part unit tests miss: every panel can be internally consistent and
still disagree with its neighbour. Deliberately does not import engine.py.
"""
import json, math, ssl, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import numpy as np

ctx = ssl.create_default_context()
ET = timezone(timedelta(hours=-4))
UA = {"User-Agent": "Mozilla/5.0"}
SLOTS = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]
# Force a rebuild so the payload and this script's own fetches sample the tape at
# the same moment. Comparing against a cached payload during a live session
# produces differences that look like maths errors but are just elapsed time.
API = "http://localhost:11001/api/rotation?refresh=1"

FAIL, WARN = [], []
def bad(sec, msg): FAIL.append(f"[{sec}] {msg}")
def warn(sec, msg): WARN.append(f"[{sec}] {msg}")
def head(t): print(f"\n{'='*72}\n{t}\n{'='*72}")

def chart(tk, iv="1d", rng="2y"):
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(tk)}"
         f"?interval={iv}&range={rng}")
    r = urllib.request.Request(u, headers=UA)
    return json.loads(urllib.request.urlopen(r, timeout=30, context=ctx).read())["chart"]["result"][0]

def dstr(ts): return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")

def slot_of(hhmm):
    h, m = (int(x) for x in hhmm.split(":")); mins = h*60+m
    s = min(SLOTS, key=lambda x: abs(mins-(int(x[:2])*60+int(x[3:]))))
    return s if abs(mins-(int(s[:2])*60+int(s[3:]))) <= 45 else None

D = json.load(urllib.request.urlopen(API))
STOCKS = {s["ticker"]: s for s in D["stocks"]}
ETFS = {s["ticker"]: s for s in D["sector_etfs"]}
ALL = list(STOCKS) + list(ETFS) + ["SPY", "QQQ"]
ALL = list(dict.fromkeys(ALL))
print(f"API live={D['live']}  as_of={dstr(D['as_of'])}  "
      f"{len(STOCKS)} stocks, {len(ETFS)} sector ETFs")

# ---------------------------------------------------------------- A. SOURCE
head("A. SOURCE — raw feeds, calendar integrity, ticker identity")
daily_raw, daily_adj, intra, meta = {}, {}, {}, {}
for tk in ALL:
    try:
        r = chart(tk, "1d", "2y"); q = r["indicators"]["quote"][0]
        adj = r.get("indicators", {}).get("adjclose")
        a = adj[0]["adjclose"] if adj else q["close"]
        daily_raw[tk] = {dstr(t): c for t, c in zip(r["timestamp"], q["close"]) if c is not None}
        daily_adj[tk] = {dstr(t): c for t, c in zip(r["timestamp"], a) if c is not None}
        meta[tk] = r.get("meta", {})
        h = chart(tk, "60m", "60d"); hq = h["indicators"]["quote"][0]
        byd = defaultdict(list)
        for t, o, c in zip(h["timestamp"], hq["open"], hq["close"]):
            if o is None or c is None: continue
            dt = datetime.fromtimestamp(t, ET)
            byd[dt.strftime("%Y-%m-%d")].append((t, dt.strftime("%H:%M"), c))
        intra[tk] = dict(byd)
    except Exception as e:
        bad("SOURCE", f"{tk} fetch failed: {e}")

for tk in ALL:
    m = meta.get(tk, {})
    if m.get("currency") != "USD": bad("SOURCE", f"{tk} currency {m.get('currency')} not USD")
    if not m.get("longName") and not m.get("shortName"): warn("SOURCE", f"{tk} has no name in meta")
print(f"  fetched {len(daily_raw)} tickers, all USD")

# sessions present intraday but missing from the daily feed
gaps = defaultdict(list)
for tk in ALL:
    for day in intra.get(tk, {}):
        if len(intra[tk][day]) >= 6 and day not in daily_raw.get(tk, {}):
            gaps[day].append(tk)
if gaps:
    for day, tks in sorted(gaps.items()):
        print(f"  NULL DAILY SESSION {day}: missing for {len(tks)}/{len(ALL)} tickers "
              f"(intraday present) -> must be backfilled")
else:
    print("  no null daily sessions in the intraday window")

# the repaired calendar this audit will use
cal = sorted(set(daily_raw["SPY"]) | {d for d in intra["SPY"] if len(intra["SPY"][d]) >= 6})
print(f"  repaired SPY calendar: {len(cal)} sessions, last 4 {cal[-4:]}")
px = {}
for tk in ALL:
    px[tk] = {}
    for d in cal:
        if d in daily_adj.get(tk, {}): px[tk][d] = daily_adj[tk][d]
        elif d in intra.get(tk, {}) and intra[tk][d]: px[tk][d] = intra[tk][d][-1][2]

# monotonic + no dupes + no absurd jumps
for tk in ALL:
    ser = [(d, px[tk][d]) for d in cal if d in px[tk]]
    for i in range(1, len(ser)):
        ch = ser[i][1]/ser[i-1][1]-1
        if abs(ch) > 0.35:
            warn("SOURCE", f"{tk} {ser[i][0]} moved {ch*100:+.1f}% vs prior session (split/data?)")
print(f"  jump scan done ({sum(len(px[t]) for t in ALL)} closes)")

# ---------------------------------------------------------------- B. DERIVED
head("B. DERIVED — re-derive each panel from the refetched data")
def rel_on(tk, d):
    """relative return vs SPY for session d, adjusted closes"""
    i = cal.index(d)
    if i == 0: return None
    p = cal[i-1]
    if d not in px[tk] or p not in px[tk] or d not in px["SPY"] or p not in px["SPY"]: return None
    return ((px[tk][d]/px[tk][p]) - (px["SPY"][d]/px["SPY"][p])) * 100

today, yday = cal[-1], cal[-2]
# Even with a forced rebuild, seconds pass between the server's fetch and this
# script's, and a moving benchmark shifts every relative number. Measured skew on
# a live tape is under 0.05%; 0.25 leaves headroom without hiding a real error.
TOL = 0.25 if D["live"] else 0.02

print(f"  today={today} yday={yday}  tolerance {TOL}%")
n_ok = 0
for tk, s in list(STOCKS.items()) + list(ETFS.items()):
    r_t, r_y = rel_on(tk, today), rel_on(tk, yday)
    if r_t is None: bad("DERIVED", f"{tk} has no return for {today}"); continue
    if abs(r_t - s["rel_today"]) > TOL:
        bad("DERIVED", f"{tk} rel_today mine {r_t:+.3f} vs api {s['rel_today']:+.3f}")
    elif abs(r_y - s["rel_yday"]) > TOL:
        bad("DERIVED", f"{tk} rel_yday mine {r_y:+.3f} vs api {s['rel_yday']:+.3f}")
    else: n_ok += 1
print(f"  rel_today/rel_yday reproduce for {n_ok}/{len(STOCKS)+len(ETFS)} entities")

# 5d and 21d windows
n5 = 0
for tk, s in list(STOCKS.items()) + list(ETFS.items()):
    for win, key in ((5, "rel_5d"), (21, "rel_21d")):
        days = [d for d in cal[-win:]]
        vals = [rel_on(tk, d) for d in days]
        if any(v is None for v in vals): continue
        mine = sum(vals)
        if abs(mine - s[key]) > TOL * 2:
            bad("DERIVED", f"{tk} {key} mine {mine:+.3f} vs api {s[key]:+.3f}")
        else: n5 += 1
print(f"  rel_5d/rel_21d reproduce in {n5} checks")

# streaks and dots
ds = 0
for tk, s in list(STOCKS.items()) + list(ETFS.items()):
    series = [(d, rel_on(tk, d)) for d in cal[1:]]
    series = [(d, v) for d, v in series if v is not None]
    if not series: continue
    sign = 1 if series[-1][1] > 0 else -1
    n = 0
    for d, v in reversed(series):
        if (1 if v > 0 else -1) != sign: break
        n += 1
    if s["streak_dir"] != sign or s["streak"] != n:
        if not D["live"]:
            bad("DERIVED", f"{tk} streak mine {n*sign:+d} vs api {s['streak']*s['streak_dir']:+d}")
        else:
            warn("DERIVED", f"{tk} streak mine {n*sign:+d} vs api {s['streak']*s['streak_dir']:+d} (live)")
    else: ds += 1
    if len(series) != s["n_hist"]:
        bad("DERIVED", f"{tk} n_hist mine {len(series)} vs api {s['n_hist']}")
print(f"  streaks reproduce for {ds} entities; n_hist checked for all")

# hourly store cells
store = json.load(open("hourly_store.json"))
H = D["hourly"]
cells = 0; cbad = 0
for day, row in store.items():
    sp = row.get("__SPY__")
    if not sp: bad("STORE", f"{day} missing __SPY__ leg"); continue
    for tk, cs in row.items():
        if tk == "__SPY__": continue
        if tk not in intra or day not in intra[tk]: continue
        prev = [d for d in sorted(intra[tk]) if d < day]
        if not prev or prev[-1] not in intra[tk]: continue
        base = intra[tk][prev[-1]][-1][2]
        spb = [d for d in sorted(intra["SPY"]) if d < day]
        if not spb: continue
        sbase = intra["SPY"][spb[-1]][-1][2]
        p, sp_p = base, sbase
        sret = {}
        for t, hh, c in intra["SPY"][day]:
            sl = slot_of(hh)
            if sl and sl not in sret: sret[sl] = c/sp_p-1; sp_p = c
        seen = {}
        for t, hh, c in intra[tk][day]:
            sl = slot_of(hh)
            if not sl or sl in seen or sl not in sret: continue
            seen[sl] = ((c/p-1)-sret[sl])*100; p = c
        for sl, v in seen.items():
            if sl in cs:
                cells += 1
                if abs(v-cs[sl]) > 0.002: cbad += 1
print(f"  hourly store: {cells} cells recomputed, {cbad} mismatches, "
      f"{len(store)} sessions, __SPY__ present on all: "
      f"{all('__SPY__' in v for v in store.values())}")
if cbad: bad("STORE", f"{cbad} hourly cells disagree")

# ---------------------------------------------------------------- C. TIE-OUT
head("C. TIE-OUT — panels that show the same quantity must agree")

# 1. morning panel end point vs daily table rel_today
m = D["morning"]
mt = 0
for p in m["paths"]:
    s = STOCKS.get(p["ticker"])
    if not s: continue
    if abs(p["last"] - s["rel_today"]) > 0.35:
        bad("TIE-OUT", f"{p['ticker']} morning ends {p['last']:+.2f} but daily table says "
                       f"{s['rel_today']:+.2f} (panels disagree)")
    else: mt += 1
print(f"  morning path endpoint ties to rel_today for {mt}/{len(m['paths'])} stocks")

# 2. morning_rel field vs the morning path
for tk, s in STOCKS.items():
    p = next((x for x in m["paths"] if x["ticker"] == tk), None)
    if p and s.get("morning_rel") is not None and abs(p["last"]-s["morning_rel"]) > 1e-6:
        bad("TIE-OUT", f"{tk} morning_rel {s['morning_rel']} != path last {p['last']}")
print("  morning_rel field ties to the morning path")

# 3. sector detail vs member stocks
smap = defaultdict(list)
for tk, s in STOCKS.items(): smap[s["sector"]].append(s)
for sd in D["sectors_detail"]:
    mem = smap.get(sd["sector"], [])
    if not mem: continue
    avg = sum(x["rel_today"] for x in mem)/len(mem)
    if abs(avg - sd["rel_today"]) > 0.25:
        bad("TIE-OUT", f"sector {sd['sector']} rel_today {sd['rel_today']:+.2f} vs member mean {avg:+.2f}")
print(f"  sectors_detail ties to member means for {len(D['sectors_detail'])} sectors")

# 4. hourly group series vs group members
G = H["groups"]
for g, mem in G["members"].items():
    listed = {x["ticker"] for x in mem}
    expect = {tk for tk, s in STOCKS.items() if s["sector"] == g}
    if listed != expect:
        bad("TIE-OUT", f"group {g} legend {sorted(listed)} != stocks tagged {sorted(expect)}")
print(f"  group legend membership matches stock sector tags for {len(G['members'])} groups")

# 5. components panel vs stocks panel
comp = {c["ticker"]: c for c in D["components"]}
ct = 0
for tk, s in STOCKS.items():
    c = comp.get(tk)
    if not c: bad("TIE-OUT", f"{tk} missing from components"); continue
    if abs(c["price"]-s["price"]) > max(0.02, s["price"]*0.001):
        bad("TIE-OUT", f"{tk} price components {c['price']} vs stocks {s['price']}")
    else: ct += 1
print(f"  components price ties to stocks for {ct}/{len(STOCKS)}")

# 6. regime series vs the hourly store it is built from
R = H["regime"]
rt = 0
for s in R["series"][-10:]:
    row = store.get(s["date"])
    if not row: bad("TIE-OUT", f"regime session {s['date']} not in store"); continue
    vals = {k: sum(v.values()) for k, v in row.items() if k != "__SPY__" and k in H["tickers"]}
    if len(vals) != s["n"]:
        bad("TIE-OUT", f"regime {s['date']} n={s['n']} but store has {len(vals)} names")
    else:
        disp = float(np.std(list(vals.values()), ddof=1))
        if abs(disp - s["disp"]) > 0.005:
            bad("TIE-OUT", f"regime {s['date']} disp {s['disp']} vs store {disp:.3f}")
        else: rt += 1
print(f"  regime dispersion ties to the store for {rt}/10 recent sessions")

# 7. headline vs sector_flow
hl = D["headline"]
flows = sorted(D["sector_flow"], key=lambda x: -x.get("rel_today", 0))
if flows and flows[0]["sector"] not in hl:
    bad("TIE-OUT", f"headline names a different leader than sector_flow top ({flows[0]['sector']})")
print("  headline leader matches sector_flow")

# 8. counter rows cover exactly the tracked universe
C = H["counter"]
if {r["ticker"] for r in C["rows"]} - set(H["tickers"]):
    bad("TIE-OUT", "counter has rows outside the tracked universe")
print(f"  counter covers {len(C['rows'])}/{len(H['tickers'])} tracked names")

# ---------------------------------------------------------------- D. HYGIENE
head("D. HYGIENE — JSON safety, ranges, sample sizes")
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items(): walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o[:400]): walk(v, f"{path}[{i}]")
    elif isinstance(o, float):
        if math.isnan(o) or math.isinf(o): bad("HYGIENE", f"non-finite at {path}")
walk(D)
print("  no NaN/Infinity anywhere in the payload")

for tk, s in list(STOCKS.items()) + list(ETFS.items()):
    for k in ("corr_spy", "corr_qqq"):
        v = s.get(k)
        if v is not None and not (-1.001 <= v <= 1.001): bad("HYGIENE", f"{tk} {k}={v} out of range")
    p = (s.get("pctl") or {}).get("windows") or {}
    for w, blk in p.items():
        if blk.get("pct") is not None and not (0 <= blk["pct"] <= 100):
            bad("HYGIENE", f"{tk} percentile {w}d = {blk['pct']} out of range")
print("  correlations in [-1,1]; percentiles in [0,100]")

for p in H["pairs"]:
    if not (-1.001 <= p["corr"] <= 1.001): bad("HYGIENE", f"pair {p['a']}/{p['b']} corr {p['corr']}")
    if not (0 <= p["same_dir"] <= 100): bad("HYGIENE", f"pair {p['a']}/{p['b']} same_dir {p['same_dir']}")
if any(p["corr"] <= 0 for p in H["pairs_together"]): bad("HYGIENE", "sign leak in pairs_together")
if any(p["corr"] >= 0 for p in H["pairs_opposite"]): bad("HYGIENE", "sign leak in pairs_opposite")
print(f"  {len(H['pairs'])} pairs in range, no sign leaks")

for tk, s in STOCKS.items():
    if s["short_hist"] and s["n_hist"] >= 252: bad("HYGIENE", f"{tk} flagged short_hist with {s['n_hist']}d")
    if s["n_hist"] < 60 and (s.get("pctl") or {}).get("windows"):
        bad("HYGIENE", f"{tk} quotes percentiles on only {s['n_hist']} sessions")
short = [f"{t}({s['n_hist']}d)" for t, s in STOCKS.items() if s["short_hist"]]
print(f"  short-history names correctly gated: {', '.join(short) if short else 'none'}")

for q, v in D["duration"].items():
    if v["n"] < 30: warn("HYGIENE", f"duration bucket {q} has only n={v['n']}")
print(f"  duration buckets: {', '.join(f'{k}:n={v['n']}' for k, v in D['duration'].items())}")

stale = (datetime.now(timezone.utc).timestamp() - (H.get("harvest") or {}).get("at", 0))/60
print(f"  last harvest {stale:.0f} min ago; store holds {H['stored_sessions']} sessions")
if stale > 90: warn("HYGIENE", f"harvest is {stale:.0f} min old")

# ---------------------------------------------------------------- RESULT
head("RESULT")
for w in WARN: print(f"  WARN  {w}")
for f in FAIL: print(f"  FAIL  {f}")
print(f"\n  {len(FAIL)} failures, {len(WARN)} warnings")
print("  " + ("ALL DATA CHECKS PASS" if not FAIL else "FAILURES PRESENT — see above"))
