#!/usr/bin/env python3
"""
One-command generator for ShadeMatch "Color Challenge" social posts.

Data-driven: reads real game targets + their real recipes from
artifacts/gamut_targets/gamut_targets.csv (drop_white/black/red/yellow/blue + RGB)
and the friendly names from gamut_name_mapping.csv. Renders a language-less
card (1:1 and 4:5) and prints a ready-to-post bilingual caption plus the
correct recipe for your later reveal.

No source edits, no hand-picked hex. Examples:

  # browse good candidates (2-3 pigments, sensible drop totals):
  python challenge.py --list

  # generate challenge #3 from a specific target row (id from --list):
  python challenge.py --num 3 --pick 128

  # or pick by friendly name:
  python challenge.py --num 3 --name Sage

  # fully manual override (no CSV):
  python challenge.py --num 3 --target '#6BA292' --pigments yellow,blue,white
"""
from __future__ import annotations
import argparse, csv, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CSV = REPO / 'artifacts' / 'gamut_targets' / 'gamut_targets.csv'
NAMES = REPO / 'artifacts' / 'gamut_targets' / 'gamut_name_mapping.csv'
NODE_RENDER = HERE / 'render_one.js'

# brand drop colours for the pigment icons (matches the rest of the FB kit)
PIGMENT_HEX = {'white': '#FDFDFB', 'black': '#3A3A3A', 'red': '#E85D75',
               'yellow': '#F7C948', 'blue': '#2D6CDF'}
# colourful pigments first, neutrals last (nicer reading order on the card)
DISPLAY_ORDER = ['yellow', 'red', 'blue', 'black', 'white']
WORDS = {
    'en': {'white': 'white', 'black': 'black', 'red': 'red', 'yellow': 'yellow', 'blue': 'blue'},
    'hu': {'white': 'fehér', 'black': 'fekete', 'red': 'piros', 'yellow': 'sárga', 'blue': 'kék'},
}

# ---------------------------------------------------------------- brand SVG --
def drop_svg(color, cls=''):
    return (f'<svg class="dropsvg {cls}" viewBox="0 0 24 30">'
            f'<path d="M12 1 C12 1 3 13 3 19.5 a9 9 0 0 0 18 0 C21 13 12 1 12 1 Z" '
            f'fill="{color}" stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>'
            f'<ellipse cx="8.6" cy="18.5" rx="2.4" ry="3.4" fill="rgba(255,255,255,0.35)"/></svg>')

ARROW_SVG = ('<svg class="arrow" viewBox="0 0 24 24">'
             '<path d="M12 3 v13 M6.5 11.5 l5.5 5.5 5.5-5.5" fill="none" '
             'stroke="#2F6FB5" stroke-width="2.6" stroke-linecap="round" '
             'stroke-linejoin="round"/></svg>')
FONT = ('<link href="https://fonts.googleapis.com/css2?'
        'family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">')

# ---------------------------------------------------------------- data load --
def hexify(r, g, b):
    return '#%02X%02X%02X' % (int(r), int(g), int(b))

def load_targets():
    rows = []
    with open(CSV, newline='') as f:
        for i, row in enumerate(csv.DictReader(f)):
            counts = {p: int(float(row[f'drop_{p}'])) for p in PIGMENT_HEX}
            used = [p for p in DISPLAY_ORDER if counts[p] > 0]
            rows.append(dict(idx=i, counts=counts, used=used,
                             total=int(float(row['total_drops'])),
                             hex=hexify(row['R'], row['G'], row['B'])))
    # attach friendly names by matching RGB
    names = {}
    if NAMES.exists():
        with open(NAMES, newline='') as f:
            for row in csv.DictReader(f):
                names[hexify(row['r'], row['g'], row['b'])] = row['new_name']
    for r in rows:
        r['name'] = names.get(r['hex'], '')
    return rows

def is_nice(r):
    """A good puzzle: 2-3 pigments, not a trivial single primary, sane totals."""
    return 2 <= len(r['used']) <= 3 and 3 <= r['total'] <= 12

