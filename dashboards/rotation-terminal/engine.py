#!/usr/bin/env python3
"""
Proflex Rotation Terminal.

Answers four questions, in order:
  1. MORNING     — is a rotation showing up in the first two hours of trade?
  2. MAP         — who is being bought and sold versus the index right now?
  3. DURATION    — how long do these rotations historically run, and how far in is this one?
  4. PERSISTENCE — is the relationship (e.g. Microsoft moving against semis) holding?

Every "relative" number is stock return minus SPY return over the same window.
Raw returns hide rotation on a flat-index day; relative returns are the point.
"""

import json
import ssl
from pathlib import Path
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import numpy as np

_ctx = ssl.create_default_context()
ET = timezone(timedelta(hours=-4))          # US market clock (EDT)

TOP10 = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
         "BRK-B", "LLY",
         # requested watchlist: Mag7 is already above; these are the additions
         "AMD", "MU", "SKHY", "SPCX"]
NAMES = {"NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft",
         "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta",
         "AVGO": "Broadcom", "TSLA": "Tesla", "BRK-B": "Berkshire",
         "LLY": "Eli Lilly", "AMD": "AMD", "MU": "Micron",
         "SKHY": "SK hynix", "SPCX": "SpaceX"}

# Five groups — the cap that keeps the scatter palette valid on all pairs.
# Space is a single-name group by design: SpaceX has no peer in this universe,
# and folding it in with Tesla was hiding that it trades on its own story.
SECTOR = {"NVDA": "AI / Semis", "AVGO": "AI / Semis", "AMD": "AI / Semis",
          "MU": "Memory", "SKHY": "Memory",
          "MSFT": "Big Tech", "AAPL": "Big Tech", "GOOGL": "Big Tech",
          "AMZN": "Big Tech", "META": "Big Tech",
          "TSLA": "Big Tech", "SPCX": "Space",
          "BRK-B": "Defensive / Value", "LLY": "Defensive / Value"}
SECTOR_ORDER = ["AI / Semis", "Memory", "Big Tech",
                "Space", "Defensive / Value"]

BENCH = "SPY"
SEMIS = "SMH"                                # semis proxy for the pair work

# Sector ETFs tracked as first-class entities — a rotation is a sector event, and
# the eleven SPDR sectors plus semis/software are how that event is actually named.
SECTOR_ETFS = ["XLK", "XLC", "XLY", "XLF", "XLV", "XLI",
               "XLE", "XLP", "XLU", "XLB", "XLRE", "SMH", "IGV"]
SECTOR_ETF_NAMES = {
    "XLK": "Technology", "XLC": "Communication Services", "XLY": "Consumer Discretionary",
    "XLF": "Financials", "XLV": "Health Care", "XLI": "Industrials",
    "XLE": "Energy", "XLP": "Consumer Staples", "XLU": "Utilities",
    "XLB": "Materials", "XLRE": "Real Estate", "SMH": "Semiconductors",
    "IGV": "Software",
}
EXTRA = ["SPY", "QQQ", "IGV", "SMH"]

# ETF/index members of the quadrant framework, on top of the 10 mega-caps.
# SPY and QQQ are the two switchable benchmarks — measured against themselves
# they sit at the origin, which is the honest baseline, not a signal.
ETFS = ["QQQ", "IGV", "SPY"]
ETF_NAMES = {"QQQ": "Nasdaq-100", "IGV": "Software (IGV)", "SPY": "S&P 500"}
ETF_SECTOR = "Index / ETF"
BENCHMARKS = ["SPY", "QQQ"]

THRESH = 0.15        # % vs index — below this a day is "quiet", not a rotation
CORR_FAST, CORR_SLOW = 10, 63
TREND_WIN = 30
DOT_WIN = 30         # sessions of red/green dots kept per stock
RUN_CAP = 6          # run lengths above this are pooled into one bucket

# ---- percentile system -------------------------------------------------
# A streak answers "how long"; a percentile answers "how unusual". Five green
# dots means nothing until you know that five-in-a-row, at this strength, sits
# in the 97th percentile of everything this name has done.
PCT_WINDOWS = [1, 3, 5, 10, 21, 63]   # trading-day lookbacks that get ranked
PCT_HORIZONS = [1, 3, 5, 10]          # forward horizons scored in the backtest
MIN_DOTS = 25        # return observations needed before dots/streaks are shown
MIN_PCT = 60         # observations needed before a percentile is quoted
MIN_POOL = 252       # observations needed to join the pooled 2y backtests
PCT_BUCKETS = [(0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100)]

_cache = {"t": 0, "payload": None}
CACHE_TTL = 300


# ------------------------------------------------------------------ fetch

def _chart(ticker, interval, rng):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(ticker)}?interval={interval}&range={rng}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=_ctx) as r:
        return json.loads(r.read().decode())["chart"]["result"][0]


def _daily(ticker):
    try:
        res = _chart(ticker, "1d", "2y")
        q = res["indicators"]["quote"][0]
        raw = q["close"]
        adj = res.get("indicators", {}).get("adjclose")
        close = adj[0]["adjclose"] if adj else raw
        # adjusted for returns, raw for anchoring the intraday panel to the
        # unadjusted close a trader sees on their screen
        return ticker, [(t, c, r) for t, c, r in zip(res["timestamp"], close, raw)
                        if c is not None and r is not None]
    except Exception:  # noqa: BLE001
        return ticker, None


def _intraday(ticker):
    """60-minute bars, 60 days, grouped by US trading date."""
    try:
        res = _chart(ticker, "60m", "60d")
        q = res["indicators"]["quote"][0]
        out = defaultdict(list)
        for t, o, c in zip(res["timestamp"], q["open"], q["close"]):
            if o is None or c is None:
                continue
            d = datetime.fromtimestamp(t, ET)
            out[d.strftime("%Y-%m-%d")].append(
                {"ts": t, "hhmm": d.strftime("%H:%M"), "open": o, "close": c})
        return ticker, dict(out)
    except Exception:  # noqa: BLE001
        return ticker, None


def _session_ts(day_bars):
    """A daily bar's timestamp is the session's midnight in ET. Rebuild it from
    an intraday bar so a backfilled session lands on the same key the daily
    feed would have used."""
    d = datetime.fromtimestamp(day_bars[0]["ts"], ET)
    return int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp())


def _backfill_daily(raw, intra):
    """Yahoo's daily endpoint intermittently drops a whole session for EVERY
    symbol at once while its 60m and 15m bars for that same session are present
    and agree with each other. When that happens the session silently vanishes
    from the calendar, and the next real session's "1-day" return is measured
    across the gap — a two-session move reported as today's.

    Any session that exists in the intraday feed but is missing from the daily
    feed is rebuilt here from the last intraday bar of that session. Only
    sessions inside the intraday window can be repaired, which is exactly the
    recent range where the damage would be visible."""
    patched = defaultdict(list)
    for tk, series in raw.items():
        days = intra.get(tk)
        if not series or not days:
            continue
        have = {datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")
                for ts, _, _ in series}
        for day, bars in days.items():
            if day in have or not bars:
                continue
            close = bars[-1]["close"]
            # the adjustment factor for a session this recent is 1.0 — no
            # distribution has gone ex since — so raw doubles as adjusted
            series.append((_session_ts(bars), close, close))
            patched[day].append(tk)
        series.sort(key=lambda r: r[0])
    for day, tks in sorted(patched.items()):
        print(f"[backfill] daily bar missing for {day}; rebuilt from intraday "
              f"for {len(tks)} tickers", flush=True)
    return raw


# ------------------------------------------------------------- daily core

def _panel():
    """Align every ticker onto the benchmark's calendar.

    Deliberately NOT an intersection of dates: SPCX has 38 sessions of history and
    SKHY has 20, and intersecting would truncate the whole two-year panel down to
    those. Missing days are NaN, and every downstream statistic works off each
    ticker's own valid tail."""
    tickers = list(dict.fromkeys(TOP10 + SECTOR_ETFS + EXTRA))
    with ThreadPoolExecutor(max_workers=16) as ex:
        raw = dict(ex.map(_daily, tickers))
        # Intraday is fetched for the WHOLE daily universe, not just the names
        # the morning panel draws, so a dropped session can be repaired
        # everywhere rather than leaving the sector ETFs on a different calendar.
        intra = dict(ex.map(_intraday, tickers))
    raw = _backfill_daily(raw, intra)
    if not raw.get(BENCH) or len(raw[BENCH]) < 120:
        raise RuntimeError("SPY daily data unavailable")
    dates = [r[0] for r in raw[BENCH]]
    pos = {d: i for i, d in enumerate(dates)}
    px, px_raw = {}, {}
    for k, v in raw.items():
        if not v:
            continue
        a = np.full(len(dates), np.nan)
        b = np.full(len(dates), np.nan)
        for ts, adj, rawc in v:
            i = pos.get(ts)
            if i is not None:
                a[i], b[i] = adj, rawc
        if np.isnan(a[-1]):          # no current quote — drop rather than mislead
            continue
        px[k], px_raw[k] = a, b
    return dates, px, px_raw, intra


def _tail(a):
    """Trailing run of finite values — a ticker's usable history."""
    a = np.asarray(a, dtype=float)
    ok = np.isfinite(a)
    if not ok.any():
        return a[:0], 0
    i = len(a)
    while i > 0 and ok[i - 1]:
        i -= 1
    return a[i:], i


