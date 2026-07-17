// main.js (Mixbox JS + Flask colormath backend)

import { startTimer, stopTimer, resetTimerDisplay } from './timer.js';
import { captureEnv } from './env_capture.js?v=20260508-qc2';
import { sfx } from './sfx.js?v=20260710';
import { shareCard } from './share-card.js?v=20260709-share1';

console.log('✅ main.js loaded');
let sessionLogs = [];
let currentSessionSaved = false;
// True once a give-up (skip) has been persisted for the current color, so
// re-clicking "Next color" advances instead of re-opening the perception modal.
let skipSavedThisColor = false;
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
// DeltaE after each add/remove step of the current round — outlives the
// telemetry buffer (which flushes mid-round) so share texts can tell the
// story of the solve without leaking the recipe.
let deltaJourney = [];

function journeyGlyphs(finalDeltaE) {
  const vals = deltaJourney.filter(Number.isFinite);
  if (Number.isFinite(finalDeltaE)) vals.push(finalDeltaE);
  if (!vals.length) return '';
  const cap = 10;
  let picked = vals;
  if (vals.length > cap) {
    picked = [];
    const step = (vals.length - 1) / (cap - 1);
    for (let i = 0; i < cap; i++) picked.push(vals[Math.round(i * step)]);
  }
  const sq = d => (d <= 1 ? '🟩' : d <= 3 ? '🟨' : d <= 8 ? '🟧' : '🟥');
  let s = picked.map(sq).join('');
  if (picked[picked.length - 1] <= 0.01) s += '⭐';
  return s;
}

function getAuthenticatedUserId() {
  const id = window.currentUserId || localStorage.getItem('userId') || '';
  return typeof id === 'string' ? id.trim().toUpperCase() : '';
}

function promptLoginRequired(reason) {
  const msg = reason
    || t('You must be logged in with a valid user ID to play. Please log in or register to continue.');
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
  // A new round starts here — retire any post-match share prompt from the slot.
  setCta('share', null);
  deltaJourney = [];
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
  processPendingChallengeClaim();

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
      processPendingChallengeClaim();
      clearInterval(checkUserIdInterval);
    }
  }, 1000);
  setTimeout(() => clearInterval(checkUserIdInterval), 30000);
});

// ── Toast system ──────────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  // Don't talk over the spotlight walkthrough — hold toasts until it ends.
  if (window.SpotlightGuide && SpotlightGuide.isActive && SpotlightGuide.isActive()) {
    setTimeout(() => showToast(message, type, duration), 1500);
    return;
  }
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

window.showToast = showToast;

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
      if (data.daily_status) renderDailyStatusBadge(data.daily_status);
      if (data.daily_missions) renderDailyMissions(data.daily_missions);
      // Unconditional: absent echo must clear a banner left from a prior load.
      renderChallengeEcho(data.challenge_echo);
    }
  } catch { /* silent */ }
}

