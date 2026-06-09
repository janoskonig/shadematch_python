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

// Per-pigment "relative amount" is continuous (dial drag) but also nudged ±1 by tap / buttons.
const AMOUNT_MAX = 10;
const formatAmount = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

// Dial geometry. The SVG is rotated 135° in CSS so the 270° arc opens at the bottom.
const DIAL_R = 44;
const DIAL_C = 2 * Math.PI * DIAL_R;   // full circumference (px in viewBox units)
const DIAL_ARC = DIAL_C * 0.75;        // 270° usable sweep
const DRAG_PX_PER_UNIT = 16;           // vertical px of drag per 1.0 of amount
const DRAG_THRESHOLD = 4;              // px before a press counts as a drag (vs a tap)

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
        this.target = null;      // { rgb, recipe }
        this.solved = false;
        this.deltaReqId = 0;     // latest-wins guard for async ΔE responses

        this.setupDials();
        this.initializeControls();
        this.initializeBasePlots();
        this.newTarget();
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

    // Clamp + round to 0.1 and push the new amount everywhere.
    setAmount(key, value) {
        const v = Math.max(0, Math.min(AMOUNT_MAX, Math.round(value * 10) / 10));
        this.relativeAmounts[key] = v;
        this.syncControl(key);
        this.updateMixedColor();
    }

    // Whole-drop nudge from tap / ± buttons (bumps the badge).
    nudge(key, delta, { bump = false } = {}) {
        const v = Math.max(0, Math.min(AMOUNT_MAX, this.relativeAmounts[key] + delta));
        this.relativeAmounts[key] = Math.round(v * 10) / 10;
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

            // Keyboard (role="slider"): arrows nudge by 0.5, Home/End jump to the ends.
            dial.addEventListener('keydown', (e) => {
                let handled = true;
                if (e.key === 'ArrowUp' || e.key === 'ArrowRight') this.setAmount(key, this.relativeAmounts[key] + 0.5);
                else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') this.setAmount(key, this.relativeAmounts[key] - 0.5);
                else if (e.key === 'Home') this.setAmount(key, 0);
                else if (e.key === 'End') this.setAmount(key, AMOUNT_MAX);
                else handled = false;
                if (handled) e.preventDefault();
            });
        });

        const reset = document.getElementById('spectralResetBtn');
        if (reset) {
            reset.addEventListener('click', () => this.resetMix());
        }
        const newTarget = document.getElementById('newTargetBtn');
        if (newTarget) {
            newTarget.addEventListener('click', () => this.newTarget());
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
            badge.textContent = txt;
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
        this.scoreAgainstTarget(total);
    }

    // ── Game: target generation & matching ──────────────────────────────────

    // Random recipe of 2–3 pigments, small counts — keeps the exact-match ratio
    // discoverable. Mixed by the engine so the target is reachable by construction.
    generateRecipe() {
        const pool = PALETTE_ORDER.slice();
        for (let i = pool.length - 1; i > 0; i -= 1) {
            const j = Math.floor(Math.random() * (i + 1));
            [pool[i], pool[j]] = [pool[j], pool[i]];
        }
        const k = 2 + Math.floor(Math.random() * 2); // 2 or 3 pigments
        const recipe = {};
        pool.slice(0, k).forEach((key) => { recipe[key] = 1 + Math.floor(Math.random() * 4); }); // 1..4
        return recipe;
    }

    newTarget() {
        const recipe = this.generateRecipe();
        const mixArgs = Object.entries(recipe).map(([key, n]) => [this.bases[key].color, n]);
        const rgb = spectral.mix(...mixArgs).sRGB;
        this.target = { rgb, recipe };
        this.solved = false;

        if (this.targetSwatch) this.targetSwatch.style.backgroundColor = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
        if (this.matchBar.container) this.matchBar.container.style.display = 'none';
        if (this.status) {
            this.status.textContent = 'Match the target by mixing pigments.';
            this.status.classList.remove('is-win');
        }
        this.resetMix();
    }

    scoreAgainstTarget(total) {
        if (!this.target) return;
        if (total === 0) {
            // Nothing mixed yet — keep the bar hidden until the player acts.
            if (this.matchBar.container) this.matchBar.container.style.display = 'none';
            return;
        }
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
                this.updateMatchBar(de);
                if (isPerfectMatch(de) && !this.solved) this.onWin(de);
            })
            .catch(() => { /* offline / transient — leave the bar as-is */ });
    }

    // Mirrors the main game's match bar (exponential decay, K=3).
    updateMatchBar(deltaE) {
        const { container, fill, label } = this.matchBar;
        if (!container || !fill || !label) return;
        container.style.display = '';

        if (isPerfectMatch(deltaE)) {
            fill.style.width = '100%';
            fill.style.backgroundColor = 'var(--accent-success)';
            label.textContent = 'Match!';
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

    onWin(deltaE) {
        this.solved = true;
        if (navigator.vibrate) navigator.vibrate([15, 40, 15]);
        if (this.status) {
            this.status.textContent = `Matched! ΔE ${deltaE.toFixed(2)} 🎉`;
            this.status.classList.add('is-win');
        }
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
        Plotly.newPlot(div, [{
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

document.addEventListener('DOMContentLoaded', function () {
    // eslint-disable-next-line no-new
    new SpectralMixer();
});
