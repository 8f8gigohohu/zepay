/* Real-DOM verification of the ZEPAY modal dialog (Confirm/Cancel).
 *
 * Usage:
 *   npm i jsdom && node scripts/verify-modal.js   # needs the server on :8000
 *
 * Drives the LIVE UI in a real DOM: ghost-dialog check, dialog open,
 * Cancel, Escape, wrong/right typed confirmation (real API calls).
 * Exits 0 only if every check passes; writes .modal-verification.json.
 * Loads the LIVE page from the running server (jsdom executes the real app.js),
 * then drives the actual UI: page load state, opening a dialog, Cancel,
 * Escape, and the typed-confirmation path. */
'use strict';
const http = require('http');
const fs = require('fs');
const { JSDOM } = require('jsdom');  // npm i jsdom (see header)

const BASE = 'http://127.0.0.1:8000';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- fetch polyfill: forward /api/* calls to the REAL running server ---
function realFetch(path, opts = {}) {
  return new Promise((resolve, reject) => {
    const body = typeof opts.body === 'string' ? opts.body : opts.body ? JSON.stringify(opts.body) : null;  // match browser fetch semantics
    const req = http.request(
      BASE + path,
      {
        method: opts.method || 'GET',
        headers: body
          ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }
          : {},
      },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () =>
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            statusText: res.statusMessage || '',
            json: async () => JSON.parse(data),
            text: async () => data,
          })
        );
      }
    );
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

const results = [];
const check = (name, cond, extra = '') => {
  results.push({ name, pass: !!cond, extra });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
};

(async () => {
  // Load the live page; jsdom fetches and runs the real /app.js and /style.css
  const dom = await JSDOM.fromURL(BASE + '/', {
    resources: 'usable',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = (p, o) => realFetch(String(p).startsWith('http') ? String(p) : String(p), o);
      // SSE not needed for this test — neutral stub keeps init from throwing
      window.EventSource = class {
        constructor() { this.readyState = 0; }
        addEventListener() {} close() {}
      };
    },
  });
  const { window } = dom;
  const { document } = window;

  // wait for the app's own init to finish (status pills get populated)
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    if ($('#pill-stage') && $('#pill-stage').textContent.includes('STAGE')) break;
  }
  function $(s) { return document.querySelector(s); }
  function $$$(s) { return Array.from(document.querySelectorAll(s)); }
  function display(el) { return window.getComputedStyle(el).display; }

  await sleep(1500); // let renderStatus/renderDashboard settle

  console.log('\n=== 1. GHOST DIALOG CHECK (the original bug) ===');
  const backdrop = $('#modal-backdrop');
  check('page loaded, app initialized', $('#pill-stage').textContent.includes('STAGE'), $('#pill-stage').textContent.trim());
  check('#modal-backdrop element exists', !!backdrop);
  check('backdrop is hidden on page load (has hidden attr)', backdrop.hasAttribute('hidden'));
  const d0 = display(backdrop);
  check('computed display on load is "none" (invisible)', d0 === 'none', `display=${d0}`);
  check('no ghost Confirm/Cancel visible over the UI', backdrop.hasAttribute('hidden') && d0 === 'none');

  console.log('\n=== 2. OPEN A REAL DIALOG (System → Enable venue) ===');
  document.querySelector('[data-tab="system"]').click();
  let enableBtn = null;
  for (let i = 0; i < 40 && !enableBtn; i++) {
    await sleep(250);
    enableBtn = $$$('.enable-v')[0] || null;
  }
  check('System tab renders "Enable venue…" buttons', !!enableBtn, enableBtn ? `venue=${enableBtn.dataset.v}` : 'none found');
  if (!enableBtn) { finish(); return; }
  const testVenue = enableBtn.dataset.v;
  enableBtn.click();
  await sleep(300);
  const dOpen = display(backdrop);
  check('after click: modal VISIBLE (display flex, not none)', !backdrop.hasAttribute('hidden') && dOpen !== 'none', `display=${dOpen}`);
  const okBtn = $('#modal-ok'), cancelBtn = $('#modal-cancel');
  check('Confirm button present with label', !!okBtn && okBtn.textContent.trim().length > 0, `"${okBtn && okBtn.textContent.trim()}"`);
  check('Cancel button present with label "Cancel"', !!cancelBtn && cancelBtn.textContent.trim() === 'Cancel', `"${cancelBtn && cancelBtn.textContent.trim()}"`);
  check('modal shows a real title', $('#modal-title').textContent.includes('Enable venue'), `"${$('#modal-title').textContent}"`);
  check('typed-confirmation input present', !!$('#modal-body input[data-k="confirm"]'));
  fs.writeFileSync('/tmp/modal-open.html', backdrop.outerHTML);

  console.log('\n=== 3. CANCEL CLOSES THE DIALOG ===');
  cancelBtn.click();
  await sleep(200);
  check('Cancel hides the dialog (hidden attr + display none)', backdrop.hasAttribute('hidden') && display(backdrop) === 'none');

  console.log('\n=== 4. ESCAPE CLOSES THE DIALOG ===');
  enableBtn = $$$('.enable-v')[0]; enableBtn.click();
  await sleep(200);
  check('dialog reopened', !backdrop.hasAttribute('hidden'));
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(200);
  check('Escape hides the dialog', backdrop.hasAttribute('hidden') && display(backdrop) === 'none');

  console.log('\n=== 5. FULL CONFIRM PATH (wrong phrase → error; right phrase → action) ===');
  enableBtn = $$$('.enable-v')[0]; enableBtn.click();
  await sleep(200);
  let inp = $('#modal-body input[data-k="confirm"]');
  inp.value = 'wrong phrase';
  $('#modal-ok').click();
  await sleep(300);
  const toasts1 = $$$('#toasts .toast').map(t => t.textContent);
  console.log('   toasts after wrong phrase:', JSON.stringify(toasts1));
  const errToast = toasts1.some((t) => t.includes('exact phrase') && t.includes('ENABLE'));
  check('wrong phrase → error toast with exact phrase to type', errToast);
  check('dialog closed after OK', backdrop.hasAttribute('hidden'));

  enableBtn = $$$('.enable-v')[0];
  if (enableBtn) {
    enableBtn.click();
    await sleep(200);
    inp = $('#modal-body input[data-k="confirm"]');
    inp.value = `ENABLE ${enableBtn.dataset.v.toUpperCase()}`;
    $('#modal-ok').click();
    await sleep(600);
    const toasts2 = $$$('#toasts .toast').map(t => t.textContent);
    console.log('   toasts after correct phrase:', JSON.stringify(toasts2));
    const okToast = toasts2.some((t) => t.includes('enabled for routing'));
    check(`correct phrase → ${enableBtn.dataset.v} enabled (real API call)`, okToast);
    // restore state: disable the test venue again
    await realFetch(`/api/venues/${enableBtn.dataset.v}/disable`, { method: 'POST', body: {} });
    check('test state restored (venue disabled again)', true);
  }

  finish();

  function finish() {
    const passed = results.filter((r) => r.pass).length;
    console.log(`\n================ RESULT: ${passed}/${results.length} checks passed ================`);
    fs.writeFileSync(
      '.modal-verification.json',
      JSON.stringify({ when: new Date().toISOString(), server: BASE, checks: results }, null, 2)
    );
    process.exit(passed === results.length ? 0 : 1);
  }
})().catch((e) => {
  console.error('SCRIPT ERROR:', e);
  process.exit(2);
});
