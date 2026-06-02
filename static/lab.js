/**
 * Standalone mix lab: same Mixbox latent mixing as main.js, no game / telemetry.
 */
(function () {
  const baseColors = {
    white: [255, 255, 255],
    black: [0, 0, 0],
    red: [255, 0, 0],
    yellow: [255, 255, 0],
    blue: [0, 0, 255],
  };

  let dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
  let currentRgb = [255, 255, 255];
  const pigmentOrder = ['red', 'yellow', 'white', 'blue', 'black'];
  const pigmentHex = {
    red: '#ef4444',
    yellow: '#f59e0b',
    white: '#f8fafc',
    blue: '#3b82f6',
    black: '#111827',
  };
  let selectedTarget = null;
  let actionTimeline = [];
  let ratioTimeline = [];
  let stepCounter = 0;
  let lastDeltaE = null;
  let actionQueue = Promise.resolve();

  function hexByte(n) {
    const x = Math.max(0, Math.min(255, n | 0));
    return x.toString(16).toUpperCase().padStart(2, '0');
  }

  function updateRgbPanel(rgb) {
    const [r, g, b] = rgb;
    const hex = '#' + hexByte(r) + hexByte(g) + hexByte(b);
    const line = document.getElementById('labColorCodesLine');
    const sw = document.getElementById('labCurrentMix');
    if (line) {
      line.textContent = 'rgb(' + r + ', ' + g + ', ' + b + ') · R' + r + ' G' + g + ' B' + b + ' · ' + hex;
    }
    if (sw) sw.style.backgroundColor = 'rgb(' + r + ',' + g + ',' + b + ')';
  }

  function updateBadge(color, count) {
    const badge = document.querySelector('#labPalette .drop-badge[data-badge-for="' + color + '"]');
    if (badge) {
      badge.textContent = count;
      badge.classList.add('is-bumped');
      setTimeout(function () { badge.classList.remove('is-bumped'); }, 150);
    }
  }

  function resetAllBadges() {
    document.querySelectorAll('#labPalette .drop-badge').forEach(function (b) {
      b.textContent = '0';
    });
  }

  function updateMixed() {
    const totalDrops = Object.values(dropCounts).reduce(function (a, b) { return a + b; }, 0);
    if (totalDrops === 0) {
      currentRgb = [255, 255, 255];
      document.querySelectorAll('#labPalette .color-circle').forEach(function (c) {
        c.textContent = '0';
      });
      resetAllBadges();
      updateRgbPanel(currentRgb);
      return currentRgb;
    }

    if (typeof mixbox === 'undefined' || !mixbox.rgbToLatent || !mixbox.latentToRgb) {
      console.error('mixbox.js not loaded');
      return currentRgb;
    }

    const zMix = new Array(mixbox.LATENT_SIZE).fill(0);
    for (const color in dropCounts) {
      const count = dropCounts[color];
      if (count > 0) {
        const rgb = baseColors[color];
        const z = mixbox.rgbToLatent(rgb[0], rgb[1], rgb[2]);
        for (let i = 0; i < zMix.length; i++) zMix[i] += (count / totalDrops) * z[i];
      }
    }
    currentRgb = mixbox.latentToRgb(zMix).map(Math.round);
    updateRgbPanel(currentRgb);
    return currentRgb;
  }

  function rgbCss(rgb) {
    return 'rgb(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ')';
  }

  function totalDrops() {
    return pigmentOrder.reduce(function (sum, k) { return sum + (dropCounts[k] | 0); }, 0);
  }

  function currentRatios() {
    const total = totalDrops();
    const out = {};
    pigmentOrder.forEach(function (k) {
      out[k] = total > 0 ? (dropCounts[k] / total) : 0;
    });
    return out;
  }

  function targetRatios() {
    const drops = selectedTarget && selectedTarget.drops ? selectedTarget.drops : null;
    const out = {};
    if (!drops) {
      pigmentOrder.forEach(function (k) { out[k] = 0; });
      return out;
    }
    const total = pigmentOrder.reduce(function (sum, k) { return sum + Number(drops[k] || 0); }, 0);
    pigmentOrder.forEach(function (k) {
      out[k] = total > 0 ? Number(drops[k] || 0) / total : 0;
    });
    return out;
  }

  function renderLivePlot() {
    const host = document.getElementById('labLivePlotHost');
    if (!host) return;
    host.innerHTML = '';
    if (!actionTimeline.length) {
      host.innerHTML = '<div style="padding:12px;color:var(--text-secondary);font-size:0.85rem;">Start mixing to see live plot.</div>';
      return;
    }

    const W = 1080;
    const H = 420;
    const m = { l: 54, r: 18, t: 26, b: 34 };
    const hasTarget = !!selectedTarget;
    const splitX = 585;
    const gap = 24;
    const leftRight = splitX - (gap / 2);
    const rightLeft = splitX + (gap / 2);
    const leftW = hasTarget ? (leftRight - m.l) : (W - m.l - m.r);
    const rightW = hasTarget ? (W - rightLeft - m.r) : 0;
    const panelH = H - m.t - m.b;
    const xMin = 1;
    const xMax = Math.max(2, stepCounter);

    function sxL(x) { return m.l + ((x - xMin) / (xMax - xMin)) * leftW; }
    function sxR(x) { return rightLeft + ((x - xMin) / (xMax - xMin)) * rightW; }
    function sy(y, y0, y1) { return m.t + ((y1 - y) / (y1 - y0)) * panelH; }
    function syRatio(y) { return m.t + ((1 - y) * panelH); }
    function esc(s) {
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    const finiteDelta = actionTimeline
      .map(function (p) { return Number(p.deltaEAfter); })
      .filter(function (v) { return Number.isFinite(v); });
    let y0 = 0;
    let y1 = 10;
    if (finiteDelta.length) {
      y0 = Math.min.apply(null, finiteDelta);
      y1 = Math.max.apply(null, finiteDelta);
      const pad = Math.max(0.2, (y1 - y0) * 0.1);
      y0 = Math.max(-0.05, y0 - pad);
      y1 = y1 + pad;
      if (y1 <= y0) y1 = y0 + 1;
    }

    const target = targetRatios();
    const out = [];
    out.push('<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Live lab strategy plot">');
    out.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="white"/>');
    out.push('<rect x="' + m.l + '" y="' + m.t + '" width="' + leftW + '" height="' + panelH + '" fill="none" stroke="#cbd5e1" stroke-width="1"/>');
    if (hasTarget) {
      out.push('<rect x="' + rightLeft + '" y="' + m.t + '" width="' + rightW + '" height="' + panelH + '" fill="#e5e7eb" stroke="#cbd5e1" stroke-width="1"/>');
    }

    for (let i = 0; i <= 5; i += 1) {
      const xv = xMin + (xMax - xMin) * (i / 5);
      const xl = sxL(xv);
      out.push('<line x1="' + xl.toFixed(2) + '" y1="' + m.t + '" x2="' + xl.toFixed(2) + '" y2="' + (H - m.b) + '" stroke="#e2e8f0" stroke-width="1"/>');
      out.push('<text x="' + xl.toFixed(2) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="#475569">' + Math.round(xv) + '</text>');
      if (hasTarget) {
        const xr = sxR(xv);
        out.push('<line x1="' + xr.toFixed(2) + '" y1="' + m.t + '" x2="' + xr.toFixed(2) + '" y2="' + (H - m.b) + '" stroke="#e2e8f0" stroke-width="1"/>');
        out.push('<text x="' + xr.toFixed(2) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="#475569">' + Math.round(xv) + '</text>');
      }
    }

    for (let i = 0; i <= 4; i += 1) {
      const yv = y0 + (y1 - y0) * (i / 4);
      const yy = sy(yv, y0, y1);
      out.push('<line x1="' + m.l + '" y1="' + yy.toFixed(2) + '" x2="' + leftRight + '" y2="' + yy.toFixed(2) + '" stroke="#eef2f7" stroke-width="1"/>');
      out.push('<text x="' + (m.l - 8) + '" y="' + (yy + 3).toFixed(2) + '" text-anchor="end" font-size="10" fill="#475569">' + yv.toFixed(1) + '</text>');
    }
    if (hasTarget) {
      [0, 0.25, 0.5, 0.75, 1].forEach(function (rv) {
        const yy = syRatio(rv);
        out.push('<line x1="' + rightLeft + '" y1="' + yy.toFixed(2) + '" x2="' + (W - m.r) + '" y2="' + yy.toFixed(2) + '" stroke="#eef2f7" stroke-width="1"/>');
        out.push('<text x="' + (rightLeft - 8) + '" y="' + (yy + 3).toFixed(2) + '" text-anchor="end" font-size="10" fill="#475569">' + rv.toFixed(2) + '</text>');
      });
    }

    if (selectedTarget && finiteDelta.length) {
      const thrY = sy(2, y0, y1);
      out.push('<line x1="' + m.l + '" y1="' + thrY.toFixed(2) + '" x2="' + leftRight + '" y2="' + thrY.toFixed(2) + '" stroke="#16a34a" stroke-dasharray="6 4" stroke-width="1.3"/>');
    }

    if (finiteDelta.length) {
      let pth = '';
      actionTimeline.forEach(function (p, idx) {
        const d = Number(p.deltaEAfter);
        if (!Number.isFinite(d)) return;
        const cmd = pth ? ' L ' : 'M ';
        pth += cmd + sxL(p.step).toFixed(2) + ' ' + sy(d, y0, y1).toFixed(2);
      });
      if (pth) out.push('<path d="' + pth + '" fill="none" stroke="#94a3b8" stroke-width="1.4"/>');
    } else {
      out.push('<text x="' + (m.l + 8) + '" y="' + (m.t + 18) + '" font-size="11" fill="#64748b">Select a target to render live DeltaE.</text>');
    }

    actionTimeline.forEach(function (p) {
      const d = Number(p.deltaEAfter);
      if (!Number.isFinite(d)) return;
      const x = sxL(p.step);
      const y = sy(d, y0, y1);
      const col = pigmentHex[p.actionColor] || '#64748b';
      const dc = Number(p.deltaChange);
      const r = 8 + Math.min(14, Math.abs(Number.isFinite(dc) ? dc : 0) * 6);
      const txtColor = (p.actionColor === 'yellow' || p.actionColor === 'white') ? '#111827' : '#f9fafb';
      const sign = p.actionType === 'remove' ? '-' : '+';
      const lbl = Number.isFinite(dc) ? ((dc >= 0 ? '+' : '') + dc.toFixed(2)) : 'n/a';
      const dy = (p.step % 2 === 0) ? -12 : 14;
      out.push('<circle cx="' + x.toFixed(2) + '" cy="' + y.toFixed(2) + '" r="' + r.toFixed(2) + '" fill="' + col + '" stroke="#0f172a" stroke-width="1"/>');
      out.push('<text x="' + x.toFixed(2) + '" y="' + (y + 3).toFixed(2) + '" text-anchor="middle" font-size="9" font-weight="700" fill="' + txtColor + '">' + sign + '</text>');
      out.push('<text x="' + x.toFixed(2) + '" y="' + (y + dy).toFixed(2) + '" text-anchor="middle" font-size="9" fill="#0f172a">' + esc(lbl) + '</text>');
    });

    function jitterRatio(v, idx) {
      const n = Number(v);
      if (Math.abs(n) > 1e-12) return syRatio(n);
      const off = (idx - 2) * 0.006;
      return syRatio(Math.max(0, Math.min(1, off)));
    }
    if (hasTarget) {
      pigmentOrder.forEach(function (pk, idx) {
        let rp = '';
        ratioTimeline.forEach(function (r, j) {
          const cmd = j ? ' L ' : 'M ';
          rp += cmd + sxR(r.step).toFixed(2) + ' ' + jitterRatio(r[pk], idx).toFixed(2);
        });
        if (rp) out.push('<path d="' + rp + '" fill="none" stroke="' + pigmentHex[pk] + '" stroke-width="1.6"/>');
        out.push('<line x1="' + rightLeft + '" y1="' + jitterRatio(target[pk], idx).toFixed(2) + '" x2="' + (W - m.r) + '" y2="' + jitterRatio(target[pk], idx).toFixed(2) + '" stroke="' + pigmentHex[pk] + '" stroke-dasharray="4 4" stroke-width="1"/>');
      });
    }

    const titleParts = [];
    if (selectedTarget) titleParts.push('Target: ' + selectedTarget.name);
    else titleParts.push('Target: none');
    titleParts.push('steps: ' + stepCounter);
    out.push('<text x="' + m.l + '" y="14" font-size="11" fill="#334155">' + esc(titleParts.join(' | ')) + '</text>');
    out.push('<text x="' + m.l + '" y="30" font-size="11" fill="#334155">DeltaE after action</text>');
    if (hasTarget) {
      out.push('<text x="' + rightLeft + '" y="30" font-size="11" fill="#334155">Pigment ratio (solid=actual, dashed=target)</text>');
      out.push('<text x="' + leftRight + '" y="' + (H - 8) + '" text-anchor="end" font-size="11" fill="#334155">Step index</text>');
      out.push('<text x="' + (W - m.r) + '" y="' + (H - 8) + '" text-anchor="end" font-size="11" fill="#334155">Step index</text>');
    } else {
      out.push('<text x="' + (W - m.r) + '" y="' + (H - 8) + '" text-anchor="end" font-size="11" fill="#334155">Step index</text>');
      out.push('<text x="' + (m.l + 8) + '" y="' + (m.t + 48) + '" font-size="10" fill="#64748b">Choose a target to split view and show ratio panel.</text>');
    }
    out.push('</svg>');
    host.innerHTML = out.join('');
  }

  function resetTimeline() {
    actionTimeline = [];
    ratioTimeline = [];
    stepCounter = 0;
    lastDeltaE = null;
    actionQueue = Promise.resolve();
    renderLivePlot();
  }

  function setTargetMeta() {
    const dot = document.getElementById('labTargetDot');
    const txt = document.getElementById('labTargetMeta');
    const grid = document.getElementById('labSwatchGrid');
    const targetSwatch = document.getElementById('labTargetMix');
    if (!dot || !txt) return;
    if (!selectedTarget) {
      dot.style.background = 'rgb(255,255,255)';
      txt.textContent = 'No target selected';
      if (grid) grid.classList.remove('is-split');
      return;
    }
    dot.style.background = rgbCss(selectedTarget.rgb);
    const sdc = selectedTarget.sum_drop_count != null ? (' | total drops ' + selectedTarget.sum_drop_count) : '';
    txt.textContent = selectedTarget.name + sdc;
    if (targetSwatch && Array.isArray(selectedTarget.rgb) && selectedTarget.rgb.length === 3) {
      targetSwatch.style.backgroundColor = rgbCss(selectedTarget.rgb);
    }
    if (grid) grid.classList.add('is-split');
  }

  function enqueueLivePoint(actionType, actionColor) {
    const mixedRgb = [currentRgb[0], currentRgb[1], currentRgb[2]];
    const ratios = currentRatios();
    const step = ++stepCounter;
    ratioTimeline.push(Object.assign({ step: step }, ratios));
    const targetRgb = selectedTarget && Array.isArray(selectedTarget.rgb) ? selectedTarget.rgb.slice(0, 3) : null;
    actionQueue = actionQueue
      .then(function () {
        if (!targetRgb) return null;
        return fetch('/calculate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: targetRgb, mixed: mixedRgb }),
        })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            if (data && data.error) return null;
            const de = Number(data && data.delta_e);
            return Number.isFinite(de) ? de : null;
          })
          .catch(function () { return null; });
      })
      .then(function (deltaAfter) {
        const deltaChange = (lastDeltaE != null && deltaAfter != null) ? (deltaAfter - lastDeltaE) : null;
        if (deltaAfter != null) lastDeltaE = deltaAfter;
        actionTimeline.push({
          step: step,
          actionType: actionType,
          actionColor: actionColor,
          deltaEAfter: deltaAfter,
          deltaChange: deltaChange,
        });
        renderLivePlot();
      });
  }

  function loadTargets() {
    const sel = document.getElementById('labTargetSelect');
    if (!sel) return Promise.resolve();
    const uid = (window.currentUserId || localStorage.getItem('userId') || '').trim().toUpperCase();
    const url = uid ? ('/api/target-colors?user_id=' + encodeURIComponent(uid)) : '/api/target-colors';
    return fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        const rows = (data && data.status === 'success' && Array.isArray(data.colors)) ? data.colors : [];
        sel.innerHTML = '<option value="">Choose target color…</option>' + rows.map(function (r) {
          return '<option value="' + r.id + '">' + String(r.name || ('id ' + r.id)) + '</option>';
        }).join('');
        sel.addEventListener('change', function () {
          const id = Number(sel.value);
          selectedTarget = rows.find(function (r) { return Number(r.id) === id; }) || null;
          setTargetMeta();
          resetTimeline();
        });
      })
      .catch(function () {
        sel.innerHTML = '<option value="">Target load failed</option>';
      });
  }

  function setStatus(msg, isError) {
    const el = document.getElementById('labSaveStatus');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = isError ? 'var(--accent-danger, #c0392b)' : 'var(--text-secondary)';
  }

  document.addEventListener('DOMContentLoaded', function () {
    const palette = document.getElementById('labPalette');
    if (!palette) return;

    updateRgbPanel(currentRgb);
    setTargetMeta();
    renderLivePlot();
    loadTargets();

    palette.querySelectorAll('.color-circle').forEach(function (circle) {
      circle.addEventListener('click', function (e) {
        e.preventDefault();
        const color = circle.dataset.color;
        dropCounts[color]++;
        circle.textContent = dropCounts[color];
        updateBadge(color, dropCounts[color]);
        circle.classList.add('is-tapped');
        setTimeout(function () { circle.classList.remove('is-tapped'); }, 200);
        if (navigator.vibrate) navigator.vibrate(15);
        updateMixed();
        enqueueLivePoint('add', color);
      });
    });

    palette.querySelectorAll('.minus-button').forEach(function (button) {
      button.addEventListener('click', function (e) {
        e.preventDefault();
        const color = button.dataset.color;
        if (dropCounts[color] <= 0) return;
        dropCounts[color]--;
        const circle = palette.querySelector('.color-circle[data-color="' + color + '"]');
        if (circle) circle.textContent = dropCounts[color];
        updateBadge(color, dropCounts[color]);
        updateMixed();
        enqueueLivePoint('remove', color);
      });
    });

    var resetBtn = document.getElementById('labResetBtn');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
        palette.querySelectorAll('.color-circle').forEach(function (c) {
          c.textContent = '0';
        });
        resetAllBadges();
        currentRgb = [255, 255, 255];
        updateRgbPanel(currentRgb);
        setStatus('');
        resetTimeline();
      });
    }

    var saveBtn = document.getElementById('labSaveBtn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var rgb = updateMixed();
        var nameInput = document.getElementById('labColorName');
        var name = nameInput ? nameInput.value : '';
        saveBtn.disabled = true;
        setStatus('Saving…');
        fetch('/api/lab/save-target-color', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            r: rgb[0],
            g: rgb[1],
            b: rgb[2],
            name: name,
            drops: {
              white: dropCounts.white | 0,
              black: dropCounts.black | 0,
              red: dropCounts.red | 0,
              yellow: dropCounts.yellow | 0,
              blue: dropCounts.blue | 0,
            },
          }),
        })
          .then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }); })
          .then(function (_ref) {
            var res = _ref.res;
            var data = _ref.data;
            if (res.ok && data.status === 'success' && data.target_color) {
              setStatus('Saved as “' + data.target_color.name + '” (catalog id ' + data.target_color.id + ').', false);
            } else {
              setStatus((data && data.message) || 'Save failed.', true);
            }
          })
          .catch(function () {
            setStatus('Network error — could not save.', true);
          })
          .finally(function () {
            saveBtn.disabled = false;
          });
      });
    }
  });
})();
