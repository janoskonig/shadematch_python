// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';

console.log("✅ main.js loaded");
let sessionLogs = [];
let currentSessionSaved = false;

window.lastMixDeltaE = NaN;
window.shadeMatchTargetRgb = [255, 255, 255];

// Cookie Consent Integration
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(() => {
        if (window.cookieConsent) {
            console.log("🍪 Cookie consent system loaded");
            if (window.cookieConsent.canUseAnalytics()) {
                console.log("📊 Analytics cookies enabled");
            }
            if (window.cookieConsent.canUsePreferences()) {
                console.log("⚙️ Preference cookies enabled");
            }
            document.addEventListener('cookieConsentUpdated', function(event) {
                console.log("🍪 Cookie consent updated:", event.detail);
            });
        }
    }, 1000);
});

window.currentUserId = localStorage.getItem('userId');

function displayUserId() {
  const userInfoDiv = document.getElementById('userInfo');
  const userIdDisplay = document.getElementById('userIdDisplay');
  if (window.currentUserId && userInfoDiv && userIdDisplay) {
    userIdDisplay.textContent = window.currentUserId;
    userInfoDiv.style.display = '';
  }
}

document.addEventListener('DOMContentLoaded', function() {
  displayUserId();
  const justRegistered = localStorage.getItem('justRegistered');
  if (justRegistered === 'true') {
    localStorage.removeItem('justRegistered');
    const userId = localStorage.getItem('userId');
    if (userId) { window.currentUserId = userId; displayUserId(); }
  }
  const checkUserIdInterval = setInterval(() => {
    const currentUserId = localStorage.getItem('userId');
    if (currentUserId && currentUserId !== window.currentUserId) {
      window.currentUserId = currentUserId;
      displayUserId();
      clearInterval(checkUserIdInterval);
    }
  }, 1000);
  setTimeout(() => clearInterval(checkUserIdInterval), 30000);
});

// ---- Badge helper ----
function updateBadge(color, count) {
  const badge = document.querySelector(`.drop-badge[data-badge-for="${color}"]`);
  if (badge) {
    badge.textContent = count;
    badge.classList.add('is-bumped');
    setTimeout(() => badge.classList.remove('is-bumped'), 150);
  }
}

function resetAllBadges() {
  document.querySelectorAll('.drop-badge').forEach(b => { b.textContent = '0'; });
}

// ---- Match quality bar ----
function updateMatchBar(deltaE) {
  const container = document.getElementById('matchBarContainer');
  const fill = document.getElementById('matchBarFill');
  const label = document.getElementById('matchBarLabel');
  if (!container || !fill || !label) return;

  container.style.display = '';
  const progress = Math.max(0, Math.min(100, 100 - deltaE * 2));
  fill.style.width = progress + '%';

  if (progress < 33) {
    fill.style.backgroundColor = 'var(--accent-danger)';
    label.textContent = 'Far';
  } else if (progress < 66) {
    fill.style.backgroundColor = 'var(--accent-warning)';
    label.textContent = 'Closer';
  } else if (progress < 98) {
    fill.style.backgroundColor = '#8BC34A';
    label.textContent = 'Very close';
  } else {
    fill.style.backgroundColor = 'var(--accent-success)';
    label.textContent = 'Match!';
  }
}

// ---- Progress indicator ----
function updateProgressIndicator(currentIndex, total) {
  const textEl = document.getElementById('progressText');
  const segEl = document.getElementById('progressSegments');
  if (!textEl || !segEl) return;

  textEl.textContent = `Color ${currentIndex + 1} of ${total}`;
  segEl.innerHTML = '';
  for (let i = 0; i < total; i++) {
    const seg = document.createElement('div');
    seg.className = 'progress-segment';
    if (i < currentIndex) seg.classList.add('is-done');
    else if (i === currentIndex) seg.classList.add('is-current');
    segEl.appendChild(seg);
  }
}

