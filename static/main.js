// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';
import { captureEnv } from './env_capture.js?v=20260508-qc2';

console.log('✅ main.js loaded');
let sessionLogs = [];
let currentSessionSaved = false;
// Incremented on every color change (resetMix); used to discard stale /calculate responses.
let _calcColorGen = 0;

window.lastMixDeltaE = NaN;
window.shadeMatchTargetRgb = [255, 255, 255];

const MATCH_PERFECT_DELTA_E = 0.01;

function isPerfectMatch(deltaE) {
  return Number.isFinite(deltaE) && deltaE <= MATCH_PERFECT_DELTA_E;
}

// ── UUID ──────────────────────────────────────────────────────────────────
function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

// ── Client session ID (one per browser session, for analytics funnel) ─────
const CLIENT_SESSION_ID = (() => {
  let id = sessionStorage.getItem('csid');
  if (!id) { id = generateUUID(); sessionStorage.setItem('csid', id); }
  return id;
})();

const MIXING_TELEMETRY_ENDPOINT = '/api/mixing-attempt/ingest';
const MIXING_START_UPDATE_ENDPOINT = '/api/mixing-attempt/start-or-update';
const APP_VERSION = document.documentElement?.dataset?.appVersion || null;

let telemetryAttempt = null;
let telemetryEventBuffer = [];
let currentMixedRgb = [255, 255, 255];
let mixStateStepId = 0;

function getAuthenticatedUserId() {
  const id = window.currentUserId || localStorage.getItem('userId') || '';
  return typeof id === 'string' ? id.trim().toUpperCase() : '';
}

function promptLoginRequired(reason) {
  const msg = reason
    || 'You must be logged in with a valid user ID to play. Please log in or register to continue.';
  try {
    if (typeof showToast === 'function') {
      showToast(msg, 'info', 5000);
    }
  } catch { /* showToast may not be defined yet at module load */ }
  const modal = document.getElementById('userModal');
  if (modal) {
    modal.style.display = 'flex';
    if (typeof showSection === 'function') {
      showSection('login');
    } else {
      const loginSection = document.getElementById('loginSection');
      const registerSection = document.getElementById('registerSection');
      if (loginSection) loginSection.style.display = '';
      if (registerSection) registerSection.style.display = 'none';
    }
  } else {
    alert(msg);
  }
}

function requireAuthenticatedUser({ silent = false } = {}) {
  const uid = getAuthenticatedUserId();
  if (uid) return uid;
  if (!silent) promptLoginRequired();
  return null;
}

function nowClientTsMs() {
  return Date.now();
}

