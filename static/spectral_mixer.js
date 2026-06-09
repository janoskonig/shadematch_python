/**
 * Spectral mixer (laboratory mode).
 *
 * Same mixing controls as the main app (click a pigment to add a drop, minus to
 * remove), but the engine is a spectral Kubelka–Munk pigment-mixing *simulation*
 * based on measured reflectance spectra of the base pigments — NOT literal physical
 * paint mixing. Real paint also depends on concentration, layer thickness, binder,
 * particle size, opacity and substrate, none of which are modelled here. The
 * per-pigment count is therefore a relative digital pigment amount, not a real drop.
 *
 * All color science is delegated to the spectral.js engine (static/spectral.js,
 * Ronald van Wijnen, MIT): Kubelka–Munk mixing in K/S space and reflectance→XYZ
 * convolution against the CIE observer weighted by the D65 illuminant. This file
 * resamples the measured base spectra onto the engine grid, wires the (main-app)
 * palette UI, and draws the Plotly spectra. It reimplements none of that math.
 *
 * Depends on:
 *   - window.spectral  (static/spectral.js) — must load BEFORE this file.
 *   - spectrum_plots   (injected by /spectral) — measured base reflectances,
 *                        keyed white/black/red/yellow/blue.
 *   - Plotly           (CDN) — spectrum charts.
 */

// Fail fast if the engine isn't present (template ordering / SW cache mistakes).
if (!window.spectral || !window.spectral.Color || !window.spectral.mix) {
    throw new Error('spectral.js engine not loaded — include static/spectral.js before spectral_mixer.js');
}

// Canonical 38-bin reflectance grid used by spectral.js: 380–750 nm @10 nm.
// Defined once and reused for resampling, new spectral.Color(R38), and plotting.
const WAVELENGTHS = Array.from({ length: 38 }, (_, i) => 380 + i * 10);

const PALETTE_ORDER = ['white', 'black', 'red', 'yellow', 'blue'];
const PALETTE_LABEL = { white: 'White', black: 'Black', red: 'Red', yellow: 'Yellow', blue: 'Blue' };

// Same strict win threshold as the main game. Targets are generated from a pigment
// recipe mixed by the same engine, and the engine's mix is ratio-invariant, so an
// exact integer-drop solution (ΔE → 0) always exists.
const MATCH_PERFECT_DELTA_E = 0.01;
const isPerfectMatch = (deltaE) => Number.isFinite(deltaE) && deltaE <= MATCH_PERFECT_DELTA_E;

// Per-pigment "relative amount": a continuous 0–100 scale in fine 0.01 increments
// (dial drag / keyboard); also nudged ±1 by tap / ± buttons.
const AMOUNT_MAX = 100;
const AMOUNT_STEP = 0.1;
const roundStep = (v) => Math.round(v / AMOUNT_STEP) * AMOUNT_STEP;
// Round to the 0.1 grid and drop float noise / trailing zeros: 0.1, 99.9, 37.5, 100.
const formatAmount = (n) => String(Math.round(n * 10) / 10);

// Dial geometry. The SVG is rotated 135° in CSS so the 270° arc opens at the bottom.
const DIAL_R = 44;
const DIAL_C = 2 * Math.PI * DIAL_R;   // full circumference (px in viewBox units)
const DIAL_ARC = DIAL_C * 0.75;        // 270° usable sweep
const DRAG_PX_PER_UNIT = 4;            // vertical px per 1.0 (≈400px sweeps the full 0–100)
const DRAG_THRESHOLD = 4;              // px before a press counts as a drag (vs a tap)

// Leveling over the ported (Mixbox) catalog targets. Progress is local to the device.
const SPECTRAL_COMPLETED_KEY = 'spectral_completed';
const SPECTRAL_INDEX_KEY = 'spectral_shade_index';
const SHADES_PER_LEVEL = 8;

function loadCompletedSet() {
    try {
        const a = JSON.parse(localStorage.getItem(SPECTRAL_COMPLETED_KEY) || '[]');
        return new Set(Array.isArray(a) ? a : []);
    } catch (_) { return new Set(); }
}
function saveCompletedSet(set) {
    try { localStorage.setItem(SPECTRAL_COMPLETED_KEY, JSON.stringify(Array.from(set))); } catch (_) { /* ignore */ }
}

