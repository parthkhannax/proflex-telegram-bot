#!/usr/bin/env python3
"""
Proflex Daily — Stock Chartplay + Movers/Drags dashboard.
Live data via Yahoo Finance (same backend as yfinance).
Mirrors the Apps Script logic: +/-2% thresholds, priority watchlist stars,
Telegram report. Runs a local dashboard on http://localhost:8000
"""

import json
import os
import smtplib
import ssl
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8000
HERE = Path(__file__).parent

# ── Email (Gmail SMTP) ──
EMAIL_TO = "parthbusinessofficialid@gmail.com"
EMAIL_FROM = "parthbusinessofficialid@gmail.com"
# App password read from ~/.proflex/email.txt (or PROFLEX_EMAIL_PASSWORD env).
# Google App Password: https://myaccount.google.com/apppasswords
PW_FILE = Path.home() / ".proflex" / "email.txt"

LOGO_URL = ("https://48102387.fs1.hubspotusercontent-na1.net/hubfs/48102387/"
            "Proflex_Square200xLogo_TransparentBlack.png")


def get_app_password():
    pw = os.environ.get("PROFLEX_EMAIL_PASSWORD")
    if pw:
        return pw.strip().replace(" ", "")
    if PW_FILE.exists():
        return PW_FILE.read_text().strip().replace(" ", "")
    return None

# ── Thresholds (from CONFIG) ──
MOVER_THRESHOLD = 2.0
DRAGGER_THRESHOLD = -2.0

# ── Priority watchlist (⭐ always highlighted) ──
PRIORITY = {"META", "GOOG", "NVDA", "TSLA", "AMD", "AVGO", "MSFT",
            "AMZN", "ORCL", "CDNS", "IOT"}

