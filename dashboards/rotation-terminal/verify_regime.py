#!/usr/bin/env python3
"""Independent recompute of the rotation-regime panel from a fresh Yahoo fetch.

Rebuilds dispersion, average pairwise correlation, tercile spread, breadth and
the regime label from their definitions, without reusing engine.py's code."""
import json, urllib.request
import numpy as np
from verify_hourly import bars, relseries

api = json.load(urllib.request.urlopen("http://localhost:11001/api/rotation"))
H = api["hourly"]; SEC = H["sectors"]
spy = bars("SPY")
S = {t: relseries(t, spy) for t in H["tickers"]}
SLOTS = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]

# SPY's own hourly return, rebuilt independently (raw = rel + spy)
spyret, sess_all = {}, sorted(spy)
for i in range(1, len(sess_all)):
    day, prev = sess_all[i], sess_all[i-1]
    if len(spy.get(day, [])) < 6: continue
    p = spy[prev][-1][1]; seen = {}
    for h, c in spy[day]:
        hh, mm = (int(x) for x in h.split(":")); m = hh*60+mm
        sl = min(SLOTS, key=lambda s: abs(m-(int(s[:2])*60+int(s[3:]))))
        if abs(m-(int(sl[:2])*60+int(sl[3:]))) > 45 or sl in seen: continue
        seen[sl] = (c/p-1)*100; p = c
    spyret[day] = seen

def check(series_by_key, keys, R, label):
    print(f"\n--- {label} ---")
    daily = {}
    for k in keys:
        for (d, sl), v in series_by_key[k].items():
            daily.setdefault(d, {}).setdefault(k, 0.0)
            daily[d][k] += v
    bad = 0
    api_by_date = {s["date"]: s for s in R["series"]}
    kcut = R["kcut"]
    for d, row in sorted(daily.items()):
        a = api_by_date.get(d)
        if not a: continue
        vals = np.array(list(row.values()))
        disp = round(float(vals.std(ddof=1)), 3)
        order = sorted(row.items(), key=lambda kv: -kv[1])
        spread = round(float(np.mean([v for _, v in order[:kcut]])
                             - np.mean([v for _, v in order[-kcut:]])), 3)
        breadth = int((vals > 0).sum())
        errs = []
        if abs(disp-a["disp"]) > 0.002: errs.append(f"disp mine={disp} api={a['disp']}")
        if abs(spread-a["spread"]) > 0.005: errs.append(f"spread mine={spread} api={a['spread']}")
        if breadth != a["breadth"]: errs.append(f"breadth mine={breadth} api={a['breadth']}")
        if len(row) != a["n"]: errs.append(f"n mine={len(row)} api={a['n']}")
        if order[0][0] != a["leader"]: errs.append(f"leader mine={order[0][0]} api={a['leader']}")
        if errs: bad += 1; print(f"  MISMATCH {d}: "+"; ".join(errs))
    print(f"  {bad} session mismatches across {len(api_by_date)} sessions")

    # average pairwise correlation on RAW returns, trailing window
    sess = sorted(daily)
    cbad = 0; checked = 0
    for i, d in enumerate(sess):
        a = api_by_date.get(d)
        if not a or a["corr"] is None: continue
        win = set(sess[max(0, i-R["corr_win"]+1):i+1])
        raw = {k: {(dd, sl): v + spyret[dd][sl]
                   for (dd, sl), v in series_by_key[k].items()
                   if dd in win and dd in spyret and sl in spyret[dd]} for k in keys}
        ks = [k for k in keys if len(raw[k]) >= 20]
        rs = []
        for ai, x in enumerate(ks):
            for y in ks[ai+1:]:
                c = sorted(raw[x].keys() & raw[y].keys())
                if len(c) < 20: continue
                rs.append(float(np.corrcoef([raw[x][j] for j in c], [raw[y][j] for j in c])[0,1]))
        mine = round(float(np.mean(rs)), 3)
        checked += 1
        if abs(mine - a["corr"]) > 0.002:
            cbad += 1; print(f"  CORR MISMATCH {d}: mine={mine} api={a['corr']}")
    print(f"  {cbad} correlation mismatches across {checked} sessions")

    # regime labels follow from the medians
    sc = [s for s in R["series"] if s["corr"] is not None]
    dm = float(np.median([s["disp"] for s in sc])); cm = float(np.median([s["corr"] for s in sc]))
    assert abs(dm-R["disp_median"])<0.002 and abs(cm-R["corr_median"])<0.002, "median mismatch"
    rbad = 0
    for s in sc:
        want = ("rotation" if s["disp"]>=dm and s["corr"]<cm else
                "split_trend" if s["disp"]>=dm else "macro" if s["corr"]>=cm else "quiet")
        if want != s["regime"]: rbad += 1; print(f"  REGIME MISMATCH {s['date']}: {want} vs {s['regime']}")
    print(f"  {rbad} regime-label mismatches across {len(sc)} sessions")
    return bad+cbad+rbad

# group series = equal-weight member mean per hour
G = H["groups"]
gser = {}
for g in G["keys"]:
    acc = {}
    for t in [x for x in H["tickers"] if SEC[x]==g]:
        for k, v in S[t].items(): acc.setdefault(k, []).append(v)
    gser[g] = {k: float(np.mean(v)) for k, v in acc.items()}

tot = check(S, H["tickers"], H["regime"], "STOCKS")
tot += check(gser, G["keys"], G["regime"], "GROUPS")

print("\n--- DISPERSION INVARIANCE (claim made on the panel) ---")
# dispersion must be identical on raw and on index-relative returns, because
# rel_i = raw_i - spy subtracts the SAME constant from every name that session.
d = sorted(S["NVDA"].keys())[0][0]
for day in list(sorted({k[0] for k in S["NVDA"]}))[:3]:
    rel = [sum(v for (dd,sl),v in S[t].items() if dd==day) for t in H["tickers"] if any(k[0]==day for k in S[t])]
    sp = sum(spyret[day].values())
    rawv = [x+sp for x in rel]
    print(f"  {day}: stdev(relative)={np.std(rel,ddof=1):.6f}  stdev(raw)={np.std(rawv,ddof=1):.6f}  "
          f"identical={abs(np.std(rel,ddof=1)-np.std(rawv,ddof=1))<1e-9}")

print("\n--- RAW RECONSTRUCTION EXACTNESS ---")
# raw from the store must equal raw fetched straight off Yahoo
nv = bars("NVDA"); sess2 = sorted(spy); ok = 0; tested = 0
for i in range(1, len(sess2)):
    day, prev = sess2[i], sess2[i-1]
    if day not in spyret or day not in nv or prev not in nv or len(nv[day]) < 6: continue
    p = nv[prev][-1][1]; seen = {}
    for h, c in nv[day]:
        hh, mm = (int(x) for x in h.split(":")); m = hh*60+mm
        sl = min(SLOTS, key=lambda s: abs(m-(int(s[:2])*60+int(s[3:]))))
        if abs(m-(int(sl[:2])*60+int(sl[3:]))) > 45 or sl in seen: continue
        seen[sl] = (c/p-1)*100; p = c
    for sl, direct in seen.items():
        if (day, sl) not in S["NVDA"] or sl not in spyret[day]: continue
        recon = S["NVDA"][(day, sl)] + spyret[day][sl]
        tested += 1; ok += abs(recon-direct) < 1e-6
print(f"  {ok}/{tested} NVDA hourly raw returns reconstruct exactly from rel + SPY")

print(f"\nTOTAL MISMATCHES: {tot}")
