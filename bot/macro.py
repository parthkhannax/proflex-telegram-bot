#!/usr/bin/env python3
"""Critical US macro calendar + watchlist earnings: week-ahead / day-ahead one-pagers and a release squawk.

Sources (free, no key): Nasdaq economic calendar (time ET, actual, consensus, previous), Forex Factory's
this-week feed (fills consensus Nasdaq lacks), Nasdaq earnings calendar (BMO/AMC) for tv-levels/levels.csv.
Only a whitelist of market-moving releases is kept (CRITICAL below); Fed speakers are tagged FED.

  python3 macro.py week [--send]     render The Week Ahead PNG (next week from Fri evening ET on)
  python3 macro.py day  [--send]     render The Day Ahead PNG for today (ET)
  python3 macro.py today             print today's critical list with actuals so far
"""
import os, shutil
import html, json, re, subprocess, sys, time, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import tg

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
LEVELS_CSV = HERE.parent / "tv-levels" / "levels.csv"
CHROME = (os.environ.get("CHROME") or next((c for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",) if Path(c).exists()), None)
          or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser"))
ET, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
ETFS = {"SPY", "QQQ", "SLV", "IBIT"}
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

# (pattern on the Nasdaq event name, short label, tier). Tier 1 = squawked on release and can headline.
# Names that come twice (m/m and y/y) are split by the size of the previous print, see label_pairs().
CRITICAL = [
    (r"^Core CPI$", "Core CPI", 1), (r"^CPI$", "CPI", 1),
    (r"^Core PCE Price Index$", "Core PCE", 1), (r"^PCE Price index$", "PCE", 1),
    (r"^Core PPI$", "Core PPI", 1), (r"^PPI$", "PPI", 1),
    (r"^Nonfarm Payrolls$", "Nonfarm Payrolls", 1), (r"^Unemployment Rate$", "Unemployment Rate", 1),
    (r"^Average Hourly Earnings", "Avg Hourly Earnings", 1),
    (r"^GDP$|^GDP \(QoQ\)", "GDP", 1),
    (r"^Retail Sales$", "Retail Sales", 1), (r"^Retail Control$", "Retail Control", 1),
    (r"^ISM Manufacturing PMI$", "ISM Manufacturing", 1),
    (r"^ISM Non-Manufacturing PMI$|^ISM Services PMI$", "ISM Services", 1),
    (r"^JOLTs? Job Openings$", "JOLTS Job Openings", 1),
    (r"^ADP Nonfarm Employment Change$", "ADP Employment", 1),
    (r"^Initial Jobless Claims$", "Initial Jobless Claims", 1),
    (r"^Michigan Consumer Sentiment$", "UMich Sentiment", 1),
    (r"^Michigan 1-Year Inflation Expectations$", "UMich 1-yr Infl. Expectations", 1),
    (r"^Michigan 5-Year Inflation Expectations$", "UMich 5–10yr Infl. Expectations", 2),
    (r"^CB Consumer Confidence$", "Conference Board Confidence", 1),
    (r"^Fed Interest Rate Decision$", "FOMC Rate Decision", 1),
    (r"^FOMC Press Conference$", "FOMC Press Conference", 1),
    (r"^FOMC Meeting Minutes$", "FOMC Minutes", 1),
    (r"^Beige Book$", "Beige Book", 2),
    (r"^Fed Chair .*(Speaks|Testifies)$", None, 1),
    (r"^Philadelphia Fed Manufacturing Index$", "Philly Fed Manufacturing", 2),
    (r"^NY Empire State Manufacturing Index$", "Empire State Manufacturing", 2),
    (r"^Durable Goods Orders$", "Durable Goods", 2),
    (r"^10-Year Note Auction$", "10-yr Note Auction", 2), (r"^30-Year Bond Auction$", "30-yr Bond Auction", 2),
    (r"^(FOMC Member|Fed) .*Speaks$|^Fed's .* Speaks$", None, 2),
]
INFLATION = re.compile(r"CPI|PCE|PPI|Infl|Hourly")
NO_ACTUAL = re.compile(r"Speaks|Testifies|Minutes|Beige|Press Conference")
_cache = {}


def _get(url, ttl=0):
    hit = _cache.get(url)
    if ttl and hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    _cache[url] = (time.time(), data)
    return data


def clean(v):
    v = html.unescape(v or "").replace("\xa0", " ").strip()
    return v or ""


def num(v):
    m = re.search(r"-?[\d,]*\.?\d+", v or "")
    return float(m.group().replace(",", "")) if m else None


def is_fed(name):
    return bool(re.search(r"Speaks|Testifies|FOMC|Fed Interest|Beige", name))


# ── data ─────────────────────────────────────────────────────────────────────────────────

def events(day, ttl=600):
    """Critical US releases on `day` (ET): [{t, name, label, tier, fed, actual, cons, prev}] sorted by time.
    Nasdaq's ?date=D returns the events of D-1 (checked against Thursday jobless claims), hence the +1."""
    rows = _get(f"https://api.nasdaq.com/api/calendar/economicevents?date={day + timedelta(days=1):%Y-%m-%d}", ttl)
    rows = ((rows or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        if r.get("country") != "United States":
            continue
        name = clean(r["eventName"])
        for rank, (pat, label, tier) in enumerate(CRITICAL):
            if re.search(pat, name):
                out.append({"t": clean(r["gmt"]), "name": name, "label": label or speaker(name), "tier": tier, "rank": rank,
                            "fed": is_fed(name), "actual": clean(r["actual"]), "cons": clean(r["consensus"]),
                            "prev": clean(r["previous"])})
                break
    label_pairs(out)
    ff_consensus(day, out)
    return sorted(out, key=lambda e: (e["t"], e["tier"], e["rank"], e["label"].endswith("y/y")))


def speaker(name):
    m = re.search(r"(?:FOMC Member|Fed Chair|Fed's|Fed)\s+(.*?)\s+(Speaks|Testifies)", name)
    who = m.group(1) if m else name
    return f"Fed Chair {who} {m.group(2).lower()}" if "Chair" in name else f"Fed's {who} speaks"


def label_pairs(evs):
    """CPI / PPI / PCE / Retail Sales come as two rows with one name: the smaller previous print is m/m."""
    by = {}
    for e in evs:
        by.setdefault((e["t"], e["label"]), []).append(e)
    for (t, lab), grp in by.items():
        if len(grp) == 2 and all("%" in (e["prev"] or e["actual"]) for e in grp):
            grp.sort(key=lambda e: abs(num(e["prev"]) or 0))
            grp[0]["label"], grp[1]["label"] = f"{lab} m/m", f"{lab} y/y"
            grp[1]["tier"] = 2 if grp[1]["tier"] == 1 and lab.startswith("Retail") else grp[1]["tier"]
        elif len(grp) > 1:
            for e in grp[1:]:
                evs.remove(e)


def ff_consensus(day, evs):
    """Forex Factory's this-week feed has forecasts Nasdaq often lacks before the week starts."""
    try:
        ff = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", 3600)
    except Exception:
        return
    def key(s):
        s = s.lower().replace("uom", "umich").replace("prelim ", "").replace("final ", "").replace("revised ", "")
        return re.sub(r"[^a-z0-9/]", "", s)
    fc = {}
    for f in ff:
        if f.get("country") == "USD" and f.get("forecast") and f["date"][:10] == f"{day:%Y-%m-%d}":
            fc[(f["date"][11:16], key(f["title"]))] = f["forecast"]
    for e in evs:
        if not e["cons"]:
            k = key(e["label"])
            e["cons"] = next((v for (t, fk), v in fc.items() if t == e["t"] and (fk == k or fk.startswith(k))), "")


def watchlist():
    syms = [l.split(",")[0] for l in LEVELS_CSV.read_text().splitlines()[1:] if l.strip()]
    return [s for s in syms if s not in ETFS]


def earnings(day, syms=None):
    """Watchlist names reporting on `day`: [(sym, 'BMO'|'AMC'|'')]. Nasdaq earnings dates are not offset."""
    syms = set(syms or watchlist())
    try:
        rows = ((_get(f"https://api.nasdaq.com/api/calendar/earnings?date={day:%Y-%m-%d}", 3600) or {})
                .get("data") or {}).get("rows") or []
    except Exception:
        return []
    when = {"time-pre-market": "BMO", "time-after-hours": "AMC"}
    return sorted((r["symbol"], when.get(r.get("time"), "")) for r in rows if r["symbol"] in syms)


def week_days(now=None):
    """Mon-Fri of the week to preview: this week until Friday 16:00 ET, then next week."""
    now = now or datetime.now(ET)
    mon = now.date() - timedelta(days=now.weekday())
    if now.weekday() > 4 or (now.weekday() == 4 and now.hour >= 16):
        mon += timedelta(days=7)
    return [mon + timedelta(days=i) for i in range(5)]


# ── words ────────────────────────────────────────────────────────────────────────────────

def ist(day, t):
    hh, mm = map(int, t.split(":"))
    d = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).astimezone(IST)
    return f"{d:%-I:%M%p}".lower().replace("m", "") + ("" if d.date() == day else " +1")


def et12(t):
    hh, mm = map(int, t.split(":"))
    return f"{(hh - 1) % 12 + 1}:{mm:02d}{'a' if hh < 12 else 'p'}"


def headline(days):
    """'CPI, PPI and retail sales headline the week' from the tier-1 releases, in a fixed order of importance."""
    order = ["FOMC Rate Decision", "Nonfarm Payrolls", "CPI", "Core PCE", "PCE", "PPI", "Retail Sales", "GDP",
             "ISM Manufacturing", "ISM Services", "JOLTS Job Openings", "FOMC Minutes", "UMich Sentiment", "Initial Jobless Claims"]
    seen = {e["label"].split(" m/m")[0].split(" y/y")[0] for _, evs, _ in days for e in evs if e["tier"] == 1}
    seen |= {"Fed Chair" for _, evs, _ in days for e in evs if e["label"].startswith("Fed Chair")}
    top = [o for o in order if o in seen]
    nice = {"FOMC Rate Decision": "the Fed decision", "Nonfarm Payrolls": "payrolls", "Initial Jobless Claims": "jobless claims", "Fed Chair": "the Fed Chair",
            "Retail Sales": "retail sales", "UMich Sentiment": "Michigan sentiment", "FOMC Minutes": "Fed minutes"}
    if "Fed Chair" in seen and len(top) < 3:
        top.append("Fed Chair")
    top = [nice.get(o, o) for o in top][:3]
    if not top:
        return "A light day for US data" if len(days) == 1 else "A light week for US data"
    s = " and ".join([", ".join(top[:-1]), top[-1]]) if len(top) > 1 else top[0]
    verb = ("leads" if len(top) == 1 else "lead") + " the day" if len(days) == 1 else \
        ("headlines" if len(top) == 1 else "headline") + " the week"
    return f"{s[0].upper()}{s[1:]} {verb}"


def standfirst(days):
    bits = []
    for d, evs, _ in days:
        for e in evs:
            if e["tier"] == 1 and not e["label"].endswith("y/y") and len(bits) < 4:
                if e["label"].startswith("Fed Chair"):
                    bits.append(f"{e['label']} {d:%a} {et12(e['t'])} ET")
                elif e["prev"]:
                    bits.append(f"{e['label']} {d:%a} {et12(e['t'])} ET (prev {e['prev']}"
                                + (f", cons. {e['cons']})" if e["cons"] else ")"))
    earn = [f"{s} {d:%a} {w}".strip() for d, _, er in days for s, w in er]
    s = "; ".join(bits) + "." if bits else ""
    if earn:
        s += f" Watchlist earnings: {', '.join(earn)}."
    return s.strip()


# ── render ───────────────────────────────────────────────────────────────────────────────

def render(days, title, stem):
    from proflex_charts import _FONTS, LOGO, _crop
    multi = len(days) > 1
    rng = (f"{days[0][0]:%a %b %-d} – {days[-1][0]:%a %b %-d, %Y}" if multi else f"{days[0][0]:%a · %b %-d, %Y}")
    trs = []
    for d, evs, ers in days:
        if multi:
            trs.append(f'<tr class="day"><td colspan="5">{d:%A · %b %-d}</td></tr>')
        minor = [e for e in evs if e["fed"] and e["tier"] == 2 and "speaks" in e["label"]]
        rows = [e for e in evs if e not in minor]
        if not rows and not ers and not minor:
            trs.append('<tr><td colspan="5" class="none">No critical US releases</td></tr>')
        for e in rows:
            tag = '<span class="tag fed">FED</span>' if e["fed"] else ""
            cls = ' class="fedrow"' if e["fed"] else ""
            name = f'<b>{html.escape(e["label"])}</b>' if e["tier"] == 1 else html.escape(e["label"])
            act = f'<td class="num act">{e["actual"]}</td>' if e["actual"] else '<td class="num dim">—</td>'
            trs.append(f'<tr{cls}><td class="t">{ist(d, e["t"])}<small>{et12(e["t"])} ET</small></td>'
                       f'<td class="ev">{name}{tag}</td><td class="num">{e["prev"] or "—"}</td>'
                       f'<td class="num cons">{e["cons"] or "—"}</td>{act}</tr>')
        if minor:
            who = ", ".join(dict.fromkeys(e["label"].replace("Fed's ", "").replace(" speaks", "") for e in minor))
            trs.append(f'<tr class="fedrow"><td class="t">{ist(d, minor[0]["t"])}<small>from {et12(minor[0]["t"])} ET</small></td>'
                       f'<td class="ev" colspan="4">Fed speakers: {who}<span class="tag fed">FED</span></td></tr>')
        for s_, w in ers:
            t = {"BMO": "Pre-mkt", "AMC": "After close"}.get(w, "Time n/a")
            trs.append(f'<tr class="earn"><td class="t">{t}</td><td class="ev" colspan="4"><b>{s_}</b> earnings'
                       f'<span class="tag earn">EARN</span></td></tr>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><link href="{_FONTS}" rel="stylesheet"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--head:'Libre Franklin',Arial,sans-serif;--sans:'Public Sans',Arial,sans-serif;--mono:'IBM Plex Mono',monospace;
--navy:#1d4f7c;--dark:#141c25;--mut:rgba(20,28,37,.55);--ln:rgba(20,28,37,.12)}}
body{{background:#f3f3ee;font-family:var(--sans);color:var(--dark);-webkit-font-smoothing:antialiased;width:860px}}
.card{{width:860px;background:#fff}}
.inner{{padding:30px 30px 6px}}
.top{{display:flex;justify-content:space-between;align-items:baseline}}
h1{{font-family:var(--head);font-weight:900;font-size:52px;color:var(--navy);letter-spacing:-1.5px}}
.date{{font-family:var(--head);font-weight:800;font-size:22px;color:var(--navy)}}
.rule{{height:3px;background:var(--navy);margin:12px 0 18px}}
h2{{font-family:var(--head);font-weight:800;font-size:29px;line-height:1.25;margin-bottom:18px}}
table{{width:100%;border-collapse:collapse;font-size:24px;border-bottom:3px solid var(--navy)}}
th{{font-weight:700;font-size:17px;color:rgba(20,28,37,.65);text-align:left;padding:8px 10px;border-bottom:3px solid var(--navy)}}
td{{padding:13px 10px;border-bottom:1px solid var(--ln);vertical-align:middle}}
td.t{{font-weight:700;white-space:nowrap;width:1%}} td.t small{{display:block;font-weight:500;font-size:15px;color:var(--mut);margin-top:2px}}
td.ev{{line-height:1.25}}
th.num,td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;width:1%}}
td.cons{{color:var(--navy);font-weight:700}} td.act{{font-weight:900}} td.dim{{color:var(--mut)}}
tr.day td{{font-family:var(--head);font-weight:900;font-size:22px;color:#fff;background:var(--navy);padding:9px 10px}}
tr.fedrow td{{background:#faf3e6}} tr.earn td{{background:#ecf5ee}}
.tag{{font-size:14px;font-weight:800;padding:3px 7px;border-radius:4px;color:#fff;vertical-align:3px;margin-left:10px}}
.fed{{background:#b5651d}} .earn{{background:#2f6f4e}}
.none{{color:var(--mut)}}
.foot{{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:14px 30px 22px}}
.src{{font-size:14px;color:var(--mut);line-height:1.5}}
.fbrand{{display:flex;align-items:center;gap:10px}}.fbrand img{{width:40px;height:40px;object-fit:contain}}
.fbrand .w{{font-family:var(--mono);font-weight:700;font-size:17px;letter-spacing:2.4px}}
</style></head><body><div class="card"><div class="inner">
<div class="top"><h1>{title}</h1><div class="date">{rng}</div></div><div class="rule"></div>
<h2>{html.escape(headline(days))}</h2>
<table><tr><th>IST</th><th>Event</th><th class="num">Prev</th><th class="num">Cons.</th><th class="num">Actual</th></tr>
{"".join(trs)}</table></div>
<div class="foot"><div class="src"><b>Bold</b> = market-moving, posted to the group the moment it prints. Source: Nasdaq, Forex Factory.</div>
<div class="fbrand"><img src="{LOGO}"><div class="w">PROFLEX</div></div></div>
</div></body></html>"""
    OUT.mkdir(exist_ok=True)
    htm, png = OUT / f"{stem}.html", OUT / f"{stem}.png"
    htm.write_text(page)
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    "--virtual-time-budget=5000", "--window-size=860,4000", f"--screenshot={png}", str(htm)],
                   capture_output=True, timeout=120)
    _crop(png)
    return png


def week_ahead(send=False, chat=None):
    days = [(d, events(d), earnings(d)) for d in week_days()]
    png = render(days, "The Week Ahead", f"week-{days[0][0]:%Y-%m-%d}")
    cap = f"*The Week Ahead* · {days[0][0]:%b %-d}–{days[-1][0]:%b %-d}\n{headline(days)}."
    if send:
        tg.send_photo(chat or tg.target("group"), str(png), cap)
    return png, days


def day_ahead(send=False, chat=None, day=None):
    d = day or datetime.now(ET).date()
    days = [(d, events(d), earnings(d))]
    if not days[0][1] and not days[0][2]:
        return None, days
    png = render(days, "The Day Ahead", f"day-{d:%Y-%m-%d}")
    if send:
        tg.send_photo(chat or tg.target("group"), str(png), f"*The Day Ahead* · {d:%a %b %-d}\n{headline(days)}.")
    return png, days


def today_text(day=None):
    d = day or datetime.now(ET).date()
    evs = events(d, ttl=60)
    ers = earnings(d)
    if not evs and not ers:
        return f"📅 *{d:%a %b %-d}* · no critical US releases or watchlist earnings."
    lines = [f"📅 *Macro today* · {d:%a %b %-d} (ET / IST)"]
    for e in evs:
        res = f" → *{e['actual']}*{surprise(e)}" if e["actual"] else ""
        cons = f" · cons {e['cons']}" if e["cons"] else ""
        prev = f" · prev {e['prev']}" if e["prev"] else ""
        lines.append(f"{et12(e['t'])} / {ist(d, e['t'])} {'🏛 ' if e['fed'] else ''}{e['label']}{cons}{prev}{res}")
    for s, w in ers:
        lines.append(f"💼 {s} earnings {w}".rstrip())
    return "\n".join(lines)


# ── squawk ───────────────────────────────────────────────────────────────────────────────

def surprise(e):
    a, c = num(e["actual"]), num(e["cons"])
    if a is None or c is None or not e["cons"]:
        return ""
    if a == c:
        return " · in line"
    up = a > c
    if INFLATION.search(e["label"]):
        return " · 🔥 hotter than expected" if up else " · ❄️ cooler than expected"
    return " · ▲ above consensus" if up else " · ▼ below consensus"


def squawk(st, now=None):
    """Called every ~20s by the bot. Posts tier-1 releases as their actuals land (grouped by release time), and a
    one-line heads-up for events with no number (Fed minutes, Chair speeches). Each event posts once (state)."""
    now = now or datetime.now(ET)
    d = now.date()
    if now.weekday() > 4:
        return
    sched = st.setdefault("macro_sched", {})
    if sched.get("day") != f"{d}" or time.time() - sched.get("at", 0) > 1800:
        evs = events(d, ttl=0)
        sched.update(day=f"{d}", at=time.time(), times=sorted({e["t"] for e in evs if e["tier"] == 1}))
    hhmm = f"{now:%H:%M}"
    live = [t for t in sched.get("times", []) if t <= hhmm <= f"{min(int(t[:2]) + 1, 23):02d}{t[2:]}"]
    if not live:
        return
    done = st.setdefault("macro_posted", [])
    evs = events(d, ttl=15)
    for t in live:
        grp = [e for e in evs if e["t"] == t and e["tier"] == 1]
        ids = {id(e): f"{d}|{t}|{e['label']}" for e in grp}
        for e in grp:
            if NO_ACTUAL.search(e["name"]) and ids[id(e)] not in done:
                tg.send_message(tg.target("group"), f"🏛 *{e['label']}* · now ({et12(t)} ET / {ist(d, t)} IST)")
                done.append(ids[id(e)])
        nums = [e for e in grp if not NO_ACTUAL.search(e["name"])]
        ready = [e for e in nums if e["actual"] and ids[id(e)] not in done]
        waiting = [e for e in nums if not e["actual"]]
        late = hhmm >= f"{int(t[:2]) + (int(t[3:]) + 3) // 60:02d}:{(int(t[3:]) + 3) % 60:02d}"
        if ready and (not waiting or late):
            lines = [f"🔔 *US data* · {et12(t)} ET / {ist(d, t)} IST"]
            for e in ready:
                cons = f" vs {e['cons']} cons" if e["cons"] else ""
                lines.append(f"*{e['label']}* {e['actual']}{cons} (prev {e['prev'] or '—'}){surprise(e)}")
                done.append(ids[id(e)])
            tg.send_message(tg.target("group"), "\n".join(lines))
    st["macro_posted"] = done[-300:]


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "week"
    if what == "today":
        print(today_text())
    else:
        png, days = (week_ahead if what == "week" else day_ahead)(send="--send" in sys.argv)
        for d, evs, ers in days:
            print(d, [(e["t"], e["label"], e["prev"], e["cons"], e["actual"]) for e in evs], ers)
        print(png)
        if png and "--send" not in sys.argv and sys.platform == "darwin":
            subprocess.run(["open", str(png)])