# ── Full watchlist: (Company, Ticker, Category) ──
WATCHLIST = [
    ("Strategy Inc Class A", "MSTR", "Crypto & Miners"),
    ("CoreWeave Inc", "CRWV", "AI & Semis"),
    ("Hims & Hers Health Inc", "HIMS", "Healthcare"),
    ("Astera Labs Inc", "ALAB", "AI & Semis"),
    ("Upstart Holdings Inc", "UPST", "Fintech"),
    ("Novo Nordisk A/S", "NVO", "Healthcare"),
    ("CAVA Group Inc", "CAVA", "Consumer"),
    ("Affirm Holdings Inc", "AFRM", "Fintech"),
    ("iShares Bitcoin Trust ETF", "IBIT", "Crypto & Miners"),
    ("D-Wave Quantum Inc", "QBTS", "Quantum & Frontier"),
    ("Coinbase Global Inc", "COIN", "Crypto & Miners"),
    ("Zscaler Inc", "ZS", "Software & Cloud"),
    ("Trade Desk Inc", "TTD", "Software & Cloud"),
    ("Joby Aviation Inc", "JOBY", "Quantum & Frontier"),
    ("Equinor ASA", "EQNR", "Energy"),
    ("Uber Technologies Inc", "UBER", "Consumer"),
    ("Chord Energy Corp", "CHRD", "Energy"),
    ("Nike Inc", "NKE", "Consumer"),
    ("Strategy Perpetual Strike Prf", "STRK", "Crypto & Miners"),
    ("UnitedHealth Group Inc", "UNH", "Healthcare"),
    ("Global X Uranium ETF", "URA", "Energy"),
    ("PayPal Holdings Inc", "PYPL", "Fintech"),
    ("Palantir Technologies Inc", "PLTR", "Software & Cloud"),
    ("Palo Alto Networks Inc", "PANW", "Software & Cloud"),
    ("Boeing Co", "BA", "Industrials"),
    ("Tesla Inc", "TSLA", "Mega Cap Tech"),
    ("Robinhood Markets Inc", "HOOD", "Fintech"),
    ("iShares 20+ Year Treasury Bond", "TLT", "Bonds & Rates"),
    ("Strategy Var Rate Perp Stretch", "STRC", "Crypto & Miners"),
    ("Starbucks Corp", "SBUX", "Consumer"),
    ("Applovin Corp", "APP", "Software & Cloud"),
    ("Target Corp", "TGT", "Consumer"),
    ("NVIDIA Corp", "NVDA", "Mega Cap Tech"),
    ("Amazon.com Inc", "AMZN", "Mega Cap Tech"),
    ("Walmart Inc", "WMT", "Consumer"),
    ("Annaly Capital Management", "NLY", "Bonds & Rates"),
    ("Apple Inc", "AAPL", "Mega Cap Tech"),
    ("VanEck Gold Miners ETF", "GDX", "Metals & Mining"),
    ("Freeport-McMoRan Inc", "FCX", "Metals & Mining"),
    ("Marvell Technology Inc", "MRVL", "AI & Semis"),
    ("Pan American Silver Corp", "PAAS", "Metals & Mining"),
    ("Vale SA", "VALE", "Metals & Mining"),
    ("Alphabet Inc Class C", "GOOG", "Mega Cap Tech"),
    ("Alphabet Inc Class A", "GOOGL", "Mega Cap Tech"),
    ("iShares Silver Trust", "SLV", "Metals & Mining"),
    ("Advanced Micro Devices", "AMD", "AI & Semis"),
    ("Madrigal Pharmaceuticals", "MDGL", "Healthcare"),
    ("Seabridge Gold Inc", "SA", "Metals & Mining"),
    ("Eli Lilly And Co", "LLY", "Healthcare"),
    ("Micron Technology Inc", "MU", "AI & Semis"),
    ("Meta Platforms Inc", "META", "Mega Cap Tech"),
    ("Oracle Corp", "ORCL", "Software & Cloud"),
    ("Microsoft Corp", "MSFT", "Mega Cap Tech"),
    ("Cadence Design Systems", "CDNS", "AI & Semis"),
    ("Samsara Inc", "IOT", "Software & Cloud"),
    ("Broadcom Inc", "AVGO", "AI & Semis"),
    ("Salesforce Inc", "CRM", "Software & Cloud"),
    ("ServiceNow Inc", "NOW", "Software & Cloud"),
    ("Rivian Automotive Inc", "RIVN", "Consumer"),
    ("Datadog Inc", "DDOG", "Software & Cloud"),
    ("Snowflake Inc", "SNOW", "Software & Cloud"),
    ("Intel Corp", "INTC", "AI & Semis"),
    ("Zoom Communications Inc", "ZM", "Software & Cloud"),
    ("IREN Ltd", "IREN", "Crypto & Miners"),
    ("Terawulf Inc", "WULF", "Crypto & Miners"),
    ("Oklo Inc", "OKLO", "Energy"),
    ("AST SpaceMobile Inc", "ASTS", "Quantum & Frontier"),
    # ── AI infrastructure complex ──
    ("ASML Holding NV", "ASML", "Semi Equipment"),
    ("KLA Corp", "KLAC", "Semi Equipment"),
    ("Lam Research Corp", "LRCX", "Semi Equipment"),
    ("Applied Materials Inc", "AMAT", "Semi Equipment"),
    ("Nebius Group NV", "NBIS", "Neocloud & AI Compute"),
    ("Core Scientific Inc", "CORZ", "Neocloud & AI Compute"),
    ("Applied Digital Corp", "APLD", "Neocloud & AI Compute"),
    ("Lumentum Holdings Inc", "LITE", "Networking & Optics"),
    ("Arista Networks Inc", "ANET", "Networking & Optics"),
    ("Coherent Corp", "COHR", "Networking & Optics"),
    ("Ciena Corp", "CIEN", "Networking & Optics"),
    ("Dell Technologies Inc", "DELL", "Servers & Storage"),
    ("NetApp Inc", "NTAP", "Servers & Storage"),
    ("Super Micro Computer Inc", "SMCI", "Servers & Storage"),
    ("Hewlett Packard Enterprise", "HPE", "Servers & Storage"),
    ("Global X Data Center & Digital Infra ETF", "DTCR", "Data Centre Infra"),
    ("Digital Realty Trust Inc", "DLR", "Data Centre Infra"),
    ("Equinix Inc", "EQIX", "Data Centre Infra"),
    ("Constellation Energy Corp", "CEG", "Power & Cooling"),
    ("Quanta Services Inc", "PWR", "Power & Cooling"),
    ("Vertiv Holdings Co", "VRT", "Power & Cooling"),
    ("Vistra Corp", "VST", "Power & Cooling"),
    ("Eaton Corp plc", "ETN", "Power & Cooling"),
    ("iShares Expanded Tech-Software ETF", "IGV", "Software & Cloud"),
]

