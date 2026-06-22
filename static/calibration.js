/**
 * Calibration game (/calibration) — perceptibility/acceptability threshold probe.
 *
 * Presents server-generated colour pairs at controlled ΔE₀₀ (the ΔE is NEVER sent to the
 * client, so it can't leak into the UI) and records a three-way appearance judgment per pair.
 * Standalone: shares nothing with the main game loop, so it can be piloted on power users.
 *
 * Flow: POST /calibration/start → run trials (judgment + reaction time per pair, posted to
 * /calibration/respond) → POST /calibration/finish → show the session summary.
 *
 * Methodological guardrails (see the design discussion):
 *   - no per-trial feedback (a "right answer" would train the criterion and move the threshold)
 *   - no ΔE shown anywhere during play
 *   - left/right side of each pair randomised so position isn't a cue
 *   - catch trials (handled server-side) gate session quality
 */
(function () {
  'use strict';

  const el = (id) => document.getElementById(id);
  const gate = el('calGate');
  const intro = el('calIntro');
  const runner = el('calRunner');
  const result = el('calResult');
  const patchA = el('calPatchA');
  const patchB = el('calPatchB');
  const progressFill = el('calProgressFill');
  const countLine = el('calCount');
  const prompt = el('calPrompt');
  const btnYes = el('calBtnYes');
  const btnNo = el('calBtnNo');

  let session = null;     // { session_id, trials: [{trial_id, a, b}] }
  let idx = 0;            // current trial index
  let stage = 'identical'; // 'identical' (Q1 perceptibility) → 'acceptable' (Q2 acceptability)
  let shownAt = 0;        // performance.now() when the current pair was rendered
  let locked = false;     // guard against double-answering while advancing

  function userId() {
    try { return (window.currentUserId || localStorage.getItem('userId') || '').trim().toUpperCase() || null; }
    catch (_) { return null; }
  }

  // Passive environment snapshot — display gamut + viewport matter for interpreting thresholds.
  function envSnapshot() {
    const gamut = (window.matchMedia && matchMedia('(color-gamut: p3)').matches) ? 'p3'
      : (window.matchMedia && matchMedia('(color-gamut: srgb)').matches) ? 'srgb' : 'unknown';
    let tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) {}
    return {
      screen_w: screen.width, screen_h: screen.height,
      viewport_w: window.innerWidth, viewport_h: window.innerHeight,
      dpr: window.devicePixelRatio || 1,
      color_gamut: gamut,
      locale: navigator.language || '', timezone: tz,
      ua: navigator.userAgent || '',
    };
  }

  const rgb = (c) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;

  // Set the prompt + button labels for the current question stage.
  function renderStage() {
    if (stage === 'identical') {
      prompt.textContent = 'Are they identical?';
      btnYes.innerHTML = '<span class="cal-kbd">1</span>Yes — identical';
      btnNo.innerHTML = '<span class="cal-kbd">2</span>No — different';
    } else {
      prompt.textContent = 'Would this difference be acceptable on your face?';
      btnYes.innerHTML = '<span class="cal-kbd">1</span>Yes — acceptable';
      btnNo.innerHTML = '<span class="cal-kbd">2</span>No — too different';
    }
  }

  function renderTrial() {
    const t = session.trials[idx];
    // Randomise which colour sits on the left so side never cues the answer.
    const swap = Math.random() < 0.5;
    patchA.style.backgroundColor = rgb(swap ? t.b : t.a);
    patchB.style.backgroundColor = rgb(swap ? t.a : t.b);
    const n = session.trials.length;
    progressFill.style.width = (100 * idx / n).toFixed(1) + '%';
    countLine.textContent = `Pair ${idx + 1} of ${n}`;
    stage = 'identical';     // every pair starts with the perceptibility question
    renderStage();
    locked = false;
    shownAt = performance.now();
  }

  // Two-stage elicitation: Q1 "are they identical?" (perceptibility); only if NOT identical,
  // Q2 "acceptable on your face?" (acceptability). An imperceptible pair is trivially
  // acceptable, so Q2 is skipped — that maps to the same identical/acceptable/unacceptable
  // categories the analysis expects.
  function respond(ans) {            // ans: 'yes' | 'no'
    if (locked || !session || idx >= session.trials.length) return;
    if (stage === 'identical') {
      if (ans === 'yes') commit('identical');
      else { stage = 'acceptable'; renderStage(); }   // same pair stays up for Q2
    } else {
      commit(ans === 'yes' ? 'acceptable' : 'unacceptable');
    }
  }

  function commit(judgment) {
    locked = true;
    const t = session.trials[idx];
    const reaction_ms = Math.round(performance.now() - shownAt);
    // Fire-and-forget: each response carries its own trial_id, so order doesn't matter.
    fetch('/calibration/respond', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: session.session_id, trial_id: t.trial_id, judgment, reaction_ms }),
    }).catch(() => { /* transient — the trial row just stays unjudged */ });

    idx += 1;
    if (idx >= session.trials.length) finish();
    else renderTrial();
  }

  function finish() {
    progressFill.style.width = '100%';
    fetch('/calibration/finish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: session.session_id }),
    })
      .then((r) => r.json())
      .then((data) => showResult(data && data.summary, data && data.progress))
      .catch(() => showResult(null, null));
  }

  const fmtDe = (v) => (v != null ? Number(v).toFixed(2) : '—');

  // "N of target" session dots.
  function renderDots(container, completed, target) {
    if (!container) return;
    const t = target || 5;
    const done = Math.min(completed || 0, t);
    let dots = '';
    for (let i = 0; i < t; i++) dots += `<span class="cal-dot${i < done ? ' done' : ''}"></span>`;
    const label = (completed >= t)
      ? `All ${t} sessions done — thank you! Extra runs sharpen it further.`
      : `Session ${Math.min(completed + 1, t)} of ${t}`;
    container.innerHTML = `<span class="cal-dots">${dots}</span><span class="lbl">${label}</span>`;
  }

  // Per-session perceptibility history as a compact bar row (the learning curve).
  function renderHistory(history) {
    const host = el('calHistory');
    const cap = el('calHistoryCap');
    if (!host) return;
    const pts = (history || []).filter((h) => h.pt != null);
    if (!pts.length) {
      host.innerHTML = '<span class="cal-history-empty">Your per-session history will build up here.</span>';
      if (cap) cap.textContent = '';
      return;
    }
    // Taller bar = sharper eyes (smaller ΔE), so invert against the worst.
    const maxDe = Math.max(...pts.map((h) => h.pt), 1.5);
    host.innerHTML = pts.map((h) => {
      const frac = Math.max(0.08, 1 - (h.pt / (maxDe * 1.1)));
      return `<div class="hbar${h.low_quality ? ' lowq' : ''}" style="height:${(frac * 100).toFixed(0)}%" title="ΔE ${h.pt}"></div>`;
    }).join('');
    if (cap) cap.textContent = 'Perceptibility per session (taller = finer discrimination). Amber = low-confidence run.';
  }

  function showResult(summary, progress) {
    runner.classList.add('cal-hidden');
    result.classList.remove('cal-hidden');

    const completed = (progress && progress.completed) || 0;
    const target = (progress && progress.target) || 5;
    const isFinal = completed >= target;
    renderDots(el('calResultProgress'), completed, target);

    // Attention-check QC is process quality, not a threshold, so it's safe to show every time.
    const q = el('calQuality');
    if (summary && summary.low_quality) {
      q.className = 'cal-quality warn';
      q.textContent = '⚠ Some attention-check pairs were missed, so this run is low-confidence. Take the next one when you can focus on each pair.';
    } else if (summary && summary.catch_pass_rate != null) {
      q.className = 'cal-quality';
      q.textContent = `Attention checks passed: ${Math.round(summary.catch_pass_rate * 100)}%.`;
    } else {
      q.className = 'cal-quality';
      q.textContent = '';
    }

    const interim = el('calInterim');
    const final = el('calFinal');
    if (isFinal) {
      // Reveal results only now that the protocol is done — pooled across all sessions.
      el('calResultTitle').textContent = 'Your calibration is complete';
      interim.classList.add('cal-hidden');
      final.classList.remove('cal-hidden');
      const pooled = progress && progress.pooled;
      el('calPoolPt').textContent = fmtDe(pooled && pooled.perceptibility_de);
      el('calPoolAt').textContent = fmtDe(pooled && pooled.acceptability_de);
      renderHistory(progress && progress.history);
      el('calAgainBtn').textContent = 'Run another';
    } else {
      // Sessions 1…N-1: progress only, no numbers (they'd bias the sessions still to come).
      el('calResultTitle').textContent = 'Session complete';
      final.classList.add('cal-hidden');
      interim.classList.remove('cal-hidden');
      const left = target - completed;
      el('calInterimMsg').textContent =
        `Saved — ${completed} of ${target} sessions done. ${left} to go before your results unlock. ` +
        `Come back for the next one, ideally on another day.`;
      el('calAgainBtn').textContent = 'Next session';
    }
  }

  function start() {
    el('calStartBtn').disabled = true;
    fetch('/calibration/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId(), env: envSnapshot() }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, status: r.status, data })))
      .then(({ ok, status, data }) => {
        if (status === 403 || (data && data.error === 'registration_required')) {
          showGate();   // ID was cleared / not registered after all
          return;
        }
        if (!ok || !data || data.error || !Array.isArray(data.trials) || !data.trials.length) {
          throw new Error((data && data.error) || 'no trials');
        }
        session = data; idx = 0;
        intro.classList.add('cal-hidden');
        result.classList.add('cal-hidden');
        runner.classList.remove('cal-hidden');
        renderTrial();
      })
      .catch(() => {
        el('calStartBtn').disabled = false;
        const p = intro.querySelector('p');
        if (p) p.textContent = 'Could not start a calibration session — please reload and try again.';
      });
  }

  function showGate() {
    intro.classList.add('cal-hidden');
    runner.classList.add('cal-hidden');
    result.classList.add('cal-hidden');
    gate.classList.remove('cal-hidden');
  }

  function showIntro() {
    gate.classList.add('cal-hidden');
    intro.classList.remove('cal-hidden');
    const uid = userId();
    if (uid) el('calUserLine').textContent = `Signed in as ${uid}.`;
    // Load standing so the player sees how far along they are before starting.
    fetch('/calibration/progress?user_id=' + encodeURIComponent(uid))
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => { if (p && !p.error) renderDots(el('calIntroProgress'), p.completed, p.target); })
      .catch(() => { /* progress is best-effort */ });
  }

  // Wire controls.
  el('calStartBtn').addEventListener('click', start);
  el('calAgainBtn').addEventListener('click', () => { el('calStartBtn').disabled = false; start(); });
  runner.querySelectorAll('[data-ans]').forEach((b) =>
    b.addEventListener('click', () => respond(b.dataset.ans)));
  document.addEventListener('keydown', (e) => {
    if (runner.classList.contains('cal-hidden')) return;
    const k = (e.key || '').toLowerCase();
    if (k === '1' || k === 'y') { e.preventDefault(); respond('yes'); }
    else if (k === '2' || k === 'n') { e.preventDefault(); respond('no'); }
  });

  // Registered users only: no stored ID → show the gate, never the game.
  if (userId()) showIntro(); else showGate();
})();
