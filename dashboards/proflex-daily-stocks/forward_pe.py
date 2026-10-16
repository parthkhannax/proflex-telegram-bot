#!/usr/bin/env python3
"""
Forward P/E on calendar-year-normalised earnings estimates.

Every company reports on its own fiscal calendar: NVDA's "FY2027" ends 31 Jan 2027,
AAPL's "FY2026" ends 30 Sep 2026. Comparing those headline numbers side by side
compares different spans of time. This module normalises them onto true calendar
years so a CY2027 multiple means the same thing for every name on the board.

Method — a quarterly EPS ledger, not an annual blend:

  1. Take the last four *actual* quarterly EPS prints (earningsHistory).
  2. Add the current and next quarter *estimates* (earningsTrend 0q / +1q).
  3. Take the FY1 / FY2 annual estimates (earningsTrend 0y / +1y) and spread each
     fiscal year's residual — the annual total minus the quarters already known —
     evenly across that year's still-unknown quarters.
  4. Give every quarter a real start/end date, then allocate its EPS to calendar
     years by day overlap.

Blending two annual numbers with a fixed weight is the usual shortcut; it breaks
whenever a fiscal quarter straddles 31 December (NVDA's Jan-ending quarter sits
two-thirds in the prior calendar year). Day-overlap allocation is exact for any
fiscal calendar, and the ledger is auditable quarter by quarter.

Coverage: FY2 ends before the far calendar year closes for non-December filers, so
that year is only partly covered by estimates. The covered portion is grossed up to
a full year and the row is flagged with its coverage %, never silently completed.
"""

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

# Reuse the cookie/crumb session and ticker universe the dashboard already owns.
from server import _get_yahoo_crumb, COMPANY, CATEGORY, PRIORITY, WATCHLIST

MODULES = "earningsHistory,earningsTrend,defaultKeyStatistics,price"


# ── date helpers ────────────────────────────────────────────────────────────────

def _parse(d):
    """'2026-01-31' → date, tolerating None/garbage."""
    try:
        y, m, dd = str(d)[:10].split("-")
        return date(int(y), int(m), int(dd))
    except Exception:
        return None


def _minus_months(d, n):
    """Step back n months, clamping the day to the target month's length."""
    m = d.month - n
    y = d.year
    while m <= 0:
        m += 12
        y -= 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _snap(d, known_dates, tol=5):
    """Snap a generated quarter-end onto a reported one within `tol` days.

    Fiscal quarter ends drift by a day or two (AAPL reports 31 Dec and 31 Mar, but
    stepping back three months from its 30 Sep year end generates the 30ths). Without
    snapping, the derived quarter and the actual print are treated as two separate
    quarters and the calendar year is double-counted.
    """
    best, best_gap = None, tol + 1
    for k in known_dates:
        gap = abs((k - d).days)
        if gap < best_gap:
            best, best_gap = k, gap
    return best if best is not None else d


def _overlap_days(a_start, a_end, b_start, b_end):
    """Inclusive-day overlap between two date spans."""
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0, (hi - lo).days + 1)


# ── fetch ───────────────────────────────────────────────────────────────────────

def _fetch_summary(ticker):
    opener, crumb = _get_yahoo_crumb()
    if not (opener and crumb):
        return None
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
           + urllib.parse.quote(ticker)
           + "?modules=" + MODULES
           + "&crumb=" + urllib.parse.quote(crumb))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = json.loads(opener.open(req, timeout=12).read().decode())
        return data["quoteSummary"]["result"][0]
    except Exception:
        return None


def _raw(node, *path):
    """Dig out Yahoo's {'raw': x, 'fmt': ...} leaves, returning None on any miss."""
    cur = node
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, dict):
        cur = cur.get("raw")
    return cur if isinstance(cur, (int, float)) else None


# ── the ledger ──────────────────────────────────────────────────────────────────

