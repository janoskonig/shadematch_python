// End-to-end check of the match-based gameplay (10 rounds, one per cluster)
// against a LOCAL Flask + sqlite instance on port 5001. Never run against prod.
//
// Prereqs (see the session notes / plan):
//   export DATABASE_URL="sqlite:////path/to/match_test.db"; PORT=5001 python run.py
//   user TEST03 exists with a ConsentRecord and today's daily already submitted.
//
// Usage: node scripts/verify_match_flow.mjs
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:5001';
const UID = 'TEST03';

let failures = 0;
function check(cond, label) {
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + label);
  if (!cond) failures += 1;
}

async function api(path, body) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ── Server-side fixtures ────────────────────────────────────────────────────
const lab = await (await fetch(BASE + '/api/lab/target-colors')).json();
const recipeOf = new Map(lab.colors.map((c) => [c.id, c.drops]));

let match = (await api('/api/match/current', { user_id: UID })).match;
check(!!match && match.rounds.length === 10, 'match/current returns a 10-round match');
check(new Set(match.rounds.map((r) => r.cluster_code)).size === 10, 'all 10 clusters present');
const firstMatchId = match.match_id;

// Stale-client simulation: a save WITHOUT match fields must not advance a match.
const staleSave = await api('/save_session', {
  user_id: UID,
  attempt_uuid: crypto.randomUUID(),
  target_color_id: null,
  target_r: 10, target_g: 20, target_b: 30,
  drop_white: 1, drop_black: 1, drop_red: 0, drop_yellow: 0, drop_blue: 0,
  mixed_r: 10, mixed_g: 20, mixed_b: 30,
  delta_e: 5.0, time_sec: 3.2,
  timestamp: new Date().toISOString(),
  skipped: false,
});
const matchAfterStale = (await api('/api/match/current', { user_id: UID })).match;
check(staleSave.status === 'success' && staleSave.match === null, 'stale save (no match fields) accepted, match untouched');
check(matchAfterStale.match_id === firstMatchId && matchAfterStale.current_round === match.current_round,
  'match state unchanged after stale save');

// ── Browser ────────────────────────────────────────────────────────────────
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
await ctx.addInitScript(({ uid }) => {
  // Must match config.CLIENT_STORAGE_VERSION or base.html nukes localStorage.
  localStorage.setItem('sm_client_storage_version', '2');
  localStorage.setItem('userId', uid);
  localStorage.setItem('userBirthdate', '1990-01-01');
  localStorage.setItem('userGender', 'other');
  localStorage.setItem('guideShown', 'true');
  localStorage.setItem('shadeMatchInstructionsAckV1', '1');
  document.cookie = 'shadematch_cookie_consent=' + encodeURIComponent(JSON.stringify({
    given: true, timestamp: new Date().toISOString(),
    categories: { necessary: true, analytics: false, preferences: false },
  })) + ';path=/;SameSite=Lax';
}, { uid: UID });
const page = await ctx.newPage();
page.on('pageerror', (e) => { console.log('PAGEERROR', e.message); failures += 1; });

async function progressText() {
  return (await page.textContent('#progressText')) || '';
}

async function currentServerRound() {
  const m = (await api('/api/match/current', { user_id: UID })).match;
  return m;
}

async function solveCurrentRoundPerfectly() {
  const m = await currentServerRound();
  const round = m.rounds.find((r) => r.round_index === m.current_round);
  const drops = recipeOf.get(round.target.id);
  for (const [color, n] of Object.entries(drops)) {
    for (let i = 0; i < n; i++) {
      await page.click(`.color-circle[data-color='${color}']`);
    }
  }
  // Perfect detection is async (/calculate) → the Skip button relabels.
  await page.waitForFunction(
    () => document.getElementById('skipBtn').textContent.trim() !== 'Skip'
       && document.getElementById('skipBtn').textContent.trim() !== 'Kihagyás',
    { timeout: 15000 },
  );
  return round;
}