// ---- Control state management ----
function setControlState(state) {
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const skipBtn = document.getElementById("skipBtn");
  const retryBtn = document.getElementById("retryBtn");
  const restartBtn = document.getElementById("restartBtn");

  if (state === 'idle') {
    startBtn.style.display = ''; startBtn.disabled = false;
    stopBtn.style.display = 'none';
    skipBtn.style.display = 'none';
    retryBtn.style.display = 'none';
    restartBtn.disabled = true;
  } else if (state === 'mixing') {
    startBtn.style.display = 'none';
    stopBtn.style.display = ''; stopBtn.disabled = false;
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = 'Skip';
    retryBtn.style.display = ''; retryBtn.disabled = false;
    restartBtn.disabled = false;
  } else if (state === 'stopped') {
    startBtn.style.display = 'none';
    stopBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false;
    retryBtn.style.display = 'none';
    restartBtn.disabled = false;
  } else if (state === 'completed') {
    startBtn.style.display = 'none';
    stopBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = 'Next color';
    retryBtn.style.display = 'none';
    restartBtn.disabled = false;
  }
}

// ---- Enable/disable mixing ----
function disableColorMixing() {
  const mc = document.getElementById("mainContent");
  if (mc) mc.classList.add("mixing-disabled");
}
window.disableColorMixing = disableColorMixing;

function enableColorMixing() {
  const mc = document.getElementById("mainContent");
  if (mc) mc.classList.remove("mixing-disabled");
}

window.currentUserBirthdate = localStorage.getItem('userBirthdate');
window.currentUserGender = localStorage.getItem('userGender');
window.currentSessionSaved = false;

let dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
window.shadeMatchDropCounts = dropCounts;

function resetMix() {
  document.querySelectorAll('.color-circle').forEach(circle => { circle.textContent = '0'; });
  document.getElementById('currentMix').style.backgroundColor = 'rgb(255, 255, 255)';
  document.getElementById('mixedRgbValues').textContent = 'RGB: [255, 255, 255]';
  window.lastMixDeltaE = NaN;

  dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
  window.shadeMatchDropCounts = dropCounts;
  resetAllBadges();

  const matchContainer = document.getElementById('matchBarContainer');
  if (matchContainer) matchContainer.style.display = 'none';

  currentSessionSaved = false;
  window.currentSessionSaved = false;
}

window.addEventListener('storage', (e) => {
  if (e.key === 'userId') {
    window.currentUserId = e.newValue;
    window.currentUserBirthdate = localStorage.getItem('userBirthdate');
    window.currentUserGender = localStorage.getItem('userGender');
    resetMix();
    resetTimerDisplay();
    setControlState('idle');
    disableColorMixing();
  }
});

function updateBox(id, rgb) {
  const el = document.getElementById(id);
  el.style.backgroundColor = `rgb(${rgb.join(',')})`;
}

async function refreshDatabaseConnection() {
  try {
    const response = await fetch('/refresh_connection', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }
    });
    if (response.ok) {
      const result = await response.json();
      return result.status === 'success';
    }
    return false;
  } catch { return false; }
}

function saveSessionToServer(session) {
  if (!window.currentUserId) {
    console.error('❌ No user ID found');
    alert('No user ID found. Please log in again.');
    return;
  }

  let sessionData;
  if (session.target && session.drops) {
    sessionData = {
      user_id: window.currentUserId,
      target_r: session.target[0], target_g: session.target[1], target_b: session.target[2],
      drop_white: session.drops.white, drop_black: session.drops.black,
      drop_red: session.drops.red, drop_yellow: session.drops.yellow, drop_blue: session.drops.blue,
      delta_e: session.deltaE, time_sec: session.time,
      timestamp: session.timestamp, skipped: session.skipped || false
    };
  } else {
    sessionData = {
      user_id: session.user_id,
      target_r: session.target_r, target_g: session.target_g, target_b: session.target_b,
      drop_white: session.drop_white, drop_black: session.drop_black,
      drop_red: session.drop_red, drop_yellow: session.drop_yellow, drop_blue: session.drop_blue,
      delta_e: session.delta_e, time_sec: session.time_sec,
      timestamp: session.timestamp, skipped: session.skipped || false
    };
  }

  fetch('/save_session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sessionData)
  })
  .then(res => {
    if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
    return res.json();
  })
  .then(data => {
    if (data.status !== 'success') {
      console.error('Failed to save session:', data.error);
      alert('Failed to save session data. Please try again.');
    }
  })
  .catch(error => {
    console.error('Error saving session:', error);
    alert('Error saving session data. Please check your connection and try again.');
  });
}