function getTimerSec() {
  const timerEl = document.getElementById('timer');
  const parsed = timerEl ? parseFloat(timerEl.textContent) : NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function cloneDrops() {
  return {
    white: dropCounts.white | 0,
    black: dropCounts.black | 0,
    red: dropCounts.red | 0,
    yellow: dropCounts.yellow | 0,
    blue: dropCounts.blue | 0,
  };
}

function normalizeDelta(value) {
  return Number.isFinite(value) ? value : null;
}

function buildMixSnapshot({ mixedRgbOverride, deltaEOverride, timerSecOverride } = {}) {
  return {
    drops: cloneDrops(),
    mixed_rgb: Array.isArray(mixedRgbOverride) ? [...mixedRgbOverride] : [...currentMixedRgb],
    delta_e: normalizeDelta(deltaEOverride !== undefined ? deltaEOverride : window.lastMixDeltaE),
    timer_sec: Number.isFinite(timerSecOverride) ? timerSecOverride : getTimerSec(),
  };
}

function actionTypeFromEventType(eventType, metadata = null) {
  if (eventType === 'action_add') return 'add';
  if (eventType === 'action_remove') return 'remove';
  if (eventType === 'boundary_reset') return 'reset';
  if (eventType === 'boundary_skip') return 'skip';
  if (eventType === 'boundary_save') {
    const reason = metadata?.terminal_end_reason || null;
    if (reason === 'saved_match') return 'success';
    if (reason === 'saved_stop') return 'stop';
  }
  return null;
}

function isDecisionAction(actionType) {
  return ['add', 'remove', 'reset', 'stop', 'skip', 'success'].includes(actionType);
}

function rgbFields(snapshot, prefix) {
  const rgb = Array.isArray(snapshot?.mixed_rgb) && snapshot.mixed_rgb.length === 3
    ? snapshot.mixed_rgb
    : [null, null, null];
  return {
    [`${prefix}_r`]: Number.isFinite(rgb[0]) ? rgb[0] : null,
    [`${prefix}_g`]: Number.isFinite(rgb[1]) ? rgb[1] : null,
    [`${prefix}_b`]: Number.isFinite(rgb[2]) ? rgb[2] : null,
  };
}

function serializeAttemptHeader(attempt) {
  return {
    attempt_uuid: attempt.attempt_uuid,
    user_id: getAuthenticatedUserId() || null,
    target_color_id: attempt.target_color_id ?? null,
    target_r: attempt.target_rgb[0],
    target_g: attempt.target_rgb[1],
    target_b: attempt.target_rgb[2],
    initial_drop_white: attempt.initial_snapshot.drops.white,
    initial_drop_black: attempt.initial_snapshot.drops.black,
    initial_drop_red: attempt.initial_snapshot.drops.red,
    initial_drop_yellow: attempt.initial_snapshot.drops.yellow,
    initial_drop_blue: attempt.initial_snapshot.drops.blue,
    initial_mixed_r: attempt.initial_snapshot.mixed_rgb[0],
    initial_mixed_g: attempt.initial_snapshot.mixed_rgb[1],
    initial_mixed_b: attempt.initial_snapshot.mixed_rgb[2],
    initial_delta_e: attempt.initial_snapshot.delta_e,
    attempt_started_client_ts_ms: attempt.attempt_started_client_ts_ms,
    first_action_client_ts_ms: attempt.first_action_client_ts_ms,
    attempt_ended_client_ts_ms: attempt.attempt_ended_client_ts_ms,
    end_reason: attempt.end_reason,
    final_delta_e: normalizeDelta(window.lastMixDeltaE),
    num_steps: attempt.decisionStepIndex,
    app_version: APP_VERSION,
    client_env_json: attempt.client_env_json || null,
  };
}

async function postAttemptHeaderUpdate() {
  if (!telemetryAttempt) return;
  const payload = serializeAttemptHeader(telemetryAttempt);
  if (!payload.user_id) {
    // Never write attempts for unauthenticated users.
    return;
  }
  try {
    await fetch(MIXING_START_UPDATE_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    // Keep gameplay resilient; ingest endpoint is idempotent and can recover later.
  }
}

function enqueueTelemetryEvent({ event_type, action_color = null, state_before_json, state_after_json, client_ts_ms, metadata_json = null }) {
  if (!telemetryAttempt) return null;
  const action_type = actionTypeFromEventType(event_type, metadata_json);
  const decisionEvent = isDecisionAction(action_type);
  let step_index = null;
  let time_since_prev_step_ms = null;
  if (decisionEvent) {
    telemetryAttempt.decisionStepIndex += 1;
    step_index = telemetryAttempt.decisionStepIndex;
    if (telemetryAttempt.lastDecisionClientTsMs != null) {
      time_since_prev_step_ms = Math.max(0, client_ts_ms - telemetryAttempt.lastDecisionClientTsMs);
    }
    telemetryAttempt.lastDecisionClientTsMs = client_ts_ms;
  }

  telemetryAttempt.seq += 1;
  const event = {
    attempt_uuid: telemetryAttempt.attempt_uuid,
    seq: telemetryAttempt.seq,
    event_type,
    action_color,
    client_ts_ms,
    state_before_json,
    state_after_json,
    metadata_json,
    step_index,
    time_since_prev_step_ms,
    action_type,
    amount: (action_type === 'add' || action_type === 'remove') ? 1 : null,
    delta_e_before: normalizeDelta(state_before_json?.delta_e),
    delta_e_after: normalizeDelta(state_after_json?.delta_e),
    ...rgbFields(state_before_json, 'mix_before'),
    ...rgbFields(state_after_json, 'mix_after'),
  };
  telemetryEventBuffer.push(event);
  return event;
}

function updateBufferedEventDelta(stepId, deltaE) {
  for (let i = telemetryEventBuffer.length - 1; i >= 0; i -= 1) {
    const ev = telemetryEventBuffer[i];
    if (ev?.metadata_json?.step_id === stepId && ev.state_after_json?.delta_e == null) {
      ev.state_after_json.delta_e = deltaE;
      ev.delta_e_after = normalizeDelta(deltaE);
      break;
    }
  }
}

function beginAttemptForCurrentTarget() {
  if (!Array.isArray(targetColor) || targetColor.length !== 3) return;
  if (!getAuthenticatedUserId()) {
    // Never start an attempt (and thus never write a mixing_attempts row) for
    // an unauthenticated visitor.
    telemetryAttempt = null;
    telemetryEventBuffer = [];
    return;
  }
  const nowMs = nowClientTsMs();
  const initialSnapshot = buildMixSnapshot({
    mixedRgbOverride: currentMixedRgb,
    deltaEOverride: window.lastMixDeltaE,
    timerSecOverride: getTimerSec(),
  });

  let envSnapshot = null;
  try { envSnapshot = captureEnv(); } catch { envSnapshot = null; }

  telemetryAttempt = {
    attempt_uuid: generateUUID(),
    seq: 0,
    target_color_id: currentTargetColor?.id ?? null,
    target_rgb: [...targetColor],
    attempt_started_client_ts_ms: nowMs,
    first_action_client_ts_ms: null,
    attempt_ended_client_ts_ms: null,
    end_reason: null,
    initial_snapshot: initialSnapshot,
    lastDecisionClientTsMs: null,
    decisionStepIndex: 0,
    client_env_json: envSnapshot,
  };
  telemetryEventBuffer = [];

  enqueueTelemetryEvent({
    event_type: 'boundary_start',
    client_ts_ms: nowMs,
    state_before_json: initialSnapshot,
    state_after_json: initialSnapshot,
    metadata_json: { source: 'client' },
  });
  enqueueTelemetryEvent({
    event_type: 'boundary_target_shown',
    client_ts_ms: nowMs + 1,
    state_before_json: initialSnapshot,
    state_after_json: initialSnapshot,
    metadata_json: { target_color_id: currentTargetColor?.id ?? null },
  });

  postAttemptHeaderUpdate();
}

async function flushTelemetry({ finalize = false, endReason = null, terminalBoundaryType = null, useBeacon = false } = {}) {
  if (!telemetryAttempt) return;
  if (!getAuthenticatedUserId()) {
    // Drop any buffered telemetry for guests; we never persist it server-side.
    telemetryAttempt = null;
    telemetryEventBuffer = [];
    return;
  }

  if (finalize && !telemetryAttempt.end_reason) {
    telemetryAttempt.end_reason = endReason || 'abandoned';
    telemetryAttempt.attempt_ended_client_ts_ms = nowClientTsMs();
    if (terminalBoundaryType) {
      const snap = buildMixSnapshot();
      enqueueTelemetryEvent({
        event_type: terminalBoundaryType,
        client_ts_ms: telemetryAttempt.attempt_ended_client_ts_ms,
        state_before_json: snap,
        state_after_json: snap,
        metadata_json: { terminal_end_reason: telemetryAttempt.end_reason },
      });
    }
  }

  const payload = {
    attempt: serializeAttemptHeader(telemetryAttempt),
    events: telemetryEventBuffer,
  };
  const hasPayload = payload.events.length > 0 || payload.attempt.end_reason !== null;
  if (!hasPayload) return;

  if (useBeacon && navigator.sendBeacon) {
    const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
    const accepted = navigator.sendBeacon(MIXING_TELEMETRY_ENDPOINT, blob);
    if (accepted) telemetryEventBuffer = [];
    return;
  }

  try {
    const res = await fetch(MIXING_TELEMETRY_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      telemetryEventBuffer = [];
      if (telemetryAttempt.end_reason) {
        telemetryAttempt = null;
      }
    } else {
      let errText = '';
      try {
        errText = await res.text();
      } catch {
        errText = '';
      }
      console.warn('Telemetry ingest failed', res.status, errText);
    }
  } catch (err) {
    console.warn('Telemetry ingest request error', err);
    // Keep buffer for next flush retry.
  }
}

// ── Analytics ─────────────────────────────────────────────────────────────
const ALLOWED_EVENTS = new Set([
  'app_opened', 'app_ready', 'first_palette_interaction', 'save_attempt',
  'instruction_acknowledged', 'fullscreen_change', 'visibility_change',
]);

function trackEvent(event, metadata = {}) {
  if (!ALLOWED_EVENTS.has(event)) return;
  let device = null;
  try { device = captureEnv(); } catch { device = null; }
  const payload = {
    event,
    ts: new Date().toISOString(),
    user_id: window.currentUserId || localStorage.getItem('userId') || null,
    metadata: { client_session_id: CLIENT_SESSION_ID, device, ...metadata },
  };
  fetch('/api/analytics/event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

window.shadeMatchTrackEvent = trackEvent;
window.shadeMatchCaptureEnv = captureEnv;

// Fire app_opened immediately on module load
trackEvent('app_opened');

// Passive listeners for environmental state changes that affect color rendering.
if (typeof document !== 'undefined') {
  document.addEventListener('fullscreenchange', () => {
    trackEvent('fullscreen_change');
  });
  document.addEventListener('webkitfullscreenchange', () => {
    trackEvent('fullscreen_change');
  });
  document.addEventListener('visibilitychange', () => {
    trackEvent('visibility_change', { visibility_state: document.visibilityState });
  });
}

// ── Cookie Consent ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  setTimeout(() => {
    if (window.cookieConsent) {
      document.addEventListener('cookieConsentUpdated', function (event) {
        console.log('🍪 Cookie consent updated:', event.detail);
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

document.addEventListener('DOMContentLoaded', function () {
  displayUserId();
  loadAndRenderProgress();

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
      loadAndRenderProgress();
      clearInterval(checkUserIdInterval);
    }
  }, 1000);
  setTimeout(() => clearInterval(checkUserIdInterval), 30000);
});

// ── Toast system ──────────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toastContainer') || createToastContainer();
  const toast = document.createElement('div');
  toast.className = `shade-toast shade-toast--${type}`;
  toast.innerHTML = `<span class="shade-toast-msg">${message}</span>`;
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('shade-toast--visible'));
  setTimeout(() => {
    toast.classList.remove('shade-toast--visible');
    setTimeout(() => toast.remove(), 400);
  }, duration);
}

function createToastContainer() {
  const el = document.createElement('div');
  el.id = 'toastContainer';
  el.className = 'shade-toast-container';
  document.body.appendChild(el);
  return el;
}

// ── Progress strip ────────────────────────────────────────────────────────
async function loadAndRenderProgress() {
  const uid = window.currentUserId || localStorage.getItem('userId');
  if (!uid) return;
  try {
    const res = await fetch('/api/user-progress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid }),
    });
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === 'success') {
      renderProgressStrip(data.progress);
      if (data.next_action) renderNextAction(data.next_action);
      if (data.daily_missions) renderDailyMissions(data.daily_missions);
    }
  } catch { /* silent */ }
}

