#!/usr/bin/env python3
"""Earnings performance tracker — what a stock does into, on, and after its print.

Five measurements per print, all on closes, all in *trading sessions*, anchored
on d0 = the last close before the announcement:

    between   close(d0)      / close(prev print's reaction close)   drift between prints
    pre15     close(d0)      / close(d0 - 15)                       run-up into the print
    reaction  close(d0 + 1)  / close(d0)                            the print itself
    fwd5      close(d0 + 6)  / close(d0 + 1)                        5 sessions after
    fwd10     close(d0 + 11) / close(d0 + 1)                        10 sessions after

`between` deliberately starts at the previous reaction close, so the prior
print's own jump is not counted twice — it makes `between` and `pre15` two
clean, comparable reads on positioning *before* this print.

d0 depends on when the company reports: an after-close (AMC) print on day D
reacts on D+1, so d0 = D; a before-open (BMO) print reacts on D itself, so
d0 = the session before D. The report time comes from Yahoo, not guessed.

Everything is Yahoo:
  * quoteSummary `earnings`        — last 4 quarters incl. period-end dates
  * /v1/finance/visualization      — deep EPS history (~10y); stale at the recent
                                     end, so it is only trusted for older dates
  * quoteSummary `calendarEvents`  — the upcoming date + consensus EPS
  * chart?events=split             — split table, to put old EPS on today's basis
  * chart SPY / ^VIX               — market move on the reaction day, VIX at d0
"""

import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — stdlib tz data always present on macOS
    ET = timezone.utc

HERE = Path(__file__).parent
CACHE_FILE = HERE / ".earnings_perf_cache.json"
CACHE_TTL = 12 * 60 * 60          # history only changes once a quarter
# Bump whenever the shape of a cached payload changes, so old entries are
# discarded instead of being served with fields the UI now expects.
CACHE_SCHEMA = 5
MAX_EVENTS = 40                   # ~10 years of prints, the span of the price series
PRE_SESSIONS = 15                 # Raman: "last 15 days performance before earnings"
FWD = (5, 10)                     # post-release horizons, in sessions

# The five measured windows, in reading order, grouped by the sub-tab that owns
# them. Order matters: the UI renders straight from this.
METRICS = [
    ("between", "Between Prints", "pre"),
    ("pre15", "15D Before", "pre"),
    ("reaction", "Reaction", "day"),
    ("fwd5", "5D Fwd", "post"),
    ("fwd10", "10D Fwd", "post"),
]
GROUPS = {"pre": ["between", "pre15"], "day": ["reaction"], "post": ["fwd5", "fwd10"]}

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

_cache = {}
_cache_loaded = False
_market = {}                      # SPY / ^VIX series, fetched once per process


def _load_cache():
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            _cache = {}


def _save_cache():
    try:
        CACHE_FILE.write_text(json.dumps(_cache))
    except Exception:
        pass


# ── Yahoo access ─────────────────────────────────────────────────────────────

def _crumbed():
    """(opener, crumb) from the host server if available, else a local session."""
    try:
        import server  # reuse the dashboard's cached cookie jar + crumb
        return server._get_yahoo_crumb()
    except Exception:
        pass
    try:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj),
            urllib.request.HTTPSHandler(context=_ctx))
        opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        try:
            opener.open("https://fc.yahoo.com", timeout=10)
        except Exception:
            pass
        crumb = opener.open(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=10).read().decode()
        return (opener, crumb) if crumb else (None, None)
    except Exception:
        return None, None


def _quote_summary(ticker, modules, opener, crumb):
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
           + urllib.parse.quote(ticker) + "?modules=" + modules
           + "&crumb=" + urllib.parse.quote(crumb))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(opener.open(req, timeout=12).read().decode())
    return data["quoteSummary"]["result"][0]


def _chart(ticker, events=None):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(ticker) + "?interval=1d&range=10y"
           + ("&events=" + events if events else ""))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=_ctx) as r:
        return json.loads(r.read().decode())["chart"]["result"][0]


def _closes(result):
    """[(YYYY-MM-DD, close)], oldest first, gaps dropped."""
    ts = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    out = []
    for t, c in zip(ts, closes):
        if t is None or c is None:
            continue
        # Yahoo stamps a daily bar at the exchange open; ET date is the session.
        out.append((datetime.fromtimestamp(t, ET).strftime("%Y-%m-%d"), float(c)))
    return out


