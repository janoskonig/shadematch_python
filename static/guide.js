/**
 * SpotlightGuide — a lightweight coach-mark / spotlight walkthrough.
 *
 * Dims the page and cuts a "spotlight" around one element at a time, with a tooltip
 * card (title, body, progress dots, Back / Next / Skip). Steps that don't resolve to a
 * visible element are skipped automatically, so it's safe to list elements that may be
 * hidden depending on state.
 *
 *   SpotlightGuide.start([
 *     { el: '#target', title: 'Match this', body: '…' },
 *     { el: () => document.querySelector('.foo'), title: '…', body: '…' },
 *   ], { onDone: fn });
 *
 * Keyboard: →/Enter next, ← back, Esc skip. Repositions on scroll/resize.
 */
(function (global) {
  'use strict';

  let state = null;

  function el(tag, cls) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  // Resolve a step's element if it's present and visible, else null.
  function resolve(step) {
    const node = (typeof step.el === 'function') ? step.el() : document.querySelector(step.el);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) return null;
    const cs = getComputedStyle(node);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return null;
    return node;
  }

  function hasResolvable(from, dir) {
    let i = from + dir;
    while (i >= 0 && i < state.steps.length) {
      if (resolve(state.steps[i])) return true;
      i += dir;
    }
    return false;
  }

  function start(steps, opts) {
    opts = opts || {};
    if (state) finish();
    if (!Array.isArray(steps) || !steps.length) { if (opts.onDone) opts.onDone(); return; }

    const root = el('div', 'sg-root');
    const hole = el('div', 'sg-hole');
    const tip = el('div', 'sg-tip');
    const skip = el('button', 'sg-skip'); skip.type = 'button'; skip.textContent = 'Skip';
    const title = el('h3', 'sg-tip-title');
    const body = el('p', 'sg-tip-body');
    const foot = el('div', 'sg-tip-foot');
    const dots = el('div', 'sg-dots');
    const back = el('button', 'sg-btn sg-back'); back.type = 'button'; back.textContent = 'Back';
    const next = el('button', 'sg-btn sg-next'); next.type = 'button'; next.textContent = 'Next';
    const btnRow = el('div', 'sg-btn-row');
    btnRow.appendChild(back); btnRow.appendChild(next);
    foot.appendChild(dots); foot.appendChild(btnRow);
    tip.appendChild(skip); tip.appendChild(title); tip.appendChild(body); tip.appendChild(foot);
    root.appendChild(hole); root.appendChild(tip);
    document.body.appendChild(root);
    // Swallow clicks so page-level "outside click" handlers (e.g. menu auto-close)
    // don't react to the guide's own buttons.
    root.addEventListener('click', function (e) { e.stopPropagation(); });

    state = {
      steps: steps.slice(), i: 0, node: null, onDone: opts.onDone,
      root, hole, tip, title, body, dots, back, next,
    };

    skip.onclick = finish;
    back.onclick = function () { show(state.i - 1, -1); };
    next.onclick = function () { show(state.i + 1, 1); };
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', reposition, true);
    window.addEventListener('scroll', reposition, true);

    show(0, 1);
  }

  function onKey(e) {
    if (!state) return;
    if (e.key === 'Escape') { e.preventDefault(); finish(); }
    else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); show(state.i + 1, 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); show(state.i - 1, -1); }
  }

  function show(idx, dir) {
    if (!state) return;
    const steps = state.steps;
    let i = idx;
    let node = null;
    while (i >= 0 && i < steps.length) {
      // before() can reveal the target first (e.g. open a menu) so it resolves.
      if (steps[i].before) { try { steps[i].before(); } catch (_) { /* ignore */ } }
      node = resolve(steps[i]);
      if (node) break;
      i += (dir >= 0 ? 1 : -1);
    }
    if (i < 0 || i >= steps.length || !node) { finish(); return; }

    state.i = i;
    state.node = node;
    try { node.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }); } catch (_) { /* old browsers */ }
    setTimeout(reposition, 80);

    state.title.textContent = steps[i].title || '';
    state.body.textContent = steps[i].body || '';
    state.back.style.visibility = hasResolvable(i, -1) ? 'visible' : 'hidden';
    state.next.textContent = hasResolvable(i, 1) ? 'Next' : 'Done';
    renderDots();
  }

  function renderDots() {
    state.dots.textContent = '';
    for (let k = 0; k < state.steps.length; k++) {
      const d = el('span', 'sg-dot' + (k === state.i ? ' is-active' : ''));
      state.dots.appendChild(d);
    }
  }

  function reposition() {
    if (!state || !state.node) return;
    const vw = window.innerWidth, vh = window.innerHeight;
    const r = state.node.getBoundingClientRect();
    const pad = 8;
    const hx = Math.max(0, r.left - pad);
    const hy = Math.max(0, r.top - pad);
    const hw = Math.min(vw, r.right + pad) - hx;
    const hh = Math.min(vh, r.bottom + pad) - hy;
    const hole = state.hole;
    hole.style.left = hx + 'px';
    hole.style.top = hy + 'px';
    hole.style.width = hw + 'px';
    hole.style.height = hh + 'px';

    const tip = state.tip;
    const tr = tip.getBoundingClientRect();
    const spaceBelow = vh - (hy + hh);
    const spaceAbove = hy;
    let top;
    if (spaceBelow > tr.height + 16 || spaceBelow >= spaceAbove) {
      top = hy + hh + 12;
    } else {
      top = hy - tr.height - 12;
    }
    top = Math.max(12, Math.min(top, vh - tr.height - 12));
    let left = hx + hw / 2 - tr.width / 2;
    left = Math.max(12, Math.min(left, vw - tr.width - 12));
    tip.style.top = top + 'px';
    tip.style.left = left + 'px';
  }

  function finish() {
    if (!state) return;
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', reposition, true);
    window.removeEventListener('scroll', reposition, true);
    if (state.root && state.root.parentNode) state.root.parentNode.removeChild(state.root);
    const cb = state.onDone;
    state = null;
    if (cb) { try { cb(); } catch (_) { /* swallow */ } }
  }

  global.SpotlightGuide = {
    start: start,
    stop: finish,
    isActive: function () { return !!state; },
  };
})(window);