function renderProgressStrip(p) {
  const strip = document.getElementById('progressStrip');
  if (!strip) return;

  // Primary bar: quota coverage (completed_attempt_units / required_attempt_units)
  const quotaPct = p.level_progress_pct != null ? p.level_progress_pct : 0;

  const freezeHtml = p.streak_freeze_available > 0
    ? `<span class="ps-freeze" title="${t('Streak freezes available')}">🧊 ${p.streak_freeze_available}</span>`
    : '';

  const completedColors = p.completed_colors != null ? p.completed_colors : 0;
  const totalColors = p.total_tracked_colors != null ? p.total_tracked_colors : '?';
  const coveragePct = p.catalog_coverage_pct != null ? p.catalog_coverage_pct.toFixed(1) : '0.0';

  const levelTitle = p.is_maxed_out
    ? `${p.level_name} — ${t('All Colors Mastered!')}`
    : `${p.level_name} — ${t('{done}/{total} colors complete ({pct}%)').replace('{done}', String(completedColors)).replace('{total}', String(totalColors)).replace('{pct}', String(coveragePct))}`;

  const colorsAtQuotaTitle = t('{done} of {total} colors completed')
    .replace('{done}', String(completedColors)).replace('{total}', String(totalColors));

  strip.innerHTML = `
    <div class="ps-rank" style="color:${p.rank_color}" title="${t(p.rank)}">${t(p.rank)}</div>
    <div class="ps-level">${p.level_name}</div>
    <div class="ps-xpbar-wrap" title="${levelTitle}">
      <div class="ps-xpbar-fill" style="width:${quotaPct}%"></div>
    </div>
    <div class="ps-colors" title="${colorsAtQuotaTitle}">${completedColors}/${totalColors}</div>
    <div class="ps-streak" title="${t('Current streak')}">🔥 ${p.current_streak}</div>
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

// Back-to-back match chain: toast for the completion bonus. n matches in one
// sitting = an n-times replicated block design, so the reward scales with n.
function maybeMatchChainToast(matchState) {
  if (!matchState || !matchState.match_completed || !matchState.chain) return;
  const c = matchState.chain;
  if (c.length > 1) {
    showToast(t('🏁 {n} matches in one sitting — +{xp} XP bonus')
      .replace('{n}', String(c.length)).replace('{xp}', String(c.xp_bonus)), 'levelup', 5200);
  } else if (c.xp_bonus) {
    showToast(t('🏁 Match complete — +{xp} XP')
      .replace('{xp}', String(c.xp_bonus)), 'xp', 3600);
  }
}

async function handleProgressionResponse(data) {
  if (!data || data.status !== 'success' || data.duplicate) return;

  // Match accounting first (synchronous): the save may have advanced the
  // match or completed it (summary shown on the next round transition).
  if (data.match && window.__applyMatchUpdate) window.__applyMatchUpdate(data.match);
  maybeMatchChainToast(data.match);

  // Claim a sequence slot; abort if a newer response arrives mid-sequence.
  const mySeq = ++_seqId;
  const stale = () => mySeq !== _seqId;

  // Phase 0 — Head-to-head comparison (a modal; shown over the toast stream)
  if (data.challenge) {
    showChallengeComparison(data.challenge, { delayMs: 1500 });
  }

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
    const colorsCtx = p ? ' ' + t('({done}/{total} colors)').replace('{done}', String(p.completed_colors)).replace('{total}', String(p.total_tracked_colors)) : '';
    showToast('⬆️ ' + t('Level {n} reached').replace('{n}', String(data.level_up.to)) + colorsCtx, 'levelup', 5500);
    await _delay(SEQ_GAP * 2); if (stale()) return;
  }

  // Phase 4 — Streak event
  if (data.streak_event === 'started') {
    showToast(t('🔥 Streak started — play again tomorrow!'), 'streak', 3500);
  } else if (data.streak_event === 'incremented' && data.progress) {
    const s = data.progress.current_streak;
    showToast(t('🔥 {n}-day streak!').replace('{n}', String(s)), 'streak', 3000);
  } else if (data.streak_event === 'freeze_consumed') {
    const freeze = data.progress ? data.progress.streak_freeze_available : '?';
    showToast(t('🧊 Streak protected — 1 freeze used ({n} left)').replace('{n}', String(freeze)), 'freeze', 5000);
  } else if (data.streak_event === 'reset') {
    showToast(t('Streak reset. Keep going!'), 'info', 3000);
  }

  await _delay(SEQ_GAP); if (stale()) return;

  // Phase 4.5 — In-session heat (server-computed consecutive completions)
  renderHeatState(data.heat);
  if (data.heat && data.heat.consecutive != null) {
    const pct = Math.round((data.heat.bonus_pct || 0) * 100);
    showToast(t('🔥 On fire! {n} in a row — +{pct}% XP').replace('{n}', String(data.heat.consecutive)).replace('{pct}', String(pct)), 'streak', 3200);
    await _delay(SEQ_GAP); if (stale()) return;
  }

  // Phase 5 — XP (secondary reinforcement — shown after quota signals)
  if (data.xp_earned && data.xp_earned > 0) {
    showToast(t('+{n} XP').replace('{n}', String(data.xp_earned)), 'xp', 2500);
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
  if (data.daily_status) {
    renderDailyStatusBadge(data.daily_status);
  }
  if (data.daily_missions) {
    renderDailyMissions(data.daily_missions);
  }
}

// ── In-session heat indicator (flame chip on the target swatch) ───────────
function renderHeatState(heat) {
  let chip = document.getElementById('heatChip');
  const on = heat && heat.consecutive != null;
  if (!chip) {
    if (!on) return;
    chip = document.createElement('div');
    chip.id = 'heatChip';
    chip.className = 'daily-chip'; // reuse the swatch-overlay chip styling
    chip.style.top = 'auto';
    chip.style.bottom = '10px';
    const pair = document.querySelector('.color-pair');
    if (!pair) return;
    pair.appendChild(chip);
  }
  if (on) {
    const pct = Math.round((heat.bonus_pct || 0) * 100);
    chip.textContent = t('🔥 {n} in a row · +{pct}% XP').replace('{n}', String(heat.consecutive)).replace('{pct}', String(pct));
    chip.style.display = '';
  } else {
    chip.style.display = 'none';
  }
}

// ── Daily round chip (target swatch overlay) ──────────────────────────────
function setDailyChip(on) {
  const chip = document.getElementById('dailyChip');
  if (chip) chip.style.display = on ? '' : 'none';
}

// ── Unified CTA slot ──────────────────────────────────────────────────────
// One in-page surface for every transient message that used to have its own
// banner (post-match share, challenge invite, challenge-echo, streak-at-risk,
// verify-email, enable-reminders, install-app, daily-missions). Sources call
// setCta(key, descriptor|null); the slot renders only the highest-priority
// eligible message, so at most one shows at a time in one consistent style.
// Exposed on window so the inline template script (push/email prompts) and
// share-card.js can register too.
//   descriptor: { icon, labelHtml, reasonHtml?, onDismiss?, variant?,
//                  actions?: [{ label, onClick, variant? }],
//                  actionLabel?, onAction? (single-action shorthand) }
const CTA_PRIORITY = ['share', 'challenge', 'challengeEcho', 'streak', 'emailVerify', 'push', 'pwa', 'missions'];
const _ctaState = {};

function setCta(key, descriptor) {
  if (descriptor) _ctaState[key] = descriptor;
  else delete _ctaState[key];
  renderCtaSlot();
}

function renderCtaSlot() {
  const el = document.getElementById('ctaSlot');
  if (!el) return;
  let chosen = null;
  for (const key of CTA_PRIORITY) {
    if (_ctaState[key]) { chosen = _ctaState[key]; break; }
  }
  if (!chosen) {
    el.style.display = 'none';
    el.innerHTML = '';
    el.className = 'next-action-cta cta-slot';
    return;
  }
  const actions = chosen.actions
    || (chosen.actionLabel ? [{ label: chosen.actionLabel, onClick: chosen.onAction }] : []);
  const parts = [
    `<span class="na-icon">${chosen.icon || '→'}</span>`,
    `<span class="na-label">${chosen.labelHtml || ''}</span>`,
  ];
  const cluster = actions.map((a, i) =>
    `<button type="button" class="btn btn-${a.variant || 'primary'} cta-action" data-cta-action="${i}">${a.label}</button>`);
  if (chosen.onDismiss) {
    cluster.push('<button type="button" class="cta-dismiss" data-cta-dismiss aria-label="' + t('Dismiss') + '">✕</button>');
  }
  if (cluster.length) parts.push(`<span class="cta-actions">${cluster.join('')}</span>`);
  if (chosen.reasonHtml) parts.push(`<span class="na-reason">${chosen.reasonHtml}</span>`);
  el.innerHTML = parts.join('');
  el.className = 'next-action-cta cta-slot' + (chosen.variant ? ' cta-' + chosen.variant : '');
  el.style.display = 'flex';

  actions.forEach((a, i) => {
    const b = el.querySelector(`[data-cta-action="${i}"]`);
    if (b && a.onClick) b.onclick = a.onClick;
  });
  const dismissBtn = el.querySelector('[data-cta-dismiss]');
  if (dismissBtn && chosen.onDismiss) dismissBtn.onclick = chosen.onDismiss;
}

window.setCta = setCta;
window.renderCtaSlot = renderCtaSlot;

// ── Next-action renderer ──────────────────────────────────────────────────
// The slot is reserved for urgent, action-required states. The daily
// challenge is auto-served as the first round of the day (with the target
// swatch chip) and tracked by the header badge, so it no longer needs a
// persistent banner; routine practice suggestions stay ambient.
const BANNER_ACTION_IDS = new Set(['streak_at_risk']);

function renderNextAction(na) {
  const p = na && na.primary;
  if (!p || !BANNER_ACTION_IDS.has(p.id)) { setCta('streak', null); return; }
  const typeIcon = {
    daily_challenge: '📅',
    practice: '🎨',
    navigate: '→',
  }[p.type] || '→';
  setCta('streak', {
    icon: typeIcon,
    labelHtml: p.label,
    reasonHtml: p.reason,
    variant: 'streak',
  });
}

// ── Daily-challenge status badge (always-visible header indicator) ────────
function renderDailyStatusBadge(status) {
  if (status) {
    const cur = window.__dailyStatus;
    // Monotonic within a day: a submitted challenge never becomes
    // unsubmitted. Guards against stale envelopes rendered late by the
    // queued toast sequence overwriting the submit-time update.
    const staleDowngrade = cur && cur.submitted && !status.submitted
      && (!status.challenge_date || !cur.challenge_date
          || status.challenge_date === cur.challenge_date);
    if (!staleDowngrade) window.__dailyStatus = status;
  }
  const st = window.__dailyStatus;
  const host = document.querySelector('.header-right');
  if (!host) return;
  let el = document.getElementById('dailyStatusBadge');
  if (!el) {
    el = document.createElement('button');
    el.id = 'dailyStatusBadge';
    el.type = 'button';
    el.className = 'daily-status-badge';
    host.insertBefore(el, host.firstChild);
  }
  if (!st) { el.style.display = 'none'; return; }
  el.style.display = '';
  el.classList.remove('daily-pending', 'daily-done', 'daily-playing');
  if (window.__dailyPlaying) {
    el.classList.add('daily-playing');
    el.innerHTML = '📅<span class="daily-status-text">' + t('Daily…') + '</span>';
    el.title = t('Daily round in progress — save or skip to submit');
    el.onclick = null;
    el.style.cursor = 'default';
  } else if (st.submitted) {
    el.classList.add('daily-done');
    el.innerHTML = '📅<span class="daily-status-text">' + t('Daily ✓') + '</span>';
    el.title = t("Today's challenge completed — come back tomorrow!");
    el.onclick = null;
    el.style.cursor = 'default';
  } else {
    el.classList.add('daily-pending');
    el.innerHTML = '📅<span class="daily-status-text">' + t('Daily') + '</span><span class="daily-dot"></span>';
    el.title = t("Today's challenge is waiting — tap to play");
    el.onclick = () => { if (window.__startDailyChallenge) window.__startDailyChallenge(); };
    el.style.cursor = 'pointer';
  }
}

function renderDailyMissions(dm) {
  if (!dm || !Array.isArray(dm.missions)) { setCta('missions', null); return; }
  const completed = dm.missions.filter(m => m.completed).length;
  const total = dm.missions.length;
  const chips = dm.missions.map((m) => {
    const state = m.completed ? '✅' : '⬜';
    return `<span class="na-label">${state} ${m.icon || '🎯'} ${m.label}</span>`;
  }).join('');
  setCta('missions', {
    icon: '📆',
    labelHtml: t('Daily missions {done}/{total}').replace('{done}', String(completed)).replace('{total}', String(total)),
    reasonHtml: chips,
    variant: 'missions',
  });
}

// ── Challenge echo ────────────────────────────────────────────────────────
// Challenge history lives on /results and is pull-only, so a creator whose link
// was played never learns it happened. The server sends a rolling 7-day window
// (build_challenge_echo); the seen-marker keeps an acknowledged echo from
// reappearing until a newer acceptance lands.
const CHALLENGE_ECHO_SEEN_KEY = 'sm_challenge_echo_seen';

function renderChallengeEcho(echo) {
  if (!echo || !echo.count) { setCta('challengeEcho', null); return; }

  let seen = '';
  try { seen = localStorage.getItem(CHALLENGE_ECHO_SEEN_KEY) || ''; } catch { /* blocked storage */ }
  // Both stamps are naive-UTC isoformat from the same source, so a lexicographic
  // compare is chronological.
  if (echo.latest_at && seen && seen >= echo.latest_at) {
    setCta('challengeEcho', null);
    return;
  }

  const ack = () => {
    try {
      if (echo.latest_at) localStorage.setItem(CHALLENGE_ECHO_SEEN_KEY, echo.latest_at);
    } catch { /* blocked storage */ }
    setCta('challengeEcho', null);
  };

  const beat = echo.best_beat;
  let labelHtml;
  let reasonHtml = null;
  if (beat) {
    labelHtml = t('{name} beat your challenge — ΔE {de}')
      .replace('{name}', escapeHtml(String(beat.user)))
      .replace('{de}', Number(beat.delta_e).toFixed(2));
    if (echo.count > 1) {
      reasonHtml = t('{n} played it this week.').replace('{n}', String(echo.count));
    }
  } else {
    labelHtml = echo.count === 1
      ? t('Someone played your challenge')
      : t('{n} people played your challenge').replace('{n}', String(echo.count));
    reasonHtml = t('Nobody has beaten you yet.');
  }

  setCta('challengeEcho', {
    icon: beat ? '⚔️' : '👀',
    labelHtml,
    reasonHtml,
    variant: 'challenge',
    actions: [{
      label: t('See it'),
      onClick: () => { ack(); window.location.href = '/results#challenges-section'; },
    }],
    onDismiss: ack,
  });
}

// ── Guest challenge claim ─────────────────────────────────────────────────
// A guest's /c/<code> attempt is recorded under a client-generated
// attempt_uuid that only this browser knows. Remember it, and once the same
// browser is logged in, attach the attempt to that account so it shows up in
// their challenge history (and the creator sees a name instead of "Guest").
const GUEST_CLAIM_KEY = 'sm_guest_challenge_claim';
const GUEST_CLAIM_TTL_MS = 7 * 24 * 3600 * 1000;

function rememberGuestChallengeClaim(attemptUuid, code) {
  try {
    localStorage.setItem(GUEST_CLAIM_KEY, JSON.stringify({
      attempt_uuid: attemptUuid, code: code, ts: Date.now(),
    }));
  } catch { /* blocked storage */ }
}

async function processPendingChallengeClaim() {
  const uid = getAuthenticatedUserId();
  if (!uid) return;
  let claim = null;
  try { claim = JSON.parse(localStorage.getItem(GUEST_CLAIM_KEY) || 'null'); } catch { /* corrupt */ }
  if (!claim || !claim.attempt_uuid || (Date.now() - (claim.ts || 0)) > GUEST_CLAIM_TTL_MS) {
    if (claim !== null) { try { localStorage.removeItem(GUEST_CLAIM_KEY); } catch { /* ignore */ } }
    return;
  }
  try {
    const res = await fetch('/api/challenge/claim-attempt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attempt_uuid: claim.attempt_uuid, user_id: uid }),
    });
    // Any definitive answer settles the claim; only a server/network failure
    // leaves the marker for the next load.
    if (res.status < 500) {
      try { localStorage.removeItem(GUEST_CLAIM_KEY); } catch { /* ignore */ }
    }
    if (res.ok) {
      const d = await res.json();
      if (d && d.claimed) {
        showToast(t('✅ Your challenge result now lives in your account'), 'success', 3500);
      }
    }
  } catch { /* offline — retry on the next load */ }
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
    label.textContent = t('Match!');
    return;
  }

  // Exponential decay: K=3 keeps the bar well below full for deltaE ~0.5-1.0
  const K = 3;
  const progress = Math.max(0, Math.min(99, 100 * Math.exp(-deltaE / K)));
  fill.style.width = progress + '%';

  if (progress < 19) {
    fill.style.backgroundColor = 'var(--accent-danger)';
    label.textContent = t('Far');
  } else if (progress < 51) {
    fill.style.backgroundColor = 'var(--accent-warning)';
    label.textContent = t('Closer');
  } else if (progress < 85) {
    fill.style.backgroundColor = '#8BC34A';
    label.textContent = t('Very close');
  } else {
    fill.style.backgroundColor = '#8BC34A';
    label.textContent = t('Nearly there!');
  }
}

// ── Progress indicator ────────────────────────────────────────────────────
function updateProgressIndicator(currentIndex, total, visitCount) {
  const textEl = document.getElementById('progressText');
  const segEl = document.getElementById('progressSegments');
  if (!textEl || !segEl) return;

  textEl.textContent = t('Round {i} of {total}').replace('{i}', String(currentIndex + 1)).replace('{total}', String(total));
  if (visitCount != null && visitCount > 0) {
    // Separate span so phones can hide the suffix (main.css ≤768px).
    const s = document.createElement('span');
    s.className = 'progress-text-visit';
    s.textContent = ' · ' + t('{n} this visit').replace('{n}', String(visitCount));
    textEl.appendChild(s);
  }
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
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = t('Skip');
    retryBtn.style.display = ''; retryBtn.disabled = false;
    restartBtn.disabled = false;
  } else if (state === 'stopped') {
    startBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = t('Skip');
    retryBtn.style.display = 'none';
    restartBtn.disabled = false;
  } else if (state === 'completed') {
    startBtn.style.display = 'none';
    skipBtn.style.display = ''; skipBtn.disabled = false; skipBtn.textContent = t('Next color');
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
  deltaJourney = [];        // fresh journey for the new color (guests included)
  mixStateStepId = 0;
  currentSessionSaved = false;
  window.currentSessionSaved = false;
  skipSavedThisColor = false;
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
      challenge_code: session.challenge_code ?? null,
      match_id: session.match_id ?? null,
      match_round_index: session.match_round_index ?? null,
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
      match_id: session.match_id ?? null,
      match_round_index: session.match_round_index ?? null,
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
  let sessionShadesCompleted = 0;

  // ── Match state (10 rounds, one colour per macro-cluster) ────────────────
  // The server draws and persists the match (/api/match/current); the client
  // just plays the current round. Round accounting is server-authoritative:
  // the save endpoints advance the match, and their response carries the new
  // state (consumed via window.__applyMatchUpdate).
  let matchState = null;            // {match_id, status, round_count, current_round, rounds}
  let matchRoundActive = false;     // the served target is a match round
  let servedMatchRoundIndex = null; // round_index the player is currently on
  let pendingMatchSummary = null;   // summary delivered by the completing save

  async function refreshMatchFromServer() {
    const uid = getAuthenticatedUserId();
    if (!uid) return null;
    try {
      const res = await fetch('/api/match/current', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid }),
      });
      const d = await res.json().catch(() => ({}));
      if (d.status === 'success' && d.match && Array.isArray(d.match.rounds)) {
        matchState = d.match;
        if (d.next_action) renderNextAction(d.next_action);
        if (d.daily_status) renderDailyStatusBadge(d.daily_status);
        return matchState;
      }
    } catch { /* keep existing match state */ }
    return null;
  }

  function currentMatchRound() {
    if (!matchState || matchState.status !== 'active') return null;
    return matchState.rounds.find((r) => r.round_index === matchState.current_round) || null;
  }

  function applyMatchUpdate(m) {
    // m: {match_id, round_index, current_round, match_completed, summary}
    if (!m || !matchState || m.match_id !== matchState.match_id) return;
    matchState.current_round = m.current_round;
    const done = matchState.rounds.find((r) => r.round_index === m.round_index);
    if (done && done.state === 'current') done.state = 'played';
    if (m.match_completed) {
      matchState.status = 'completed';
      if (m.summary) pendingMatchSummary = m.summary;
    }
  }
  window.__applyMatchUpdate = applyMatchUpdate;

  function serveMatchRoundTarget() {
    const r = currentMatchRound();
    if (!r || !r.target || !Array.isArray(r.target.rgb)) return false;
    matchRoundActive = true;
    servedMatchRoundIndex = r.round_index;
    currentTargetColor = r.target;
    return true;
  }

  function matchSaveFields() {
    // Only round-terminal saves of an actual match round advance the match;
    // daily/challenge rounds and mid-round resets send nulls (server ignores).
    if (matchRoundActive && matchState && matchState.status === 'active'
        && matchState.current_round === servedMatchRoundIndex) {
      return { match_id: matchState.match_id, match_round_index: servedMatchRoundIndex };
    }
    return { match_id: null, match_round_index: null };
  }

  async function fetchMatchSummary(matchId) {
    const uid = getAuthenticatedUserId();
    if (!uid || !matchId) return null;
    try {
      const res = await fetch('/api/match/summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, match_id: matchId }),
      });
      const d = await res.json().catch(() => ({}));
      if (d.status === 'success') return d.summary;
    } catch { /* fall through */ }
    return null;
  }

  function showMatchSummaryModal(summary) {
    return new Promise((resolve) => {
      const old = document.getElementById('matchSummaryModal');
      if (old) old.remove();
      const overlay = document.createElement('div');
      overlay.id = 'matchSummaryModal';
      overlay.className = 'skip-modal-overlay';
      overlay.style.display = 'flex';
      const rowsHtml = (summary && Array.isArray(summary.rounds) ? summary.rounds : [])
        .map((r) => {
          const rgb = Array.isArray(r.target_rgb) ? r.target_rgb : [255, 255, 255];
          const de = Number.isFinite(r.delta_e) ? ('ΔE ' + r.delta_e.toFixed(2)) : t('skipped');
          const name = r.cluster_name || r.cluster_code || '';
          return `<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">
            <span style="width:22px;height:22px;border-radius:5px;flex:none;border:1px solid rgba(0,0,0,.15);background:rgb(${rgb.join(',')})"></span>
            <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.85rem;">${escapeHtml(name)}</span>
            <span style="flex:none;font-size:.85rem;font-variant-numeric:tabular-nums;">${de}</span>
          </div>`;
        }).join('');
      const meanDe = summary && Number.isFinite(summary.mean_delta_e)
        ? `<div style="margin-top:8px;font-size:.9rem;">${t('Average ΔE: {n}').replace('{n}', summary.mean_delta_e.toFixed(2))}</div>`
        : '';
      overlay.innerHTML = `
        <div class="skip-modal-panel" style="max-width:420px;max-height:85vh;overflow:auto;">
          <h2 class="skip-modal-title">🏁 ${t('Match complete!')}</h2>
          <div>${rowsHtml}</div>
          ${meanDe}
          <div class="skip-choices" style="margin-top:12px;">
            <button type="button" id="matchSummaryNewBtn" class="skip-choice skip-choice--accept">${t('New match')}</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const btn = overlay.querySelector('#matchSummaryNewBtn');
      btn.addEventListener('click', () => { overlay.remove(); resolve(); });
    });
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

  function maybeRhythmFeedback(sessionN) {
    if (sessionN <= 0) return;
    if (sessionN % 12 === 0) {
      showToast(t('Strong stretch — open Results anytime for awards and coverage.'), 'info', 4200);
      return;
    }
    if (sessionN % 4 === 0) {
      const tips = [
        t('Tiny nudges beat big jumps.'),
        t('Compare the two squares from the side — distance reads clearer.'),
        t('When stuck: one drop of black or white, then reassess.'),
      ];
      showToast(tips[Math.floor(sessionN / 4) % tips.length], 'info', 3000);
    }
  }

  // Flow probes are no longer served in-game (a quota-neutral extra round
  // does not fit the 10-round match model); the daily challenge remains the
  // probe carrier, so bindProbeAttempt stays.
  async function bindProbeAttempt(slotId, attemptUuid) {
    const uid = getAuthenticatedUserId();
    if (!uid || !slotId || !attemptUuid) return;
    try {
      await fetch('/api/probe/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, slot_id: slotId, attempt_uuid: attemptUuid }),
      });
    } catch (e) { /* server falls back to matching by target colour on save */ }
  }

  // ── Head-to-head challenge mode (/c/<code> links) ───────────────────────
  let challengeMode = null; // window.__challenge while a challenge round is active

  function renderChallengeBanner() {
    const ch = window.__challenge;
    if (!ch) {
      if (window.__challengeMissing) {
        showToast(t('That challenge link is invalid or gone — free play instead.'), 'info', 4200);
      }
      setCta('challenge', null);
      return;
    }
    const bits = [];
    if (Number.isFinite(ch.delta_e)) bits.push(`ΔE ${ch.delta_e.toFixed(2)}`);
    if (Number.isFinite(ch.drops)) bits.push(t('{n} drops').replace('{n}', String(ch.drops)));
    if (Number.isFinite(ch.time_sec)) bits.push(t('{n}s').replace('{n}', String(Math.round(ch.time_sec))));
    setCta('challenge', {
      icon: '⚔️',
      labelHtml: t('{name} challenges you').replace('{name}', escapeHtml(ch.creator)),
      reasonHtml: t('Their result: {bits} — same colour. Beat it.').replace('{bits}', bits.join(' · ') || t('on record')),
      actionLabel: t('Accept'),
      onAction: startChallengeRound,
      variant: 'challenge',
    });
  }

  async function startChallengeRound() {
    const ch = window.__challenge;
    if (!ch || !Array.isArray(ch.target_rgb)) return;
    if (telemetryAttempt) {
      await flushTelemetry({ finalize: true, endReason: 'abandoned' });
    }
    challengeMode = ch;
    window.__guestRoundDone = false;
    dailyMode = null;
    matchRoundActive = false;
    currentTargetColor = {
      id: ch.target_color_id, rgb: ch.target_rgb, name: t('Challenge colour'),
    };
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    stopTimer();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    beginAttemptForCurrentTarget();
    setCta('challenge', null);
    showToast(t('⚔️ Beat {name} — same colour, your mix').replace('{name}', escapeHtml(ch.creator)), 'info', 3000);
  }

  // ── Daily challenge mode (probe carrier) ────────────────────────────────
  let dailyMode = null; // {slot_id} while today's challenge round is active
  let dailyAutoAttempted = false; // auto-serve at most once per session

  async function maybeServeDaily() {
    // The colour of the day is served automatically as the player's next
    // round, once per session, until today's run is submitted. The header
    // badge remains a manual entry point after that.
    const uid = getAuthenticatedUserId();
    if (!uid) return null;
    if (dailyAutoAttempted) return null;
    if (window.__dailyStatus && window.__dailyStatus.submitted) return null;
    dailyAutoAttempted = true;
    try {
      const res = await fetch('/api/daily-challenge/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid }),
      });
      const d = await res.json().catch(() => ({}));
      if (d.status === 'success' && !d.already_submitted && d.target_color
          && Array.isArray(d.target_color.rgb)) {
        return { slot_id: d.slot_id, target_color: d.target_color };
      }
      if (d.already_submitted) {
        renderDailyStatusBadge({
          challenge_date: (window.__dailyStatus || {}).challenge_date,
          submitted: true,
        });
      }
    } catch (e) { /* the daily must never block gameplay */ }
    return null;
  }

  async function startDailyChallenge() {
    if (!requireAuthenticatedUser()) return;
    const uid = getAuthenticatedUserId();
    try {
      const res = await fetch('/api/daily-challenge/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid }),
      });
      const d = await res.json().catch(() => ({}));
      if (d.already_submitted) {
        showToast(t("You've already completed today's challenge — come back tomorrow!"), 'info', 3200);
        return;
      }
      if (d.status !== 'success' || !d.target_color || !Array.isArray(d.target_color.rgb)) return;

      // Leave the round in progress cleanly, then serve the daily colour in
      // the normal game UI.
      if (telemetryAttempt) {
        await flushTelemetry({ finalize: true, endReason: 'abandoned' });
      }
      dailyMode = { slot_id: d.slot_id };
      matchRoundActive = false;
      currentTargetColor = d.target_color;
      setGameTarget(currentTargetColor);
      updateBox('targetColor', targetColor);
      resetMix();
      stopTimer();
      resetTimerDisplay();
      startTimer();
      enableColorMixing();
      setControlState('mixing');
      beginAttemptForCurrentTarget();
      if (dailyMode.slot_id && telemetryAttempt) {
        await bindProbeAttempt(dailyMode.slot_id, telemetryAttempt.attempt_uuid);
      }
      dailyAutoAttempted = true;
      window.__dailyPlaying = true;
      renderDailyStatusBadge(null);
      setDailyChip(true);
      showToast(t("📅 Today's challenge — one run, make it count!"), 'info', 3000);
    } catch (e) {
      console.error('Daily challenge start failed:', e);
    }
  }
  window.__startDailyChallenge = startDailyChallenge;

  async function maybeSubmitDailyRun(attemptUuid, deltaE, steps) {
    if (!dailyMode) return;
    const uid = getAuthenticatedUserId();
    dailyMode = null;
    window.__dailyPlaying = false;
    renderDailyStatusBadge(null);
    setDailyChip(false);
    if (!uid || !attemptUuid) return;
    try {
      const res = await fetch('/api/daily-challenge/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: uid,
          attempt_uuid: attemptUuid,
          score_primary: Number.isFinite(deltaE) ? deltaE : null,
          score_secondary: Number.isFinite(steps) ? steps : null,
          is_final: true,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (d.status === 'success') {
        renderDailyStatusBadge({
          challenge_date: (window.__dailyStatus || {}).challenge_date,
          submitted: true,
        });
        showToast(t('📅 Daily challenge submitted — see you tomorrow!'), 'success', 3200);
        loadAndRenderProgress().catch(() => {}); // refresh the CTA (daily done)
      }
    } catch (e) { /* run stays playable tomorrow; never block the game */ }
  }

  async function goToNextShade() {
    sessionShadesCompleted += 1;
    // Any transition ends the previous round's daily state (a submitted run
    // already cleared it; an unfinished one stays reachable via the badge).
    dailyMode = null;
    challengeMode = null;
    if (window.__dailyPlaying) {
      window.__dailyPlaying = false;
      renderDailyStatusBadge(null);
    }
    setDailyChip(false);

    // Serving priority: colour of the day (once per session, until
    // submitted) → the match's current round. Daily rounds do not consume
    // a match round.
    const daily = await maybeServeDaily();
    if (daily) {
      dailyMode = { slot_id: daily.slot_id };
      matchRoundActive = false;
      currentTargetColor = daily.target_color;
    } else {
      // Match just completed? Show the summary, then a fresh match begins.
      if (matchState && matchState.status === 'completed') {
        const summary = pendingMatchSummary
          || await fetchMatchSummary(matchState.match_id);
        pendingMatchSummary = null;
        stopTimer();
        disableColorMixing();
        await showMatchSummaryModal(summary);
        matchState = null; // the server will draw the next match below
        loadAndRenderProgress().catch(() => {});
      }
      if (!currentMatchRound()) {
        await refreshMatchFromServer();
      }
      if (!serveMatchRoundTarget()) {
        sessionShadesCompleted -= 1;
        alert(t('Could not load your match. Check your connection and try again.'));
        return;
      }
    }
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    stopTimer();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateMatchProgressUI();
    beginAttemptForCurrentTarget();
    if (dailyMode && dailyMode.slot_id && telemetryAttempt) {
      await bindProbeAttempt(dailyMode.slot_id, telemetryAttempt.attempt_uuid);
      window.__dailyPlaying = true;
      renderDailyStatusBadge(null);
      setDailyChip(true);
      showToast(t("📅 Today's challenge — mix the colour of the day!"), 'info', 2800);
    }

    maybeRhythmFeedback(sessionShadesCompleted);

    if (sessionShadesCompleted % 6 === 0) {
      loadAndRenderProgress().catch(() => {});
    }
  }

  function updateMatchProgressUI() {
    if (matchState && matchState.status === 'active') {
      updateProgressIndicator(matchState.current_round, matchState.round_count, sessionShadesCompleted);
    } else {
      updateProgressIndicator(0, 10, sessionShadesCompleted);
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
        : t('Could not load target colors (HTTP {status}). Check the server log.').replace('{status}', String(res.status));
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
      t('Could not load target colors. Ensure the database is reachable (DATABASE_URL / VPN / firewall), migrated (npm run db:migrate), and try again.'),
    );
    return;
  }

  // app_ready: catalog loaded, user context available, ready to play
  trackEvent('app_ready');

  function setGameTarget(color) {
    targetColor = color.rgb;
    window.shadeMatchTargetRgb = color.rgb;
  }

  // Logged-in players resume (or get) their persisted 10-round match; the
  // first round shows immediately as the pending target. Guests keep the
  // plain catalog (pickGuestTarget serves the demo round).
  if (getAuthenticatedUserId()) {
    await refreshMatchFromServer();
  }
  if (serveMatchRoundTarget()) {
    setGameTarget(currentTargetColor);
  } else {
    currentTargetColor = pickGuestTarget() || fullCatalog[0];
    setGameTarget(currentTargetColor);
  }
  updateMatchProgressUI();

  // ── Guest demo round ─────────────────────────────────────────────────────
  function pickGuestTarget() {
    // Prefer an easy shade (few recipe drops) so the demo is winnable fast.
    // Skin-zone targets are retired from serving (spectral version will bring
    // them back), so the demo draws from the even-coverage set only.
    const withRgb = fullCatalog.filter((c) => Array.isArray(c.rgb) && c.rgb.length === 3
      && c.classification !== 'even_gamut_v2_skin');
    const easy = withRgb.filter((c) => {
      const s = c.sum_drop_count;
      return Number.isFinite(s) && s >= 3 && s <= 7;
    });
    const pool = easy.length ? easy : withRgb;
    return pool[Math.floor(Math.random() * pool.length)] || null;
  }

  function buildGuestChallengeComparison(deltaE) {
    const ch = challengeMode;
    const mine = {
      delta_e: Number.isFinite(deltaE) ? deltaE : null,
      drops: Object.values(dropCounts).reduce((a, b) => a + (b | 0), 0),
      time_sec: getTimerSec(),
    };
    const theirs = { delta_e: ch.delta_e, drops: ch.drops, time_sec: ch.time_sec };
    return {
      code: ch.code,
      creator: ch.creator,
      creator_delta_e: ch.delta_e,
      creator_drops: ch.drops,
      creator_time_sec: ch.time_sec,
      your_delta_e: mine.delta_e,
      your_drops: mine.drops,
      your_time_sec: mine.time_sec,
      won: challengeBeats(mine, theirs),
    };
  }

  function recordGuestChallengeAcceptance(cmp) {
    // Best-effort: the creator's echo and the guest's later claim-on-register
    // both hang off this row; the local comparison shows regardless.
    const attemptUuid = generateUUID();
    fetch('/api/challenge/accept-guest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_code: cmp.code,
        attempt_uuid: attemptUuid,
        delta_e: cmp.your_delta_e,
        drops: cmp.your_drops,
        time_sec: cmp.your_time_sec,
      }),
    }).then((res) => {
      // Only a recorded attempt is claimable after registration.
      if (res.ok) rememberGuestChallengeClaim(attemptUuid, cmp.code);
    }).catch(() => {});
  }

  async function startGuestRound() {
    const tc = pickGuestTarget();
    if (!tc) return;
    window.__guestRoundDone = false;
    dailyMode = null;
    matchRoundActive = false;
    currentTargetColor = tc;
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    stopTimer();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    showToast(t('🎨 Tap the pigments below to match the colour on the left'), 'info', 3600);
  }
  window.__startGuestRound = startGuestRound;

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
        if (Number.isFinite(data.delta_e)) deltaJourney.push(data.delta_e);

        // Guest demo round: complete at a forgiving threshold, never persist.
        if (isGuest()) {
          if (!window.__guestRoundDone
              && Number.isFinite(data.delta_e) && data.delta_e <= GUEST_GOOD_DELTA_E) {
            window.__guestRoundDone = true;
            stopTimer();
            celebratePerfectMatch();
            setControlState('completed');
            if (challengeMode) {
              const cmp = buildGuestChallengeComparison(data.delta_e);
              recordGuestChallengeAcceptance(cmp);
              showChallengeComparison(cmp, { delayMs: 1500 });
              challengeMode = null;
            } else {
              showGuestResult(data.delta_e, { delayMs: 1500 });
            }
          }
          return;
        }

        if (isPerfectMatch(data.delta_e) && !currentSessionSaved) {
          stopTimer();
          celebratePerfectMatch();
          const session = {
            attempt_uuid: telemetryAttempt?.attempt_uuid || generateUUID(),
            user_id: window.currentUserId,
            target: targetColor,
            target_color_id: currentTargetColor.id,
            challenge_code: challengeMode ? challengeMode.code : null,
            ...matchSaveFields(),
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
          const stepsForDaily = telemetryAttempt?.decisionStepIndex ?? null;
          const wasDailyRound = !!dailyMode; // captured before submit clears it
          await flushTelemetry({
            finalize: true,
            endReason: 'saved_match',
            terminalBoundaryType: 'boundary_save',
          });
          window.__lastSavedAttemptUuid = session.attempt_uuid;
          challengeMode = null;
          await saveSessionToServer(session);
          await maybeSubmitDailyRun(session.attempt_uuid, data.delta_e, stepsForDaily);
          setControlState('completed');
          shareCard.offer({
            kind: wasDailyRound ? 'daily' : 'perfect',
            targetRgb: [...session.target],
            mixedRgb: [...session.mixed_rgb],
            deltaE: data.delta_e,
            drops: Object.values(session.drops).reduce((a, b) => a + b, 0),
            timeSec: session.time,
            attemptUuid: session.attempt_uuid,
          });
        }
      });

    return { stepId, mixedRGB: [...currentMixedRgb], deltaEResolvedAtEmit: null };
  }

  // ── Button handlers ───────────────────────────────────────────────────
  document.getElementById('startBtn').addEventListener('click', async () => {
    if (window.__challenge && !challengeMode && !window.__challengeStarted) {
      // Arriving via a challenge link: the first Start plays the challenge.
      window.__challengeStarted = true;
      await startChallengeRound();
      return;
    }
    if (isGuest()) { await startGuestRound(); return; }
    dailyMode = null;
    challengeMode = null;
    if (window.__dailyPlaying) { window.__dailyPlaying = false; renderDailyStatusBadge(null); }
    setDailyChip(false);
    if (telemetryAttempt) {
      await flushTelemetry({
        finalize: true,
        endReason: 'abandoned',
      });
    }

    refreshDatabaseConnection();
    sessionShadesCompleted = 0;
    // The first round of the session is the colour of the day (when today's
    // run is still open); the match's current round stays next in line.
    const dailyFirst = await maybeServeDaily();
    if (dailyFirst) {
      dailyMode = { slot_id: dailyFirst.slot_id };
      matchRoundActive = false;
      currentTargetColor = dailyFirst.target_color;
    } else {
      await refreshMatchFromServer();
      if (!serveMatchRoundTarget()) {
        alert(t('Could not load your match. Check your connection and try again.'));
        return;
      }
    }
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateMatchProgressUI();
    beginAttemptForCurrentTarget();
    if (dailyMode && dailyMode.slot_id && telemetryAttempt) {
      await bindProbeAttempt(dailyMode.slot_id, telemetryAttempt.attempt_uuid);
      window.__dailyPlaying = true;
      renderDailyStatusBadge(null);
      showToast(t("📅 Today's challenge — mix the colour of the day!"), 'info', 2800);
    }
    setDailyChip(!!dailyMode);
  });

  document.getElementById('skipBtn').addEventListener('click', async () => {
    if (isGuest()) {
      // Guest: no perception modal, no save — either move on after a
      // completed demo, or end the demo and show the conversion card.
      if (window.__guestRoundDone) { await startGuestRound(); return; }
      stopTimer();
      window.__guestRoundDone = true;
      const de = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
      if (challengeMode) {
        const cmp = buildGuestChallengeComparison(de);
        // A give-up after real mixing is still an acceptance the creator can
        // hear about (and the guest can later claim); an untouched palette
        // producing NaN is not.
        if (Number.isFinite(de)) recordGuestChallengeAcceptance(cmp);
        showChallengeComparison(cmp);
        challengeMode = null;
      } else {
        showGuestResult(de);
      }
      return;
    }
    refreshDatabaseConnection();
    const currentDeltaE = Number.isFinite(window.lastMixDeltaE) ? window.lastMixDeltaE : NaN;
    const alreadyCompletedThisColor = window.currentSessionSaved === true;

    const shouldSaveSkip = Number.isFinite(currentDeltaE) && !isPerfectMatch(currentDeltaE)
      && !alreadyCompletedThisColor && !skipSavedThisColor;

    if (shouldSaveSkip) {
      const skipPerception = await showSkipPerceptionModal();
      if (!skipPerception) return;
      const wasDailyRound = !!dailyMode; // captured before save clears daily state
      const skipData = {
        attempt_uuid: telemetryAttempt?.attempt_uuid || generateUUID(),
        user_id: window.currentUserId,
        target_color_id: currentTargetColor.id,
        challenge_code: challengeMode ? challengeMode.code : null,
        ...matchSaveFields(),
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
      const stepsForDaily = telemetryAttempt?.decisionStepIndex ?? null;
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
          alert(t('Failed to save skip data. Please try again.'));
          return;
        }
        window.__lastSavedAttemptUuid = skipData.attempt_uuid;
        skipSavedThisColor = true;
        challengeMode = null;
        handleProgressionResponse(data);
        await maybeSubmitDailyRun(skipData.attempt_uuid, skipData.delta_e, stepsForDaily);
      } catch {
        alert(t('Error saving skip data. Please check your connection and try again.'));
        return;
      }

      // A close-enough give-up is worth bragging about: after a long mix that
      // the player judged identical or acceptable, hold on the result and
      // surface the share card instead of jumping straight to the next colour.
      if (skipPerception === 'identical' || skipPerception === 'acceptable') {
        stopTimer();
        setControlState('completed');
        const totalSkipDrops = skipData.drop_white + skipData.drop_black
          + skipData.drop_red + skipData.drop_yellow + skipData.drop_blue;
        shareCard.offer({
          kind: wasDailyRound ? 'daily' : 'perfect',
          targetRgb: [skipData.target_r, skipData.target_g, skipData.target_b],
          mixedRgb: [skipData.mixed_r, skipData.mixed_g, skipData.mixed_b],
          deltaE: skipData.delta_e,
          drops: totalSkipDrops,
          timeSec: skipData.time_sec,
          attemptUuid: skipData.attempt_uuid,
        });
        return;
      }
    } else if (matchRoundActive && matchState && matchState.status === 'active'
               && matchState.current_round === servedMatchRoundIndex) {
      // Nothing was mixed (or this colour was already resolved by an earlier
      // save): tell the server to advance past the round so the client and
      // the persisted match never drift apart.
      const uid = getAuthenticatedUserId();
      try {
        const res = await fetch('/api/match/skip-round', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: uid,
            match_id: matchState.match_id,
            round_index: servedMatchRoundIndex,
          }),
        });
        const d = await res.json().catch(() => ({}));
        if (d.status === 'success' && d.match) {
          applyMatchUpdate(d.match);
          maybeMatchChainToast(d.match);
        }
      } catch { /* the next /api/match/current resyncs */ }
    }

    await goToNextShade();
  });

  document.getElementById('restartBtn').addEventListener('click', async () => {
    if (isGuest()) { await startGuestRound(); return; }
    dailyMode = null;
    challengeMode = null;
    if (window.__dailyPlaying) { window.__dailyPlaying = false; renderDailyStatusBadge(null); }
    setDailyChip(false);
    await flushTelemetry({
      finalize: true,
      endReason: 'restart',
      terminalBoundaryType: 'boundary_restart',
    });

    refreshDatabaseConnection();
    sessionShadesCompleted = 0;
    // Restart resumes the SAME persisted match (its current round), it does
    // not redraw — abandoning progress would defeat the 10-round structure.
    await refreshMatchFromServer();
    if (!serveMatchRoundTarget()) {
      alert(t('Could not load your match. Check your connection and try again.'));
      return;
    }
    setGameTarget(currentTargetColor);
    updateBox('targetColor', targetColor);
    resetMix();
    resetTimerDisplay();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    updateMatchProgressUI();
    document.getElementById('overflowDropdown').classList.remove('is-open');
    beginAttemptForCurrentTarget();
  });

  document.getElementById('retryBtn').addEventListener('click', async () => {
    if (isGuest()) {
      // Guest: just clear the mix and restart the clock on the same target.
      window.__guestRoundDone = false;
      resetMix();
      resetTimerDisplay();
      stopTimer();
      startTimer();
      enableColorMixing();
      setControlState('mixing');
      return;
    }
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
      const savePromise = saveSessionToServer(session);
      // In daily mode the save must land before the slot is rebound below,
      // otherwise the reset-save could re-claim the freshly cleared slot.
      if (dailyMode) await savePromise;
    }
    resetMix();
    resetTimerDisplay();
    stopTimer();
    startTimer();
    enableColorMixing();
    setControlState('mixing');
    beginAttemptForCurrentTarget();
    if (dailyMode) {
      try {
        const res = await fetch('/api/daily-challenge/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: getAuthenticatedUserId() }),
        });
        const d = await res.json().catch(() => ({}));
        if (d.status === 'success' && d.slot_id && telemetryAttempt) {
          dailyMode.slot_id = d.slot_id;
          await bindProbeAttempt(d.slot_id, telemetryAttempt.attempt_uuid);
        }
      } catch (e) { /* the save-time colour fallback still applies */ }
    }
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
      try { sfx.drop(dropCounts[color]); } catch { /* audio is best-effort */ }

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
        if (navigator.vibrate) navigator.vibrate(10);
        try { sfx.remove(); } catch { /* audio is best-effort */ }
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

  // Head-to-head challenge landing (/c/<code>): show the banner last, once
  // the board is ready.
  renderChallengeBanner();
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
          const finishLogin = function () {
            localStorage.removeItem('pendingVerifyUserId');
            localStorage.setItem('userId', userId);
            localStorage.setItem('userBirthdate', data.birthdate);
            localStorage.setItem('userGender', data.gender);
            if (data.nickname) localStorage.setItem('userNickname', data.nickname);
            else localStorage.removeItem('userNickname');
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
          };

          // Back-fill: a participant who registered before consent capture
          // existed (consent_recorded === false) must agree before playing.
          if (data.consent_recorded === false && typeof window.showResearchConsentModal === 'function') {
            document.getElementById('userModal').style.display = 'none';
            window.showResearchConsentModal(async function () {
              const consent = (window.getStoredResearchConsent && window.getStoredResearchConsent()) || {};
              try {
                await fetch('/research-consent', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    user_id: userId,
                    consent_version: consent.version || '',
                    consent_agreed_at: consent.agreedAt || null,
                  }),
                });
              } catch {}
              finishLogin();
            });
          } else {
            finishLogin();
          }
        } else if (data.code === 'EMAIL_NOT_VERIFIED') {
          const resend = confirm(t('Your email is not verified yet. Resend verification email now?'));
          if (resend) {
            try {
              const resendResp = await fetch('/email/verification/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId }),
              });
              const resendData = await resendResp.json();
              if (resendData.status === 'success') {
                alert(t('Verification email sent. Please verify first, then log in.'));
              } else {
                alert(resendData.message || t('Could not send verification email.'));
              }
            } catch {
              alert(t('Could not send verification email.'));
            }
          }
        } else {
          alert(data.message || t('Invalid user ID. Please try again.'));
        }
      } catch {
        alert(t('Invalid user ID. Please try again.'));
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
    _showPwaInstallCtaWhenClear();
  }
});