def _corr(a, b):
    if len(a) < 3 or a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _quad(prev, today):
    """Classify one day from (yesterday vs index, today vs index)."""
    up_y, up_t = prev > THRESH, today > THRESH
    dn_y, dn_t = prev < -THRESH, today < -THRESH
    if up_y and up_t:
        return "running"
    if dn_y and up_t:
        return "turning"
    if up_y and dn_t:
        return "fading"
    if dn_y and dn_t:
        return "avoid"
    return "quiet"


QUAD_META = {
    "running": ("RUNNING", "Beat the index yesterday and again today — money is still coming in."),
    "turning": ("TURNING UP", "Lagged the index yesterday, leading it today — this is the fresh turn."),
    "fading":  ("FADING", "Was leading yesterday, lagging today — the move is being sold."),
    "avoid":   ("AVOID", "Behind the index two days running — money is leaving."),
    "quiet":   ("QUIET", "Moving with the index, no independent story today."),
}


def _streak(rel):
    """Current run of consecutive same-direction days versus the index."""
    if len(rel) == 0:
        return 0, 0
    sign = 1 if rel[-1] > 0 else -1
    n = 0
    for v in rel[::-1]:
        if (v > 0) == (sign > 0):
            n += 1
        else:
            break
    return n, sign


# ------------------------------------------------------- dots and runs
#
# One dot per session: green when the stock beat the index that day, red when it
# lagged. A "run" is consecutive dots of the same colour — five reds in a row is
# a sustained rotation OUT of that name. The question that actually pays is when
# the run breaks, so every run is tracked, and the break is scored against two
# years of history for the same run length.

def _dots(rel, dts):
    """Last DOT_WIN sessions as coloured dots. `dts` are the dates for `rel`."""
    out = []
    for d, v in zip(dts[-DOT_WIN:], rel[-DOT_WIN:]):
        v = float(v)
        out.append({"d": d, "rel": round(v, 2),
                    "s": 1 if v > 0 else -1,
                    "quiet": abs(v) < THRESH})
    return out


def _runs(rel):
    """Segment a relative series into runs: [{sign, len, sum}] oldest first."""
    out = []
    for v in rel:
        s = 1 if float(v) > 0 else -1
        if out and out[-1]["sign"] == s:
            out[-1]["len"] += 1
            out[-1]["sum"] += float(v)
        else:
            out.append({"sign": s, "len": 1, "sum": float(v)})
    for r in out:
        r["sum"] = round(r["sum"], 2)
    return out


def _break_state(rel):
    """Today's run, and whether today snapped a run that had been going."""
    n_now, dir_now = _streak(rel)
    prev_n, prev_dir = _streak(rel[:-1]) if len(rel) > 1 else (0, 0)
    broke = n_now == 1 and prev_n >= 2 and prev_dir != dir_now
    return {"streak": n_now, "streak_dir": dir_now,
            "broke": broke,
            "broke_len": prev_n if broke else 0,
            "broke_dir": prev_dir if broke else 0}


def _run_stats(rel_by_ticker):
    """Two years, pooled across the ten names.

    continue_pct[k] — a run has reached exactly k days: how often did day k+1
                      stay the same colour?
    after_break[k]  — a run of k days just ended: average relative move over the
                      next 3 sessions, measured in the NEW direction. Positive
                      means the reversal kept paying after the flip.
    """
    cont = defaultdict(lambda: [0, 0])
    brk = defaultdict(list)
    for rel in rel_by_ticker.values():
        n = len(rel)
        sign, k = 0, 0
        for i in range(n):
            s = 1 if float(rel[i]) > 0 else -1
            if s == sign:
                k += 1
            else:
                if sign != 0 and i + 4 <= n:
                    # A run of length k ended at i-1 and day i is the flip. Score
                    # the THREE SESSIONS AFTER the flip — including day i itself
                    # would bake in a day whose sign is true by construction.
                    brk[min(k, RUN_CAP)].append(float(rel[i + 1:i + 4].sum()) * s)
                sign, k = s, 1
            if i + 1 < n:
                b = cont[min(k, RUN_CAP)]
                b[1] += 1
                if (1 if float(rel[i + 1]) > 0 else -1) == s:
                    b[0] += 1
    out = {}
    for k in range(1, RUN_CAP + 1):
        c, tot = cont[k]
        f = np.array(brk[k], dtype=float)
        out[str(k)] = {
            "k": k,
            "n": int(tot),
            "continue_pct": round(c / tot * 100, 1) if tot else 0.0,
            "reverse_pct": round((1 - c / tot) * 100, 1) if tot else 0.0,
            "n_break": int(len(f)),
            "after_break_fwd3": round(float(f.mean()), 3) if len(f) else 0.0,
            "after_break_hit": round(float((f > 0).mean() * 100), 1) if len(f) else 0.0,
        }
    return out


def _pattern_note(bs, stats):
    """Plain-English read on where a name sits in its run."""
    k = str(min(max(bs["streak"], 1), RUN_CAP))
    s = stats.get(k, {})
    if bs["broke"]:
        bk = str(min(bs["broke_len"], RUN_CAP))
        b = stats.get(bk, {})
        side = "underperformance" if bs["broke_dir"] < 0 else "outperformance"
        tail = ""
        if b.get("n_break", 0) > 30:
            tail = (f" After a {bs['broke_len']}-day run has snapped, the 3 sessions "
                    f"that followed the flip went the new way {b['after_break_hit']}% of the time "
                    f"({b['after_break_fwd3']:+.2f}% average).")
        return (f"Broke a {bs['broke_len']}-day {side} streak today.{tail}")
    word = "behind" if bs["streak_dir"] < 0 else "ahead of"
    if bs["streak"] >= 4:
        return (f"{bs['streak']} days {word} the index in a row — a sustained "
                f"rotation. Runs this long have carried on only "
                f"{s.get('continue_pct', 0)}% of the time, so the flip is close.")
    if bs["streak"] >= 2:
        return (f"{bs['streak']} days {word} the index. Historically {s.get('continue_pct', 0)}% "
                f"of runs this long added another day.")
    return f"Day one {word} the index — no streak yet."


# ------------------------------------------------------ percentile system
#
# The streak tells you HOW LONG a name has led or lagged the index. The percentile
# tells you HOW UNUSUAL that is for this particular name. "NVDA beat the index 5 of
# the last 5 sessions" is a fact; "and that 5-day relative move is in the 97th
# percentile of every 5-day stretch it has run in two years" is the part that
# carries information, because it says the move is stretched rather than ordinary.
#
# Three ranks are produced per name:
#   strength — the rolling W-day sum of relative return, ranked against history
#   breadth  — how many of the last W sessions were green, ranked against history
#   run      — the current streak length, ranked against past runs the same way
#
# All ranks are percentile-of-history in [0,100]. Ties are mid-ranked so a flat
# series cannot report 100th percentile.


def _roll_sum(a, w):
    """Every W-day sum in the series, oldest first."""
    a = np.asarray(a, dtype=float)
    if w <= 0 or len(a) < w:
        return np.array([])
    if w == 1:
        return a.copy()
    c = np.concatenate(([0.0], np.cumsum(a)))
    return c[w:] - c[:-w]


def _roll_green(a, w):
    """Count of green (>0) days in every W-day window, oldest first."""
    return _roll_sum((np.asarray(a, dtype=float) > 0).astype(float), w)


def _rank(hist, val):
    """Mid-rank percentile of `val` within `hist`, inclusive of itself."""
    h = np.asarray(hist, dtype=float)
    if len(h) == 0:
        return None
    below = float((h < val).sum())
    equal = float((h == val).sum())
    return round((below + 0.5 * equal) / len(h) * 100, 1)


def _run_lengths(rel):
    """Past run lengths split by direction, for ranking the current streak."""
    up, dn = [], []
    for r in _runs(rel):
        (up if r["sign"] > 0 else dn).append(r["len"])
    return up, dn


def _percentiles(rel):
    """Today's percentile card for one entity."""
    rel = np.asarray(rel, dtype=float)
    n = len(rel)
    out = {"n": int(n), "windows": {}, "low_n": n < MIN_PCT}
    for w in PCT_WINDOWS:
        rs = _roll_sum(rel, w)
        if len(rs) < MIN_PCT:
            continue
        gs = _roll_green(rel, w)
        val, gval = float(rs[-1]), float(gs[-1])
        out["windows"][str(w)] = {
            "w": w,
            "value": round(val, 2),
            "pct": _rank(rs, val),
            "green": int(gval),
            "green_pct": _rank(gs, gval),
            "n": int(len(rs)),
            # the extremes are what a trader actually acts on
            "best": round(float(rs.max()), 2),
            "worst": round(float(rs.min()), 2),
        }
    n_now, dir_now = _streak(rel)
    up, dn = _run_lengths(rel)
    same = up if dir_now > 0 else dn
    out["run"] = {
        "len": n_now, "dir": dir_now,
        "pct": _rank(same, n_now) if len(same) >= 20 else None,
        "n": len(same),
        "longest": max(same) if same else 0,
    }
    return out


def _ord(v):
    """1st / 2nd / 3rd / 11th — percentiles read as ranks, so spell them properly."""
    n = int(round(v))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _pct_reading(tk, name, p):
    """The one-line read the user asked for, in plain English."""
    w5 = p["windows"].get("5")
    if not w5 or w5["pct"] is None:
        return f"{name} has too little history for a percentile read yet."
    side = "beating" if w5["value"] > 0 else "lagging"
    bit = (f"{name} is {side} the index over the past 5 sessions ({w5['value']:+.2f}%), "
           f"{w5['green']}/5 days green — that 5-day run sits in the "
           f"<b>{_ord(w5['pct'])} percentile</b> of its own two-year history.")
    if w5["pct"] >= 90:
        bit += " Stretched to the upside: historically this is where the run has run out."
    elif w5["pct"] <= 10:
        bit += " Washed out: this is the zone reversals have come from."
    else:
        bit += " Middle of its own range — no extreme to trade against."
    r = p.get("run", {})
    if r.get("pct") is not None and r["len"] >= 3:
        bit += (f" The current {r['len']}-day "
                f"{'green' if r['dir'] > 0 else 'red'} run is longer than "
                f"{r['pct']:.0f}% of its past runs the same way "
                f"(longest ever: {r['longest']}).")
    return bit


