#!/usr/bin/env python3
"""Proflex Levels alert watcher.

Each tick: fetch the latest price for every ticker in levels.csv (yfinance batch), compare it with the
price from the previous tick, and alert on every level crossed in between (Run, Worry, Clear, each TP),
up or down, once price is 0.5 daily ATR past the level (chop inside that band doesn't alert), plus an early "approaching" heads-up when price gets within approach_pct of a level,
via Telegram (proflex-telegram-bot, group chat) and email (Gmail SMTP). The WhatsApp bridge is kept but off.
Zones: RUN < run <= WORRY < worry <= NEUTRAL < clear <= CLEAR < tp1 <= TAKE PROFIT [TP2...]

  python3 alerts.py tick      # one pass (launchd runs this every minute)
  python3 alerts.py status    # print current price/zone for every ticker, no alerts
  python3 alerts.py test      # send a test message on both channels
"""
import os
import csv, json, smtplib, ssl, sys, time, urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
LEVELS = HERE / "levels.csv"
STATE = HERE / "alert_state.json"
STALE_FILE = HERE / "stale.json"   # edited from the localhost:8765 dashboard
LOG = HERE / "alerts.log"

CHANNELS = {"telegram": True, "email": True, "whatsapp": False}   # WhatsApp bridge retired 2026-10-07 for Telegram
TG_DIR = HERE.parent / "bot" if (HERE.parent / "bot").exists() else HERE.parent / "proflex-telegram-bot"

WA_RECIPIENT = os.environ.get("WA_RECIPIENT", "")   # "Parth 2" contact; set via env, never committed
WA_URL = "http://127.0.0.1:8080/api/send"
WA_TOKEN = HERE.parent / "whatsapp-mcp" / "whatsapp-bridge" / "store" / ".api_token"

EMAIL_FROM = "parth@proflexfinance.com"
EMAIL_TO = "parth@proflexfinance.com"
PW_FILE = Path.home() / ".proflex" / "proflex-email.txt"

ATR_MULT = 0.5             # default only: stale.json "atr_mult" overrides it. A cross only counts once price is
                           # this many daily ATRs past the level; until then the ticker keeps its old zone
ATR_LEN = 14
FALLBACK_BUF_PCT = 0.3     # confirmation band (% of level) for a ticker whose ATR can't be fetched
APPROACH_PCT = 0.2         # default only: stale.json "approach_pct" overrides it (edited from the :8765 dashboard)
SWING_PCT = 3.0            # default only: stale.json "swing_pct". A day move of this size off a level = levels being tested
SWING_NEAR_PCT = 1.0       # default only: stale.json "swing_near_pct". "Off a level" = prior close within this % of it, or crossed
STALE_PCT = 3.0            # default only: stale.json "pct" overrides it. Stale = pct above the top Clear/TP or pct below Run;
                           # a per-ticker price in stale.json "levels" replaces the upside trigger for that ticker
EXTENDED_HOURS = False     # True = also alert in pre/post market (4:00-20:00 ET)
ET = ZoneInfo("America/New_York")

EMOJI = {"RUN": "🔴", "WORRY": "🟠", "NEUTRAL": "⚪", "CLEAR": "🟢", "TAKE PROFIT": "🔵"}


def log(msg):
    line = f"{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET  {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def nums(cell):
    out = []
    for part in (cell or "").replace("+", "").split("/"):
        try:
            out.append(float(part.strip()))
        except ValueError:
            pass
    return out


def load_levels():
    rows = {}
    with open(LEVELS) as f:
        for r in csv.DictReader(f):
            run, worry, clear = nums(r["run"]), nums(r["worry"]), nums(r["clear"])
            if not (run and worry and clear):
                continue
            rows[r["symbol"].strip().upper()] = {
                "run": run[0], "worry": worry[0], "clear": clear[0],
                "tp": nums(r.get("take_profit")), "dir": r.get("dir") or "None",
            }
    return rows


def sig(lv):
    """Fingerprint of one ticker's levels, to spot which tickers changed when levels.csv is saved."""
    return "|".join(fmt(x) for x in [lv["run"], lv["worry"], lv["clear"], *lv["tp"]])


def zone(price, lv):
    """Zone name plus a rank, so we can tell up-moves from down-moves."""
    if price < lv["run"]:
        return "RUN", 0
    if price < lv["worry"]:
        return "WORRY", 1
    if price < lv["clear"]:
        return "NEUTRAL", 2
    hit = [t for t in lv["tp"] if price >= t]
    if not hit:
        return "CLEAR", 3
    n = len(hit)
    return ("TAKE PROFIT" if len(lv["tp"]) == 1 else f"TAKE PROFIT {n}"), 3 + n