window.addEventListener('appinstalled', () => {
  _hidePwaInstallCta();
  localStorage.setItem('pwaDismissed', '1');
  _deferredInstallPrompt = null;
});

function _showPwaInstallCta(opts) {
  const isIos = !!(opts && opts.ios);
  setCta('pwa', {
    icon: '📲',
    labelHtml: t('Install ShadeMatch'),
    reasonHtml: isIos
      ? t('Quick access from your home screen. Tap <strong>Share</strong> → <strong>Add to Home Screen</strong>.')
      : t('Quick access from your home screen.'),
    actionLabel: isIos ? null : t('Install'),
    onAction: isIos ? null : window.triggerPwaInstall,
    onDismiss: window.dismissPwaInstall,
    variant: 'pwa',
  });
}

// The install card is the LAST voice in the first-visit sequence: it stays
// away while the cookie banner, consent/registration modals, or the spotlight
// walkthrough (running or scheduled — window.__guidePending) hold the screen.
function _firstVisitOverlayActive() {
  if (window.__guidePending) return true;
  if (window.SpotlightGuide && SpotlightGuide.isActive && SpotlightGuide.isActive()) return true;
  if (document.querySelector('.cookie-consent-banner')) return true;
  return ['userModal', 'researchConsentModal'].some((id) => {
    const m = document.getElementById(id);
    return m && getComputedStyle(m).display !== 'none';
  });
}

