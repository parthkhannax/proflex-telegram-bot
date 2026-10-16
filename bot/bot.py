#!/usr/bin/env python3
"""Proflex Telegram bot daemon: on-demand commands + daily schedules, run by launchd (KeepAlive).

Commands (group or Parth's private chat only):
  /snap      all snapshots              /movers    Daily movers + drags
  /sectors   Sector Rotation            /rotation  Rotation Terminal
  /levels    levels board               /near [%]  tickers within % of a level (default 1)
  /status    bot health + schedules     /chatid    this chat's id
  /stale     tickers whose levels are flagged stale (just the list)
  /staleupdate  one levels form pre-filled with every stale ticker; anyone in the group can fill it in and reply
  /update [SYM..]  Parth only: editable levels block in his DM; sending edited rows back updates levels.csv,
                   rebuilds the Pine and DMs it.   /undo  restores the levels from before the last update

Schedules (config.json "schedule", each in its own tz): each job runs once per day at/after its time, with a 3h catch-up
window if the Mac was asleep; config.json "stale_reminder" pings the group every N minutes (inside its from/to hours)
while any ticker is stale; last runs live in state.json so a restart never double-sends. The snapshot
job posts each US session once, so holidays (no new session) are skipped. The group is auto-registered when Parth adds the bot to it,
and its id follows a group -> supergroup migration.
"""
import importlib.util, json, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import levels_edit, macro, snap, tg, xpost

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
LEVELS_DIR = HERE.parent / "tv-levels"
ET = ZoneInfo("America/New_York")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CATCH_UP = timedelta(hours=3)
RETRY = timedelta(minutes=5)
TZ_LABEL = {"America/New_York": "ET", "Asia/Kolkata": "IST"}
log = snap.log
busy = threading.Lock()     # one Playwright capture at a time

COMMANDS = [("snap", "All snapshots: movers, sectors, rotation"), ("movers", "Daily top movers + drags"),
            ("sectors", "Sector Rotation chart + table"), ("rotation", "Rotation Terminal"),
            ("levels", "Levels board: zone + nearest level"), ("near", "Tickers close to a level, e.g. /near 0.5"),
            ("support", "Bouncing 2%+ off Run/Worry"), ("ceiling", "Pulling back 2%+ off Clear/TP"),
            ("sr", "Support + ceiling, e.g. /sr 1.5 10"),
            ("stale", "Tickers with stale levels"), ("staleupdate", "Update all stale tickers in one form"),
            ("week", "The Week Ahead: critical US macro + earnings"), ("today", "Today's macro with actuals so far"),
            ("dayahead", "The Day Ahead one-pager"),
            ("update", "Edit levels: pick a ticker (Parth only)"), ("undo", "Undo the last levels update"), ("status", "Bot health and schedules"), ("help", "What this bot does")]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"offset": None, "last_run": {}}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1))