class SpectralMixer {
    constructor() {
        // bases[color] = { name, swatch, raw:{wavelengths,reflectances}, R38, color }
        this.bases = {};
        this.relativeAmounts = {};

        PALETTE_ORDER.forEach((key) => {
            const data = spectrum_plots[key];
            if (!data) { console.error(`No measured spectrum for base "${key}"`); return; }
            const R38 = resampleToGrid(data.wavelengths, data.reflectances);
            const color = new spectral.Color(R38);
            this.bases[key] = {
                name: data.name || key,
                swatch: color.sRGB,
                raw: { wavelengths: data.wavelengths, reflectances: data.reflectances },
                R38: R38,
                color: color,
            };
            this.relativeAmounts[key] = 0;
        });

        this.currentMix = document.getElementById('currentMix');
        this.targetSwatch = document.getElementById('targetColor');
        this.colorCodesLine = document.getElementById('colorCodesLine');
        this.palette = document.getElementById('spectralPalette');
        this.status = document.getElementById('spectralStatus');
        this.matchBar = {
            container: document.getElementById('matchBarContainer'),
            fill: document.getElementById('matchBarFill'),
            label: document.getElementById('matchBarLabel'),
        };

        this.currentRgb = [255, 255, 255];
        this.target = null;          // { id, rgb, name }
        this.deltaReqId = 0;         // latest-wins guard for async ΔE responses
        this.scoreTimer = null;      // debounce handle for /calculate during drags
        this.bestDeltaE = null;      // closest ΔE reached on the current shade

        // Leveling over the ported catalog (Mixbox) targets.
        this.catalog = [];
        this.shadeIndex = 0;
        this.completed = loadCompletedSet();

        this.shadeLabel = document.getElementById('shadeLabel');
        this.shadeName = document.getElementById('shadeName');
        this.levelLabel = document.getElementById('levelLabel');
        this.progressFill = document.getElementById('spectralProgressFill');
        this.perceptionModal = document.getElementById('perceptionModal');

        this.setupDials();
        this.initializeControls();
        this.initializeBasePlots();
        this.loadCatalog();
    }

    // Paint the static dial parts: the 270° track and each ring's pigment colour.
    setupDials() {
        if (!this.palette) return;
        PALETTE_ORDER.forEach((key) => {
            const dial = this.palette.querySelector(`.dial[data-color="${key}"]`);
            if (!dial) return;
            const track = dial.querySelector('.dial-track');
            const fill = dial.querySelector('.dial-fill');
            if (track) track.style.strokeDasharray = `${DIAL_ARC.toFixed(2)} ${DIAL_C.toFixed(2)}`;
            if (fill) {
                fill.style.stroke = this.dialColor(key);
                fill.style.strokeDasharray = `0 ${DIAL_C.toFixed(2)}`;
                fill.style.display = 'none';
            }
        });
    }

    // Ring colour = the pigment's own sRGB, but force a visible grey for near-white pigments.
    dialColor(key) {
        const [r, g, b] = this.bases[key] ? this.bases[key].swatch : [120, 120, 120];
        const lum = 0.299 * r + 0.587 * g + 0.114 * b;
        return lum > 210 ? '#8a8580' : `rgb(${r}, ${g}, ${b})`;
    }

    // Move keyboard focus to the previous/next pigment's value field (wraps around).
    focusAdjacentPigment(key, dir) {
        const i = PALETTE_ORDER.indexOf(key);
        if (i < 0) return;
        const next = PALETTE_ORDER[(i + dir + PALETTE_ORDER.length) % PALETTE_ORDER.length];
        const el = this.palette.querySelector(`input.drop-badge[data-badge-for="${next}"]`);
        if (el) { el.focus(); el.select(); }
    }

    // Independent per-pigment amount, 0–100 on the 0.1 grid.
    setAmount(key, value) {
        const v = Math.max(0, Math.min(AMOUNT_MAX, roundStep(value)));
        this.relativeAmounts[key] = v;
        this.syncControl(key);
        this.updateMixedColor();
    }

    // Whole-drop nudge from tap / ± buttons (bumps the badge).
    nudge(key, delta, { bump = false } = {}) {
        const v = Math.max(0, Math.min(AMOUNT_MAX, this.relativeAmounts[key] + delta));
        this.relativeAmounts[key] = roundStep(v);
        this.syncControl(key, { bump });
        this.updateMixedColor();
    }