function _showPwaInstallCtaWhenClear(opts) {
  if (localStorage.getItem('pwaDismissed')) return;
  if (!_firstVisitOverlayActive()) { _showPwaInstallCta(opts); return; }
  setTimeout(() => _showPwaInstallCtaWhenClear(opts), 1000);
}

function _hidePwaInstallCta() {
  setCta('pwa', null);
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
    _showPwaInstallCtaWhenClear({ ios: true });
  }
});

// ── Push notification opt-in ──────────────────────────────────────────────
window.requestPushPermission = async function () {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    showToast(t('Push notifications are not supported in this browser.'), 'info', 4000);
    return;
  }

  const userId = window.currentUserId || localStorage.getItem('userId');
  if (!userId) {
    showToast(t('Please log in before enabling reminders.'), 'info', 4000);
    return;
  }

  try {
    const permission = await Notification.requestPermission();
    if (permission === 'denied') {
      showToast(t('Notifications blocked. Enable them in browser settings.'), 'info', 5000);
      return;
    }
    if (permission !== 'granted') {
      return;
    }

    const keyRes = await fetch('/push/vapid-public-key');
    const keyData = await keyRes.json();
    const vapidKey = keyData.vapid_public_key;
    if (!vapidKey) {
      showToast(t('Push notifications not configured on this server.'), 'info', 4000);
      setCta('push', null);
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
      showToast(t('🔔 Daily reminders enabled!'), 'award', 4000);
      setCta('push', null);
      localStorage.setItem('pushSubscribed', '1');
    } else {
      throw new Error(data.message || 'Subscribe failed');
    }
  } catch (err) {
    console.error('Push subscribe error:', err);
    showToast(t('Could not enable reminders. Try again later.'), 'info', 4000);
  }
};

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return new Uint8Array([...rawData].map(c => c.charCodeAt(0)));
}