def levels_mod():
    spec = importlib.util.spec_from_file_location("levels_alerts", LEVELS_DIR / "alerts.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── levels board ─────────────────────────────────────────────────────────────────────────

def levels_board(within=None):
    A = levels_mod()
    levels = A.load_levels()
    px = A.prices(list(levels))
    apct = A.load_stale()["approach_pct"]
    rows = []
    for sym, lv in levels.items():
        if sym not in px:
            rows.append((99, f"{sym}: no price"))
            continue
        p = px[sym]
        z = A.zone(p, lv)[0]
        n, L = min(A.boundaries(lv), key=lambda b: abs(b[1] - p))
        d = (L - p) / L * 100
        if within is not None and abs(d) > within:
            continue
        icon = A.EMOJI["TAKE PROFIT"] if z.startswith("TAKE") else A.EMOJI[z]
        flag = " ⚡" if abs(d) <= apct else ""
        rows.append((abs(d), f"{icon} *{sym}* {p:.2f} · {z} · {abs(d):.2f}% {'below' if d > 0 else 'above'} {n} {A.fmt(L)}{flag}"))
    rows.sort()
    title = (f"*Levels within {within:g}%*" if within is not None else "*Proflex Levels board*")
    body = "\n".join(r for _, r in rows) or "Nothing that close right now."
    return (f"{title} · {datetime.now(ET):%b %d %H:%M} ET\n{body}\n"
            f"⚡ = inside the {apct:g}% heads-up band · sorted by distance to the nearest level")


def stale_syms():
    """Tickers the alerts job flagged stale (alert_state.json "_stale"); the flag clears once their levels are edited."""
    try:
        marks = json.loads((LEVELS_DIR / "alert_state.json").read_text()).get("_stale", {})
    except (OSError, ValueError):
        marks = {}
    return sorted({k.split(":", 1)[0] for k in marks})


def stale_list():
    syms = stale_syms()
    return f"⚠️ *Stale* ({len(syms)})\n" + "\n".join(syms) if syms else "No stale tickers right now."


def in_window(now, frm, to):
    """True if now's HH:MM is inside [frm, to); a window like 09:00-02:00 wraps past midnight."""
    t = f"{now:%H:%M}"
    return frm <= t < to if frm <= to else (t >= frm or t < to)


def stale_reminder_loop(st):
    """Hourly (config.json "stale_reminder") nudge to the group while any ticker is stale; last ping kept in state.json."""
    while True:
        try:
            rc = {"enabled": True, "every_min": 60, "from": "09:00", "to": "02:00", "tz": "Asia/Kolkata"} | tg.config().get("stale_reminder", {})
            now = datetime.now(ZoneInfo(rc["tz"]))
            due = time.time() - st.get("stale_reminded_at", 0) >= rc["every_min"] * 60 - 30
            syms = stale_syms() if rc["enabled"] and due and in_window(now, rc["from"], rc["to"]) else []
            if syms:
                tg.send_message(tg.target("group"), f"⏰ *{len(syms)} stale level{'s' * (len(syms) > 1)}*: {', '.join(syms)}\n"
                                                    "Tap /staleupdate to update them all in one form.")
                st["stale_reminded_at"] = time.time()
                save_state(st)
                log(f"stale reminder: {syms}")
        except Exception as e:
            log(f"!! stale reminder: {e}")
        time.sleep(60)


def closes(syms):
    """Last daily bar per symbol from yfinance: {sym: (close, bar date)}. No forward-fill, so a stale ticker shows its real date."""
    import yfinance as yf
    df = yf.download(syms, period="5d", interval="1d", progress=False, auto_adjust=False, group_by="column")["Close"]
    out = {}
    for s in syms:
        col = df[s].dropna() if s in df else []
        if len(col):
            out[s] = (float(col.iloc[-1]), col.index[-1].date())
    return out


# ── support / ceiling scan ───────────────────────────────────────────────────────────────

MIN_BOUNCE = 2.0   # % move off the low (support) or off the high (ceiling) before it counts
SR_DAYS = 5        # sessions to look back for the touch, today included
MAX_AWAY = 10.0    # % price may sit from the level and still count as "at" it; beyond that it has moved on


def sr_scan(band=1.0, days=SR_DAYS):
    """Support: the low of the last `days` sessions tagged Run/Worry (within band%) and price is
    now >= MIN_BOUNCE% off that low, still below the next level up.
    Ceiling: the high tagged Clear/TP and price is now >= MIN_BOUNCE% under it, still above the next level down."""
    import yfinance as yf
    A = levels_mod()
    levels = A.load_levels()
    syms = list(levels)
    df = yf.download(syms, period=f"{days + 5}d", interval="1d", progress=False, auto_adjust=False, group_by="column")
    sup, ceil = [], []
    for s, lv in levels.items():
        try:
            lo, hi, cl = (df[k][s].dropna().iloc[-days:] for k in ("Low", "High", "Close"))
        except KeyError:
            continue
        if not len(cl):
            continue
        p, low, high = float(cl.iloc[-1]), float(lo.min()), float(hi.max())
        bounds = A.boundaries(lv)
        for i, (name, L) in enumerate(bounds):
            nxt_up = bounds[i + 1][1] if i + 1 < len(bounds) else float("inf")
            nxt_dn = bounds[i - 1][1] if i else 0.0
            if name in ("RUN", "WORRY") and abs(low - L) / L * 100 <= band:
                up = (p / low - 1) * 100
                if up >= MIN_BOUNCE and p < nxt_up and (p / L - 1) * 100 <= MAX_AWAY:
                    sup.append((up, s, A.label(name), L, low, p, lo.idxmin().date()))
            if name not in ("RUN", "WORRY") and abs(high - L) / L * 100 <= band:
                dn = (1 - p / high) * 100
                if dn >= MIN_BOUNCE and p > nxt_dn and (1 - p / L) * 100 <= MAX_AWAY:
                    ceil.append((dn, s, A.label(name), L, high, p, hi.idxmax().date()))
    best = lambda rows: list({r[1]: r for r in sorted(rows)}.values())   # one line per ticker, biggest move
    return sorted(best(sup), reverse=True), sorted(best(ceil), reverse=True)


def sr_message(which="both", band=1.0, days=SR_DAYS):
    sup, ceil = sr_scan(band, days)
    f = lambda x: f"{x:,.2f}"
    out = []
    if which in ("both", "support"):
        out.append(f"🟢 *At support* · bounced {MIN_BOUNCE:g}%+ off Run/Worry (low within {band:g}%, last {days} sessions)")
        out += [f"{s}  {f(p)}  ▲{m:.1f}% off {f(x)} low ({d:%d %b}) · {n} {f(L)}" for m, s, n, L, x, p, d in sup] or ["None right now"]
    if which in ("both", "ceiling"):
        out.append("")
        out.append(f"🔴 *At ceiling* · pulled back {MIN_BOUNCE:g}%+ off Clear/TP (high within {band:g}%, last {days} sessions)")
        out += [f"{s}  {f(p)}  ▼{m:.1f}% off {f(x)} high ({d:%d %b}) · {n} {f(L)}" for m, s, n, L, x, p, d in ceil] or ["None right now"]
    return "\n".join(out).strip()


def session_closed(sess):
    """True once the regular session on `sess` is over (16:05 ET, leaving time for the final bar to settle)."""
    now = datetime.now(ET)
    return now.date() > sess or now >= now.replace(hour=16, minute=5, second=0, microsecond=0)


def levels_summary(pct=None):
    """One page: what is within pct% of breaking OUT (next level above) and of breaking DOWN (next level below)."""
    A = levels_mod()
    levels = A.load_levels()
    pct = pct if pct is not None else tg.config().get("summary_pct", 2)
    sess = snap.session_date()
    bars = closes(list(levels))
    stale = sorted(s for s in levels if s not in bars or bars[s][1] != sess)
    up, down = [], []
    for sym, lv in levels.items():
        if sym in stale:
            continue
        p = bars[sym][0]
        bounds = sorted(A.boundaries(lv), key=lambda b: b[1])
        above = [(n, L) for n, L in bounds if L > p]
        below = [(n, L) for n, L in bounds if L <= p]
        if above:
            n, L = above[0]
            if (L - p) / p * 100 <= pct:
                up.append(((L - p) / p, sym, p, f"{A.label(n)} {A.fmt(L)}", f"+${L - p:,.2f}"))
        if below:
            n, L = below[-1]
            if (p - L) / p * 100 <= pct:
                down.append(((p - L) / p, sym, p, f"{A.label(n)} {A.fmt(L)}", f"-${p - L:,.2f}"))
    up.sort(), down.sort()
    when = "close" if session_closed(sess) else f"intraday as of {datetime.now(ET):%H:%M} ET"
    note = f"\n⚠️ No fresh {sess:%b %d} data, left out: {', '.join(stale)}" if stale else ""
    return (f"📋 *Proflex Levels · daily summary* · {sess:%a %b %d} {when}\n\n"
            f"🟢 *Close to breakout* · a small move UP crosses the next level\n" + summary_table(up, "Rise") + "\n"
            f"🔴 *Close to breakdown* · a small move DOWN loses the level below\n" + summary_table(down, "Drop") + "\n"
            f"Only names within {pct:g}% of a level · closest first · "
            f"{len(levels) - len(stale)}/{len(levels)} tickers checked{note}")


def summary_table(rows, move):
    """Monospace table for Telegram: ticker, close, the level in play, and the $ move needed to reach it."""
    if not rows:
        return "None\n"
    lines = [f"{'Ticker':<6} {'Close':>8}  {'Level':<11} {move:>7}"]
    lines += [f"{s:<6} {p:>8,.2f}  {lvl:<11} {need:>7}" for _, s, p, lvl, need in rows]
    return "```\n" + "\n".join(lines) + "```\n"


# ── jobs ─────────────────────────────────────────────────────────────────────────────────

def run_snap(chat, names, label):
    if not busy.acquire(blocking=False):
        tg.send_message(chat, "⏳ A snapshot is already being taken, it will land shortly.")
        return
    try:
        tg.send_message(chat, f"📸 Taking {label} snapshots, about a minute…")
        got, errs = snap.capture(names)
        for name in names:
            if name in got:
                items = got[name]
                (tg.send_album(chat, items) if len(items) > 1 else tg.send_photo(chat, *items[0]))
        if errs:
            tg.send_message(chat, "⚠️ Could not capture: " + ", ".join(errs) + ". Parth has the details.")
            tg.send_message(tg.target("admin"), "⚠️ *Snapshot problem*\n" + "\n".join(f"• {k}: {v}" for k, v in errs.items()))
        log(f"on-demand {names} -> {chat}: ok {list(got)} failed {list(errs)}")
    finally:
        busy.release()


def scheduled_snapshots(st, slot="snapshots"):
    # Three slots a day (8 PM, 11 PM, 1:30 AM IST), each posts once per US session. A slot only fires when the
    # current ET day is a trading day (SPY traded today), so weekends and holidays skip.
    sess = snap.session_date()
    if sess != datetime.now(ET).date():
        return "skipped (no session today)"
    key = f"snap_session_{slot}"
    if st.get(key) == f"{sess:%Y-%m-%d}":
        return f"skipped (session {sess} already posted)"
    with busy:
        res = snap.send(["all"], to="group")
    if "failed" not in res:
        st[key] = f"{sess:%Y-%m-%d}"
    return res


def scheduled_board(st):
    if snap.session_date() != datetime.now(ET).date():
        return "skipped (no session)"
    tg.send_message(tg.target("group"), levels_board())
    return "sent"


def scheduled_summary(st):
    # 1:30 AM IST lands after the US close (before the Nov-Mar clock change it is 3 PM ET, so wait for the close).
    # Each session posts once, so weekends and holidays skip; a failed send retries inside the catch-up window.
    sess = snap.session_date()
    if st.get("summary_session") == f"{sess:%Y-%m-%d}":
        return f"skipped (session {sess} already posted)"
    if not session_closed(sess):
        return "retry: market still open"
    try:
        msg = tg.send_message(tg.target("group"), levels_summary())
    except Exception as e:
        return f"retry: {e}"
    st["summary_session"] = f"{sess:%Y-%m-%d}"
    pin_summary(st, msg["chat"]["id"], msg["message_id"])
    return "sent"


def pin_summary(st, chat, mid):
    """Pin the latest summary at the top of the group (needs admin + Pin messages); unpin the previous one."""
    try:
        old = st.get("summary_pin")
        if old and old[0] == chat:
            try:
                tg._call("unpinChatMessage", {"chat_id": chat, "message_id": old[1]}, retries=1)
            except Exception:
                pass
        tg._call("pinChatMessage", {"chat_id": chat, "message_id": mid, "disable_notification": False}, retries=1)
        st["summary_pin"] = [chat, mid]
    except Exception as e:
        log(f"!! pin summary: {e}")
        if not st.get("pin_warned"):
            st["pin_warned"] = True
            tg.send_message(tg.target("admin"), "📌 Could not pin the daily summary. Make the bot a group admin "
                                                "with *Pin messages* permission and it will pin from the next one.")


def scheduled_scorecard(st):
    # Proflex Levels one-pager (tv-levels/scorecard.py): photo + PDF to the levels group, once per session
    sess = snap.session_date()
    if st.get("scorecard_session") == f"{sess:%Y-%m-%d}":
        return f"skipped (session {sess} already posted)"
    if not session_closed(sess):
        return "retry: market still open"
    spec = importlib.util.spec_from_file_location("levels_scorecard", LEVELS_DIR / "scorecard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try:
        m.build(send=True)
    except Exception as e:
        return f"retry: {e}"
    st["scorecard_session"] = f"{sess:%Y-%m-%d}"
    return "sent"


def mirror_to_x(st, kind, png, days):
    """After the Telegram post: same PNG + a 2-line tweet to ProflexFinance via Buffer. Never fails the job."""
    try:
        return f"x {xpost.mirror(st, kind, png, days)}"
    except Exception as e:
        log(f"!! x post {kind}: {e}")
        try:
            tg.send_message(tg.target("admin"), f"⚠️ {kind} ahead posted to Telegram but the X post failed: {e}")
        except Exception:
            pass
        return "x FAILED"


def scheduled_week_ahead(st):
    png, days = macro.week_ahead(send=True)
    return f"sent {days[0][0]}, {mirror_to_x(st, 'week', png, days)}"


def scheduled_day_ahead(st):
    # quiet days (no critical releases or watchlist earnings) post nothing, on Telegram or X.
    png, days = macro.day_ahead(send=True)
    return f"sent, {mirror_to_x(st, 'day', png, days)}" if png else "skipped (nothing critical today)"


def squawk_loop(st):
    """Release squawk: posts critical US data to the group as the actuals print (see macro.squawk)."""
    warned = None
    while True:
        try:
            macro.squawk(st)
            save_state(st)
        except Exception as e:
            log(f"!! squawk: {e}")
            if warned != datetime.now(ET).date():
                warned = datetime.now(ET).date()
                try:
                    tg.send_message(tg.target("admin"), f"⚠️ Macro squawk feed problem: {e}")
                except Exception:
                    pass
        time.sleep(20)


JOBS = {"week_ahead": scheduled_week_ahead, "day_ahead": scheduled_day_ahead, "snapshots": scheduled_snapshots, "snapshots_11pm": lambda st: scheduled_snapshots(st, "snapshots_11pm"),
        "snapshots_close": lambda st: scheduled_snapshots(st, "snapshots_close"), "levels_board": scheduled_board, "levels_summary": scheduled_summary,
        "scorecard": scheduled_scorecard}


def due_jobs(st):
    for name, job in tg.config().get("schedule", {}).items():
        if not job.get("enabled", True) or name not in JOBS:
            continue
        now = datetime.now(ZoneInfo(job.get("tz", "America/New_York")))
        days = job.get("days", "mon-fri")
        allowed = DAYS[DAYS.index(days[:3]):DAYS.index(days[-3:]) + 1] if "-" in days else days.split(",")
        if DAYS[now.weekday()] not in allowed:
            continue
        hh, mm = map(int, job["time"].split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        last = st["last_run"].get(name, {})
        if last.get("retry_at") and now < datetime.fromisoformat(last["retry_at"]):
            continue
        if at <= now <= at + CATCH_UP and last.get("date") != f"{now:%Y-%m-%d}":
            yield name


def run_job(name, st):
    now = datetime.now(ZoneInfo(tg.config()["schedule"][name].get("tz", "America/New_York")))
    st["last_run"][name] = {"date": f"{now:%Y-%m-%d}", "started": f"{now:%H:%M:%S}"}
    save_state(st)  # mark first: a crash mid-job must not resend on restart; failures are reported instead
    try:
        res = JOBS[name](st)
        st["last_run"][name]["result"] = res
        if res.startswith("retry"):  # free the slot so due_jobs picks it up again in 5 minutes
            hh, mm = map(int, tg.config()["schedule"][name]["time"].split(":"))
            if now + RETRY > now.replace(hour=hh, minute=mm, second=0, microsecond=0) + CATCH_UP:
                tg.send_message(tg.target("admin"), f"⚠️ Scheduled *{name}* gave up after retrying: {res}")
            else:
                st["last_run"][name].update(date=None, retry_at=(now + RETRY).isoformat())
        log(f"scheduled {name}: {res}")
    except Exception as e:
        st["last_run"][name]["result"] = f"FAILED {e}"
        log(f"!! scheduled {name} failed: {traceback.format_exc()}")
        try:
            tg.send_message(tg.target("admin"), f"⚠️ Scheduled *{name}* failed: {e}")
        except Exception:
            pass
    save_state(st)


# ── updates ──────────────────────────────────────────────────────────────────────────────

def allowed_chat(chat_id):
    cfg = tg.config()
    return chat_id in (cfg.get("group_chat_id"), cfg["admin_chat_id"])


def on_member(upd):
    cfg = tg.config()
    chat, who = upd["chat"], upd["from"]
    status = upd["new_chat_member"]["status"]
    if chat["type"] not in ("group", "supergroup"):
        return
    if status in ("member", "administrator"):
        if who["id"] != cfg["admin_chat_id"]:
            tg.send_message(cfg["admin_chat_id"], f"⚠️ Someone else ({who.get('first_name')}) added the bot to "
                                                  f"“{chat.get('title')}” ({chat['id']}). Ignored.")
            return
        cfg["group_chat_id"] = chat["id"]
        tg.save_config(cfg)
        log(f"group registered: {chat.get('title')} {chat['id']}")
        tg.send_message(chat["id"], "👋 *Proflex bot connected.* Levels alerts, approaching heads-ups and the daily "
                                    "dashboard snapshots will post here. Type /help for commands.")
        tg.send_message(cfg["admin_chat_id"], f"✅ Group registered: “{chat.get('title')}” ({chat['id']}).")
    elif status in ("left", "kicked") and chat["id"] == cfg.get("group_chat_id"):
        cfg["group_chat_id"] = None
        tg.save_config(cfg)
        tg.send_message(cfg["admin_chat_id"], f"⚠️ Bot was removed from “{chat.get('title')}”. "
                                              "Alerts fall back to this private chat until it is re-added.")


HELP = ("*Proflex bot*\n"
        "/snap · all dashboard snapshots\n/movers · daily top movers + drags\n/sectors · sector rotation\n"
        "/rotation · rotation terminal\n/levels · levels board\n/near 0.5 · tickers within 0.5% of a level\n/support · tickers bouncing 2%+ off Run/Worry\n/ceiling · tickers pulling back 2%+ off Clear/TP\n/sr · both (optional: /sr 1.5 10 = 1.5% band, 10 sessions)\n/stale · tickers whose levels are stale\n/staleupdate · update all stale tickers in one form\n"
        "/week · the week ahead (critical macro + earnings)\n/dayahead · today's one-pager\n/today · today's macro with actuals\n"
        "/status · health + schedules\n/update · pick a ticker, fill the form (anyone in the group), /undo to revert\n"
        "/guide · walkthrough: adding a ticker + what the bot does\n\n"
        "Automatic: levels crossings (Run / Worry / Clear / TP), early heads-ups when price gets close to a level, "
        "±3% day swings off a level, the dashboards at 8 PM, 11 PM and 1:30 AM IST on trading days, "
        "The Week Ahead Monday 7:30 PM IST, The Day Ahead weekdays 7 PM IST, and critical US data the moment it prints.")


GUIDE = ("*How to add a ticker* (anyone in this group)\n\n"
         "*1.* Type /update here.\n"
         "*2.* Tap *➕ New ticker* in the list (or tap an existing ticker to change its levels).\n"
         "*3.* The bot replies with a form. Tap Reply on it and fill it in:\n"
         "```\nTicker: PLTR\nRun: 150\nWorry: 158\nDir: Up\nClear: 172\nTP: 180/188\nNote: base breakout\n```\n"
         "• *Run* = bail-out level below, *Worry* = caution level, *Clear* = breakout level, *TP* = target (two targets like 180/188 are fine)\n"
         "• *Dir*: Up / Down / Flat / Flat/Down / Flat/Up / Chop / None\n"
         "• Run, Worry, Dir, Clear and TP are needed for a new ticker; Note is optional\n"
         "• Editing an existing ticker: leave a line blank to keep it, `-` to clear it\n"
         "• Several tickers at once: repeat the block. Remove one: `Ticker: XYZ delete`\n"
         "*4.* Send it. The bot checks every line first: if anything is off it says what, and nothing is saved.\n"
         "*5.* ⏳ Updating → ✅ Levels updated (with what changed and who changed it).\n\n"
         "*What happens next, automatically*\n"
         "• The levels file on Parth's Mac is updated and the TradingView Pine script is rebuilt with the new levels\n"
         "• The full Pine script goes to Parth's DM, one block to copy into TradingView → Pine Editor\n"
         "• Within a minute the ticker is live in alerts: level crossings, early heads-ups when price gets close, "
         "±3% swings off a level, /levels and /near, and the 1:30 AM IST close summary\n"
         "• Made a mistake? /undo puts the levels back to before the last update\n\n"
         "*Everything else the bot does*\n"
         "• Snapshots 8 PM, 11 PM, 1:30 AM IST: movers, sectors, rotation (/snap /movers /sectors /rotation any time)\n"
         "• Levels board at the US open, scorecard after the close\n"
         "• The Day Ahead weekdays 7 PM IST, The Week Ahead Monday 7:30 PM IST (/dayahead /week /today)\n"
         "• Critical US data posted the moment it prints\n"
         "/help for the full command list.")


def on_message(msg):
    chat_id = msg["chat"]["id"]
    if msg.get("migrate_to_chat_id") and chat_id == tg.config().get("group_chat_id"):
        cfg = tg.config()
        cfg["group_chat_id"] = msg["migrate_to_chat_id"]
        tg.save_config(cfg)
        log(f"group migrated to supergroup {cfg['group_chat_id']}")
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        # the group only delivers replies to the bot (privacy mode), so the filled form arrives as a reply
        if can_edit(msg.get("from", {}), chat_id) and levels_edit.looks_like_edit(text):
            levels_update(text, chat_id, msg["message_id"], who=name_of(msg.get("from", {})))
        return
    cmd, *args = text.split()
    cmd = cmd[1:].split("@")[0].lower()
    if cmd == "chatid":
        tg.send_message(chat_id, f"Chat id: {chat_id}")
        return
    if not allowed_chat(chat_id):
        return
    log(f"cmd /{cmd} {args} from {msg['from'].get('first_name')} in {chat_id}")
    snaps = {"snap": (list(snap.JOBS), "all"), "movers": (["daily"], "movers + drags"),
             "sectors": (["sectors"], "sector rotation"), "rotation": (["rotation"], "rotation terminal")}
    if cmd in snaps:
        threading.Thread(target=run_snap, args=(chat_id, *snaps[cmd]), daemon=True).start()
    elif cmd in ("week", "dayahead"):
        def go():
            tg.send_message(chat_id, f"📅 Building The {'Week' if cmd == 'week' else 'Day'} Ahead, about 30 seconds…")
            try:
                png, _ = (macro.week_ahead if cmd == "week" else macro.day_ahead)(send=True, chat=chat_id)
                if not png:
                    tg.send_message(chat_id, macro.today_text())
                log(f"on-demand /{cmd} -> {chat_id}: ok")
            except Exception as e:
                log(f"!! on-demand /{cmd}: {e!r}")
                tg.send_message(chat_id, "⚠️ Could not build it right now, Parth has the details.")
                tg.send_message(tg.target("admin"), f"⚠️ */{cmd} failed*\n`{e!r}`"[:3500])
        threading.Thread(target=go, daemon=True).start()
    elif cmd == "today":
        tg.send_message(chat_id, macro.today_text())
    elif cmd in ("update", "undo", "staleupdate"):
        group = tg.config().get("group_chat_id")
        if group and chat_id != group:
            tg.send_message(chat_id, f"Levels are updated from the group: type /{cmd} there.")
        elif cmd == "staleupdate":
            syms = stale_syms()
            if syms:
                send_form(chat_id, syms, msg["message_id"])
            else:
                tg.send_message(chat_id, "✅ No stale tickers right now, nothing to update.")
        elif cmd == "update" and args:
            send_form(chat_id, [a.upper() for a in args], msg["message_id"])
        elif cmd == "update":
            tg.send_message(chat_id, "✏️ *Update levels*: pick a ticker", reply_markup=levels_edit.picker(),
                            reply_parameters={"message_id": msg["message_id"], "allow_sending_without_reply": True})
        else:
            res = levels_edit.undo()
            if res:
                who = name_of(msg["from"])
                log(f"levels undo by {who}")
                send_pine(f"Proflex Levels Pine (restored by {who})")
                tg.send_message(chat_id, f"↩️ Levels restored to before the last update by {who}. Pine script sent to Parth's DM.")
            else:
                tg.send_message(chat_id, "Nothing to undo.")
    elif cmd == "levels":
        tg.send_message(chat_id, levels_board())
    elif cmd == "stale":
        tg.send_message(chat_id, stale_list())
    elif cmd == "near":
        try:
            within = float(args[0]) if args else 1.0
        except ValueError:
            within = 1.0
        tg.send_message(chat_id, levels_board(within))
    elif cmd in ("support", "ceiling", "sr"):
        try:
            band = float(args[0]) if args else 1.0
            days = int(args[1]) if len(args) > 1 else SR_DAYS
        except ValueError:
            band, days = 1.0, SR_DAYS
        tg.send_message(chat_id, sr_message({"sr": "both"}.get(cmd, cmd), band, days))
    elif cmd == "status":
        cfg, st = tg.config(), load_state()
        lines = [f"Group: {cfg.get('group_chat_id') or 'not set (posting to Parth)'}"]
        for name, job in cfg.get("schedule", {}).items():
            last = st["last_run"].get(name, {})
            lines.append(f"• {name} {job['time']} {TZ_LABEL.get(job.get('tz'), job.get('tz', 'ET'))} {job.get('days')} {'on' if job.get('enabled', True) else 'OFF'}"
                         f" · last {last.get('date', '–')} {last.get('result', '')}")
        tick = LEVELS_DIR / "alert_state.json"
        age = (time.time() - tick.stat().st_mtime) / 60 if tick.exists() else None
        rc = cfg.get("stale_reminder", {})
        lines.append(f"Stale reminder: every {rc.get('every_min', 60)} min, {rc.get('from', '09:00')}–{rc.get('to', '02:00')} "
                     f"{TZ_LABEL.get(rc.get('tz', 'Asia/Kolkata'))} {'on' if rc.get('enabled', True) else 'OFF'} · stale now: {', '.join(stale_syms()) or 'none'}")
        lines.append(f"Levels watcher last tick: {age:.0f} min ago (runs every minute in market hours)" if age is not None
                     else "Levels watcher: no state yet")
        tg.send_message(chat_id, "*Status*\n" + "\n".join(lines))
    elif cmd in ("help", "start"):
        tg.send_message(chat_id, HELP)
    elif cmd in ("guide", "addticker", "howto"):
        tg.send_message(chat_id, GUIDE)


def name_of(user):
    return user.get("first_name") or user.get("username") or str(user.get("id"))


def can_edit(user, chat_id):
    """Anyone in the group can edit levels; in a private chat only Parth (other DMs are ignored anyway)."""
    cfg = tg.config()
    return chat_id == cfg.get("group_chat_id") or user.get("id") == cfg["admin_chat_id"]


def send_form(chat_id, syms, reply_to):
    """The plain-text form as a forced reply to the /update, so only that person's keyboard opens and their reply reaches the bot."""
    tg.send_message(chat_id, levels_edit.template(syms),
                    reply_parameters={"message_id": reply_to, "allow_sending_without_reply": True},
                    reply_markup={"force_reply": True, "selective": True, "input_field_placeholder": "Ticker: …"})


def on_callback(cq):
    """Ticker dropdown taps from /update: lv:SYM opens that ticker's pre-filled form, lv:+ a blank one, lv:x cancels."""
    data, msg = cq.get("data", ""), cq.get("message") or {}
    if not data.startswith("lv:") or not msg:
        return tg.answer_callback(cq["id"])
    chat_id, sym = msg["chat"]["id"], data[3:]
    if not can_edit(cq["from"], chat_id):
        return tg.answer_callback(cq["id"], "Levels are edited from the group.")
    tg.answer_callback(cq["id"])
    if sym == "x":
        tg.edit_message(chat_id, msg["message_id"], "✖️ Levels update cancelled.")
        return
    tg.edit_message(chat_id, msg["message_id"], f"✏️ *Update levels*: {'new ticker' if sym == '+' else sym}")
    reply_to = (msg.get("reply_to_message") or {}).get("message_id", msg["message_id"])
    send_form(chat_id, None if sym == "+" else [sym], reply_to)


def send_pine(caption, changes=""):
    """DM the whole Pine as one plain-text block (phones can't open .pine files): tap it to copy, paste into
    TradingView → Pine Editor. If the watchlist ever outgrows Telegram's 4096-char cap, it comes as a .txt instead."""
    admin = tg.config()["admin_chat_id"]
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    code = levels_edit.PINE.read_text().rstrip()
    tg.send_message(admin, (f"📜 *{caption}*\n{changes}\n\n" if changes else f"📜 *{caption}*\n\n")
                    + "Tap the block below to copy the full script, then TradingView → Pine Editor → select all → paste → Save.")
    if len(code) <= 4000:
        tg._call("sendMessage", {"chat_id": admin, "parse_mode": "HTML", "text": f"<pre>{esc(code)}</pre>"})
    else:
        txt = levels_edit.PINE.with_suffix(".txt")
        txt.write_text(code + "\n")
        tg.send_document(admin, txt, caption="Script is too long for one message: open, select all, copy.")


EDIT_LOCK = threading.Lock()   # two forms sent back to back must not interleave their levels.csv writes


def levels_update(text, chat_id, reply_to=None, who="Parth"):
    rp = {"reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True}} if reply_to else {}
    say = lambda m: tg.send_message(chat_id, m, **rp)
    status = None
    try:
        changes, errs = levels_edit.parse(text)
        if errs:
            say("⚠️ *Nothing saved*, fix these and reply to the form again:\n" + "\n".join(f"• {e}" for e in errs))
            return
        status = say("⏳ Updating levels…")["message_id"]
        with EDIT_LOCK:
            lines = levels_edit.apply(changes)
        if not lines:
            tg.edit_message(chat_id, status, "No changes: those levels are already set.")
            return
        log(f"levels updated from Telegram by {who}: {list(changes)}")
        head = f"✅ *Levels updated* by {who}\n" + "\n".join(lines)
        tg.edit_message(chat_id, status, head + "\n\n⏳ Sending Pine script to Parth's DM…")
        send_pine(f"Proflex Levels Pine · updated by {who}", "\n".join(lines))
        tg.edit_message(chat_id, status, head +
                        "\n\n📜 Pine script sent to Parth's DM. Alerts use the new levels within a minute.")
    except Exception as e:
        log(f"!! levels update: {traceback.format_exc()}")
        msg = "⚠️ Levels update failed, nothing changed. Parth has the details."
        try:
            tg.edit_message(chat_id, status, msg) if status else say(msg)
        except Exception:
            pass
        tg.send_message(tg.config()["admin_chat_id"], f"⚠️ *Levels update failed*\n`{e!r}`"[:3500])


POOL = ThreadPoolExecutor(max_workers=6)


def handle(upd):
    try:
        if "my_chat_member" in upd:
            on_member(upd["my_chat_member"])
        elif "callback_query" in upd:
            on_callback(upd["callback_query"])
        elif "message" in upd:
            on_message(upd["message"])
    except Exception:
        log(f"!! update failed: {traceback.format_exc()}")


def main():
    st = load_state()
    try:
        tg.set_commands(COMMANDS)
    except Exception as e:
        log(f"!! setMyCommands: {e}")
    threading.Thread(target=squawk_loop, args=(st,), daemon=True).start()
    threading.Thread(target=stale_reminder_loop, args=(st,), daemon=True).start()
    log("bot up")
    while True:
        try:
            for name in list(due_jobs(st)):
                threading.Thread(target=run_job, args=(name, st), daemon=True).start()
                time.sleep(1)
            for upd in tg.get_updates(st.get("offset"), timeout=10):
                st["offset"] = upd["update_id"] + 1
                save_state(st)
                POOL.submit(handle, upd)   # handlers run off the poll loop so a slow one never delays the next message
        except Exception as e:
            log(f"!! poll loop: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
