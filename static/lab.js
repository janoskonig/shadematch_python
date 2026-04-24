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