document.addEventListener("DOMContentLoaded", () => {
  disableColorMixing();

  const baseColors = {
    white: [255, 255, 255], black: [0, 0, 0],
    red: [255, 0, 0], yellow: [255, 255, 0], blue: [0, 0, 255]
  };

  const allTargetColors = [
    { name: 'Orange', type: 'basic', classification: null, rgb: [255, 102, 30] },
    { name: 'Purple', type: 'basic', classification: null, rgb: [113, 1, 105] },
    { name: 'Green', type: 'basic', classification: null, rgb: [78, 150, 100] },
    { name: 'Pink', type: 'basic', classification: null, rgb: [255, 179, 188] },
    { name: 'Olive', type: 'basic', classification: null, rgb: [113, 112, 62] },
    { name: 'Custom', type: 'basic', classification: null, rgb: [111, 122, 102] },
    { name: 'Peach', type: 'basic', classification: null, rgb: [255, 228, 175] },
    { name: 'Coral', type: 'basic', classification: null, rgb: [255, 131, 82] },
    { name: 'Turquoise', type: 'basic', classification: null, rgb: [103, 157, 174] },
    { name: 'Chartreuse', type: 'basic', classification: null, rgb: [157, 210, 103] },
    { name: 'Teal', type: 'basic', classification: null, rgb: [84, 122, 122] },
    { name: '#D1AE90', type: 'skin', classification: 'skin_light', rgb: [208, 176, 148] },
    { name: '#AE967E', type: 'skin', classification: 'skin_light', rgb: [175, 149, 126] },
    { name: '#C3A28F', type: 'skin', classification: 'skin_light', rgb: [242, 166, 129] },
    { name: '#BE8870', type: 'skin', classification: 'skin_light', rgb: [193, 135, 115] },
    { name: '#6D544D', type: 'skin', classification: 'skin_light', rgb: [178, 125, 107] },
    { name: '#34261B', type: 'skin', classification: 'skin_light', rgb: [205, 87, 91] },
    { name: '#C8AF91', type: 'skin', classification: 'skin_light', rgb: [208, 176, 148] },
    { name: '#A97367', type: 'skin', classification: 'skin_light', rgb: [172, 115, 104] },
    { name: '#CB9781', type: 'skin', classification: 'skin_light', rgb: [212, 147, 125] },
    { name: '#B68678', type: 'skin', classification: 'skin_light', rgb: [193, 135, 115] },
    { name: '#E8B7BA', type: 'skin', classification: 'skin_light', rgb: [228, 183, 190] },
    { name: '#A58F5E', type: 'skin', classification: 'skin_light', rgb: [167, 145, 92] },
    { name: '#B5866A', type: 'skin', classification: 'skin_light', rgb: [180, 134, 106] },
    { name: '#DE958F', type: 'skin', classification: 'skin_light', rgb: [225, 155, 151] },
    { name: '#99856A', type: 'skin', classification: 'skin_dark', rgb: [155, 131, 108] },
    { name: '#A8856F', type: 'skin', classification: 'skin_dark', rgb: [182, 137, 96] },
    { name: '#A07E63', type: 'skin', classification: 'skin_dark', rgb: [169, 120, 74] },
    { name: '#80685C', type: 'skin', classification: 'skin_dark', rgb: [143, 103, 88] },
    { name: '#584B42', type: 'skin', classification: 'skin_dark', rgb: [88, 71, 52] },
    { name: '#7B5749', type: 'skin', classification: 'skin_dark', rgb: [127, 84, 67] },
    { name: '#543B34', type: 'skin', classification: 'skin_dark', rgb: [174, 121, 123] },
    { name: '#583E2D', type: 'skin', classification: 'skin_dark', rgb: [80, 62, 41] },
    { name: '#A76662', type: 'skin', classification: 'skin_dark', rgb: [161, 104, 98] },
    { name: '#A28074', type: 'skin', classification: 'skin_dark', rgb: [165, 130, 118] },
    { name: '#8F7868', type: 'skin', classification: 'skin_dark', rgb: [144, 121, 101] },
    { name: '#9F7954', type: 'skin', classification: 'skin_dark', rgb: [189, 131, 76] },
    { name: '#392D1D', type: 'skin', classification: 'skin_dark', rgb: [57, 42, 22] },
    { name: '#9D7248', type: 'skin', classification: 'skin_dark', rgb: [150, 114, 71] },
    { name: '#58482F', type: 'skin', classification: 'skin_dark', rgb: [88, 68, 44] }
  ];

  const colorFrequencyData = {
    '#FFB3BC': 145, '#FFE4AF': 108, '#6F7A66': 102, '#71703E': 101,
    '#547A7A': 96, '#FF8352': 90, '#679DAE': 68, '#9DD267': 66,
    '#D1AE90': 48, '#BE8870': 39, '#AE967E': 22, '#C3A28F': 17,
    '#A97367': 16, '#CB9781': 11, '#E8B7BA': 10, '#A58F5E': 18,
    '#B5866A': 20, '#DE958F': 1,
    '#99856A': 5, '#A8856F': 23, '#A07E63': 4, '#80685C': 3,
    '#584B42': 13, '#7B5749': 14, '#543B34': 9, '#583E2D': 2,
    '#A76662': 21, '#A28074': 7, '#8F7868': 8, '#9F7954': 1,
    '#392D1D': 19, '#9D7248': 12, '#58482F': 24
  };

  function rgbToHex(rgb) {
    return '#' + rgb.map(x => { const h = x.toString(16); return h.length === 1 ? '0' + h : h; }).join('').toUpperCase();
  }

  function weightedRandomSelection(items, weights, count) {
    const totalWeight = weights.reduce((s, w) => s + w, 0);
    const cumulativeWeights = [];
    let cum = 0;
    for (let i = 0; i < weights.length; i++) { cum += weights[i]; cumulativeWeights.push(cum); }
    const selected = []; const selectedIndices = new Set();
    while (selected.length < count && selected.length < items.length) {
      const r = Math.random() * totalWeight;
      for (let i = 0; i < cumulativeWeights.length; i++) {
        if (r <= cumulativeWeights[i] && !selectedIndices.has(i)) {
          selected.push(items[i]); selectedIndices.add(i); break;
        }
      }
    }
    return selected;
  }

  function generateRandomizedColors() {
    const firstThreeBasic = allTargetColors.slice(0, 3);
    const remainingBasic = allTargetColors.slice(3, 11);
    const basicWeights = remainingBasic.map(c => 1 / (colorFrequencyData[rgbToHex(c.rgb)] || 1));
    const selectedRemainingBasic = weightedRandomSelection(remainingBasic, basicWeights, 3);
    const skinColors = allTargetColors.slice(11);
    const skinWeights = skinColors.map(c => 1 / (colorFrequencyData[rgbToHex(c.rgb)] || 1));
    const selectedSkinColors = weightedRandomSelection(skinColors, skinWeights, 5);
    return [...firstThreeBasic, ...selectedRemainingBasic, ...selectedSkinColors];
  }

  const targetColors = generateRandomizedColors();
  let currentTargetIndex = 0;
  let currentTargetColor = targetColors[0];
  let targetColor = currentTargetColor.rgb;

  function setGameTargetRgb(rgb) { targetColor = rgb; window.shadeMatchTargetRgb = rgb; }
  setGameTargetRgb(currentTargetColor.rgb);
  updateProgressIndicator(currentTargetIndex, targetColors.length);

  function showSkipPerceptionModal() {
    return new Promise((resolve) => {
      const modal = document.getElementById('skipPerceptionModal');
      if (!modal) { resolve(null); return; }
      modal.style.display = 'flex';
      const options = [
        { id: 'skipPerceptionIdentical', value: 'identical' },
        { id: 'skipPerceptionAcceptable', value: 'acceptable' },
        { id: 'skipPerceptionUnacceptable', value: 'unacceptable' }
      ];
      const handlers = [];
      const finish = (value) => {
        modal.style.display = 'none';
        for (const { el, fn } of handlers) el.removeEventListener('click', fn);
        resolve(value);
      };
      for (const { id, value } of options) {
        const el = document.getElementById(id);
        if (!el) continue;
        const fn = () => finish(value);
        el.addEventListener('click', fn);
        handlers.push({ el, fn });
      }
    });
  }

  function updateCurrentMix() {
    const totalDrops = Object.values(dropCounts).reduce((a, b) => a + b, 0);
    if (totalDrops === 0) {
      updateBox("currentMix", [255, 255, 255]);
      window.lastMixDeltaE = NaN;
      return;
    }

    let zMix = new Array(mixbox.LATENT_SIZE).fill(0);
    for (let color in dropCounts) {
      const count = dropCounts[color];
      if (count > 0) {
        const [r, g, b] = baseColors[color];
        const z = mixbox.rgbToLatent(r, g, b);
        for (let i = 0; i < zMix.length; i++) zMix[i] += (count / totalDrops) * z[i];
      }
    }

    const mixedRGB = mixbox.latentToRgb(zMix).map(Math.round);
    updateBox("currentMix", mixedRGB);
    document.getElementById("mixedRgbValues").textContent = `RGB: [${mixedRGB.join(', ')}]`;

    fetch("/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: targetColor, mixed: mixedRGB })
    })
    .then(res => res.json())
    .then(data => {
      if (data.error) return console.error("Server error:", data.error);
      window.lastMixDeltaE = data.delta_e;
      updateMatchBar(data.delta_e);

      if (data.delta_e <= 0.01) {
        stopTimer();
        const session = {
          user_id: window.currentUserId,
          target: targetColor,
          drops: { ...dropCounts },
          deltaE: data.delta_e,
          time: parseFloat(document.getElementById("timer").textContent),
          timestamp: new Date().toISOString(),
          skipped: false
        };
        sessionLogs.push(session);
        saveSessionToServer(session);
        currentSessionSaved = true;
        window.currentSessionSaved = true;
        setControlState('completed');
      }
    });
  }

  // ---- Button handlers ----
  document.getElementById("startBtn").addEventListener("click", async () => {
    refreshDatabaseConnection();
    const newTargetColors = generateRandomizedColors();
    targetColors.length = 0;
    targetColors.push(...newTargetColors);

    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTargetRgb(currentTargetColor.rgb);
    updateBox("targetColor", targetColor);
    resetMix();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateProgressIndicator(currentTargetIndex, targetColors.length);
  });

  document.getElementById("stopBtn").addEventListener("click", () => {
    stopTimer();
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    if (!isNaN(currentDeltaE)) {
      const sessionData = {
        user_id: window.currentUserId,
        target_r: targetColor[0], target_g: targetColor[1], target_b: targetColor[2],
        drop_white: dropCounts.white, drop_black: dropCounts.black,
        drop_red: dropCounts.red, drop_yellow: dropCounts.yellow, drop_blue: dropCounts.blue,
        delta_e: currentDeltaE,
        time_sec: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(), skipped: true
      };
      sessionLogs.push(sessionData);
      saveSessionToServer(sessionData);
    }
    disableColorMixing();
    setControlState('stopped');
  });

  document.getElementById("skipBtn").addEventListener("click", async () => {
    refreshDatabaseConnection();
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    const mc = document.getElementById("mainContent");
    const isAfterStop = mc && mc.classList.contains("mixing-disabled");
    const alreadyCompletedThisColor = window.currentSessionSaved === true;

    const shouldSaveSkip = currentDeltaE > 0.01 && !isAfterStop && !alreadyCompletedThisColor;

    if (shouldSaveSkip) {
      const skipPerception = await showSkipPerceptionModal();
      if (!skipPerception) return;
      const skipData = {
        user_id: window.currentUserId,
        target_r: targetColor[0], target_g: targetColor[1], target_b: targetColor[2],
        drop_white: dropCounts.white || 0, drop_black: dropCounts.black || 0,
        drop_red: dropCounts.red || 0, drop_yellow: dropCounts.yellow || 0, drop_blue: dropCounts.blue || 0,
        time_sec: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        delta_e: currentDeltaE,
        skip_perception: skipPerception
      };
      try {
        const res = await fetch('/save_skip', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(skipData)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
          alert('Failed to save skip data. Please try again.');
          return;
        }
      } catch {
        alert('Error saving skip data. Please check your connection and try again.');
        return;
      }
    }

    currentTargetIndex++;
    if (currentTargetIndex < targetColors.length) {
      currentTargetColor = targetColors[currentTargetIndex];
      setGameTargetRgb(currentTargetColor.rgb);
      updateBox("targetColor", targetColor);
      resetMix();
      stopTimer();
      resetTimerDisplay();
      startTimer();
      enableColorMixing();
      setControlState('mixing');
      updateProgressIndicator(currentTargetIndex, targetColors.length);
    } else {
      // All colors completed
      const congratulations = `
        <div id="congratulations-modal" style="
          position:fixed;inset:0;
          background:rgba(0,0,0,0.7);
          display:flex;justify-content:center;align-items:center;
          z-index:10000;font-family:var(--font-family);
        ">
          <div style="
            background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
            color:white;padding:40px;border-radius:20px;
            text-align:center;max-width:500px;margin:20px;
            box-shadow:0 20px 40px rgba(0,0,0,0.3);
          ">
            <div style="font-size:4em;margin-bottom:20px;">🎉</div>
            <h2 style="margin:0 0 20px 0;font-size:2rem;font-weight:300;">Congratulations!</h2>
            <p style="margin:0 0 30px 0;font-size:1.1em;line-height:1.6;">
              You have completed all color matching challenges!
            </p>
            <div style="
              display:inline-block;background:rgba(255,255,255,0.2);
              padding:15px 30px;border-radius:25px;font-size:1.1em;
            ">Redirecting to results…</div>
          </div>
        </div>`;
      document.body.insertAdjacentHTML('beforeend', congratulations);
      createConfetti();
      setControlState('idle');
      document.getElementById("startBtn").disabled = true;
      setTimeout(() => { window.location.href = '/results'; }, 4000);
    }
  });

  document.getElementById("restartBtn").addEventListener("click", async () => {
    refreshDatabaseConnection();
    const newTargetColors = generateRandomizedColors();
    targetColors.length = 0;
    targetColors.push(...newTargetColors);

    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTargetRgb(currentTargetColor.rgb);
    updateBox("targetColor", targetColor);
    resetMix();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateProgressIndicator(currentTargetIndex, targetColors.length);
    document.getElementById('overflowDropdown').classList.remove('is-open');
  });

  document.getElementById("retryBtn").addEventListener("click", () => {
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    if (!isNaN(currentDeltaE)) {
      const session = {
        user_id: window.currentUserId,
        target: targetColor,
        drops: { ...dropCounts },
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById("timer").textContent),
        timestamp: new Date().toISOString(),
        skipped: true
      };
      sessionLogs.push(session);
      saveSessionToServer(session);
    }
    resetMix();
    resetTimerDisplay();
    stopTimer();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
  });

  // ---- Palette interaction ----
  document.querySelectorAll(".color-circle").forEach(circle => {
    circle.addEventListener("click", (e) => {
      e.preventDefault();
      const color = circle.dataset.color;
      dropCounts[color]++;
      circle.textContent = dropCounts[color];
      updateBadge(color, dropCounts[color]);

      // Tap feedback
      circle.classList.add('is-tapped');
      setTimeout(() => circle.classList.remove('is-tapped'), 200);
      if (navigator.vibrate) navigator.vibrate(15);

      updateCurrentMix();
    });
  });

  document.querySelectorAll(".minus-button").forEach(button => {
    button.addEventListener("click", (e) => {
      e.preventDefault();
      const color = button.dataset.color;
      if (dropCounts[color] > 0) {
        dropCounts[color]--;
        document.querySelector(`.color-circle[data-color='${color}']`).textContent = dropCounts[color];
        updateBadge(color, dropCounts[color]);
        updateCurrentMix();
      }
    });
  });
});