def _pct_backtest(rel_by_ticker):
    """What has actually followed each percentile zone?

    For every name with two years of history, rank each day's W-day relative move
    against ONLY the history available up to that day (expanding window, minimum
    MIN_PCT observations) — an in-sample rank would use tomorrow's data to grade
    today and would flatter every extreme. Then bucket the days by that rank and
    measure the forward relative return over each horizon.

    Positive numbers in the top bucket mean momentum: extreme strength kept paying.
    Negative numbers in the top bucket mean mean reversion: the stretch got sold."""
    out = {}
    for w in PCT_WINDOWS:
        rows = {str(h): defaultdict(list) for h in PCT_HORIZONS}
        for rel in rel_by_ticker.values():
            rel = np.asarray(rel, dtype=float)
            if len(rel) < MIN_POOL:
                continue
            rs = _roll_sum(rel, w)
            if len(rs) < MIN_PCT + max(PCT_HORIZONS) + 5:
                continue
            for i in range(MIN_PCT, len(rs)):
                pr = _rank(rs[:i], rs[i])       # trailing only — no lookahead
                t = i + w - 1                   # index in `rel` of the window's last day
                for h in PCT_HORIZONS:
                    if t + h < len(rel):
                        rows[str(h)][_bucket(pr)].append(float(rel[t + 1:t + 1 + h].sum()))
        res = {}
        for h, v in rows.items():
            # Mega-caps beat the index on average over this sample, so a raw
            # forward number is positive almost everywhere. EDGE strips that
            # baseline out: it is the bucket's forward return minus the average
            # across all buckets, i.e. what the percentile itself is worth.
            allv = np.array([x for lst in v.values() for x in lst], dtype=float)
            base = float(allv.mean()) if len(allv) else 0.0
            hit0 = float((allv > 0).mean() * 100) if len(allv) else 0.0
            res[h] = {
                "base": round(base, 3),
                "base_hit": round(hit0, 1),
                "buckets": [{
                    "lo": lo, "hi": hi,
                    "n": len(v.get((lo, hi), [])),
                    "fwd": round(float(np.mean(v[(lo, hi)])), 3) if v.get((lo, hi)) else 0.0,
                    "edge": round(float(np.mean(v[(lo, hi)])) - base, 3) if v.get((lo, hi)) else 0.0,
                    "hit": round(float((np.array(v[(lo, hi)]) > 0).mean() * 100), 1)
                           if v.get((lo, hi)) else 0.0,
                } for lo, hi in PCT_BUCKETS],
            }
        out[str(w)] = res
    return out


def _bucket(pr):
    for lo, hi in PCT_BUCKETS:
        if pr < hi or hi == 100:
            return (lo, hi)
    return PCT_BUCKETS[-1]


def _pct_verdict(bt, w=5, h=5):
    """Turn the backtest into the sentence that decides how to use the chart."""
    blk = bt.get(str(w), {}).get(str(h))
    if not blk:
        return ""
    b = blk["buckets"]
    top, bot = b[-1], b[0]
    if top["n"] < 200 or bot["n"] < 200:
        return "Samples are still thin — read the percentile chart as descriptive."
    spread = bot["edge"] - top["edge"]
    tail = (f" (Everything here drifts {blk['base']:+.2f}% over {h} sessions on average; "
            "the numbers above are measured against that baseline, so only the "
            "difference between buckets counts.)")
    if spread > 0.10:
        return (f"Over two years this set is <b>mean-reverting</b> at the extremes: relative "
                f"to its own baseline the top decile of {w}-day strength ran "
                f"{top['edge']:+.2f}% over the next {h} sessions while the bottom decile ran "
                f"{bot['edge']:+.2f}% — a {spread:.2f}pt spread in favour of fading the "
                f"90th+ percentile and buying the 10th." + tail)
    if spread < -0.10:
        return (f"Over two years this set <b>trends</b>: relative to baseline the top decile of "
                f"{w}-day strength added {top['edge']:+.2f}% over the next {h} sessions and the "
                f"bottom decile ran {bot['edge']:+.2f}% — a {-spread:.2f}pt spread in favour of "
                f"staying with the extreme rather than fading it." + tail)
    return (f"No usable edge at the extremes: top decile {top['edge']:+.2f}% vs bottom "
            f"{bot['edge']:+.2f}% against baseline over {h} sessions. Use percentiles to "
            "size conviction, not to trigger the trade." + tail)


# --------------------------------------------------- duration backtest

def _duration_stats(rel_by_ticker):
    """For each quadrant: after a day is classified, how long does the move keep
    going, and how much relative return follows?

    Duration = consecutive following sessions the stock stays on the same side of
    the index. Samples overlap (a long run contributes several starts), so we
    report n and treat it as descriptive, not a p-value."""
    buckets = defaultdict(lambda: {"dur": [], "fwd1": [], "fwd3": [], "fwd5": []})
    for rel in rel_by_ticker.values():
        n = len(rel)
        for t in range(1, n - 5):
            q = _quad(rel[t - 1], rel[t])
            if q == "quiet":
                continue
            want_up = q in ("running", "turning")
            run = 0
            for k in range(t + 1, n):
                if (rel[k] > 0) == want_up:
                    run += 1
                else:
                    break
            b = buckets[q]
            b["dur"].append(run)
            sgn = 1 if want_up else -1
            b["fwd1"].append(sgn * float(rel[t + 1]))
            b["fwd3"].append(sgn * float(rel[t + 1:t + 4].sum()))
            b["fwd5"].append(sgn * float(rel[t + 1:t + 6].sum()))
    out = {}
    for q, b in buckets.items():
        d = np.array(b["dur"], dtype=float)
        f5 = np.array(b["fwd5"], dtype=float)
        f3 = np.array(b["fwd3"], dtype=float)
        f1 = np.array(b["fwd1"], dtype=float)
        out[q] = {
            "label": QUAD_META[q][0],
            "n": int(len(d)),
            "median_days": float(np.median(d)),
            "mean_days": round(float(d.mean()), 1),
            "p75_days": float(np.percentile(d, 75)),
            "share_2plus": round(float((d >= 2).mean() * 100), 1),
            "fwd1": round(float(f1.mean()), 3),
            "fwd3": round(float(f3.mean()), 3),
            "fwd5": round(float(f5.mean()), 3),
            "hit5": round(float((f5 > 0).mean() * 100), 1),
        }
    return out


# ------------------------------------------------- intraday / morning

def _prev_closes(intra, ticker, sessions):
    """Previous session's closing level for each session, taken from the last
    hourly bar of the prior session."""
    d = intra.get(ticker) or {}
    out = {}
    for i in range(1, len(sessions)):
        p = sessions[i - 1]
        if p in d and d[p]:
            out[sessions[i]] = d[p][-1]["close"]
    return out


def _morning(intra, true_prev_close=None):
    """Today's intraday path vs SPY, plus a 60-day backtest of whether the
    first two hours predict the rest of the session.

    Everything is measured from the PREVIOUS CLOSE, not today's open. A stock
    that gaps down and recovers has not outperformed — measuring from the open
    would hide the gap and overstate the move, and would leave this panel
    disagreeing with the daily numbers everywhere else in the terminal."""
    if BENCH not in intra or intra[BENCH] is None:
        return None
    spy_days = intra[BENCH]
    sessions = sorted(spy_days)
    if not sessions:
        return None
    today = sessions[-1]

    # ---- today's path, cumulative % vs SPY measured from the previous close
    spy_bars = spy_days[today]
    tpc = true_prev_close or {}
    spy_prev = _prev_closes(intra, BENCH, sessions)
    # Prefer the official prior daily close so this panel ties out exactly to the
    # daily table; the hourly-derived close is only a fallback.
    spy_base = tpc.get(BENCH) or spy_prev.get(today, spy_bars[0]["open"])
    paths, marks = [], []
    for t in TOP10:
        d = intra.get(t)
        if not d or today not in d:
            continue
        bars = d[today]
        base = tpc.get(t) or _prev_closes(intra, t, sessions).get(today, bars[0]["open"])
        pts = []
        for b in bars:
            # Match within 30 minutes rather than exactly: the final in-progress
            # bar is stamped at the moment of the last trade, so it differs by a
            # few minutes between tickers and an exact match would drop it.
            sb = min(spy_bars, key=lambda x: abs(x["ts"] - b["ts"]))
            if abs(sb["ts"] - b["ts"]) > 1800:
                continue
            rel = ((b["close"] / base) - (sb["close"] / spy_base)) * 100
            raw = (b["close"] / base - 1) * 100   # stock's own move vs prev close
            pts.append({"hhmm": b["hhmm"], "ts": b["ts"],
                        "rel": round(rel, 3), "raw": round(raw, 3)})
        if pts:
            paths.append({"ticker": t, "name": NAMES[t], "sector": SECTOR[t],
                          "pts": pts, "last": pts[-1]["rel"],
                          "raw_last": pts[-1]["raw"]})
    paths.sort(key=lambda p: -p["last"])
    bars_done = len(spy_bars)

    # ---- backtest: first two hours vs rest of day (relative to SPY)
    hits = tot = 0
    coll_m, coll_r = [], []
    per_stock = {}
    spy_pc = _prev_closes(intra, BENCH, sessions)
    for t in TOP10:
        d = intra.get(t)
        if not d:
            continue
        pc = _prev_closes(intra, t, sessions)
        sh = st = 0
        for day in sessions[:-1]:            # exclude today (incomplete)
            if day not in d or day not in spy_days:
                continue
            if day not in pc or day not in spy_pc:
                continue                     # no prior session to anchor to
            b, sb = d[day], spy_days[day]
            if len(b) < 4 or len(sb) < 4:
                continue
            # morning leg = previous close → 11:30, so the overnight gap counts
            m_ret = b[1]["close"] / pc[day] - 1
            r_ret = b[-1]["close"] / b[1]["close"] - 1
            sm = sb[1]["close"] / spy_pc[day] - 1
            sr = sb[-1]["close"] / sb[1]["close"] - 1
            mrel, rrel = (m_ret - sm) * 100, (r_ret - sr) * 100
            if abs(mrel) < 0.1:              # no morning signal to test
                continue
            coll_m.append(mrel)
            coll_r.append(rrel)
            st += 1
            if (mrel > 0) == (rrel > 0):
                sh += 1
        if st >= 10:
            per_stock[t] = {"n": st, "hit": round(sh / st * 100, 1)}
        hits += sh
        tot += st

    hit = round(hits / tot * 100, 1) if tot else 0.0
    cm, cr = np.array(coll_m), np.array(coll_r)
    return {
        "session": today,
        "bars_done": bars_done,
        "paths": paths,
        "backtest": {
            "n": tot,
            "hit": hit,
            "corr": round(_corr(cm, cr), 3) if tot > 20 else 0.0,
            "sessions": len(sessions) - 1,
            "per_stock": per_stock,
            "verdict": _morning_verdict(hit, tot),
        },
    }


