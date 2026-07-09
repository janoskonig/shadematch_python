// share-card.js — shareable result cards (canvas PNG + Web Share API).
//
// Renders a spoiler-free result card (target vs mix swatches, delta-E, drops,
// time — never the recipe) and shares it through the native share sheet where
// available, falling back to a text share, then to download + clipboard copy.

const APP_URL = 'https://shadematch.app';

// Nearest colour-family emoji for the text fallback (chat apps show no image).
function hueEmoji(rgb) {
  const [r, g, b] = rgb;
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  if (mx - mn < 24) return mx > 200 ? '⬜' : mx < 70 ? '⬛' : '🟫';
  const d = mx - mn;
  let h = 0;
  if (mx === r) h = ((g - b) / d + 6) % 6;
  else if (mx === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  if (h < 20 || h >= 330) return '🟥';
  if (h < 48) return '🟧';
  if (h < 72) return '🟨';
  if (h < 165) return '🟩';
  if (h < 260) return '🟦';
  return '🟪';
}

function shareText({ kind, deltaE, drops, timeSec, targetRgb, date }) {
  const day = date || new Date().toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const what = kind === 'daily' ? `ShadeMatch Daily — ${day}` : 'ShadeMatch';
  const bits = [];
  if (Number.isFinite(deltaE)) bits.push(`ΔE ${deltaE.toFixed(2)}`);
  if (Number.isFinite(drops)) bits.push(`${drops} drops`);
  if (Number.isFinite(timeSec)) bits.push(`${timeSec.toFixed(0)}s`);
  const emoji = Array.isArray(targetRgb) ? hueEmoji(targetRgb) : '🎨';
  return `${emoji} ${what} · ${bits.join(' · ')} — can you beat me? ${APP_URL}`;
}

// Render the card. Square 1080×1080 so it looks right in every feed.
function renderCard({ kind, targetRgb, mixedRgb, deltaE, drops, timeSec, date }) {
  const S = 1080;
  const canvas = document.createElement('canvas');
  canvas.width = S;
  canvas.height = S;
  const ctx = canvas.getContext('2d');

  // Ground.
  ctx.fillStyle = '#151221';
  ctx.fillRect(0, 0, S, S);

  // Header.
  ctx.fillStyle = '#ffffff';
  ctx.font = '700 52px Nunito, system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText('ShadeMatch', 72, 110);
  ctx.font = '400 34px Nunito, system-ui, sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  const day = date || new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
  ctx.fillText(kind === 'daily' ? `Daily challenge · ${day}` : day, 72, 165);

  // Swatch pair.
  const swY = 230, swH = 430, swW = (S - 144 - 8) / 2;
  const rgbCss = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  ctx.fillStyle = rgbCss(targetRgb);
  ctx.fillRect(72, swY, swW, swH);
  ctx.fillStyle = rgbCss(mixedRgb);
  ctx.fillRect(72 + swW + 8, swY, swW, swH);
  ctx.strokeStyle = 'rgba(255,255,255,0.25)';
  ctx.lineWidth = 2;
  ctx.strokeRect(72, swY, swW * 2 + 8, swH);

  ctx.font = '600 30px Nunito, system-ui, sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.65)';
  ctx.textAlign = 'center';
  ctx.fillText('the target', 72 + swW / 2, swY + swH + 52);
  ctx.fillText('my mix', 72 + swW + 8 + swW / 2, swY + swH + 52);

  // Score row.
  const statY = 850;
  ctx.textAlign = 'center';
  const stats = [];
  if (Number.isFinite(deltaE)) stats.push({ v: 'ΔE ' + deltaE.toFixed(2), l: 'match error' });
  if (Number.isFinite(drops)) stats.push({ v: String(drops), l: 'drops' });
  if (Number.isFinite(timeSec)) stats.push({ v: timeSec.toFixed(0) + 's', l: 'time' });
  const cellW = (S - 144) / Math.max(stats.length, 1);
  stats.forEach((s, i) => {
    const cx = 72 + cellW * i + cellW / 2;
    ctx.font = '900 84px Nunito, system-ui, sans-serif';
    ctx.fillStyle = '#ffffff';
    ctx.fillText(s.v, cx, statY);
    ctx.font = '600 30px Nunito, system-ui, sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.fillText(s.l, cx, statY + 48);
  });

  // Footer.
  ctx.font = '700 36px Nunito, system-ui, sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.fillText('Can you beat me?  ·  shadematch.app', S / 2, 1010);

  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
}

async function share(payload) {
  const text = shareText(payload);
  let blob = null;
  try { blob = await renderCard(payload); } catch { blob = null; }

  if (blob && navigator.share && navigator.canShare) {
    const file = new File([blob], 'shadematch-result.png', { type: 'image/png' });
    if (navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], text });
        return 'shared';
      } catch (e) {
        if (e && e.name === 'AbortError') return 'cancelled';
      }
    }
  }
  if (navigator.share) {
    try {
      await navigator.share({ text });
      return 'shared';
    } catch (e) {
      if (e && e.name === 'AbortError') return 'cancelled';
    }
  }
  // Desktop fallback: download the card and copy the text.
  if (blob) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'shadematch-result.png';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  try { await navigator.clipboard.writeText(text); } catch { /* clipboard may be blocked */ }
  return 'downloaded';
}

// Dismissible "share your result" banner under the game panel.
function offer(payload) {
  let el = document.getElementById('shareCta');
  if (!el) {
    el = document.createElement('div');
    el.id = 'shareCta';
    el.className = 'pwa-install-cta'; // same visual language as the install card
    const anchor = document.getElementById('nextActionCta');
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(el, anchor.nextSibling);
    } else {
      document.body.appendChild(el);
    }
  }
  const label = payload.kind === 'daily' ? "Share today's result" : 'Share this match';
  el.innerHTML = `
    <div class="pwa-install-body">
      <span class="pwa-install-icon">📤</span>
      <div class="pwa-install-text">
        <strong>${label}</strong>
        <span>Send the card — see if a friend can beat you</span>
      </div>
    </div>
    <div class="pwa-install-actions">
      <button id="shareCtaBtn" class="btn btn-primary" style="font-size:0.8rem;padding:6px 14px;">Share</button>
      <button id="shareCtaDismiss" class="btn btn-secondary" style="font-size:0.8rem;padding:6px 10px;">✕</button>
    </div>
  `;
  el.style.display = 'flex';
  document.getElementById('shareCtaBtn').onclick = async () => {
    const outcome = await share(payload);
    if (outcome === 'downloaded' && window.showToast) {
      window.showToast('📋 Card downloaded and text copied — paste it anywhere', 'info', 4200);
    }
    if (outcome === 'shared') el.style.display = 'none';
  };
  document.getElementById('shareCtaDismiss').onclick = () => { el.style.display = 'none'; };
}

export const shareCard = { share, offer, shareText };
window.shadeMatchShare = { share, offer };