COMPANY = {t: c for c, t, _ in WATCHLIST}
CATEGORY = {t: cat for _, t, cat in WATCHLIST}

# ── Newsletter model portfolios: (Company, Ticker) ──
# Order mirrors the published tracker sheets.
GROWTH_GAZETTE = [
    ("NVIDIA Corp", "NVDA"),
    ("Tesla Inc", "TSLA"),
    ("Meta Platforms Inc", "META"),
    ("Apple Inc", "AAPL"),
    ("Pan American Silver Corp", "PAAS"),
    ("Hims & Hers Health Inc", "HIMS"),
    ("Coinbase Global Inc", "COIN"),
    ("iShares Bitcoin Trust ETF", "IBIT"),
    ("Strategy Inc", "MSTR"),
    ("Oracle Corp", "ORCL"),
    ("Microsoft Corp", "MSFT"),
    ("iShares Silver Trust", "SLV"),
    ("Salesforce Inc", "CRM"),
    ("Broadcom Inc", "AVGO"),
    ("Taiwan Semiconductor Mfg Co", "TSM"),
    ("Alphabet Inc Class C", "GOOG"),
    ("Alphabet Inc Class A", "GOOGL"),
    ("IREN Ltd", "IREN"),
    ("AST SpaceMobile Inc", "ASTS"),
    ("Micron Technology Inc", "MU"),
    ("VanEck Gold Miners ETF", "GDX"),
]

INCOME_INSIDER = [
    ("iShares 20+ Year Treasury Bond", "TLT"),
    ("Annaly Capital Management", "NLY"),
    ("Strategy Perpetual Strike Prf", "STRK"),
    ("Global X Uranium ETF", "URA"),
    ("Strategy Var Rate Perp Stretch", "STRC"),
    ("Target Corp", "TGT"),
    ("Abbott Laboratories", "ABT"),
    ("Starbucks Corp", "SBUX"),
    ("Nike Inc", "NKE"),
    ("Apple Inc", "AAPL"),
]

# Fill in company names for any newsletter-only tickers.
for _c, _t in GROWTH_GAZETTE + INCOME_INSIDER:
    COMPANY.setdefault(_t, _c)

GG_TICKERS = [t for _, t in GROWTH_GAZETTE]
II_TICKERS = [t for _, t in INCOME_INSIDER]

# Every ticker the dashboard is allowed to look up (watchlist + newsletters).
KNOWN_TICKERS = {t for _, t, _ in WATCHLIST} | set(GG_TICKERS) | set(II_TICKERS)

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── Earnings-date cache (Yahoo quoteSummary needs a session crumb; dates
# change at most once a quarter, so cache aggressively to avoid hammering
# the API). Cache file survives server restarts; entries expire after 12h. ──
EARNINGS_CACHE_FILE = HERE / ".earnings_cache.json"
EARNINGS_TTL_SECONDS = 12 * 60 * 60
_earnings_cache = {}
_yahoo_cookie_jar = None
_yahoo_crumb = None


def _load_earnings_cache():
    global _earnings_cache
    if EARNINGS_CACHE_FILE.exists():
        try:
            _earnings_cache = json.loads(EARNINGS_CACHE_FILE.read_text())
        except Exception:
            _earnings_cache = {}


def _save_earnings_cache():
    try:
        EARNINGS_CACHE_FILE.write_text(json.dumps(_earnings_cache))
    except Exception:
        pass


def _get_yahoo_crumb():
    """Fetch (and cache in-process) the cookie + crumb Yahoo requires for
    quoteSummary calls. Returns (opener, crumb) or (None, None) on failure."""
    global _yahoo_cookie_jar, _yahoo_crumb
    if _yahoo_cookie_jar is not None and _yahoo_crumb:
        return _yahoo_cookie_jar, _yahoo_crumb
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
            pass  # sets the session cookie even when the response itself 404s
        crumb = opener.open(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10
        ).read().decode()
        if not crumb:
            return None, None
        _yahoo_cookie_jar, _yahoo_crumb = opener, crumb
        return opener, crumb
    except Exception:
        return None, None


def fetch_earnings_date(ticker):
    """Next earnings date for ticker, cached ~12h. Returns fmt string or None."""
    import time
    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and now - cached.get("t", 0) < EARNINGS_TTL_SECONDS:
        return cached.get("date")

    date = None
    opener, crumb = _get_yahoo_crumb()
    if opener and crumb:
        try:
            url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   + urllib.parse.quote(ticker)
                   + "?modules=calendarEvents&crumb=" + urllib.parse.quote(crumb))
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(opener.open(req, timeout=10).read().decode())
            earnings = data["quoteSummary"]["result"][0]["calendarEvents"]["earnings"]
            dates = earnings.get("earningsDate") or []
            if dates:
                date = dates[0].get("fmt")
        except Exception:
            date = None

    _earnings_cache[ticker] = {"date": date, "t": now}
    _save_earnings_cache()
    return date


