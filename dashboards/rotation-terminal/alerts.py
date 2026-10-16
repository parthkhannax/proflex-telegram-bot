#!/usr/bin/env python3
"""Rotation Terminal — intraday raw-move email alerts.

Watches the same intraday feed the terminal draws and emails you the moment a
top-10 stock does something on its OWN price (not vs the index):

  • FLIP  — crosses its previous close intraday (green -> red on the day, or red -> green)
  • SWING — its intraday high-to-low range hits the threshold (default 2%)

One email per stock per trigger-type per session (deduped), so a stock that
flips at 10am and then swings 2% at 2pm gives you two alerts, not twenty.

Config lives in alert_config.json (gitignored — holds your Gmail app password).
Run continuously:   python3 alerts.py
One-shot (testing):  python3 alerts.py --once
Test the SMTP path:  python3 alerts.py --test-email
"""
import json, os, sys, time, smtplib, ssl, urllib.request
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "alert_config.json")
STATE_PATH = os.path.join(HERE, "alert_state.json")
ET = timezone(timedelta(hours=-4))          # US Eastern (EDT); good enough for the gate

DEFAULTS = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",                        # your gmail address
    "smtp_app_password": "",                # 16-char Google App Password (no spaces)
    "email_from": "",                       # defaults to smtp_user if blank
    "email_to": "",                         # where alerts land
    "api_url": "http://localhost:11001/api/rotation?refresh=1",
    "swing_pct": 2.0,                       # intraday raw range that counts as a swing
    "poll_minutes": 15,
    "market_hours_only": True,              # only act 09:30-16:00 ET, Mon-Fri
}


def load_cfg():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        cfg.update(json.load(open(CFG_PATH)))
    # env override so nothing secret has to sit on disk if you prefer
    for k in ("smtp_user", "smtp_app_password", "email_to"):
        cfg[k] = os.environ.get("ROT_" + k.upper(), cfg[k])
    if not cfg["email_from"]:
        cfg["email_from"] = cfg["smtp_user"]
    return cfg


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            pass
    return {"session": None, "sent": {}}     # sent: {ticker: ["flip","swing"]}


def save_state(st):
    json.dump(st, open(STATE_PATH, "w"), indent=2)


def market_open(cfg):
    if not cfg["market_hours_only"]:
        return True
    now = datetime.now(ET)
    if now.weekday() >= 5:                    # Sat/Sun
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


def flags_for(path, swing):
    """Flag a stock only when it swings from - to + (or + to -) BY >= `swing`%:
    it must cross its previous close AND the full -to-+ amplitude must clear the
    threshold. `side` records which way it is currently pointing so a later
    cross back the other way counts as a fresh alert."""
    raw = [p["raw"] for p in path["pts"]]
    if not raw:
        return None
    mn, mx = min(raw), max(raw)
    if not (mn < 0 and mx > 0 and (mx - mn) >= swing):
        return None
    last = raw[-1]
    side = "to_plus" if last >= 0 else "to_minus"
    return {"range": mx - mn, "side": side}


def fetch(cfg):
    with urllib.request.urlopen(cfg["api_url"], timeout=60) as r:
        return json.loads(r.read().decode())


