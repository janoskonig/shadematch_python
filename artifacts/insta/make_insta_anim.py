import pathlib
import make_insta as mi

SCRATCH = pathlib.Path(__file__).parent

# (name, W, H, scale, pad_top, pad_bottom) — matching the static ad formats
VIDEO_FORMATS = [
    ('45',  1080, 1350, 0.92, 64,  56),
    ('916', 1080, 1920, 0.69, 270, 680),  # Reels/Stories ad-safe zone: top 14%, bottom 35%
]

ANIM_CSS = """
  /* ---- entrance timeline (6.5s total) ---- */
  .brandrow { animation: rise 0.6s cubic-bezier(.2,.8,.3,1) 0.05s both; }
  h1        { animation: rise 0.7s cubic-bezier(.2,.8,.3,1) 0.25s both; }
  .lead     { animation: rise 0.6s cubic-bezier(.2,.8,.3,1) 0.55s both; }
  .mixcard  { animation: rise 0.6s cubic-bezier(.2,.8,.3,1) 0.85s both; }
  .mixlabel { animation: fade 0.5s ease 3.15s both; }
  .challenge{ animation: rise 0.6s cubic-bezier(.2,.8,.3,1) 3.7s both; }
  .pill:nth-child(1) { animation: pop 0.45s cubic-bezier(.2,.8,.3,1) 4.25s both; }
  .pill:nth-child(2) { animation: pop 0.45s cubic-bezier(.2,.8,.3,1) 4.4s both; }
  .pill:nth-child(3) { animation: pop 0.45s cubic-bezier(.2,.8,.3,1) 4.55s both; }
  .cta      { animation: rise 0.7s cubic-bezier(.2,.8,.3,1) 5.0s both; }
  .ctaurl   { display:inline-block; animation: pulse 0.9s ease-in-out 5.7s both; }
  .footer   { animation: fade 0.6s ease 5.4s both; }

  /* drops fall into the recipe one by one */
  .ingredient:nth-child(1) .dropsvg { animation: fall 0.55s cubic-bezier(.3,.7,.4,1.2) 1.15s both; }
  .ingredient:nth-child(3) .dropsvg { animation: fall 0.55s cubic-bezier(.3,.7,.4,1.2) 1.6s both; }
  .ingredient:nth-child(5) .dropsvg { animation: fall 0.55s cubic-bezier(.3,.7,.4,1.2) 2.05s both; }
  .ingredient:nth-child(1) b { animation: fade 0.35s ease 1.5s both; }
  .ingredient:nth-child(3) b { animation: fade 0.35s ease 1.95s both; }
  .ingredient:nth-child(5) b { animation: fade 0.35s ease 2.4s both; }
  .op:nth-of-type(2) { animation: fade 0.3s ease 1.45s both; }
  .op:nth-of-type(4) { animation: fade 0.3s ease 1.9s both; }
  .op:nth-of-type(6) { animation: fade 0.3s ease 2.5s both; }

  /* target chip fills with the mixed color, check draws itself */
  .target { animation: fillTarget 0.55s ease 2.65s both, pop 0.55s cubic-bezier(.2,.8,.3,1) 2.65s both; }
  .chip   { animation: pop 0.5s cubic-bezier(.2,.8,.3,1) 0.6s both; }
  .checksvg path { stroke-dasharray: 100; animation: draw 0.5s ease-out 3.0s both; }

  /* ambient float on the decorative rain */
  .rain .r1 { animation: float 3.6s ease-in-out 0s infinite alternate; }
  .rain .r2 { animation: float 4.2s ease-in-out -1.2s infinite alternate; }
  .rain .r3 { animation: float 3.2s ease-in-out -0.6s infinite alternate; }
  .rain .r4 { animation: float 4.6s ease-in-out -2.1s infinite alternate; }
  .rain .r5 { animation: float 3.9s ease-in-out -1.7s infinite alternate; }
  .rain .r6 { animation: float 3.4s ease-in-out -0.3s infinite alternate; }

  @keyframes rise  { from { opacity:0; transform:translateY(2.2rem); } to { opacity:1; transform:none; } }
  @keyframes fade  { from { opacity:0; } to { opacity:1; } }
  @keyframes pop   { 0% { opacity:0; transform:scale(0.6); } 70% { opacity:1; transform:scale(1.06); } 100% { opacity:1; transform:scale(1); } }
  @keyframes fall  { 0% { opacity:0; transform:translateY(-5.5rem); } 55% { opacity:1; transform:translateY(0.5rem); }
                     78% { transform:translateY(-0.25rem); } 100% { opacity:1; transform:none; } }
  @keyframes fillTarget { from { background-color:#DDD9CF; } to { background-color:#7FB98A; } }
  @keyframes draw  { from { stroke-dashoffset:100; } to { stroke-dashoffset:0; } }
  @keyframes pulse { 0% { transform:scale(1); } 45% { transform:scale(1.055); } 100% { transform:scale(1); } }
"""

for lang in ('hu', 'en'):
    for fmt, W, H, SCALE, PT, PB in VIDEO_FORMATS:
        body = mi.body(lang, show_lead=True, show_pills=True)
        body = body.replace('<path d="M5 12.5l4.5 4.5 9.5-10"', '<path pathLength="100" d="M5 12.5l4.5 4.5 9.5-10"')
        css = (mi.CSS.replace('{W}', str(W)).replace('{H}', str(H))
                  .replace('{scale}', f'{16 * SCALE:.3f}')
                  .replace('{pad_top}', str(PT)).replace('{pad_bottom}', str(PB)))
        html = mi.TPL.format(css=css + ANIM_CSS, body=body)
        (SCRATCH / f'insta_anim_{fmt}_{lang}.html').write_text(html, encoding='utf-8')
        print('wrote', f'insta_anim_{fmt}_{lang}')
