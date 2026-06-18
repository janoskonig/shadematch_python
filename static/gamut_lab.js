/**
 * Gamut lab (/gamut).
 *
 * Interactive widest-gamut search over the 327-pigment Kremer catalog (Hyperspectral
 * Pigments, Zenodo 5592485). All gamut maths runs server-side (app/gamut_lab.py — CIELAB
 * convex-hull volume of Kubelka–Munk mixtures); this file is the picker UI: it loads the
 * catalog, lets the user lock pigments and pick a candidate pool + target size, calls the
 * /gamut endpoints, and renders the ordered palette + an a*–b* gamut plot.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const rgb = (s) => `rgb(${s[0]}, ${s[1]}, ${s[2]})`;
  const fmt = (n) => Math.round(n).toLocaleString();

  const state = {
    catalog: [],
    byPn: new Map(),
    baseline: { volume: 0, ab_hull: [] },
    skin: null,            // { polygon, label, cite } — human skin-colour gamut overlay
    locked: [],            // ordered pnumbers
    poolMode: 'all',       // 'all' | 'groups' | 'picks'
    selectedGroups: new Set(),
    size: 8,
  };

  // ── Networking ────────────────────────────────────────────────────────────
  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    return r.json();
  }

  function poolPnumbers() {
    if (state.poolMode === 'picks') return state.locked.slice();
    if (state.poolMode === 'groups') {
      if (!state.selectedGroups.size) return null;             // none selected → whole catalog
      return state.catalog.filter((p) => state.selectedGroups.has(p.group)).map((p) => p.pnumber);
    }
    return null;                                               // whole catalog
  }

  function busy(btn, on) { btn.classList.toggle('is-busy', on); btn.disabled = on; }

  // ── Locked pigments (chips) ───────────────────────────────────────────────
  function toggleLock(pn) {
    const i = state.locked.findIndex((x) => String(x) === String(pn));
    if (i >= 0) state.locked.splice(i, 1); else state.locked.push(pn);
    renderLocked();
    markCatalog();
  }

  function renderLocked() {
    const box = $('lockedChips');
    if (!state.locked.length) { box.innerHTML = '<span class="chips-empty">none — the search is unconstrained</span>'; return; }
    box.innerHTML = state.locked.map((pn) => {
      const p = state.byPn.get(String(pn)); if (!p) return '';
      return `<span class="chip"><span class="sw" style="background:${rgb(p.srgb)}"></span>${p.name}
        <button type="button" data-unlock="${pn}" aria-label="Unlock ${p.name}">×</button></span>`;
    }).join('');
    box.querySelectorAll('[data-unlock]').forEach((b) =>
      b.addEventListener('click', () => toggleLock(b.dataset.unlock)));
  }

  // ── Catalog list ──────────────────────────────────────────────────────────
  function filteredCatalog() {
    const q = ($('catSearch').value || '').trim().toLowerCase();
    const grp = $('catGroup').value;
    const sort = $('catSort').value;
    let list = state.catalog.filter((p) => {
      if (grp && p.group !== grp) return false;
      if (q && !(p.name.toLowerCase().includes(q) || String(p.pnumber).includes(q))) return false;
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
    const lockedSet = new Set(state.locked.map(String));
    $('catList').innerHTML = list.map((p) => {
      const picked = lockedSet.has(String(p.pnumber));
      return `<div class="cat-item ${picked ? 'is-picked' : ''}" data-pn="${p.pnumber}">
        <span class="cat-sw" style="background:${rgb(p.srgb)}"></span>
        <span><span class="cat-name">${p.name}</span><br>
          <span class="cat-meta">${p.group} · #${p.pnumber} · hue ${Math.round(p.hue)}° · chroma ${Math.round(p.chroma)}</span></span>
        <span class="cat-add">${picked ? '✓ locked' : '+ lock'}</span>
      </div>`;
    }).join('');
    $('catList').querySelectorAll('.cat-item').forEach((el) =>
      el.addEventListener('click', () => toggleLock(el.dataset.pn)));
  }

  function markCatalog() { renderCatalog(); }   // re-render to reflect lock state

  // ── Results ───────────────────────────────────────────────────────────────
  function renderResult(res) {
    const seq = res.sequence || [];
    const base = (res.baseline && res.baseline.volume) || state.baseline.volume || 0;
    const total = res.total_volume != null ? res.total_volume : (res.volume || 0);
    $('resVol').textContent = fmt(total);
    $('resN').textContent = seq.length || (res.n || 0);
    if (base > 0 && total > 0) {
      const pct = Math.round((total / base - 1) * 100);
      $('resDelta').innerHTML = `<span class="${pct >= 0 ? 'delta-pos' : ''}">${pct >= 0 ? '+' : ''}${pct}%</span>`;
    } else { $('resDelta').textContent = '—'; }

    if (seq.length) {
      const maxDelta = Math.max(1, ...seq.map((s) => s.delta || 0));
      $('resSeq').innerHTML = seq.map((s, i) => {
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
    } else {
      $('resSeq').innerHTML = '<p class="muted">No palette returned.</p>';
    }
    renderCoverage(res.coverage, (res.baseline && res.baseline.coverage) || state.baseline.coverage);
    drawPlot(res);
  }

  // ── Coverage error (ΔE2000 reachability of the catalog) ─────────────────────
  function renderCoverage(cov, baseCov) {
    const box = $('coverageBox');
    if (!cov || cov.mean_delta_e == null) { box.hidden = true; return; }
    box.hidden = false;
    const de = (v) => (v == null ? '—' : `ΔE ${v.toFixed(2)}`);
    $('covMeanDe').textContent = de(cov.mean_delta_e);
    $('covMaxDe').textContent = de(cov.max_delta_e);
    $('covWithin6').textContent = `${cov.within['6.0']}%`;
    // Volume coverage can exceed 100% (extent), so flag that distinctly from containment.
    const vc = cov.volume_coverage_pct;
    $('covVol').innerHTML = vc > 100
      ? `${vc}% <span class="muted" style="font-size:.62rem;font-weight:600;">(extent &gt; catalog)</span>`
      : `${vc}%`;

    // Stacked bar: reachable within ΔE1 / ΔE3 / ΔE6 / beyond (cumulative → disjoint slices).
    const w1 = cov.within['1.0'], w3 = cov.within['3.0'], w6 = cov.within['6.0'];
    const seg = [
      { w: w1, c: '#2e9e5b', label: '≤1' },
      { w: w3 - w1, c: '#7cc36a', label: '≤3' },
      { w: w6 - w3, c: '#e7b84b', label: '≤6' },
      { w: 100 - w6, c: 'rgba(0,0,0,0.12)', label: '>6' },
    ];
    $('covSeg').innerHTML = seg.filter((s) => s.w > 0.5).map((s) =>
      `<span style="width:${s.w}%;background:${s.c}" title="${s.label} ΔE: ${s.w.toFixed(1)}%">${s.w >= 8 ? s.label : ''}</span>`).join('');

    const parts = [`<strong>${cov.containment_pct}%</strong> of the ${cov.targets} catalog colours fall inside this gamut`];
    if (baseCov && baseCov.mean_delta_e != null) {
      const d = +(baseCov.mean_delta_e - cov.mean_delta_e).toFixed(2);
      if (Math.abs(d) >= 0.01) parts.push(`coverage error ${d > 0 ? 'down' : 'up'} ${Math.abs(d).toFixed(2)} ΔE vs the shipped 5 (${baseCov.mean_delta_e.toFixed(2)})`);
    }
    $('covNote').innerHTML = parts.join(' · ') + '.';
  }

  function drawPlot(res) {
    if (typeof Plotly === 'undefined') return;
    const traces = [];
    const ring = (hull, name, fill, line, dash) => {
      if (!hull || hull.length < 3) return null;
      const xs = hull.map((p) => p[0]).concat([hull[0][0]]);
      const ys = hull.map((p) => p[1]).concat([hull[0][1]]);
      return { x: xs, y: ys, mode: 'lines', name, fill: fill ? 'toself' : 'none',
        fillcolor: fill, line: { color: line, width: 2, dash: dash || 'solid' }, hoverinfo: 'skip' };
    };
    // Dashed reference = the human skin-colour gamut, so you can see if this palette
    // covers real skin tones. The measured mean chromaticities (per ethnicity × body
    // site) and their hull are from Xiao et al. 2017 — see state.skin.cite.
    const skin = state.skin;
    if (skin && skin.hull && skin.hull.length >= 3) {
      traces.push(ring(skin.hull, skin.label || 'human skin', 'rgba(208,138,108,0.12)', 'rgba(190,104,74,0.95)', 'dash'));
    }
    if (skin && skin.points && skin.points.length) {
      const ethColor = { Caucasian: '#e6a57e', Chinese: '#cf9b46', Kurdish: '#8c5e3c', Thai: '#a64d79' };
      // One trace per ethnicity for a legible legend; facial sites = filled diamond, body = open circle.
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
    const mainRing = ring(res.ab_hull, 'this set', 'rgba(59,110,245,0.13)', 'rgba(59,110,245,0.95)');
    if (mainRing) traces.push(mainRing);
    const pts = res.pigment_points || [];
    if (pts.length) {
      traces.push({
        x: pts.map((p) => p.a), y: pts.map((p) => p.b), mode: 'markers', name: 'pigments',
        text: pts.map((p) => p.name), hovertemplate: '%{text}<br>a* %{x:.0f}, b* %{y:.0f}<extra></extra>',
        marker: { size: 11, color: pts.map((p) => rgb(p.srgb)), line: { color: 'rgba(0,0,0,0.35)', width: 1 } },
      });
    }
    Plotly.react('gamutPlot', traces, {
      margin: { t: 10, r: 10, b: 76, l: 44 }, showlegend: true,
      legend: { orientation: 'h', y: -0.30, font: { size: 10 } },
      xaxis: { title: 'a* (green ← → red)', zeroline: true, zerolinecolor: '#ccc', range: [-70, 80] },
      yaxis: { title: 'b* (blue ← → yellow)', zeroline: true, zerolinecolor: '#ccc', range: [-80, 90], scaleanchor: 'x' },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    }, { displayModeBar: false, responsive: true });
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  async function runGreedy() {
    busy($('runBtn'), true);
    try {
      const res = await postJSON('/gamut/optimize', { size: state.size, locked: state.locked, pool: poolPnumbers() });
      renderResult(res);
    } catch (e) { $('resSeq').innerHTML = `<p class="muted">Search failed: ${e.message}</p>`; }
    finally { busy($('runBtn'), false); }
  }

  async function scorePicks() {
    if (!state.locked.length) { $('resSeq').innerHTML = '<p class="muted">Lock at least 4 pigments to score an exact set (or run the search).</p>'; return; }
    busy($('scoreBtn'), true);
    try {
      const res = await postJSON('/gamut/score', { pnumbers: state.locked });
      // shape it like a sequence so the same renderer works
      res.sequence = state.locked.map((pn) => {
        const p = state.byPn.get(String(pn));
        return { ...p, volume_after: res.volume, delta: null, locked: true };
      });
      res.total_volume = res.volume;
      renderResult(res);
    } catch (e) { $('resSeq').innerHTML = `<p class="muted">Scoring failed: ${e.message}</p>`; }
    finally { busy($('scoreBtn'), false); }
  }

  // ── Wiring ────────────────────────────────────────────────────────────────
  function wire() {
    $('sizeRange').addEventListener('input', (e) => { state.size = +e.target.value; $('sizeVal').textContent = e.target.value; });
    $('poolSeg').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
      state.poolMode = b.dataset.pool;
      $('poolSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('is-on', x === b));
    }));
    ['catSearch', 'catGroup', 'catSort'].forEach((id) => $(id).addEventListener('input', renderCatalog));
    $('runBtn').addEventListener('click', runGreedy);
    $('scoreBtn').addEventListener('click', scorePicks);
    $('clearBtn').addEventListener('click', () => { state.locked = []; renderLocked(); markCatalog(); });
  }

  async function init() {
    wire();
    try {
      const data = await (await fetch('/gamut/catalog')).json();
      state.catalog = data.pigments || [];
      state.baseline = data.baseline || { volume: 0, ab_hull: [] };
      state.skin = data.skin_gamut || null;
      if (state.skin && state.skin.cite) { const el = $('skinCite'); if (el) el.textContent = state.skin.cite; }
      state.byPn = new Map(state.catalog.map((p) => [String(p.pnumber), p]));
      const groups = Array.from(new Set(state.catalog.map((p) => p.group))).sort();
      $('catGroup').innerHTML = '<option value="">All groups</option>' + groups.map((g) => `<option value="${g}">${g}</option>`).join('');
      renderCatalog();
      renderLocked();
      drawPlot({ ab_hull: [], pigment_points: [] });   // show the baseline outline immediately
    } catch (e) {
      $('catList').innerHTML = `<p class="muted" style="padding:12px;">Failed to load catalog: ${e.message}</p>`;
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
