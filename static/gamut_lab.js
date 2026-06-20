/**
 * Gamut lab (/gamut).
 *
 * Build two independent palettes (A and B) from the 327-pigment Kremer catalog
 * (Hyperspectral Pigments, Zenodo 5592485) and compare their reachable colour gamut
 * head-to-head. All gamut maths runs server-side (app/gamut_lab.py — CIELAB convex-hull
 * volume of Kubelka–Munk mixtures); this file is the picker UI: it loads the catalog, lets
 * the user lock pigments into A or B, choose each palette's candidate pool + target size,
 * calls the /gamut endpoints per palette, and renders both palettes plus an A-vs-B
 * comparison (volume delta, catalog coverage, overlaid a*–b* gamut plot).
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const rgb = (s) => `rgb(${s[0]}, ${s[1]}, ${s[2]})`;
  const fmt = (n) => Math.round(n).toLocaleString();
  const IDS = ['A', 'B'];
  const PAL_COLOR = { A: '#3b6ef5', B: '#e1872b' };

  const state = {
    catalog: [],
    byPn: new Map(),
    skin: null,            // { points, hull, label, cite } — human skin-colour gamut overlay
    pal: {
      A: { locked: [], poolMode: 'all', size: 8, result: null },
      B: { locked: [], poolMode: 'all', size: 8, result: null },
    },
  };
  const panels = {};       // id -> root element of that palette's config panel

  // ── Networking ────────────────────────────────────────────────────────────
  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    return r.json();
  }

  function poolPnumbers(id) {
    const p = state.pal[id];
    if (p.poolMode === 'picks') return p.locked.slice();
    return null;                                               // whole catalog
  }

  function busy(btn, on) { btn.classList.toggle('is-busy', on); btn.disabled = on; }
  const q = (id, sel) => panels[id].querySelector(sel);

  // ── Locked pigments ───────────────────────────────────────────────────────
  function toggleLock(id, pn) {
    const locked = state.pal[id].locked;
    const i = locked.findIndex((x) => String(x) === String(pn));
    if (i >= 0) locked.splice(i, 1); else locked.push(pn);
    renderChips(id);
    renderCatalog();
  }

  function renderChips(id) {
    const box = q(id, '[data-chips]');
    const locked = state.pal[id].locked;
    if (!locked.length) {
      box.innerHTML = `<span class="chips-empty">no pigments locked into ${id} yet</span>`;
      return;
    }
    box.innerHTML = locked.map((pn) => {
      const p = state.byPn.get(String(pn)); if (!p) return '';
      return `<span class="chip"><span class="sw" style="background:${rgb(p.srgb)}"></span>${p.name}
        <button type="button" data-unlock="${pn}" aria-label="Unlock ${p.name}">×</button></span>`;
    }).join('');
    box.querySelectorAll('[data-unlock]').forEach((b) =>
      b.addEventListener('click', () => toggleLock(id, b.dataset.unlock)));
  }

  // ── Catalog list (shared; per-row A/B lock pills) ─────────────────────────
  function filteredCatalog() {
    const query = ($('catSearch').value || '').trim().toLowerCase();
    const grp = $('catGroup').value;
    const sort = $('catSort').value;
    let list = state.catalog.filter((p) => {
      if (grp && p.group !== grp) return false;
      if (query && !(p.name.toLowerCase().includes(query) || String(p.pnumber).includes(query))) return false;
      return true;
    });
    const cmp = {
      chroma: (a, b) => b.chroma - a.chroma,
      hue: (a, b) => a.hue - b.hue,
      light: (a, b) => b.lab[0] - a.lab[0],
      name: (a, b) => a.name.localeCompare(b.name),
    }[sort] || (() => 0);
    list.sort(cmp);
    return list;
  }

  function renderCatalog() {
    const list = filteredCatalog();
    $('catCount').textContent = `${list.length} of ${state.catalog.length}`;
    const inA = new Set(state.pal.A.locked.map(String));
    const inB = new Set(state.pal.B.locked.map(String));
    $('catList').innerHTML = list.map((p) => {
      const a = inA.has(String(p.pnumber)), b = inB.has(String(p.pnumber));
      return `<div class="cat-item">
        <span class="cat-sw" style="background:${rgb(p.srgb)}"></span>
        <span><span class="cat-name">${p.name}</span><br>
          <span class="cat-meta">${p.group} · #${p.pnumber} · hue ${Math.round(p.hue)}° · chroma ${Math.round(p.chroma)}</span></span>
        <span class="cat-pills">
          <button type="button" class="cat-pill a ${a ? 'is-on' : ''}" data-lock="A" data-pn="${p.pnumber}" aria-pressed="${a}" aria-label="Lock ${p.name} into palette A">A</button>
          <button type="button" class="cat-pill b ${b ? 'is-on' : ''}" data-lock="B" data-pn="${p.pnumber}" aria-pressed="${b}" aria-label="Lock ${p.name} into palette B">B</button>
        </span>
      </div>`;
    }).join('');
    $('catList').querySelectorAll('.cat-pill').forEach((el) =>
      el.addEventListener('click', () => toggleLock(el.dataset.lock, el.dataset.pn)));
  }

  // ── Per-palette result rendering ──────────────────────────────────────────
  function renderPalette(id, res) {
    state.pal[id].result = res;
    const seq = res.sequence || [];
    const total = res.total_volume != null ? res.total_volume : (res.volume || 0);
    q(id, '[data-vol]').textContent = total > 0 ? fmt(total) : '—';

    const box = q(id, '[data-result]');
    if (!seq.length) { box.innerHTML = '<p class="muted">No palette returned.</p>'; updateComparison(); return; }

    const maxDelta = Math.max(1, ...seq.map((s) => s.delta || 0));
    const rows = seq.map((s, i) => {
      const w = s.delta ? Math.max(2, Math.round(100 * s.delta / maxDelta)) : 0;
      const bar = s.delta != null
        ? `<div>+${fmt(s.delta)}</div><div class="seq-bar-track"><div class="seq-bar" style="width:${w}%"></div></div>`
        : `<div class="muted">${s.locked ? 'locked' : 'seed'}</div>`;
      return `<div class="seq-item">
        <span class="seq-idx">${i + 1}</span>
        <span class="seq-sw" style="background:${rgb(s.srgb)}"></span>
        <span><span class="seq-name">${s.name}${s.locked ? '<span class="tag-lock">locked</span>' : ''}</span><br>
          <span class="seq-sub">${s.group} · #${s.pnumber} · gamut ${fmt(s.volume_after)}</span></span>
        <span class="seq-bar-wrap">${bar}</span>
      </div>`;
    }).join('');

    const cov = res.coverage;
    const covLine = (cov && cov.mean_delta_e != null)
      ? `<p class="cov-line"><strong>${cov.containment_pct}%</strong> of the ${cov.targets} catalog colours fall inside this gamut · coverage error mean ΔE ${cov.mean_delta_e.toFixed(2)}, worst ΔE ${cov.max_delta_e.toFixed(2)}.</p>`
      : '';
    box.innerHTML = `<div class="muted">${seq.length} pigment${seq.length === 1 ? '' : 's'} · gamut ${total > 0 ? fmt(total) : '—'}</div>
      ${covLine}<div class="seq-list">${rows}</div>`;

    updateComparison();
  }

  // ── A-vs-B comparison ─────────────────────────────────────────────────────
  function paletteVolume(id) {
    const r = state.pal[id].result;
    if (!r) return null;
    return r.total_volume != null ? r.total_volume : (r.volume || 0);
  }

  function updateComparison() {
    const vA = paletteVolume('A'), vB = paletteVolume('B');
    $('cmpVolA').textContent = vA != null && vA > 0 ? fmt(vA) : '—';
    $('cmpVolB').textContent = vB != null && vB > 0 ? fmt(vB) : '—';

    if (vA > 0 && vB > 0) {
      const pct = Math.round((vB / vA - 1) * 100);
      const cls = pct >= 0 ? 'delta-up' : 'delta-down';
      $('cmpDelta').innerHTML = `<span class="${cls}">${pct >= 0 ? '+' : ''}${pct}%</span>`;
    } else {
      $('cmpDelta').textContent = '—';
    }

    // Coverage comparison table — only once at least one palette has coverage.
    const cA = state.pal.A.result && state.pal.A.result.coverage;
    const cB = state.pal.B.result && state.pal.B.result.coverage;
    const haveA = cA && cA.mean_delta_e != null;
    const haveB = cB && cB.mean_delta_e != null;
    const tbl = $('cmpTable');
    if (!haveA && !haveB) { tbl.hidden = true; } else {
      tbl.hidden = false;
      const de = (c, k) => (c && c[k] != null ? `ΔE ${c[k].toFixed(2)}` : '—');
      const pc = (c, k) => (c && c[k] != null ? `${c[k]}%` : '—');
      $('covMeanA').textContent = de(cA, 'mean_delta_e'); $('covMeanB').textContent = de(cB, 'mean_delta_e');
      $('covMaxA').textContent = de(cA, 'max_delta_e'); $('covMaxB').textContent = de(cB, 'max_delta_e');
      $('cov6A').textContent = haveA ? `${cA.within['6.0']}%` : '—'; $('cov6B').textContent = haveB ? `${cB.within['6.0']}%` : '—';
      $('covContA').textContent = pc(cA, 'containment_pct'); $('covContB').textContent = pc(cB, 'containment_pct');
      $('covVolA').textContent = pc(cA, 'volume_coverage_pct'); $('covVolB').textContent = pc(cB, 'volume_coverage_pct');
    }

    drawPlot();
  }

  // ── a*–b* gamut plot (both palettes overlaid + skin reference) ────────────
  function drawPlot() {
    if (typeof Plotly === 'undefined') return;
    const traces = [];
    const ring = (hull, name, fill, line, dash) => {
      if (!hull || hull.length < 3) return null;
      const xs = hull.map((p) => p[0]).concat([hull[0][0]]);
      const ys = hull.map((p) => p[1]).concat([hull[0][1]]);
      return { x: xs, y: ys, mode: 'lines', name, fill: fill ? 'toself' : 'none',
        fillcolor: fill, line: { color: line, width: 2, dash: dash || 'solid' }, hoverinfo: 'skip' };
    };

    // Dashed reference = the human skin-colour gamut, so you can see if a palette covers
    // real skin tones. Mean chromaticities + hull from Xiao et al. 2017 (state.skin.cite).
    const skin = state.skin;
    if (skin && skin.hull && skin.hull.length >= 3) {
      traces.push(ring(skin.hull, skin.label || 'human skin', 'rgba(208,138,108,0.10)', 'rgba(190,104,74,0.95)', 'dash'));
    }
    if (skin && skin.points && skin.points.length) {
      const ethColor = { Caucasian: '#e6a57e', Chinese: '#cf9b46', Kurdish: '#8c5e3c', Thai: '#a64d79' };
      Object.keys(ethColor).forEach((eth) => {
        const pp = skin.points.filter((p) => p.ethnicity === eth);
        if (!pp.length) return;
        traces.push({
          x: pp.map((p) => p.a), y: pp.map((p) => p.b), mode: 'markers', name: eth,
          legendgroup: 'skin',
          text: pp.map((p) => `${p.ethnicity} · ${p.site}<br>L* ${p.L}, a* ${p.a}, b* ${p.b}`),
          hovertemplate: '%{text}<extra></extra>',
          marker: {
            size: 9, color: ethColor[eth],
            symbol: pp.map((p) => (p.facial ? 'diamond' : 'circle-open')),
            line: { color: 'rgba(0,0,0,0.4)', width: 1 },
          },
        });
      });
    }

    // Each palette's reachable hull + its pure pigments. A = blue, B = orange.
    const fillFor = { A: 'rgba(59,110,245,0.13)', B: 'rgba(225,135,43,0.13)' };
    const symbolFor = { A: 'circle', B: 'square' };
    IDS.forEach((id) => {
      const res = state.pal[id].result;
      if (!res) return;
      const r = ring(res.ab_hull, `palette ${id}`, fillFor[id], PAL_COLOR[id]);
      if (r) traces.push(r);
      const pts = res.pigment_points || [];
      if (pts.length) {
        traces.push({
          x: pts.map((p) => p.a), y: pts.map((p) => p.b), mode: 'markers', name: `${id} pigments`,
          text: pts.map((p) => p.name),
          hovertemplate: `%{text}<br>palette ${id} · a* %{x:.0f}, b* %{y:.0f}<extra></extra>`,
          marker: { size: 11, symbol: symbolFor[id], color: pts.map((p) => rgb(p.srgb)),
                    line: { color: PAL_COLOR[id], width: 2 } },
        });
      }
    });

    Plotly.react('gamutPlot', traces, {
      margin: { t: 10, r: 10, b: 76, l: 44 }, showlegend: true,
      legend: { orientation: 'h', y: -0.30, font: { size: 10 } },
      xaxis: { title: 'a* (green ← → red)', zeroline: true, zerolinecolor: '#ccc' },
      yaxis: { title: 'b* (blue ← → yellow)', zeroline: true, zerolinecolor: '#ccc', scaleanchor: 'x' },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    }, { displayModeBar: false, responsive: true });
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  async function runGreedy(id) {
    const btn = q(id, '[data-run]');
    busy(btn, true);
    try {
      const p = state.pal[id];
      const res = await postJSON('/gamut/optimize', { size: p.size, locked: p.locked, pool: poolPnumbers(id) });
      renderPalette(id, res);
    } catch (e) { q(id, '[data-result]').innerHTML = `<p class="muted">Search failed: ${e.message}</p>`; }
    finally { busy(btn, false); }
  }

  async function scorePicks(id) {
    const p = state.pal[id];
    if (p.locked.length < 4) {
      q(id, '[data-result]').innerHTML = `<p class="muted">Lock at least 4 pigments into ${id} to score an exact set (or run the search).</p>`;
      return;
    }
    const btn = q(id, '[data-score]');
    busy(btn, true);
    try {
      const res = await postJSON('/gamut/score', { pnumbers: p.locked });
      // Shape it like a sequence so the same renderer works.
      res.sequence = p.locked.map((pn) => {
        const pig = state.byPn.get(String(pn));
        return { ...pig, volume_after: res.volume, delta: null, locked: true };
      });
      res.total_volume = res.volume;
      renderPalette(id, res);
    } catch (e) { q(id, '[data-result]').innerHTML = `<p class="muted">Scoring failed: ${e.message}</p>`; }
    finally { busy(btn, false); }
  }

  function clearPalette(id) {
    const p = state.pal[id];
    p.locked = [];
    p.result = null;
    q(id, '[data-vol]').textContent = '—';
    q(id, '[data-result]').innerHTML = `<p class="muted">Run a search or score your ${id} picks.</p>`;
    renderChips(id);
    renderCatalog();
    updateComparison();
  }

  // ── Wiring ────────────────────────────────────────────────────────────────
  function wirePalette(id) {
    const root = panels[id];
    const size = root.querySelector('[data-size]');
    size.addEventListener('input', (e) => {
      state.pal[id].size = +e.target.value;
      root.querySelector('[data-size-val]').textContent = e.target.value;
    });
    root.querySelector('[data-pool]').querySelectorAll('button').forEach((b) =>
      b.addEventListener('click', () => {
        state.pal[id].poolMode = b.dataset.poolMode;
        root.querySelector('[data-pool]').querySelectorAll('button').forEach((x) => x.classList.toggle('is-on', x === b));
      }));
    root.querySelector('[data-run]').addEventListener('click', () => runGreedy(id));
    root.querySelector('[data-score]').addEventListener('click', () => scorePicks(id));
    root.querySelector('[data-clear]').addEventListener('click', () => clearPalette(id));
  }

  function wire() {
    IDS.forEach((id) => { panels[id] = document.querySelector(`[data-pal="${id}"]`); wirePalette(id); });
    ['catSearch', 'catGroup', 'catSort'].forEach((cid) => $(cid).addEventListener('input', renderCatalog));
  }

  async function init() {
    wire();
    try {
      const data = await (await fetch('/gamut/catalog')).json();
      state.catalog = data.pigments || [];
      state.skin = data.skin_gamut || null;
      if (state.skin && state.skin.cite) { const el = $('skinCite'); if (el) el.textContent = state.skin.cite; }
      state.byPn = new Map(state.catalog.map((p) => [String(p.pnumber), p]));
      const groups = Array.from(new Set(state.catalog.map((p) => p.group))).sort();
      $('catGroup').innerHTML = '<option value="">All groups</option>' + groups.map((g) => `<option value="${g}">${g}</option>`).join('');
      renderCatalog();
      IDS.forEach(renderChips);
      drawPlot();                              // empty plot (skin overlay only) to start
    } catch (e) {
      $('catList').innerHTML = `<p class="muted" style="padding:12px;">Failed to load catalog: ${e.message}</p>`;
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