def market_open(now):
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (4 * 60 <= hm < 20 * 60) if EXTENDED_HOURS else (9 * 60 + 30 <= hm < 16 * 60)


def prices(syms):
    """Latest price for every symbol: one yfinance batch call, raw Yahoo endpoint as per-ticker fallback."""
    out = {}
    try:
        import yfinance as yf
        df = yf.download(syms, period="1d", interval="1m", prepost=EXTENDED_HOURS,
                         progress=False, auto_adjust=False, group_by="column", threads=True)["Close"]
        for s in syms:
            col = df[s].dropna() if s in df else []
            if len(col):
                out[s] = float(col.iloc[-1])
    except Exception as e:
        log(f"!! yfinance batch failed: {e}")
    for s in syms:
        if s not in out:
            try:
                out[s] = price(s)
            except Exception as e:
                log(f"!! {s} price fetch failed: {e}")
    return out


def price(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m"
    if EXTENDED_HOURS:
        url += "&includePrePost=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.load(r)["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    return closes[-1] if (EXTENDED_HOURS and closes) else res["meta"]["regularMarketPrice"]


def send_whatsapp(text):
    req = urllib.request.Request(
        WA_URL, data=json.dumps({"recipient": WA_RECIPIENT, "message": text}).encode(),
        headers={"Content-Type": "application/json", "X-Bridge-Token": WA_TOKEN.read_text().strip()})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    if not res.get("success"):
        raise RuntimeError(res)


def send_email(subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"], msg["Date"] = subject, EMAIL_FROM, EMAIL_TO, formatdate(localtime=True)
    pw = PW_FILE.read_text().strip().replace(" ", "")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as s:
        s.login(EMAIL_FROM, pw)
        s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())


def send_telegram(text):
    if str(TG_DIR) not in sys.path:
        sys.path.insert(0, str(TG_DIR))
    import tg
    tg.send_message(tg.target(tg.config().get("levels_target", "group")), text)


def notify(subject, body, email=True):
    senders = {"telegram": lambda: send_telegram(body), "whatsapp": lambda: send_whatsapp(body),
               "email": lambda: send_email(subject, body)}
    for name, fn in senders.items():
        if not CHANNELS[name] or (name == "email" and not email):
            continue
        try:
            fn()
        except Exception as e:
            log(f"!! {name} send failed: {e}")


def fmt(x):
    return f"{x:g}"


def label(n):
    """Level name for one-line alerts: Run / Worry / Clear / TP / TP2."""
    return n if n.startswith("TP") else n.title()


def boundaries(lv):
    """Every alertable level, low to high: (name, price)."""
    tps = lv["tp"]
    named = [("RUN", lv["run"]), ("WORRY", lv["worry"]), ("CLEAR", lv["clear"])]
    named += [("TP" if len(tps) == 1 else f"TP{i}", t) for i, t in enumerate(tps, 1)]
    return named


def crossings(prev_p, p, lv):
    """Levels crossed between the last checked price and now. A gap through several levels returns all of them."""
    up = [(n, L) for n, L in boundaries(lv) if prev_p < L <= p]
    down = [(n, L) for n, L in boundaries(lv) if p < L <= prev_p]
    return [(n, L, True) for n, L in up] + [(n, L, False) for n, L in reversed(down)]


def zone_name(rank, lv):
    """Inverse of zone(): rank = number of levels at or below price."""
    if rank < 3:
        return ["RUN", "WORRY", "NEUTRAL"][rank]
    n = rank - 3
    if n == 0:
        return "CLEAR"
    return "TAKE PROFIT" if len(lv["tp"]) == 1 else f"TAKE PROFIT {n}"


def confirmed_rank(p, lv, prev_rank, buf):
    """Zone rank with hysteresis: price must close a level by `buf` (0.5 ATR) to move past it.
    Inside the band around a level the ticker keeps its previous zone, so chop there never alerts."""
    lo = sum(p >= L + buf for _, L in boundaries(lv))   # levels confirmed above
    hi = sum(p >= L - buf for _, L in boundaries(lv))   # levels not yet confirmed below
    return max(lo, min(prev_rank, hi))


def confirmed_crossings(prev_rank, rank, lv):
    """Levels between the old and new confirmed zone, in the order they were crossed."""
    b = boundaries(lv)
    if rank > prev_rank:
        return [(n, L, True) for n, L in b[prev_rank:rank]]
    return [(n, L, False) for n, L in reversed(b[rank:prev_rank])]


def atrs(syms, state):
    """Daily ATR per symbol, fetched once per ET day and cached in state["_atr"]."""
    today = f"{datetime.now(ET):%Y-%m-%d}"
    cache = state.get("_atr", {})
    if cache.get("date") == today and all(s in cache.get("v", {}) for s in syms):
        return cache["v"]
    out = {}
    try:
        import yfinance as yf
        df = yf.download(syms, period="2mo", interval="1d", progress=False, auto_adjust=False,
                         group_by="column", threads=True)
        for s in syms:
            try:
                h, l, c = (df[k][s] for k in ("High", "Low", "Close"))
                pc = c.shift(1)
                tr = (h - l).combine((h - pc).abs(), max).combine((l - pc).abs(), max).dropna()
                if len(tr) >= 5:
                    out[s] = float(tr.tail(ATR_LEN).mean())
            except Exception:
                pass
    except Exception as e:
        log(f"!! ATR fetch failed: {e}")
    if out:
        state["_atr"] = {"date": today, "v": out}
    return out or cache.get("v", {})


def prev_closes(syms, state):
    """Prior session's close per symbol (the base for today's % move), fetched once per ET day, cached in state["_pc"]."""
    today = datetime.now(ET).date()
    cache = state.get("_pc", {})
    if cache.get("date") == f"{today}" and all(s in cache.get("v", {}) for s in syms):
        return cache["v"]
    out = {}
    try:
        import yfinance as yf
        df = yf.download(syms, period="10d", interval="1d", progress=False, auto_adjust=False,
                         group_by="column", threads=True)["Close"]
        for s in syms:
            col = df[s].dropna() if s in df else []
            col = col[[d.date() < today for d in col.index]] if len(col) else col
            if len(col):
                out[s] = float(col.iloc[-1])
    except Exception as e:
        log(f"!! prior close fetch failed: {e}")
    if out:
        state["_pc"] = {"date": f"{today}", "v": out}
    return out or cache.get("v", {})


def swing_level(p, pc, lv, cfg):
    """Day swing of swing_pct+ that started at a level (prior close within swing_near_pct of it) or ran through one:
    (name, level, chg%) for the level nearest the prior close, or None."""
    chg = (p / pc - 1) * 100
    if abs(chg) < cfg["swing_pct"]:
        return None
    lo, hi = min(p, pc), max(p, pc)
    hit = [(n, L) for n, L in boundaries(lv) if abs(pc - L) / L * 100 <= cfg["swing_near_pct"] or lo < L < hi]
    if not hit:
        return None
    n, L = min(hit, key=lambda b: abs(pc - b[1]))
    return n, L, chg


def swing_text(sym, p, n, L, chg):
    return f"⚠️ *{sym}* {chg:+.1f}% today off {label(n)} {fmt(L)} · {p:.2f} · levels being tested, review"


def alert_text(sym, p, lv, old_zone, new_zone, hits):
    moves = ", ".join(f"{'above' if up else 'below'} {label(name)} {fmt(L)}" for name, L, up in hits)
    icon = EMOJI["TAKE PROFIT"] if new_zone.startswith("TAKE") else EMOJI[new_zone]
    return f"{icon} *{sym}* → *{new_zone}* · {p:.2f} · crossed {moves} (was {old_zone.title()})"


# What getting close to each level means, by side: (headline, emoji). From below = price rising into it.
APPROACH = {
    ("CLEAR", True): ("approaching *ALL CLEAR* breakout zone", "🟡"),
    ("CLEAR", False): ("testing *Clear* from above, close to losing All Clear", "🟠"),
    ("WORRY", False): ("approaching *WORRY* signal", "🟠"),
    ("WORRY", True): ("approaching a reclaim of *Worry* (back to Neutral)", "🟡"),
    ("RUN", False): ("approaching *RUN* signal", "🔴"),
    ("RUN", True): ("approaching a reclaim of *Run* (out of the Run zone)", "🟡"),
    ("TP", True): ("approaching *Take Profit{n}*", "🔵"),
    ("TP", False): ("slipping back toward *Take Profit{n}*", "🟠"),
}


def near_levels(p, lv, pct):
    """Levels price is within pct% of, without having crossed: [(key, name, level, from_below, dist%)]."""
    out = []
    for n, L in boundaries(lv):
        below = p < L
        dist = abs(L - p) / L * 100
        if dist <= pct:
            out.append((f"{n}{'+' if below else '-'}", n, L, below, dist))
    return out


def approach_text(sym, p, lv, n, L, below, dist):
    base = "TP" if n.startswith("TP") else n
    head, icon = APPROACH[(base, below)]
    head = head.format(n=f" {n[2:]}" if base == "TP" and len(n) > 2 else "")
    return f"{icon} *{sym}* {head} · {p:.2f} · {dist:.2f}% {'below' if below else 'above'} {label(n)} {fmt(L)}"


def approaches(sym, p, lv, near, pct, crossed):
    """Approaching heads-up: at most ONCE PER DAY per ticker per level (either side counts as the same level).
    `near` is the ticker's {level name: ET date last alerted} memory (mutated); `crossed` = level names
    that alerted this tick (the crossing alert already covers those, and still uses up today's heads-up)."""
    today = f"{datetime.now(ET):%Y-%m-%d}"
    for n in [n for n, d in near.items() if d != today]:
        near.pop(n)
    for key, n, L, below, dist in near_levels(p, lv, pct):
        if n in near:
            continue
        near[n] = today
        if n in crossed:
            continue
        notify(f"Proflex Levels: {sym} near {n} {fmt(L)} ({p:.2f})", approach_text(sym, p, lv, n, L, below, dist),
               email=False)
        log(f"NEAR {sym} @ {p:.2f}  {dist:.2f}% {'below' if below else 'above'} {n} {fmt(L)}")


def load_stale():
    """{"pct": default % past Clear/TP (up) or Run (down), "levels": {SYM: explicit upside stale price}} from stale.json."""
    try:
        cfg = json.loads(STALE_FILE.read_text())
    except (OSError, ValueError):
        cfg = {}
    return {"pct": float(cfg.get("pct", STALE_PCT)),
            "approach_pct": float(cfg.get("approach_pct", APPROACH_PCT)),
            "atr_mult": float(cfg.get("atr_mult", ATR_MULT)),
            "swing_pct": float(cfg.get("swing_pct", SWING_PCT)),
            "swing_near_pct": float(cfg.get("swing_near_pct", SWING_NEAR_PCT)),
            "levels": {k.upper(): float(v) for k, v in (cfg.get("levels") or {}).items() if v not in ("", None)}}


def stale_level(p, lv, sym, cfg):
    """Stale trigger price hit: (name, level, pct) or None. pct is signed: + above the level, - below it.
    Up: cfg pct above the highest Clear/TP it has run past (or the ticker's explicit stale price, if set).
    Down: cfg pct below Run."""
    if p <= lv["run"] * (1 - cfg["pct"] / 100):
        return "RUN", lv["run"], (p / lv["run"] - 1) * 100
    if sym in cfg["levels"]:
        L = cfg["levels"][sym]
        return (f"STALE {fmt(L)}", L, (p / L - 1) * 100) if p >= L else None
    ups = [(n, L) for n, L in boundaries(lv) if n == "CLEAR" or n.startswith("TP")]
    over = [(n, L) for n, L in ups if p >= L * (1 + cfg["pct"] / 100)]
    if not over:
        return None
    n, L = over[-1]
    return n, L, (p / L - 1) * 100


def stale_where(name, L, pct):
    if name.startswith("STALE"):
        return f"hit stale level {fmt(L)}"
    return f"{pct:+.1f}% {'above' if pct > 0 else 'below'} {label(name)} {fmt(L)}"


def stale_text(sym, p, lv, name, L, pct):
    return f"⚠️ *{sym}* levels going *STALE* · {p:.2f} · {stale_where(name, L, pct)}, time to update"


def tick(force=False):
    now = datetime.now(ET)
    if not force and not market_open(now):
        return
    levels = load_levels()
    cfg = load_stale()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.pop("_levels_mtime", None)
    stale = state.setdefault("_stale", {})
    for sym, lv in levels.items():
        prev = state.get(sym)
        if prev and prev.get("sig", sig(lv)) != sig(lv):
            # this ticker's levels were edited: re-seed it silently and clear its stale marker;
            # every other ticker keeps its zone, cooldowns and stale state
            state.pop(sym)
            for k in [k for k in stale if k.split(":")[0] == sym]:
                stale.pop(k)
            log(f"{sym} levels changed: re-seeding")
    px = prices(list(levels))
    atr = atrs(list(levels), state)
    pcs = prev_closes(list(levels), state)
    today = f"{now:%Y-%m-%d}"
    swung = {s: d for s, d in state.get("_swing", {}).items() if d == today}
    state["_swing"] = swung
    for sym, lv in levels.items():
        if sym not in px:
            continue
        p = px[sym]
        buf = atr[sym] * cfg["atr_mult"] if sym in atr else lv["worry"] * FALLBACK_BUF_PCT / 100
        st = stale_level(p, lv, sym, cfg)
        if st and f"{sym}:{st[0]}" not in stale:
            notify(f"Proflex Levels: {sym} levels going stale ({p:.2f})", stale_text(sym, p, lv, *st))
            log(f"STALE {sym} @ {p:.2f}  {stale_where(*st)}")
            # the localhost:8765 dashboard reads this to mark the ticker stale until its levels are edited
            stale[f"{sym}:{st[0]}"] = {"t": time.time(), "price": round(p, 2), "name": st[0],
                                       "level": st[1], "pct": round(st[2], 2)}
        sw = swing_level(p, pcs[sym], lv, cfg) if sym in pcs else None
        if sw and sym not in swung:  # once per ticker per ET day
            notify(f"Proflex Levels: {sym} {sw[2]:+.1f}% off {label(sw[0])} {fmt(sw[1])}", swing_text(sym, p, *sw))
            log(f"SWING {sym} @ {p:.2f}  {sw[2]:+.1f}% off {sw[0]} {fmt(sw[1])}")
            swung[sym] = today
        prev = state.get(sym)
        if prev is None or "price" not in prev:
            near = {}
            approaches(sym, p, lv, near, cfg["approach_pct"], set())
            rank = zone(p, lv)[1]
            state[sym] = {"price": p, "zone": zone_name(rank, lv), "rank": rank, "fired": {}, "sig": sig(lv),
                          "near": near}
            continue
        # a level only counts as crossed once price is buf (0.5 ATR) past it; chop inside that band keeps
        # the old zone. Each level alerts at most once per ET day in each direction (fired = {"WORRY-": date})
        prev_rank = prev.get("rank", zone(prev["price"], lv)[1])
        rank = confirmed_rank(p, lv, prev_rank, buf)
        new_zone = zone_name(rank, lv)
        crossed = confirmed_crossings(prev_rank, rank, lv)
        fired = {k: d for k, d in prev.get("fired", {}).items() if d == today}
        hits = [(n, L, up) for n, L, up in crossed if f"{n}{'+' if up else '-'}" not in fired]
        if hits:
            notify(f"Proflex Levels: {sym} → {new_zone} ({p:.2f})",
                   alert_text(sym, p, lv, prev["zone"], new_zone, hits))
            log(f"ALERT {sym} {prev['zone']} -> {new_zone} @ {p:.2f}  "
                + ", ".join(f"{n}{'↑' if up else '↓'}" for n, _, up in hits) + f"  (band ±{buf:.2f})")
            for n, _, up in hits:
                fired[f"{n}{'+' if up else '-'}"] = today
        near = prev.get("near", {})
        # a level that already crossed today (raw or confirmed) uses up its approaching heads-up too
        approaches(sym, p, lv, near, cfg["approach_pct"],
                   {n for n, _, _ in crossings(prev["price"], p, lv)} | {k[:-1] for k in fired})
        state[sym] = {"price": p, "zone": new_zone, "rank": rank, "fired": fired, "sig": sig(lv), "near": near}
    STATE.write_text(json.dumps(state, indent=1))


def status():
    levels = load_levels()
    cfg = load_stale()
    px = prices(list(levels))
    pcs = prev_closes(list(levels), {})
    for sym, lv in levels.items():
        if sym in px:
            st = stale_level(px[sym], lv, sym, cfg)
            sw = swing_level(px[sym], pcs[sym], lv, cfg) if sym in pcs else None
            print(f"{sym:6} {px[sym]:>9.2f}  {zone(px[sym], lv)[0]}"
                  + (f"  STALE {stale_where(*st)}" if st else "")
                  + (f"  SWING {sw[2]:+.1f}% off {label(sw[0])} {fmt(sw[1])}" if sw else ""))
        else:
            print(f"{sym:6} no price")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        tick()
    elif cmd == "status":
        status()
    elif cmd == "test":
        notify("Proflex Levels: test alert", "✅ Proflex Levels alerts are wired up ("
               + " + ".join(k for k, v in CHANNELS.items() if v) + ").")
        log("test alert sent")
