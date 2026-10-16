#!/usr/bin/env python3
"""Snapshot the Proflex Terminal (localhost:9000) dashboards and post them to Telegram.

Each dashboard is captured from its own port (the hub embeds exactly these pages in iframes), after
booting it through the hub's /start endpoint and pressing its Refresh button. Every capture is checked
before anything is sent: data must be from the current session, tables must be populated and the image
must not be blank. A failed check retries, then reports the failure to the admin chat; a broken or stale
image never reaches the group.

  python3 snap.py all|daily|sectors|rotation [--to admin|group] [--dry]
"""
import os
import json, re, socket, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import tg

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
HUB = "http://127.0.0.1:9000"
SERVICES = Path(os.environ.get("SERVICES_JSON", HERE / "services.json"))
ET = ZoneInfo("America/New_York")
SCALE = 2          # retina capture, Telegram downsizes to 2560px max side


def log(msg):
    line = f"{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET  {msg}"
    print(line, flush=True)
    with open(HERE / "bot.log", "a") as f:
        f.write(line + "\n")


def port_up(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_up(sid):
    """Start a hub service if its port is down: via the hub, or directly from services.json if the hub is down."""
    svc = next(s for s in json.loads(SERVICES.read_text())["services"] if s["id"] == sid)
    if port_up(svc["port"]):
        return svc["port"]
    try:
        urllib.request.urlopen(urllib.request.Request(f"{HUB}/start?id={sid}", method="POST"), timeout=30).read()
    except OSError:
        subprocess.Popen(svc["cmd"], cwd=SERVICES.parent / svc["cwd"], start_new_session=True,
                         stdout=open(HERE / f"log-{sid}.txt", "ab"), stderr=subprocess.STDOUT)
    for _ in range(120):
        if port_up(svc["port"]):
            time.sleep(2)  # let the first data fetch kick off
            return svc["port"]
        time.sleep(0.5)
    raise RuntimeError(f"{svc['name']} did not come up on :{svc['port']}")


def session_date():
    """Date (ET) of the latest US regular session, from SPY's last trade time."""
    req = urllib.request.Request("https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        t = json.load(r)["chart"]["result"][0]["meta"]["regularMarketTime"]
    return datetime.fromtimestamp(t, ET).date()


def clip_of(page, selectors, pad=16):
    """Bounding box covering every selector's element (page coordinates)."""
    boxes = []
    sx, sy = page.evaluate("[window.scrollX, window.scrollY]")
    for sel in selectors:
        el = page.query_selector(sel)
        if not el:
            raise RuntimeError(f"element not found: {sel}")
        b = el.bounding_box()
        if not b or b["width"] < 50 or b["height"] < 5:
            raise RuntimeError(f"element not rendered: {sel}")
        boxes.append({**b, "x": b["x"] + sx, "y": b["y"] + sy})
    x0 = min(b["x"] for b in boxes) - pad
    y0 = min(b["y"] for b in boxes) - pad
    x1 = max(b["x"] + b["width"] for b in boxes) + pad
    y1 = max(b["y"] + b["height"] for b in boxes) + pad
    clip = {"x": max(0, x0), "y": max(0, y0), "width": x1 - max(0, x0), "height": y1 - max(0, y0)}
    if clip["height"] < 150:
        raise RuntimeError(f"capture area too small: {selectors}")
    return clip


def not_blank(path):
    from PIL import Image, ImageStat
    im = Image.open(path).convert("L")
    if ImageStat.Stat(im).stddev[0] < 8:
        raise RuntimeError(f"{path.name} looks blank")


def fit_for_telegram(path, max_sum=9800, max_ratio=20, max_bytes=9_500_000):
    """Telegram rejects photos with width+height > 10000 or aspect ratio > 20 (PHOTO_INVALID_DIMENSIONS),
    and files over 10MB. A long movers/drags list at 2x scale can cross that, so shrink in place."""
    from PIL import Image
    im = Image.open(path)
    w, h = im.size
    if max(w, h) / min(w, h) > max_ratio:
        raise RuntimeError(f"{path.name} is {w}x{h}, too long for a Telegram photo")
    if w + h > max_sum:
        k = max_sum / (w + h)
        im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
        im.save(path, optimize=True)
        log(f"resized {path.name} {w}x{h} -> {im.size[0]}x{im.size[1]}")
    if path.stat().st_size > max_bytes:
        im.convert("RGB").save(path, "PNG", optimize=True)
        if path.stat().st_size > max_bytes:
            raise RuntimeError(f"{path.name} is {path.stat().st_size // 1_000_000}MB, over Telegram's 10MB photo limit")


def shot(page, name, selectors):
    path = OUT / f"{datetime.now(ET):%Y%m%d}-{name}.png"
    page.screenshot(path=str(path), clip=clip_of(page, selectors), full_page=True)
    not_blank(path)
    fit_for_telegram(path)
    return path


# ── one capture function per dashboard: returns [(png, caption)] ──────────────────────────

def mover_note(rows, up):
    """One-line read of a movers/drags table: the biggest name and the sector that dominates the list."""
    parsed = []
    for r in rows:
        head, _, rest = r.partition("\n\t")
        cells = rest.split("\t")
        chg = re.search(r"[+-]?\d+(?:\.\d+)?%", rest)
        if len(cells) > 1 and chg:
            parsed.append((head.split("\n")[0].strip(), cells[0].strip(), chg.group(0)))
    if not parsed:
        return ""
    tick, sector, chg = parsed[0]
    counts = {}
    for _, sec, _ in parsed:
        counts[sec] = counts.get(sec, 0) + 1
    hot, k = max(counts.items(), key=lambda kv: kv[1])
    lead = f"{tick} ({sector}) is the {'biggest gainer' if up else 'heaviest drag'} at {chg}"
    return lead + (f"; {hot} {'leads' if up else 'is hit hardest'} with {k} of {len(parsed)} names" if k > 1 else "")


def cap_daily(page, sess):
    page.goto(f"http://127.0.0.1:{ensure_up('daily')}/", wait_until="networkidle", timeout=120000)
    page.wait_for_selector("#view table, #view .empty", timeout=90000)
    page.click("#refresh")
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle", timeout=120000)
    page.wait_for_selector("#view table", timeout=90000)
    upd = page.inner_text("#updated").replace("updated", "").strip()
    t = datetime.strptime(upd, "%b %d, %Y, %I:%M %p")
    if abs(datetime.now() - t) > timedelta(minutes=15):
        raise RuntimeError(f"Daily Chartplay data not refreshed (updated {upd})")
    stats = page.inner_text("#stats")
    movers = re.search(r"(\d+)\s*MOVERS", stats)
    drags = re.search(r"(\d+)\s*DRAGS", stats)
    out = []
    for tab, label in (("Top Movers", "Top movers ≥ +2%"), ("Top Drags", "Top drags ≤ −2%")):
        page.click(f"#tabs .tab:text-is('{tab}')")
        page.wait_for_timeout(800)
        rows = [r.inner_text() for r in page.query_selector_all("#view tbody tr")]
        top = ", ".join(r.split("\n")[0].strip() for r in rows[:5])
        n = (movers if "movers" in label else drags)
        cap = f"*{label}* · {sess:%a %b %d}" + (f" · {n.group(1)} names" if n else "") + (f"\n{top}" if top else "")
        note = mover_note(rows, "movers" in label)
        cap += f"\n💬 {note}" if note else ""
        out.append((shot(page, tab.lower().replace(" ", "-"), ["#view"]), cap))
    return out


def cap_sectors(page, sess):
    page.goto(f"http://127.0.0.1:{ensure_up('sector')}/", wait_until="networkidle", timeout=120000)
    page.wait_for_function("document.querySelectorAll('#tableBody tr').length >= 40", timeout=120000)
    page.click("#refreshBtn")
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle", timeout=120000)
    page.wait_for_function("/^Live/.test(document.querySelector('#updatedText').innerText)", timeout=120000)
    page.wait_for_timeout(1500)  # chart animation
    rows = [r.inner_text().split("\t") for r in page.query_selector_all("#tableBody tr")]
    lead = ", ".join(f"{r[0]} {r[3]}" for r in rows[:3])
    lag = ", ".join(f"{r[0]} {r[3]}" for r in rows[-3:][::-1])
    note = (f"{rows[0][1]} ({rows[0][0]}) leads the S&P at {rows[0][3]} today; "
            f"{rows[-1][1]} ({rows[-1][0]}) lags most at {rows[-1][3]}")
    chart = shot(page, "sectors-chart", ["#paneUniverse > .legend", "#paneUniverse > .wlwrap"])
    table = shot(page, "sectors-table", ["#fullTable"])
    return [(chart, f"*Sector Rotation vs S&P 500* · {sess:%a %b %d}\nLeading (1D rel): {lead}\nLagging: {lag}\n💬 {note}"),
            (table, f"*Full universe* · {len(rows)} sector ETFs · 1D / 5D / 3M / 1Y relative to SPY")]


def cap_rotation(page, sess):
    page.goto(f"http://127.0.0.1:{ensure_up('rotation')}/", wait_until="networkidle", timeout=120000)
    page.wait_for_selector("#quadcard", timeout=120000)
    page.click("button:text-is('Refresh')")
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle", timeout=180000)
    page.wait_for_timeout(1500)
    asof = page.inner_text("#asof")
    m = re.search(r"(\d+)/(\d+)/(\d{4})", asof)
    if not m or (int(m[3]), int(m[1]), int(m[2])) != (sess.year, sess.month, sess.day):
        raise RuntimeError(f"Rotation Terminal not on the latest session ({asof.strip()} vs {sess})")
    page.wait_for_selector("#app > section.on > .card + .card", timeout=120000)
    page.wait_for_timeout(1000)
    head = page.query_selector("#app > section.on .headline")
    one_line = head.inner_text().strip() if head else ""
    page.evaluate("[...document.querySelectorAll('#app > section.on > .card')].forEach((c,i)=>c.dataset.snap=i)")
    today = shot(page, "rotation-today", ["[data-snap='0']", "[data-snap='1']"])
    # "What to take next" (Stocks tab): every mega-cap ranked by today vs the index, with the last 10 sessions as dots
    page.click("#tabs button[data-k='stocks']")
    page.wait_for_timeout(1000)
    if not page.evaluate("""(() => { const c = [...document.querySelectorAll("section.on .card")]
        .find(c => c.querySelector('h2')?.innerText.trim() === 'What to take next');
        if (c && c.querySelector('tr td')) { c.dataset.snap = 'next'; return true } return false })()"""):
        raise RuntimeError("Rotation Terminal: 'What to take next' table not found or empty")
    nxt = shot(page, "rotation-take-next", ["[data-snap='next']"])
    return [(today, f"*Rotation Terminal* · {sess:%a %b %d}" + (f"\n💬 {one_line}" if one_line else "")),
            (nxt, "*What to take next* · every stock ranked by today vs the index, last 10 sessions as dots")]


JOBS = {"daily": cap_daily, "sectors": cap_sectors, "rotation": cap_rotation}


def capture(names, retries=2):
    """Capture the named dashboards; returns ({name: [(png, caption)]}, {name: error})."""
    from playwright.sync_api import sync_playwright
    OUT.mkdir(exist_ok=True)
    sess = session_date()
    got, errs = {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in names:
            for attempt in range(retries + 1):
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=SCALE)
                try:
                    got[name] = JOBS[name](page, sess)
                    errs.pop(name, None)
                    break
                except Exception as e:
                    errs[name] = f"{type(e).__name__}: {e}"
                    log(f"!! snapshot {name} attempt {attempt + 1} failed: {errs[name]}")
                    time.sleep(10)
                finally:
                    page.close()
        browser.close()
    return got, errs


def send(names, to="group", dry=False):
    """Capture + post. Returns a one-line summary. Failures go to the admin chat, never to the group."""
    names = list(JOBS) if names in (None, ["all"], "all") else names
    got, errs = capture(names)
    chat = tg.target(to)
    if dry:
        return f"dry run: captured {sum(len(v) for v in got.values())} images, failed {list(errs)}"
    sent = []
    for name in names:
        if name in got:
            items = got[name]
            try:  # one dashboard failing to post must not block the others
                if len(items) > 1:
                    tg.send_album(chat, items)
                else:
                    tg.send_photo(chat, *items[0])
            except Exception as e:
                errs[name] = f"send failed: {e}"
                log(f"!! snapshot {name} send failed: {e}")
                continue
            sent.append(name)
            log(f"sent snapshot {name} ({len(items)} images) -> {chat}")
    if errs:
        tg.send_message(tg.target("admin"),
                        "⚠️ *Snapshot problem* — not sent to the group:\n" + "\n".join(f"• {k}: {v}" for k, v in errs.items()))
    return f"sent {sent}" + (f", failed {list(errs)}" if errs else "")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    to = "admin" if "--to" not in sys.argv else sys.argv[sys.argv.index("--to") + 1]
    args = [a for a in args if a != to]
    print(send(args or ["all"], to=to, dry="--dry" in sys.argv))
