#!/usr/bin/env python3
"""Independent re-derivation of the dot chart, runs, run-length stats and the
percentile system, for both tracked sets (stocks and sector ETFs).

Like verify.py, this deliberately does NOT import engine.py — it refetches from
Yahoo and recomputes in plain Python, then diffs against whatever the live API
is serving on :11001. A bug in the engine cannot reproduce itself here.

Run:  python3 verify_dots.py
"""
import json
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone

ctx = ssl.create_default_context()
ET = timezone(timedelta(hours=-4))
UA = {"User-Agent": "Mozilla/5.0"}
STOCKS = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
          "BRK-B", "LLY", "AMD", "MU", "SKHY", "SPCX"]
ETFS = ["XLK", "XLC", "XLY", "XLF", "XLV", "XLI", "XLE", "XLP", "XLU",
        "XLB", "XLRE", "SMH", "IGV"]
CAP = 6
WINDOWS = [1, 3, 5, 10, 21, 63]
MIN_PCT = 60
MIN_POOL = 252


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
    a = adj[0]["adjclose"] if adj else q
    adjm = {ts: v for ts, v in zip(r["timestamp"], a) if v is not None}
    rawm = {ts: v for ts, v in zip(r["timestamp"], q) if v is not None}
    adjm, rawm = _repair(t, adjm, rawm)
    return adjm, rawm, r.get("meta", {})


ALL = STOCKS + ETFS
adjc, rawc, meta = {}, {}, {}
for t in ALL + ["SPY"]:
    adjc[t], rawc[t], meta[t] = daily(t)

# SPY's calendar is the spine; each ticker keeps its own trailing history, which
# is the only way SPCX (weeks old) and NVDA (two years) can share one panel.
spine = sorted(adjc["SPY"])
last = spine[-1]
dts_all = [datetime.fromtimestamp(k, ET).strftime("%Y-%m-%d") for k in spine[1:]]
print(f"spine sessions {len(spine)}   last bar "
      f"{datetime.fromtimestamp(last, ET):%Y-%m-%d %H:%M ET}")
now = datetime.now(ET)
settled = not (datetime.fromtimestamp(last, ET).date() == now.date() and now.hour < 16)
print(f"now {now:%Y-%m-%d %H:%M ET}  →  last bar is "
      f"{'a settled session' if settled else 'TODAY, still forming'}")

spy_ret = [(adjc["SPY"][spine[i]] / adjc["SPY"][spine[i - 1]] - 1) * 100
           for i in range(1, len(spine))]

rel, dts = {}, {}
for t in ALL:
    d = adjc[t]
    have = [i for i in range(1, len(spine))
            if spine[i] in d and spine[i - 1] in d]
    # trailing contiguous run only
    tail = []
    for i in reversed(have):
        if tail and tail[-1] - i != 1:
            break
        tail.append(i)
    tail = sorted(tail)
    rel[t] = [(d[spine[i]] / d[spine[i - 1]] - 1) * 100 - spy_ret[i - 1] for i in tail]
    dts[t] = [dts_all[i - 1] for i in tail]


def runs(seq):
    out = []
    for v in seq:
        s = 1 if v > 0 else -1
        if out and out[-1][0] == s:
            out[-1][1] += 1
            out[-1][2] += v
        else:
            out.append([s, 1, v])
    return out


def streak(seq):
    s = 1 if seq[-1] > 0 else -1
    n = 0
    for v in reversed(seq):
        if (v > 0) == (s > 0):
            n += 1
        else:
            break
    return n, s


def roll(seq, w, f=sum):
    return [f(seq[i:i + w]) for i in range(len(seq) - w + 1)]


def rank(hist, val):
    below = sum(1 for h in hist if h < val)
    equal = sum(1 for h in hist if h == val)
    return round((below + 0.5 * equal) / len(hist) * 100, 1) if hist else None


def run_stats(names):
    cont = {k: [0, 0] for k in range(1, CAP + 1)}
    brk = {k: [] for k in range(1, CAP + 1)}
    for t in names:
        r = rel[t]
        if len(r) < MIN_POOL:
            continue
        n = len(r)
        sign = k = 0
        for i in range(n):
            s = 1 if r[i] > 0 else -1
            if s == sign:
                k += 1
            else:
                if sign != 0 and i + 4 <= n:
                    brk[min(k, CAP)].append(sum(r[i + 1:i + 4]) * s)
                sign, k = s, 1
            if i + 1 < n:
                cont[min(k, CAP)][1] += 1
                if (1 if r[i + 1] > 0 else -1) == s:
                    cont[min(k, CAP)][0] += 1
    return cont, brk


