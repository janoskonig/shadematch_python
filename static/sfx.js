// sfx.js — synthesized game sounds (Web Audio, no assets) + mute toggle.
//
// All sounds are generated with oscillators so nothing is downloaded. The
// AudioContext is created lazily on the first call, which always happens
// inside a user gesture (palette tap / save), satisfying autoplay policies.

const MUTE_KEY = 'sfxMuted';

let _ctx = null;
let _master = null;

function ctx() {
  if (_ctx) {
    if (_ctx.state === 'suspended') _ctx.resume().catch(() => {});
    return _ctx;
  }
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  _ctx = new AC();
  _master = _ctx.createGain();
  _master.gain.value = 0.16; // keep everything quiet — feedback, not fanfare
  _master.connect(_ctx.destination);
  return _ctx;
}

function isMuted() {
  return localStorage.getItem(MUTE_KEY) === '1';
}

// One enveloped oscillator note. freqFrom→freqTo glide over `glide` seconds,
// gain decays exponentially over `dur` seconds, starting at `when` (ctx time).
function note({ type = 'sine', freqFrom, freqTo = null, glide = 0.06, dur = 0.15, gain = 1, when = 0 }) {
  const c = ctx();
  if (!c) return;
  const t0 = c.currentTime + when;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freqFrom, t0);
  if (freqTo && freqTo !== freqFrom) {
    osc.frequency.exponentialRampToValueAtTime(freqTo, t0 + glide);
  }
  g.gain.setValueAtTime(gain, t0);
  g.gain.exponentialRampToValueAtTime(0.001, t0 + dur);
  osc.connect(g);
  g.connect(_master);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

export const sfx = {
  // Adding a drop: a rising "bloop". Pitch creeps up with the count of that
  // pigment so repeated taps feel like filling a vessel.
  drop(count = 1) {
    if (isMuted()) return;
    const base = 200 + Math.min(count, 24) * 14;
    note({ type: 'sine', freqFrom: base, freqTo: base * 1.55, glide: 0.07, dur: 0.14, gain: 1 });
    note({ type: 'triangle', freqFrom: base * 2, freqTo: base * 2.4, glide: 0.05, dur: 0.07, gain: 0.25 });
  },

  // Removing a drop: falling pitch, quieter.
  remove() {
    if (isMuted()) return;
    note({ type: 'sine', freqFrom: 320, freqTo: 180, glide: 0.08, dur: 0.12, gain: 0.6 });
  },

  // Perfect match: a short major arpeggio (C5 E5 G5 C6).
  perfect() {
    if (isMuted()) return;
    const freqs = [523.25, 659.25, 783.99, 1046.5];
    freqs.forEach((f, i) => {
      note({ type: 'triangle', freqFrom: f, dur: 0.34, gain: 0.8, when: i * 0.09 });
    });
    note({ type: 'sine', freqFrom: 261.63, dur: 0.6, gain: 0.5, when: 0 });
  },

  // Small positive tick (missions, awards).
  tick() {
    if (isMuted()) return;
    note({ type: 'sine', freqFrom: 880, freqTo: 1174.66, glide: 0.04, dur: 0.1, gain: 0.5 });
  },

  muted: isMuted,

  toggleMuted() {
    const next = !isMuted();
    localStorage.setItem(MUTE_KEY, next ? '1' : '0');
    return next;
  },
};

// Wire the mute toggles if the page has any (header button and/or the
// mobile overflow-menu row — one render keeps them in sync).
document.addEventListener('DOMContentLoaded', () => {
  const btns = document.querySelectorAll('#sfxToggleBtn, #sfxToggleRow');
  if (!btns.length) return;
  const render = () => {
    const m = isMuted();
    btns.forEach((btn) => {
      btn.querySelector('.sfx-icon').textContent = m ? '🔇' : '🔊';
      btn.setAttribute('aria-label', m ? 'Unmute sounds' : 'Mute sounds');
      btn.title = m ? 'Sounds off — tap to unmute' : 'Sounds on — tap to mute';
    });
  };
  btns.forEach((btn) => btn.addEventListener('click', () => {
    const nowMuted = sfx.toggleMuted();
    if (!nowMuted) sfx.tick(); // audible confirmation only when turning ON
    render();
  }));
  render();
});
