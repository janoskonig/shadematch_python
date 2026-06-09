/**
 * Shared mixing core — turns per-pigment amounts into a mixed sRGB using either:
 *   - 'mixbox'   : Mixbox latent-space pigment mixing (the app's existing model; the
 *                  default branch — any value that isn't 'spectral' uses this)
 *   - 'spectral' : Kubelka–Munk reflectance mixing via spectral.js
 *
 * Engine-agnostic and DOM-free, so /lab, the main game and /spectral can all share it.
 * Construct once with the base spectra (window injects `spectrum_plots`), then call
 * `mix(model, amounts)` where `amounts` is { white, black, red, yellow, blue } (numbers;
 * may be fractional for dialer input). Returns { rgb:[r,g,b], reflectance:[…]|null }.
 *
 * Depends on (loaded before this file):
 *   - mixbox.js   for model === 'mixbox'
 *   - spectral.js for model === 'spectral'
 */
(function (global) {
    'use strict';

    var ORDER = ['white', 'black', 'red', 'yellow', 'blue'];

    // Idealised base swatch RGBs (same as main.js / lab.js baseColors) for Mixbox.
    var BASE_RGB = {
        white: [255, 255, 255],
        black: [0, 0, 0],
        red: [255, 0, 0],
        yellow: [255, 255, 0],
        blue: [0, 0, 255],
    };

    // spectral.js reflectance grid: 380–750 nm @10 nm (38 bins).
    var WAVELENGTHS = [];
    for (var w = 0; w < 38; w++) WAVELENGTHS.push(380 + w * 10);

    // Linear-interpolate a measured curve onto the engine grid; clamp to (0,1].
    function resampleToGrid(wavelengths, reflectances) {
        var x = wavelengths;
        var y = reflectances;
        var last = x.length - 1;
        return WAVELENGTHS.map(function (xi) {
            var v;
            if (xi <= x[0]) {
                v = y[0];
            } else if (xi >= x[last]) {
                v = y[last];
            } else {
                var j = 1;
                while (x[j] < xi) j += 1;
                var x0 = x[j - 1], x1 = x[j], y0 = y[j - 1], y1 = y[j];
                v = y0 + (y1 - y0) * (xi - x0) / (x1 - x0);
            }
            return Math.max(1e-4, Math.min(1, v));
        });
    }

    function MixingCore(opts) {
        opts = opts || {};
        this.baseRGB = opts.baseRGB || BASE_RGB;
        this.order = opts.order || ORDER;
        this.spectra = null;            // { key: spectral.Color }
        this.spectraSwatch = null;      // { key: [r,g,b] } measured base colours
        if (opts.spectrumPlots) this.setSpectra(opts.spectrumPlots);
    }

    // Build spectral.Color objects for each base from injected spectrum_plots.
    MixingCore.prototype.setSpectra = function (spectrumPlots) {
        if (!global.spectral || !global.spectral.Color || !spectrumPlots) return;
        this.spectra = {};
        this.spectraSwatch = {};
        for (var i = 0; i < this.order.length; i++) {
            var key = this.order[i];
            var d = spectrumPlots[key];
            if (!d) continue;
            var R = resampleToGrid(d.wavelengths, d.reflectances);
            var c = new global.spectral.Color(R);
            this.spectra[key] = c;
            this.spectraSwatch[key] = c.sRGB;
        }
    };

    MixingCore.prototype.hasSpectral = function () {
        return !!(this.spectra && global.spectral && global.spectral.mix);
    };
    MixingCore.prototype.hasMixbox = function () {
        return typeof global.mixbox !== 'undefined' && !!global.mixbox.rgbToLatent;
    };

    // amounts: { key: number }. Returns { rgb, reflectance }.
    MixingCore.prototype.mix = function (model, amounts) {
        var entries = [];
        var total = 0;
        for (var i = 0; i < this.order.length; i++) {
            var key = this.order[i];
            var n = amounts[key] || 0;
            if (n > 0) { entries.push([key, n]); total += n; }
        }
        if (total <= 0) return { rgb: [255, 255, 255], reflectance: null };

        if (model === 'spectral' && this.hasSpectral()) {
            var args = entries
                .filter(function (e) { return this.spectra[e[0]]; }, this)
                .map(function (e) { return [this.spectra[e[0]], e[1]]; }, this);
            var mixed = global.spectral.mix.apply(null, args);
            return { rgb: mixed.sRGB, reflectance: mixed.R };
        }
        return { rgb: this._mixbox(entries, total), reflectance: null };
    };

    MixingCore.prototype._mixbox = function (entries, total) {
        if (!this.hasMixbox()) {
            // Fallback: weighted average of base RGBs (keeps the UI alive if mixbox is missing).
            var acc = [0, 0, 0];
            for (var e = 0; e < entries.length; e++) {
                var rgbF = this.baseRGB[entries[e][0]];
                var wF = entries[e][1] / total;
                acc[0] += rgbF[0] * wF; acc[1] += rgbF[1] * wF; acc[2] += rgbF[2] * wF;
            }
            return acc.map(Math.round);
        }
        var zMix = new Array(global.mixbox.LATENT_SIZE).fill(0);
        for (var i = 0; i < entries.length; i++) {
            var rgb = this.baseRGB[entries[i][0]];
            var weight = entries[i][1] / total;
            var z = global.mixbox.rgbToLatent(rgb[0], rgb[1], rgb[2]);
            for (var k = 0; k < zMix.length; k++) zMix[k] += weight * z[k];
        }
        return global.mixbox.latentToRgb(zMix).map(Math.round);
    };

    MixingCore.WAVELENGTHS = WAVELENGTHS;
    MixingCore.ORDER = ORDER;
    global.MixingCore = MixingCore;
})(window);