function renderProgressStrip(p) {
  const strip = document.getElementById('progressStrip');
  if (!strip) return;

  // Primary bar: quota coverage (completed_attempt_units / required_attempt_units)
  const quotaPct = p.level_progress_pct != null ? p.level_progress_pct : 0;

  const freezeHtml = p.streak_freeze_available > 0
    ? `<span class="ps-freeze" title="Streak freezes available">🧊 ${p.streak_freeze_available}</span>`
    : '';

  const completedColors = p.completed_colors != null ? p.completed_colors : 0;
  const totalColors = p.total_tracked_colors != null ? p.total_tracked_colors : '?';
  const coveragePct = p.catalog_coverage_pct != null ? p.catalog_coverage_pct.toFixed(1) : '0.0';

  const levelTitle = p.is_maxed_out
    ? `${p.level_name} — All Colors Mastered!`
    : `${p.level_name} — ${completedColors}/${totalColors} colors complete (${coveragePct}%)`;

  strip.innerHTML = `
    <div class="ps-rank" style="color:${p.rank_color}" title="${p.rank}">${p.rank}</div>
    <div class="ps-level">${p.level_name}</div>
    <div class="ps-xpbar-wrap" title="${levelTitle}">
      <div class="ps-xpbar-fill" style="width:${quotaPct}%"></div>
    </div>
    <div class="ps-colors" title="${completedColors} of ${totalColors} colors at quota">${completedColors}/${totalColors}</div>
    <div class="ps-streak" title="Current streak">🔥 ${p.current_streak}</div>
    ${freezeHtml}
  `;
  strip.style.display = 'flex';
}

// ── Post-save reinforcement sequencer ────────────────────────────────────
// Monotonic counter: only the most-recent save response may drive UI updates.
// Older responses cancel all pending timers and become no-ops.
let _seqId = 0;

// Gap between phases (ms)
const SEQ_GAP = 420;

function _delay(ms) { return new Promise(r => setTimeout(r, ms)); }

async function handleProgressionResponse(data) {
  if (!data || data.status !== 'success' || data.duplicate) return;

  // Claim a sequence slot; abort if a newer response arrives mid-sequence.
  const mySeq = ++_seqId;
  const stale = () => mySeq !== _seqId;

  // Phase 1 — Progress strip (render immediately, no toast)
  if (data.progress) renderProgressStrip(data.progress);

  await _delay(SEQ_GAP); if (stale()) return;

  // Phase 2 — Quota-major awards (highest salience — fire before anything else)
  if (Array.isArray(data.new_awards)) {
    const majorAwards = data.new_awards.filter(a => a.award_class === 'quota_major');
    for (const award of majorAwards) {
      const icon = award.icon || '✅';
      showToast(`${icon} ${award.name}`, 'levelup', 6000);
      await _delay(SEQ_GAP * 2); if (stale()) return;
    }
  }

  // Phase 3 — Level-up (quota-based)
  if (data.level_up) {
    const p = data.progress;
    const colorsCtx = p ? ` (${p.completed_colors}/${p.total_tracked_colors} colors)` : '';
    showToast(`⬆️ Level ${data.level_up.to} reached${colorsCtx}`, 'levelup', 5500);
    await _delay(SEQ_GAP * 2); if (stale()) return;
  }

  // Phase 4 — Streak event
  if (data.streak_event === 'started') {
    showToast('🔥 Streak started — play again tomorrow!', 'streak', 3500);
  } else if (data.streak_event === 'incremented' && data.progress) {
    const s = data.progress.current_streak;
    showToast(`🔥 ${s}-day streak!`, 'streak', 3000);
  } else if (data.streak_event === 'freeze_consumed') {
    const freeze = data.progress ? data.progress.streak_freeze_available : '?';
    showToast(`🧊 Streak protected — 1 freeze used (${freeze} left)`, 'freeze', 5000);
  } else if (data.streak_event === 'reset') {
    showToast('Streak reset. Keep going!', 'info', 3000);
  }

  await _delay(SEQ_GAP); if (stale()) return;

  // Phase 5 — XP (secondary reinforcement — shown after quota signals)
  if (data.xp_earned && data.xp_earned > 0) {
    showToast(`+${data.xp_earned} XP`, 'xp', 2500);
  }

  await _delay(SEQ_GAP); if (stale()) return;

  // Phase 6 — Reinforcement badges (streak, achievement, level badges etc.)
  if (Array.isArray(data.new_awards)) {
    const reinforcementAwards = data.new_awards.filter(a => a.award_class !== 'quota_major');
    for (const award of reinforcementAwards) {
      const icon = award.icon || '🏅';
      showToast(`${icon} ${award.name}`, 'award', 4000);
      await _delay(SEQ_GAP); if (stale()) return;
    }
  }

  // Phase 7 — Next action CTA
  if (data.next_action) {
    renderNextAction(data.next_action);
  }
  if (data.daily_missions) {
    renderDailyMissions(data.daily_missions);
  }
}