def fetch_sessions(ticker):
    """Daily close series plus the split table, both from one chart call."""
    res = _chart(ticker, events="split")
    splits = []
    for s in (res.get("events", {}).get("splits") or {}).values():
        try:
            d = datetime.fromtimestamp(s["date"], ET).strftime("%Y-%m-%d")
            ratio = float(s["numerator"]) / float(s["denominator"])
            if ratio > 0:
                splits.append((d, ratio))
        except Exception:
            continue
    splits.sort()
    return _closes(res), splits


def _market_series():
    """SPY and ^VIX closes keyed by session date. Fetched once, best effort."""
    global _market
    if _market:
        return _market
    out = {}
    for key, sym in (("spy", "SPY"), ("vix", "^VIX")):
        try:
            out[key] = dict(_closes(_chart(sym)))
        except Exception:
            out[key] = {}
    _market = out
    return _market


# ── Earnings dates ───────────────────────────────────────────────────────────

def _timing_from_et(dt_et):
    """AMC / BMO / DMH from the announcement time in Eastern."""
    minutes = dt_et.hour * 60 + dt_et.minute
    if minutes >= 16 * 60:
        return "AMC"
    if minutes <= 9 * 60 + 30:
        return "BMO"
    return "DMH"


def _to_float(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _recent_events(ticker, opener, crumb):
    """Last 4 reported quarters — authoritative, current, and the only source
    that carries the period-end date."""
    out = []
    try:
        res = _quote_summary(ticker, "earnings", opener, crumb)
        for q in res["earnings"]["earningsChart"]["quarterly"]:
            rd = (q.get("reportedDate") or {}).get("raw")
            if not rd:
                continue
            dt = datetime.fromtimestamp(rd, ET)
            out.append({
                "date": dt.strftime("%Y-%m-%d"),
                "timing": _timing_from_et(dt),
                "surprisePct": _to_float(q.get("surprisePct")),
                "epsActual": (q.get("actual") or {}).get("raw"),
                "epsEstimate": (q.get("estimate") or {}).get("raw"),
                "periodEnd": (q.get("periodEndDate") or {}).get("fmt"),
                "adjusted": True,   # already on today's share basis
            })
    except Exception:
        pass
    return out


def _historical_events(ticker, opener, crumb):
    """Deep history from the earnings index. Stale at the recent end by design,
    and its EPS is on the share basis of the day — not split-adjusted."""
    out = []
    body = {
        "sortType": "DESC", "entityIdType": "earnings",
        "sortField": "startdatetime",
        "includeFields": ["ticker", "startdatetime", "startdatetimetype",
                          "epsestimate", "epsactual", "epssurprisepct"],
        "query": {"operator": "and", "operands": [
            {"operator": "eq", "operands": ["ticker", ticker]}]},
        "offset": 0, "size": 60,
    }
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/visualization?crumb="
               + urllib.parse.quote(crumb))
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"User-Agent": "Mozilla/5.0",
                     "Content-Type": "application/json"})
        data = json.loads(opener.open(req, timeout=20).read().decode())
        doc = data["finance"]["result"][0]["documents"][0]
        for r in doc["rows"]:
            iso = r[1]
            if not iso:
                continue
            dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S") \
                .replace(tzinfo=timezone.utc).astimezone(ET)
            # Columns follow includeFields: [ticker, startdatetime,
            # startdatetimetype, epsestimate, epsactual, epssurprisepct].
            estimate, actual, surprise = r[3], r[4], r[5]
            if actual is None:        # no reported EPS ⇒ never actually printed
                continue
            out.append({
                "date": dt.strftime("%Y-%m-%d"),
                "timing": _timing_from_et(dt),
                "surprisePct": _to_float(surprise),
                "epsActual": actual, "epsEstimate": estimate,
                "periodEnd": None, "adjusted": False,
            })
    except Exception:
        pass
    return out


def _next_earnings(ticker, opener, crumb):
    try:
        res = _quote_summary(ticker, "calendarEvents", opener, crumb)
        e = res["calendarEvents"]["earnings"]
        dates = e.get("earningsDate") or []
        return {
            "date": dates[0].get("fmt") if dates else None,
            "estimated": bool(e.get("isEarningsDateEstimate")),
            "epsEstimate": (e.get("earningsAverage") or {}).get("raw"),
        }
    except Exception:
        return {"date": None, "estimated": None, "epsEstimate": None}