def fetch_all_earnings(tickers):
    _load_earnings_cache() if not _earnings_cache else None
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_earnings_date, tickers))
    return dict(zip(tickers, results))


def fetch_quote(ticker):
    """Fetch one quote + sparkline + YTD returns from Yahoo Finance chart API.

    Range is 2y so the series reaches back into the prior calendar year, which
    lets us anchor Year-to-Date returns to the last close of that year. Total
    (dividend-inclusive) returns use Yahoo's adjusted close, which reinvests
    dividends; the price-only return uses the raw close.
    """
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(ticker) + "?interval=1d&range=2y&events=div")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12, context=_ctx) as r:
            data = json.loads(r.read().decode())
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice") or 0

        # Raw (timestamp-aligned) series — kept aligned for YTD anchoring.
        ts = result.get("timestamp") or []
        raw_close = result["indicators"]["quote"][0].get("close") or []
        try:
            raw_adj = result["indicators"]["adjclose"][0].get("adjclose") or []
        except Exception:
            raw_adj = raw_close

        # Compact non-None close series (drives sparkline + accurate prior close;
        # meta.previousClose reflects the range start, so we derive the true
        # prior-session close from the series instead).
        closes = [c for c in raw_close if c is not None]

        if len(closes) >= 2:
            prev = closes[-2]
            if not price:
                price = closes[-1]
        else:
            prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0

        # Sparkline: last ~1 year (252 sessions), downsampled to ~60 points
        spark = []
        if closes:
            recent = closes[-252:]
            step = max(1, len(recent) // 60)
            spark = [round(recent[i], 2) for i in range(0, len(recent), step)]

        # 50-day % change
        pct50 = None
        if len(closes) > 50 and closes[-51]:
            pct50 = round((closes[-1] - closes[-51]) / closes[-51] * 100, 2)

        # YTD returns — anchor to the last close of the previous calendar year.
        ytd = ytd_total = None
        cur_year = datetime.now(timezone.utc).year
        base_i = None
        for i, t in enumerate(ts):
            if t and datetime.fromtimestamp(t, timezone.utc).year < cur_year \
                    and i < len(raw_close) and raw_close[i] is not None:
                base_i = i
        if base_i is not None:
            base_c = raw_close[base_i]
            last_c = closes[-1] if closes else None
            if base_c and last_c:
                ytd = round((last_c - base_c) / base_c * 100, 2)
            # Dividend-inclusive total return via adjusted close.
            base_a = raw_adj[base_i] if base_i < len(raw_adj) else None
            adj_valid = [a for a in raw_adj if a is not None]
            last_a = adj_valid[-1] if adj_valid else None
            if base_a and last_a:
                ytd_total = round((last_a - base_a) / base_a * 100, 2)

        return {
            "ticker": ticker,
            "company": COMPANY.get(ticker, ticker),
            "category": CATEGORY.get(ticker, "Other"),
            "priority": ticker.upper() in PRIORITY,
            "price": round(price, 2),
            "change": round(change, 2),
            "changePct": round(change_pct, 2),
            "pct50": pct50,
            "ytd": ytd,
            "ytdTotal": ytd_total,
            "dayHigh": round(meta.get("regularMarketDayHigh") or price, 2),
            "dayLow": round(meta.get("regularMarketDayLow") or price, 2),
            "week52High": round(meta.get("fiftyTwoWeekHigh") or price, 2),
            "week52Low": round(meta.get("fiftyTwoWeekLow") or price, 2),
            "volume": meta.get("regularMarketVolume") or 0,
            "marketState": meta.get("marketState") or "REGULAR",
            "spark": spark,
            "ok": True,
        }
    except Exception as e:
        return {
            "ticker": ticker, "company": COMPANY.get(ticker, ticker),
            "category": CATEGORY.get(ticker, "Other"),
            "priority": ticker.upper() in PRIORITY,
            "ok": False, "error": str(e),
            "changePct": None, "price": None, "spark": [],
        }


def fetch_live_snapshot(tickers):
    """Authoritative price / previous-close / market-state for many tickers.

    The chart API's daily series is the wrong source for the prior close: Yahoo
    intermittently serves an all-null bar for a completed session (it did for
    2026-08-17), and dropping nulls silently makes the "previous" close two
    sessions old, so every change % becomes a 2-day return and Movers/Drags
    fill up with stocks that never moved. The crumbed v7 quote endpoint carries
    regularMarketPreviousClose directly, in one batched call. Returns
    {ticker: quote-dict}; empty on failure, in which case callers keep the
    chart-derived numbers.
    """
    opener, crumb = _get_yahoo_crumb()
    if not (opener and crumb):
        return {}
    out = {}
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i + 50]
        try:
            url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                   + urllib.parse.quote(",".join(batch))
                   + "&crumb=" + urllib.parse.quote(crumb))
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(opener.open(req, timeout=12).read().decode())
            for q in data.get("quoteResponse", {}).get("result", []):
                if q.get("symbol"):
                    out[q["symbol"]] = q
        except Exception:
            continue
    return out


