import base64, pathlib

SCRATCH = pathlib.Path(__file__).parent
LOGO = pathlib.Path('/Users/janoskonig/shadematch_python/static/img/semmelweis-logo.png')
logo_b64 = base64.b64encode(LOGO.read_bytes()).decode()

ICON = pathlib.Path('/Users/janoskonig/shadematch_python/static/icons/icon-512.png')

def _icon_transparent_b64():
    """Knock the opaque white background out of the app icon so the face can sit
    on a branded field without a hard white square. Near-white -> transparent,
    with a soft ramp to keep the anti-aliased edges clean."""
    from PIL import Image
    import io
    im = Image.open(ICON).convert('RGBA')
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            m = min(r, g, b)
            if m >= 250:
                px[x, y] = (r, g, b, 0)
            elif m >= 236:                      # soft edge ramp 236..250
                px[x, y] = (r, g, b, int(a * (250 - m) / 14))
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()

icon_b64 = _icon_transparent_b64()

# ---- shared brand pieces (matched to flyer / insta kit) --------------------
def drop_svg(color, cls=''):
    return (f'<svg class="dropsvg {cls}" viewBox="0 0 24 30">'
            f'<path d="M12 1 C12 1 3 13 3 19.5 a9 9 0 0 0 18 0 C21 13 12 1 12 1 Z" '
            f'fill="{color}" stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>'
            f'<ellipse cx="8.6" cy="18.5" rx="2.4" ry="3.4" fill="rgba(255,255,255,0.35)"/></svg>')

CHECK_SVG = ('<svg class="checksvg" viewBox="0 0 24 24">'
             '<path d="M5 12.5l4.5 4.5 9.5-10" stroke="#FFFFFF" stroke-width="3.4" '
             'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

FONT = ('<link href="https://fonts.googleapis.com/css2?'
        'family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">')

RESET = "* { margin:0; padding:0; box-sizing:border-box; }"
HTMLFONT = ("html { font-family:'Nunito', -apple-system, 'Segoe UI', "
            "'Helvetica Neue', Arial, sans-serif; }")

def page(css, body):
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8">{FONT}'
            f'<style>{RESET}{HTMLFONT}{css}</style></head><body>{body}</body></html>')

# ============================================================================
# 1) PROFILE PICTURE  — 500x500, masked to a circle by Facebook
# ============================================================================
def profile():
    # App face/mask icon on a clean white circle with a thin warm brand ring.
    # FB masks the 500x500 square to a circle; a full white field reads as a
    # crisp white avatar, and the icon's white interior blends into it.
    css = """
    .wrap { width:500px; height:500px; position:relative; background:#FFFFFF;
      overflow:hidden; display:flex; align-items:center; justify-content:center; }
    .disc { position:absolute; width:500px; height:500px; border-radius:50%; background:#FFFFFF; }
    .ring { position:absolute; width:470px; height:470px; border-radius:50%;
      border:7px solid #7FB98A; box-shadow:inset 0 0 0 3px #FFFFFF, 0 0 0 3px rgba(127,185,138,0.20); }
    .icon { position:relative; width:404px; height:404px; margin-top:6px;
      filter:drop-shadow(0 12px 26px rgba(34,37,43,0.14)); }
    """
    body = f"""<div class="wrap">
      <div class="disc"></div>
      <div class="ring"></div>
      <img class="icon" src="data:image/png;base64,{icon_b64}" alt="ShadeMatch">
    </div>"""
    return page(css, body)

# ============================================================================
# 2) COVER PHOTO  — 1640x624 source (FB shows 820x312 desktop / center-crops on
#    mobile). Composition kept horizontally centered so nothing important is cut.
# ============================================================================
COVER_STRINGS = {
    'en': dict(badge='a game + a study', h1='Can you mix', h1em='this color?',
               recipe='Yellow + blue + white. One target. Two minutes a day.',
               cta='Play free at', uni='Color-vision research · Semmelweis University'),
    'hu': dict(badge='játék + kutatás', h1='Ki tudod keverni', h1em='ezt a színt?',
               recipe='Sárga + kék + fehér. Egy célszín. Napi két perc.',
               cta='Játssz ingyen:', uni='Színlátás-kutatás · Semmelweis Egyetem'),
}