# ---------------------------------------------------------------- rendering --
def build_html(num, target_hex, used, W, H, scale):
    fs = f'{16 * scale:.3f}'
    dw = '4.2rem' if len(used) <= 3 else '3.4rem'
    css = f"""
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    html {{ font-family:'Nunito',-apple-system,'Segoe UI','Helvetica Neue',Arial,sans-serif;
      font-size:{fs}px; }}
    .page {{ width:{W}px; height:{H}px; position:relative; background:#F8F6F3; overflow:hidden;
      color:#22252B; }}
    .wash {{ position:absolute; inset:0; background:
       radial-gradient(28rem 28rem at 106% -6%, rgba(247,201,72,0.42), rgba(247,201,72,0) 70%),
       radial-gradient(26rem 26rem at -6% 58%, rgba(127,185,138,0.32), rgba(127,185,138,0) 70%),
       radial-gradient(25rem 25rem at 108% 108%, rgba(74,144,217,0.26), rgba(74,144,217,0) 70%),
       radial-gradient(18rem 18rem at -4% -4%, rgba(232,93,117,0.20), rgba(232,93,117,0) 70%); }}
    .rain {{ position:absolute; inset:0; }}
    .rain .dropsvg {{ position:absolute; }}
    .rain .r1 {{ width:2.6rem; top:31%; left:6%; transform:rotate(14deg); opacity:.7; }}
    .rain .r2 {{ width:3.2rem; top:40%; right:7%; transform:rotate(-10deg); opacity:.65; }}
    .rain .r3 {{ width:2rem; top:49%; left:9%; transform:rotate(-16deg); opacity:.55; }}
    .content {{ position:relative; height:100%; padding:3.6rem 4rem; display:flex; flex-direction:column; }}
    .brandrow {{ display:flex; align-items:center; gap:0.9rem; }}
    .dropmark {{ display:flex; align-items:flex-end; }}
    .dropmark .dropsvg {{ width:2.6rem; margin-right:-0.9rem; }}
    .wordmark {{ font-weight:900; font-size:3.2rem; letter-spacing:-0.07rem; margin-left:1rem; }}
    .numbadge {{ margin-left:auto; display:flex; align-items:center; gap:0.7rem; background:#2F6FB5;
      color:#fff; font-weight:900; font-size:2.1rem; padding:0.55rem 1.5rem; border-radius:3rem;
      box-shadow:0 0.5rem 1.2rem rgba(47,111,181,0.30); }}
    .numbadge .mini {{ display:flex; }}
    .numbadge .mini i {{ width:0.9rem; height:0.9rem; border-radius:50%; margin-left:-0.28rem;
      border:1.5px solid rgba(255,255,255,0.85); }}
    .hero {{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
      gap:1.4rem; }}
    .ingredients {{ display:flex; align-items:center; gap:1.4rem; background:#FFFFFF;
      border:2px solid #ECE7DF; border-radius:2.4rem; padding:1.6rem 2.6rem;
      box-shadow:0 0.8rem 1.8rem rgba(34,37,43,0.06); }}
    .ing {{ display:flex; flex-direction:column; align-items:center; gap:0.4rem; }}
    .ing .dropsvg {{ width:{dw}; }}
    .ing b {{ font-size:2.1rem; color:#6B655B; }}
    .op {{ font-size:2.6rem; font-weight:900; color:#C3BCAF; }}
    .arrow {{ width:3.4rem; height:3.4rem; opacity:0.9; }}
    .targetwrap {{ position:relative; }}
    .target {{ width:21rem; height:21rem; border-radius:3.6rem; background:{target_hex};
      border:3px solid rgba(0,0,0,0.08); box-shadow:0 1.8rem 3.2rem rgba(34,37,43,0.18); }}
    .qbadge {{ position:absolute; right:-1.5rem; top:-1.5rem; width:6rem; height:6rem;
      border-radius:50%; background:#FFFFFF; color:#2F6FB5; font-weight:900; font-size:3.6rem;
      display:flex; align-items:center; justify-content:center; border:3px solid #2F6FB5;
      box-shadow:0 0.7rem 1.6rem rgba(34,37,43,0.18); }}
    .url {{ text-align:center; font-size:3.1rem; font-weight:900; letter-spacing:-0.05rem;
      background:linear-gradient(135deg,#4A90D9,#2F6FB5); -webkit-background-clip:text;
      background-clip:text; color:transparent; }}
    """
    q = lambda p: f'<span class="ing">{drop_svg(PIGMENT_HEX[p])}<b>?×</b></span>'
    ing_row = '<span class="op">+</span>'.join(q(p) for p in used)
    mini = ''.join(f'<i style="background:{c}"></i>' for c in ('#E85D75', '#F7C948', '#4A90D9'))
    body = f"""
    <div class="page"><div class="wash"></div>
      <div class="rain">{drop_svg('#F7C948','r1')}{drop_svg('#4A90D9','r2')}{drop_svg('#E85D75','r3')}</div>
      <div class="content">
        <div class="brandrow">
          <div class="dropmark">{drop_svg('#E85D75')}{drop_svg('#F7C948')}{drop_svg('#4A90D9')}</div>
          <div class="wordmark">ShadeMatch</div>
          <div class="numbadge"><span class="mini">{mini}</span>#{num}</div>
        </div>
        <div class="hero">
          <div class="ingredients">{ing_row}</div>
          {ARROW_SVG}
          <div class="targetwrap"><div class="target"></div><div class="qbadge">?</div></div>
        </div>
        <div class="url">shadestudy.com</div>
      </div>
    </div>"""
    return f'<!DOCTYPE html><html><head><meta charset="utf-8">{FONT}<style>{css}</style></head><body>{body}</body></html>'

