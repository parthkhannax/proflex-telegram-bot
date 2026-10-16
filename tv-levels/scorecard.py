"""Proflex Levels daily scorecard: one bright, institutional one-pager per US session.

Built from levels.csv (the Pine levels), yfinance daily bars and alerts.log, rendered with headless Chrome
in the Proflex brand chrome (proflex_charts), archived to scorecards/<date>.{png,json} and, with --send,
posted to the Telegram levels group. The Telegram bot runs it daily at 16:15 ET (config.json "scorecard").

  python3 scorecard.py            build today's (or the latest session's) scorecard and open it
  python3 scorecard.py --send     build and post to Telegram
"""
import os, shutil
import html, json, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE / "scorecards"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bot"))
sys.path.insert(0, str(HERE.parent / "proflex-telegram-bot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
import alerts as A
from proflex_charts import _FONTS, LOGO, _crop

ET = ZoneInfo("America/New_York")
CHROME = (os.environ.get("CHROME") or next((c for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",) if Path(c).exists()), None)
          or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser"))
WATCH_ATR = 1.0          # a ticker is on the watchlist when its next level is within this many daily ATRs
FAST_PCT = 3.0           # the table lists only fast movers: |today| >= this (zone changes have their own box)
TRAIL_SESSIONS = 5       # follow-through window for logged signals

ZONE_COLOR = {"RUN": "#b8432f", "WORRY": "#c47f17", "NEUTRAL": "#7a828c", "CLEAR": "#2f6f4e", "TP": "#3b6b8a"}
ZONE_ORDER = ["TP", "CLEAR", "NEUTRAL", "WORRY", "RUN"]
ZONE_TITLE = {"TP": "Take Profit", "CLEAR": "All Clear", "NEUTRAL": "Neutral", "WORRY": "Worry", "RUN": "Run"}


def zkey(z):
    return "TP" if z.startswith("TAKE") else z


def daily_bars(syms):
    """{sym: DataFrame of daily OHLC} for the last 3 months."""
    import yfinance as yf
    df = yf.download(syms, period="3mo", interval="1d", progress=False, auto_adjust=False, group_by="ticker",
                     threads=True)
    out = {}
    for s in syms:
        try:
            d = df[s][["High", "Low", "Close"]].dropna()
            if len(d) >= 6:
                out[s] = d
        except Exception:
            pass
    return out


def atr(d, n=A.ATR_LEN):
    pc = d["Close"].shift(1)
    tr = (d["High"] - d["Low"]).combine((d["High"] - pc).abs(), max).combine((d["Low"] - pc).abs(), max)
    return float(tr.dropna().tail(n).mean())


def log_events(sess):
    """Today's ALERT / NEAR / STALE lines from alerts.log, plus prior-session ALERTs for follow-through."""
    today, prior = [], []
    rx = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2} ET  (ALERT|NEAR|STALE) (\S+) (.*)$")
    for line in (HERE / "alerts.log").read_text().splitlines():
        m = rx.match(line)
        if not m:
            continue
        d, t, kind, sym, rest = m.groups()
        if d == f"{sess:%Y-%m-%d}":
            today.append((t, kind, sym, rest))
        elif d < f"{sess:%Y-%m-%d}" and kind == "ALERT":
            prior.append((d, t, sym, rest))
    return today, prior


def follow_through(prior, bars, sess):
    """Each logged zone-change signal in the trailing sessions (first per ticker/day/direction), scored by the
    move from the alert price to this session's close in the signal's direction."""
    days = sorted({d for d, *_ in prior})[-TRAIL_SESSIONS:]
    seen, rows = set(), []
    for d, t, sym, rest in prior:
        if d not in days or sym not in bars:
            continue
        m = re.match(r"(\S+(?: \d)?(?: PROFIT(?: \d)?)?) -> (.+?) @ ([\d.]+)\s+(.*)", rest)
        if not m:
            continue
        frm, to, p, moves = m.groups()
        up = "↑" in moves
        if (d, sym, up) in seen:
            continue
        seen.add((d, sym, up))
        close = float(bars[sym]["Close"].iloc[-1])
        ret = (close / float(p) - 1) * 100 * (1 if up else -1)
        rows.append({"date": d, "time": t, "sym": sym, "to": to.strip(), "up": up, "price": float(p),
                     "close": close, "ret": ret})
    return rows, days


def ladder(lv, p, prev):
    """Inline SVG: the ticker's level stack as a zoned track, today's close as a dot, prior close hollow."""
    W, H, pad = 250, 26, 8
    levels = [lv["run"], lv["worry"], lv["clear"], *lv["tp"]]
    lo, hi = min(levels + [p, prev]), max(levels + [p, prev])
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    x = lambda v: pad + (v - lo) / (hi - lo) * (W - 2 * pad)
    edges = [lo, lv["run"], lv["worry"], lv["clear"], *(lv["tp"][:1]), hi]
    fills = ["RUN", "WORRY", "NEUTRAL", "CLEAR"] + (["TP"] if lv["tp"] else [])
    o = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    for (a, b), z in zip(zip(edges, edges[1:]), fills):
        o.append(f'<rect x="{x(a):.1f}" y="10" width="{max(x(b) - x(a), 0):.1f}" height="6" '
                 f'fill="{ZONE_COLOR[z]}" opacity=".22"/>')
    for L in levels:
        o.append(f'<line x1="{x(L):.1f}" x2="{x(L):.1f}" y1="6" y2="20" stroke="#141c25" stroke-opacity=".45" stroke-width="1"/>')
    o.append(f'<circle cx="{x(prev):.1f}" cy="13" r="4" fill="#fff" stroke="#141c25" stroke-opacity=".5" stroke-width="1.3"/>')
    z = zkey(A.zone(p, lv)[0])
    o.append(f'<circle cx="{x(p):.1f}" cy="13" r="5.5" fill="{ZONE_COLOR[z]}" stroke="#fff" stroke-width="1.5"/>')
    o.append("</svg>")
    return "".join(o)


def pct(v, d=2):
    return f'<span class="{"pos" if v > 0 else "neg" if v < 0 else ""}">{v:+.{d}f}%</span>'


def build(send=False):
    import snap
    from bot import session_closed
    levels = A.load_levels()
    sess = snap.session_date()
    closed = session_closed(sess)
    now = datetime.now(ET)
    bars = daily_bars(list(levels))
    events, prior = log_events(sess)

    rows, missing = [], []
    for sym, lv in levels.items():
        d = bars.get(sym)
        if d is None or d.index[-1].date() != sess:
            missing.append(sym)
            continue
        p, prev, prev5 = (float(d["Close"].iloc[i]) for i in (-1, -2, -6))
        a = atr(d)
        z, zp = A.zone(p, lv)[0], A.zone(prev, lv)[0]
        b = A.boundaries(lv)
        up = [(n, L) for n, L in b if L > p]
        dn = [(n, L) for n, L in b if L <= p]
        rows.append({"sym": sym, "lv": lv, "conv": lv["dir"], "p": p, "prev": prev, "d1": (p / prev - 1) * 100,
                     "d5": (p / prev5 - 1) * 100, "atr": a, "zone": z, "zprev": zp,
                     "up": (up[0][0], up[0][1], (up[0][1] - p) / a) if up else None,
                     "dn": (dn[-1][0], dn[-1][1], (p - dn[-1][1]) / a) if dn else None})

    by = {r["sym"]: r for r in rows}
    counts = {z: sum(zkey(r["zone"]) == z for r in rows) for z in ZONE_ORDER}
    rank = {z: i for i, z in enumerate(reversed(ZONE_ORDER))}
    moves = [r for r in rows if zkey(r["zone"]) != zkey(r["zprev"])]
    upgrades = [r for r in moves if rank[zkey(r["zone"])] > rank[zkey(r["zprev"])]]
    downgrades = [r for r in moves if r not in upgrades]
    n_alert = sum(k == "ALERT" for _, k, _, _ in events)
    n_near = sum(k == "NEAR" for _, k, _, _ in events)
    n_stale = sum(k == "STALE" for _, k, _, _ in events)
    breakout = sorted((r for r in rows if r["up"] and r["up"][2] <= WATCH_ATR), key=lambda r: r["up"][2])
    breakdown = sorted((r for r in rows if r["dn"] and r["dn"][2] <= WATCH_ATR), key=lambda r: r["dn"][2])
    conflicts = [r for r in rows if (r["conv"] == "Up" and r["zone"] in ("WORRY", "RUN"))
                 or (r["conv"] == "Down" and zkey(r["zone"]) in ("CLEAR", "TP"))]
    ft, ft_days = follow_through(prior, bars, sess)
    hits = [f for f in ft if f["ret"] > 0]

    # ── simple page: headline, three plain-language boxes, one table ──
    breadth = counts["CLEAR"] + counts["TP"]
    stress = counts["WORRY"] + counts["RUN"]
    NEAR_PCT = 1.0   # "close to" = within 1% of the level

    def away(r, side):
        n, L, _ = r[side]
        return n, L, abs(L - r["p"]) / r["p"] * 100

    def meaning(n, up):
        base = "TP" if n.startswith("TP") else n
        return {("CLEAR", True): "All Clear", ("TP", True): "Take Profit", ("WORRY", True): "back to Neutral",
                ("RUN", True): "back to Worry", ("CLEAR", False): "back to Neutral", ("TP", False): "back to All Clear",
                ("WORRY", False): "Worry", ("RUN", False): "Run"}[(base, up)]

    ups = sorted((r for r in rows if r["up"] and away(r, "up")[2] <= NEAR_PCT), key=lambda r: away(r, "up")[2])
    dns = sorted((r for r in rows if r["dn"] and away(r, "dn")[2] <= NEAR_PCT), key=lambda r: away(r, "dn")[2])

    tape = ", ".join(f'{s} {"up" if by[s]["d1"] >= 0 else "down"} {abs(by[s]["d1"]):.1f}%' for s in ("SPY", "QQQ") if s in by)
    if downgrades and not upgrades:
        headline = f'{len(downgrades)} stock{"s" if len(downgrades) != 1 else ""} slipped into <span class="r">{"Worry" if all(r["zone"] == "WORRY" for r in downgrades) else "a weaker zone"}</span> today'
    elif upgrades and not downgrades:
        headline = f'{len(upgrades)} stock{"s" if len(upgrades) != 1 else ""} moved <span class="gr">up a zone</span> today'
    elif moves:
        headline = f'{len(upgrades)} stocks improved, {len(downgrades)} weakened'
    else:
        headline = "No stock changed zone today"
    stand = f'{tape}. {breadth} of {len(rows)} stocks are in All Clear and {stress} are in Worry or Run.'

    def pill(z):
        k = zkey(z)
        return f'<span class="pill" style="background:{ZONE_COLOR[k]}">{ZONE_TITLE[k]}</span>'

    def box(title, items, empty):
        li = "".join(f"<li>{i}</li>" for i in items) or f'<li class="none">{empty}</li>'
        return f'<div class="box"><div class="bt">{title}</div><ul>{li}</ul></div>'

    def usd(r, side):
        return f'${abs(r[side][1] - r["p"]):,.2f}'

    changed = [f'<b>{r["sym"]}</b> {ZONE_TITLE[zkey(r["zprev"])]} to {ZONE_TITLE[zkey(r["zone"])]}' for r in upgrades + downgrades]
    near_up = [f'<b>{r["sym"]}</b> Above {A.fmt(away(r, "up")[1])} = {meaning(away(r, "up")[0], True)} '
               f'<span class="m">+{usd(r, "up")}</span>' for r in ups[:6]]
    near_dn = [f'<b>{r["sym"]}</b> Below {A.fmt(away(r, "dn")[1])} = {meaning(away(r, "dn")[0], False)} '
               f'<span class="m">-{usd(r, "dn")}</span>' for r in dns[:6]]

    def watch_cell(r):
        opts = []
        if r["up"]:
            n, L, d = away(r, "up"); opts.append((d, f'Above {A.fmt(L)} = {meaning(n, True)}', f'needs +{usd(r, "up")}'))
        if r["dn"]:
            n, L, d = away(r, "dn"); opts.append((d, f'Below {A.fmt(L)} = {meaning(n, False)}', f'{usd(r, "dn")} above it'))
        d, txt, gap = min(opts)
        return f'{txt} <span class="m">({gap})</span>'

    cfg = A.load_stale()

    def tested(r):
        sw = A.swing_level(r["p"], r["prev"], r["lv"], cfg)
        return f' <span class="tag">Tested {A.label(sw[0])} {A.fmt(sw[1])}</span>' if sw else ""

    fast = sorted((r for r in rows if abs(r["d1"]) >= FAST_PCT), key=lambda r: -abs(r["d1"]))
    trs = [f'<tr><td class="sym">{r["sym"]}</td><td class="num">{r["p"]:,.2f}</td>'
           f'<td class="num">{pct(r["d1"], 1)}</td><td>{pill(r["zone"])}</td><td>{watch_cell(r)}{tested(r)}</td></tr>'
           for r in fast] or [f'<tr><td colspan="5" class="none">No stock moved {FAST_PCT:g}% or more today</td></tr>']

    when = f"{sess:%A, %B %-d, %Y}" + ("" if closed else f" · intraday {now:%H:%M} ET")
    note = f" No data today for {', '.join(missing)}." if missing else ""
    page = f"""<!doctype html><html><head><meta charset="utf-8"><link href="{_FONTS}" rel="stylesheet"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--head:'Libre Franklin',Arial,sans-serif;--sans:'Public Sans',Arial,sans-serif;--mono:'IBM Plex Mono',monospace;
--dark:#141c25;--gold:#a68a3e;--mut:rgba(20,28,37,.55);--ln:rgba(20,28,37,.10)}}
body{{background:#f3f3ee;font-family:var(--sans);color:var(--dark);-webkit-font-smoothing:antialiased;width:1200px}}
.card{{width:1200px;background:#fff;border:1px solid var(--ln);border-radius:6px;overflow:hidden}}
.inner{{padding:32px 44px 24px}}
.top{{display:flex;justify-content:space-between;align-items:flex-start}}
.accent{{width:150px;height:6px;background:var(--gold);border-radius:2px;margin-bottom:14px}}
.kicker{{font-family:var(--mono);font-size:14px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--mut)}}
.logo{{width:58px;height:58px;object-fit:contain}}
h1{{font-family:var(--head);font-weight:900;font-size:50px;letter-spacing:-1.5px;line-height:1.05;margin-top:14px}}
h1 .r{{color:#c47f17}} h1 .gr{{color:#2f6f4e}}
.sub{{font-size:20px;color:rgba(20,28,37,.75);margin-top:12px;line-height:1.4}}
.boxes{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:26px}}
.box{{border:1px solid var(--ln);border-top:4px solid var(--dark);border-radius:4px;padding:16px 18px}}
.box:nth-child(2){{border-top-color:#2f6f4e}} .box:nth-child(3){{border-top-color:#b8432f}}
.bt{{font-family:var(--head);font-weight:800;font-size:18px;margin-bottom:8px}}
ul{{list-style:none}} li{{font-size:16px;padding:6px 0;border-bottom:1px solid var(--ln)}} li:last-child{{border:0}}
li b{{font-family:var(--head);font-weight:800;margin-right:4px}}
.m{{color:var(--mut);font-size:14px}} .none{{color:var(--mut)}}
.h2{{font-family:var(--head);font-weight:800;font-size:20px;margin:30px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:16px}}
th{{font:600 12px var(--mono);letter-spacing:.5px;text-transform:uppercase;color:var(--mut);text-align:left;padding:8px 10px;border-bottom:2px solid var(--dark)}}
td{{padding:8px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}}
th.num,td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.sym{{font-family:var(--head);font-weight:800}}
.tag{{background:#c47f1722;color:#8a5a0f;font-size:13px;font-weight:700;padding:2px 8px;border-radius:4px;margin-left:6px}}
.pill{{color:#fff;font-size:13px;font-weight:700;padding:3px 10px;border-radius:20px}}
.pos{{color:#2f6f4e;font-weight:600}}.neg{{color:#b8432f;font-weight:600}}
.foot{{display:flex;justify-content:space-between;align-items:center;gap:26px;border-top:1px solid var(--ln);padding:14px 44px 18px;margin-top:20px}}
.src{{font-family:var(--mono);font-size:11.5px;color:var(--mut);line-height:1.55;max-width:760px}}
.fbrand{{display:flex;align-items:center;gap:12px}}.fbrand img{{width:44px;height:44px;object-fit:contain}}
.fbrand .w{{font-family:var(--mono);font-weight:700;font-size:18px;letter-spacing:2.6px}}
.fbrand .u{{font-family:var(--mono);font-size:11px;color:var(--mut);margin-top:3px}}
</style></head><body><div class="card"><div class="inner">
<div class="top"><div><div class="accent"></div><div class="kicker">Proflex Levels · Daily Scorecard · {when}</div></div>
<img class="logo" src="{LOGO}"></div>
<h1>{headline}</h1><div class="sub">{stand}</div>
<div class="boxes">
{box("Changed today", changed, "No zone changes")}
{box("Close to breaking up", near_up, f"Nothing within {NEAR_PCT:g}%")}
{box("Close to breaking down", near_dn, f"Nothing within {NEAR_PCT:g}%")}
</div>
<div class="h2">Fast movers · {FAST_PCT:g}% or more today ({len(fast)} of {len(rows)})</div>
<table><tr><th>Stock</th><th class="num">Price</th><th class="num">Today</th><th>Status</th><th>Level to watch</th></tr>
{"".join(trs)}</table>
</div>
<div class="foot"><div class="src">Status = where the price sits against Proflex levels at the close. "Close to" = within {NEAR_PCT:g}% of a level. Tested = a {cfg["swing_pct"]:g}%+ day move off or through that level.
Prices: Yahoo Finance.{note} Not investment advice.</div>
<div class="fbrand"><img src="{LOGO}"><div><div class="w">PROFLEX</div><div class="u">proflexfinance.com</div></div></div></div>
</div></body></html>"""

    OUT.mkdir(exist_ok=True)
    stem = OUT / f"{sess:%Y-%m-%d}"
    stem.with_suffix(".html").write_text(page)
    png = stem.with_suffix(".png")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    "--virtual-time-budget=5000", "--window-size=1200,3200", f"--screenshot={png}",
                    str(stem.with_suffix(".html"))], capture_output=True, timeout=120)
    _crop(png)
    from PIL import Image
    im = Image.open(png)
    # snapshot for outcome tracking: every ticker's close zone, so later scorecards can score today's book
    stem.with_suffix(".json").write_text(json.dumps(
        {"session": f"{sess:%Y-%m-%d}", "closed": closed, "rows": [
            {k: r[k] for k in ("sym", "conv", "p", "prev", "d1", "d5", "atr", "zone", "zprev")} for r in rows]},
        indent=1))
    caption = (f"*Proflex Levels · Daily Scorecard* · {sess:%a %b %-d}\n"
               f"{re.sub('<[^>]+>', '', headline)}. {tape}.")
    if send:
        import tg
        chat = tg.target(tg.config().get("levels_target", "group"))
        tg.send_photo(chat, str(png), caption)
    return png, im.size


if __name__ == "__main__":
    png, size = build(send="--send" in sys.argv)
    print(png, size)