# During a live session the API's cached payload and this script's fetch are
# taken seconds apart on a moving tape, so exact equality is the wrong test for
# anything touching TODAY. Settled history is still compared exactly.
TOL_REL = 0.02 if settled else 0.20
TOL_PCT = 0.15 if settled else 4.0
TOL_VAL = 0.02 if settled else 0.30
TOL_PXR = 0.0 if settled else 0.006      # fraction of price
if not settled:
    print("\nLIVE SESSION — today's bar is still moving, so today-only figures "
          "(price, rel_today, percentiles) are compared with drift tolerance; "
          "every settled dot, date and base rate is still compared exactly.")

api = json.loads(urllib.request.urlopen(
    "http://localhost:11001/api/rotation", timeout=300).read())
bad = 0


def check_set(names, api_list, stats_key, title):
    global bad
    A = {s["ticker"]: s for s in api_list}
    print(f"\n===== {title} =====")
    print(f"{'':7}{'rel_t mine':>12}{'rel_t api':>11}{'strk':>6}{'api':>5}"
          f"{'n_hist':>8}{'api_n':>7}{'dots':>7}{'price':>10}{'raw':>10}"
          f"{'p5 mine':>9}{'p5 api':>8}")
    for t in names:
        if t not in A:
            print(f"{t:7}  MISSING FROM API")
            bad += 1
            continue
        r, a = rel[t], A[t]
        n, sd = streak(r)
        mine_dots = "".join("G" if v > 0 else "r" for v in r[-30:])
        api_dots = "".join("G" if x["s"] > 0 else "r" for x in a["dots"])
        d_ok = mine_dots == api_dots
        date_ok = [x["d"] for x in a["dots"]] == dts[t][-30:]
        rel_ok = abs(r[-1] - a["rel_today"]) < TOL_REL
        st_ok = n == a["streak"] and sd == a["streak_dir"]
        n_ok = len(r) == a["n_hist"]
        pn, pd_ = streak(r[:-1])
        broke = n == 1 and pn >= 2 and pd_ != sd
        br_ok = broke == a["broke"] and (not broke or pn == a["broke_len"])
        tail = a["dots"][-a["streak"]:]
        self_ok = all(x["s"] == a["streak_dir"] for x in tail)
        raw_last = rawc[t][last]
        px_ok = abs(a["price"] - raw_last) <= 0.01 + TOL_PXR * raw_last

        # ---- percentiles, recomputed
        p_ok = True
        mine_p5 = api_p5 = None
        for w in WINDOWS:
            rs = roll(r, w)
            av = (a["pctl"]["windows"] or {}).get(str(w))
            if len(rs) < MIN_PCT:
                p_ok &= av is None
                continue
            if av is None:
                p_ok = False
                continue
            mr = rank(rs, rs[-1])
            mg = sum(1 for v in r[-w:] if v > 0)
            p_ok &= (abs(mr - av["pct"]) < TOL_PCT
                     and abs(rs[-1] - av["value"]) < TOL_VAL
                     and mg == av["green"]
                     and len(rs) == av["n"]
                     and abs(max(rs) - av["best"]) < TOL_VAL
                     and abs(min(rs) - av["worst"]) < TOL_VAL)
            if w == 5:
                mine_p5, api_p5 = mr, av["pct"]
        # run-length percentile
        rl = [x[1] for x in runs(r) if x[0] == sd]
        ar = a["pctl"].get("run", {})
        if len(rl) >= 20:
            p_ok &= (ar.get("pct") is not None
                     and abs(rank(rl, n) - ar["pct"]) < TOL_PCT
                     and ar["longest"] == max(rl) and ar["len"] == n)
        flags = "".join([" " if x else "!" for x in
                         (rel_ok, st_ok, d_ok, date_ok, br_ok, self_ok, px_ok,
                          n_ok, p_ok)])
        bad += flags.count("!")
        print(f"{t:7}{r[-1]:+12.2f}{a['rel_today']:+11.2f}{n*sd:+6d}"
              f"{a['streak']*a['streak_dir']:+5d}{len(r):>8}{a['n_hist']:>7}"
              f"{'ok' if d_ok else 'DIFF':>7}{a['price']:10.2f}{raw_last:10.2f}"
              f"{(f'{mine_p5:.1f}' if mine_p5 is not None else '-'):>9}"
              f"{(f'{api_p5:.1f}' if api_p5 is not None else '-'):>8}  {flags}")

    cont, brk = run_stats(names)
    print(f"\n{'k':>3}{'n mine':>9}{'n api':>8}{'cont% mine':>12}{'cont% api':>11}"
          f"{'nbrk mine':>11}{'nbrk api':>10}{'fwd3 mine':>11}{'fwd3 api':>10}")
    for k in range(1, CAP + 1):
        c, tot = cont[k]
        f = brk[k]
        mine_c = c / tot * 100 if tot else 0
        mine_f = sum(f) / len(f) if f else 0
        v = api[stats_key][str(k)]
        ok = (tot == v["n"] and len(f) == v["n_break"]
              and abs(mine_c - v["continue_pct"]) < 0.2
              and abs(mine_f - v["after_break_fwd3"]) < 0.02)
        bad += 0 if ok else 1
        print(f"{k:>3}{tot:>9}{v['n']:>8}{mine_c:>12.1f}{v['continue_pct']:>11.1f}"
              f"{len(f):>11}{v['n_break']:>10}{mine_f:>+11.3f}"
              f"{v['after_break_fwd3']:>+10.3f}  {'' if ok else '!'}")
    return A