def _morning_verdict(hit, n):
    if n < 100:
        return "Not enough sessions yet to judge — treat the morning read as descriptive."
    if hit >= 55:
        return (f"The first two hours have pointed the right way {hit}% of the time. "
                "A strong morning lead has tended to hold into the close.")
    if hit <= 45:
        return (f"The first two hours have pointed the right way only {hit}% of the time — "
                "morning moves have tended to REVERSE into the close. Fade them, don't chase.")
    return (f"At {hit}% the first two hours are close to a coin flip on direction. "
            "Use the morning read to see WHAT is rotating, not to time the entry.")


# ------------------------------------------------- quadrant components

def _components(rets, px_raw):
    """Every member of the quadrant framework — the 10 mega-caps plus the
    SPY / QQQ / IGV ETFs — scored against BOTH switchable benchmarks over three
    windows (1 day, 5 days, 30 days). The frontend picks the benchmark, window
    and quadrant to slice this; nothing here is benchmark-specific so switching
    never needs a refetch."""
    out = []
    for t in TOP10 + ETFS:
        if t not in rets:
            continue
        vs = {}
        for b in BENCHMARKS:
            if b not in rets:
                continue
            # tail-only: a short-history name has NaN before it listed
            r, _ = _tail((rets[t] - rets[b]) * 100)   # relative return, percent
            if len(r) < 2:
                continue
            yday, d1 = float(r[-2]), float(r[-1])
            q = _quad(yday, d1)
            sn, sd = _streak(r)
            vs[b] = {
                "d1": round(d1, 2), "yday": round(yday, 2),
                "d5": round(float(r[-5:].sum()), 2),
                "d30": round(float(r[-30:].sum()), 2),
                "quad": q, "call": QUAD_META[q][0],
                "streak": sn, "streak_dir": sd,
            }
        if not vs:
            continue
        out.append({
            "ticker": t,
            "name": NAMES.get(t, ETF_NAMES.get(t, t)),
            "sector": SECTOR.get(t, ETF_SECTOR),
            "is_etf": t in ETFS,
            # Levels are the UNADJUSTED close — the number on a trader's screen.
            # Returns stay adjusted, so a dividend never shows up as a fake move.
            "price": round(float(px_raw[t][-1]), 2),
            "today": round(float(rets[t][-1]) * 100, 2),
            "vs": vs,
        })
    return out


# ------------------------------------------------- hourly pattern engine
#
# Step 1 shows ONE session hour by hour. This section asks the next question:
# across every session we have, does the same thing happen at the same hour, and
# which names move together when it does?
#
# Unit of observation = one (session, ticker, hour) cell holding the RELATIVE
# return of that hour: the stock's move over the hour minus SPY's move over the
# same hour, in percent. The first bar of the day is measured from the previous
# session's close, so the overnight gap is counted there — same convention as
# Step 1, so the two panels never disagree.

HOUR_SLOTS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
HOUR_LABELS = {
    "09:30": "Open → 10:30",   # includes the overnight gap
    "10:30": "10:30 → 11:30",
    "11:30": "11:30 → 12:30",
    "12:30": "12:30 → 13:30",
    "13:30": "13:30 → 14:30",
    "14:30": "14:30 → 15:30",
    "15:30": "15:30 → close",
}
STORE_PATH = Path(__file__).parent / "hourly_store.json"
SPY_KEY = "__SPY__"     # reserved: the benchmark's own hourly return, in %
MIN_SESSION_BARS = 6      # a session with fewer bars than this is incomplete
MIN_CELL_N = 15           # sessions needed before an hour cell is scored at all
MIN_PAIR_N = 60           # hour observations needed before a pair is scored


def _slot(hhmm):
    """Snap a bar timestamp to its hour slot. Yahoo stamps the final in-progress
    bar at the moment of the last trade, so exact string matching drops it."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return None
    mins = h * 60 + m
    best, bd = None, 10 ** 9
    for s in HOUR_SLOTS:
        sh, sm = (int(x) for x in s.split(":"))
        d = abs(mins - (sh * 60 + sm))
        if d < bd:
            best, bd = s, d
    return best if bd <= 45 else None


def _hourly_rows(intra, true_prev_close=None):
    """Turn the raw 60-day hourly fetch into {date: {ticker: {slot: rel_pct}}}.

    Only sessions where SPY itself has a full set of bars are emitted — without
    the index leg there is no relative number to compute."""
    if BENCH not in intra or not intra.get(BENCH):
        return {}
    tpc = true_prev_close or {}
    spy_days = intra[BENCH]
    sessions = sorted(spy_days)
    spy_pc = _prev_closes(intra, BENCH, sessions)

    out = {}
    for i, day in enumerate(sessions):
        sb = spy_days.get(day) or []
        if len(sb) < MIN_SESSION_BARS:
            continue
        # base for SPY's first bar: previous session's close
        s_base = spy_pc.get(day)
        if s_base is None:
            continue
        # SPY's per-slot simple return
        s_ret, prev = {}, s_base
        for b in sb:
            sl = _slot(b["hhmm"])
            if sl is None or sl in s_ret:
                continue
            s_ret[sl] = b["close"] / prev - 1
            prev = b["close"]

        # SPY's own hourly return is stored under a reserved key. Cells hold
        # INDEX-RELATIVE returns, but the correlation leg of the rotation panel
        # needs RAW ones (relative returns have the common factor removed, which
        # pins their average pairwise correlation near -1/(n-1) by construction).
        # Storing this one series makes raw = rel + spy exactly recoverable
        # without a second fetch or a second store.
        row = {SPY_KEY: {sl: round(v * 100, 4) for sl, v in s_ret.items()}}
        for t in TOP10:
            d = intra.get(t)
            if not d or day not in d:
                continue
            bars = d[day]
            if len(bars) < MIN_SESSION_BARS:
                continue
            base = _prev_closes(intra, t, sessions).get(day)
            if base is None:
                continue
            cells, p = {}, base
            for b in bars:
                sl = _slot(b["hhmm"])
                if sl is None or sl in cells or sl not in s_ret:
                    continue
                cells[sl] = round(((b["close"] / p - 1) - s_ret[sl]) * 100, 4)
                p = b["close"]
            if cells:
                row[t] = cells
        if row:
            out[day] = row
    return out


def _load_store():
    try:
        with open(STORE_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


_harvest = {"at": None, "sessions": 0, "wrote": 0}


def harvest_state():
    return dict(_harvest)


def _merge_store(fresh):
    """Persist and grow. Fresh sessions overwrite stored ones of the same date —
    a session captured live is partial, and the next fetch supplies the complete
    version, so last-write-wins is what keeps the store honest. Sessions that
    have aged out of Yahoo's 60-day window are simply kept."""
    store = _load_store()
    store.update(fresh)
    tmp = STORE_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(store, f, separators=(",", ":"))
        tmp.replace(STORE_PATH)
        _harvest.update(at=time.time(), sessions=len(store), wrote=len(fresh))
    except Exception:  # noqa: BLE001
        pass
    return store


def _binom_p(k, n, p=0.5):
    """Two-sided exact binomial p-value. Used to ask whether an hour cell that
    looks one-directional could just be coin flips — with 14 names x 7 hours,
    a handful of 65% cells is the EXPECTED result of noise, not a finding."""
    if n <= 0:
        return 1.0
    from math import comb
    obs = comb(n, k) * p ** k * (1 - p) ** (n - k)
    tot = 0.0
    for i in range(n + 1):
        pr = comb(n, i) * p ** i * (1 - p) ** (n - i)
        if pr <= obs * (1 + 1e-9):
            tot += pr
    return min(1.0, tot)


