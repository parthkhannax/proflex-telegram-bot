"""Yahoo Finance chart-API client (one HTTP request per symbol, all horizons).

Why not yf.Ticker().get_info() per horizon: get_info() is ~1s/symbol and only
yields the 1-day move. The GS basket tab needs ~313 symbols, so the dashboard
uses the underlying chart endpoint directly, going through yfinance's YfData
session so the cookie/crumb handshake and rate limiting are handled for us
(a bare requests.get to this endpoint returns HTTP 429).

A single `range=2y&interval=1d` call returns every daily close, covering the
1D / 5D / 3M / 1Y windows at once. `range=1y` is NOT enough for the 1Y window:
Yahoo's 1y window starts a few sessions short of 365 calendar days, so a
365-day lookback finds no bar at or before the target date and returns None.

Lookback selection is BY DATE, never by row position. Yahoo's daily history
intermittently drops a bar entirely (observed for REM/TAN/IHI/GRID/EWY/SMH),
which silently shifts any fixed row offset onto the wrong day.

Verified 25-Aug-2026: the close of the last bar before the current session
matches info["regularMarketPreviousClose"] exactly for AAPL, SMH and MJ --
including MJ, the symbol where fast_info["previousClose"] was observed wrong.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from yfinance.data import YfData

_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{}"
_yf_data = YfData()

# calendar-day lookbacks; ~5, ~63 and ~252 trading sessions respectively
HORIZONS = {"5d": 7, "3m": 91, "1y": 365}


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def fetch_series(symbol, rng="2y"):
    """Return {'last': float, 'closes': [(date, close), ...]} or None on failure."""
    try:
        j = _yf_data.get_raw_json(
            _CHART_URL.format(symbol), params={"range": rng, "interval": "1d"}
        )
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None
        r = res[0]
        meta = r.get("meta") or {}
        ts = r.get("timestamp") or []
        closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        if not ts or not closes:
            return None

        offset = meta.get("gmtoffset") or 0
        series = [
            (datetime.fromtimestamp(t + offset, timezone.utc).date(), c)
            for t, c in zip(ts, closes)
            if c is not None
        ]
        if not series:
            return None

        last = meta.get("regularMarketPrice")
        if last is None:
            last = series[-1][1]

        # Anchor the current session on regularMarketTime, not on the last bar.
        # Yahoo sometimes has no bar at all for the latest session (observed for
        # CARZ on 25-Aug-2026), and inferring the session from the last bar then
        # shifts the day-over-day base a full session too far back.
        rmt = meta.get("regularMarketTime")
        session_date = (
            datetime.fromtimestamp(rmt + offset, timezone.utc).date()
            if rmt else series[-1][0]
        )
        return {
            "last": float(last),
            "closes": series,
            "session_date": session_date,
            "currency": meta.get("currency"),
        }
    except Exception:
        return None


def compute_returns(series):
    """Turn a fetch_series result into {'day','5d','3m','1y'} percent moves.

    The current session's own bar is excluded from every base so the day move
    is last-vs-prior-close and the longer windows are anchored on completed
    sessions.
    """
    if not series:
        return None
    last = series["last"]
    closes = series["closes"]
    session_date = series.get("session_date") or closes[-1][0]
    prior = [(d, c) for d, c in closes if d < session_date]
    if not prior:
        # No completed session before this one in the window (session_date can
        # predate every bar for a stale symbol); fall back to the last bar.
        prior = closes[:-1]
        if not prior:
            return None

    out = {"day": _pct(last, prior[-1][1])}
    for label, days in HORIZONS.items():
        target = session_date - timedelta(days=days)
        window = [c for d, c in prior if d <= target]
        out[label] = _pct(last, window[-1]) if window else None
    out["session_date"] = session_date.isoformat()
    return out


def fetch_returns(symbol):
    return compute_returns(fetch_series(symbol))


def fetch_many(symbols, max_workers=12):
    """{symbol: returns-or-None} for a list of symbols, fetched in parallel."""
    symbols = list(symbols)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(fetch_returns, symbols))
    return dict(zip(symbols, results))