def cover(lang):
    s = COVER_STRINGS[lang]
    css = """
    .page { width:1640px; height:624px; position:relative; background:#F8F6F3; overflow:hidden;
      color:#22252B; }
    .wash { position:absolute; inset:0; background:
       radial-gradient(560px 560px at 100% -20%, rgba(247,201,72,0.40), rgba(247,201,72,0) 70%),
       radial-gradient(520px 520px at -6% 120%, rgba(127,185,138,0.34), rgba(127,185,138,0) 70%),
       radial-gradient(480px 480px at 108% 130%, rgba(74,144,217,0.26), rgba(74,144,217,0) 70%),
       radial-gradient(360px 360px at -4% -20%, rgba(232,93,117,0.20), rgba(232,93,117,0) 70%); }
    .rain { position:absolute; inset:0; }
    .rain .dropsvg { position:absolute; }
    .rain .r1 { width:46px; top:70px; right:150px; transform:rotate(14deg); opacity:.9;}
    .rain .r2 { width:66px; top:150px; right:80px; transform:rotate(-10deg); opacity:.85;}
    .rain .r3 { width:40px; top:250px; right:180px; transform:rotate(8deg); opacity:.8;}
    .rain .r4 { width:52px; top:120px; left:90px; transform:rotate(-16deg); opacity:.55;}
    .rain .r5 { width:38px; top:430px; left:150px; transform:rotate(20deg); opacity:.5;}
    .content { position:relative; height:100%; display:flex; flex-direction:column;
      align-items:center; justify-content:center; text-align:center; padding:44px 60px; }
    .brandrow { display:flex; align-items:center; gap:18px; }
    .dropmark { display:flex; align-items:flex-end; }
    .dropmark .dropsvg { width:56px; margin-right:-19px; }
    .wordmark { font-weight:900; font-size:60px; letter-spacing:-1.4px; margin-left:22px; }
    .badge { background:#FFFFFF; border:2px solid #E3DED6; color:#2F6FB5; font-weight:800;
      font-size:19px; padding:8px 20px; border-radius:40px; letter-spacing:2px;
      text-transform:uppercase; margin-left:8px; }
    h1 { font-size:78px; line-height:1.02; font-weight:900; letter-spacing:-2.4px; margin-top:26px; }
    h1 em { font-style:normal; color:#2F6FB5; }
    .chip { display:inline-block; width:58px; height:58px; border-radius:15px; background:#7FB98A;
      border:3px solid rgba(0,0,0,0.10); vertical-align:-8px; margin-left:6px; }
    .recipe { display:flex; align-items:center; gap:18px; margin-top:30px; font-size:22px;
      font-weight:700; color:#5B5B5B; }
    .eq { display:flex; align-items:center; gap:12px; background:#FFFFFF; border:2px solid #ECE7DF;
      border-radius:18px; padding:12px 22px; }
    .eq .dropsvg { width:34px; }
    .eq b { font-size:22px; color:#6B655B; }
    .eq .op { font-size:26px; font-weight:900; color:#B9B2A6; }
    .eq .target { width:48px; height:48px; border-radius:12px; background:#7FB98A;
      border:2px solid rgba(0,0,0,0.08); display:flex; align-items:center; justify-content:center; }
    .eq .checksvg { width:30px; height:30px; }
    .cta { margin-top:30px; display:flex; align-items:center; gap:14px; font-weight:900; }
    .cta .kick { font-size:22px; color:#3A3A3A; font-weight:800; }
    .cta .url { font-size:34px; letter-spacing:-0.6px;
      background:linear-gradient(135deg,#4A90D9,#2F6FB5); -webkit-background-clip:text;
      background-clip:text; color:transparent; }
    .uni { display:flex; align-items:center; gap:12px; margin-top:22px; }
    .uni img { height:26px; }
    .uni span { font-size:16px; color:#8A8378; font-weight:700; }
    """
    body = f"""
    <div class="page"><div class="wash"></div>
      <div class="rain">
        {drop_svg('#F7C948','r1')}{drop_svg('#4A90D9','r2')}{drop_svg('#E85D75','r3')}
        {drop_svg('#7FB98A','r4')}{drop_svg('#9B6FC3','r5')}
      </div>
      <div class="content">
        <div class="brandrow">
          <div class="dropmark">{drop_svg('#E85D75')}{drop_svg('#F7C948')}{drop_svg('#4A90D9')}</div>
          <div class="wordmark">ShadeMatch</div>
          <div class="badge">{s['badge']}</div>
        </div>
        <h1>{s['h1']} <em>{s['h1em']}</em><span class="chip"></span></h1>
        <div class="recipe">
          <div class="eq">
            <span>{drop_svg('#F7C948')}</span><b>3×</b><span class="op">+</span>
            <span>{drop_svg('#2D6CDF')}</span><b>2×</b><span class="op">+</span>
            <span>{drop_svg('#FDFDFB')}</span><b>1×</b><span class="op">=</span>
            <span class="target">{CHECK_SVG}</span>
          </div>
          <span>{s['recipe']}</span>
        </div>
        <div class="cta"><span class="kick">{s['cta']}</span><span class="url">shadestudy.com</span></div>
        <div class="uni"><img src="data:image/png;base64,{logo_b64}"><span>{s['uni']}</span></div>
      </div>
    </div>"""
    return page(css, body)

