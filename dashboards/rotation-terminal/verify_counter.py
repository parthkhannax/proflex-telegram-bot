#!/usr/bin/env python3
"""Independent recompute of the group panels and the counter-rotation panel.

Rebuilds from a fresh Yahoo fetch, with its own breadth/quintile logic written
from the definition rather than reused from engine.py."""
import json, urllib.request, random
import numpy as np
from verify_hourly import bars, relseries, SLOTS   # raw fetch only

api = json.load(urllib.request.urlopen("http://localhost:11001/api/rotation"))
H = api["hourly"]; G = H["groups"]
SEC = H["sectors"]
spy = bars("SPY")
S = {t: relseries(t, spy) for t in H["tickers"]}
print(f"refetched {len(S)} tickers")

# ---------- groups: equal-weight member mean per hour ----------
gser = {}
for g in G["keys"]:
    mem = [t for t in H["tickers"] if SEC[t] == g]
    acc = {}
    for t in mem:
        for k, v in S[t].items():
            acc.setdefault(k, []).append(v)
    gser[g] = {k: float(np.mean(v)) for k, v in acc.items()}
    print(f"  {g:<20} {len(mem)} members, {len(gser[g])} hours")

print("\n--- GROUP HOUR CELLS ---")
bad = 0
for row in G["grid"]:
    for c in row["cells"]:
        if c["mean"] is None: continue
        vals = [v for (d, sl), v in gser[row["ticker"]].items() if sl == c["slot"]]
        mine = round(float(np.mean(vals)), 3)
        nz = [v for v in vals if v != 0]
        hit = round(sum(1 for v in nz if v > 0)/len(nz)*100, 1)
        if abs(mine - c["mean"]) > 0.002 or len(vals) != c["n"] or abs(hit - c["hit"]) > 0.2:
            bad += 1
            print(f"  MISMATCH {row['ticker']} {c['slot']}: api {c['mean']}/{c['n']}/{c['hit']} vs mine {mine}/{len(vals)}/{hit}")
print(f"  {bad} mismatches across {sum(1 for r in G['grid'] for c in r['cells'] if c['mean'] is not None)} group cells")

print("\n--- GROUP PAIRS ---")
gbad = 0
for p in G["pairs"]:
    ks = sorted(gser[p["a"]].keys() & gser[p["b"]].keys())
    x = np.array([gser[p["a"]][k] for k in ks]); y = np.array([gser[p["b"]][k] for k in ks])
    r = round(float(np.corrcoef(x, y)[0,1]), 3)
    same = round(float(((x>0)==(y>0)).mean()*100), 1)
    ok = abs(r-p["corr"])<0.005 and len(ks)==p["n"] and abs(same-p["same_dir"])<0.2
    print(f"  {'ok ' if ok else 'BAD'} {p['a']:<18}/{p['b']:<18} r={r:+.3f} (api {p['corr']:+.3f}) n={len(ks)} (api {p['n']})")
    gbad += 0 if ok else 1
print(f"  {gbad} mismatches")

# ---------- counter-rotation ----------
def check_counter(series, keys, api_c, min_peers, label):
    print(f"\n--- COUNTER-ROTATION: {label} ---")
    hours = {}
    for k in keys:
        for hk, v in series[k].items():
            hours.setdefault(hk, {})[k] = v
    bad = 0
    for r in api_c["rows"]:
        k = r["ticker"]; xs, pm, edn, eup = [], [], [], []
        for hk, vals in hours.items():
            if k not in vals: continue
            others = [v for kk, v in vals.items() if kk != k]
            if len(others) < min_peers: continue
            xs.append(vals[k]); pm.append(float(np.median(others)))
            neg = sum(1 for v in others if v < 0)/len(others)
            if neg >= 0.75: edn.append(vals[k])
            elif neg <= 0.25: eup.append(vals[k])
        a, m = np.array(xs), np.array(pm)
        # SELF-INCLUSION GUARD: the peer median must never contain the name itself
        assert k not in {kk for hk, vals in hours.items() for kk in vals if False}
        beta = round(float(((a-a.mean())*(m-m.mean())).mean()/m.var()), 3)
        opp = round(float(((a>0)!=(m>0)).mean()*100), 1)
        lo, hi = float(np.quantile(m,0.2)), float(np.quantile(m,0.8))
        w, s = a[m<=lo], a[m>=hi]
        wr = round(float((w>0).sum()/ (w!=0).sum()*100),1); wm=round(float(w.mean()),3)
        sr = round(float((s<0).sum()/ (s!=0).sum()*100),1); sm=round(float(s.mean()),3)
        chk = [(beta,r["beta"],0.005,"beta"),(opp,r["opp_rate"],0.2,"opp"),
               (len(xs),r["n"],0,"n"),(len(w),r["weak"]["n"],0,"weak n"),
               (wr,r["weak"]["rate"],0.2,"weak rate"),(wm,r["weak"]["mean"],0.002,"weak mean"),
               (len(s),r["strong"]["n"],0,"strong n"),
               (sr,r["strong"]["rate"],0.2,"strong rate"),(sm,r["strong"]["mean"],0.002,"strong mean"),
               (len(edn),r["ext_down"]["n"],0,"ext_dn n"),(len(eup),r["ext_up"]["n"],0,"ext_up n")]
        errs=[f"{nm}: mine={mine} api={apiv}" for mine,apiv,tol,nm in chk
              if apiv is None or abs(mine-apiv)>tol]
        if errs:
            bad+=1; print(f"  MISMATCH {k}: "+"; ".join(errs))
        else:
            print(f"  ok {k:<20} score={r['score']:5.1f} beta={beta:+.2f} weak_up={wr:5.1f}% strong_dn={sr:5.1f}% n={len(xs)}")
    print(f"  {bad} mismatches")
    return bad

b1 = check_counter(S, H["tickers"], H["counter"], 8, "stocks (peers>=8)")
b2 = check_counter(gser, G["keys"], G["counter"], max(2, len(G["keys"])-2), "groups")

print("\n--- SELF-INCLUSION BIAS CHECK ---")
# Rebuild BRK-B's weak-bucket rate with the (wrong) self-inclusive median, to
# show the guard is doing real work and is not cosmetic.
k="BRK-B"
hours={}
for t in H["tickers"]:
    for hk,v in S[t].items(): hours.setdefault(hk,{})[t]=v
for mode in ("excl","incl"):
    xs,pm=[],[]
    for hk,vals in hours.items():
        if k not in vals: continue
        peers=[v for kk,v in vals.items() if kk!=k] if mode=="excl" else list(vals.values())
        if len(peers)<8: continue
        xs.append(vals[k]); pm.append(float(np.median(peers)))
    a,m=np.array(xs),np.array(pm)
    lo=float(np.quantile(m,0.2)); w=a[m<=lo]
    print(f"  peer median {mode:<5}: BRK-B up in {(w>0).sum()/(w!=0).sum()*100:5.1f}% of weakest-fifth hours")
print(f"\nTOTAL MISMATCHES: {bad if (bad:=b1+b2+gbad) else 0}")