def _project_next(events):
    """Date of the next print, projected from the ticker's own cadence.

    Yahoo's calendar sometimes still names a print that has already reported
    (OKLO's 2026-08-07 was still being returned as upcoming 18 days after the
    fact), which would pin a stale row to the top of the ledger. Project off
    the last period end plus that company's own reporting lag; falling back on
    the median gap between prints overstates it whenever Yahoo's history is
    missing a quarter."""
    if not events:
        return None
    last = events[-1]
    ld = datetime.strptime(last["date"], "%Y-%m-%d")
    pe = last.get("periodEnd")
    if pe:
        p = datetime.strptime(pe, "%Y-%m-%d")
        lag = (ld - p).days
        if 5 <= lag <= 120:
            m, y = p.month + 3, p.year
            if m > 12:
                m, y = m - 12, y + 1
            nxt_pe = datetime(y, m, monthrange(y, m)[1])
            return (nxt_pe + timedelta(days=lag)).strftime("%Y-%m-%d")
    if len(events) < 3:
        return None
    ds = [datetime.strptime(e["date"], "%Y-%m-%d") for e in events[-9:]]
    spans = sorted(s for s in ((ds[i] - ds[i - 1]).days for i in range(1, len(ds)))
                   if 45 <= s <= 135)
    cadence = spans[len(spans) // 2] if spans else 91
    return (ld + timedelta(days=cadence)).strftime("%Y-%m-%d")


def collect_events(ticker, opener, crumb):
    """Merged, de-duplicated, oldest-first list of past earnings prints."""
    recent = _recent_events(ticker, opener, crumb)
    older = _historical_events(ticker, opener, crumb)
    merged = list(recent)
    seen = {e["date"] for e in recent}

    def near_existing(d):
        # Sources occasionally differ by a day on the same print.
        dt = datetime.strptime(d, "%Y-%m-%d")
        for s in seen:
            if abs((dt - datetime.strptime(s, "%Y-%m-%d")).days) <= 3:
                return True
        return False

    for e in older:
        if not near_existing(e["date"]):
            merged.append(e)
            seen.add(e["date"])
    merged.sort(key=lambda e: e["date"])
    return merged[-MAX_EVENTS:]


# ── EPS: split adjustment + period ending ────────────────────────────────────

def _median_abs(vals):
    vals = sorted(abs(v) for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


SPLIT_WIN = 2        # prints compared on each side of a candidate boundary
SPLIT_TOL = 2.2      # how far the observed jump may sit from the split ratio


def _split_boundary(events, split_date, ratio):
    """Index of the last event still on the pre-split share basis, or None.

    Yahoo's earnings index is restated for some splits and not others (AAPL's
    and TSLA's were back-applied; NVDA's 2024 10:1 was not), and nothing in the
    payload says which. But an unapplied split leaves a signature: EPS drops by
    roughly the split ratio from one print to the next. Find the print pair
    whose jump best matches the ratio; if none is close, the series is already
    adjusted and nothing is changed."""
    if ratio < 1.5:
        return None
    best, best_err = None, None
    sd = datetime.strptime(split_date, "%Y-%m-%d")
    for i in range(len(events) - 1):
        # The boundary can only be at or before the split (a row printed after it
        # is necessarily on the new basis) and within about a year of it —
        # without that leash any ordinary EPS decline of roughly the split ratio
        # is mistaken for the split itself.
        if events[i]["date"] > split_date:
            break
        if (sd - datetime.strptime(events[i]["date"], "%Y-%m-%d")).days > 400:
            continue
        before = _median_abs([e.get("epsActual") for e in
                              events[max(0, i - SPLIT_WIN + 1):i + 1]])
        after = _median_abs([e.get("epsActual") for e in
                             events[i + 1:i + 1 + SPLIT_WIN]])
        if not before or not after or before < 0.02 or after < 0.02:
            continue
        jump = before / after
        if not (ratio / SPLIT_TOL <= jump <= ratio * SPLIT_TOL):
            continue
        err = abs(math.log(jump / ratio))
        if best_err is None or err < best_err:
            best, best_err = i, err
    return best


def apply_splits(events, splits):
    """Put every pre-split EPS on today's share basis.

    Rows from quoteSummary (adjusted=True) are already there and are left
    alone; only the deep-history index rows are candidates."""
    for e in events:
        e.setdefault("splitFactor", 1.0)
    idx = [e for e in events if not e.get("adjusted")]
    for split_date, ratio in splits:
        b = _split_boundary(idx, split_date, ratio)
        if b is None:
            continue
        for e in idx[:b + 1]:
            e["splitFactor"] = round(e["splitFactor"] * ratio, 6)
    for e in events:
        f = e.get("splitFactor") or 1.0
        if f == 1.0:
            continue
        for k in ("epsActual", "epsEstimate"):
            v = _to_float(e.get(k))
            e[k] = round(v / f, 4) if v is not None else None
    return events


def _month_index(d):
    return d.year * 12 + d.month - 1


def infer_period_ends(events):
    """Fill in the period ending for prints the index doesn't carry one for.

    Companies keep their fiscal calendar, so the four quarters that *do* have a
    period-end date (from quoteSummary) define the report-month → period-month
    offset for every older print of the same quarter. Only month precision is
    ever shown, so this is exact where it matters."""
    known = []
    for e in events:
        if e.get("periodEnd"):
            rd = datetime.strptime(e["date"], "%Y-%m-%d")
            pe = datetime.strptime(e["periodEnd"], "%Y-%m-%d")
            known.append((rd.month, _month_index(rd) - _month_index(pe), pe.day))
    if not known:
        return events
    for e in events:
        if e.get("periodEnd"):
            continue
        rd = datetime.strptime(e["date"], "%Y-%m-%d")
        # nearest known report month, measured around the 12-month circle
        _, lag, day = min(known, key=lambda k: min((k[0] - rd.month) % 12,
                                                   (rd.month - k[0]) % 12))
        mi = _month_index(rd) - lag
        y, m = divmod(mi, 12)
        m += 1
        e["periodEnd"] = "%04d-%02d-%02d" % (y, m, min(day, monthrange(y, m)[1]))
        e["periodEndInferred"] = True
    return events


# ── Window maths ─────────────────────────────────────────────────────────────

def _index_on_or_before(dates, target):
    """Index of the last session on or before `target`; None if out of range."""
    lo, hi, found = 0, len(dates) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found


def _ret(closes, i_from, i_to):
    if i_from is None or i_to is None:
        return None
    if not (0 <= i_from < len(closes) and 0 <= i_to < len(closes)):
        return None
    base = closes[i_from]
    if not base:
        return None
    return round((closes[i_to] - base) / base * 100, 2)


def _anchor(event, dates):
    """d0 — the index of the last close before the announcement."""
    i = _index_on_or_before(dates, event["date"])
    if i is None:
        return None
    if event["timing"] == "BMO":
        # Reported before the open: that day's own close is the reaction.
        return i - 1 if dates[i] == event["date"] else i
    # AMC (or intraday, which still leaves most of the reaction to D+1):
    # the print lands after day D's close.
    return i


def _move_duration(closes, react):
    """How many sessions the reaction kept running the same way, reaction
    day included. Mirrors the mock's "Move Duration (days)"."""
    if react + 1 >= len(closes) or not closes[react - 1]:
        return None
    up = closes[react] >= closes[react - 1]
    n = 1
    for j in range(react + 1, min(react + 22, len(closes))):
        if not closes[j - 1]:
            break
        if (closes[j] >= closes[j - 1]) != up:
            break
        n += 1
    return n


def _gap_flags(event_dates):
    """True where a print is missing between this event and the one before it,
    judged against the ticker's own reporting cadence."""
    if len(event_dates) < 3:
        return [True] + [False] * (len(event_dates) - 1)
    ds = [datetime.strptime(d, "%Y-%m-%d") for d in event_dates]
    spans = sorted((ds[i] - ds[i - 1]).days for i in range(1, len(ds)))
    typical = spans[len(spans) // 2] or 91
    return [True] + [(ds[i] - ds[i - 1]).days > typical * 1.6
                     for i in range(1, len(ds))]


def measure_event(event, dates, closes, prev_react_idx, mkt, gap=False):
    """Attach the five window returns, market context and the indexed path."""
    d0 = _anchor(event, dates)
    if d0 is None or d0 < 1:
        return None
    react = d0 + 1
    have_react = react < len(closes)

    out = dict(event)
    out["preClose"] = round(closes[d0], 2)
    out["preCloseDate"] = dates[d0]
    out["reactionDate"] = dates[react] if have_react else None
    out["d0Index"] = d0
    out["reactIndex"] = react if have_react else None

    # A missing print (Yahoo's index is frozen ~a year back, quoteSummary only
    # carries 4 quarters, so one quarter can fall through the crack) would make
    # "between prints" silently span two quarters. Report nothing instead.
    out["gapBefore"] = bool(gap)
    out["between"] = None if gap else _ret(closes, prev_react_idx, d0)
    out["pre15"] = _ret(closes, d0 - PRE_SESSIONS, d0)
    out["reaction"] = _ret(closes, d0, react) if have_react else None
    for n in FWD:
        out["fwd%d" % n] = (_ret(closes, react, react + n)
                            if have_react and react + n < len(closes) else None)
    out["duration"] = _move_duration(closes, react) if have_react else None

    # Market context on the same reaction day.
    spy, vix = mkt.get("spy", {}), mkt.get("vix", {})
    out["spy"] = None
    if have_react:
        rd, pd_ = dates[react], dates[d0]
        if rd in spy and pd_ in spy and spy[pd_]:
            out["spy"] = round((spy[rd] - spy[pd_]) / spy[pd_] * 100, 2)
    out["vix"] = round(vix[dates[d0]], 1) if dates[d0] in vix else None

    # EPS surprise in dollars, on the adjusted basis.
    a, e_ = _to_float(out.get("epsActual")), _to_float(out.get("epsEstimate"))
    out["epsActual"], out["epsEstimate"] = a, e_
    out["surpriseAbs"] = round(a - e_, 4) if (a is not None and e_ is not None) else None
    if out.get("surprisePct") is None and a is not None and e_:
        out["surprisePct"] = round((a - e_) / abs(e_) * 100, 2)
    return out


# ── Statistics ───────────────────────────────────────────────────────────────

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return round(math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)), 2)


def _pctile(xs, pct):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def _median(xs):
    return _pctile(xs, 50)


def _histogram(xs, bins=13):
    """Counts over `bins` equal buckets spanning the observed range, snapped to
    a whole-percent grid so the axis reads cleanly."""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return []
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    step = max((hi - lo) / bins, 0.5)
    step = round(step * 2) / 2 or 0.5          # 0.5%-grained buckets
    start = math.floor(lo / step) * step
    n = max(1, int(math.ceil((hi - start) / step)))
    out = [{"x0": round(start + i * step, 2),
            "x1": round(start + (i + 1) * step, 2), "n": 0} for i in range(n)]
    for v in xs:
        i = min(int((v - start) / step), n - 1)
        out[i]["n"] += 1
    return out


def _corr(pairs):
    """Pearson r over (x, y) pairs where both sides exist."""
    pts = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pts) < 4:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sx = math.sqrt(sum((p[0] - mx) ** 2 for p in pts))
    sy = math.sqrt(sum((p[1] - my) ** 2 for p in pts))
    if sx == 0 or sy == 0:
        return None
    cov = sum((p[0] - mx) * (p[1] - my) for p in pts)
    return {"r": round(cov / (sx * sy), 2), "n": n}


