#!/usr/bin/env python3
"""Local Proflex Levels console on http://localhost:8765. Paste your list, one click: Claude parses -> Pine built -> copied."""
import csv, json, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from build import FIELDS, build, here
from parse import parse

PORT = 8765

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Proflex Levels</title>
<style>
:root{--bg:#f7f3ea;--card:#fff;--ink:#1c1c1c;--mute:#777;--acc:#76b900}
@media (prefers-color-scheme:dark){:root{--bg:#131722;--card:#1e222d;--ink:#d1d4dc;--mute:#888}}
body{background:var(--bg);color:var(--ink);font:15px -apple-system,system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px}
textarea{width:100%;height:260px;font:13px ui-monospace,monospace;background:var(--card);color:var(--ink);border:1px solid #ccc;border-radius:8px;padding:10px;box-sizing:border-box}
button{font-size:15px;padding:10px 18px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer;margin:10px 8px 10px 0}
button.alt{background:#555}button:disabled{opacity:.5}
table{border-collapse:collapse;width:100%;background:var(--card);margin-top:8px}td,th{border-bottom:1px solid #ddd3;padding:6px 8px;text-align:left}
#msg{color:var(--mute)}pre{background:var(--card);padding:10px;border-radius:8px;overflow:auto;font-size:12px;max-height:300px}
tr.stale td{background:#ff98001f}.badge{background:#ff9800;color:#fff;border-radius:6px;padding:2px 7px;font-size:12px;white-space:nowrap}
td input{width:70px;font:13px ui-monospace,monospace}td button{margin:0;padding:4px 10px;font-size:13px}
.Up{color:#26a69a}.Down,.Flat\\/Down{color:#ef5350}.Chop,.Flat{color:#ff9800}
</style></head><body>
<h2>Proflex Levels</h2>
<textarea id="dump" placeholder="Run | Worry | Stock | Dir | Clear | Take Profit&#10;756 | 765 | SPY | Flat/down | 770 | 790 ..."></textarea>
<button id="go" onclick="go()">Parse → Build → Copy</button>
<button class="alt" onclick="window.open('https://www.tradingview.com/chart/')">Open TradingView</button>
<span id="msg"></span>
<table id="t"></table>
<div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px"><b>Pine script</b><button class="alt" id="cp" onclick="cp()">Copy to clipboard</button></div>
<pre id="pine"></pre>
<h3 style="margin-top:28px">Stale levels</h3>
<div id="smsg" style="color:var(--mute)">Telegram alert when price runs the default % above the top Clear/TP or the default % below Run. Override = custom upside stale price.</div>
<p>Stale at ± % past Clear/TP or Run: <input id="pct" type="number" step="0.1" style="width:70px">
&nbsp; Approaching heads-up within <input id="apct" type="number" step="0.05" min="0" style="width:70px">% of a level
<button onclick="saveStale()">Save stale levels</button></p>
<table id="st"></table>
<script>
const $=id=>document.getElementById(id);
async function cp(){await navigator.clipboard.writeText($('pine').textContent);$('cp').textContent='Copied ✓';setTimeout(()=>$('cp').textContent='Copy to clipboard',1500);}
const F=['run','worry','dir','clear','take_profit','note'];
let NOW={};
function badge(s){const x=NOW[s];if(!x)return '';
  const why=x.price?`${x.price} · ${x.name.startsWith('STALE')?'hit stale '+x.level:(x.pct>0?'+':'')+x.pct+'% '+(x.pct>0?'above ':'below ')+x.name+' '+x.level}`:'';
  return ` <span class="badge" title="${why}">⚠️ STALE${why?' · '+why:''}</span>`}
function show(j){
  NOW=j.stale_now||{};
  const n=Object.keys(NOW).length;
  $('t').innerHTML=(n?`<caption style="text-align:left;padding:6px 0"><span class="badge">⚠️ ${n} stale</span> edit those rows — other tickers stay as they are</caption>`:'')+
    '<tr><th>Symbol</th><th>Run</th><th>Worry</th><th>Dir</th><th>Clear</th><th>Take Profit</th><th>Note</th><th></th></tr>'+
    j.rows.map(r=>`<tr class="${NOW[r.symbol]?'stale':''}" data-s="${r.symbol}"><td><b>${r.symbol}</b>${badge(r.symbol)}</td><td>${r.run}</td><td>${r.worry}</td><td class="${r.dir}">${r.dir}</td><td>${r.clear}</td><td>${r.take_profit||''}</td><td>${r.note||''}</td>
      <td><button class="alt" onclick="edit(this)">Edit</button></td></tr>`).join('');
  $('pine').textContent=j.pine;
}
function edit(b){
  const tr=b.closest('tr'),r=ROWS.find(x=>x.symbol===tr.dataset.s);
  [...tr.children].slice(1,7).forEach((td,i)=>td.innerHTML=`<input data-f="${F[i]}" value="${(r[F[i]]||'').replace(/"/g,'&quot;')}">`);
  b.textContent='Save';b.className='';b.onclick=()=>saveRow(tr);
}
async function saveRow(tr){
  const row={symbol:tr.dataset.s};tr.querySelectorAll('input').forEach(i=>row[i.dataset.f]=i.value.trim());
  $('msg').textContent='Saving '+row.symbol+'…';
  const j=await (await fetch('/row',{method:'POST',body:JSON.stringify(row)})).json();
  $('msg').textContent=j.msg; if(j.ok){ROWS=j.rows;STALE=j.stale;show(j);showStale();}
}
async function go(){
  $('go').disabled=true;$('msg').textContent='Claude is parsing…';
  try{const j=await (await fetch('/run',{method:'POST',body:JSON.stringify({text:$('dump').value})})).json();
    $('msg').textContent=j.msg; if(j.ok){ROWS=j.rows;STALE=j.stale;show(j);showStale();}}
  catch(e){$('msg').textContent=e}
  $('go').disabled=false;
}
let ROWS=[];
function topLevel(r){const v=[r.clear,r.take_profit].join('/').replace(/\\+/g,'').split('/').map(parseFloat).filter(x=>!isNaN(x));return v.length?Math.max(...v):null}
function showStale(){
  const pct=parseFloat($('pct').value)||0;
  $('st').innerHTML='<tr><th>Symbol</th><th>Run</th><th>Stale below</th><th>Clear / TP</th><th>Auto stale above</th><th>Override</th><th>Alerting above</th></tr>'+
    ROWS.map(r=>{const t=topLevel(r),auto=t?(t*(1+pct/100)).toFixed(2):'–',o=STALE.levels[r.symbol]??'',rn=parseFloat(r.run),dn=isNaN(rn)?'–':(rn*(1-pct/100)).toFixed(2);
      return `<tr><td><b>${r.symbol}</b></td><td>${r.run}</td><td>${dn}</td><td>${r.clear}${r.take_profit?' / '+r.take_profit:''}</td><td>${auto}</td>
      <td><input data-s="${r.symbol}" type="number" step="0.01" value="${o}" placeholder="auto" style="width:90px" oninput="this.closest('tr').lastElementChild.textContent=this.value||'${auto}'"></td>
      <td>${o||auto}</td></tr>`}).join('');
}
let STALE={pct:3,levels:{}};
async function saveStale(){
  const levels={};document.querySelectorAll('#st input').forEach(i=>{if(i.value!=='')levels[i.dataset.s]=parseFloat(i.value)});
  const j=await (await fetch('/stale',{method:'POST',body:JSON.stringify({pct:parseFloat($('pct').value),approach_pct:parseFloat($('apct').value),levels})})).json();
  $('smsg').textContent=j.msg; if(j.ok){STALE=j.stale;showStale();}
}
fetch('/levels').then(r=>r.json()).then(j=>{ROWS=j.rows;show(j);STALE=j.stale;$('pct').value=STALE.pct;$('apct').value=STALE.approach_pct;showStale();});
$('pct').oninput=showStale;
setInterval(()=>{if(!document.querySelector('#t input'))fetch('/levels').then(r=>r.json()).then(j=>{ROWS=j.rows;show(j)})},60000);
</script></body></html>"""


def state():
    rows = list(csv.DictReader(open(here / "levels.csv")))
    pine = (here / "proflex-levels.pine").read_text() if (here / "proflex-levels.pine").exists() else ""
    return {"rows": rows, "pine": pine, "stale": stale(), "stale_now": stale_now()}


def stale_now():
    """Tickers the alerts job flagged stale (alert_state.json "_stale"), until their levels are edited."""
    f = here / "alert_state.json"
    try:
        marks = json.loads(f.read_text()).get("_stale", {})
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in marks.items():
        sym, name = k.split(":", 1)
        out[sym] = v if isinstance(v, dict) else {"t": v, "name": name}
    return out


def save_row(row):
    """Replace one ticker's row in levels.csv (append if new). Every other row is left exactly as it was."""
    sym = row["symbol"].strip().upper()
    rows = list(csv.DictReader(open(here / "levels.csv")))
    new = {k: (row.get(k) or "").strip() for k in FIELDS} | {"symbol": sym}
    if not (new["run"] and new["worry"] and new["clear"]):
        raise ValueError(f"{sym}: Run, Worry and Clear are required")
    idx = [i for i, r in enumerate(rows) if r["symbol"].strip().upper() == sym]
    if idx:
        rows[idx[0]] = new
    else:
        rows.append(new)
    with open(here / "levels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    # a per-ticker stale price was set for the old levels: drop it so the new levels use the default %
    cfg = stale()
    if cfg["levels"].pop(sym, None) is not None:
        (here / "stale.json").write_text(json.dumps(cfg, indent=1))
    # clear the dashboard marker now; alerts.py also re-seeds this ticker on its next tick (levels changed)
    f = here / "alert_state.json"
    try:
        st = json.loads(f.read_text())
        st["_stale"] = {k: v for k, v in st.get("_stale", {}).items() if k.split(":")[0] != sym}
        f.write_text(json.dumps(st, indent=1))
    except (OSError, ValueError):
        pass
    return sym


def stale():
    f = here / "stale.json"
    cfg = json.loads(f.read_text()) if f.exists() else {}
    return {"pct": cfg.get("pct", 1.5), "approach_pct": cfg.get("approach_pct", 0.2), "levels": cfg.get("levels", {})}


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/levels":
            self.send(200, json.dumps(state()))
        else:
            self.send(404, "{}")

    def do_POST(self):
        if self.path == "/stale":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cfg = {"pct": float(body["pct"]), "approach_pct": float(body.get("approach_pct", stale()["approach_pct"])),
                       "levels": {k.upper(): float(v) for k, v in body.get("levels", {}).items()}}
                (here / "stale.json").write_text(json.dumps(cfg, indent=1))
                # separate from levels.csv: re-pasting weekly levels keeps overrides, and alert zones aren't re-seeded
                self.send(200, json.dumps({"ok": True, "stale": cfg,
                    "msg": f"Saved · default {cfg['pct']:g}% · approaching {cfg['approach_pct']:g}% · {len(cfg['levels'])} override(s) · alerts use them within a minute"}))
            except Exception as e:
                self.send(200, json.dumps({"ok": False, "msg": f"Error: {e}"}))
            return
        if self.path == "/row":
            try:
                sym = save_row(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                msg = build()
                subprocess.run("pbcopy", input=(here / "proflex-levels.pine").read_text(), text=True)
                self.send(200, json.dumps({"ok": True, **state(),
                    "msg": f"{sym} updated · {msg} · copied to clipboard · alerts use the new {sym} levels within a minute"}))
            except Exception as e:
                self.send(200, json.dumps({"ok": False, "msg": f"Error: {e}"}))
            return
        if self.path != "/run":
            return self.send(404, "{}")
        try:
            text = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["text"]
            parse(text)
            msg = build()
            subprocess.run("pbcopy", input=(here / "proflex-levels.pine").read_text(), text=True)
            # alerts.py (yfinance watcher) re-reads levels.csv on its next tick, so alerts follow the new levels
            msg += " · copied to clipboard · alerts pick up new levels within a minute"
            self.send(200, json.dumps({"ok": True, "msg": msg, **state()}))
        except Exception as e:
            self.send(200, json.dumps({"ok": False, "msg": f"Error: {e}"}))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Proflex Levels on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