# ============================================================================
# 3) FIRST POST  — 1080x1080 launch card ("we're on Facebook")
# ============================================================================
POST_STRINGS = {
    'en': dict(badge='a game + a study', kicker="We're on Facebook!",
               h1='A color game that<br><em>doubles as research.</em>',
               lead='Mix virtual paint drops to match a target shade. Every round you play '
                    'powers color-vision research at Semmelweis University.',
               pills=['🆓 Free', '⏱️ 2 min a day', '🔐 Private', '📱 No download'],
               cta='Play now', hint='Runs right in your browser.',
               ethics='S.H.A.D.E. · Semmelweis University · SE RKEB 167/2025'),
    'hu': dict(badge='játék + kutatás', kicker='Már Facebookon is!',
               h1='Színkeverő játék,<br><em>ami kutatás is.</em>',
               lead='Keverj virtuális festékcseppeket, hogy eltaláld a célszínt. Minden körrel a '
                    'Semmelweis Egyetem színlátás-kutatását segíted.',
               pills=['🆓 Ingyenes', '⏱️ Napi 2 perc', '🔐 Álneves', '📱 Nincs letöltés'],
               cta='Játssz most', hint='A böngésződben fut.',
               ethics='S.H.A.D.E. · Semmelweis Egyetem · SE RKEB 167/2025'),
}