def build_quarter_ledger(summary):
    """Return (quarters, fy_end_month, meta).

    quarters: list of {'start','end','eps','kind'} covering ~3 years, oldest first.
    kind is 'actual', 'estimate' (an explicit analyst quarter) or 'derived'
    (an annual residual spread across that fiscal year's unknown quarters).
    """
    trend = {t.get("period"): t
             for t in (summary.get("earningsTrend", {}).get("trend") or [])}

    fy1, fy2 = trend.get("0y"), trend.get("+1y")
    fy1_end = _parse(fy1.get("endDate")) if fy1 else None
    fy2_end = _parse(fy2.get("endDate")) if fy2 else None
    fy1_eps = _raw(fy1, "earningsEstimate", "avg") if fy1 else None
    fy2_eps = _raw(fy2, "earningsEstimate", "avg") if fy2 else None
    if not fy1_end or fy1_eps is None:
        return [], None, {}

    # Known quarter-end → EPS. Actual prints win over estimates for the same date.
    known, kinds = {}, {}
    for h in (summary.get("earningsHistory", {}).get("history") or []):
        qe = _parse((h.get("quarter") or {}).get("fmt"))
        eps = _raw(h, "epsActual")
        if qe and eps is not None:
            known[qe] = eps
            kinds[qe] = "actual"
    for period in ("0q", "+1q"):
        t = trend.get(period)
        if not t:
            continue
        qe = _parse(t.get("endDate"))
        eps = _raw(t, "earningsEstimate", "avg")
        if qe and eps is not None and qe not in known:
            known[qe] = eps
            kinds[qe] = "estimate"

    basis_drift = None

    def fy_quarter_ends(fy_end):
        """The four quarter-end dates of the fiscal year ending fy_end, each snapped
        onto a reported quarter end when one sits within a few days."""
        return [_snap(_minus_months(fy_end, 3 * i), known.keys())
                for i in (3, 2, 1, 0)]

    # Reconcile every fiscal year to its annual analyst consensus.
    #
    # Two sources disagree by construction: the sum of the quarterly estimates is
    # not the annual estimate (different analyst panels, and annual numbers are
    # revised on a different cadence). The annual figure is the one the forward-P/E
    # convention is built on, so the fiscal year must total to it — but reported
    # actuals are facts and are never touched. The difference is therefore spread
    # additively across that year's non-actual quarters, which holds the annual
    # total exactly while preserving the quarterly shape analysts published. An
    # additive spread is used rather than proportional scaling because EPS crosses
    # zero often enough (loss-makers, turnarounds) to make scaling explode.
    for fy_end, fy_eps in ((fy1_end, fy1_eps), (fy2_end, fy2_eps)):
        if not fy_end or fy_eps is None:
            continue
        ends = fy_quarter_ends(fy_end)
        # How far the quarters Yahoo publishes sit from Yahoo's own annual number,
        # measured before any reconciliation. A large gap means the two feeds are
        # on different accounting bases (GAAP actuals against non-GAAP consensus,
        # typically), which no amount of arithmetic can repair — so it is surfaced
        # on the row instead of being quietly averaged away.
        if fy_end == fy1_end:
            direct = [known[q] for q in ends if q in known]
            if len(direct) >= 3:
                basis_drift = abs(sum(direct) - fy_eps) / max(abs(fy_eps), 0.50)
        missing = [q for q in ends if q not in known]
        if missing:
            # Preferred path: the unknown quarters absorb the whole residual, so
            # every published quarterly estimate keeps the value analysts gave it.
            residual = fy_eps - sum(known[q] for q in ends if q in known)
            per = residual / len(missing)
            for q in missing:
                known[q] = per
                kinds[q] = "derived"
            continue
        # Fallback: all four quarters are already known, so the only way to hold
        # the annual total is to adjust the estimated ones. Actuals stay fixed.
        adjustable = [q for q in ends if kinds.get(q) != "actual"]
        if not adjustable:
            continue                           # fiscal year fully reported
        per = (fy_eps - sum(known[q] for q in ends)) / len(adjustable)
        for q in adjustable:
            known[q] += per

    quarters = []
    for qe in sorted(known):
        # A quarter covers the three months ending on its end date.
        qs = _minus_months(qe, 3) + timedelta(days=1)
        quarters.append({"start": qs, "end": qe,
                         "eps": known[qe], "kind": kinds[qe]})

    meta = {"fy1End": fy1_end.isoformat(), "fy1Eps": fy1_eps,
            "fy2End": fy2_end.isoformat() if fy2_end else None, "fy2Eps": fy2_eps,
            "fy1Analysts": _raw(fy1, "earningsEstimate", "numberOfAnalysts"),
            "lastCovered": max(known).isoformat() if known else None,
            "basisDrift": round(basis_drift, 3) if basis_drift is not None else None}
    return quarters, fy1_end.month, meta


def calendar_year_eps(quarters, year):
    """Day-overlap-weighted EPS for a calendar year.

    Returns (eps, coverage) where coverage is the fraction of the year spanned by
    the ledger. eps is already grossed up to a full year when coverage < 1.
    """
    cy_start, cy_end = date(year, 1, 1), date(year, 12, 31)
    total_days = (cy_end - cy_start).days + 1
    eps_sum = 0.0
    covered = 0
    for q in quarters:
        q_days = (q["end"] - q["start"]).days + 1
        ov = _overlap_days(q["start"], q["end"], cy_start, cy_end)
        if ov <= 0:
            continue
        eps_sum += q["eps"] * (ov / q_days)
        covered += ov
    if covered == 0:
        return None, 0.0
    coverage = covered / total_days
    # Gross a partial span up to a full year at the covered run-rate. Quarter spans
    # can total a day or two over the calendar year; never deflate for that.
    return eps_sum / min(coverage, 1.0), coverage


