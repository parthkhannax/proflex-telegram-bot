"""Independently re-derive the dashboard's numbers from raw Yahoo daily closes.

Deliberately does NOT import yahoo_http -- it re-fetches with its own request
and its own date arithmetic, so a bug in the client cannot cancel itself out.
"""
import sys, os, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json, urllib.request
from yfinance.data import YfData
import gs_baskets, gics_baskets

d = YfData()

def closes(sym):
    j = d.get_raw_json("https://query2.finance.yahoo.com/v8/finance/chart/" + sym,
                       params={"range": "2y", "interval": "1d"})
    r = j["chart"]["result"][0]; m = r["meta"]; off = m.get("gmtoffset", 0)
    ser = [(dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date(), c)
           for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    sess = dt.datetime.fromtimestamp(m["regularMarketTime"] + off, dt.timezone.utc).date()
    return m["regularMarketPrice"], sess, ser

def ret(sym, days=None):
    last, sess, ser = closes(sym)
    prior = [(dd, c) for dd, c in ser if dd < sess] or ser[:-1]
    if days is None:
        return (last / prior[-1][1] - 1) * 100
    tgt = sess - dt.timedelta(days=days)
    w = [c for dd, c in prior if dd <= tgt]
    return (last / w[-1] - 1) * 100 if w else None

api = json.load(urllib.request.urlopen("http://localhost:5050/api/sectors"))
gs = json.load(urllib.request.urlopen("http://localhost:5050/api/gs-baskets"))

spy1y, spy3m = ret("SPY", 365), ret("SPY", 91)
spyd, spy5 = ret("SPY"), ret("SPY", 7)
fails = 0

print("--- sector tab: 3M / 1Y relative (5 samples) ---")
for row in api["rows"][:5]:
    t = row["ticker"]
    e3, e1 = ret(t, 91) - spy3m, ret(t, 365) - spy1y
    for label, got, exp in (("3M", row["rel_3m_pct"], e3), ("1Y", row["rel_1y_pct"], e1)):
        ok = abs(got - exp) < 0.02
        fails += not ok
        print(f"  {t:6} {label}  dash={got:8.2f}  indep={exp:8.2f}  {'OK' if ok else 'MISMATCH'}")

print("--- GS baskets: equal-weight aggregation (3 samples) ---")
for b in gs["baskets"][:3]:
    spec = gs_baskets.BASKETS[b["ticker"]]
    detail = json.load(urllib.request.urlopen("http://localhost:5050/api/gs-basket/" + b["ticker"]))
    members = [c["ticker"] for c in detail["constituents"]]
    vals = [ret(s) for s in members]
    w = spec.get("weights")
    if w:
        tot = sum(w[s] for s in members)
        exp = sum(v * w[s] for v, s in zip(vals, members)) / tot
    else:
        exp = sum(vals) / len(vals)
    exp_rel = exp - spyd
    ok = abs(b["rel_day_pct"] - exp_rel) < 0.05
    fails += not ok
    print(f"  {b['ticker']:9} {b['name'][:20]:22} n={len(members):3} dash={b['rel_day_pct']:7.2f}  indep={exp_rel:7.2f}  {'OK' if ok else 'MISMATCH'}")

print("--- GICS industry groups: membership + aggregation (3 samples) ---")
for b in gs["gics"][:3]:
    spec = gics_baskets.INDICES[b["ticker"]]
    detail = json.load(urllib.request.urlopen("http://localhost:5050/api/gs-basket/" + b["ticker"]))
    members = [c["ticker"] for c in detail["constituents"]]
    # membership must match the workbook exactly -- this panel is not a proxy
    same = sorted(members) == sorted(spec["members"])
    exp = sum(ret(s2) for s2 in members) / len(members) - spyd
    ok = abs(b["rel_day_pct"] - exp) < 0.05 and same
    fails += not ok
    print(f"  {b['ticker']:10} {b['name'][:26]:28} n={len(members):3} "
          f"membership={'exact' if same else 'DRIFT'}  dash={b['rel_day_pct']:7.2f}  "
          f"indep={exp:7.2f}  {'OK' if ok else 'MISMATCH'}")

n_gics = sum(len(v["members"]) for v in gics_baskets.INDICES.values())
print(f"  workbook totals: {len(gics_baskets.INDICES)} indices, {n_gics} constituent slots, "
      f"{len(gics_baskets.all_symbols())} unique symbols")

print(f"\n{'ALL CHECKS PASS' if not fails else str(fails)+' MISMATCHES'}")
sys.exit(1 if fails else 0)