def post(lang):
    s = POST_STRINGS[lang]
    pills = ''.join(f'<span class="pill">{p}</span>' for p in s['pills'])
    css = """
    .page { width:1080px; height:1080px; position:relative; background:#F8F6F3; overflow:hidden;
      color:#22252B; }
    .wash { position:absolute; inset:0; background:
       radial-gradient(460px 460px at 106% -6%, rgba(247,201,72,0.45), rgba(247,201,72,0) 70%),
       radial-gradient(420px 420px at -6% 60%, rgba(127,185,138,0.35), rgba(127,185,138,0) 70%),
       radial-gradient(400px 400px at 108% 108%, rgba(74,144,217,0.28), rgba(74,144,217,0) 70%),
       radial-gradient(300px 300px at -4% -4%, rgba(232,93,117,0.22), rgba(232,93,117,0) 70%); }
    .rain { position:absolute; inset:0; }
    .rain .dropsvg { position:absolute; }
    .rain .r1 { width:44px; top:250px; right:44px; transform:rotate(14deg); opacity:.85;}
    .rain .r2 { width:62px; top:320px; right:96px; transform:rotate(-10deg); opacity:.8;}
    .rain .r3 { width:38px; top:410px; right:52px; transform:rotate(8deg); opacity:.75;}
    .content { position:relative; height:100%; padding:62px 74px 56px; display:flex; flex-direction:column; }
    .brandrow { display:flex; align-items:center; gap:16px; }
    .dropmark { display:flex; align-items:flex-end; }
    .dropmark .dropsvg { width:50px; margin-right:-17px; }
    .wordmark { font-weight:900; font-size:54px; letter-spacing:-1.3px; margin-left:20px; }
    .badge { margin-left:auto; background:#FFFFFF; border:2px solid #E3DED6; color:#2F6FB5;
      font-weight:800; font-size:22px; padding:9px 24px; border-radius:40px; letter-spacing:2px;
      text-transform:uppercase; }
    .kicker { display:inline-flex; align-self:flex-start; align-items:center; gap:12px;
      margin-top:44px; background:#2F6FB5; color:#fff; font-weight:900; font-size:30px;
      padding:12px 26px; border-radius:40px; letter-spacing:0.4px; }
    h1 { font-size:82px; line-height:1.05; font-weight:900; letter-spacing:-2.6px; margin-top:26px; }
    h1 em { font-style:normal; color:#2F6FB5; }
    .lead { font-size:31px; line-height:1.42; color:#5B5B5B; margin-top:26px; font-weight:600; max-width:900px; }
    .pills { display:flex; flex-wrap:wrap; gap:16px; margin-top:34px; }
    .pill { background:#FFFFFF; border:2px solid #ECE7DF; border-radius:44px; padding:13px 28px;
      font-size:29px; font-weight:800; color:#3A3A3A; }
    .cta { margin-top:auto; background:linear-gradient(135deg,#4A90D9 0%,#2F6FB5 100%);
      border-radius:34px; padding:34px 50px; color:#fff; display:flex; align-items:center; }
    .cta .l { display:flex; flex-direction:column; }
    .cta .kick { font-size:28px; font-weight:800; text-transform:uppercase; letter-spacing:3px; opacity:.9; }
    .cta .url { font-size:62px; font-weight:900; letter-spacing:-1.4px; margin-top:4px; }
    .cta .hint { margin-left:auto; text-align:right; font-size:26px; font-weight:700; opacity:.94; max-width:300px; }
    .footer { display:flex; align-items:center; gap:20px; margin-top:26px; }
    .footer img { height:34px; }
    .footer span { font-size:21px; color:#8A8378; font-weight:600; }
    """
    body = f"""
    <div class="page"><div class="wash"></div>
      <div class="rain">{drop_svg('#F7C948','r1')}{drop_svg('#4A90D9','r2')}{drop_svg('#E85D75','r3')}</div>
      <div class="content">
        <div class="brandrow">
          <div class="dropmark">{drop_svg('#E85D75')}{drop_svg('#F7C948')}{drop_svg('#4A90D9')}</div>
          <div class="wordmark">ShadeMatch</div>
          <div class="badge">{s['badge']}</div>
        </div>
        <div class="kicker">👋 {s['kicker']}</div>
        <h1>{s['h1']}</h1>
        <p class="lead">{s['lead']}</p>
        <div class="pills">{pills}</div>
        <div class="cta">
          <div class="l"><span class="kick">{s['cta']}</span><span class="url">shadestudy.com</span></div>
          <span class="hint">{s['hint']}</span>
        </div>
        <div class="footer"><img src="data:image/png;base64,{logo_b64}"><span>{s['ethics']}</span></div>
      </div>
    </div>"""
    return page(css, body)

# ============================================================================
# 4) CHALLENGE POST  — clean, low-text "guess the recipe" creative.
#    Renders in 1:1 (1080x1080) and 4:5 (1080x1350). The game IS the hook:
#    a target shade + hidden drop counts. Comment-bait organically, curiosity
#    ad paid. Words live in the caption/ad fields, not baked into the image.
# ============================================================================
CHAL_TARGET = '#6BA292'   # soft teal-green target, mixable from yellow+blue+white
CHAL_STRINGS = {
    'en': dict(badge='color challenge', fromlabel='mix it from',
               h1a='Can you mix', h1b='this color?',
               comment='👇 Guess the recipe in the comments',
               url='Play free · shadestudy.com'),
    'hu': dict(badge='színfeladvány', fromlabel='keverd ki ebből',
               h1a='Ki tudod keverni', h1b='ezt a színt?',
               comment='👇 Tippelj a recepttel a kommentekben',
               url='Játssz ingyen · shadestudy.com'),
}