def _conditional(events, cond_key, out_key):
    """Mean outcome split by the sign of the conditioning window — the plain
    reading of "does what happens before tell you anything about after?"."""
    up = [e[out_key] for e in events
          if e.get(cond_key) is not None and e[cond_key] > 0 and e.get(out_key) is not None]
    dn = [e[out_key] for e in events
          if e.get(cond_key) is not None and e[cond_key] <= 0 and e.get(out_key) is not None]
    if not up or not dn:
        return None
    return {"upN": len(up), "upMean": _mean(up), "upHit": round(sum(1 for v in up if v > 0) / len(up) * 100),
            "dnN": len(dn), "dnMean": _mean(dn), "dnHit": round(sum(1 for v in dn if v > 0) / len(dn) * 100)}


def stats_for(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": _mean(vals), "sd": _stdev(vals),
        "median": _median(vals), "avgAbs": _mean([abs(v) for v in vals]),
        "q05": _pctile(vals, 5), "q20": _pctile(vals, 20), "q50": _pctile(vals, 50),
        "q80": _pctile(vals, 80), "q95": _pctile(vals, 95),
        "min": round(min(vals), 2), "max": round(max(vals), 2),
        "upPct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        "hist": _histogram(vals),
    }


def _streak(reactions):
    """Consecutive same-direction reaction days at the most recent end."""
    if not reactions:
        return None
    sign = 1 if reactions[-1] > 0 else -1
    n = 0
    for v in reversed(reactions):
        if (v > 0) == (sign > 0):
            n += 1
        else:
            break
    return {"n": n, "up": sign > 0}


