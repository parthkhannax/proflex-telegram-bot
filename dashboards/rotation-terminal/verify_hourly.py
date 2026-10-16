#!/usr/bin/env python3
"""Independent recompute of the hourly pattern screen.

Refetches Yahoo directly and rebuilds a sample of grid cells and pair
correlations from scratch, without importing anything from engine.py's pattern
section, then compares against what the API is serving."""
import json, ssl, urllib.request, urllib.parse, random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import numpy as np

ET = timezone(timedelta(hours=-4))
ctx = ssl.create_default_context()
SLOTS = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]

def bars(tk):
    u=(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(tk)}"
       f"?interval=60m&range=60d")
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    res=json.loads(urllib.request.urlopen(r,timeout=25,context=ctx).read())["chart"]["result"][0]
    q=res["indicators"]["quote"][0]
    out=defaultdict(list)
    for t,o,c in zip(res["timestamp"],q["open"],q["close"]):
        if o is None or c is None: continue
        d=datetime.fromtimestamp(t,ET)
        out[d.strftime("%Y-%m-%d")].append((d.strftime("%H:%M"),c))
    return dict(out)

def slot(h):
    hh,mm=(int(x) for x in h.split(":")); m=hh*60+mm
    best,bd=None,10**9
    for s in SLOTS:
        sh,sm=(int(x) for x in s.split(":")); d=abs(m-(sh*60+sm))
        if d<bd: best,bd=s,d
    return best if bd<=45 else None

def relseries(tk, spy):
    """{(date,slot): rel%} recomputed from scratch."""
    d=bars(tk); sess=sorted(spy)
    out={}
    for i in range(1,len(sess)):
        day, prevday = sess[i], sess[i-1]
        if day not in d or prevday not in d: continue
        if len(spy[day])<6 or len(d[day])<6: continue
        sbase=spy[prevday][-1][1]; base=d[prevday][-1][1]
        s_ret={}; p=sbase
        for h,c in spy[day]:
            sl=slot(h)
            if sl is None or sl in s_ret: continue
            s_ret[sl]=c/p-1; p=c
        p=base
        for h,c in d[day]:
            sl=slot(h)
            if sl is None or (day,sl) in out: continue
            if sl not in s_ret: continue
            out[(day,sl)]=((c/p-1)-s_ret[sl])*100; p=c
    return out

api=json.load(urllib.request.urlopen("http://localhost:11001/api/rotation"))
H=api["hourly"]
spy=bars("SPY")
print(f"API: {H['sessions']} sessions, {H['tested']} cells tested, alpha={H['alpha']}")

random.seed(7)
picks=random.sample(H["tickers"], 5)
series={t:relseries(t,spy) for t in set(picks+["SPY"])-{"SPY"}}

print("\n--- GRID CELLS (independent recompute vs API) ---")
bad=0
for t in picks:
    g=next(x for x in H["grid"] if x["ticker"]==t)
    for c in g["cells"]:
        if c["mean"] is None: continue
        vals=[v for (d,sl),v in series[t].items() if sl==c["slot"]]
        if not vals: continue
        mine_mean=round(float(np.mean(vals)),3)
        nz=[v for v in vals if v!=0]
        mine_hit=round(sum(1 for v in nz if v>0)/len(nz)*100,1) if nz else None
        ok = abs(mine_mean-c["mean"])<0.002 and len(vals)==c["n"] and abs(mine_hit-c["hit"])<0.2
        if not ok:
            bad+=1
            print(f"  MISMATCH {t} {c['slot']}: api mean={c['mean']} n={c['n']} hit={c['hit']} "
                  f"| mine mean={mine_mean} n={len(vals)} hit={mine_hit}")
print(f"  {bad} mismatches across {len(picks)} tickers x 7 slots")

print("\n--- PAIR CORRELATIONS ---")
pbad=0
for i,a in enumerate(picks):
    for b in picks[i+1:]:
        api_p=next((p for p in H["pairs"] if {p["a"],p["b"]}=={a,b}),None)
        if not api_p: continue
        keys=sorted(series[a].keys() & series[b].keys())
        x=np.array([series[a][k] for k in keys]); y=np.array([series[b][k] for k in keys])
        r=round(float(np.corrcoef(x,y)[0,1]),3)
        same=round(float(((x>0)==(y>0)).mean()*100),1)
        ok=abs(r-api_p["corr"])<0.005 and len(keys)==api_p["n"] and abs(same-api_p["same_dir"])<0.2
        if not ok:
            pbad+=1
            print(f"  MISMATCH {a}/{b}: api r={api_p['corr']} n={api_p['n']} same={api_p['same_dir']} "
                  f"| mine r={r} n={len(keys)} same={same}")
        else:
            print(f"  ok {a:5}/{b:5} r={r:+.3f} same={same:5.1f}% n={len(keys)}")
print(f"  {pbad} pair mismatches")

print("\n--- SIGNIFICANCE BAR ---")
from math import comb
def binom(k,n):
    obs=comb(n,k)*0.5**n
    return sum(comb(n,i)*0.5**n for i in range(n+1) if comb(n,i)*0.5**n<=obs*(1+1e-9))
worst=[]
for g in H["grid"]:
    for c in g["cells"]:
        if c["p"] is None: continue
        worst.append((c["p"],g["ticker"],c["slot"],c["hit"],c["n"],c["sig"]))
worst.sort()
print(f"  alpha (Bonferroni 0.05/{H['tested']}) = {H['alpha']}")
for p,t,sl,hit,n,sig in worst[:5]:
    print(f"  best cell: {t:5} {sl}  hit={hit}%  n={n}  p={p}  sig={sig}")
print(f"  cells marked sig: {sum(1 for w in worst if w[5])}")

print("\n--- STORE ---")
st=json.load(open("hourly_store.json"))
print(f"  {len(st)} sessions on disk, {sum(len(v) for v in st.values())} ticker-days, "
      f"{sum(len(c) for v in st.values() for c in v.values())} hour cells")
print(f"  range {min(st)} → {max(st)}")