// ── Next-action renderer ──────────────────────────────────────────────────
function renderNextAction(na) {
  if (!na || !na.primary) return;
  const p = na.primary;

  // Find or create the next-action slot inside the progress strip area
  let el = document.getElementById('nextActionCta');
  if (!el) {
    el = document.createElement('div');
    el.id = 'nextActionCta';
    el.className = 'next-action-cta';
    // Insert after progressStrip if it exists, else append to body
    const strip = document.getElementById('progressStrip');
    if (strip && strip.parentNode) {
      strip.parentNode.insertBefore(el, strip.nextSibling);
    } else {
      document.body.appendChild(el);
    }
  }

  const typeIcon = {
    daily_challenge: '📅',
    practice: '🎨',
    navigate: '→',
  }[p.type] || '→';

  el.innerHTML = `
    <span class="na-icon">${typeIcon}</span>
    <span class="na-label">${p.label}</span>
    <span class="na-reason">${p.reason}</span>
  `;
  el.dataset.actionId = p.id;
  el.dataset.route = (p.payload && p.payload.route) || '';
  el.style.display = 'flex';
}

function renderDailyMissions(dm) {
  if (!dm || !Array.isArray(dm.missions)) return;
  let el = document.getElementById('dailyMissions');
  if (!el) {
    el = document.createElement('div');
    el.id = 'dailyMissions';
    el.className = 'next-action-cta';
    const anchor = document.getElementById('nextActionCta') || document.getElementById('progressStrip');
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(el, anchor.nextSibling);
    } else {
      document.body.appendChild(el);
    }
  }
  const completed = dm.missions.filter(m => m.completed).length;
  const total = dm.missions.length;
  const chips = dm.missions.map((m) => {
    const state = m.completed ? '✅' : '⬜';
    return `<span class="na-label">${state} ${m.icon || '🎯'} ${m.label}</span>`;
  }).join('');
  el.innerHTML = `
    <span class="na-icon">📆</span>
    <span class="na-label">Daily missions ${completed}/${total}</span>
    <span class="na-reason">${chips}</span>
  `;
  el.style.display = 'flex';
}

// ── Badge helper ──────────────────────────────────────────────────────────
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

// ── Recipe strip (stacked-bar visualization of current mix) ──────────────
// Sets each .recipe-seg's flex-basis to (drops / total) * 100%, or 0 when total is 0.
function updateRecipeStrip(counts) {
  const c = counts || { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
  const total = (c.white | 0) + (c.black | 0) + (c.red | 0) + (c.yellow | 0) + (c.blue | 0);
  document.querySelectorAll('.recipe-seg').forEach((seg) => {
    const color = seg.dataset.color;
    const drops = (c[color] | 0);
    const pct = total > 0 ? (drops / total) * 100 : 0;
    seg.style.flexBasis = pct + '%';
  });
}

// ── Match quality bar ─────────────────────────────────────────────────────
function updateMatchBar(deltaE) {
  const container = document.getElementById('matchBarContainer');
  const fill = document.getElementById('matchBarFill');
  const label = document.getElementById('matchBarLabel');
  if (!container || !fill || !label) return;

  container.style.display = '';

  if (isPerfectMatch(deltaE)) {
    fill.style.width = '100%';
    fill.style.backgroundColor = 'var(--accent-success)';
    label.textContent = 'Match!';
    return;
  }

  // Exponential decay: K=3 keeps the bar well below full for deltaE ~0.5-1.0
  const K = 3;
  const progress = Math.max(0, Math.min(99, 100 * Math.exp(-deltaE / K)));
  fill.style.width = progress + '%';

  if (progress < 19) {
    fill.style.backgroundColor = 'var(--accent-danger)';
    label.textContent = 'Far';
  } else if (progress < 51) {
    fill.style.backgroundColor = 'var(--accent-warning)';
    label.textContent = 'Closer';
  } else if (progress < 85) {
    fill.style.backgroundColor = '#8BC34A';
    label.textContent = 'Very close';
  } else {
    fill.style.backgroundColor = '#8BC34A';
    label.textContent = 'Nearly there!';
  }
}

// ── Progress indicator ────────────────────────────────────────────────────
function updateProgressIndicator(currentIndex, total, visitCount) {
  const textEl = document.getElementById('progressText');
  const segEl = document.getElementById('progressSegments');
  if (!textEl || !segEl) return;

  const suffix = visitCount != null && visitCount > 0 ? ` · ${visitCount} this visit` : '';
  textEl.textContent = `Color ${currentIndex + 1} of ${total}${suffix}`;
  segEl.innerHTML = '';
  for (let i = 0; i < total; i++) {
    const seg = document.createElement('div');
    seg.className = 'progress-segment';
    if (i < currentIndex) seg.classList.add('is-done');
    else if (i === currentIndex) seg.classList.add('is-current');
    segEl.appendChild(seg);
  }
}

// ── Control state ─────────────────────────────────────────────────────────
function setControlState(state) {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const skipBtn = document.getElementById('skipBtn');
  const retryBtn = document.getElementById('retryBtn');
  const restartBtn = document.getElementById('restartBtn');

  // Done button permanently removed from active play — always hidden
  if (stopBtn) stopBtn.style.display = 'none';

  if (state === 'idle') {
    startBtn.style.display = ''; startBtn.disabled = false;
    skipBtn.style.display = 'none';
    retryBtn.style.display = 'none';
    restartBtn.disabled = true;
  } else if (state === 'mixing') {
    startBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = 'Skip';
    retryBtn.style.display = ''; retryBtn.disabled = false;
    restartBtn.disabled = false;
  } else if (state === 'stopped') {
    startBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = 'Skip';
    retryBtn.style.display = 'none';
    restartBtn.disabled = false;
  } else if (state === 'completed') {
    startBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = 'Next color';
    retryBtn.style.display = 'none';
    restartBtn.disabled = false;
  }
}

// ── Mixing enable/disable ─────────────────────────────────────────────────
function disableColorMixing() {
  const mc = document.getElementById('mainContent');
  if (mc) mc.classList.add('mixing-disabled');
}
window.disableColorMixing = disableColorMixing;

function enableColorMixing() {
  const mc = document.getElementById('mainContent');
  if (mc) mc.classList.remove('mixing-disabled');
}

window.currentUserBirthdate = localStorage.getItem('userBirthdate');
window.currentUserGender = localStorage.getItem('userGender');
window.currentSessionSaved = false;

let dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
window.shadeMatchDropCounts = dropCounts;
let currentTargetColor = null;
let targetColor = [255, 255, 255];

function resetMix() {
  document.querySelectorAll('.color-circle').forEach(circle => { circle.textContent = '0'; });
  document.getElementById('currentMix').style.backgroundColor = 'rgb(255, 255, 255)';
  document.getElementById('mixedRgbValues').textContent = 'RGB: [255, 255, 255]';
  window.lastMixDeltaE = NaN;
  currentMixedRgb = [255, 255, 255];

  dropCounts = { white: 0, black: 0, red: 0, yellow: 0, blue: 0 };
  window.shadeMatchDropCounts = dropCounts;
  resetAllBadges();
  updateRecipeStrip(dropCounts);

  const matchContainer = document.getElementById('matchBarContainer');
  if (matchContainer) matchContainer.style.display = 'none';

  _calcColorGen++;          // invalidate any in-flight /calculate responses for the old color
  mixStateStepId = 0;
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
    loadAndRenderProgress();
  }
});

