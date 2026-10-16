"""Real-time sector rotation dashboard backend.

Polls yfinance for the sector ETF universe + SPY benchmark on a background
thread, computes 1-Day and 5-session (1-Week) performance relative to SPY,
and serves the latest snapshot as JSON.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yfinance as yf
from flask import Flask, jsonify, send_from_directory

import gics_baskets
import gs_baskets
import yahoo_http
from tickers import TICKERS, FRONTIER, BENCHMARK, SECTORS

POLL_SECONDS = 90
MAX_WORKERS = 10
HOLDINGS_TTL = 120  # seconds to cache an ETF's holdings snapshot

# The GS tab needs ~300 constituent quotes per sweep, so it runs on its own,
# slower thread rather than blocking the 90s sector poll.
GS_POLL_SECONDS = 300

app = Flask(__name__, static_folder=".", static_url_path="")

_lock = threading.Lock()
_cache = {
    "updated_at": None,
    "spy_day_pct": None,
    "rows": [],
    "frontier": [],
}

_gs_lock = threading.Lock()
_gs_cache = {
    "updated_at": None,
    "disclaimer": gs_baskets.DISCLAIMER,
    "gics_disclaimer": gics_baskets.DISCLAIMER,
    "baskets": [],
    "gics": [],
}
# last-good values per ticker, so a transient fetch failure doesn't blank a row
_last_good = {}

# per-ETF holdings snapshots, computed on demand and cached briefly
_holdings_lock = threading.Lock()
_holdings_cache = {}  # etf -> {"updated_at": iso, "ts": epoch, "holdings": [...]}


def _pct(a, b):
    """% change from b to a."""
    if b is None or b == 0 or a is None:
        return None
    return (a / b - 1.0) * 100.0


def _fetch_one(symbol):
    """Return (day_pct, five_day_pct) for a symbol, or None on failure.

    day_pct is computed from t.info["regularMarketPreviousClose"], the
    authoritative previous-close field Yahoo Finance itself displays.
    NOTE: fast_info["previousClose"] was observed silently wrong for some
    symbols (e.g. MJ: 23.50 vs the true 24.05), and deriving previous close
    from t.history() by row-position was *also* wrong for other symbols
    because Yahoo's daily history intermittently drops a bar entirely (e.g.
    07-14 missing for REM/TAN/IHI/GRID/EWY/SMH), silently shifting every
    positional lookback onto the wrong day. t.info's previousClose matched
    ground truth in all cases checked, so it's used as the sole source for
    the day change even though it's a slower call (~1s/symbol — mitigated
    by fetching the universe in parallel, see _build_rows).

    five_day_pct = % change vs. the close nearest 7 calendar days ago
    (~5 trading sessions), selected by actual date rather than a fixed row
    offset so a missing bar elsewhere in the window doesn't misalign it.
    """
    try:
        t = yf.Ticker(symbol)
        info = t.get_info()
        last = info.get("regularMarketPrice")
        prev_close = info.get("regularMarketPreviousClose")
        day_pct = _pct(last, prev_close)

        hist = t.history(period="14d", interval="1d")
        five_day_pct = None
        if len(hist) >= 2 and last is not None:
            target_date = hist.index[-1] - timedelta(days=7)
            prior = hist[hist.index <= target_date]
            if len(prior) > 0:
                base = prior["Close"].iloc[-1]
                five_day_pct = _pct(last, base)

        if day_pct is None or five_day_pct is None:
            return None
        return (day_pct, five_day_pct)
    except Exception:
        return None


def _build_rows(universe):
    rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = dict(zip(universe.keys(), ex.map(_fetch_one, universe.keys())))

    for symbol, name in universe.items():
        result = results[symbol]
        stale = False
        if result is None:
            result = _last_good.get(symbol)
            stale = True
            if result is None:
                continue
        else:
            _last_good[symbol] = result

        day_pct, five_day_pct = result
        rows.append({
            "ticker": symbol,
            "name": name,
            "sector": SECTORS.get(symbol, name),
            "day_pct": round(day_pct, 2),
            "five_day_pct": round(five_day_pct, 2),
            "stale": stale,
        })
    return rows


def _poll_loop():
    while True:
        try:
            spy_result = _fetch_one(BENCHMARK)
            if spy_result is None:
                spy_result = _last_good.get(BENCHMARK, (0.0, 0.0))
            else:
                _last_good[BENCHMARK] = spy_result
            spy_day_pct, spy_5d_pct = spy_result

            raw_rows = _build_rows(TICKERS)
            raw_frontier = _build_rows(FRONTIER)

            # 3M and 1Y come from the chart endpoint (one HTTP call per symbol
            # covers both) rather than info, which only carries the day move.
            long_syms = list(TICKERS) + list(FRONTIER) + [BENCHMARK]
            longs = yahoo_http.fetch_many(long_syms, max_workers=MAX_WORKERS)
            spy_long = longs.get(BENCHMARK) or {}

            def _rel_long(sym, key):
                base = spy_long.get(key)
                mine = (longs.get(sym) or {}).get(key)
                if base is None or mine is None:
                    return None
                return round(mine - base, 2)

            def relativize(rows):
                out = []
                for r in rows:
                    out.append({
                        "ticker": r["ticker"],
                        "name": r["name"],
                        "sector": r["sector"],
                        "rel_day_pct": round(r["day_pct"] - spy_day_pct, 2),
                        "rel_5d_pct": round(r["five_day_pct"] - spy_5d_pct, 2),
                        "rel_3m_pct": _rel_long(r["ticker"], "3m"),
                        "rel_1y_pct": _rel_long(r["ticker"], "1y"),
                        "stale": r["stale"],
                    })
                return out

            with _lock:
                _cache["updated_at"] = datetime.now(timezone.utc).isoformat()
                _cache["spy_day_pct"] = round(spy_day_pct, 2)
                _cache["rows"] = relativize(raw_rows)
                _cache["frontier"] = relativize(raw_frontier)
        except Exception as e:
            print(f"[poll error] {e}")

        time.sleep(POLL_SECONDS)


def _fetch_holding_day(symbol):
    """Return day_pct for a single holding symbol, or None on failure.

    Uses the same authoritative previous-close field as _fetch_one, but only
    the 1-day read is needed here (holdings drill-down), so we skip the
    history call for speed.
    """
    try:
        info = yf.Ticker(symbol).get_info()
        last = info.get("regularMarketPrice")
        prev_close = info.get("regularMarketPreviousClose")
        return _pct(last, prev_close)
    except Exception:
        return None


def _build_holdings(etf):
    """Fetch an ETF's top holdings + each holding's 1-day move, sorted desc."""
    fd = yf.Ticker(etf).funds_data
    th = fd.top_holdings  # DataFrame indexed by Symbol, cols Name + Holding Percent
    if th is None or len(th) == 0:
        return []

    symbols = [str(s) for s in th.index]
    names = {str(s): th.loc[s, "Name"] for s in th.index}
    weights = {str(s): float(th.loc[s, "Holding Percent"]) for s in th.index}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        day = dict(zip(symbols, ex.map(_fetch_holding_day, symbols)))

    holdings = []
    for sym in symbols:
        d = day.get(sym)
        holdings.append({
            "ticker": sym,
            "name": names.get(sym, sym),
            "weight_pct": round(weights.get(sym, 0.0) * 100.0, 2),
            "day_pct": None if d is None else round(d, 2),
        })
    holdings.sort(key=lambda r: (r["day_pct"] is not None, r["day_pct"] or 0), reverse=True)
    return holdings


@app.route("/api/holdings/<etf>")
def api_holdings(etf):
    etf = etf.upper()
    if etf not in TICKERS and etf not in FRONTIER:
        return jsonify({"error": "unknown ETF", "holdings": []}), 404

    now = time.time()
    with _holdings_lock:
        cached = _holdings_cache.get(etf)
        if cached and now - cached["ts"] < HOLDINGS_TTL:
            return jsonify(cached)

    try:
        holdings = _build_holdings(etf)
    except Exception as e:
        with _holdings_lock:
            stale = _holdings_cache.get(etf)
        if stale:
            return jsonify(stale)
        return jsonify({"error": str(e), "etf": etf, "holdings": []}), 502

    name = TICKERS.get(etf) or FRONTIER.get(etf)
    payload = {
        "etf": etf,
        "etf_name": name,
        "sector": SECTORS.get(etf, name),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ts": now,
        "holdings": holdings,
    }
    with _holdings_lock:
        _holdings_cache[etf] = payload
    return jsonify(payload)


def _basket_stats(basket, quotes, spy):
    """Aggregate a basket's constituents into rel-1D / rel-5D vs SPY.

    Equal-weighted unless the basket carries explicit weights (GVIP). A dynamic
    basket has no static list, so its proxy ETF is measured directly.
    Constituents that failed to quote are excluded and counted, so a partial
    sweep is visible in the UI rather than silently skewing the average.
    """
    weights = basket.get("weights")
    if basket.get("dynamic"):
        members = [basket["proxy"]] if basket.get("proxy") else []
        weights = None
    else:
        members = basket.get("members", [])

    rows, missing = [], []
    for sym in members:
        q = quotes.get(sym)
        if not q or q.get("day") is None:
            missing.append(sym)
            continue
        rows.append({
            "ticker": sym,
            "day_pct": round(q["day"], 2),
            "five_day_pct": None if q.get("5d") is None else round(q["5d"], 2),
            "rel_day_pct": round(q["day"] - spy["day"], 2),
            "rel_5d_pct": (
                None if q.get("5d") is None or spy.get("5d") is None
                else round(q["5d"] - spy["5d"], 2)
            ),
            "weight_pct": round(weights[sym] * 100.0, 2) if weights and sym in weights else None,
        })

    def agg(key):
        vals = [(r[key], r["ticker"]) for r in rows if r[key] is not None]
        if not vals:
            return None
        if weights:
            wsum = sum(weights.get(t, 0.0) for _, t in vals)
            if wsum == 0:
                return None
            return round(sum(v * weights.get(t, 0.0) for v, t in vals) / wsum, 2)
        return round(sum(v for v, _ in vals) / len(vals), 2)

    rows.sort(key=lambda r: r["rel_day_pct"], reverse=True)
    return rows, missing, agg("day_pct"), agg("five_day_pct")


def _build_panel(defs, quotes, spy):
    out = []
    for ticker, b in defs.items():
        rows, missing, day, five = _basket_stats(b, quotes, spy)
        if day is None:
            continue
        out.append({
            "ticker": ticker,
            "name": b["name"],
            "desc": b["desc"],
            "proxy": b.get("proxy"),
            "confidence": b.get("confidence"),
            "note": b.get("note"),
            "dynamic": bool(b.get("dynamic")),
            "weighted": bool(b.get("weights")),
            "day_pct": day,
            "five_day_pct": five,
            "rel_day_pct": round(day - spy["day"], 2),
            "rel_5d_pct": (
                None if five is None or spy.get("5d") is None
                else round(five - spy["5d"], 2)
            ),
            "n_members": len(rows),
            "n_missing": len(missing),
            "missing": missing,
            "constituents": rows,
        })
    out.sort(key=lambda b: b["rel_day_pct"], reverse=True)
    return out


def _build_gs_baskets():
    """One quote sweep over the union of both panels, then aggregate each.

    The GS baskets and the GICS industry groups overlap heavily (NVDA, JPM and
    friends sit in both), so fetching the union once rather than per panel saves
    ~120 redundant requests against a rate-limited endpoint.
    """
    symbols = sorted(set(gs_baskets.all_symbols()) | set(gics_baskets.all_symbols()))
    quotes = yahoo_http.fetch_many(symbols, max_workers=MAX_WORKERS)
    spy = yahoo_http.fetch_returns(BENCHMARK)
    if not spy or spy.get("day") is None:
        raise RuntimeError("benchmark quote unavailable")

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": gs_baskets.DISCLAIMER,
        "gics_disclaimer": gics_baskets.DISCLAIMER,
        "benchmark": BENCHMARK,
        "spy_day_pct": round(spy["day"], 2),
        "spy_5d_pct": None if spy.get("5d") is None else round(spy["5d"], 2),
        "session_date": spy.get("session_date"),
        "baskets": _build_panel(gs_baskets.BASKETS, quotes, spy),
        "gics": _build_panel(gics_baskets.INDICES, quotes, spy),
    }


def _gs_poll_loop():
    while True:
        try:
            payload = _build_gs_baskets()
            with _gs_lock:
                _gs_cache.clear()
                _gs_cache.update(payload)
        except Exception as e:
            print(f"[gs poll error] {e}")
        time.sleep(GS_POLL_SECONDS)


@app.route("/api/gs-baskets")
def api_gs_baskets():
    """Basket-level summary (constituent detail stripped to keep it small)."""
    with _gs_lock:
        payload = dict(_gs_cache)
        for panel in ("baskets", "gics"):
            payload[panel] = [
                {k: v for k, v in b.items() if k != "constituents"}
                for b in payload.get(panel, [])
            ]
        return jsonify(payload)


@app.route("/api/gs-basket/<ticker>")
def api_gs_basket(ticker):
    ticker = ticker.upper()
    with _gs_lock:
        for panel in ("baskets", "gics"):
            for b in _gs_cache.get(panel, []):
                if b["ticker"] == ticker:
                    return jsonify(b)
    return jsonify({"error": "unknown basket", "constituents": []}), 404


@app.route("/api/sectors")
def api_sectors():
    with _lock:
        return jsonify(_cache)


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


if __name__ == "__main__":
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    threading.Thread(target=_gs_poll_loop, daemon=True).start()
    print(f"Polling every {POLL_SECONDS}s. Waiting for first snapshot...")
    while _cache["updated_at"] is None:
        time.sleep(1)
    print("First snapshot ready. Serving on http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