def apply_live_snapshot(results):
    """Overwrite chart-derived price/change fields with the v7 quote snapshot."""
    snap = fetch_live_snapshot([r["ticker"] for r in results])
    if not snap:
        return results
    for r in results:
        q = snap.get(r["ticker"])
        if not q:
            continue
        price = q.get("regularMarketPrice")
        prev = q.get("regularMarketPreviousClose")
        if price is None or not prev:
            continue
        r["price"] = round(price, 2)
        r["change"] = round(price - prev, 2)
        r["changePct"] = round((price - prev) / prev * 100, 2)
        r["prevClose"] = round(prev, 2)
        r["ok"] = True
        for key, field in (("dayHigh", "regularMarketDayHigh"),
                           ("dayLow", "regularMarketDayLow"),
                           ("week52High", "fiftyTwoWeekHigh"),
                           ("week52Low", "fiftyTwoWeekLow")):
            if q.get(field) is not None:
                r[key] = round(q[field], 2)
        if q.get("regularMarketVolume") is not None:
            r["volume"] = q["regularMarketVolume"]
        if q.get("marketState"):
            r["marketState"] = q["marketState"]
    return results


def fetch_all():
    tickers = [t for _, t, _ in WATCHLIST]
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(fetch_quote, tickers))
    apply_live_snapshot(results)
    earnings = fetch_all_earnings(tickers)
    for r in results:
        r["nextEarnings"] = earnings.get(r["ticker"])
    return results


def fetch_newsletters():
    """Fetch both newsletter model portfolios, preserving sheet order."""
    tickers = list(dict.fromkeys(GG_TICKERS + II_TICKERS))
    with ThreadPoolExecutor(max_workers=16) as ex:
        quotes = {q["ticker"]: q for q in ex.map(fetch_quote, tickers)}
    apply_live_snapshot(list(quotes.values()))
    return {
        "growthGazette": [quotes[t] for t in GG_TICKERS],
        "incomeInsider": [quotes[t] for t in II_TICKERS],
    }


def split_movers_drags(stocks):
    valid = [s for s in stocks if s.get("ok") and s.get("changePct") is not None]
    movers = sorted([s for s in valid if s["changePct"] >= MOVER_THRESHOLD],
                    key=lambda s: -s["changePct"])
    drags = sorted([s for s in valid if s["changePct"] <= DRAGGER_THRESHOLD],
                   key=lambda s: s["changePct"])
    return movers, drags


# ── Proflex brand tokens (from proflex-sectors infographic skill) ──
GOLD = "#a68a3e"
GOLD_B = "#d4bc6a"
GREEN = "#16a34a"
RED = "#dc2626"
DARK = "#141c25"
NAVY = "#1a2f4a"
MUTED = "#5a6672"