def ntm_eps(quarters, today=None):
    """Rolling next-twelve-months EPS — the assumption-free forward measure.

    Needs no extrapolation: the NTM window always falls inside FY1+FY2.
    """
    today = today or date.today()
    win_start, win_end = today, today + timedelta(days=364)
    total = (win_end - win_start).days + 1
    eps_sum, covered = 0.0, 0
    for q in quarters:
        q_days = (q["end"] - q["start"]).days + 1
        ov = _overlap_days(q["start"], q["end"], win_start, win_end)
        if ov <= 0:
            continue
        eps_sum += q["eps"] * (ov / q_days)
        covered += ov
    if covered == 0:
        return None, 0.0
    coverage = covered / total
    return eps_sum / min(coverage, 1.0), coverage


# ── per-ticker assembly ─────────────────────────────────────────────────────────

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _pe(price, eps):
    """P/E, or None when EPS is negative/zero — a multiple on a loss is meaningless."""
    if price is None or eps is None or eps <= 0:
        return None
    return round(price / eps, 1)


def forward_pe(ticker, today=None):
    today = today or date.today()
    base = {"ticker": ticker,
            "company": COMPANY.get(ticker, ticker),
            "category": CATEGORY.get(ticker, "Other"),
            "priority": ticker.upper() in PRIORITY,
            "ok": False}

    summary = _fetch_summary(ticker)
    if not summary:
        return {**base, "error": "no data"}

    price = _raw(summary, "price", "regularMarketPrice")
    yahoo_fwd = _raw(summary, "defaultKeyStatistics", "forwardPE")
    trailing_eps = _raw(summary, "defaultKeyStatistics", "trailingEps")

    quarters, fy_month, meta = build_quarter_ledger(summary)
    if not quarters:
        # ETFs and uncovered names have no analyst estimates at all.
        return {**base, "price": price, "error": "no estimates",
                "trailingPE": _pe(price, trailing_eps)}

    cy1, cy2 = today.year, today.year + 1
    eps1, cov1 = calendar_year_eps(quarters, cy1)
    eps2, cov2 = calendar_year_eps(quarters, cy2)
    ntm, ntm_cov = ntm_eps(quarters, today)

    return {
        **base,
        "ok": True,
        "price": round(price, 2) if price else None,
        "fyEndMonth": MONTHS[fy_month] if fy_month else None,
        "isCalendarFY": fy_month == 12,
        "cy1Year": cy1, "cy2Year": cy2,
        "cy1Eps": round(eps1, 2) if eps1 is not None else None,
        "cy2Eps": round(eps2, 2) if eps2 is not None else None,
        "cy1Pe": _pe(price, eps1),
        "cy2Pe": _pe(price, eps2),
        "cy1Coverage": round(cov1, 3),
        "cy2Coverage": round(cov2, 3),
        "cy1Partial": cov1 < 0.995,
        "cy2Partial": cov2 < 0.995,
        "ntmEps": round(ntm, 2) if ntm is not None else None,
        "ntmPe": _pe(price, ntm),
        "ntmPartial": ntm_cov < 0.995,
        # Growth on the normalised calendar numbers — the whole point of the exercise.
        "cyGrowth": (round((eps2 / eps1 - 1) * 100, 1)
                     if eps1 and eps2 and eps1 > 0 else None),
        "trailingEps": trailing_eps,
        "trailingPE": _pe(price, trailing_eps),
        "yahooForwardPE": (round(yahoo_fwd, 1)
                           if yahoo_fwd and yahoo_fwd > 0 else None),
        "analysts": meta.get("fy1Analysts"),
        # True when Yahoo's quarterly and annual feeds disagree materially.
        "mixedBasis": (meta.get("basisDrift") or 0) > 0.25,
        "basisDrift": meta.get("basisDrift"),
        "fy1End": meta.get("fy1End"), "fy1Eps": meta.get("fy1Eps"),
        "fy2End": meta.get("fy2End"), "fy2Eps": meta.get("fy2Eps"),
        "ledger": [{"end": q["end"].isoformat(), "eps": round(q["eps"], 3),
                    "kind": q["kind"]} for q in quarters],
    }


def forward_pe_all(tickers=None):
    tickers = tickers or [t for _, t, _ in WATCHLIST]
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(forward_pe, tickers))
    return {"asOf": date.today().isoformat(), "rows": rows}


if __name__ == "__main__":
    import sys
    for t in (sys.argv[1:] or ["NVDA", "AAPL"]):
        print(json.dumps(forward_pe(t.upper()), indent=2))