// ── Guest mode ────────────────────────────────────────────────────────────
// A brand-new visitor can play one fully client-side demo round before any
// consent or registration. Nothing is persisted: telemetry never starts for
// unauthenticated users, the save path is skipped, and only the stateless
// /calculate endpoint is called for scoring.
function isGuest() {
  return !getAuthenticatedUserId();
}

// Demo rounds complete at a forgiving threshold so the first experience is a
// success moment, not a grind toward delta-E 0.01.
const GUEST_GOOD_DELTA_E = 2.0;

function showGuestResult(deltaE, { delayMs = 0 } = {}) {
  const modal = document.getElementById('guestResultModal');
  if (!modal) return;
  const tEl = document.getElementById('guestResultTarget');
  const m = document.getElementById('guestResultMix');
  if (tEl && Array.isArray(window.shadeMatchTargetRgb)) {
    tEl.style.backgroundColor = `rgb(${window.shadeMatchTargetRgb.join(',')})`;
  }
  if (m) m.style.backgroundColor = `rgb(${currentMixedRgb.join(',')})`;
  const de = document.getElementById('guestResultDeltaE');
  if (de) de.textContent = Number.isFinite(deltaE) ? deltaE.toFixed(2) : '—';
  const timeEl = document.getElementById('guestResultTime');
  if (timeEl) timeEl.textContent = t('{n}s').replace('{n}', getTimerSec().toFixed(1));
  // Stash the payload for the modal's Share button (wired in index.html).
  window.__lastGuestResult = {
    kind: 'perfect',
    targetRgb: Array.isArray(window.shadeMatchTargetRgb) ? [...window.shadeMatchTargetRgb] : [255, 255, 255],
    mixedRgb: [...currentMixedRgb],
    deltaE: Number.isFinite(deltaE) ? deltaE : null,
    drops: Object.values(window.shadeMatchDropCounts || {}).reduce((a, b) => a + (b | 0), 0),
    timeSec: getTimerSec(),
  };
  setTimeout(() => { modal.style.display = 'flex'; }, delayMs);
}