def _hourly_patterns(store):
    """The three readings of the same store:
       1. hour-of-day grid  — did this name do the same thing at this hour?
       2. day shape         — the average session path, per name.
       3. pairs             — which names moved together, hour by hour."""
    sessions = sorted(store)
    if len(sessions) < 5:
        return None
    # Only complete sessions are scored. A session is complete when SPY-relative
    # cells exist for the closing hour on at least one name.
    complete = [d for d in sessions
                if any(len(c) >= MIN_SESSION_BARS and "15:30" in c
                       for c in store[d].values())]
    if not complete:
        complete = sessions[:-1]
    used = complete

    tickers = [t for t in TOP10
               if sum(1 for d in used if t in store[d]) >= MIN_CELL_N]

    lbl = lambda t: NAMES[t]
    sec = lambda t: SECTOR[t]
    grid, tested, alpha = _grid_for(store, tickers, used, lbl, sec, MIN_CELL_N)
    shapes = _shapes_for(grid)
    pairs = _pairs_for(store, tickers, used, sec, MIN_PAIR_N)
    counter = _counter(store, tickers, used, lbl, sec, MIN_PEERS, "stock")
    regime = _rotation_regime(store, tickers, used, lbl, sec, "stock")

    # ---- the same three readings, one level up: groups, not single names
    gstore = _group_store(store)
    gused = [d for d in used if d in gstore]
    groups = [g for g in SECTOR_ORDER
              if sum(1 for d in gused if g in gstore[d]) >= MIN_CELL_N]
    g_grid, g_tested, g_alpha = _grid_for(gstore, groups, gused,
                                          lambda g: g, lambda g: g, MIN_CELL_N)
    g_shapes = _shapes_for(g_grid)
    g_pairs = _pairs_for(gstore, groups, gused, lambda g: g, MIN_PAIR_N)
    # Groups are few, so the peer bar for breadth has to come down with them.
    g_counter = _counter(gstore, groups, gused, lambda g: g, lambda g: g,
                         max(2, len(groups) - 2), "group")
    g_regime = _rotation_regime(gstore, groups, gused, lambda g: g, lambda g: g, "group")

    n_sig = sum(1 for g in grid for c in g["cells"] if c["sig"])
    g_n_sig = sum(1 for g in g_grid for c in g["cells"] if c["sig"])
    return {
        "slots": HOUR_SLOTS,
        "slot_labels": HOUR_LABELS,
        "sessions": len(used),
        "first_session": used[0] if used else None,
        "last_session": used[-1] if used else None,
        "stored_sessions": len(sessions),
        "harvest": harvest_state(),
        "tested": tested,
        "alpha": round(alpha, 6),
        "n_sig": n_sig,
        "tickers": tickers,
        "names": {t: NAMES[t] for t in tickers},
        "sectors": {t: SECTOR[t] for t in tickers},
        "grid": grid,
        "shapes": shapes,
        "pairs": pairs,
        # Sign-filtered, not just sliced: with a small universe the top and bottom
        # slices overlap, which would list a negatively-correlated pair under
        # "moved together" and print the wrong explanation next to it.
        "pairs_together": [p for p in pairs if p["corr"] > 0][:10],
        "pairs_opposite": [p for p in sorted(pairs, key=lambda p: p["corr"])
                           if p["corr"] < 0][:10],
        "counter": counter,
        "regime": regime,
        "groups": {
            "keys": groups,
            "grid": g_grid,
            "shapes": g_shapes,
            "pairs": g_pairs,
            "pairs_together": [p for p in g_pairs if p["corr"] > 0][:6],
            "pairs_opposite": [p for p in sorted(g_pairs, key=lambda p: p["corr"])
                               if p["corr"] < 0][:6],
            "counter": g_counter,
            "regime": g_regime,            "members": {g: [{"ticker": t, "name": NAMES[t]}
                            for t in tickers if SECTOR[t] == g] for g in groups},
            "tested": g_tested,
            "alpha": round(g_alpha, 6),
            "n_sig": g_n_sig,
            "verdict": _pattern_verdict(g_n_sig, g_tested, len(gused)),
        },
        "verdict": _pattern_verdict(n_sig, tested, len(used)),
    }


# ---- group series -------------------------------------------------------
# A rotation is a group event before it is a stock event. Same cell definition
# as the stock grid, but the value is the equal-weight mean of whichever members
# traded that hour — so Memory keeps working on days SK hynix has no bar.

MIN_GROUP_MEMBERS = 1      # a group hour needs at least this many members present
BROAD_CUT = 0.75           # share of OTHER names on one side to call an hour broad
MIN_PEERS = 8              # other names needed before an hour can be classified
MIN_BROAD_N = 20           # broad hours needed before a name is scored


def _group_store(store):
    """{date: {group: {slot: rel_pct}}} — members averaged, equal weight."""
    out = {}
    for day, row in store.items():
        acc = defaultdict(lambda: defaultdict(list))
        for t, cells in row.items():
            g = None if t == SPY_KEY else SECTOR.get(t)
            if not g:
                continue
            for sl, v in cells.items():
                acc[g][sl].append(v)
        # carry the benchmark leg through unchanged — the group panel needs the
        # same raw-return reconstruction the stock panel does
        day_row = {}
        if SPY_KEY in row:
            day_row[SPY_KEY] = dict(row[SPY_KEY])
        for g, slots in acc.items():
            cells = {sl: round(float(np.mean(vs)), 4)
                     for sl, vs in slots.items() if len(vs) >= MIN_GROUP_MEMBERS}
            if cells:
                day_row[g] = cells
        if len(day_row) > (1 if SPY_KEY in day_row else 0):
            out[day] = day_row
    return out


def _grid_for(store, keys, used, label_of, sector_of, min_n):
    """Shared hour-of-day grid builder — identical maths for stocks and groups,
    so the two panels can never drift apart."""
    grid, tested = [], 0
    for k in keys:
        cells = []
        for sl in HOUR_SLOTS:
            vals = [store[d][k][sl] for d in used
                    if k in store[d] and sl in store[d][k]]
            n = len(vals)
            if n < min_n:
                cells.append({"slot": sl, "n": n, "mean": None, "median": None,
                              "hit": None, "p": None, "sig": False})
                continue
            a = np.array(vals, dtype=float)
            up = int((a > 0).sum())
            nz = int((a != 0).sum())
            p = _binom_p(up, nz) if nz else 1.0
            tested += 1
            cells.append({"slot": sl, "n": n,
                          "mean": round(float(a.mean()), 3),
                          "median": round(float(np.median(a)), 3),
                          "hit": round(up / nz * 100, 1) if nz else None,
                          "p": round(p, 4), "sig": False})
        grid.append({"ticker": k, "name": label_of(k), "sector": sector_of(k),
                     "cells": cells})
    alpha = 0.05 / tested if tested else 1.0
    for g in grid:
        for c in g["cells"]:
            if c["p"] is not None and c["p"] < alpha:
                c["sig"] = True
    return grid, tested, alpha


def _pairs_for(store, keys, used, sector_of, min_n):
    series = {k: {(d, sl): store[d][k][sl] for d in used
                  for sl in HOUR_SLOTS
                  if k in store[d] and sl in store[d][k]} for k in keys}
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ks = sorted(series[a].keys() & series[b].keys())
            if len(ks) < min_n:
                continue
            x = np.array([series[a][k] for k in ks], dtype=float)
            y = np.array([series[b][k] for k in ks], dtype=float)
            r = _corr(x, y)
            if not np.isfinite(r):
                continue
            pairs.append({"a": a, "b": b, "n": len(ks),
                          "corr": round(float(r), 3),
                          "same_dir": round(float(((x > 0) == (y > 0)).mean() * 100), 1),
                          "same_sector": sector_of(a) == sector_of(b),
                          "sector_a": sector_of(a), "sector_b": sector_of(b)})
    pairs.sort(key=lambda p: -p["corr"])
    return pairs


def _shapes_for(grid):
    shapes = []
    for g in grid:
        run, pts = 0.0, []
        for c in g["cells"]:
            if c["mean"] is None:
                continue
            run += c["mean"]
            pts.append({"slot": c["slot"], "cum": round(run, 3),
                        "step": c["mean"], "n": c["n"]})
        if pts:
            shapes.append({"ticker": g["ticker"], "name": g["name"],
                           "sector": g["sector"], "pts": pts,
                           "last": pts[-1]["cum"]})
    shapes.sort(key=lambda s: -s["last"])
    return shapes


# ---- counter-rotation ---------------------------------------------------
# "When everything else is down, who is up?"
#
# Breadth for a given name is measured across the OTHER names only. Including
# the name in its own breadth count would guarantee it looks contrarian on the
# hours it alone was green — the bias would BE the finding.