A = check_set(STOCKS, api["stocks"], "run_stats", "STOCKS")
E = check_set(ETFS, api["sector_etfs"], "etf_run_stats", "SECTOR ETFS")

print("\nflag order: rel_today, streak, dot signs, dot dates, break state, "
      "dots-vs-streak self-consistency, price-vs-raw-close, history length, percentiles")

# ---- streak board must be a strict function of the per-name state
for board, M, label in ((api["streak_board"], A, "stocks"),
                        (api["etf_streak_board"], E, "sectors")):
    for x in board["breaks"]:
        a = M[x["ticker"]]
        if not (a["broke"] and a["broke_len"] == x["len"] >= 2):
            print(f"! {label} streak_board break mismatch {x['ticker']}")
            bad += 1
    for x in board["stretched"]:
        a = M[x["ticker"]]
        if not (not a["broke"] and a["streak"] == x["len"] >= 3):
            print(f"! {label} streak_board stretched mismatch {x['ticker']}")
            bad += 1
    listed = {x["ticker"] for x in board["breaks"]} | {x["ticker"] for x in board["stretched"]}
    missed = [t for t, a in M.items() if t not in listed
              and (a["broke"] and a["broke_len"] >= 2 or
                   not a["broke"] and a["streak"] >= 3)]
    if missed:
        print(f"! {label}: qualifying names missing from the board: {missed}")
        bad += len(missed)

# ---- percentile board must be a strict slice of the per-name percentiles
pb = api["pct_board"]
byt = {**{s["ticker"]: s for s in api["stocks"]},
       **{s["ticker"]: s for s in api["sector_etfs"]}}
for c in pb["stretched"] + pb["washed"]:
    w = byt[c["ticker"]]["pctl"]["windows"]["5"]
    want_hot = w["pct"] >= 90
    in_hot = c in pb["stretched"]
    if abs(w["pct"] - c["pct"]) > 1e-9 or want_hot != in_hot:
        print(f"! pct_board mismatch {c['ticker']}")
        bad += 1
qual = [t for t, s in byt.items()
        if (s["pctl"]["windows"] or {}).get("5")
        and (s["pctl"]["windows"]["5"]["pct"] >= 90 or s["pctl"]["windows"]["5"]["pct"] <= 10)]
listed = {c["ticker"] for c in pb["stretched"] + pb["washed"]}
if set(qual) - listed:
    print(f"! pct_board missing extremes: {sorted(set(qual) - listed)}")
    bad += len(set(qual) - listed)

# ---- percentile backtest: buckets must partition the sample, edges net to zero
for uni, blocks in api["pct_backtest"].items():
    for w, hors in blocks.items():
        for h, blk in hors.items():
            n = sum(b["n"] for b in blk["buckets"])
            wavg = sum(b["n"] * b["fwd"] for b in blk["buckets"]) / n if n else 0
            if abs(wavg - blk["base"]) > 0.01:
                print(f"! {uni} w{w} h{h}: bucket-weighted mean {wavg:.3f} != base {blk['base']}")
                bad += 1
            eavg = sum(b["n"] * b["edge"] for b in blk["buckets"]) / n if n else 0
            if abs(eavg) > 0.01:
                print(f"! {uni} w{w} h{h}: edges do not net to zero ({eavg:+.3f})")
                bad += 1
print("percentile backtest: bucket partition + edge-vs-baseline identities checked "
      f"({sum(len(h) for b in api['pct_backtest'].values() for h in b.values())} blocks)")

# ---- dividend / split sanity: adjusted and raw must agree on RETURNS recently
print("\nadjusted-vs-raw return drift over the last 30 sessions (should be ~0 "
      "except across an ex-dividend date):")
for t in ALL + ["SPY"]:
    ks = [k for k in spine if k in adjc[t] and k in rawc[t]][-31:]
    if len(ks) < 5:
        print(f"  {t:7} too little history")
        continue
    mx = max(abs((adjc[t][ks[i]] / adjc[t][ks[i - 1]] - 1) * 100
                 - (rawc[t][ks[i]] / rawc[t][ks[i - 1]] - 1) * 100)
             for i in range(1, len(ks)))
    print(f"  {t:7} max daily diff {mx:.4f}%{'   <-- ex-div in window' if mx > 0.02 else ''}")

print(f"\n=== {'ALL CHECKS PASS' if bad == 0 else str(bad) + ' MISMATCH(ES)'} ===")