    initializeControls() {
        if (!this.palette) return;

        // Tap the disc → add a whole drop. A tap that followed a dial drag is suppressed.
        this.palette.querySelectorAll('.color-circle').forEach((circle) => {
            const key = circle.dataset.color;
            circle.addEventListener('click', () => {
                const dial = circle.closest('.dial');
                if (dial && dial._suppressTap) { dial._suppressTap = false; return; }
                this.nudge(key, +1, { bump: true });
                circle.classList.add('is-tapped');
                setTimeout(() => circle.classList.remove('is-tapped'), 150);
                if (navigator.vibrate) navigator.vibrate(12);
            });
        });

        this.palette.querySelectorAll('.minus-button').forEach((button) => {
            const key = button.dataset.color;
            button.addEventListener('click', () => {
                if (this.relativeAmounts[key] <= 0) return;
                this.nudge(key, -1, { bump: true });
            });
        });

        // Dial: drag up/down for a continuous amount (shares relativeAmounts with tap/±).
        this.palette.querySelectorAll('.dial').forEach((dial) => {
            const key = dial.dataset.color;
            let startY = 0;
            let startAmount = 0;
            let active = false;
            let dragging = false;

            dial.addEventListener('pointerdown', (e) => {
                startY = e.clientY;
                startAmount = this.relativeAmounts[key];
                active = true;
                dragging = false;
                dial._suppressTap = false;
                try { dial.setPointerCapture(e.pointerId); } catch (_) { /* synthetic / unsupported */ }
            });
            dial.addEventListener('pointermove', (e) => {
                if (!active) return;
                const dy = startY - e.clientY;                 // drag up = increase
                if (!dragging && Math.abs(dy) > DRAG_THRESHOLD) dragging = true;
                if (dragging) {
                    this.setAmount(key, startAmount + dy / DRAG_PX_PER_UNIT);
                    e.preventDefault();
                }
            });
            const endDrag = (e) => {
                if (!active) return;
                active = false;
                try { dial.releasePointerCapture(e.pointerId); } catch (_) { /* no-op */ }
                if (dragging) {
                    dial._suppressTap = true;                  // swallow the synthetic click
                    setTimeout(() => { dial._suppressTap = false; }, 400);
                }
                dragging = false;
            };
            dial.addEventListener('pointerup', endDrag);
            dial.addEventListener('pointercancel', endDrag);

            // Keyboard (role="slider"): arrows ±1, Shift+arrow ±0.01 (fine), Home/End to ends.
            dial.addEventListener('keydown', (e) => {
                let handled = true;
                const step = e.shiftKey ? AMOUNT_STEP : 1;
                if (e.key === 'ArrowUp' || e.key === 'ArrowRight') this.setAmount(key, this.relativeAmounts[key] + step);
                else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') this.setAmount(key, this.relativeAmounts[key] - step);
                else if (e.key === 'Home') this.setAmount(key, 0);
                else if (e.key === 'End') this.setAmount(key, AMOUNT_MAX);
                else handled = false;
                if (handled) e.preventDefault();
            });
        });

        // Editable value badges — type a precise amount; ↑/↓ step, Shift+↑/↓ = 0.01.
        this.palette.querySelectorAll('.drop-badge').forEach((input) => {
            if (input.tagName !== 'INPUT') return;
            const key = input.dataset.badgeFor;
            const commit = () => {
                const v = parseFloat(input.value);
                this.setAmount(key, Number.isFinite(v) ? v : 0);
            };
            input.addEventListener('focus', () => input.select());
            input.addEventListener('change', commit);
            input.addEventListener('keydown', (e) => {
                const step = e.shiftKey ? AMOUNT_STEP : 1;
                if (e.key === 'Enter') { e.preventDefault(); commit(); input.blur(); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); this.setAmount(key, this.relativeAmounts[key] + step); }
                else if (e.key === 'ArrowDown') { e.preventDefault(); this.setAmount(key, this.relativeAmounts[key] - step); }
                else if (e.key === 'ArrowRight') { e.preventDefault(); this.focusAdjacentPigment(key, +1); }
                else if (e.key === 'ArrowLeft') { e.preventDefault(); this.focusAdjacentPigment(key, -1); }
            });
        });

