"""Update levels from Telegram with a fixed text format (no LLM): /update sends the current rows, Parth edits and
sends them back in his DM, levels.csv is updated, the Pine is rebuilt and sent to his DM. /undo restores the previous file.

Accepted lines (one ticker per line, several lines per message):
  AMD, worry, 625                                 TICKER, FIELD, NEW VALUE (the format /update asks for)
  AMD, worry, 625, clear, 670                     more field/value pairs on the same line
  AMD | 600 | 625 | Chop | 660 | 680 | 640+      full row: SYM | Run | Worry | Dir | Clear | TP | Note
  AMD worry 625 clear 670                         same as the comma form, with spaces
  AMD, delete                                     remove the ticker
Clear and TP may hold two targets, e.g. 341/348. Every line is checked first; nothing is saved if any line is wrong.
"""
import csv, re, shutil, sys
from pathlib import Path

LEVELS_DIR = Path(__file__).resolve().parent.parent / "tv-levels"
sys.path.insert(0, str(LEVELS_DIR))
from build import FIELDS, build      # noqa: E402  (tv-levels/build.py)
import server                        # noqa: E402  (tv-levels/server.py: save_row keeps stale state in step)

CSV = LEVELS_DIR / "levels.csv"
PREV = LEVELS_DIR / "levels.csv.prev"
PINE = LEVELS_DIR / "proflex-levels.pine"
COLS = ["run", "worry", "dir", "clear", "take_profit", "note"]
ALIAS = {"run": "run", "worry": "worry", "dir": "dir", "direction": "dir", "clear": "clear", "tp": "take_profit",
         "take_profit": "take_profit", "target": "take_profit", "note": "note"}
DIRS = {d.lower(): d for d in ["Up", "Down", "Flat", "Flat/Down", "Flat/Up", "Chop", "None"]}
NUM = r"\d+(\.\d+)?"
SYM = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def rows():
    return {r["symbol"].strip().upper(): r for r in csv.DictReader(open(CSV)) if (r.get("symbol") or "").strip()}


FORM_KEYS = [("Ticker", None), ("Run", "run"), ("Worry", "worry"), ("Dir", "dir"), ("Clear", "clear"),
             ("TP", "take_profit"), ("Note", "note")]


def picker():
    """Inline keyboard for /update: one button per ticker in levels.csv (4 a row), then New ticker."""
    syms = sorted(rows())
    kb = [[{"text": s, "callback_data": f"lv:{s}"} for s in syms[i:i + 4]] for i in range(0, len(syms), 4)]
    kb.append([{"text": "➕ New ticker", "callback_data": "lv:+"}, {"text": "✖️ Cancel", "callback_data": "lv:x"}])
    return {"inline_keyboard": kb}


def template(syms=None):
    """Plain-text form posted in the group. With tickers, each block comes pre-filled with today's levels."""
    cur = rows()
    blocks = []
    for s in (syms or [""]):
        r = cur.get(s, {})
        blocks.append("\n".join(f"{k}: {s if c is None else (r.get(c) or '')}".rstrip() for k, c in FORM_KEYS))
    return ("✏️ *Update levels*: reply to this message with the form filled in. Blank = keep as is, `-` = clear it.\n"
            "```\n" + "\n\n".join(blocks) + "\n```\n"
            "More tickers: repeat the block. Dir: " + " / ".join(DIRS.values()) + "\n"
            "Remove a ticker: `Ticker: XYZ delete`")


def form_rows(text):
    """Ticker:/Run:/Worry:... blocks -> pipe rows for parse(); blanks fall back to the current levels."""
    cur, out, blk = rows(), [], None
    names = {k.lower(): c for k, c in FORM_KEYS} | {"direction": "dir", "take profit": "take_profit",
                                                    "target": "take_profit", "symbol": None, "sym": None}

    def flush():
        if blk and blk.get("sym"):
            sym, base = blk["sym"], cur.get(blk["sym"]) or {"dir": "None"}
            if blk.get("delete"):
                out.append(f"{sym} delete")
                return
            vals = [blk[c] if c in blk else (base.get(c) or "") for c in COLS]
            out.append(" | ".join([sym] + ["" if v == "-" else v for v in vals]))

    for raw in text.splitlines():
        key, colon, val = raw.strip().strip("`").partition(":")
        if not colon or key.strip().lower() not in names:
            continue
        col, val = names[key.strip().lower()], val.strip()
        if col is None:
            flush()
            sym, _, rest = val.partition(" ")
            blk = {"sym": sym.upper(), "delete": rest.strip().lower() in ("delete", "remove")}
        elif blk is not None and val:
            blk[col] = val
    flush()
    return out


def is_form(text):
    return any(re.match(r"^\s*`*\s*(ticker|symbol|sym)\s*:", l, re.I) for l in text.splitlines())


def is_header(line):
    return line.strip().strip("`").upper().startswith(("SYM", "TICKER"))