def _counter(store, keys, used, label_of, sector_of, min_peers, tag):
    """For each name: what does it do when the REST of the board moves?

    Conditioning is on the peer median — the median relative move of every other
    tracked name in that same hour — for two reasons. The peer set excludes the
    name being scored, so a name cannot make itself look contrarian on the hours
    it alone was green; and a continuous median lets every hour be used, where a
    hard "75% of others are red" cut leaves so few hours per name that nothing is
    measurable. The hard cut is still reported alongside, as the literal reading
    of the question, wherever the sample supports it."""
    obs = defaultdict(dict)          # (day,slot) -> {key: rel}
    for d in used:
        for k, cells in store[d].items():
            if k not in keys:
                continue
            for sl, v in cells.items():
                obs[(d, sl)][k] = v

    rows = []
    for k in keys:
        xs, pm, extreme_dn, extreme_up = [], [], [], []
        for hour, vals in obs.items():
            if k not in vals:
                continue
            others = [v for kk, v in vals.items() if kk != k]
            if len(others) < min_peers:
                continue
            med = float(np.median(others))
            xs.append(vals[k])
            pm.append(med)
            neg = sum(1 for v in others if v < 0) / len(others)
            if neg >= BROAD_CUT:
                extreme_dn.append(vals[k])
            elif neg <= 1 - BROAD_CUT:
                extreme_up.append(vals[k])

        if len(xs) < MIN_PAIR_N:
            continue
        a, m = np.array(xs, dtype=float), np.array(pm, dtype=float)
        var = float(m.var())
        beta = round(float(((a - a.mean()) * (m - m.mean())).mean() / var), 3) if var > 0 else None
        opp = round(float(((a > 0) != (m > 0)).mean() * 100), 1)

        # Quintile split on the peer median: the weakest and strongest fifth of
        # hours for everyone else. ~20% of the sample each, so every name is
        # scored on the same footing.
        lo, hi = float(np.quantile(m, 0.2)), float(np.quantile(m, 0.8))
        weak = a[m <= lo]
        strong = a[m >= hi]

        def block(v, want_up):
            n = len(v)
            if n < MIN_BROAD_N:
                return {"n": n, "mean": None, "rate": None, "p": None, "sig": False}
            hits = int((v > 0).sum()) if want_up else int((v < 0).sum())
            nz = int((v != 0).sum())
            return {"n": n, "mean": round(float(v.mean()), 3),
                    "rate": round(hits / nz * 100, 1) if nz else None,
                    "p": round(_binom_p(hits, nz), 4) if nz else None,
                    "sig": False}

        w_blk, s_blk = block(weak, True), block(strong, False)
        e_dn = block(np.array(extreme_dn, dtype=float), True) if extreme_dn else \
            {"n": 0, "mean": None, "rate": None, "p": None, "sig": False}
        e_up = block(np.array(extreme_up, dtype=float), False) if extreme_up else \
            {"n": 0, "mean": None, "rate": None, "p": None, "sig": False}
        score = None
        if w_blk["rate"] is not None and s_blk["rate"] is not None:
            score = round((w_blk["rate"] + s_blk["rate"]) / 2, 1)
        rows.append({"ticker": k, "name": label_of(k), "sector": sector_of(k),
                     "n": len(xs), "beta": beta, "opp_rate": opp,
                     "weak": w_blk, "strong": s_blk,
                     "ext_down": e_dn, "ext_up": e_up, "score": score})

    tested = sum(1 for r in rows for b in (r["weak"], r["strong"])
                 if b["p"] is not None)
    alpha = 0.05 / tested if tested else 1.0
    for r in rows:
        for b in (r["weak"], r["strong"], r["ext_down"], r["ext_up"]):
            if b["p"] is not None and b["p"] < alpha:
                b["sig"] = True

    scored = [r for r in rows if r["score"] is not None]
    scored.sort(key=lambda r: -r["score"])
    n_dn = sum(1 for v in obs.values() if len(v) >= min_peers + 1
               and sum(1 for x in v.values() if x < 0) / len(v) >= BROAD_CUT)
    n_up = sum(1 for v in obs.values() if len(v) >= min_peers + 1
               and sum(1 for x in v.values() if x < 0) / len(v) <= 1 - BROAD_CUT)
    return {
        "tag": tag,
        "rows": scored,
        "broad_cut": int(BROAD_CUT * 100),
        "hours_total": len(obs),
        "hours_down": n_dn,
        "hours_up": n_up,
        "tested": tested,
        "alpha": round(alpha, 6),
        "n_sig": sum(1 for r in rows for b in (r["weak"], r["strong"]) if b["sig"]),
        "top_defensive": scored[0] if scored else None,
        "top_follower": scored[-1] if scored else None,
        "verdict": _counter_verdict(scored, tag),
    }


def _counter_verdict(rows, tag):
    noun = "stock" if tag == "stock" else "group"
    if not rows:
        return f"Not enough overlapping hours yet to score any {noun}."
    top, bot = rows[0], rows[-1]
    real = [r for r in rows if r["weak"]["sig"] or r["strong"]["sig"]]
    head = (f"{top['name']} is the {noun} that most often goes the other way: in the "
            f"weakest fifth of hours for everyone else it was up {top['weak']['rate']}% "
            f"of the time (avg {top['weak']['mean']:+.2f}% vs the index, {top['weak']['n']} hours), "
            f"and in the strongest fifth it was down {top['strong']['rate']}% "
            f"({top['strong']['n']} hours). ")
    tail = (f"{bot['name']} is the opposite — it moves with the pack, so it adds "
            f"the least diversification of anything here.")
    if not real:
        return head + tail + " No row clears the noise bar, so read this as description, not edge."
    return head + tail + f" {len(real)} of {len(rows)} rows clear the noise bar."

# ---- rotation regime ----------------------------------------------------
# The two measures the industry actually uses to say whether a rotation is
# happening, applied to the universe already on this terminal — no new tickers,
# no extra history required:
#
#   DISPERSION  — cross-sectional standard deviation of the session's relative
#                 returns (the S&P DSPX / CBOE dispersion convention). It is a
#                 statistic ACROSS NAMES within one session, so its sample size
#                 is the 14 names, not the 58 sessions. Wide = the board split.
#
#   AVG PAIRWISE CORRELATION — mean of every pairwise correlation (the CBOE COR
#                 convention), measured on the hourly observations inside a
#                 trailing window. Low = names going their own way.
#
# Rotation is high dispersion WITH low correlation. Both are direct measurements
# of what happened, not estimates of what tends to happen, so nothing on this
# panel carries a hit rate, a p-value or a significance mark.

CORR_WIN = 10        # trailing sessions (~70 hourly obs) for the correlation leg
MIN_NAMES = 8        # names needed in a session before it is measured


def _rotation_regime(store, keys, used, label_of, sector_of, tag):
    # --- session-level relative return per name = sum of its hourly cells
    daily = {}
    for d in used:
        row = {k: round(sum(store[d][k].values()), 4)
               for k in keys if k in store[d] and store[d][k]}
        if len(row) >= MIN_NAMES or (tag == "group" and len(row) >= 3):
            daily[d] = row
    sess = sorted(daily)
    if len(sess) < CORR_WIN + 2:
        return None

    # tercile size — the standard top/bottom cut, scaled to the universe size
    kcut = max(1, round(len(keys) / 3))

    series = []
    for i, d in enumerate(sess):
        row = daily[d]
        vals = np.array(list(row.values()), dtype=float)
        order = sorted(row.items(), key=lambda kv: -kv[1])
        top, bot = order[:kcut], order[-kcut:]
        spread = float(np.mean([v for _, v in top]) - np.mean([v for _, v in bot]))

        # trailing average pairwise correlation on the hourly observations
        corr = None
        if i >= CORR_WIN:
            win = sess[i - CORR_WIN + 1:i + 1]
            # RAW returns, per the CBOE COR convention: reconstruct each name's
            # own return by adding the benchmark's back onto the relative one.
            # Sessions stored before the benchmark leg existed are skipped
            # rather than silently mixed in.
            hs = {}
            for k in keys:
                hs[k] = {}
                for dd in win:
                    sp = store[dd].get(SPY_KEY)
                    if not sp or k not in store[dd]:
                        continue
                    for sl in HOUR_SLOTS:
                        if sl in store[dd][k] and sl in sp:
                            hs[k][(dd, sl)] = store[dd][k][sl] + sp[sl]
            rs = []
            ks = [k for k in keys if len(hs[k]) >= 20]
            for a_i, a in enumerate(ks):
                for b in ks[a_i + 1:]:
                    common = sorted(hs[a].keys() & hs[b].keys())
                    if len(common) < 20:
                        continue
                    r = _corr(np.array([hs[a][c] for c in common], dtype=float),
                              np.array([hs[b][c] for c in common], dtype=float))
                    if np.isfinite(r):
                        rs.append(float(r))
            corr = round(float(np.mean(rs)), 3) if rs else None

        series.append({
            "date": d,
            "n": len(row),
            "disp": round(float(vals.std(ddof=1)), 3),
            "spread": round(spread, 3),
            "breadth": int((vals > 0).sum()),
            "corr": corr,
            "leader": order[0][0], "leader_rel": round(order[0][1], 2),
            "laggard": order[-1][0], "laggard_rel": round(order[-1][1], 2),
        })

    # --- regime: each session against the medians of this same window
    scored = [s for s in series if s["corr"] is not None]
    if not scored:
        return None
    dmed = float(np.median([s["disp"] for s in scored]))
    cmed = float(np.median([s["corr"] for s in scored]))
    for s in series:
        if s["corr"] is None:
            s["regime"] = None
            continue
        hi_d, hi_c = s["disp"] >= dmed, s["corr"] >= cmed
        s["regime"] = ("rotation" if hi_d and not hi_c else
                       "split_trend" if hi_d and hi_c else
                       "macro" if hi_c else "quiet")
    counts = defaultdict(int)
    for s in scored:
        counts[s["regime"]] += 1

    latest = series[-1]
    # Both percentiles are ranked against the SAME set of measured sessions the
    # regime split uses, so the panel never quotes two different denominators.
    disps = sorted(s["disp"] for s in scored)
    d_pct = round(sum(1 for v in disps if v <= latest["disp"]) / len(disps) * 100)
    corrs = sorted(s["corr"] for s in scored)
    c_pct = (round(sum(1 for v in corrs if v <= latest["corr"]) / len(corrs) * 100)
             if latest["corr"] is not None else None)

    # the single cleanest number on the panel: how far apart the top and bottom
    # third finished on an average session
    avg_spread = round(float(np.mean([s["spread"] for s in series])), 2)
    rot_spread = round(float(np.mean([s["spread"] for s in scored
                                      if s["regime"] == "rotation"])), 2) \
        if counts["rotation"] else None
    macro_spread = round(float(np.mean([s["spread"] for s in scored
                                        if s["regime"] == "macro"])), 2) \
        if counts["macro"] else None

    return {
        "tag": tag,
        "series": series,
        "scored": len(scored),
        "kcut": kcut,
        "corr_win": CORR_WIN,
        "disp_median": round(dmed, 3),
        "corr_median": round(cmed, 3),
        "counts": dict(counts),
        "share": {k: round(v / len(scored) * 100, 1) for k, v in counts.items()},
        "latest": latest,
        "disp_pct": d_pct,
        "corr_pct": c_pct,
        "avg_spread": avg_spread,
        "rotation_spread": rot_spread,
        "macro_spread": macro_spread,
        "summary": _regime_summary(counts, len(scored), avg_spread,
                                   rot_spread, macro_spread, kcut, tag),
    }