        // Keep Tab order clean: the value field is the one keyboard stop per pigment
        // (Tab / Shift+Tab changes colour). The dial stays mouse/touch-only, and the
        // − button is reachable but skipped by Tab (type 0 or ↓ on the field instead).
        this.palette.querySelectorAll('.dial').forEach((d) => { d.removeAttribute('tabindex'); d.removeAttribute('role'); });
        this.palette.querySelectorAll('.minus-button, .color-circle').forEach((b) => { b.tabIndex = -1; });

        const reset = document.getElementById('spectralResetBtn');
        if (reset) reset.addEventListener('click', () => this.resetMix());

        const judge = document.getElementById('spectralJudgeBtn');
        if (judge) judge.addEventListener('click', () => this.openPerception());

        const skip = document.getElementById('spectralSkipBtn');
        if (skip) skip.addEventListener('click', () => this.nextShade());

        if (this.perceptionModal) {
            this.perceptionModal.querySelectorAll('[data-perc]').forEach((btn) => {
                btn.addEventListener('click', () => this.judge(btn.dataset.perc));
            });
            const cancel = document.getElementById('perceptionCancel');
            if (cancel) cancel.addEventListener('click', () => this.closePerception());
            this.perceptionModal.addEventListener('click', (e) => {
                if (e.target === this.perceptionModal) this.closePerception();
            });
        }
    }

    resetMix() {
        PALETTE_ORDER.forEach((key) => { this.relativeAmounts[key] = 0; this.syncControl(key); });
        this.updateMixedColor();
    }

    // Reflect a pigment's amount on its badge and dial ring (kept in sync).
    syncControl(key, { bump = false } = {}) {
        const n = this.relativeAmounts[key];
        const txt = formatAmount(n);
        const badge = this.palette.querySelector(`.drop-badge[data-badge-for="${key}"]`);
        const fill = this.palette.querySelector(`.dial-fill[data-fill-for="${key}"]`);
        const dial = this.palette.querySelector(`.dial[data-color="${key}"]`);
        if (badge) {
            // Editable input badge: write .value (typing only commits on change, so this
            // never fights mid-edit, and ↑/↓ in the field still reflect immediately).
            if (badge.tagName === 'INPUT') badge.value = txt;
            else badge.textContent = txt;
            if (bump) {
                badge.classList.add('is-bumped');
                setTimeout(() => badge.classList.remove('is-bumped'), 150);
            }
        }
        if (fill) {
            const frac = Math.max(0, Math.min(1, n / AMOUNT_MAX));
            fill.style.strokeDasharray = `${(frac * DIAL_ARC).toFixed(2)} ${DIAL_C.toFixed(2)}`;
            fill.style.display = frac > 0 ? '' : 'none';  // hide the round-cap dot at zero
        }
        if (dial) dial.setAttribute('aria-valuenow', String(n));
    }

    initializeBasePlots() {
        Object.entries(this.bases).forEach(([key, base]) => {
            const div = document.getElementById(`spectrumPlot-${key}`);
            if (!div) return;
            const [r, g, b] = base.swatch;
            // Full-resolution measured curve, titled with the real pigment name.
            Plotly.newPlot(div, [{
                x: base.raw.wavelengths,
                y: base.raw.reflectances,
                type: 'scatter',
                mode: 'lines',
                name: base.name,
                line: { color: `rgb(${r}, ${g}, ${b})`, width: 2 },
            }], spectrumLayout(base.name), { displayModeBar: false, responsive: true });
        });
    }

    updateMixedColor() {
        const entries = Object.entries(this.relativeAmounts).filter(([, n]) => n > 0);
        const total = entries.reduce((acc, [, n]) => acc + n, 0);

        let rgb;
        let reflectances;
        if (total === 0) {
            rgb = [255, 255, 255];
            reflectances = WAVELENGTHS.map(() => 1);
        } else {
            // Kubelka–Munk subtractive mixing, done entirely by the engine.
            // factor = relative digital pigment amount (not a real drop).
            const mixArgs = entries.map(([key, relativeAmount]) => [this.bases[key].color, relativeAmount]);
            const mixed = spectral.mix(...mixArgs);
            rgb = mixed.sRGB;
            reflectances = mixed.R;
        }

        const [r, g, b] = rgb;
        this.currentRgb = rgb;
        if (this.currentMix) this.currentMix.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
        if (this.colorCodesLine) {
            const hex = '#' + [r, g, b].map((v) => v.toString(16).toUpperCase().padStart(2, '0')).join('');
            this.colorCodesLine.textContent = `rgb(${r}, ${g}, ${b}) · R${r} G${g} B${b} · ${hex}`;
        }
        this.updateRecipeStrip(total);
        this.drawMixedSpectrum(reflectances, rgb);
        drawSpectrumOverlay('mixSpectrumOverlay', reflectances, rgb);
        this.scoreAgainstTarget(total);
    }

    // ── Leveling over the ported catalog (Mixbox) targets ───────────────────

    loadCatalog() {
        fetch('/api/target-colors')
            .then((res) => res.json())
            .then((data) => {
                const rows = (data && data.status === 'success' && Array.isArray(data.colors)) ? data.colors : [];
                this.catalog = rows.filter((r) => Array.isArray(r.rgb) && r.rgb.length === 3);
                if (!this.catalog.length) {
                    if (this.status) this.status.textContent = 'No target colours available.';
                    return;
                }
                // Resume at the saved shade, else the first not-yet-completed one.
                let idx = parseInt(localStorage.getItem(SPECTRAL_INDEX_KEY) || '', 10);
                if (!Number.isInteger(idx) || idx < 0 || idx >= this.catalog.length) {
                    idx = this.catalog.findIndex((t) => !this.completed.has(t.id));
                    if (idx < 0) idx = 0;
                }
                this.loadShade(idx);
            })
            .catch(() => { if (this.status) this.status.textContent = 'Could not load target colours.'; });
    }

    loadShade(index) {
        if (!this.catalog.length) return;
        this.shadeIndex = Math.max(0, Math.min(this.catalog.length - 1, index));
        try { localStorage.setItem(SPECTRAL_INDEX_KEY, String(this.shadeIndex)); } catch (_) { /* ignore */ }
        const t = this.catalog[this.shadeIndex];
        this.target = { id: t.id, rgb: t.rgb.slice(0, 3), name: t.name || ('Shade ' + (this.shadeIndex + 1)) };
        this.bestDeltaE = null;

        const [r, g, b] = this.target.rgb;
        if (this.targetSwatch) this.targetSwatch.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
        // Overlay the target's reflectance on its window. This is a metameric reconstruction
        // from the target's sRGB (the catalog has no measured spectrum), so it's drawn dashed
        // to distinguish it from the solid, true mix curve it sits beside.
        try { drawSpectrumOverlay('targetSpectrumOverlay', new spectral.Color(this.target.rgb).R, this.target.rgb, { dash: 'dot' }); }
        catch (_) { /* engine missing — skip overlay */ }
        if (this.matchBar.container) this.matchBar.container.style.display = 'none';
        if (this.status) {
            this.status.classList.remove('is-win');
            this.status.textContent = this.completed.has(t.id)
                ? 'Already matched — mix again or skip ahead.'
                : 'Mix to match, then tap “Judge match”.';
        }
        this.resetMix();
        this.updateProgressUI();
    }

    nextShade() {
        const n = this.catalog.length;
        if (!n) return;
        for (let step = 1; step <= n; step += 1) {
            const i = (this.shadeIndex + step) % n;
            if (!this.completed.has(this.catalog[i].id)) { this.loadShade(i); return; }
        }
        this.loadShade((this.shadeIndex + 1) % n);   // all done → just advance
    }

    levelInfo() {
        const done = this.completed.size;
        const total = this.catalog.length || 1;
        return { done, total, level: Math.floor(done / SHADES_PER_LEVEL) + 1 };
    }

    updateProgressUI() {
        const { done, total, level } = this.levelInfo();
        if (this.shadeLabel) this.shadeLabel.textContent = `Shade ${this.shadeIndex + 1} of ${total}`;
        if (this.shadeName && this.target) {
            this.shadeName.textContent = this.completed.has(this.target.id) ? `${this.target.name} ✓` : this.target.name;
        }
        if (this.levelLabel) this.levelLabel.textContent = `Level ${level} · ${done}/${total}`;
        if (this.progressFill) this.progressFill.style.width = (100 * done / total).toFixed(1) + '%';
    }

    openPerception() {
        if (!this.target || !this.perceptionModal) return;
        const [tr, tg, tb] = this.target.rgb;
        const [mr, mg, mb] = this.currentRgb;
        const pt = document.getElementById('percTarget');
        const pm = document.getElementById('percMix');
        if (pt) pt.style.backgroundColor = `rgb(${tr}, ${tg}, ${tb})`;
        if (pm) pm.style.backgroundColor = `rgb(${mr}, ${mg}, ${mb})`;
        const meta = document.getElementById('perceptionMeta');
        if (meta) {
            meta.textContent = (this.bestDeltaE != null)
                ? `Your closest so far: ΔE ${this.bestDeltaE.toFixed(1)}. Compare your mix (right) to the target (left).`
                : 'Compare your mix (right) to the target (left).';
        }
        this.perceptionModal.style.display = 'flex';
    }

    closePerception() {
        if (this.perceptionModal) this.perceptionModal.style.display = 'none';
    }

    // Perceptual self-report — the completion path (ΔE≈0 isn't reachable spectrally).
    judge(perception) {
        this.closePerception();
        if (!this.target) return;
        if (perception === 'identical' || perception === 'acceptable') {
            this.completed.add(this.target.id);
            saveCompletedSet(this.completed);
            if (this.status) {
                this.status.textContent = perception === 'identical'
                    ? '✓ No perceptible difference — shade complete!'
                    : '✓ Acceptable match — shade complete!';
                this.status.classList.add('is-win');
            }
            this.updateProgressUI();
            setTimeout(() => this.nextShade(), 900);
        } else {
            if (this.status) {
                this.status.classList.remove('is-win');
                this.status.textContent = 'Still too different — keep mixing or skip.';
            }
        }
    }

    scoreAgainstTarget(total) {
        if (!this.target) return;
        if (total === 0) {
            if (this.scoreTimer) { clearTimeout(this.scoreTimer); this.scoreTimer = null; }
            this.deltaReqId += 1;   // cancel any in-flight response
            if (this.matchBar.container) this.matchBar.container.style.display = 'none';
            return;
        }
        // Debounce: a dial drag fires updateMixedColor on every pointermove, which would
        // otherwise POST /calculate dozens of times a second. Coalesce to the latest value.
        if (this.scoreTimer) clearTimeout(this.scoreTimer);
        this.scoreTimer = setTimeout(() => { this.scoreTimer = null; this.requestDeltaE(); }, 120);
    }

    requestDeltaE() {
        if (!this.target) return;
        const mixed = this.currentRgb;
        const id = (this.deltaReqId += 1);
        fetch('/calculate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: this.target.rgb, mixed }),
        })
            .then((res) => res.json())
            .then((data) => {
                if (id !== this.deltaReqId) return; // a newer action superseded this one
                const de = Number(data && data.delta_e);
                if (!Number.isFinite(de)) return;
                if (this.bestDeltaE == null || de < this.bestDeltaE) this.bestDeltaE = de;
                this.updateMatchBar(de);
            })
            .catch(() => { /* offline / transient — leave the bar as-is */ });
    }

    // Closeness feedback only (exponential decay, K=3). Completion is the perceptual judgment.
    updateMatchBar(deltaE) {
        const { container, fill, label } = this.matchBar;
        if (!container || !fill || !label) return;
        container.style.display = '';

        if (isPerfectMatch(deltaE)) {
            fill.style.width = '100%';
            fill.style.backgroundColor = 'var(--accent-success)';
            label.textContent = 'Exact!';
            return;
        }
        const K = 3;
        const progress = Math.max(0, Math.min(99, 100 * Math.exp(-deltaE / K)));
        fill.style.width = progress + '%';
        if (progress < 19) { fill.style.backgroundColor = 'var(--accent-danger)'; label.textContent = 'Far'; }
        else if (progress < 51) { fill.style.backgroundColor = 'var(--accent-warning)'; label.textContent = 'Closer'; }
        else if (progress < 85) { fill.style.backgroundColor = '#8BC34A'; label.textContent = 'Very close'; }
        else { fill.style.backgroundColor = '#8BC34A'; label.textContent = 'Nearly there!'; }
    }

    updateRecipeStrip(total) {
        PALETTE_ORDER.forEach((key) => {
            const seg = document.querySelector(`#recipeStrip .recipe-seg[data-color="${key}"]`);
            if (seg) seg.style.flexGrow = total > 0 ? this.relativeAmounts[key] : 0;
        });
    }

    drawMixedSpectrum(reflectances, rgb) {
        const div = document.getElementById('mixedSpectrum');
        if (!div) return;
        const [r, g, b] = rgb;
        // react (not newPlot): a dial drag redraws this every frame, so reuse the trace
        // in place instead of tearing the plot down and rebuilding it each time.
        Plotly.react(div, [{
            x: WAVELENGTHS,
            y: reflectances,
            type: 'scatter',
            mode: 'lines',
            name: 'Mixed',
            line: { color: `rgb(${r}, ${g}, ${b})`, width: 2 },
        }], spectrumLayout('Mixed Spectrum'), { displayModeBar: false, responsive: true });
    }
}