def looks_like_edit(text):
    """True for a filled form, or a first line that starts with a ticker followed by '|', a field name or 'delete'."""
    if is_form(text):
        return True
    lines = [l.strip().strip("`") for l in text.strip().splitlines() if l.strip().strip("`") and not is_header(l)]
    first = lines[0] if lines else ""
    m = re.match(r"^([A-Za-z][A-Za-z0-9.\-]{0,9})\s*(\||[\s,]+(\w+))", first)
    return bool(m) and (m.group(2) == "|" or (m.group(3) or "").lower() in set(ALIAS) | {"delete", "remove"})


def check(sym, r):
    for c in ("run", "worry", "clear"):
        if not r.get(c):
            return f"{sym}: {c.title()} is required"
    for c in ("run", "worry"):
        if not re.fullmatch(NUM, r[c]):
            return f"{sym}: {c.title()} must be a number, got “{r[c]}”"
    for c in ("clear", "take_profit"):
        if r.get(c) and not re.fullmatch(rf"{NUM}(/{NUM})?", r[c]):
            return f"{sym}: {'TP' if c == 'take_profit' else 'Clear'} must be a number or two like 341/348, got “{r[c]}”"
    if r.get("dir", "").lower() not in DIRS:
        return f"{sym}: Dir must be one of {', '.join(DIRS.values())}, got “{r.get('dir')}”"
    r["dir"] = DIRS[r["dir"].lower()]


def parse(text):
    """-> (changes {SYM: row or None for delete}, errors [str]). Header and blank lines are skipped."""
    cur, out, errs = rows(), {}, []
    if is_form(text):
        text = "\n".join(form_rows(text))
        if not text:
            return {}, ["No ticker filled in: put it after Ticker:"]
    for raw in text.strip().splitlines():
        line = raw.strip().strip("`")
        if not line or is_header(line):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.split("|")]
            sym = cells[0].upper()
            if len(cells) < 6:
                errs.append(f"{sym or line}: need at least SYM | Run | Worry | Dir | Clear | TP")
                continue
            r = dict(zip(COLS, (cells[1:] + [""] * 6)[:6]))
            r["dir"] = r["dir"] or "None"
        else:
            if "," in line:                           # AMD, worry, 625, clear, 670  (a cell may also be "worry 625")
                cells = [c.strip() for c in line.split(",") if c.strip()]
                parts, sep = [cells[0]], ", "
                for c in cells[1:]:
                    k, _, v = c.partition(" ")
                    parts += [k, v.strip()] if v.strip() and k.lower() in ALIAS else [c]
            else:
                parts, sep = line.split(), " "
            sym = parts[0].upper()
            if len(parts) == 2 and parts[1].lower() in ("delete", "remove"):
                if sym in cur:
                    out[sym] = None
                else:
                    errs.append(f"{sym}: not on the list, nothing to delete")
                continue
            base = cur.get(sym) or {"dir": "None"}     # new ticker: check() below insists on run, worry, clear
            r = {c: base.get(c) or "" for c in COLS} | {k: v for k, v in (out.get(sym) or {}).items() if k in COLS}
            toks, i = parts[1:], 0
            while i < len(toks):
                key = ALIAS.get(toks[i].lower())
                if not key or i + 1 >= len(toks):
                    errs.append(f"{sym}: can't read “{sep.join(toks[i:])}” (fields: run worry dir clear tp note)")
                    break
                if key == "note":                     # note takes the rest of the line
                    r[key], i = sep.join(toks[i + 1:]), len(toks)
                else:
                    r[key] = "" if toks[i + 1] == "-" else toks[i + 1]
                    i += 2
        if not SYM.match(sym):
            errs.append(f"“{sym}” doesn't look like a ticker")
            continue
        err = check(sym, r)
        if err:
            errs.append(err)
        else:
            out[sym] = r
    return out, errs


def fmt_row(r):
    tp = f" · TP {r['take_profit']}" if r.get("take_profit") else ""
    note = f" · {r['note']}" if r.get("note") else ""
    return f"Run {r['run']} · Worry {r['worry']} · {r['dir']} · Clear {r['clear']}{tp}{note}"


def apply(changes):
    """Write the changed rows, rebuild the Pine. -> summary lines. Unchanged rows are skipped (no alert re-seed)."""
    cur, lines = rows(), []
    real = {s: r for s, r in changes.items()
            if r is None or s not in cur or any((cur[s].get(c) or "") != r[c] for c in COLS)}
    if not real:
        return []
    shutil.copy(CSV, PREV)
    for sym, r in real.items():
        if r is None:
            keep = [x for s, x in rows().items() if s != sym]     # re-read: earlier rows in this batch are saved
            with open(CSV, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows(keep)
            cur = rows()
            lines.append(f"🗑 *{sym}* removed")
            continue
        old = cur.get(sym)
        server.save_row({"symbol": sym, **r})
        if old:
            diff = [f"{'TP' if c == 'take_profit' else c.title()} {old.get(c) or '–'} → {r[c] or '–'}"
                    for c in COLS if (old.get(c) or "") != r[c]]
            lines.append(f"✏️ *{sym}* " + " · ".join(diff))
        else:
            lines.append(f"🆕 *{sym}* {fmt_row(r)}")
    build()
    return lines


def undo():
    if not PREV.exists():
        return None
    back = PREV.read_text()
    PREV.write_text(CSV.read_text())         # /undo twice = redo
    CSV.write_text(back)
    return build()