def render(num, target_hex, used):
    outs = []
    for fmt, W, H, scale in [('square', 1080, 1080, 0.82), ('portrait', 1080, 1350, 0.94)]:
        html = HERE / f'_ch{num}_{fmt}.html'
        html.write_text(build_html(num, target_hex, used, W, H, scale), encoding='utf-8')
        out = HERE / f'shadestudy_fb_challenge{num}_{fmt}.png'
        subprocess.run(['node', str(NODE_RENDER), str(html), str(out), str(W), str(H)], check=True)
        html.unlink()
        outs.append(out)
    return outs

# ---------------------------------------------------------------- caption ----
def recipe_str(counts, lang):
    return ' + '.join(f"{counts[p]}× {WORDS[lang][p]}" for p in DISPLAY_ORDER if counts[p] > 0)

def caption(num, used, lang_pigments):
    en_p = ', '.join(WORDS['en'][p] for p in used)
    hu_p = ', '.join(WORDS['hu'][p] for p in used)
    return f"""🎨 Color Challenge #{num} · Színfeladvány #{num}

🇬🇧 Only {en_p}. How many drops of each would you mix to match this shade? Guess in the comments 👇 Then check yourself — free, no download: shadestudy.com. Every round also powers color-vision research at Semmelweis University.

🇭🇺 Csak {hu_p}. Melyikből hány csepp kell, hogy eltaláld ezt a színt? Tippelj a kommentekben 👇 Aztán ellenőrizd magad — ingyen, letöltés nélkül: shadestudy.com. Minden körrel a Semmelweis Egyetem színlátás-kutatását is segíted."""

# ---------------------------------------------------------------- main -------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--num', type=int, help='Challenge series number (e.g. 3)')
    ap.add_argument('--pick', type=int, help='Target row index from --list')
    ap.add_argument('--name', help='Pick target by friendly name (case-insensitive)')
    ap.add_argument('--target', help='Manual override: target hex, e.g. #6BA292')
    ap.add_argument('--pigments', help='Manual override: comma list, e.g. yellow,blue,white')
    ap.add_argument('--list', action='store_true', help='List good candidate targets and exit')
    ap.add_argument('--limit', type=int, default=40, help='How many candidates to list')
    args = ap.parse_args()

    if args.target and args.pigments:                       # manual mode
        used = [p.strip() for p in args.pigments.split(',')]
        bad = [p for p in used if p not in PIGMENT_HEX]
        if bad: ap.error(f'unknown pigment(s): {bad}')
        if not args.num: ap.error('--num is required')
        outs = render(args.num, args.target.upper(), used)
        print('\n'.join(f'  {o.relative_to(REPO)}' for o in outs))
        print('\n----- CAPTION (post this) -----\n' + caption(args.num, used, None))
        print('\n----- ANSWER (recipe unknown in manual mode) -----')
        return

    rows = load_targets()
    if args.list:
        nice = [r for r in rows if is_nice(r)]
        print(f'{len(nice)} good candidates (idx · name · hex · pigments · total drops):\n')
        for r in nice[:args.limit]:
            print(f"  {r['idx']:>4}  {r['hex']}  {(r['name'] or '—'):<16} "
                  f"{'+'.join(r['used']):<22} {r['total']}d")
        print(f"\nThen: python challenge.py --num <k> --pick <idx>")
        return

    # select a target row
    row = None
    if args.pick is not None:
        row = next((r for r in rows if r['idx'] == args.pick), None)
        if row is None: ap.error(f'no target with idx {args.pick}')
    elif args.name:
        row = next((r for r in rows if r['name'].lower() == args.name.lower()), None)
        if row is None: ap.error(f'no target named {args.name!r}')
    else:
        ap.error('choose a target: --pick <idx>, --name <name>, or --list to browse')
    if not args.num: ap.error('--num is required')

    outs = render(args.num, row['hex'], row['used'])
    print('\n'.join(f'  {o.relative_to(REPO)}' for o in outs))
    print('\n----- CAPTION (post this) -----\n' + caption(args.num, row['used'], None))
    label = row['name'] or 'target'
    print(f"\n----- ANSWER (keep for the reveal) -----\n"
          f"  {label} · {row['hex']} · {recipe_str(row['counts'], 'en')}")

if __name__ == '__main__':
    main()
