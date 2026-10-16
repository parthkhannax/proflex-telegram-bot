#!/usr/bin/env python3
"""
Proflex chart engine — locked brand chrome, open-ended chart ideas.

The RULE this module encodes:
  * The FRAME never changes  (accent bar, kicker/title/subtitle, palette, fonts,
    source line, PROFLEX wordmark, 1200px width, auto-crop, GitHub hosting).
  * The CHART IDEA changes every edition. Compose primitives, or write a custom
    `svg` string and pass it to page() — anything you draw inherits the frame.

Usage
-----
    import sys; sys.path.insert(0, "vendor")
    from proflex_charts import *

    svg = grouped_bars([("10-Year", [("Jul", 4.58, NAVY), ("Aug", 4.71, GOLD)])], 4.3, 5.5)
    save("01-rates", page("Macro Watch — Rates", "Headline", "Standfirst sentence.",
                          svg, "U.S. Treasury, data through Aug 19, 2026"))
    render_all()          # -> PNGs next to the HTML, auto-cropped
    print(host("01-rates"))   # -> public raw.githubusercontent URL

Every primitive returns an SVG string. Mix them, nest them, or ignore them.
"""
import os
import pathlib, subprocess, shutil

# ------------------------------------------------------------------ palette
DARK   = "#141c25"   # headings, axis
NAVY   = "#1a2f4a"   # primary series
GOLD   = "#a68a3e"   # Proflex accent / "our" series
GOLDL  = "#d4bc6a"
RED    = "#c0563a"   # bad / peak / risk
GREEN  = "#2f6f4e"   # good outcome
GREY   = "#c9c5b8"   # comparison / prior
MUTED  = "#7a828c"
STEEL  = "#3b6b8a"
TEAL   = "#0d7d72"
PLUM   = "#6b4d7a"
SERIES = [NAVY, GOLD, STEEL, TEAL, PLUM, RED]   # use in order for n-series charts

W = 1200          # card width — never change
CW = 1108         # usable chart width inside the card padding

OUTDIR = pathlib.Path.cwd()
def set_outdir(path):
    global OUTDIR
    OUTDIR = pathlib.Path(path); OUTDIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ frame
LOGO = "https://48102387.fs1.hubspotusercontent-na1.net/hubfs/48102387/Proflex_Square200xLogo_TransparentBlack.png"