def _email_rows(entries, up):
    if not entries:
        return ('<tr><td colspan="3" style="padding:20px;text-align:center;'
                f'font-family:Inter,Arial,sans-serif;font-size:13px;color:{MUTED};'
                'font-style:italic;">No stocks crossed the threshold today.</td></tr>')
    pill_bg = "#e7f5ec" if up else "#fbe9e9"
    pill_tx = GREEN if up else RED
    arrow = "&#9650;" if up else "&#9660;"
    out = ""
    for i, s in enumerate(entries):
        prio = s["priority"]
        row_bg = "#faf6ea" if prio else ("#ffffff" if i % 2 == 0 else "#faf9f5")
        lb = f"border-left:3px solid {GOLD};" if prio else "border-left:3px solid transparent;"
        star = f'<span style="color:{GOLD};">&#9733;</span> ' if prio else ""
        pct = s["changePct"]
        disp = ("+" if pct >= 0 else "") + f"{pct:.2f}%"
        out += (
            f'<tr style="background:{row_bg};{lb}">'
            f'<td style="padding:12px 14px;font-family:Inter,Arial,sans-serif;'
            f'font-size:14px;font-weight:800;color:{DARK};border-bottom:1px solid #f0efe9;">'
            f'{star}{s["ticker"]}</td>'
            f'<td style="padding:12px 14px;font-family:Inter,Arial,sans-serif;'
            f'font-size:12.5px;color:{MUTED};border-bottom:1px solid #f0efe9;">{s["company"]}</td>'
            f'<td align="right" style="padding:12px 14px;border-bottom:1px solid #f0efe9;">'
            f'<span style="display:inline-block;background:{pill_bg};color:{pill_tx};'
            f'border-radius:20px;padding:5px 13px;font-family:Inter,Arial,sans-serif;'
            f'font-size:12.5px;font-weight:700;white-space:nowrap;">'
            f'<span style="font-size:9px;">{arrow}</span> {disp}</span></td></tr>'
        )
    return out


def _email_section(title, subtitle, color, entries, up):
    return (
        f'<tr><td style="padding:26px 40px 6px;">'
        f'<div style="border-bottom:3px solid {color};padding-bottom:9px;">'
        f'<span style="font-family:Inter,Arial,sans-serif;font-size:13px;font-weight:700;'
        f'letter-spacing:1.4px;text-transform:uppercase;color:{NAVY};">{title}</span>'
        f'<span style="font-family:Inter,Arial,sans-serif;font-size:12px;color:{MUTED};'
        f'margin-left:8px;">{subtitle}</span></div></td></tr>'
        f'<tr><td style="padding:2px 40px 4px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;">{_email_rows(entries, up)}</table></td></tr>'
    )


def build_email_html(movers, drags):
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p")

    h = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f3f3ee;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f3f3ee;">
<tr><td align="center" style="padding:34px 16px;">
<table width="640" cellpadding="0" cellspacing="0" border="0"
  style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 30px rgba(0,0,0,.07);">
<tr><td style="height:4px;background:linear-gradient(90deg,{GOLD},{GOLD_B},{GOLD});font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td style="padding:28px 40px 20px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td valign="middle">
      <img src="{LOGO_URL}" width="42" height="42" alt="Proflex" style="vertical-align:middle;">
      <span style="font-family:Georgia,serif;font-weight:800;font-size:21px;letter-spacing:4px;color:{GOLD};vertical-align:middle;margin-left:12px;">PROFLEX</span>
    </td>
    <td align="right" valign="middle">
      <span style="font-family:Inter,Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{GOLD};border:1px solid rgba(166,138,62,.4);padding:6px 12px;border-radius:20px;">Daily Report</span>
    </td>
  </tr></table>
  <h1 style="font-family:Georgia,serif;font-weight:800;font-size:27px;color:{DARK};margin:20px 0 6px;line-height:1.15;">
    Daily <span style="color:{GOLD};">Movers &amp; Drags</span></h1>
  <p style="font-family:Inter,Arial,sans-serif;font-size:13px;color:{MUTED};margin:0;">{date_str} &middot; {time_str} IST</p>