function updateBox(id, rgb) {
  const el = document.getElementById(id);
  el.style.backgroundColor = `rgb(${rgb.join(',')})`;
}

async function refreshDatabaseConnection() {
  try {
    const response = await fetch('/refresh_connection', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (response.ok) {
      const result = await response.json();
      return result.status === 'success';
    }
    return false;
  } catch { return false; }
}

// ── Save session to server ────────────────────────────────────────────────
async function saveSessionToServer(session) {
  if (!requireAuthenticatedUser({ silent: true })) {
    console.error('❌ No user ID found — not saving session');
    return;
  }

  let sessionData;
  if (session.target && session.drops) {
    const mix = Array.isArray(session.mixed_rgb) && session.mixed_rgb.length === 3
      ? session.mixed_rgb
      : currentMixedRgb;
    sessionData = {
      attempt_uuid: session.attempt_uuid || generateUUID(),
      user_id: window.currentUserId,
      target_color_id: session.target_color_id ?? null,
      target_r: session.target[0], target_g: session.target[1], target_b: session.target[2],
      drop_white: session.drops.white, drop_black: session.drops.black,
      drop_red: session.drops.red, drop_yellow: session.drops.yellow, drop_blue: session.drops.blue,
      mixed_r: mix[0], mixed_g: mix[1], mixed_b: mix[2],
      delta_e: session.deltaE, time_sec: session.time,
      timestamp: session.timestamp, skipped: session.skipped || false,
      attempt_ended_client_ts_ms: session.attempt_ended_client_ts_ms ?? null,
    };
  } else {
    sessionData = {
      attempt_uuid: session.attempt_uuid || generateUUID(),
      user_id: session.user_id,
      target_color_id: session.target_color_id ?? null,
      target_r: session.target_r, target_g: session.target_g, target_b: session.target_b,
      drop_white: session.drop_white, drop_black: session.drop_black,
      drop_red: session.drop_red, drop_yellow: session.drop_yellow, drop_blue: session.drop_blue,
      delta_e: session.delta_e, time_sec: session.time_sec,
      timestamp: session.timestamp, skipped: session.skipped || false,
      attempt_ended_client_ts_ms: session.attempt_ended_client_ts_ms ?? null,
    };
    const mix = Array.isArray(session.mixed_rgb) && session.mixed_rgb.length === 3
      ? session.mixed_rgb
      : currentMixedRgb;
    sessionData.mixed_r = mix[0];
    sessionData.mixed_g = mix[1];
    sessionData.mixed_b = mix[2];
  }

  trackEvent('save_attempt', { skipped: sessionData.skipped || false });

  try {
    const res = await fetch('/save_session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sessionData),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.status !== 'success') {
      console.error('Failed to save session:', data.error);
    } else {
      handleProgressionResponse(data);
    }
  } catch (error) {
    console.error('Error saving session:', error);
  }
}

// ── Quota-aware target selection ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  disableColorMixing();

  const baseColors = {
    white: [255, 255, 255], black: [0, 0, 0],
    red: [255, 0, 0], yellow: [255, 255, 0], blue: [0, 0, 255],
  };

  let fullCatalog = [];
  const targetColors = [];
  let sessionShadesCompleted = 0;

  function onlyBlockedByPracticeQuota(catalog) {
    if (!catalog || !catalog.length) return false;
    const unlocked = catalog.filter((c) => c.unlocked !== false);
    if (!unlocked.length) return false;
    return unlocked.every((c) => c.under_quota === false);
  }

  function buildShuffledPlayQueue(catalog) {
    if (!catalog || !catalog.length) return [];
    const unlockedOk = (c) => c.unlocked !== false;
    const underQuotaOk = (c) => c.under_quota !== false;
    let pool = catalog.filter((c) => unlockedOk(c) && underQuotaOk(c));
    if (!pool.length) {
      const hasUnlocked = catalog.some(unlockedOk);
      if (hasUnlocked) {
        return [];
      }
      pool = catalog.slice();
    }
    const arr = pool.map((x) => x);
    for (let i = arr.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  async function refreshCatalogFromServer() {
    try {
      const uid = window.currentUserId || localStorage.getItem('userId') || '';
      const url = uid ? `/api/target-colors?user_id=${encodeURIComponent(uid)}` : '/api/target-colors';
      const res = await fetch(url);
      const d = await res.json();
      if (d.status === 'success' && Array.isArray(d.colors) && d.colors.length > 0) {
        fullCatalog = d.colors;
        return true;
      }
    } catch { /* keep existing catalog */ }
    return false;
  }

  function maybeRhythmFeedback(sessionN, reshuffled) {
    if (reshuffled || sessionN <= 0) return;
    if (sessionN % 12 === 0) {
      showToast('Strong stretch — open Results anytime for awards and coverage.', 'info', 4200);
      return;
    }
    if (sessionN % 7 === 0) {
      showToast('Coverage builds shade by shade. The strip above tracks your tier.', 'info', 3600);
      return;
    }
    if (sessionN % 4 === 0) {
      const tips = [
        'Tiny nudges beat big jumps.',
        'Compare the two squares from the side — distance reads clearer.',
        'When stuck: one drop of black or white, then reassess.',
      ];
      showToast(tips[Math.floor(sessionN / 4) % tips.length], 'info', 3000);
    }
  }

  async function goToNextShade() {
    sessionShadesCompleted += 1;
    let reshuffled = false;
    let nextIndex = currentTargetIndex + 1;
    if (nextIndex >= targetColors.length) {
      await refreshCatalogFromServer();
      const next = buildShuffledPlayQueue(fullCatalog);
      if (!next.length) {
        sessionShadesCompleted -= 1;
        alert(
          onlyBlockedByPracticeQuota(fullCatalog)
            ? 'You have reached the practice attempt quota on every shade in your tier. Unlock higher sum-drop caps or add new shades to keep playing.'
            : 'No target colors available. Check the catalog or your tier unlocks.',
        );
        return;
      }
      targetColors.length = 0;
      targetColors.push(...next);
      nextIndex = 0;
      reshuffled = true;
      showToast('New shuffle — fresh order, same practice.', 'info', 3200);
    }
    currentTargetIndex = nextIndex;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    stopTimer();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateProgressIndicator(currentTargetIndex, targetColors.length, sessionShadesCompleted);
    beginAttemptForCurrentTarget();

    maybeRhythmFeedback(sessionShadesCompleted, reshuffled);

    if (sessionShadesCompleted % 6 === 0) {
      loadAndRenderProgress().catch(() => {});
    }
  }

  try {
    const uid = window.currentUserId || localStorage.getItem('userId') || '';
    const url = uid ? `/api/target-colors?user_id=${encodeURIComponent(uid)}` : '/api/target-colors';
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const hint = data.error === 'database_unavailable' && data.message
        ? data.message
        : `Could not load target colors (HTTP ${res.status}). Check the server log.`;
      alert(hint);
      return;
    }
    if (data.status === 'success' && Array.isArray(data.colors) && data.colors.length > 0) {
      fullCatalog = data.colors;
    }
  } catch (e) {
    console.error('Failed to load target colors:', e);
  }
  if (!fullCatalog.length) {
    alert(
      'Could not load target colors. '
      + 'Ensure the database is reachable (DATABASE_URL / VPN / firewall), migrated (npm run db:migrate), and try again.',
    );
    return;
  }

  const initialQueue = buildShuffledPlayQueue(fullCatalog);
  if (!initialQueue.length) {
    alert(
      onlyBlockedByPracticeQuota(fullCatalog)
        ? 'You have reached the practice attempt quota on every shade in your tier. Open Results for coverage, or unlock higher sum-drop caps to access more shades.'
        : 'No playable colors (logged-in users need unlocked shades with drop recipes). '
          + 'See scripts/BACKFILL_TARGET_COLOR_DROPS.md or use Lab.',
    );
    return;
  }
  targetColors.push(...initialQueue);

  // app_ready: catalog loaded, user context available, ready to play
  trackEvent('app_ready');
  let currentTargetIndex = 0;
  currentTargetColor = targetColors[0];
  targetColor = currentTargetColor.rgb;

  function setGameTarget(color) {
    targetColor = color.rgb;
    window.shadeMatchTargetRgb = color.rgb;
  }
  setGameTarget(currentTargetColor);
  updateProgressIndicator(currentTargetIndex, targetColors.length, sessionShadesCompleted);

  function showSkipPerceptionModal() {
    return new Promise((resolve) => {
      const modal = document.getElementById('skipPerceptionModal');
      if (!modal) { resolve(null); return; }
      modal.style.display = 'flex';
      const options = [
        { id: 'skipPerceptionIdentical', value: 'identical' },
        { id: 'skipPerceptionAcceptable', value: 'acceptable' },
        { id: 'skipPerceptionUnacceptable', value: 'unacceptable' },
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
    const stepId = ++mixStateStepId;
    const totalDrops = Object.values(dropCounts).reduce((a, b) => a + b, 0);
    updateRecipeStrip(dropCounts);
    if (totalDrops === 0) {
      currentMixedRgb = [255, 255, 255];
      updateBox('currentMix', currentMixedRgb);
      document.getElementById('mixedRgbValues').textContent = 'RGB: [255, 255, 255]';
      window.lastMixDeltaE = NaN;
      return { stepId, mixedRGB: [...currentMixedRgb], deltaEResolvedAtEmit: null };
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

    currentMixedRgb = mixbox.latentToRgb(zMix).map(Math.round);
    updateBox('currentMix', currentMixedRgb);
    document.getElementById('mixedRgbValues').textContent = `RGB: [${currentMixedRgb.join(', ')}]`;

    const _myGen = _calcColorGen;  // snapshot before async gap
    fetch('/calculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: targetColor, mixed: currentMixedRgb }),
    })
      .then(res => res.json())
      .then(async (data) => {
        if (_calcColorGen !== _myGen) return;  // response belongs to a previous color — discard
        if (data.error) return console.error('Server error:', data.error);
        window.lastMixDeltaE = data.delta_e;
        updateMatchBar(data.delta_e);
        updateBufferedEventDelta(stepId, data.delta_e);

        if (isPerfectMatch(data.delta_e) && !currentSessionSaved) {
          stopTimer();
          const session = {
            attempt_uuid: telemetryAttempt?.attempt_uuid || generateUUID(),
            user_id: window.currentUserId,
            target: targetColor,
            target_color_id: currentTargetColor.id,
            drops: { ...dropCounts },
            mixed_rgb: [...currentMixedRgb],
            deltaE: data.delta_e,
            time: parseFloat(document.getElementById('timer').textContent),
            timestamp: new Date().toISOString(),
            skipped: false,
            attempt_ended_client_ts_ms: nowClientTsMs(),
          };
          currentSessionSaved = true;
          window.currentSessionSaved = true;
          sessionLogs.push(session);
          await flushTelemetry({
            finalize: true,
            endReason: 'saved_match',
            terminalBoundaryType: 'boundary_save',
          });
          await saveSessionToServer(session);
          setControlState('completed');
        }
      });

    return { stepId, mixedRGB: [...currentMixedRgb], deltaEResolvedAtEmit: null };
  }

  // ── Button handlers ───────────────────────────────────────────────────
  document.getElementById('startBtn').addEventListener('click', async () => {
    if (!requireAuthenticatedUser()) return;
    if (telemetryAttempt) {
      await flushTelemetry({
        finalize: true,
        endReason: 'abandoned',
      });
    }

    refreshDatabaseConnection();
    await refreshCatalogFromServer();
    sessionShadesCompleted = 0;
    const newQueue = buildShuffledPlayQueue(fullCatalog);
    if (!newQueue.length) {
      alert(
        onlyBlockedByPracticeQuota(fullCatalog)
          ? 'Every shade in your tier is already at the practice attempt quota. Unlock more shades or raise your sum-drop cap to continue.'
          : 'No playable colors for your tier. See Lab / backfill docs.',
      );
      return;
    }
    targetColors.length = 0;
    targetColors.push(...newQueue);

    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateProgressIndicator(currentTargetIndex, targetColors.length, sessionShadesCompleted);
    beginAttemptForCurrentTarget();
  });

  document.getElementById('skipBtn').addEventListener('click', async () => {
    if (!requireAuthenticatedUser()) return;
    refreshDatabaseConnection();
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    const alreadyCompletedThisColor = window.currentSessionSaved === true;

    const shouldSaveSkip = Number.isFinite(currentDeltaE) && !isPerfectMatch(currentDeltaE) && !alreadyCompletedThisColor;

    if (shouldSaveSkip) {
      const skipPerception = await showSkipPerceptionModal();
      if (!skipPerception) return;
      const skipData = {
        attempt_uuid: telemetryAttempt?.attempt_uuid || generateUUID(),
        user_id: window.currentUserId,
        target_color_id: currentTargetColor.id,
        target_r: targetColor[0], target_g: targetColor[1], target_b: targetColor[2],
        drop_white: dropCounts.white || 0, drop_black: dropCounts.black || 0,
        drop_red: dropCounts.red || 0, drop_yellow: dropCounts.yellow || 0, drop_blue: dropCounts.blue || 0,
        mixed_r: currentMixedRgb[0],
        mixed_g: currentMixedRgb[1],
        mixed_b: currentMixedRgb[2],
        time_sec: parseFloat(document.getElementById('timer').textContent),
        timestamp: new Date().toISOString(),
        delta_e: currentDeltaE,
        skip_perception: skipPerception,
        attempt_ended_client_ts_ms: nowClientTsMs(),
      };
      await flushTelemetry({
        finalize: true,
        endReason: 'skipped',
        terminalBoundaryType: 'boundary_skip',
      });
      trackEvent('save_attempt', { skipped: true });
      try {
        const res = await fetch('/save_skip', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(skipData),
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
          alert('Failed to save skip data. Please try again.');
          return;
        }
        handleProgressionResponse(data);
      } catch {
        alert('Error saving skip data. Please check your connection and try again.');
        return;
      }
    }

    await goToNextShade();
  });

  document.getElementById('restartBtn').addEventListener('click', async () => {
    if (!requireAuthenticatedUser()) return;
    await flushTelemetry({
      finalize: true,
      endReason: 'restart',
      terminalBoundaryType: 'boundary_restart',
    });

    refreshDatabaseConnection();
    await refreshCatalogFromServer();
    sessionShadesCompleted = 0;
    const newQueue = buildShuffledPlayQueue(fullCatalog);
    if (!newQueue.length) {
      alert(
        onlyBlockedByPracticeQuota(fullCatalog)
          ? 'Every shade in your tier is already at the practice attempt quota. Unlock more shades or raise your sum-drop cap to continue.'
          : 'No playable colors. Check catalog / tier unlocks.',
      );
      return;
    }
    targetColors.length = 0;
    targetColors.push(...newQueue);

    currentTargetIndex = 0;
    currentTargetColor = targetColors[currentTargetIndex];
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateProgressIndicator(currentTargetIndex, targetColors.length, sessionShadesCompleted);
    document.getElementById('overflowDropdown').classList.remove('is-open');
    beginAttemptForCurrentTarget();
  });

  document.getElementById('retryBtn').addEventListener('click', async () => {
    if (!requireAuthenticatedUser()) return;
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    if (!isNaN(currentDeltaE)) {
      const session = {
        attempt_uuid: telemetryAttempt?.attempt_uuid || generateUUID(),
        user_id: window.currentUserId,
        target: targetColor,
        target_color_id: currentTargetColor.id,
        drops: { ...dropCounts },
        mixed_rgb: [...currentMixedRgb],
        deltaE: currentDeltaE,
        time: parseFloat(document.getElementById('timer').textContent),
        timestamp: new Date().toISOString(),
        skipped: true,
        attempt_ended_client_ts_ms: nowClientTsMs(),
      };
      sessionLogs.push(session);
      await flushTelemetry({
        finalize: true,
        endReason: 'reset',
        terminalBoundaryType: 'boundary_reset',
      });
      saveSessionToServer(session);
    }
    resetMix();
    resetTimerDisplay();
    stopTimer();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    beginAttemptForCurrentTarget();
  });

  // ── Palette interaction ────────────────────────────────────────────────
  let _firstInteractionFired = false;
  document.querySelectorAll('.color-circle').forEach(circle => {
    circle.addEventListener('click', (e) => {
      e.preventDefault();
      const color = circle.dataset.color;
      const clickTs = nowClientTsMs();
      const beforeSnapshot = buildMixSnapshot();
      dropCounts[color]++;
      circle.textContent = dropCounts[color];
      updateBadge(color, dropCounts[color]);

      if (!_firstInteractionFired) {
        _firstInteractionFired = true;
        trackEvent('first_palette_interaction', { color });
      }

      circle.classList.add('is-tapped');
      setTimeout(() => circle.classList.remove('is-tapped'), 200);
      if (navigator.vibrate) navigator.vibrate(15);

      const mixResult = updateCurrentMix();
      const afterSnapshot = buildMixSnapshot({
        mixedRgbOverride: mixResult?.mixedRGB,
        deltaEOverride: null,
      });

      if (telemetryAttempt && telemetryAttempt.first_action_client_ts_ms == null) {
        telemetryAttempt.first_action_client_ts_ms = clickTs;
        postAttemptHeaderUpdate();
      }

      enqueueTelemetryEvent({
        event_type: 'action_add',
        action_color: color,
        client_ts_ms: clickTs,
        state_before_json: beforeSnapshot,
        state_after_json: afterSnapshot,
        metadata_json: {
          step_id: mixResult?.stepId ?? null,
          interaction: 'click_add',
        },
      });
    });
  });

  document.querySelectorAll('.minus-button').forEach(button => {
    button.addEventListener('click', (e) => {
      e.preventDefault();
      const color = button.dataset.color;
      if (dropCounts[color] > 0) {
        const clickTs = nowClientTsMs();
        const beforeSnapshot = buildMixSnapshot();
        dropCounts[color]--;
        document.querySelector(`.color-circle[data-color='${color}']`).textContent = dropCounts[color];
        updateBadge(color, dropCounts[color]);
        const mixResult = updateCurrentMix();
        const afterSnapshot = buildMixSnapshot({
          mixedRgbOverride: mixResult?.mixedRGB,
          deltaEOverride: null,
        });

        if (telemetryAttempt && telemetryAttempt.first_action_client_ts_ms == null) {
          telemetryAttempt.first_action_client_ts_ms = clickTs;
          postAttemptHeaderUpdate();
        }

        enqueueTelemetryEvent({
          event_type: 'action_remove',
          action_color: color,
          client_ts_ms: clickTs,
          state_before_json: beforeSnapshot,
          state_after_json: afterSnapshot,
          metadata_json: {
            step_id: mixResult?.stepId ?? null,
            interaction: 'click_remove',
          },
        });
      }
    });
  });
});

// ── Login form handler ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const userId = document.getElementById('loginId').value.toUpperCase();
      try {
        const response = await fetch('/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userId }),
        });
        const data = await response.json();
        if (data.status === 'success') {
          localStorage.removeItem('pendingVerifyUserId');
          localStorage.setItem('userId', userId);
          localStorage.setItem('userBirthdate', data.birthdate);
          localStorage.setItem('userGender', data.gender);
          if (data.email) localStorage.setItem('userEmail', data.email);
          localStorage.setItem('userEmailVerified', data.email_verified ? '1' : '0');
          localStorage.setItem('emailOptInReminders', data.email_opt_in_reminders ? '1' : '0');
          window.currentUserId = userId;
          document.getElementById('userModal').style.display = 'none';
          resetMix();
          resetTimerDisplay();
          disableColorMixing();
          displayUserId();
          loadAndRenderProgress();
          if (window.location && typeof window.location.reload === 'function') {
            window.location.reload();
          }
        } else if (data.code === 'EMAIL_NOT_VERIFIED') {
          const resend = confirm('Your email is not verified yet. Resend verification email now?');
          if (resend) {
            try {
              const resendResp = await fetch('/email/verification/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId }),
              });
              const resendData = await resendResp.json();
              if (resendData.status === 'success') {
                alert('Verification email sent. Please verify first, then log in.');
              } else {
                alert(resendData.message || 'Could not send verification email.');
              }
            } catch {
              alert('Could not send verification email.');
            }
          }
        } else {
          alert(data.message || 'Invalid user ID. Please try again.');
        }
      } catch {
        alert('Invalid user ID. Please try again.');
      }
    });
  }
});