# Canonical Proflex type system — identical to charts/shell.py in the gazette and
# weekly skills. Libre Franklin headlines, Public Sans body, IBM Plex Mono metadata.
# Playfair Display and Inter are RETIRED brand-wide. Do not reintroduce them.
_FONTS = ("https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;600;700;800;900"
          "&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap")

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{--head:'Libre Franklin','Helvetica Neue',Arial,sans-serif;
--sans:'Public Sans','Helvetica Neue',Arial,sans-serif;
--mono:'IBM Plex Mono',ui-monospace,monospace;
--dark:#141c25;--goldl:#a68a3e;--mut:rgba(20,28,37,.52);--fnt:rgba(20,28,37,.38);--ln:rgba(20,28,37,.10)}
body{background:#f3f3ee;font-family:var(--sans);-webkit-font-smoothing:antialiased}
.card{width:1200px;background:#fff;border:1px solid var(--ln);border-radius:6px;overflow:hidden}
.inner{padding:30px 44px 24px}
.hdr{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:14px}
.hl{flex:1}
.accent{width:150px;height:6px;background:var(--goldl);border-radius:2px;margin-bottom:14px}
.kicker{font-family:var(--mono);font-size:13px;font-weight:600;letter-spacing:.5px;
text-transform:uppercase;color:var(--mut)}
.logo{width:62px;height:62px;object-fit:contain;flex-shrink:0}
h1{font-family:var(--head);font-weight:900;font-size:54px;letter-spacing:-1.7px;line-height:1.02;color:var(--dark)}
h1 .g{color:var(--goldl)}
.sub{font-size:19px;font-weight:400;color:rgba(20,28,37,.70);margin-top:12px;
line-height:1.34;max-width:940px;margin-bottom:22px}
.foot{display:flex;justify-content:space-between;align-items:center;gap:26px;
border-top:1px solid var(--ln);padding:14px 44px 20px}
.src{font-family:var(--mono);font-size:11px;color:var(--fnt);line-height:1.55;max-width:700px}
.fbrand{display:flex;align-items:center;gap:12px;flex-shrink:0}
.fbrand img{width:48px;height:48px;object-fit:contain}
.fbrand .w{font-family:var(--mono);font-weight:700;font-size:19px;letter-spacing:2.6px;color:var(--dark)}
.fbrand .u{font-family:var(--mono);font-size:11.5px;color:var(--fnt);margin-top:3px;letter-spacing:.2px}
text{font-family:var(--sans)}
.lbl{font-size:15px;fill:var(--dark);font-weight:700}
.val{font-family:var(--head);font-weight:900;fill:var(--dark);font-variant-numeric:tabular-nums}
.cap{font-family:var(--mono);font-size:12px;fill:var(--fnt)}
"""

def page(kicker, title, standfirst, svg, source):
    """Wraps any SVG in the locked Proflex card. `source` is MANDATORY."""
    assert source and source.strip(), "every chart must carry a source line"
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<link href="{_FONTS}" rel="stylesheet"><style>{_CSS}</style></head><body>'
            f'<div class="card"><div class="inner">'
            f'<div class="hdr"><div class="hl"><div class="accent"></div>'
            f'<div class="kicker">{kicker}</div></div><img class="logo" src="{LOGO}"></div>'
            f'<h1>{title}</h1>'
            f'<div class="sub">{standfirst}</div>{svg}</div>'
            f'<div class="foot"><div class="src">Source: {source}</div>'
            f'<div class="fbrand"><img src="{LOGO}"><div><div class="w">PROFLEX</div>'
            f'<div class="u">proflexfinance.com</div></div></div></div></div></body></html>')

def svg_open(h, top_pad=16):
    return f'<svg width="{CW}" height="{h+top_pad}" viewBox="0 -{top_pad} {CW} {h+top_pad}">'
SVG_CLOSE = "</svg>"

# ------------------------------------------------------------------ primitives
def grouped_bars(groups, ymin, ymax, h=330, fmt="{:.3f}%", axis_fmt="{:.1f}%", pad_l=64):
    """groups: [(group_label, [(bar_label, value, colour), ...]), ...]"""
    o=[svg_open(h+70)]
    n=len(groups); gw=(CW-pad_l-20)/n
    for i in range(6):
        v=ymin+(ymax-ymin)*i/5; y=h-(v-ymin)/(ymax-ymin)*h
        o.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{CW-20}" y2="{y:.1f}" stroke="#eceae2"/>')
        o.append(f'<text x="{pad_l-12}" y="{y+4:.1f}" text-anchor="end" class="cap">{axis_fmt.format(v)}</text>')
    for gi,(glabel,bars) in enumerate(groups):
        gx=pad_l+gi*gw; bw=min(78,(gw-46)/max(len(bars),1))
        sx=gx+(gw-(len(bars)*bw+(len(bars)-1)*16))/2
        for bi,(bl,v,c) in enumerate(bars):
            x=sx+bi*(bw+16); bh=(v-ymin)/(ymax-ymin)*h; y=h-bh
            o.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{c}" rx="2"/>')
            o.append(f'<text x="{x+bw/2:.1f}" y="{y-11:.1f}" text-anchor="middle" class="val" font-size="17">{fmt.format(v)}</text>')
            o.append(f'<text x="{x+bw/2:.1f}" y="{h+21:.1f}" text-anchor="middle" class="cap">{bl}</text>')
        o.append(f'<text x="{gx+gw/2:.1f}" y="{h+46:.1f}" text-anchor="middle" class="lbl" font-weight="600">{glabel}</text>')
    o.append(f'<line x1="{pad_l}" y1="{h}" x2="{CW-20}" y2="{h}" stroke="{DARK}" stroke-width="1.5"/>{SVG_CLOSE}')
    return "".join(o)

def hbar_ladder(rows, vmax, label_w=330, marker=None, footnote=None, fmt="{}%"):
    """rows: [(label, value, colour)]. marker: (value, colour, caption) draws a dashed hurdle line."""
    h=22+len(rows)*44+40
    o=[svg_open(h+(60 if footnote else 0))]
    bar_w=CW-label_w-120
    for i,(lab,v,c) in enumerate(rows):
        y=22+i*44; bw=v/vmax*bar_w
        o.append(f'<text x="{label_w-16}" y="{y+16}" text-anchor="end" class="lbl" font-size="13.5">{lab}</text>')
        o.append(f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="24" fill="{c}" rx="2"/>')
        o.append(f'<text x="{label_w+bw+10:.1f}" y="{y+18}" class="val" font-size="17" fill="{c if c!=GREY else MUTED}">{fmt.format(v)}</text>')
    if marker:
        mv,mc,mcap=marker; x=label_w+mv/vmax*bar_w; yl=22+len(rows)*44
        o.append(f'<line x1="{x:.1f}" y1="10" x2="{x:.1f}" y2="{yl+6}" stroke="{mc}" stroke-width="1.5" stroke-dasharray="5,4"/>')
        if mcap: o.append(f'<text x="{x+8:.1f}" y="8" class="cap" fill="{mc}">{mcap}</text>')
    if footnote:
        yb=22+len(rows)*44+46
        for j,line in enumerate(footnote):
            o.append(f'<text x="0" y="{yb+j*22}" class="lbl" font-size="14" fill="{DARK}">{line}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def stat_cards(cards, y=10, h=128, per_row=4):
    """cards: [(caption, big_value, colour)] — the KPI strip."""
    rows=(len(cards)+per_row-1)//per_row
    o=[svg_open(y+rows*(h+28))]
    gap=32; bw=(CW-gap*(per_row-1))/per_row
    for i,(cap,val,c) in enumerate(cards):
        r,cix=divmod(i,per_row); x=cix*(bw+gap); yy=y+r*(h+28)
        o.append(f'<rect x="{x:.1f}" y="{yy}" width="{bw:.1f}" height="{h}" fill="#fbfaf6" stroke="{c}" stroke-width="1.5" rx="3"/>')
        o.append(f'<rect x="{x:.1f}" y="{yy}" width="{bw:.1f}" height="5" fill="{c}"/>')
        o.append(f'<text x="{x+bw/2:.1f}" y="{yy+78}" text-anchor="middle" class="val" font-size="46" fill="{c}">{val}</text>')
        o.append(f'<text x="{x+bw/2:.1f}" y="{yy+108}" text-anchor="middle" class="cap">{cap}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def blocks(n_by_colour, title=None, note_left=None, note_right=None, bw=34, gap=12, bh=60):
    """Unit blocks for votes/counts. n_by_colour: [(count, colour), ...]"""
    o=[svg_open(140)]
    if title: o.append(f'<text x="0" y="20" class="lbl" font-weight="700" fill="{DARK}" font-size="15">{title}</text>')
    i=0
    for count,c in n_by_colour:
        for _ in range(count):
            o.append(f'<rect x="{i*(bw+gap)}" y="36" width="{bw}" height="{bh}" fill="{c}" rx="2"/>'); i+=1
    if note_left:  o.append(f'<text x="0" y="{36+bh+26}" class="cap">{note_left}</text>')
    if note_right: o.append(f'<text x="{CW}" y="{36+bh+26}" text-anchor="end" class="cap">{note_right}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def timeline(points, title=None, x0=0, x1=None, y=70):
    """points: [(label, note, colour, fraction_0_to_1)]"""
    x1 = x1 or CW
    o=[svg_open(y+90)]
    if title: o.append(f'<text x="{x0}" y="20" class="lbl" font-weight="700" fill="{DARK}" font-size="15">{title}</text>')
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#dcd9cf" stroke-width="3"/>')
    for lab,note,c,f in points:
        x=x0+(x1-x0)*f
        o.append(f'<circle cx="{x:.1f}" cy="{y}" r="8" fill="{c}"/>')
        o.append(f'<text x="{x:.1f}" y="{y+28}" text-anchor="middle" class="lbl" font-weight="600">{lab}</text>')
        if note: o.append(f'<text x="{x:.1f}" y="{y+48}" text-anchor="middle" class="cap" fill="{c}">{note}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def line_series(series, ylab_fmt="{:.1f}%", h=340, pad_l=64, xlabels=None, annotate=None):
    """series: [(name, colour, [(x_index, y_value), ...])] — for real time series.
    annotate: [(x_index, y_value, text, colour[, dy])] — dy shifts the label; negative = above"""
    ys=[v for _,_,pts in series for _,v in pts]
    ymin,ymax=min(ys),max(ys); pad=(ymax-ymin)*0.15 or 1; ymin-=pad; ymax+=pad
    xs=[i for _,_,pts in series for i,_ in pts]; xmin,xmax=min(xs),max(xs)
    o=[svg_open(h+72)]
    px=lambda i: pad_l+(i-xmin)/max(xmax-xmin,1)*(CW-pad_l-24)
    py=lambda v: h-(v-ymin)/(ymax-ymin)*h
    for k in range(6):
        v=ymin+(ymax-ymin)*k/5; y=py(v)
        o.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{CW-24}" y2="{y:.1f}" stroke="#eceae2"/>')
        o.append(f'<text x="{pad_l-12}" y="{y+4:.1f}" text-anchor="end" class="cap">{ylab_fmt.format(v)}</text>')
    for name,c,pts in series:
        d=" ".join(("M" if j==0 else "L")+f"{px(i):.1f},{py(v):.1f}" for j,(i,v) in enumerate(pts))
        o.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2.5" stroke-linejoin="round"/>')
        li,lv=pts[-1]
        o.append(f'<circle cx="{px(li):.1f}" cy="{py(lv):.1f}" r="4.5" fill="{c}"/>')
        o.append(f'<text x="{px(li)-8:.1f}" y="{py(lv)-14:.1f}" text-anchor="end" class="lbl" font-weight="700" fill="{c}">{name}</text>')
    if xlabels:
        for i,lab in xlabels: o.append(f'<text x="{px(i):.1f}" y="{h+22}" text-anchor="middle" class="cap">{lab}</text>')
    for a in (annotate or []):
        i,v,txt,c = a[:4]
        dy = a[4] if len(a) > 4 else -16      # negative = label above the point
        o.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="5" fill="none" stroke="{c}" stroke-width="2"/>')
        o.append(f'<text x="{px(i):.1f}" y="{py(v)+dy:.1f}" text-anchor="middle" class="cap" fill="{c}">{txt}</text>')
    o.append(f'<line x1="{pad_l}" y1="{h}" x2="{CW-24}" y2="{h}" stroke="{DARK}" stroke-width="1.5"/>{SVG_CLOSE}')
    return "".join(o)

def before_after(rows, title=None, fmt="{}"):
    """Dumbbell: rows = [(label, before_val, after_val, before_txt, after_txt, colour)] with values 0..1."""
    h=(40 if title else 0)+len(rows)*58+20
    o=[svg_open(h)]
    if title: o.append(f'<text x="0" y="20" class="lbl" font-weight="700" fill="{DARK}" font-size="15">{title}</text>')
    x0,x1=300,CW-150
    for i,(lab,b,a,bt,at,c) in enumerate(rows):
        y=(46 if title else 6)+i*58
        xb,xa=x0+(x1-x0)*b, x0+(x1-x0)*a
        o.append(f'<text x="{x0-18}" y="{y+5}" text-anchor="end" class="lbl" font-size="13.5">{lab}</text>')
        o.append(f'<line x1="{xb:.1f}" y1="{y}" x2="{xa:.1f}" y2="{y}" stroke="{c}" stroke-width="3" opacity="0.35"/>')
        o.append(f'<circle cx="{xb:.1f}" cy="{y}" r="7" fill="{GREY}"/><circle cx="{xa:.1f}" cy="{y}" r="7" fill="{c}"/>')
        o.append(f'<text x="{xb:.1f}" y="{y-14}" text-anchor="middle" class="cap">{bt}</text>')
        o.append(f'<text x="{xa:.1f}" y="{y-14}" text-anchor="middle" class="val" font-size="15" fill="{c}">{at}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def text_block(lines, x=0, y=20, size=14, leading=22, colour=DARK):
    o=[svg_open(y+len(lines)*leading+10)]
    for j,l in enumerate(lines):
        o.append(f'<text x="{x}" y="{y+j*leading}" class="lbl" font-size="{size}" fill="{colour}">{l}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def quote_rules(items, x=0, y=20):
    """items: [(quote, attribution)] rendered with the gold left rule."""
    o=[svg_open(y+len(items)*58+10)]
    for i,(q,a) in enumerate(items):
        yy=y+i*58
        o.append(f'<rect x="{x}" y="{yy-22}" width="5" height="46" fill="{GOLD}"/>')
        o.append(f'<text x="{x+20}" y="{yy}" class="lbl" font-size="15" fill="{DARK}" font-style="italic">{q}</text>')
        o.append(f'<text x="{x+20}" y="{yy+21}" class="cap">{a}</text>')
    o.append(SVG_CLOSE); return "".join(o)

def stack(*svgs):
    """Vertically concatenate primitives into one figure."""
    return '<div style="display:block">' + "".join(f'<div style="margin-bottom:6px">{s}</div>' for s in svgs) + "</div>"

# ------------------------------------------------------------------ render / crop / host
_PAGES = {}
def save(name, html):
    _PAGES[name]=html
    p=OUTDIR/f"{name}.html"; p.write_text(html); return p

_CHROME=os.environ.get("CHROME","/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
def render_all(height=1400):
    chrome=_CHROME if pathlib.Path(_CHROME).exists() else shutil.which("chromium") or shutil.which("google-chrome")
    assert chrome, "no Chrome/Chromium found for rendering"
    for name in _PAGES:
        subprocess.run([chrome,"--headless","--disable-gpu","--hide-scrollbars",
                        f"--window-size=1200,{height}",
                        f"--screenshot={OUTDIR}/{name}.png", str(OUTDIR/f'{name}.html')],
                       capture_output=True)
        _crop(OUTDIR/f"{name}.png")
        print("rendered", name, _size(OUTDIR/f"{name}.png"))

def _size(p):
    from PIL import Image; return Image.open(p).size

def _crop(path, bg=(243,243,238)):
    """Chrome renders a fixed window; always crop to the card or you ship whitespace."""
    from PIL import Image
    im=Image.open(path).convert("RGB"); w,h=im.size; px=im.load()
    off=lambda t: any(abs(t[k]-bg[k])>3 for k in range(3))
    bottom=next((y+1 for y in range(h-1,-1,-1) if any(off(px[x,y]) for x in range(0,w,7))), h)
    right =next((x+1 for x in range(w-1,-1,-1) if any(off(px[x,y]) for y in range(0,bottom,7))), w)
    im.crop((0,0,right,bottom)).save(path)

HOST_SCRIPT=str(pathlib.Path(__file__).resolve().parent / "host_image.sh")
def host(name):
    """Publishes to parthkhannax/x-post-assets and returns the public raw URL."""
    r=subprocess.run(["bash",HOST_SCRIPT,str(OUTDIR/f"{name}.png")],capture_output=True,text=True)
    return r.stdout.strip().splitlines()[-1]

def host_all():
    return {n: host(n) for n in _PAGES}

def email_img(url, alt, caption):
    """The email-safe embed. Caption carries the source; never ship a chart without one."""
    return ('<div style="margin:0 0 22px;">'
            f'<img src="{url}" alt="{alt}" width="100%" '
            'style="width:100%;max-width:100%;height:auto;display:block;border:1px solid #e6e4dc;"/>'
            f'<p style="margin:8px 0 0;font-size:12px;color:#7a828c;line-height:1.5;"><em>{caption}</em></p></div>')


# ================================================================== BIG TYPE (house standard since Sep 2026)
# Parth, Sep 17 2026: "BIG FONT BIG CHART NO WHITE SPACE" — and after seeing the result:
# "Charts look amazing ... I love it." This is now the DEFAULT way to build every chart.
# Cards render 1200px CSS at 2x device pixels; in a ~600px email every size halves, so:
# axis text >= 22px, labels >= 25px, values >= 28px (hero values 40-66px), headline 66px.
# Worked example of every form: scripts/gold-standard/september-2026-build_charts.py
BIG_CSS = """<style>
.inner{padding:22px 34px 8px!important}
.hdr{margin-bottom:8px!important}
.accent{margin-bottom:10px!important}
.kicker{font-size:18px!important}
.logo{width:70px!important;height:70px!important}
h1{font-size:66px!important;letter-spacing:-2px!important;line-height:1.0!important}
.sub{font-size:27px!important;line-height:1.28!important;margin:10px 0 10px!important;max-width:1120px!important;color:rgba(20,28,37,.78)!important}
.foot{padding:10px 34px 14px!important}
.src{font-size:15px!important;max-width:820px!important;color:rgba(20,28,37,.55)!important}
.fbrand img{width:44px!important;height:44px!important}
.fbrand .w{font-size:20px!important}
svg text{font-family:'Public Sans',Arial,sans-serif}
.ax{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:500}.ax:not([fill]){fill:#5d646c}
.lb{font-size:25px;font-weight:700}.lb:not([fill]){fill:#141c25}
.vl{font-family:'Libre Franklin',Arial,sans-serif;font-weight:900}.vl:not([fill]){fill:#141c25}
.nt{font-size:22px;font-weight:600}
</style>"""
CW_BIG = 1132  # usable chart width inside the tighter big-type padding

def big_page(kicker, title, standfirst, svg, source):
    """page() with the big-type house spec. Use this, not page(), for Income Insider charts."""
    return page(kicker, title, standfirst, BIG_CSS + svg, source)

def big_svg(h):
    return f'<svg width="{CW_BIG}" height="{h}" viewBox="0 0 {CW_BIG} {h}" style="display:block">'

CHROME = _CHROME if pathlib.Path(_CHROME).exists() else (shutil.which("google-chrome") or shutil.which("chromium") or _CHROME)
def render_all_2x(names=None, height=1500):
    """Render at 2x device scale (crisp in email), then auto-crop. Default for big-type charts."""
    for name in (names or list(_PAGES)):
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=2", "--virtual-time-budget=4000",
                        f"--window-size=1200,{height}", f"--screenshot={OUTDIR}/{name}.png",
                        str(OUTDIR / f"{name}.html")], capture_output=True)
        _crop(OUTDIR / f"{name}.png")
        print("rendered", name, _size(OUTDIR / f"{name}.png"))
