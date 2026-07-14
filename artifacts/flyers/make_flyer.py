import base64, pathlib
import segno, io

SCRATCH = pathlib.Path(__file__).parent
LOGO = pathlib.Path('/Users/janoskonig/shadematch_python/static/img/semmelweis-logo.png')

logo_b64 = base64.b64encode(LOGO.read_bytes()).decode()

buf = io.BytesIO()
segno.make('https://shadestudy.com', error='m').save(buf, kind='svg', xmldecl=False,
    svgns=True, dark='#1F2937', light=None, border=0)
qr_svg = buf.getvalue().decode()
qr_svg = qr_svg.replace('<svg ', '<svg style="width:100%;height:100%;display:block" ', 1)
qr_svg = qr_svg.replace('width="25" height="25"', 'viewBox="0 0 25 25"')

def drop_svg(color, cls=''):
    return (f'<svg class="dropsvg {cls}" viewBox="0 0 24 30">'
            f'<path d="M12 1 C12 1 3 13 3 19.5 a9 9 0 0 0 18 0 C21 13 12 1 12 1 Z" '
            f'fill="{color}" stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>'
            f'<ellipse cx="8.6" cy="18.5" rx="2.4" ry="3.4" fill="rgba(255,255,255,0.35)"/></svg>')

CHECK_SVG = ('<svg class="checksvg" viewBox="0 0 24 24">'
             '<path d="M5 12.5l4.5 4.5 9.5-10" stroke="#FFFFFF" stroke-width="3.4" '
             'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

TARGET = '#7FB98A'

STRINGS = {
    'hu': {
        'badge': 'játék&nbsp;+&nbsp;kutatás',
        'h1': 'Ki tudod keverni<br><em>ezt a színt?</em> <span class="chip"></span>',
        'lead': 'Sárga, kék és fehér cseppek. Egy célszín. Napi két perc, telefonon vagy '
                'számítógépen — és minden körrel a Semmelweis Egyetem színlátás-kutatását segíted.',
        'mixlabel': 'Ez a zsályazöld receptje. <b>A következőt már te fejted meg.</b>',
        'challenge': 'Könnyűnek tűnik? <em>Bizonyítsd be.</em>',
        'chips': ['🆓 Ingyenes', '⏱️ Napi 2 perc', '🔐 Álneves'],
        'ctakicker': 'Szkenneld be. Játssz.',
        'ctahint': 'A böngésződben fut — azonnal indul.',
        'ethics': 'S.H.A.D.E. — Study of Human Accuracy in Digital Experiments<br>'
                  'Semmelweis Egyetem · Etikai engedély: SE RKEB 167/2025',
    },
    'en': {
        'badge': 'a&nbsp;game&nbsp;+&nbsp;a&nbsp;study',
        'h1': 'Can you mix<br><em>this color?</em> <span class="chip"></span>',
        'lead': 'Yellow, blue and white drops. One target color. Two minutes a day, on your phone '
                'or your computer — and every round you play powers color-vision research at Semmelweis University.',
        'mixlabel': 'That’s the recipe for sage green. <b>The next one is yours to crack.</b>',
        'challenge': 'Looks easy? <em>Prove it.</em>',
        'chips': ['🆓 Free', '⏱️ 2 min a day', '🔐 Private'],
        'ctakicker': 'Scan it. Play.',
        'ctahint': 'Runs right in your browser — ready in seconds.',
        'ethics': 'S.H.A.D.E. — Study of Human Accuracy in Digital Experiments<br>'
                  'Semmelweis University · Ethics approval: SE RKEB 167/2025',
    },
}

def body(lang):
    s = STRINGS[lang]
    chips = ''.join(f'<span class="pill">{c}</span>' for c in s['chips'])
    return f"""
<div class="page">
  <div class="wash"></div>
  <div class="rain">
    {drop_svg('#E85D75', 'r1')}{drop_svg('#F7C948', 'r2')}{drop_svg('#4A90D9', 'r3')}
    {drop_svg('#7FB98A', 'r4')}{drop_svg('#9B6FC3', 'r5')}{drop_svg('#F08A3C', 'r6')}
  </div>

  <div class="content">
    <div class="brandrow">
      <div class="dropmark">
        {drop_svg('#E85D75', 'dm')}{drop_svg('#F7C948', 'dm')}{drop_svg('#4A90D9', 'dm')}
      </div>
      <div class="wordmark">ShadeMatch</div>
      <div class="badge">{s['badge']}</div>
    </div>

    <h1>{s['h1']}</h1>

    <p class="lead">{s['lead']}</p>

    <div class="mixcard">
      <div class="mixrow">
        <span class="ingredient">{drop_svg('#F7C948')}<b>3×</b></span>
        <span class="op">+</span>
        <span class="ingredient">{drop_svg('#2D6CDF')}<b>2×</b></span>
        <span class="op">+</span>
        <span class="ingredient">{drop_svg('#FDFDFB')}<b>1×</b></span>
        <span class="op">=</span>
        <span class="target" style="background:{TARGET}">{CHECK_SVG}</span>
      </div>
      <div class="mixlabel">{s['mixlabel']}</div>
    </div>

    <div class="challenge">{s['challenge']}</div>

    <div class="pills">{chips}</div>

    <div class="cta">
      <div class="ctatext">
        <div class="ctakicker">{s['ctakicker']}</div>
        <div class="ctaurl">shadestudy.com</div>
        <div class="ctahint">{s['ctahint']}</div>
      </div>
      <div class="qrbox">{qr_svg}</div>
    </div>

    <div class="footer">
      <img class="selogo" src="data:image/png;base64,{logo_b64}" alt="Semmelweis">
      <div class="ethics">{s['ethics']}</div>
    </div>
  </div>
</div>
"""

CSS = """
  * { margin:0; padding:0; box-sizing:border-box; }
  :root {
    --bg:#F8F6F3; --ink:#22252B; --muted:#5B5B5B;
    --accent:#4A90D9; --accent-deep:#2F6FB5; --target:#7FB98A;
  }
  html { font-family:'Nunito', -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; }
  .page { width:{inner_w}mm; height:{inner_h}mm; background:var(--bg); position:relative; overflow:hidden; color:var(--ink); }
  .wash { position:absolute; inset:0;
    background:
      radial-gradient(52mm 52mm at 106% -6%, rgba(247,201,72,0.45), rgba(247,201,72,0) 70%),
      radial-gradient(46mm 46mm at -6% 62%, rgba(127,185,138,0.35), rgba(127,185,138,0) 70%),
      radial-gradient(44mm 44mm at 108% 104%, rgba(74,144,217,0.28), rgba(74,144,217,0) 70%),
      radial-gradient(30mm 30mm at -4% -4%, rgba(232,93,117,0.22), rgba(232,93,117,0) 70%);
  }
  .rain { position:absolute; inset:0; }
  .rain .dropsvg { position:absolute; opacity:0.9; }
  .rain .r1 { width:4.4mm; top:34mm; right:9mm; transform:rotate(14deg); }
  .rain .r2 { width:6mm; top:47mm; right:15mm; transform:rotate(-10deg); opacity:0.85; }
  .rain .r3 { width:3.6mm; top:60mm; right:8mm; transform:rotate(8deg); opacity:0.8; }
  .rain .r4 { width:4.6mm; top:73mm; right:17mm; transform:rotate(-16deg); opacity:0.75; }
  .rain .r5 { width:3.4mm; top:41mm; right:24mm; transform:rotate(20deg); opacity:0.7; }
  .rain .r6 { width:4mm; top:57mm; right:26mm; transform:rotate(-6deg); opacity:0.65; }
  .content { position:relative; height:100%; padding:{pad_t}mm {pad_s}mm {pad_b}mm; display:flex; flex-direction:column; }

  .brandrow { display:flex; align-items:center; gap:2.6mm; }
  .dropmark { display:flex; align-items:flex-end; }
  .dropsvg { width:9mm; height:auto; }
  .dropsvg.dm { width:5.6mm; margin-right:-1.8mm; }
  .wordmark { font-weight:900; font-size:7mm; letter-spacing:-0.1mm; margin-left:2mm; }
  .badge { margin-left:auto; background:#FFFFFF; border:0.35mm solid #E3DED6; color:var(--accent-deep);
           font-weight:800; font-size:3.1mm; padding:1.1mm 3mm; border-radius:5mm; letter-spacing:0.2mm; text-transform:uppercase; }

  h1 { font-size:13.6mm; line-height:1.04; font-weight:900; letter-spacing:-0.35mm; margin-top:5.5mm; }
  h1 em { font-style:normal; color:var(--accent-deep); }
  .chip { display:inline-block; width:11.5mm; height:11.5mm; border-radius:2.8mm; background:var(--target);
          border:0.5mm solid rgba(0,0,0,0.10); vertical-align:-1.4mm; }
  .lead { font-size:4.1mm; line-height:1.5; color:var(--muted); margin-top:4mm; font-weight:600; max-width:118mm; }

  .mixcard { background:#FFFFFF; border:0.4mm solid #ECE7DF; border-radius:4.5mm; padding:4mm 6mm 3.2mm;
             margin-top:4.5mm; }
  .mixrow { display:flex; align-items:center; justify-content:center; gap:3.4mm; }
  .ingredient { display:flex; flex-direction:column; align-items:center; gap:0.8mm; }
  .ingredient .dropsvg { width:10mm; }
  .ingredient b { font-size:3.6mm; color:#6B655B; }
  .op { font-size:6.6mm; font-weight:900; color:#B9B2A6; }
  .target { width:16mm; height:16mm; border-radius:3.6mm; display:flex; align-items:center; justify-content:center;
            border:0.4mm solid rgba(0,0,0,0.08); }
  .checksvg { width:9.5mm; height:9.5mm; }
  .mixlabel { text-align:center; font-size:3.3mm; color:var(--muted); margin-top:2.4mm; font-weight:700; white-space:nowrap; }

  .challenge { margin-top:4.5mm; text-align:center; font-size:6.2mm; font-weight:900; letter-spacing:-0.15mm; white-space:nowrap; }
  .challenge em { font-style:normal; color:var(--accent-deep); }

  .pills { display:flex; justify-content:center; gap:3mm; margin-top:3.5mm; }
  .pill { background:#FFFFFF; border:0.4mm solid #ECE7DF; border-radius:6mm; padding:1.8mm 4.2mm;
          font-size:3.6mm; font-weight:800; color:#3A3A3A; }

  .cta { margin-top:auto; background:linear-gradient(135deg, var(--accent) 0%, var(--accent-deep) 100%);
         border-radius:5mm; padding:6mm 7mm; display:flex; align-items:center; gap:6mm; color:#FFFFFF; }
  .ctatext { flex:1; }
  .ctakicker { font-size:3.7mm; font-weight:800; text-transform:uppercase; letter-spacing:0.45mm; opacity:0.88; white-space:nowrap; }
  .ctaurl { font-size:9.2mm; font-weight:900; letter-spacing:-0.15mm; margin-top:1mm; }
  .ctahint { font-size:3.6mm; font-weight:600; opacity:0.92; margin-top:1.6mm; }
  .qrbox { width:28mm; height:28mm; background:#FFFFFF; border-radius:3mm; padding:3.2mm; flex:none; }

  .footer { display:flex; align-items:center; gap:5mm; margin-top:4.5mm; }
  .selogo { height:9mm; }
  .ethics { font-size:2.7mm; line-height:1.45; color:#8A8378; font-weight:600; margin-left:auto; text-align:right; }
"""

TPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
<style>
@page {{ size:{page_w}mm {page_h}mm; margin:0; }}
html {{ zoom:{zoom}; }}
{css}
</style></head><body>{body}</body></html>
"""

PRESS_TPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
<style>
@page {{ size:{page_w}mm {page_h}mm; margin:0; }}
html {{ zoom:{zoom}; }}
{css}
.sheet {{ position:relative; width:{sheet_w}mm; height:{sheet_h}mm; background:#FFFFFF; overflow:hidden; }}
.sheet > .page {{ position:absolute; left:{art_off}mm; top:{art_off}mm; }}
.mark {{ position:absolute; background:#000000; }}
</style></head><body><div class="sheet">{body}{marks}</div></body></html>
"""

def crop_marks(w, h, zoom, margin=8, mark_len=4, thick=0.25):
    # all inputs physical mm; emitted in design units (physical / zoom)
    sw, sh = (w + 2 * margin) / zoom, (h + 2 * margin) / zoom
    x0, y0 = margin / zoom, margin / zoom
    x1, y1 = (margin + w) / zoom, (margin + h) / zoom
    L, t = mark_len / zoom, thick / zoom
    m = []
    for y in (y0, y1):                      # horizontal marks at trim top/bottom
        for x in (0, sw - L):
            m.append(f'<div class="mark" style="left:{x:.3f}mm;top:{y - t/2:.3f}mm;width:{L:.3f}mm;height:{t:.3f}mm"></div>')
    for x in (x0, x1):                      # vertical marks at trim left/right
        for y in (0, sh - L):
            m.append(f'<div class="mark" style="left:{x - t/2:.3f}mm;top:{y:.3f}mm;width:{t:.3f}mm;height:{L:.3f}mm"></div>')
    return ''.join(m)

MARGIN = 8  # sheet margin outside trim: 3mm bleed + 5mm for crop marks

for lang in ('hu', 'en'):
    for base, w, h in [(f'flyer_a5_{lang}', 148, 210), (f'flyer_a4_{lang}', 210, 297)]:
        zoom = h / 210  # fit by height; inner width grows to fill the page exactly
        for name, bleed in [(base, 0), (base + '_print', 3)]:
            pw, ph = w + 2 * bleed, h + 2 * bleed   # physical page incl. bleed
            b = bleed / zoom                        # bleed in design units
            css = (CSS.replace('{inner_w}', f'{pw / zoom:.4f}').replace('{inner_h}', f'{ph / zoom:.4f}')
                      .replace('{pad_t}', f'{11 + b:.4f}').replace('{pad_s}', f'{11 + b:.4f}')
                      .replace('{pad_b}', f'{10 + b:.4f}'))
            (SCRATCH / f'{name}.html').write_text(
                TPL.format(page_w=pw, page_h=ph, zoom=f'{zoom:.6f}', css=css, body=body(lang)), encoding='utf-8')
            print('wrote', name)
        # press variant: 3mm bleed artwork centered on a larger sheet, with crop marks at exact trim
        bleed = 3
        b = bleed / zoom
        css = (CSS.replace('{inner_w}', f'{(w + 2*bleed) / zoom:.4f}').replace('{inner_h}', f'{(h + 2*bleed) / zoom:.4f}')
                  .replace('{pad_t}', f'{11 + b:.4f}').replace('{pad_s}', f'{11 + b:.4f}')
                  .replace('{pad_b}', f'{10 + b:.4f}'))
        (SCRATCH / f'{base}_press.html').write_text(
            PRESS_TPL.format(page_w=w + 2*MARGIN, page_h=h + 2*MARGIN, zoom=f'{zoom:.6f}', css=css,
                             sheet_w=f'{(w + 2*MARGIN) / zoom:.4f}', sheet_h=f'{(h + 2*MARGIN) / zoom:.4f}',
                             art_off=f'{(MARGIN - bleed) / zoom:.4f}',
                             body=body(lang), marks=crop_marks(w, h, zoom, MARGIN)), encoding='utf-8')
        print('wrote', base + '_press')