def summarize(events, typical_day):
    windows = {k: stats_for([e.get(k) for e in events]) for k, _, _ in METRICS}
    reactions = [e["reaction"] for e in events if e.get("reaction") is not None]
    durations = [e["duration"] for e in events if e.get("duration") is not None]
    return {
        "windows": windows,
        "events": len(events),
        "typicalDay": typical_day,
        "reactionUpStreak": _streak(reactions),
        "avgDuration": _mean(durations),
        "corr": {
            "pre15_reaction": _corr([(e.get("pre15"), e.get("reaction")) for e in events]),
            "between_reaction": _corr([(e.get("between"), e.get("reaction")) for e in events]),
            "surprise_reaction": _corr([(e.get("surprisePct"), e.get("reaction")) for e in events]),
            "reaction_fwd5": _corr([(e.get("reaction"), e.get("fwd5")) for e in events]),
            "reaction_fwd10": _corr([(e.get("reaction"), e.get("fwd10")) for e in events]),
        },
        "cond": {
            "pre15_reaction": _conditional(events, "pre15", "reaction"),
            "between_reaction": _conditional(events, "between", "reaction"),
            "reaction_fwd5": _conditional(events, "reaction", "fwd5"),
        },
    }


def typical_daily_move(closes, from_idx):
    """Average absolute 1-session move over the covered span — the yardstick the
    reaction day is worth comparing against."""
    moves = [abs(closes[i] - closes[i - 1]) / closes[i - 1] * 100
             for i in range(max(1, from_idx), len(closes)) if closes[i - 1]]
    return _mean(moves)