</td></tr>
<tr><td style="padding:0 40px;"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
  <td width="50%" align="center" style="padding:16px 0;background:#faf9f5;border:1px solid rgba(20,28,37,.08);border-radius:10px 0 0 10px;">
    <div style="font-family:Georgia,serif;font-size:28px;font-weight:800;color:{GREEN};">{len(movers)}</div>
    <div style="font-family:Inter,Arial,sans-serif;font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:{MUTED};font-weight:700;margin-top:4px;">Movers &ge; +2%</div></td>
  <td width="50%" align="center" style="padding:16px 0;background:#faf9f5;border:1px solid rgba(20,28,37,.08);border-left:none;border-radius:0 10px 10px 0;">
    <div style="font-family:Georgia,serif;font-size:28px;font-weight:800;color:{RED};">{len(drags)}</div>
    <div style="font-family:Inter,Arial,sans-serif;font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:{MUTED};font-weight:700;margin-top:4px;">Drags &le; -2%</div></td>
</tr></table></td></tr>
{_email_section("Top Movers", "&mdash; +2% and above", GREEN, movers, True)}
{_email_section("Top Drags", "&mdash; -2% and below", RED, drags, False)}
<tr><td style="padding:22px 40px 26px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="border-top:1px solid rgba(20,28,37,.08);padding-top:14px;">
      <span style="font-family:Inter,Arial,sans-serif;font-size:10.5px;color:{MUTED};">&#9733; = Priority Watchlist &nbsp;&middot;&nbsp; Threshold &plusmn;2% &nbsp;&middot;&nbsp; Live via Yahoo Finance &middot; Not investment advice</span>
    </td>
    <td align="right" style="border-top:1px solid rgba(20,28,37,.08);padding-top:14px;white-space:nowrap;">
      <span style="font-family:Georgia,serif;font-weight:800;font-size:14px;letter-spacing:3px;color:{GOLD};">PROFLEX</span>
      <span style="font-family:Inter,Arial,sans-serif;font-size:10.5px;color:{MUTED};margin-left:8px;">proflexfinance.com</span>
    </td>
  </tr></table>
</td></tr>
</table></td></tr></table></body></html>'''
    return h


def send_email(stocks):
    pw = get_app_password()
    if not pw:
        raise RuntimeError(
            "No Gmail App Password found. Create one at "
            "myaccount.google.com/apppasswords and save it to ~/.proflex/email.txt")
    movers, drags = split_movers_drags(stocks)
    html = build_email_html(movers, drags)
    subject = "Proflex Daily | Movers & Drags | " + \
        datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Proflex Daily <{EMAIL_FROM}>"
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=25) as srv:
        srv.login(EMAIL_FROM, pw)
        srv.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    return len(movers), len(drags)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Ignore the query string so cache-busters like /api/stocks?t=123 route.
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/stocks":
            self._send(200, json.dumps(fetch_all()))
        elif path == "/api/earnings-perf":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ticker = (qs.get("ticker") or ["NVDA"])[0].upper()
            # Only serve tickers we actually track, so the endpoint can't be
            # used as an open Yahoo proxy.
            if ticker not in KNOWN_TICKERS:
                self._send(400, json.dumps(
                    {"ok": False, "error": f"{ticker} is not on the watchlist"}))
                return
            from earnings_perf import earnings_performance
            self._send(200, json.dumps(earnings_performance(ticker)))
        elif path == "/api/forward-pe":
            # Lazy import: forward_pe imports from this module for the crumb
            # session and ticker universe, so a top-level import would cycle.
            from forward_pe import forward_pe_all
            self._send(200, json.dumps(forward_pe_all()))
        elif path == "/api/newsletters":
            self._send(200, json.dumps(fetch_newsletters()))
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif path.startswith("/api/"):
            self._send(404, json.dumps({"error": "not found"}))
        else:
            # Single-page app: any non-API path serves the dashboard.
            html = (HERE / "index.html").read_text()
            self._send(200, html, "text/html; charset=utf-8")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path.rstrip("/") == "/api/email":
            try:
                stocks = fetch_all()
                nm, nd = send_email(stocks)
                self._send(200, json.dumps(
                    {"ok": True, "movers": nm, "drags": nd, "to": EMAIL_TO}))
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def main():
    _load_earnings_cache()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  Proflex Daily running →  http://localhost:{PORT}\n")
    print(f"  {len(WATCHLIST)} tickers | {len(PRIORITY)} priority | Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
        server.shutdown()


if __name__ == "__main__":
    main()
