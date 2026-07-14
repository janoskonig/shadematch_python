import base64, pathlib

SCRATCH = pathlib.Path(__file__).parent
LOGO = pathlib.Path('/Users/janoskonig/shadematch_python/static/img/semmelweis-logo.png')
logo_b64 = base64.b64encode(LOGO.read_bytes()).decode()

def drop_svg(color, cls=''):
    return (f'<svg class="dropsvg {cls}" viewBox="0 0 24 30">'
            f'<path d="M12 1 C12 1 3 13 3 19.5 a9 9 0 0 0 18 0 C21 13 12 1 12 1 Z" '
            f'fill="{color}" stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>'
            f'<ellipse cx="8.6" cy="18.5" rx="2.4" ry="3.4" fill="rgba(255,255,255,0.35)"/></svg>')

CHECK_SVG = ('<svg class="checksvg" viewBox="0 0 24 24">'
             '<path d="M5 12.5l4.5 4.5 9.5-10" stroke="#FFFFFF" stroke-width="3.4" '
             'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

STRINGS = {
    'hu': {
        'badge': 'játék + kutatás',
        'h1': 'Ki tudod keverni<br><em>ezt a színt?</em> <span class="chip"></span>',
        'lead': 'Sárga, kék és fehér cseppek. Egy célszín. Napi két perc — és minden '
                'körrel a Semmelweis Egyetem színlátás-kutatását segíted.',
        'mixlabel': 'Ez a zsályazöld receptje. <b>A következőt már te fejted meg.</b>',
        'challenge': 'Könnyűnek tűnik? <em>Bizonyítsd be.</em>',
        'chips': ['🆓 Ingyenes', '⏱️ Napi 2 perc', '🔐 Álneves'],
        'ctakicker': 'Próbáld ki most',
        'ctahint': 'A böngésződben fut — azonnal indul.',
        'ethics': 'S.H.A.D.E. · Semmelweis Egyetem · SE RKEB 167/2025',
    },
    'en': {
        'badge': 'a game + a study',
        'h1': 'Can you mix<br><em>this color?</em> <span class="chip"></span>',
        'lead': 'Yellow, blue and white drops. One target color. Two minutes a day — and '
                'every round you play powers color-vision research at Semmelweis University.',
        'mixlabel': 'That’s the recipe for sage green. <b>The next one is yours to crack.</b>',
        'challenge': 'Looks easy? <em>Prove it.</em>',
        'chips': ['🆓 Free', '⏱️ 2 min a day', '🔐 Private'],
        'ctakicker': 'Try it now',
        'ctahint': 'Runs right in your browser — ready in seconds.',
        'ethics': 'S.H.A.D.E. · Semmelweis University · SE RKEB 167/2025',
    },
}

# format: (name, width, height, scale of base 1080-design units, show_lead, show_pills, pad_top, pad_bottom)
FORMATS = [
    ('square',   1080, 1080, 0.80, False, True,  56,  48),
    ('portrait', 1080, 1350, 0.92, True,  True,  64,  56),
    ('story',    1080, 1920, 1.04, True,  True,  200, 290),
]

def body(lang, show_lead, show_pills):
    s = STRINGS[lang]
    chips = ''.join(f'<span class="pill">{c}</span>' for c in s['chips'])
    lead = f'<p class="lead">{s["lead"]}</p>' if show_lead else ''
    pills = f'<div class="pills">{chips}</div>' if show_pills else ''
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
    {lead}

    <div class="mixcard">
      <div class="mixrow">
        <span class="ingredient">{drop_svg('#F7C948')}<b>3×</b></span>
        <span class="op">+</span>
        <span class="ingredient">{drop_svg('#2D6CDF')}<b>2×</b></span>
        <span class="op">+</span>
        <span class="ingredient">{drop_svg('#FDFDFB')}<b>1×</b></span>
        <span class="op">=</span>
        <span class="target">{CHECK_SVG}</span>
      </div>
      <div class="mixlabel">{s['mixlabel']}</div>
    </div>

    <div class="challenge">{s['challenge']}</div>
    {pills}

    <div class="cta">
      <div class="ctakicker">{s['ctakicker']}</div>
      <div class="ctaurl">shadestudy.com</div>
      <div class="ctahint">{s['ctahint']}</div>
    </div>

    <div class="footer">
      <img class="selogo" src="data:image/png;base64,{logo_b64}" alt="Semmelweis">
      <span class="ethics">{s['ethics']}</span>
    </div>
  </div>
</div>
"""

# design units: 1u = 1px at scale 1.0 on a 1080-wide canvas
CSS = """
  * { margin:0; padding:0; box-sizing:border-box; }
  html { font-family:'Nunito', -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size:{scale}px; }
  .page { width:{W}px; height:{H}px; background:#F8F6F3; position:relative; overflow:hidden; color:#22252B; }
  .wash { position:absolute; inset:0;
    background:
      radial-gradient(420px 420px at 106% -6%, rgba(247,201,72,0.45), rgba(247,201,72,0) 70%),
      radial-gradient(380px 380px at -6% 62%, rgba(127,185,138,0.35), rgba(127,185,138,0) 70%),
      radial-gradient(360px 360px at 108% 104%, rgba(74,144,217,0.28), rgba(74,144,217,0) 70%),
      radial-gradient(260px 260px at -4% -4%, rgba(232,93,117,0.22), rgba(232,93,117,0) 70%);
  }
  .rain { position:absolute; inset:0; }
  .rain .dropsvg { position:absolute; opacity:0.9; }
  .rain .r1 { width:2.2rem; top:26%; right:4%; transform:rotate(14deg); }
  .rain .r2 { width:3.1rem; top:33%; right:8%; transform:rotate(-10deg); opacity:0.85; }
  .rain .r3 { width:1.9rem; top:40%; right:3.5%; transform:rotate(8deg); opacity:0.8; }
  .rain .r4 { width:2.4rem; top:47%; right:9%; transform:rotate(-16deg); opacity:0.75; }
  .rain .r5 { width:1.8rem; top:29%; right:13%; transform:rotate(20deg); opacity:0.7; }
  .rain .r6 { width:2rem; top:37%; right:14%; transform:rotate(-6deg); opacity:0.65; }
  .content { position:relative; height:100%; padding:{pad_top}px 4.6rem {pad_bottom}px; display:flex; flex-direction:column; }

  .brandrow { display:flex; align-items:center; gap:0.9rem; }
  .dropmark { display:flex; align-items:flex-end; }
  .dropsvg.dm { width:2.9rem; margin-right:-0.95rem; }
  .wordmark { font-weight:900; font-size:3.4rem; letter-spacing:-0.03rem; margin-left:1rem; }
  .badge { margin-left:auto; background:#FFFFFF; border:2px solid #E3DED6; color:#2F6FB5;
           font-weight:800; font-size:1.55rem; padding:0.55rem 1.5rem; border-radius:3rem;
           letter-spacing:0.12rem; text-transform:uppercase; white-space:nowrap; }

  h1 { font-size:6.6rem; line-height:1.04; font-weight:900; letter-spacing:-0.18rem; margin-top:auto; padding-top:2.4rem; }
  h1 em { font-style:normal; color:#2F6FB5; }
  .chip { display:inline-block; width:5.6rem; height:5.6rem; border-radius:1.4rem; background:#7FB98A;
          border:3px solid rgba(0,0,0,0.10); vertical-align:-0.7rem; }
  .lead { font-size:2.05rem; line-height:1.45; color:#5B5B5B; margin-top:2rem; font-weight:600; max-width:56rem; }

  .mixcard { background:#FFFFFF; border:2px solid #ECE7DF; border-radius:2.2rem; padding:2rem 3rem 1.6rem;
             margin-top:2.6rem; }
  .mixrow { display:flex; align-items:center; justify-content:center; gap:1.7rem; }
  .ingredient { display:flex; flex-direction:column; align-items:center; gap:0.4rem; }
  .ingredient .dropsvg { width:5rem; }
  .ingredient b { font-size:1.8rem; color:#6B655B; }
  .op { font-size:3.3rem; font-weight:900; color:#B9B2A6; }
  .target { width:8rem; height:8rem; border-radius:1.8rem; display:flex; align-items:center; justify-content:center;
            background:#7FB98A; border:2px solid rgba(0,0,0,0.08); }
  .checksvg { width:4.8rem; height:4.8rem; }
  .mixlabel { text-align:center; font-size:1.66rem; color:#5B5B5B; margin-top:1.3rem; font-weight:700; white-space:nowrap; }

  .challenge { margin-top:2.4rem; text-align:center; font-size:3.2rem; font-weight:900;
               letter-spacing:-0.06rem; white-space:nowrap; }
  .challenge em { font-style:normal; color:#2F6FB5; }

  .pills { display:flex; justify-content:center; gap:1.4rem; margin-top:1.9rem; }
  .pill { background:#FFFFFF; border:2px solid #ECE7DF; border-radius:3rem; padding:0.85rem 2rem;
          font-size:1.8rem; font-weight:800; color:#3A3A3A; white-space:nowrap; }

  .cta { margin-top:auto; background:linear-gradient(135deg, #4A90D9 0%, #2F6FB5 100%);
         border-radius:2.4rem; padding:2.6rem 3.4rem; color:#FFFFFF; text-align:center; }
  .ctakicker { font-size:1.85rem; font-weight:800; text-transform:uppercase; letter-spacing:0.24rem; opacity:0.88; }
  .ctaurl { font-size:4.6rem; font-weight:900; letter-spacing:-0.08rem; margin-top:0.5rem; }
  .ctahint { font-size:1.8rem; font-weight:600; opacity:0.92; margin-top:0.8rem; }

  .footer { display:flex; align-items:center; justify-content:center; gap:1.6rem; margin-top:2.2rem; }
  .selogo { height:2.6rem; }
  .ethics { font-size:1.4rem; color:#8A8378; font-weight:600; }
"""

TPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
<style>{css}</style></head><body>{body}</body></html>
"""

for lang in ('hu', 'en'):
    for name, W, H, scale, show_lead, show_pills, pt, pb in FORMATS:
        css = (CSS.replace('{W}', str(W)).replace('{H}', str(H))
                  .replace('{scale}', f'{16 * scale:.3f}')
                  .replace('{pad_top}', str(pt)).replace('{pad_bottom}', str(pb)))
        (SCRATCH / f'insta_{name}_{lang}.html').write_text(
            TPL.format(css=css, body=body(lang, show_lead, show_pills)), encoding='utf-8')
        print('wrote', f'insta_{name}_{lang}')