# ── Public entry point ───────────────────────────────────────────────────────

def earnings_performance(ticker, force=False):
    ticker = ticker.upper()
    _load_cache()
    now = time.time()
    hit = _cache.get(ticker)
    if (hit and not force and hit.get("v") == CACHE_SCHEMA
            and now - hit.get("t", 0) < CACHE_TTL):
        return hit["data"]

    opener, crumb = _crumbed()
    if not (opener and crumb):
        return {"ticker": ticker, "ok": False,
                "error": "Yahoo session unavailable"}
    try:
        sessions, splits = fetch_sessions(ticker)
        if len(sessions) < 60:
            raise ValueError("not enough price history")
        dates = [d for d, _ in sessions]
        closes = [c for _, c in sessions]
        mkt = _market_series()

        raw = collect_events(ticker, opener, crumb)
        raw = infer_period_ends(apply_splits(raw, splits))

        gaps = _gap_flags([e["date"] for e in raw])
        events, prev_react = [], None
        for e, gap in zip(raw, gaps):
            m = measure_event(e, dates, closes, prev_react, mkt, gap)
            if not m:
                continue
            events.append(m)
            prev_react = m.get("reactIndex")

        nxt = _next_earnings(ticker, opener, crumb)
        if events and (not nxt.get("date") or nxt["date"] <= events[-1]["date"]):
            proj = _project_next(events)
            if proj:
                nxt["date"], nxt["estimated"] = proj, True
        days_away = None
        if nxt.get("date"):
            try:
                d = datetime.strptime(nxt["date"], "%Y-%m-%d").date()
                days_away = (d - datetime.now(ET).date()).days
            except Exception:
                pass
        nxt["daysAway"] = days_away
        # Companies keep their reporting slot; the last print's timing is the
        # best available read on when the next one lands.
        nxt["timing"] = events[-1]["timing"] if events else None
        if events:
            # Where the stock stands going into it — the same two "before"
            # windows, measured to the latest close.
            last = len(closes) - 1
            nxt["between"] = _ret(closes, events[-1].get("reactIndex"), last)
            nxt["pre15"] = _ret(closes, last - PRE_SESSIONS, last)
            pe = infer_period_ends(events + [{"date": nxt["date"] or dates[last],
                                              "periodEnd": None}])[-1]
            nxt["periodEnd"] = pe.get("periodEnd")

        first_idx = events[0]["d0Index"] - PRE_SESSIONS if events else 0
        data = {
            "ticker": ticker,
            "ok": True,
            "asOf": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lastClose": round(closes[-1], 2),
            "lastCloseDate": dates[-1],
            "vixNow": (round(mkt["vix"][max(mkt["vix"])], 1)
                       if mkt.get("vix") else None),
            "next": nxt,
            "events": events,
            "series": {"dates": dates, "closes": [round(c, 2) for c in closes]},
            "summary": summarize(events, typical_daily_move(closes, max(0, first_idx))),
        }
        _cache[ticker] = {"t": now, "v": CACHE_SCHEMA, "data": data}
        _save_cache()
        return data
    except Exception as e:
        return {"ticker": ticker, "ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    d = earnings_performance(sys.argv[1] if len(sys.argv) > 1 else "NVDA", force=True)
    d.pop("series", None)
    print(json.dumps(d, indent=1))