// ── HTML escaping (for user-chosen strings rendered via innerHTML) ─────────
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── Head-to-head challenges ───────────────────────────────────────────────
// Result ordering mirrors the server: accuracy first (2-dp ΔE so perfect
// finishes tie), then fewer drops, then faster time.
function challengeScoreKey(de, drops, t) {
  return [
    Number.isFinite(de) ? Math.round(de * 100) / 100 : Infinity,
    Number.isFinite(drops) ? drops : Infinity,
    Number.isFinite(t) ? t : Infinity,
  ];
}

function challengeBeats(mine, theirs) {
  const a = challengeScoreKey(mine.delta_e, mine.drops, mine.time_sec);
  const b = challengeScoreKey(theirs.delta_e, theirs.drops, theirs.time_sec);
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return true;
    if (a[i] > b[i]) return false;
  }
  return false;
}

// Journey access for share-card.js (text glyphs + canvas squares).
window.shadeMatchJourneyGlyphs = journeyGlyphs;
window.shadeMatchDeltaJourney = function () { return deltaJourney.slice(); };

// Create a counter-challenge from a saved round and hand the link to the
// share sheet (clipboard on desktop). Exposed for share-card.js too.
window.shadeMatchCreateChallenge = async function (attemptUuid, { text } = {}) {
  const uid = getAuthenticatedUserId();
  if (!uid || !attemptUuid) return false;
  try {
    const res = await fetch('/api/challenge/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid, attempt_uuid: attemptUuid }),
    });
    const d = await res.json();
    if (d.status !== 'success' || !d.url) {
      showToast(d.message || t('Could not create the challenge link.'), 'info', 4000);
      return false;
    }
    // Carry the sharer's language on the link: the recipient lands localized
    // and messenger scrapers fetch the localized OG preview.
    let url = d.url;
    if (window.LANG && window.LANG !== 'en') {
      url += (url.includes('?') ? '&' : '?') + 'lang=' + window.LANG;
    }
    // No ΔE-journey squares here: on a challenge they hint at how the creator
    // solved it, so the shared message stays just the tagline + link.
    const msg = (text || t('⚔️ Beat my ShadeMatch result:')) + '\n' + url;
    if (navigator.share) {
      try { await navigator.share({ text: msg }); return true; } catch (e) {
        if (e && e.name === 'AbortError') return false;
      }
    }
    try {
      await navigator.clipboard.writeText(msg);
      showToast(t('🔗 Challenge link copied — send it to a friend!'), 'award', 4200);
    } catch { /* clipboard may be blocked */ }
    return true;
  } catch {
    showToast(t('Could not create the challenge link.'), 'info', 4000);
    return false;
  }
};