def challenge(lang, W, H, scale):
    s = CHAL_STRINGS[lang]
    css = ("""
    html { font-size:__FS__px; }
    .page { width:__W__px; height:__H__px; position:relative; background:#F8F6F3;
      overflow:hidden; color:#22252B; }
    .wash { position:absolute; inset:0; background:
       radial-gradient(28rem 28rem at 106% -6%, rgba(247,201,72,0.42), rgba(247,201,72,0) 70%),
       radial-gradient(26rem 26rem at -6% 58%, rgba(127,185,138,0.34), rgba(127,185,138,0) 70%),
       radial-gradient(25rem 25rem at 108% 108%, rgba(74,144,217,0.26), rgba(74,144,217,0) 70%),
       radial-gradient(18rem 18rem at -4% -4%, rgba(232,93,117,0.20), rgba(232,93,117,0) 70%); }
    .rain { position:absolute; inset:0; }
    .rain .dropsvg { position:absolute; }
    .rain .r1 { width:2.6rem; top:33%; left:6%; transform:rotate(14deg); opacity:.75;}
    .rain .r2 { width:3.2rem; top:41%; right:7%; transform:rotate(-10deg); opacity:.7;}
    .rain .r3 { width:2rem; top:50%; left:9%; transform:rotate(-16deg); opacity:.6;}
    .content { position:relative; height:100%; padding:3.6rem 4rem 3.4rem;
      display:flex; flex-direction:column; }
    .brandrow { display:flex; align-items:center; gap:0.9rem; }
    .dropmark { display:flex; align-items:flex-end; }
    .dropmark .dropsvg { width:2.6rem; margin-right:-0.9rem; }
    .wordmark { font-weight:900; font-size:3.2rem; letter-spacing:-0.07rem; margin-left:1rem; }
    .badge { margin-left:auto; background:#FFFFFF; border:2px solid #E3DED6; color:#2F6FB5;
      font-weight:800; font-size:1.4rem; padding:0.5rem 1.4rem; border-radius:3rem;
      letter-spacing:0.14rem; text-transform:uppercase; }

    .hero { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; }
    .targetwrap { position:relative; }
    .target { width:20rem; height:20rem; border-radius:3.4rem; background:__TARGET__;
      border:3px solid rgba(0,0,0,0.08); box-shadow:0 1.6rem 3rem rgba(34,37,43,0.16); }
    .qbadge { position:absolute; right:-1.4rem; top:-1.4rem; width:5.2rem; height:5.2rem;
      border-radius:50%; background:#2F6FB5; color:#fff; font-weight:900; font-size:3.2rem;
      display:flex; align-items:center; justify-content:center;
      box-shadow:0 0.6rem 1.4rem rgba(47,111,181,0.35); }
    .fromlabel { margin-top:2rem; font-size:1.7rem; font-weight:800; letter-spacing:0.14rem;
      text-transform:uppercase; color:#9A9284; }
    .ingredients { display:flex; align-items:center; gap:1.4rem; margin-top:1.2rem;
      background:#FFFFFF; border:2px solid #ECE7DF; border-radius:2.4rem; padding:1.5rem 2.4rem; }
    .ing { display:flex; flex-direction:column; align-items:center; gap:0.4rem; }
    .ing .dropsvg { width:4rem; }
    .ing b { font-size:2rem; color:#6B655B; }
    .op { font-size:2.6rem; font-weight:900; color:#C3BCAF; }

    h1 { font-size:6rem; line-height:1.02; font-weight:900; letter-spacing:-0.2rem;
      text-align:center; margin-top:1.5rem; }
    h1 em { font-style:normal; color:#2F6FB5; }
    .cta { margin-top:2.4rem; text-align:center; }
    .comment { font-size:2.15rem; font-weight:800; color:#3A3A3A; }
    .url { margin-top:1rem; font-size:2.5rem; font-weight:900; letter-spacing:-0.04rem;
      background:linear-gradient(135deg,#4A90D9,#2F6FB5); -webkit-background-clip:text;
      background-clip:text; color:transparent; }
    """
    .replace('__W__', str(W)).replace('__H__', str(H))
    .replace('__FS__', f'{16*scale:.3f}').replace('__TARGET__', CHAL_TARGET))
    q = lambda c: f"{drop_svg(c)}<b>?×</b>"
    body = f"""
    <div class="page"><div class="wash"></div>
      <div class="rain">{drop_svg('#F7C948','r1')}{drop_svg('#4A90D9','r2')}{drop_svg('#E85D75','r3')}</div>
      <div class="content">
        <div class="brandrow">
          <div class="dropmark">{drop_svg('#E85D75')}{drop_svg('#F7C948')}{drop_svg('#4A90D9')}</div>
          <div class="wordmark">ShadeMatch</div>
          <div class="badge">{s['badge']}</div>
        </div>
        <div class="hero">
          <div class="targetwrap">
            <div class="target"></div>
            <div class="qbadge">?</div>
          </div>
          <div class="fromlabel">{s['fromlabel']}</div>
          <div class="ingredients">
            <span class="ing">{q('#F7C948')}</span><span class="op">+</span>
            <span class="ing">{q('#2D6CDF')}</span><span class="op">+</span>
            <span class="ing">{q('#FDFDFB')}</span>
          </div>
        </div>
        <h1>{s['h1a']} <em>{s['h1b']}</em></h1>
        <div class="cta">
          <div class="comment">{s['comment']}</div>
          <div class="url">{s['url']}</div>
        </div>
      </div>
    </div>"""
    return page(css, body)