async function settleOverlays() {
  // Decline non-essential cookies if the banner is up.
  try { await page.click('.cookie-consent-btn-decline', { timeout: 2500 }); } catch { /* not shown */ }
  await page.waitForTimeout(400);
}

await page.goto(BASE + '/?lang=hu', { waitUntil: 'networkidle' });
await settleOverlays();
await page.waitForTimeout(800);

check((await progressText()).startsWith('Kör:'), `HU progress label shows rounds ("${await progressText()}")`);
const bootMatch = await currentServerRound();
check(bootMatch.match_id === firstMatchId, 'boot resumes the same persisted match');

await page.click('#startBtn');
await page.waitForTimeout(600);

// Round A: perfect solve
let before = await currentServerRound();
await solveCurrentRoundPerfectly();
await page.waitForTimeout(800);
let after = await currentServerRound();
check(after.current_round === before.current_round + 1, 'perfect save advanced the match round');
await page.click('#skipBtn'); // "Next color"
await page.waitForTimeout(600);
check((await progressText()).includes(`${after.current_round + 1}/10`), `progress shows round ${after.current_round + 1}/10`);

// Round B: skip WITH a mix (perception modal) advances
before = after;
await page.click(".color-circle[data-color='red']");
await page.waitForTimeout(600);
await page.click('#skipBtn');
await page.waitForSelector('#skipPerceptionModal', { state: 'visible', timeout: 5000 });
await page.click('#skipPerceptionUnacceptable');
await page.waitForTimeout(900);
after = await currentServerRound();
check(after.current_round === before.current_round + 1, 'rated skip advanced the match round');

// Round C: skip WITHOUT a mix advances via /api/match/skip-round
before = after;
await page.click('#skipBtn');
await page.waitForTimeout(900);
after = await currentServerRound();
check(after.current_round === before.current_round + 1, 'unmixed skip advanced the match round');

// Mid-match reload resumes the same match + round
await page.reload({ waitUntil: 'networkidle' });
await settleOverlays();
await page.waitForTimeout(800);
const resumed = await currentServerRound();
check(resumed.match_id === firstMatchId && resumed.current_round === after.current_round,
  'reload mid-match resumes same match_id and round');
check((await progressText()).includes(`${resumed.current_round + 1}/10`), 'progress text matches resumed round');

// Finish the match: solve every remaining round perfectly
await page.click('#startBtn');
await page.waitForTimeout(600);
let guard = 0;
while (guard++ < 12) {
  const m = await currentServerRound();
  if (m.match_id !== firstMatchId) break; // already rolled to a fresh match
  await solveCurrentRoundPerfectly();
  await page.waitForTimeout(800);
  const mm = (await api('/api/match/summary', { user_id: UID, match_id: firstMatchId }));
  await page.click('#skipBtn'); // next round OR triggers the summary modal
  await page.waitForTimeout(700);
  const modal = await page.$('#matchSummaryModal');
  if (modal) {
    const rows = await page.$$eval('#matchSummaryModal .skip-modal-panel > div:nth-of-type(1) > div', (els) => els.length);
    check(rows === 10, `summary modal lists 10 rounds (got ${rows})`);
    await page.click('#matchSummaryNewBtn');
    await page.waitForTimeout(900);
    break;
  }
}
const fresh = await currentServerRound();
check(fresh.match_id !== firstMatchId, 'a new match was drawn after completion');
check(fresh.current_round === 0 && fresh.status === 'active', 'new match starts at round 0');
check((await progressText()).includes('1/10'), 'progress reset to round 1/10');

const summary = await api('/api/match/summary', { user_id: UID, match_id: firstMatchId });
check(summary.status === 'success' && summary.summary.rounds.length === 10, 'summary endpoint returns 10 rounds');
check(summary.summary.completed_rounds + summary.summary.skipped_rounds === 10, 'all rounds have outcomes');

await page.screenshot({ path: (process.env.SCRATCH || '/tmp') + '/match_flow_final.png' });
await browser.close();

console.log(failures === 0 ? '\nALL E2E CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