// ---- Login form handler ----
document.addEventListener('DOMContentLoaded', function() {
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', async function(e) {
      e.preventDefault();
      const userId = document.getElementById('loginId').value.toUpperCase();
      try {
        const response = await fetch('/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userId })
        });
        const data = await response.json();
        if (data.status === 'success') {
          localStorage.setItem('userId', userId);
          localStorage.setItem('userBirthdate', data.birthdate);
          localStorage.setItem('userGender', data.gender);
          window.currentUserId = userId;
          document.getElementById('userModal').style.display = 'none';
          resetMix();
          resetTimerDisplay();
          disableColorMixing();
          displayUserId();
        } else {
          alert('Invalid user ID. Please try again.');
        }
      } catch {
        alert('Invalid user ID. Please try again.');
      }
    });
  }
});

// ---- Continue button handler ----
document.addEventListener('DOMContentLoaded', function() {
  const continueBtn = document.getElementById('continueBtn');
  if (continueBtn) {
    continueBtn.addEventListener('click', function() {
      document.getElementById('userModal').style.display = 'none';
      resetMix();
      resetTimerDisplay();
      disableColorMixing();
    });
  }
  const showLoginBtn = document.getElementById('showLoginBtn');
  if (showLoginBtn) {
    showLoginBtn.addEventListener('click', function() {
      document.getElementById('registerSection').style.display = 'none';
      document.getElementById('loginSection').style.display = 'block';
    });
  }
  const showRegisterBtn = document.getElementById('showRegisterBtn');
  if (showRegisterBtn) {
    showRegisterBtn.addEventListener('click', function() {
      document.getElementById('loginSection').style.display = 'none';
      document.getElementById('registerSection').style.display = 'block';
    });
  }
});