function showChallengeComparison(c, { delayMs = 0 } = {}) {
  const modal = document.getElementById('challengeResultModal');
  if (!modal || !c) return;
  const verdict = document.getElementById('challengeVerdict');
  if (verdict) {
    verdict.textContent = c.won
      ? t('🏆 You beat {name}!').replace('{name}', String(c.creator))
      : t('{name} holds it — rematch?').replace('{name}', String(c.creator));
  }
  const tEl = document.getElementById('challengeResultTarget');
  const m = document.getElementById('challengeResultMix');
  if (tEl && Array.isArray(window.shadeMatchTargetRgb)) {
    tEl.style.backgroundColor = `rgb(${window.shadeMatchTargetRgb.join(',')})`;
  }
  if (m) m.style.backgroundColor = `rgb(${currentMixedRgb.join(',')})`;

  const fmt = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '—');
  const row = (label, mine, theirs) =>
    `<div style="display:flex;justify-content:space-between;font-size:0.9rem;padding:4px 0;border-bottom:1px solid var(--border);">` +
    `<span style="color:var(--text-secondary);">${label}</span>` +
    `<span><strong>${mine}</strong> vs ${theirs}</span></div>`;
  const body = document.getElementById('challengeCompareBody');
  if (body) {
    body.innerHTML =
      row(t('Match error ΔE'), fmt(c.your_delta_e), fmt(c.creator_delta_e)) +
      row(t('Drops'), Number.isFinite(c.your_drops) ? c.your_drops : '—',
        Number.isFinite(c.creator_drops) ? c.creator_drops : '—') +
      row(t('Time'), t('{n}s').replace('{n}', fmt(c.your_time_sec, 1)), t('{n}s').replace('{n}', fmt(c.creator_time_sec, 1))) +
      `<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:6px;">` +
      `${t('you vs {name} — accuracy decides, then drops, then time').replace('{name}', escapeHtml(c.creator))}</div>` +
      (isGuest()
        ? `<div style="margin-top:10px;padding:10px 12px;border:1px dashed var(--border);` +
          `border-radius:10px;font-size:0.8rem;color:var(--text-secondary);">` +
          `${t('Guest result — leave now and it is lost to you. Register and it stays yours, in your challenge history.')}</div>`
        : '');
  }

  const rematch = document.getElementById('challengeRematchBtn');
  if (rematch) {
    // A winner wants to gloat, not replay; a loser wants revenge. Same door
    // (for guests: registration), different handle.
    rematch.textContent = c.won
      ? t('⚔️ Send {name} one back').replace('{name}', String(c.creator))
      : t('Rematch — beat {name}').replace('{name}', String(c.creator));
    rematch.onclick = async () => {
      modal.style.display = 'none';
      if (isGuest()) {
        // Conversion moment: registering is what unlocks challenging back.
        if (window.__openRegistrationFlow) window.__openRegistrationFlow();
        return;
      }
      const uuid = window.__lastSavedAttemptUuid;
      if (!uuid) {
        showToast(t('Finish a colour first, then challenge back.'), 'info', 3500);
        return;
      }
      await window.shadeMatchCreateChallenge(uuid, {
        text: t('⚔️ I took your ShadeMatch challenge — now beat mine:'),
      });
    };
  }
  const closeBtn = document.getElementById('challengeCloseBtn');
  if (closeBtn) closeBtn.onclick = () => { modal.style.display = 'none'; };
  setTimeout(() => { modal.style.display = 'flex'; }, delayMs);
}

// ── Sensory feedback ──────────────────────────────────────────────────────
function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// Full celebration for a perfect match: chime + haptic + confetti + overlay.
function celebratePerfectMatch() {
  try { sfx.perfect(); } catch { /* audio is best-effort */ }
  if (navigator.vibrate) navigator.vibrate([30, 50, 30]);

  const overlay = document.createElement('div');
  overlay.className = 'perfect-overlay';
  overlay.innerHTML = '<div class="perfect-overlay-inner">🎯<span>' + t('Perfect match!') + '</span></div>';
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-visible'));
  setTimeout(() => {
    overlay.classList.remove('is-visible');
    setTimeout(() => overlay.remove(), 400);
  }, 1400);

  if (!prefersReducedMotion()) createConfetti();
}

// ── Confetti ──────────────────────────────────────────────────────────────
function createConfetti() {
  const colors = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff9ff3', '#54a0ff', '#5f27cd'];
  for (let i = 0; i < 90; i++) {
    setTimeout(() => createConfettiPiece(colors), i * 15);
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