REGIME_LABEL = {"rotation": "Rotation", "split_trend": "Split trend",
                "macro": "Macro day", "quiet": "Quiet"}
REGIME_NOTE = {
    "rotation": "Wide gaps between names, low correlation — money moving between them.",
    "split_trend": "Wide gaps, but still correlated — the whole board moving, unevenly.",
    "macro": "Narrow gaps, high correlation — one factor moving everything at once.",
    "quiet": "Narrow gaps, low correlation — no direction and no rotation.",
}


def _regime_summary(counts, n, avg_spread, rot_spread, macro_spread, kcut, tag):
    noun = "stocks" if tag == "stock" else "groups"
    rot = counts.get("rotation", 0)
    parts = [f"Across {n} measured sessions the top {kcut} {noun} finished an average "
             f"of {avg_spread:.2f}% ahead of the bottom {kcut}, measured against the index."]
    if rot:
        parts.append(f"{rot} of those sessions ({rot / n * 100:.0f}%) were rotation days — "
                     f"dispersion above its median with correlation below its median.")
    if rot_spread is not None and macro_spread is not None:
        parts.append(f"On rotation days the top-to-bottom gap averaged {rot_spread:.2f}%, "
                     f"against {macro_spread:.2f}% on macro days — "
                     f"{rot_spread / macro_spread:.1f}x as wide.")
    return " ".join(parts)

def _pattern_verdict(n_sig, tested, sessions):
    if sessions < 20:
        return (f"Only {sessions} complete sessions stored so far. Read the grid as a "
                "description of what has happened, not as a pattern — it needs "
                "roughly a quarter of data before a cell can clear the noise bar.")
    if n_sig == 0:
        return (f"No hour cell clears the noise bar across {sessions} sessions "
                f"({tested} cells screened). Nothing here repeats reliably enough "
                "to trade off the clock alone — the movement is real, the "
                "hour-of-day timing is not.")
    return (f"{n_sig} of {tested} hour cells clear the noise bar over {sessions} "
            "sessions. Those are the only cells on the grid where the same thing "
            "has happened at the same hour more often than chance explains.")


# ----------------------------------------------------------- assembly

def _entity(tk, name, group, rel_full, rel_dates, price, today, run_stats, kind):
    """The common card every tracked thing gets — stock or sector ETF.

    Everything is computed off the ticker's own valid tail, so a name that listed
    six weeks ago reports six weeks of truth rather than two years of padding."""
    r, off = _tail(rel_full)
    if len(r) < 2:
        return None
    dts = rel_dates[off:]
    q = _quad(float(r[-2]), float(r[-1]))
    streak_n, streak_dir = _streak(r)
    bs = _break_state(r)
    dots = _dots(r, dts)
    runs30 = _runs(r[-DOT_WIN:])
    trend = np.cumprod(1 + r[-TREND_WIN:] / 100)
    pctl = _percentiles(r)
    return {
        "kind": kind,
        "ticker": tk, "name": name, "sector": group,
        "n_hist": int(len(r)),
        "short_hist": len(r) < MIN_POOL,
        "price": round(float(price), 2),
        "today": round(float(today), 2),
        "rel_today": round(float(r[-1]), 2),
        "rel_yday": round(float(r[-2]), 2),
        "rel_5d": round(float(r[-5:].sum()), 2),
        "rel_21d": round(float(r[-21:].sum()), 2),
        "quad": q, "call": QUAD_META[q][0], "why": QUAD_META[q][1],
        "streak": streak_n, "streak_dir": streak_dir,
        "dots": dots, "dots5": dots[-5:], "runs": runs30,
        "broke": bs["broke"], "broke_len": bs["broke_len"], "broke_dir": bs["broke_dir"],
        "green30": sum(1 for x in dots if x["s"] > 0),
        "longest_red30": max([x["len"] for x in runs30 if x["sign"] < 0] or [0]),
        "longest_green30": max([x["len"] for x in runs30 if x["sign"] > 0] or [0]),
        "pattern": _pattern_note(bs, run_stats),
        "pctl": pctl,
        "pct_read": _pct_reading(tk, name, pctl),
        "trend": [round(float(x) * 100, 2) for x in trend],
        "trend_chg": round(float(trend[-1] - 1) * 100, 2),
    }


def build():
    dates, px, px_raw, intra_all = _panel()
    rets = {k: np.diff(v) / v[:-1] for k, v in px.items()}
    spy = rets[BENCH]
    # rel is in PERCENT from here down — THRESH, fwd returns and the duration
    # backtest all read percent, so the conversion happens once, at the source.
    rel = {t: (rets[t] - spy) * 100 for t in TOP10 if t in rets}
    rel_etf = {t: (rets[t] - spy) * 100 for t in SECTOR_ETFS if t in rets}
    rel_smh = ((rets[SEMIS] - spy) * 100) if SEMIS in rets else None

    intra = {t: intra_all[t] for t in [*TOP10, BENCH] if intra_all.get(t)}
    # anchor today's intraday path to the official previous daily close
    prev_close = {k: float(v[-2]) for k, v in px_raw.items() if len(v) > 1}
    morning = _morning(intra, prev_close)
    # Hourly pattern store: grows on every build, so the pattern screen keeps
    # history after sessions age out of Yahoo's 60-day intraday window.
    try:
        hourly = _hourly_patterns(_merge_store(_hourly_rows(intra, prev_close)))
    except Exception:  # noqa: BLE001
        hourly = None

    # Pooled history only takes names with a full two years — a six-week listing
    # would otherwise contribute noise to the base rates everything is judged on.
    pool = {t: _tail(r)[0] for t, r in rel.items() if len(_tail(r)[0]) >= MIN_POOL}
    pool_etf = {t: _tail(r)[0] for t, r in rel_etf.items() if len(_tail(r)[0]) >= MIN_POOL}
    dur = _duration_stats(pool)
    etf_dur = _duration_stats(pool_etf)
    run_stats = _run_stats(pool)
    etf_run_stats = _run_stats(pool_etf)
    pct_bt = _pct_backtest(pool)
    pct_bt_etf = _pct_backtest(pool_etf)
    # returns are diffs, so the first date has no return attached to it
    rel_dates = [datetime.fromtimestamp(t, ET).strftime("%Y-%m-%d") for t in dates[1:]]

    # Group-level series: a rotation is a sector event, so measure duration
    # there too. A single stock can wobble while its sector keeps trending.
    sec_rel = {}
    for sec in SECTOR_ORDER:
        mem = [rel[t] for t in TOP10 if t in rel and SECTOR[t] == sec]
        if mem:
            sec_rel[sec] = np.nanmean(np.vstack(mem), axis=0)
    sec_dur = _duration_stats({k: _tail(v)[0] for k, v in sec_rel.items()})

    sectors_detail = []
    for sec, s in sec_rel.items():
        s = _tail(s)[0]
        if len(s) < 2:
            continue
        q = _quad(float(s[-2]), float(s[-1]))
        n_streak, sdir = _streak(s)
        sectors_detail.append({
            "sector": sec,
            "rel_today": round(float(s[-1]), 2),
            "rel_5d": round(float(s[-5:].sum()), 2),
            "quad": q, "call": QUAD_META[q][0],
            "streak": n_streak, "streak_dir": sdir,
            "typical_days": sec_dur.get(q, {}).get("median_days", 0),
            "p75_days": sec_dur.get(q, {}).get("p75_days", 0),
            "hist": [round(float(x), 3) for x in s[-40:]],
        })
    sectors_detail.sort(key=lambda x: -x["rel_today"])

    # Rolling 10-day correlation to semis — does the relationship hold day to day?
    corr_hist = {}
    if rel_smh is not None:
        smh_t = _tail(rel_smh)[0]
        for t in TOP10:
            if t not in rel:
                continue
            r = _tail(rel[t])[0]
            k = min(len(r), len(smh_t))
            if k < CORR_FAST + 4:
                continue
            a, b = r[-k:], smh_t[-k:]
            span = min(40, k - CORR_FAST)
            corr_hist[t] = [round(_corr(a[i - CORR_FAST:i], b[i - CORR_FAST:i]), 3)
                            for i in range(k - span, k + 1)]

    def _pair_corr(r, other, win):
        k = min(len(r), len(other), win)
        return round(_corr(r[-k:], other[-k:]), 2) if k >= 5 else 0.0

    stocks = []
    for t in TOP10:
        if t not in rel:
            continue
        e = _entity(t, NAMES[t], SECTOR[t], rel[t], rel_dates,
                    px_raw[t][-1], float(_tail(rets[t])[0][-1]) * 100,
                    run_stats, "stock")
        if e is None:
            continue
        r = _tail(rel[t])[0]
        rt = _tail(rets[t])[0]
        m = next((p for p in (morning or {}).get("paths", []) if p["ticker"] == t), None)
        e.update({
            "typical_days": dur.get(e["quad"], {}).get("median_days", 0),
            "corr_spy": _pair_corr(rt, spy, CORR_SLOW),
            "corr_qqq": _pair_corr(rt, _tail(rets["QQQ"])[0], CORR_SLOW) if "QQQ" in rets else 0.0,
            "semis_fast": _pair_corr(r, _tail(rel_smh)[0], CORR_FAST) if rel_smh is not None else 0.0,
            "semis_slow": _pair_corr(r, _tail(rel_smh)[0], CORR_SLOW) if rel_smh is not None else 0.0,
            "morning_rel": m["last"] if m else None,
            "corr_hist": corr_hist.get(t, []),
        })
        stocks.append(e)
    stocks.sort(key=lambda s: -s["rel_today"])

    etfs = []
    for t in SECTOR_ETFS:
        if t not in rel_etf:
            continue
        e = _entity(t, SECTOR_ETF_NAMES.get(t, t), "Sector ETF", rel_etf[t], rel_dates,
                    px_raw[t][-1], float(_tail(rets[t])[0][-1]) * 100,
                    etf_run_stats, "sector")
        if e is None:
            continue
        e["typical_days"] = etf_dur.get(e["quad"], {}).get("median_days", 0)
        etfs.append(e)
    etfs.sort(key=lambda s: -s["rel_today"])

    return {
        "as_of": dates[-1],
        "live": _is_live(dates[-1]),
        "spy_today": round(float(spy[-1]) * 100, 2),
        "sectors": SECTOR_ORDER,
        "benchmarks": BENCHMARKS,
        "components": _components(rets, px_raw),
        "stocks": stocks,
        "sector_etfs": etfs,
        "duration": dur,
        "sector_duration": sec_dur,
        "sectors_detail": sectors_detail,
        "morning": morning,
        "hourly": hourly,
        "headline": _headline(stocks, round(float(spy[-1]) * 100, 2)),
        "sector_flow": _sector_flow(stocks),
        "run_stats": run_stats,
        "etf_run_stats": etf_run_stats,
        "streak_board": _streak_board(stocks, run_stats),
        "etf_streak_board": _streak_board(etfs, etf_run_stats),
        "pct_windows": PCT_WINDOWS,
        "pct_horizons": PCT_HORIZONS,
        "pct_buckets": [{"lo": a, "hi": b} for a, b in PCT_BUCKETS],
        "pct_backtest": {"stocks": pct_bt, "sectors": pct_bt_etf},
        "pct_verdict": {"stocks": _pct_verdict(pct_bt),
                        "sectors": _pct_verdict(pct_bt_etf)},
        "pct_board": _pct_board(stocks, etfs),
    }