def build_email(cfg, hits, session):
    """hits: list of (path, flag). Returns (subject, text, html)."""
    lead = hits[0][0]
    subj = f"Rotation alert · {lead['ticker']} {fmt(lead['raw_last'])}"
    if len(hits) > 1:
        subj += f" +{len(hits)-1} more"
    lines, rows = [], []
    for p, f in hits:
        dir_ = "red → green" if f["side"] == "to_plus" else "green → red"
        why = f"{dir_}, {f['range']:.1f}% swing"
        lines.append(f"  {p['ticker']:5} {fmt(p['raw_last'])} on the day  ({why})")
        color = "#0ca30c" if p["raw_last"] >= 0 else "#d03b3b"
        rows.append(
            f"<tr><td style='padding:8px 14px;font-weight:700'>{p['ticker']}</td>"
            f"<td style='padding:8px 14px;color:{color};font-weight:600;text-align:right'>{fmt(p['raw_last'])}</td>"
            f"<td style='padding:8px 14px;color:#52514e'>{why}</td></tr>")
    text = (f"Rotation Terminal — intraday alert ({session})\n\n"
            + "\n".join(lines)
            + "\n\nOpen the terminal: http://localhost:11001/\n")
    html = (
        "<div style=\"font-family:Inter,Arial,sans-serif;max-width:560px\">"
        "<h2 style=\"font-family:Georgia,serif;color:#14161a;margin:0 0 4px\">Rotation alert</h2>"
        f"<div style=\"color:#8a8880;font-size:13px;margin-bottom:14px\">Intraday raw moves · session {session}</div>"
        "<table style=\"border-collapse:collapse;width:100%;font-size:14px;border:1px solid #e5e2da;border-radius:8px\">"
        + "".join(rows) +
        "</table>"
        "<p style=\"margin:16px 0 0;font-size:12.5px;color:#8a8880\">"
        "Triggered on each stock&#39;s own price vs yesterday&#39;s close. "
        "<a href=\"http://localhost:11001/\">Open the Rotation Terminal &rarr;</a></p></div>")
    return subj, text, html


def send_email(cfg, subj, text, html):
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = cfg["email_from"]
    msg["To"] = cfg["email_to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx) as s:
        s.login(cfg["smtp_user"], cfg["smtp_app_password"])
        s.send_message(msg)


def fmt(v):
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def check_and_alert(cfg, log=print):
    if not (cfg["smtp_user"] and cfg["smtp_app_password"] and cfg["email_to"]):
        log("!! alert_config.json missing smtp_user / smtp_app_password / email_to — skipping")
        return
    try:
        data = fetch(cfg)
    except Exception as e:
        log(f"!! fetch failed ({e}) — is the terminal running on :11001?")
        return "retry"                        # tell the loop to back off briefly, not a full cycle
    m = data.get("morning")
    if not m or not m.get("paths"):
        log("no intraday paths yet")
        return
    session = m["session"]
    st = load_state()
    if st.get("session") != session:         # new trading day → reset dedupe
        st = {"session": session, "sent": {}}

    new_hits = []
    for p in m["paths"]:
        f = flags_for(p, cfg["swing_pct"])
        if not f:
            continue
        already = set(st["sent"].get(p["ticker"], []))
        if f["side"] in already:
            continue                          # already emailed this crossing direction
        new_hits.append((p, f))
        st["sent"][p["ticker"]] = sorted(already | {f["side"]})

    if not new_hits:
        log(f"{session}: nothing new to alert")
        return
    new_hits.sort(key=lambda x: -x[1]["range"])
    subj, text, html = build_email(cfg, new_hits, session)
    try:
        send_email(cfg, subj, text, html)
    except Exception as e:
        log(f"!! email send failed: {e}")
        return
    save_state(st)
    log(f"{session}: emailed {cfg['email_to']} — " + ", ".join(p["ticker"] for p, _ in new_hits))


def main():
    cfg = load_cfg()
    if "--test-email" in sys.argv:
        send_email(cfg, "Rotation alert · test email",
                   "If you can read this, SMTP works.",
                   "<b>If you can read this, SMTP works.</b>")
        print(f"sent test email to {cfg['email_to']}")
        return
    if "--once" in sys.argv:
        check_and_alert(cfg)
        return
    print(f"Rotation alerts running — every {cfg['poll_minutes']} min, "
          f"market-hours-only={cfg['market_hours_only']}, swing>={cfg['swing_pct']}%")
    while True:
        ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
        wait = cfg["poll_minutes"] * 60
        if market_open(cfg):
            r = check_and_alert(cfg, log=lambda s, ts=ts: print(f"[{ts}] {s}"))
            if r == "retry":                  # server not up yet — retry soon, don't lose 15 min
                wait = 60
        else:
            print(f"[{ts}] market closed — idle")
        time.sleep(wait)


if __name__ == "__main__":
    main()
