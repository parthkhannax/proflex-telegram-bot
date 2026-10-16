#!/usr/bin/env python3
"""Mirror the scheduled Week Ahead / Day Ahead one-pagers to X (ProflexFinance) via Buffer.

One tweet (<=280, never a thread): two lines + cashtags + hashtags, with the PNG attached. The image is hosted
through the proflex-x-post skill's host_image.sh (public x-post-assets repo) and posted with its buffer_post.py.

  python3 xpost.py week|day          print the tweet that would go out (no posting)
  python3 xpost.py week|day --post   render + post now (shareNow)
"""
import os
from pathlib import Path
import subprocess, sys, tempfile
from datetime import datetime

import macro

SKILL = os.environ.get("XPOST_SCRIPTS", str(Path(__file__).resolve().parent.parent / "vendor"))
LIMIT = 280
# base label -> hashtag, in priority order
TAGS = [("FOMC", "#FOMC"), ("Nonfarm Payrolls", "#NFP"), ("CPI", "#CPI"), ("PCE", "#PCE"), ("PPI", "#PPI"),
        ("Retail Sales", "#RetailSales"), ("GDP", "#GDP"), ("ISM", "#ISM"), ("JOLTS", "#JOLTS"), ("Fed Chair", "#Fed")]


def base(label):
    """'Core CPI m/m' -> 'CPI', 'Retail Control' -> 'Retail Sales': one name per release family."""
    b = label.replace(" m/m", "").replace(" y/y", "").replace("Core ", "")
    return {"Retail Control": "Retail Sales", "Initial Jobless Claims": "Jobless Claims", "UMich 1-yr Infl. Expectations": "UMich Sentiment",
            "Avg Hourly Earnings": "Nonfarm Payrolls", "Unemployment Rate": "Nonfarm Payrolls", "FOMC Press Conference": "FOMC Rate Decision"}.get(b, b)


def families(evs):
    """Tier-1 releases collapsed to families, in time order: [(family, first event of it)]."""
    out = {}
    for e in evs:
        if e["tier"] == 1:
            out.setdefault(base(e["label"]), e)
    return list(out.items())


def item(fam, e, with_cons):
    """Week line uses the family ('CPI'); the day line names the exact print its consensus belongs to ('Core CPI')."""
    if fam.startswith("Fed Chair"):
        return e["label"].replace(" speaks", "")
    if not with_cons:
        return fam
    name = e["label"].replace(" m/m", "").replace(" y/y", "")
    return f"{name} (cons {e['cons']})" if e["cons"] else name


def tags(days):
    seen = " ".join(f for _, evs, _ in days for f, _ in families(evs))
    cash = ["$SPY", "$QQQ"]
    if any(k in seen for k in ("CPI", "PPI", "PCE", "FOMC", "Fed Chair", "Payrolls")):
        cash.append("$TLT")
    cash += [f"${s}" for _, _, er in days for s, _ in er][:4]
    hash_ = [h for k, h in TAGS if k in seen][:2] + ["#Macro"]
    return " ".join(dict.fromkeys(cash + hash_))


def fit(line1, parts, tail):
    """Drop trailing detail items until the tweet fits."""
    while True:
        text = f"{line1}\n{'; '.join(parts)}. {tail}" if parts else f"{line1}\n{tail}"
        if len(text) <= LIMIT or not parts:
            return text
        parts = parts[:-1]


def week_text(days):
    d0, d1 = days[0][0], days[-1][0]
    line1 = f"The Week Ahead, {d0:%b %-d} to {d1:%-d}: {macro.headline(days)}."
    parts = []
    for d, evs, er in days:
        fams = [item(f, e, False) for f, e in families(evs)]
        fams += [f"${s} earnings" for s, _ in er]
        if fams:
            parts.append(f"{d:%a} " + " + ".join(fams[:4]))
    return fit(line1, parts, tags(days))


def day_text(days):
    d, evs, er = days[0]
    line1 = f"The Day Ahead, {d:%a %b %-d}: {macro.headline(days)}."
    by_time = {}
    for f, e in families(evs):
        by_time.setdefault(e["t"], []).append(item(f, e, True))
    parts = [f"{macro.et12(t)} ET {' + '.join(v)}" for t, v in by_time.items()]
    parts += [f"${s} {'before the open' if w == 'BMO' else 'after the close' if w == 'AMC' else 'earnings'}" for s, w in er]
    return fit(line1, parts, tags(days))


def post(png, text):
    """Host the PNG publicly, then publish one tweet with it. Returns Buffer's JSON line."""
    url = subprocess.run(["bash", f"{SKILL}/host_image.sh", str(png)], capture_output=True, text=True,
                         timeout=180, check=True).stdout.strip().splitlines()[-1]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    r = subprocess.run([sys.executable, f"{SKILL}/buffer_post.py", "--text-file", f.name, "--image-url", url,
                        "--mode", "shareNow"], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout.strip()


def mirror(st, kind, png, days):
    """Post once per kind per day (state x_posted), so a job retry or restart never double-tweets."""
    key = f"{kind}|{days[0][0]}"
    done = st.setdefault("x_posted", [])
    if key in done:
        return "already posted"
    text = week_text(days) if kind == "week" else day_text(days)
    done.append(key)  # mark first: a half-finished post must not repeat
    st["x_posted"] = done[-60:]
    post(png, text)
    return "posted"


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "week"
    png, days = macro.week_ahead() if kind == "week" else macro.day_ahead()
    if not png:
        sys.exit("nothing critical today, no tweet")
    text = week_text(days) if kind == "week" else day_text(days)
    print(text, f"\n[{len(text)} chars] {png}")
    if "--post" in sys.argv:
        print(post(png, text))