def _pct_board(stocks, etfs):
    """Percentile highlights — the extremes are the only part worth reading first.

    `stretched` are names whose 5-day relative strength is in the top decile of
    their own history; `washed` are the bottom decile. Sorted by how extreme."""
    def card(e):
        w = e["pctl"]["windows"].get("5") or {}
        return {"ticker": e["ticker"], "name": e["name"], "sector": e["sector"],
                "kind": e["kind"], "pct": w.get("pct"), "value": w.get("value"),
                "green": w.get("green"), "streak": e["streak"],
                "streak_dir": e["streak_dir"],
                "run_pct": e["pctl"].get("run", {}).get("pct"),
                "dots5": e["dots5"], "read": e["pct_read"],
                "short_hist": e["short_hist"]}
    all_e = [card(e) for e in list(stocks) + list(etfs)
             if (e["pctl"]["windows"].get("5") or {}).get("pct") is not None]
    hot = sorted([c for c in all_e if c["pct"] >= 90], key=lambda c: -c["pct"])
    cold = sorted([c for c in all_e if c["pct"] <= 10], key=lambda c: c["pct"])
    return {"stretched": hot, "washed": cold,
            "ranked": sorted(all_e, key=lambda c: -c["pct"])}


def _streak_board(stocks, stats):
    """Highlight of the day: streaks that just snapped, and runs long enough
    that history says they are close to snapping."""
    breaks, stretched = [], []
    for s in stocks:
        if s["broke"] and s["broke_len"] >= 2:
            k = str(min(s["broke_len"], RUN_CAP))
            b = stats.get(k, {})
            breaks.append({
                "ticker": s["ticker"], "name": s["name"], "sector": s["sector"],
                "len": s["broke_len"], "dir": s["broke_dir"],
                "rel_today": s["rel_today"],
                "fwd3": b.get("after_break_fwd3", 0.0),
                "hit": b.get("after_break_hit", 0.0),
                "n": b.get("n_break", 0),
                "dots5": s["dots5"],
            })
        elif s["streak"] >= 3:
            k = str(min(s["streak"], RUN_CAP))
            c = stats.get(k, {})
            stretched.append({
                "ticker": s["ticker"], "name": s["name"], "sector": s["sector"],
                "len": s["streak"], "dir": s["streak_dir"],
                "rel_today": s["rel_today"],
                "continue_pct": c.get("continue_pct", 0.0),
                "reverse_pct": c.get("reverse_pct", 0.0),
                "n": c.get("n", 0),
                "dots5": s["dots5"],
            })
    breaks.sort(key=lambda x: -x["len"])
    stretched.sort(key=lambda x: -x["len"])
    return {"breaks": breaks, "stretched": stretched}


def _sector_flow(stocks):
    g = defaultdict(list)
    for s in stocks:
        g[s["sector"]].append(s)
    out = []
    for sec in SECTOR_ORDER:
        if sec not in g:
            continue
        m = g[sec]
        out.append({
            "sector": sec,
            "rel_today": round(sum(x["rel_today"] for x in m) / len(m), 2),
            "rel_5d": round(sum(x["rel_5d"] for x in m) / len(m), 2),
            "members": [x["ticker"] for x in m],
        })
    return sorted(out, key=lambda x: -x["rel_today"])


def _is_live(bar_ts):
    bar_day = datetime.fromtimestamp(bar_ts, ET).date()
    now = datetime.now(ET)
    return bar_day == now.date() and now.hour < 16


def _headline(stocks, spy):
    flow = _sector_flow(stocks)
    lead, lag = flow[0], flow[-1]
    turning = [s["ticker"] for s in stocks if s["quad"] == "turning"]
    parts = []
    parts.append(f"The S&P is basically flat ({spy:+.2f}%), but money is still moving underneath it."
                 if abs(spy) < 0.25 else f"The S&P is {spy:+.2f}% today.")
    parts.append(f"<b>{lead['sector']}</b> is the side being bought ({lead['rel_today']:+.2f}% vs the index); "
                 f"<b>{lag['sector']}</b> is the side being sold ({lag['rel_today']:+.2f}%).")
    if turning:
        parts.append(f"Just turning up today: <b>{', '.join(turning)}</b>.")
    return " ".join(parts)


def _json_safe(o):
    """NaN/Infinity are not valid JSON — one of them anywhere breaks the whole
    page at JSON.parse. Short-history tickers make NaN reachable, so scrub once."""
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        o = o.item()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def get(force=False):
    now = time.time()
    if not force and _cache["payload"] and now - _cache["t"] < CACHE_TTL:
        return _cache["payload"]
    _cache.update(t=now, payload=_json_safe(build()))
    return _cache["payload"]


if __name__ == "__main__":
    d = get()
    print(d["headline"], "\n")
    print(f"{'':6} {'rel_y':>7}{'rel_t':>7}  {'call':<11}{'streak':>7}{'typ':>5}"
          f"{'semis10':>9}{'semis63':>9}")
    for s in d["stocks"]:
        print(f"{s['ticker']:6} {s['rel_yday']:+7.2f}{s['rel_today']:+7.2f}  "
              f"{s['call']:<11}{s['streak']*s['streak_dir']:+7d}{s['typical_days']:5.0f}"
              f"{s['semis_fast']:9.2f}{s['semis_slow']:9.2f}")
    print("\nSECTOR NOW")
    for s in d["sectors_detail"]:
        print(f"  {s['sector']:<20}{s['rel_today']:+7.2f} 5d {s['rel_5d']:+7.2f}  "
              f"{s['call']:<11} streak {s['streak']*s['streak_dir']:+3d}  "
              f"typical {s['typical_days']:.0f}d (p75 {s['p75_days']:.0f}d)")
    print("\nSECTOR DURATION BACKTEST")
    for q, v in d["sector_duration"].items():
        print(f"  {v['label']:<11} n={v['n']:<5} median {v['median_days']:.0f}d  "
              f"mean {v['mean_days']}d  2+days {v['share_2plus']}%  "
              f"cont5 {v['fwd5']:+.3f}%  hit5 {v['hit5']}%")
    print("\nSTOCK DURATION BACKTEST")
    for q, v in d["duration"].items():
        print(f"  {v['label']:<11} n={v['n']:<5} median {v['median_days']:.0f}d  "
              f"mean {v['mean_days']}d  2+days {v['share_2plus']}%  "
              f"fwd5 {v['fwd5']:+.3f}%  hit5 {v['hit5']}%")
    m = d["morning"]
    if m:
        b = m["backtest"]
        print(f"\nMORNING ({m['session']}, {m['bars_done']} bars done): "
              f"n={b['n']} over {b['sessions']} sessions, hit {b['hit']}%, corr {b['corr']}")
        print(" ", b["verdict"])