// ---- Confetti ----
function createConfetti() {
  const colors = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff9ff3', '#54a0ff', '#5f27cd'];
  for (let i = 0; i < 150; i++) {
    setTimeout(() => createConfettiPiece(colors), i * 20);
  }
}

function createConfettiPiece(colors) {
  const confetti = document.createElement('div');
  const color = colors[Math.floor(Math.random() * colors.length)];
  const size = Math.random() * 8 + 4;
  const startX = Math.random() * window.innerWidth;
  const startY = -10;
  const endY = window.innerHeight + 10;
  const rotation = Math.random() * 360;
  const rotationSpeed = (Math.random() - 0.5) * 20;
  const horizontalDrift = (Math.random() - 0.5) * 100;
  confetti.style.cssText = `
    position:fixed;left:${startX}px;top:${startY}px;
    width:${size}px;height:${size}px;background:${color};
    border-radius:${Math.random() > 0.5 ? '50%' : '0'};
    pointer-events:none;z-index:10001;box-shadow:0 0 6px ${color};
  `;
  document.body.appendChild(confetti);
  let startTime = null;
  const duration = 3000 + Math.random() * 2000;
  function animate(currentTime) {
    if (!startTime) startTime = currentTime;
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const easeOut = 1 - Math.pow(1 - progress, 3);
    const currentY = startY + (endY - startY) * easeOut;
    const currentX = startX + horizontalDrift * Math.sin(progress * Math.PI);
    const currentRotation = rotation + rotationSpeed * elapsed / 1000;
    confetti.style.transform = `translate(${currentX - startX}px, ${currentY - startY}px) rotate(${currentRotation}deg)`;
    confetti.style.opacity = 1 - progress;
    if (progress < 1) requestAnimationFrame(animate);
    else confetti.remove();
  }
  requestAnimationFrame(animate);
}