// ── Continue button handler ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  const continueBtn = document.getElementById('continueBtn');
  if (continueBtn) {
    continueBtn.addEventListener('click', function () {
      document.getElementById('userModal').style.display = 'none';
      resetMix();
      resetTimerDisplay();
      disableColorMixing();
    });
  }
  const showLoginBtn = document.getElementById('showLoginBtn');
  if (showLoginBtn) {
    showLoginBtn.addEventListener('click', function () {
      document.getElementById('registerSection').style.display = 'none';
      document.getElementById('loginSection').style.display = 'block';
    });
  }
  const showRegisterBtn = document.getElementById('showRegisterBtn');
  if (showRegisterBtn) {
    showRegisterBtn.addEventListener('click', function () {
      document.getElementById('loginSection').style.display = 'none';
      document.getElementById('registerSection').style.display = 'block';
    });
  }
});

// ── PWA install affordance ────────────────────────────────────────────────
let _deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _deferredInstallPrompt = e;
  // Show install CTA if user hasn't dismissed it
  if (!localStorage.getItem('pwaDismissed')) {
    _showPwaInstallCta();
  }
});

window.addEventListener('appinstalled', () => {
  _hidePwaInstallCta();
  localStorage.setItem('pwaDismissed', '1');
  _deferredInstallPrompt = null;
});

