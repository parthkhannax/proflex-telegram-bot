#!/usr/bin/env python3
"""Sync native TradingView price alerts to levels.csv.

Diffs the desired alert set (priority order, capped at the plan limit) against
tv_alerts.json, then has headless Claude call the TradingView MCP to delete
stale alerts and create new ones. Unchanged alerts are left alone.
Run: python3 tv_sync.py [--dry]
"""
import csv, datetime, json, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
LEVELS = HERE / "levels.csv"
IDS = HERE / "tv_alerts.json"
LOG = HERE / "tv_sync.log"
LIMIT = 20  # TradingView plan limit on price alerts
EXCHANGE = {"SPY": "AMEX", "SLV": "AMEX"}  # everything else defaults to NASDAQ
TIERS = [("run", "RUN"), ("worry", "WORRY"), ("clear", "CLEAR"), ("take_profit", "TP")]


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def first(cell):
    m = re.search(r"\d+(\.\d+)?", cell or "")
    return float(m.group()) if m else None


def desired():
    """Ordered (tv_symbol, label, price) list, levels.csv row order = priority."""
    out = []
    for r in csv.DictReader(open(LEVELS)):
        sym = r["symbol"].strip().upper()
        tv = f"{EXCHANGE.get(sym, 'NASDAQ')}:{sym}"
        for key, name in TIERS:
            p = first(r.get(key))
            if p is not None:
                out.append((tv, f"{name} {p:g}", p))
    return out[:LIMIT]


def current():
    d = json.loads(IDS.read_text()) if IDS.exists() else {}
    return {(sym, label): aid for sym, m in d.items() if not sym.startswith("_") for label, aid in m.items()}


def plan():
    want = desired()
    have = current()
    keys = {(s, l) for s, l, _ in want}
    delete = {k: v for k, v in have.items() if k not in keys}
    create = [(s, l, p) for s, l, p in want if (s, l) not in have]
    keep = {k: v for k, v in have.items() if k in keys}
    return keep, delete, create


PROMPT = """You are syncing TradingView price alerts. Use ONLY the TradingView MCP tools.
1. If DELETE ids are listed, call mcp-tv-delete-alert once with all of them.
2. For each CREATE row, call mcp-tv-create-alert with: symbol, price, condition="cross",
   name="Proflex <SYM> <LABEL>", message="Proflex Levels: <SYM> crossed <LABEL>",
   resolution="1", auto_deactivate=true, popup=true, mobile_push=true, email=false,
   expiration="{exp}".
Then reply with ONLY a JSON object, no prose, no code fences:
{{"created": [{{"symbol": "...", "label": "...", "alert_id": 123}}], "errors": ["..."]}}

DELETE ids: {delete}
CREATE rows (symbol | label | price):
{create}
"""


def run(dry=False):
    keep, delete, create = plan()
    log(f"plan: keep {len(keep)}, delete {len(delete)}, create {len(create)}")
    if dry or (not delete and not create):
        return
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = PROMPT.format(
        exp=exp,
        delete=list(delete.values()) or "none",
        create="\n".join(f"{s} | {l} | {p:g}" for s, l, p in create) or "none",
    )
    out = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools",
         "mcp__tradingview__mcp-tv-create-alert", "mcp__tradingview__mcp-tv-delete-alert"],
        capture_output=True, text=True, timeout=600, cwd=HERE,
    )
    m = re.search(r"\{.*\}", out.stdout, re.S)
    if not m:
        log(f"FAILED: no JSON from claude. stdout={out.stdout[-500:]!r} stderr={out.stderr[-500:]!r}")
        sys.exit(1)
    res = json.loads(m.group())
    ids = {"_note": f"Native TradingView alerts, synced {datetime.date.today()} by tv_sync.py from levels.csv. Plan limit = {LIMIT}."}
    for (s, l), aid in keep.items():
        ids.setdefault(s, {})[l] = aid
    for c in res.get("created", []):
        ids.setdefault(c["symbol"], {})[c["label"]] = c["alert_id"]
    IDS.write_text(json.dumps(ids, indent=1))
    log(f"done: created {len(res.get('created', []))}, deleted {len(delete)}, errors {res.get('errors') or 'none'}")


if __name__ == "__main__":
    run(dry="--dry" in sys.argv)