/**
 * Linearly interpolate a measured (wavelengths, reflectances) curve onto the
 * canonical WAVELENGTHS grid.
 *
 * Outside the measured range we HOLD the nearest measured endpoint value (never
 * fall to zero) — pragmatic and adequate for visible-range rendering, but an
 * approximation at the band edges where our CSVs (~405–730 nm) don't reach.
 * Each result is clamped to [1e-4, 1.0] because Kubelka–Munk K/S = (1−R)²/(2R)
 * diverges at R=0 and is 0 at R=1.
 */
function resampleToGrid(wavelengths, reflectances) {
    const x = wavelengths;
    const y = reflectances;
    const last = x.length - 1;
    return WAVELENGTHS.map((xi) => {
        let v;
        if (xi <= x[0]) {
            v = y[0];
        } else if (xi >= x[last]) {
            v = y[last];
        } else {
            let j = 1;
            while (x[j] < xi) j += 1;
            const x0 = x[j - 1], x1 = x[j];
            const y0 = y[j - 1], y1 = y[j];
            v = y0 + (y1 - y0) * (xi - x0) / (x1 - x0);
        }
        return Math.max(1e-4, Math.min(1, v));
    });
}

function spectrumLayout(title) {
    return {
        title: { text: title, font: { size: 13 } },
        xaxis: { title: 'Wavelength (nm)', range: [380, 750], showgrid: true, gridcolor: '#eee' },
        yaxis: { title: 'Reflectance', range: [0, 1], showgrid: true, gridcolor: '#eee' },
        margin: { t: 30, r: 12, b: 36, l: 44 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
    };
}

// Transparent reflectance curve drawn on top of a colour window. Line colour is chosen
// to contrast with the swatch underneath. Static (decorative) and axis-free.
//
// `dash` marks a curve that is a metameric reconstruction (a reflectance inferred from
// an sRGB), not a true mixed spectrum — drawn dashed so it never reads as equivalent to
// the solid, genuine mix curve next to it.
function drawSpectrumOverlay(divId, reflectances, swatchRgb, { dash = 'solid' } = {}) {
    const div = document.getElementById(divId);
    if (!div || !reflectances || typeof Plotly === 'undefined') return;
    const [r, g, b] = swatchRgb || [255, 255, 255];
    const lum = 0.299 * r + 0.587 * g + 0.114 * b;
    const line = lum > 140 ? 'rgba(20,20,20,0.85)' : 'rgba(255,255,255,0.92)';
    Plotly.react(div, [{
        x: WAVELENGTHS, y: reflectances, type: 'scatter', mode: 'lines',
        line: { color: line, width: 2.5, shape: 'spline', dash: dash }, hoverinfo: 'skip',
    }], {
        margin: { t: 10, r: 8, b: 8, l: 8 },
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: { visible: false, range: [380, 750], fixedrange: true },
        // Top headroom (>1) so a flat reflectance≈1 curve (e.g. the empty white mix)
        // isn't pinned against the frame and half-clipped by its own line width.
        yaxis: { visible: false, range: [0, 1.08], fixedrange: true },
        showlegend: false,
    }, { displayModeBar: false, responsive: true, staticPlot: true });
}

document.addEventListener('DOMContentLoaded', function () {
    // eslint-disable-next-line no-new
    new SpectralMixer();
});