function _showPwaInstallCta() {
  let el = document.getElementById('pwaInstallCta');
  if (!el) return;
  el.style.display = 'flex';
}

function _hidePwaInstallCta() {
  const el = document.getElementById('pwaInstallCta');
  if (el) el.style.display = 'none';
}

window.triggerPwaInstall = async function () {
  if (_deferredInstallPrompt) {
    _deferredInstallPrompt.prompt();
    const { outcome } = await _deferredInstallPrompt.userChoice;
    localStorage.setItem('pwaDismissed', '1');
    _hidePwaInstallCta();
    _deferredInstallPrompt = null;
  }
};

window.dismissPwaInstall = function () {
  localStorage.setItem('pwaDismissed', '1');
  _hidePwaInstallCta();
};

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    flushTelemetry({ useBeacon: true });
  }
});

window.addEventListener('beforeunload', () => {
  if (telemetryAttempt && !telemetryAttempt.end_reason) {
    telemetryAttempt.end_reason = 'abandoned';
    telemetryAttempt.attempt_ended_client_ts_ms = nowClientTsMs();
  }
  flushTelemetry({ useBeacon: true });
});

// On DOMContentLoaded: check if already dismissed, show iOS fallback if applicable
document.addEventListener('DOMContentLoaded', function () {
  if (localStorage.getItem('pwaDismissed')) return;
  // iOS Safari: no beforeinstallprompt — show manual instructions
  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isInStandaloneMode = window.navigator.standalone === true;
  if (isIos && !isInStandaloneMode) {
    _showPwaInstallCta();
    const hint = document.getElementById('pwaIosHint');
    if (hint) hint.style.display = '';
    const btn = document.getElementById('pwaInstallBtn');
    if (btn) btn.style.display = 'none';
  }
});