# ============================================================================
# 5) LANGUAGE-LESS CHALLENGE  — pure visual puzzle, no words. One creative
#    serves a bilingual caption. Parametrized by series number, target color,
#    and the three ingredient drop colors.
# ============================================================================
ARROW_SVG = ('<svg class="arrow" viewBox="0 0 24 24">'
             '<path d="M12 3 v13 M6.5 11.5 l5.5 5.5 5.5-5.5" fill="none" '
             'stroke="#2F6FB5" stroke-width="2.6" stroke-linecap="round" '
             'stroke-linejoin="round"/></svg>')

def challenge_ll(num, target, ings, W, H, scale):
    css = ("""
    html { font-size:__FS__px; }
    .page { width:__W__px; height:__H__px; position:relative; background:#F8F6F3;
      overflow:hidden; color:#22252B; }
    .wash { position:absolute; inset:0; background:
       radial-gradient(28rem 28rem at 106% -6%, rgba(247,201,72,0.42), rgba(247,201,72,0) 70%),
       radial-gradient(26rem 26rem at -6% 58%, rgba(127,185,138,0.32), rgba(127,185,138,0) 70%),
       radial-gradient(25rem 25rem at 108% 108%, rgba(74,144,217,0.26), rgba(74,144,217,0) 70%),
       radial-gradient(18rem 18rem at -4% -4%, rgba(232,93,117,0.20), rgba(232,93,117,0) 70%); }
    .rain { position:absolute; inset:0; }
    .rain .dropsvg { position:absolute; }
    .rain .r1 { width:2.6rem; top:31%; left:6%; transform:rotate(14deg); opacity:.7;}
    .rain .r2 { width:3.2rem; top:40%; right:7%; transform:rotate(-10deg); opacity:.65;}
    .rain .r3 { width:2rem; top:49%; left:9%; transform:rotate(-16deg); opacity:.55;}
    .content { position:relative; height:100%; padding:3.6rem 4rem 3.6rem;
      display:flex; flex-direction:column; }
    .brandrow { display:flex; align-items:center; gap:0.9rem; }
    .dropmark { display:flex; align-items:flex-end; }
    .dropmark .dropsvg { width:2.6rem; margin-right:-0.9rem; }
    .wordmark { font-weight:900; font-size:3.2rem; letter-spacing:-0.07rem; margin-left:1rem; }
    .numbadge { margin-left:auto; display:flex; align-items:center; gap:0.7rem;
      background:#2F6FB5; color:#fff; font-weight:900; font-size:2.1rem; padding:0.55rem 1.5rem;
      border-radius:3rem; box-shadow:0 0.5rem 1.2rem rgba(47,111,181,0.30); }
    .numbadge .mini { display:flex; }
    .numbadge .mini i { width:0.9rem; height:0.9rem; border-radius:50%; margin-left:-0.28rem;
      border:1.5px solid rgba(255,255,255,0.85); }

    .hero { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
      gap:1.4rem; }
    .ingredients { display:flex; align-items:center; gap:1.4rem;
      background:#FFFFFF; border:2px solid #ECE7DF; border-radius:2.4rem; padding:1.6rem 2.6rem;
      box-shadow:0 0.8rem 1.8rem rgba(34,37,43,0.06); }
    .ing { display:flex; flex-direction:column; align-items:center; gap:0.4rem; }
    .ing .dropsvg { width:4.2rem; }
    .ing b { font-size:2.1rem; color:#6B655B; }
    .op { font-size:2.6rem; font-weight:900; color:#C3BCAF; }
    .arrow { width:3.4rem; height:3.4rem; opacity:0.9; }
    .targetwrap { position:relative; }
    .target { width:21rem; height:21rem; border-radius:3.6rem; background:__TARGET__;
      border:3px solid rgba(0,0,0,0.08); box-shadow:0 1.8rem 3.2rem rgba(34,37,43,0.18); }
    .qbadge { position:absolute; right:-1.5rem; top:-1.5rem; width:6rem; height:6rem;
      border-radius:50%; background:#FFFFFF; color:#2F6FB5; font-weight:900; font-size:3.6rem;
      display:flex; align-items:center; justify-content:center;
      border:3px solid #2F6FB5; box-shadow:0 0.7rem 1.6rem rgba(34,37,43,0.18); }

    .url { text-align:center; font-size:3.1rem; font-weight:900; letter-spacing:-0.05rem;
      background:linear-gradient(135deg,#4A90D9,#2F6FB5); -webkit-background-clip:text;
      background-clip:text; color:transparent; }
    """
    .replace('__W__', str(W)).replace('__H__', str(H))
    .replace('__FS__', f'{16*scale:.3f}').replace('__TARGET__', target))
    q = lambda c: f'<span class="ing">{drop_svg(c)}<b>?×</b></span>'
    ing_row = '<span class="op">+</span>'.join(q(c) for c in ings)
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
          <div class="targetwrap">
            <div class="target"></div>
            <div class="qbadge">?</div>
          </div>
        </div>
        <div class="url">shadestudy.com</div>
      </div>
    </div>"""
    return page(css, body)

# ---- write html ------------------------------------------------------------
(SCRATCH / 'fb_profile.html').write_text(profile(), encoding='utf-8'); print('wrote fb_profile')
for lang in ('en', 'hu'):
    (SCRATCH / f'fb_cover_{lang}.html').write_text(cover(lang), encoding='utf-8'); print('wrote fb_cover', lang)
    (SCRATCH / f'fb_post_{lang}.html').write_text(post(lang), encoding='utf-8'); print('wrote fb_post', lang)
    # challenge creative in feed 1:1 and 4:5
    (SCRATCH / f'fb_challenge_square_{lang}.html').write_text(
        challenge(lang, 1080, 1080, 0.82), encoding='utf-8'); print('wrote fb_challenge_square', lang)
    (SCRATCH / f'fb_challenge_portrait_{lang}.html').write_text(
        challenge(lang, 1080, 1350, 0.94), encoding='utf-8'); print('wrote fb_challenge_portrait', lang)

# language-less Challenge #2 — warm terracotta from yellow + red + white
CH2 = dict(num='2', target='#D9765A', ings=['#F7C948', '#E85D75', '#FDFDFB'])
(SCRATCH / 'fb_challenge2_square.html').write_text(
    challenge_ll(CH2['num'], CH2['target'], CH2['ings'], 1080, 1080, 0.82), encoding='utf-8')
print('wrote fb_challenge2_square')
(SCRATCH / 'fb_challenge2_portrait.html').write_text(
    challenge_ll(CH2['num'], CH2['target'], CH2['ings'], 1080, 1350, 0.94), encoding='utf-8')
print('wrote fb_challenge2_portrait')
