/**
 * Reusable "dialer" knob behaviour for a pigment palette.
 *
 * Markup per pigment (inside a `.color-control`): a `.dial[data-color]` wrapping the
 * `.color-circle`, with an SVG ring (`Dialer.ringSVG(key)`). Tap the disc to add a whole
 * drop; drag up/down anywhere on the dial for a continuous amount; arrow keys nudge ±0.5.
 *
 * Pure DOM behaviour — the host page owns the amount state. Provide getAmount/onInput/onTap
 * callbacks; call the returned `render(key, value)` whenever the amount changes (including
 * from ± buttons or reset) so the ring stays in sync.
 */
(function (global) {
    'use strict';

    var R = 44;
    var C = 2 * Math.PI * R;       // full circumference (viewBox units)
    var ARC = C * 0.75;            // 270° usable sweep
    var PX_PER_UNIT = 4;           // vertical drag px per 1.0 (≈400px sweeps a 0–100 range)
    var THRESHOLD = 4;             // px before a press counts as a drag (vs a tap)
    var STEP = 0.01;               // value grid (fine 0.01 increments)

    function ringSVG(key) {
        return '<svg class="dial-ring" viewBox="0 0 100 100">' +
            '<circle class="dial-track" cx="50" cy="50" r="' + R + '"></circle>' +
            '<circle class="dial-fill" data-fill-for="' + key + '" cx="50" cy="50" r="' + R + '"></circle>' +
            '</svg>';
    }

    /**
     * attach(paletteEl, opts) → { render(key, value) }
     * opts: { max=100, colorFor(key)->cssColor, getAmount(key)->number,
     *         onInput(key, value), onTap(key) }
     */
    function attach(paletteEl, opts) {
        opts = opts || {};
        var max = opts.max || 100;

        function clampRound(v) {
            return Math.max(0, Math.min(max, Math.round(v / STEP) * STEP));
        }
        function setVal(key, v) { if (opts.onInput) opts.onInput(key, clampRound(v)); }

        paletteEl.querySelectorAll('.dial').forEach(function (dial) {
            var key = dial.dataset.color;
            var track = dial.querySelector('.dial-track');
            var fill = dial.querySelector('.dial-fill');
            if (track) track.style.strokeDasharray = ARC.toFixed(2) + ' ' + C.toFixed(2);
            if (fill) {
                fill.style.stroke = opts.colorFor ? opts.colorFor(key) : 'var(--accent-primary)';
                fill.style.strokeDasharray = '0 ' + C.toFixed(2);
                fill.style.display = 'none';
            }

            var startY = 0, startAmount = 0, active = false, dragging = false;

            dial.addEventListener('pointerdown', function (e) {
                startY = e.clientY;
                startAmount = opts.getAmount ? opts.getAmount(key) : 0;
                active = true;
                dragging = false;
                dial._suppressTap = false;
                try { dial.setPointerCapture(e.pointerId); } catch (_) { /* synthetic */ }
            });
            dial.addEventListener('pointermove', function (e) {
                if (!active) return;
                var dy = startY - e.clientY;            // drag up = increase
                if (!dragging && Math.abs(dy) > THRESHOLD) dragging = true;
                if (dragging) {
                    setVal(key, startAmount + dy / PX_PER_UNIT);
                    e.preventDefault();
                }
            });
            var end = function (e) {
                if (!active) return;
                active = false;
                try { dial.releasePointerCapture(e.pointerId); } catch (_) { /* no-op */ }
                if (dragging) {
                    dial._suppressTap = true;           // swallow synthetic click on the disc
                    setTimeout(function () { dial._suppressTap = false; }, 400);
                    if (opts.onCommit) opts.onCommit(key);  // settled after a drag
                } else if (opts.onTap) {
                    opts.onTap(key);                    // tap = add a whole drop
                }
                dragging = false;
            };
            dial.addEventListener('pointerup', end);
            dial.addEventListener('pointercancel', end);

            dial.addEventListener('keydown', function (e) {
                var cur = opts.getAmount ? opts.getAmount(key) : 0;
                var handled = true;
                var stepKb = e.shiftKey ? STEP : 1;   // Shift = fine 0.01
                if (e.key === 'ArrowUp' || e.key === 'ArrowRight') setVal(key, cur + stepKb);
                else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') setVal(key, cur - stepKb);
                else if (e.key === 'Home') setVal(key, 0);
                else if (e.key === 'End') setVal(key, max);
                else handled = false;
                if (handled) e.preventDefault();
            });
        });

        function render(key, value) {
            var fill = paletteEl.querySelector('.dial-fill[data-fill-for="' + key + '"]');
            if (fill) {
                var frac = Math.max(0, Math.min(1, value / max));
                fill.style.strokeDasharray = (frac * ARC).toFixed(2) + ' ' + C.toFixed(2);
                fill.style.display = frac > 0 ? '' : 'none';
            }
            var dial = paletteEl.querySelector('.dial[data-color="' + key + '"]');
            if (dial) dial.setAttribute('aria-valuenow', String(value));
        }

        return { render: render };
    }

    global.Dialer = {
        attach: attach,
        ringSVG: ringSVG,
        GEO: { R: R, C: C, ARC: ARC, PX_PER_UNIT: PX_PER_UNIT, THRESHOLD: THRESHOLD },
    };
})(window);