// ── Push notification opt-in ──────────────────────────────────────────────
window.requestPushPermission = async function () {
  const btn = document.getElementById('pushOptInBtn');

  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    showToast('Push notifications are not supported in this browser.', 'info', 4000);
    return;
  }

  const userId = window.currentUserId || localStorage.getItem('userId');
  if (!userId) {
    showToast('Please log in before enabling reminders.', 'info', 4000);
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = '⏳ Setting up…'; }

  try {
    const permission = await Notification.requestPermission();
    if (permission === 'denied') {
      showToast('Notifications blocked. Enable them in browser settings.', 'info', 5000);
      if (btn) { btn.disabled = false; btn.innerHTML = '🔔 Enable daily reminders'; }
      return;
    }
    if (permission !== 'granted') {
      if (btn) { btn.disabled = false; btn.innerHTML = '🔔 Enable daily reminders'; }
      return;
    }

    const keyRes = await fetch('/push/vapid-public-key');
    const keyData = await keyRes.json();
    const vapidKey = keyData.vapid_public_key;
    if (!vapidKey) {
      showToast('Push notifications not configured on this server.', 'info', 4000);
      if (btn) btn.style.display = 'none';
      return;
    }

    // Wait for the service worker to be ready (registered from /sw.js)
    const swReg = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error('SW timeout')), 8000)),
    ]);

    const subscription = await swReg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidKey),
    });

    const subJson = subscription.toJSON();
    const res = await fetch('/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        endpoint: subJson.endpoint,
        p256dh: subJson.keys.p256dh,
        auth: subJson.keys.auth,
      }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('🔔 Daily reminders enabled!', 'award', 4000);
      if (btn) btn.style.display = 'none';
      localStorage.setItem('pushSubscribed', '1');
    } else {
      throw new Error(data.message || 'Subscribe failed');
    }
  } catch (err) {
    console.error('Push subscribe error:', err);
    showToast('Could not enable reminders. Try again later.', 'info', 4000);
    if (btn) { btn.disabled = false; btn.innerHTML = '🔔 Enable daily reminders'; }
  }
};

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return new Uint8Array([...rawData].map(c => c.charCodeAt(0)));
}

// ── Confetti ──────────────────────────────────────────────────────────────
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
