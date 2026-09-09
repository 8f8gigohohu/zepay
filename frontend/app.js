/* ZEPAY V3 frontend — vanilla JS, no external deps, same-origin API.
   Every number shown comes from a real API response; unavailable states are
   rendered as UNAVAILABLE/DEGRADED badges, never invented. */
'use strict';

// ---------------------------------------------------------------- helpers
const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
const fmt = (v, d = 2) => (v === null || v === undefined || v === '' || Number.isNaN(+v))
  ? '—' : (+v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: 0 });
const pct = (v, d = 2) => (v === null || v === undefined || v === '') ? '—' : `${(+v * 100).toFixed(d)}%`;
const usd = v => (v === null || v === undefined) ? '—' : `$${fmt(v)}`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ago = ts => {
  if (!ts) return '—';
  const t = typeof ts === 'number' ? ts : Date.parse(ts);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${s | 0}s ago`;
  if (s < 3600) return `${(s / 60) | 0}m ago`;
  if (s < 86400) return `${(s / 3600) | 0}h ago`;
  return `${(s / 86400) | 0}d ago`;
};
const badge = (text, cls = '') => `<span class="badge ${cls}">${esc(text)}</span>`;
const cls = {
  LONG: 'long', SHORT: 'short', APPROVED: 'approved', WAIT: 'wait', HALTED: 'halted',
  REJECTED: 'halted', BLOCKED: 'halted', TRADEABLE: 'TRADEABLE', LIMITED: 'LIMITED',
  OK: 'ok', CONNECTED: 'connected', DEGRADED: 'degraded', STALE: 'degraded',
  UNAVAILABLE: 'fail', FAILED: 'fail', TRAINED: 'ok', UNTRAINED: 'warn',
  PAPER: 'info', SHADOW: 'purple', LIMITED_LIVE: 'warn', FULL_LIVE: 'long',
  NORMAL: 'ok', CAUTION: 'warn', DEFENSIVE: 'warn',
};
const bcls = t => cls[String(t)] || (String(t).includes('ERROR') ? 'fail' : '');

async function api(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `${r.status} ${r.statusText}`);
  return j;
}
const get = p => api(p);
const post = (p, body) => api(p, { method: 'POST', body: body || {} });
const del = p => api(p, { method: 'DELETE' });

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

function modal(title, bodyHtml, okLabel = 'Confirm') {
  return new Promise(resolve => {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = bodyHtml;
    $('#modal-ok').textContent = okLabel;
    $('#modal-backdrop').hidden = false;
    const inp0 = $('#modal-body input, #modal-body textarea');
    if (inp0) { inp0.focus(); }
    let done = false;
    const close = val => {
      if (done) return;
      done = true;
      $('#modal-backdrop').hidden = true;
      document.removeEventListener('keydown', onKey);
      resolve(val);
    };
    const onKey = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    $('#modal-cancel').onclick = () => close(null);
    $('#modal-ok').onclick = () => {
      const inp = $('#modal-body input, #modal-body textarea');
      close(inp ? { value: inp.value, inputs: Object.fromEntries($$('#modal-body [data-k]').map(e => [e.dataset.k, e.value])) } : { value: null });
    };
  });
}
async function confirmDanger(title, msg, phrase) {
  const r = await modal(title, `<p>${esc(msg)}</p>` + (phrase
    ? `<p class="muted" style="margin-top:8px">Type exactly: <b>${esc(phrase)}</b></p><input data-phrase placeholder="${esc(phrase)}">`
    : ''), phrase ? 'Submit' : 'Confirm');
  if (!r) return false;
  if (phrase && r.value !== phrase) { toast('Confirmation phrase did not match', 'err'); return false; }
  return true;
}

function table(headers, rows) {
  if (!rows.length) return `<p class="muted">No records yet — real activity will appear here.</p>`;
  return `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>` +
    `<tbody>${rows.map(r => `<tr>${r.map(c => `<td class="${c && c._wrap ? 'wrap' : ''}">${c && c._html !== undefined ? c._html : esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
const html = s => ({ _html: s });
const wrap = s => ({ _html: esc(s), _wrap: true });
const kvList = obj => `<div class="kv">${Object.entries(obj).map(([k, v]) =>
  `<div class="k">${esc(k)}</div><div class="v">${v && v._html !== undefined ? v._html : esc(v)}</div>`).join('')}</div>`;
const card = (k, v, s = '', vcls = '') =>
  `<div class="card"><div class="k">${esc(k)}</div><div class="v ${vcls}">${v}</div>${s ? `<div class="s">${esc(s)}</div>` : ''}</div>`;

// sparkline from REAL candles
function sparkline(candles, w = 260, h = 54) {
  if (!candles || candles.length < 2) return '<span class="muted">no candles</span>';
  const cs = candles.map(c => c.c);
  const mn = Math.min(...cs), mx = Math.max(...cs), rg = mx - mn || 1;
  const pts = cs.map((c, i) => `${(i / (cs.length - 1) * w).toFixed(1)},${(h - 4 - (c - mn) / rg * (h - 8)).toFixed(1)}`);
  const up = cs[cs.length - 1] >= cs[0];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <polyline points="${pts.join(' ')}" fill="none" stroke="${up ? '#2ecc71' : '#ff5d5d'}" stroke-width="1.6"/></svg>`;
}

// ---------------------------------------------------------------- state
const S = { tab: 'dashboard', status: null, sse: null, timers: {} };

function setTab(t) {
  S.tab = t;
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  $$('.tabpane').forEach(p => p.classList.toggle('active', p.id === `tab-${t}`));
  refreshTab();
}
$$('.tab').forEach(b => b.onclick = () => setTab(b.dataset.tab));

// ---------------------------------------------------------------- status bar
async function refreshStatus() {
  try {
    const st = await get('/status');
    S.status = st;
    const eng = st.engine || {}, ex = st.execution || {}, ks = st.kill_switch || {};
    setPill('pill-stage', `STAGE ${eng.stage || '?'}`, bcls(eng.stage));
    setPill('pill-mode', `MODE ${ex.mode || '?'}`, bcls(ex.mode));
    setPill('pill-kill', ks.kill_switch ? 'KS ENGAGED' : 'KS OFF', ks.kill_switch ? 'bad' : 'ok');
    const h = await get('/health');
    setPill('pill-health', `HEALTH ${h.overall}`, h.overall === 'OK' ? 'ok' : (h.overall === 'DEGRADED' ? 'warn' : 'bad'));
    const ms = (h.models || {}).state;
    setPill('pill-ai', `AI ${ms || '?'}`, bcls(ms));
    S.health = h;
    renderBanner(st, h);
  } catch (e) {
    setPill('pill-health', 'HEALTH API DOWN', 'bad');
  }
}

// ---- global honesty banner (§7: REAL DATA UNAVAILABLE is always shown loudly) ----
function renderBanner(st, h) {
  const el = $('#global-banner');
  if (!el) return;
  const eng = st.engine || {}, ex = st.execution || {}, ks = st.kill_switch || {};
  const q = h.data_quality || {};
  const feedDown = (q.feed_health || {}).status === 'UNAVAILABLE' ||
    (h.issues || []).some(i => String(i).includes('market data UNAVAILABLE'));
  let cls = 'info', title = '', why = '', btn = '';
  if (ks.kill_switch) {
    cls = 'danger'; title = '⛔ TRADING HALTED — kill switch engaged';
    why = 'New orders are stopped platform-wide. Resume requires the typed phrase in the Risk tab. No auto-resume.';
  } else if (feedDown) {
    cls = 'danger';
    title = '⚠ REAL DATA UNAVAILABLE — NEW TRADES STOPPED (by design)';
    why = 'ZEPAY never trades without real market data. This usually means this machine/network cannot reach the exchange APIs (firewall, geo-block, or a sandboxed preview). Run the connectivity check to see exactly what is blocked.';
    btn = '<button class="btn small" id="btn-conn-check">Run connectivity check</button>';
  } else if (String(ex.mode) === 'LIVE') {
    cls = 'danger'; title = '🔴 LIVE — REAL MONEY AT RISK';
    why = `Stage ${eng.stage}. Risk engine remains the final authority on every order.`;
  } else if (String(eng.stage) === 'SHADOW') {
    cls = 'warn'; title = 'SHADOW — real data, orders shadowed (no real money)';
    why = 'Signals and risk run on real data; no orders reach exchanges.';
  } else {
    cls = 'ok'; title = '🟢 PAPER — real data, simulated fills (no real money)';
    why = 'Prices, features, AI and risk are real; only fills are simulated.';
  }
  el.className = cls;
  el.hidden = false;
  el.innerHTML = `<b>${title}</b>${btn}<span class="why">${esc(why)}</span>`;
  const b = $('#btn-conn-check');
  if (b) b.onclick = runConnectivityCheck;
}

async function runConnectivityCheck() {
  toast('Probing every exchange endpoint — real network calls…', 'info');
  try {
    const r = await get('/diagnostics/connectivity');
    S.conn = r;
    await modal('Connectivity check — real probes', `
      <p><b>${esc(r.verdict)}</b></p>
      ${table(['Venue', 'Endpoint', 'Status', 'Latency', 'Error'],
        (r.probes || []).map(p => [esc(p.venue), wrap(p.host || '—'),
          badge(p.status, p.status === 'REACHABLE' ? 'ok' : 'fail'),
          p.latency_ms != null ? `${p.latency_ms} ms` : '—', wrap(p.error || '—')]))}`,
      'Close');
    if (S.tab === 'dashboard') renderDashboard();
  } catch (e) { toast(e.message, 'err'); }
}
function setPill(id, text, cls2) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = `pill ${cls2 || ''}`;
}

// ---------------------------------------------------------------- SSE
function connectSSE() {
  if (S.sse) S.sse.close();
  S.sse = new EventSource('/api/events');
  setPill('pill-feed', 'SSE …', 'info');
  S.sse.onopen = () => setPill('pill-feed', 'SSE LIVE', 'ok');
  S.sse.onerror = () => setPill('pill-feed', 'SSE DOWN', 'bad');
  S.sse.onmessage = ev => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    const feed = $('#dash-events');
    if (!feed) return;
    const line = document.createElement('div');
    line.className = 'ev';
    const t = new Date((d.ts || Date.now() / 1000) * (d.ts < 1e12 ? 1000 : 1)).toLocaleTimeString();
    line.innerHTML = `<span class="ts">${t}</span> <b>${esc(d.event)}</b> ${esc(JSON.stringify(d.payload || {}).slice(0, 160))}`;
    feed.prepend(line);
    while (feed.children.length > 120) feed.lastChild.remove();
    if (['RiskDecision', 'OrderFilled', 'KillSwitchActivated'].includes(d.event)) {
      toast(`${d.event}: ${JSON.stringify(d.payload || {}).slice(0, 90)}`, d.event === 'KillSwitchActivated' ? 'err' : 'ok');
      refreshStatus();
    }
  };
}

// ---------------------------------------------------------------- DASHBOARD
async function renderDashboard() {
  const h = S.health || await get('/health');
  const st = S.status || await get('/status');
  try { await renderSetup(h, st); } catch (e) { /* checklist must never break the dashboard */ }
  const cards = [];
  const acct = await get('/account?mode=PAPER').catch(() => null);
  cards.push(card('Paper equity', usd(acct && acct.equity), acct ? `available ${usd(acct.available)}` : 'no account'));
  const dd = acct ? +acct.drawdown_pct || 0 : 0;
  cards.push(card('Drawdown', pct(dd), 'halt threshold from config', dd > 0.08 ? 'red' : (dd > 0.03 ? 'amber' : 'green')));
  cards.push(card('Day P&L (paper)', usd(acct && acct.day_pnl), 'real calendar day UTC', acct && acct.day_pnl >= 0 ? 'green' : 'red'));
  const eng = st.engine || {};
  cards.push(card('Engine cycles', fmt(eng.cycles, 0), eng.last_error ? `last error: ${eng.last_error.slice(0, 60)}` : 'no errors'));
  const m = (h.metrics || {});
  cards.push(card('Uptime', `${fmt((m.uptime_s || 0) / 60, 0)} min`, `p95 cycle ${fmt(m.cycle_latency_p95_s)}s`));
  cards.push(card('System health', esc(h.overall), `${(h.issues || []).length} issue(s)`, h.overall === 'OK' ? 'green' : (h.overall === 'DEGRADED' ? 'amber' : 'red')));
  const q = (h.data_quality || {});
  cards.push(card('Data quality', esc(q.overall || '?'), JSON.stringify(q.status_counts || {}), bcls(q.overall) === 'ok' ? 'green' : 'amber'));
  const ws = st.ws || {};
  cards.push(card('Websocket', esc(ws.overall || '?'), `${Object.values(ws.state_counts || {}).reduce((a, b) => a + b, 0)} streams`, bcls(ws.overall) === 'ok' ? 'green' : ''));
  const ai = h.models || {};
  cards.push(card('AI model', esc(ai.state || '?'), ai.champion ? `champion ${String(ai.champion).slice(0, 14)}` : 'no champion', bcls(ai.state) === 'ok' ? 'green' : 'amber'));
  $('#dash-cards').innerHTML = cards.join('');
  if (h.issues && h.issues.length) {
    $('#dash-cards').innerHTML += `<div class="card" style="grid-column:1/-1"><div class="k">Active issues</div>
      <div class="s" style="font-size:12.5px;color:var(--amber)">${h.issues.map(esc).join('<br>')}</div></div>`;
  }
  const venues = st.venues || {};
  $('#dash-venues').innerHTML = table(['Venue', 'Status', 'Checks'],
    Object.entries(venues).map(([vid, v]) => [vid, badge(v.status || '?', bcls(v.status)), wrap(JSON.stringify(v.checks || v.error || ''))]));
  const alerts = await get('/alerts?limit=15').catch(() => []);
  $('#dash-alerts').innerHTML = table(['Level', 'Title', 'When'],
    alerts.map(a => [badge(a.level, a.level === 'CRITICAL' ? 'fail' : (a.level === 'WARN' ? 'warn' : 'info')), wrap(a.title), ago(a.created_at)]));
}

async function renderSetup(h, st) {
  const lic = await get('/license/status').catch(() => ({}));
  const venues = st.venues || {};
  const eng = st.engine || {};
  const conn = S.conn || null;
  const anyVenueEnabled = Object.values(venues).some(v => v.enabled);
  const credsSet = Object.values(venues).some(v => (v.checks || {}).signed_account === true);
  const dataOk = ((h.data_quality || {}).feed_health || {}).status === 'OK' &&
    !((h.issues || []).some(i => String(i).includes('market data UNAVAILABLE')));
  const liveOn = String((st.execution || {}).mode) === 'LIVE' || !!((st.stage_gate || {}).live_enabled);
  const step = (n, done, t, help) =>
    `<div class="setup-step ${done ? 'done' : ''}"><div class="num">${done ? '✓' : n}</div>
     <div class="body"><div class="t">${t} ${done ? badge('DONE', 'ok') : ''}</div><div class="h">${help}</div></div></div>`;
  let reachTxt = 'unknown — run the check';
  let reachDone = false;
  if (conn) {
    const okVenues = (conn.probes || []).filter(p => p.status === 'REACHABLE').length;
    reachDone = okVenues > 0;
    reachTxt = reachDone ? `${okVenues} exchange endpoint(s) reachable` : 'no exchange endpoint reachable from this machine/network';
  }
  $('#dash-setup').innerHTML = [
    step(1, !!lic.verified, 'Verify your ZEPAY key', lic.verified
      ? `Mode: ${esc(lic.mode)}. ${lic.mode === 'offline' ? 'Offline validation (not cloud verification).' : 'Cloud-verified.'}`
      : 'System tab → ZEPAY license → paste your key. Trading stays gated until verified.'),
    step(2, anyVenueEnabled, 'Enable a trading venue', anyVenueEnabled
      ? 'At least one venue is enabled for routing (System tab → Venues).'
      : 'System tab → Venues → "Enable venue…" (typed confirmation). Spot works with data only; futures also needs step 3.'),
    step(3, credsSet, 'Connect exchange API keys (for live/LIVE futures)', credsSet
      ? 'Credentials stored (encrypted). Withdrawal-enabled keys are refused.'
      : 'System tab → Venues → "Set credentials…". Trade-only keys, withdrawals never. Paper mode works without keys.'),
    step(4, reachDone, 'Exchange APIs reachable', `${reachTxt}. <b>ZEPAY never fakes data</b> — if this is blocked (sandbox/geo-block/firewall) no trading can happen here. ` +
      '<button class="btn small" id="btn-setup-conn">Check now</button>'),
    step(5, dataOk, 'Real market data flowing', dataOk
      ? 'Feeds healthy — candles streaming from the exchange.'
      : 'Feeds are down. This follows from step 4: no exchange connectivity → REAL DATA UNAVAILABLE → new trades stopped.'),
    step(6, (eng.cycles || 0) > 0 && dataOk, 'Trade in PAPER (real data, simulated fills)', (eng.cycles || 0) > 0
      ? `Engine running — ${fmt(eng.cycles, 0)} cycles so far. ${dataOk ? 'Watch Trading tab for positions/decisions.' : 'Waiting for real data (step 5).'}`
      : 'Markets tab → "Run cycle now". Paper uses real prices; only the fills are simulated.'),
    step(7, liveOn, 'Go LIVE (optional — real money)', liveOn
      ? 'LIVE is active. Risk engine still enforces every limit.'
      : 'Risk tab → Stage gate: PAPER → SHADOW → LIMITED LIVE → FULL LIVE. Each promotion is manual, typed, and never automatic.'),
  ].join('') + `
  <div class="setup-quick"><b>Cannot get data in this preview?</b> This sandboxed preview blocks ALL outbound exchange connections
    (the connectivity check proves it). To actually trade, run ZEPAY where Binance APIs are reachable — your own machine or a VPS:
    <div style="margin-top:6px">
      <code>git clone https://github.com/8f8gigohohu/zepay.git</code> → <code>cd zepay</code> →
      <code>pip install -r requirements.txt</code> → <code>python -m zepay.apps.server</code> → open
      <code>http://localhost:8000</code>
    </div>
    Same UI, same engine — with real data flowing, paper trading starts immediately; live needs your keys + the stage gate.</div>`;
  const b = $('#btn-setup-conn');
  if (b) b.onclick = runConnectivityCheck;
}

// ---------------------------------------------------------------- MARKETS
async function renderMarkets() {
  const d = await get('/markets');
  $('#markets-cycle').textContent = d.cycle_id ? `cycle ${d.cycle_id} · ${ago(d.ts)}` : (d.note || '');
  const filter = ($('#markets-search').value || '').toUpperCase();
  const rows = (d.signals || []).filter(m => !filter || String(m.market).includes(filter));
  $('#markets-table').innerHTML = table(
    ['Market', 'Health', 'Regime', 'AI dir', 'p_long', 'Conf', 'Mode', 'Sim net%', 'Sim', 'Score', 'Decision', 'Reasons'],
    rows.map(m => {
      const ai = m.ai || {}, sim = m.sim || {};
      return [html(`<a href="#" class="mkt-link">${esc(m.market)}</a>`),
        badge(m.health || '?', bcls(m.health)), esc(m.regime || '—'),
        badge(ai.direction || m.decision || '?', bcls(ai.direction)),
        fmt(ai.p_long, 3), fmt(ai.confidence, 3), esc((ai.mode || '').replace('-LABELED', '*')),
        html(`<span class="${(sim.expected_net_pct || 0) >= 0 ? 'pos' : 'neg'} num">${sim.expected_net_pct === undefined ? '—' : pct(sim.expected_net_pct, 3)}</span>`),
        badge(sim.passes === true ? 'PASS' : (sim.passes === false ? 'FAIL' : '?'), sim.passes ? 'ok' : 'warn'),
        fmt(m.score, 1), badge(m.decision || '?', bcls(m.decision)),
        wrap((m.reasons || [m.why]).filter(Boolean).join(' · ').slice(0, 140))];
    }));
  $$('.mkt-link').forEach(a => a.onclick = e => { e.preventDefault(); openMarket(a.textContent); });
  const u = await get('/universe?limit=200');
  $('#universe-count').textContent = `· source: ${u.source.source} · ${u.source.instruments} instruments discovered · venues: ${Object.keys(u.source.venues || {}).join(', ') || '—'}`;
  const us = ($('#universe-search').value || '').toUpperCase();
  const assets = (u.assets || []).filter(a => !us || String(a.symbol).includes(us)).slice(0, 40);
  const [spotSet, futSet] = await Promise.all([
    get('/universe?venue=binance_spot&limit=300').then(r => new Set((r.assets || []).map(a => a.symbol))).catch(() => new Set()),
    get('/universe?venue=binance_futures&limit=300').then(r => new Set((r.assets || []).map(a => a.symbol))).catch(() => new Set()),
  ]);
  $('#universe-table').innerHTML = table(['Symbol', 'Venue', 'Trading', 'Status', '24h vol (USD)', 'Route'],
    assets.map(a => {
      const isFut = a.venue === 'binance_futures';
      const other = isFut ? 'binance_spot' : 'binance_futures';
      const canSwitch = isFut ? spotSet.has(a.symbol) : futSet.has(a.symbol);
      return [a.symbol,
        badge(a.venue, isFut ? 'warn' : 'ok'),
        isFut ? badge(`PERP ${a.max_leverage ? '≤' + a.max_leverage + 'x' : ''}`, 'warn') : 'SPOT',
        badge(a.status || 'TRADING', 'ok'), fmt(a.volume24h_usd, 0),
        canSwitch ? html(`<button class="btn small route-m" data-m="${esc(a.symbol)}" data-v="${other}">→ ${other === 'binance_futures' ? 'FUTURES' : 'SPOT'}</button>`) : '<span class="muted">—</span>'];
    }));
  $$('.route-m').forEach(b => b.onclick = async () => {
    try {
      const r = await post(`/markets/${encodeURIComponent(b.dataset.m)}/venue`, { venue: b.dataset.v });
      toast(`${b.dataset.m} now trades on ${r.venue} (${r.trading_mode})`, 'ok');
      renderMarkets();
    } catch (e) { toast(e.message, 'err'); }
  });
}
async function openMarket(market) {
  $('#market-detail').hidden = false;
  $('#md-title').textContent = market;
  $('#md-chart').innerHTML = '<p class="muted">loading real candles…</p>';
  $('#md-explain').innerHTML = '';
  try {
    const c = await get(`/markets/${encodeURIComponent(market)}/candles?limit=120`);
    $('#md-chart').innerHTML = c.status === 'UNAVAILABLE'
      ? badge('CANDLES UNAVAILABLE', 'fail')
      : `<h3>Price · last ${c.count} real ${esc(c.interval)} candles ${c.stale ? badge('STALE', 'warn') : ''}</h3>${sparkline(c.candles, 420, 90)}
         <p class="muted num">last close ${fmt(c.candles[c.candles.length - 1].c, 6)} · age ${fmt(c.age_s, 0)}s</p>`;
  } catch (e) { $('#md-chart').innerHTML = badge('CANDLE FETCH FAILED', 'fail') + esc(e.message); }
  try {
    const x = await get(`/markets/${encodeURIComponent(market)}/explanation`);
    if (x.status === 'NO_DATA') { $('#md-explain').innerHTML = `<p class="muted">${esc(x.note)}</p>`; return; }
    const parts = [];
    if (x.decision) parts.push(['Decision', badge(x.decision, bcls(x.decision))]);
    if (x.regime) parts.push(['Regime', esc(x.regime)]);
    if (x.ai_mode) parts.push(['AI mode', esc(x.ai_mode)]);
    (x.reasons || []).forEach(r => parts.push(['Why', wrap(r)]));
    (x.risks || []).forEach(r => parts.push(['Risk', html(`<span class="neg">${esc(r)}</span>`)]));
    (x.unavailable || []).forEach(r => parts.push(['Unavailable', badge(r, 'warn')]));
    if (x.costs) parts.push(['Costs', wrap(JSON.stringify(x.costs))]);
    $('#md-explain').innerHTML = `<h3>Explanation (§40 — real values only)</h3>${kvList(Object.fromEntries(parts.map((p, i) => [p[0] + (i > 8 ? ` ${i}` : ''), p[1]])))}`;
  } catch (e) { $('#md-explain').innerHTML = esc(e.message); }
}
$('#md-close').onclick = () => { $('#market-detail').hidden = true; };
$('#markets-search').oninput = debounce(renderMarkets, 400);
$('#universe-search').oninput = debounce(renderMarkets, 500);
$('#btn-run-cycle').onclick = async e => {
  e.target.disabled = true; e.target.textContent = 'Running cycle…';
  try { const r = await post('/cycle/run'); toast(`Cycle ${r.ok ? 'complete' : 'FAILED'} in ${r.elapsed_s}s`, r.ok ? 'ok' : 'err'); renderMarkets(); refreshStatus(); }
  catch (err) { toast(err.message, 'err'); }
  e.target.disabled = false; e.target.textContent = 'Run cycle now';
};
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ---------------------------------------------------------------- TRADING
async function renderTrading() {
  const mode = $('#pos-mode').value;
  const acct = await get(`/account?mode=${mode}`).catch(() => null);
  const snap = await get(`/portfolio?mode=${mode}`).catch(() => null);
  const cards = [];
  cards.push(card(`${mode} equity`, usd(acct && acct.equity), `invested ${usd(acct && acct.invested)}`));
  cards.push(card('Unrealized P&L', usd(acct && acct.unrealized_pnl), '', acct && acct.unrealized_pnl >= 0 ? 'green' : 'red'));
  cards.push(card('Realized P&L', usd(acct && acct.realized_pnl), `fees ${usd(acct && acct.fees_paid)}`, acct && acct.realized_pnl >= 0 ? 'green' : 'red'));
  const exp = (snap && snap.exposure) || {};
  cards.push(card('Total exposure', pct(exp.total_pct), `long ${pct(exp.long_pct)} · short ${pct(exp.short_pct)}`));
  const v = (snap && snap.var) || {};
  cards.push(card('VaR 95 / CVaR 95', `${pct(v.var_95_p, 1)} / ${pct(v.cvar_95_p, 1)}`, 'historical simulation, real returns'));
  cards.push(card('Positions', fmt(snap && snap.positions, 0), `mode ${mode}`));
  $('#trading-cards').innerHTML = cards.join('');

  const pos = await get(`/positions?mode=${mode}`).catch(() => []);
  $('#positions-table').innerHTML = table(
    ['Market', 'Venue', 'Dir', 'Qty', 'Entry', 'Mark', 'uPnL', 'SL', 'TP', 'Bars', ''],
    pos.map(p => [p.market, p.venue || '—', badge(p.direction, bcls(p.direction)), fmt(p.qty, 6),
      fmt(p.entry_price, 4), fmt(p.current_price, 4),
      html(`<span class="${p.unrealized_pnl >= 0 ? 'pos' : 'neg'} num">${fmt(p.unrealized_pnl)}</span>`),
      fmt(p.stop_loss, 4), fmt(p.take_profit, 4), fmt(p.bars_held, 0),
      html(`<button class="btn small danger close-pos" data-m="${esc(p.market)}">Close</button>`)]));
  $$('.close-pos').forEach(b => b.onclick = async () => {
    const m = b.dataset.m;
    if (!await confirmDanger('Close position', `Market-close ${m} (${mode}) at real market price with costs?`)) return;
    try { const r = await post(`/positions/${encodeURIComponent(m)}/close`, { mode, reason: 'manual-ui' }); toast(r.ok ? `Closed ${m}: net ${fmt(r.pnl)} USD @ ${fmt(r.price, 4)}` : `Close failed: ${r.error}`, r.ok ? 'ok' : 'err'); renderTrading(); }
    catch (e) { toast(e.message, 'err'); }
  });

  const orders = await get('/orders?limit=40').catch(() => []);
  $('#orders-table').innerHTML = table(['ID', 'Market', 'Venue', 'Side', 'Qty', 'Price', 'Status', 'Mode', 'When', ''],
    orders.map(o => [String(o.id).slice(0, 12), o.market, o.venue, o.side, fmt(o.qty, 6), fmt(o.price, 4),
      badge(o.status, bcls(o.status)), o.mode || '—', ago(o.created_at),
      ['CREATED', 'VALIDATED', 'SUBMITTED', 'NEW', 'PARTIALLY_FILLED'].includes(o.status)
        ? html(`<button class="btn small cancel-ord" data-id="${esc(o.id)}">Cancel</button>`) : '']));
  $$('.cancel-ord').forEach(b => b.onclick = async () => {
    try { const r = await post(`/orders/${encodeURIComponent(b.dataset.id)}/cancel?mode=${mode}`); toast(r.ok ? 'Order cancelled' : `Cancel failed: ${r.error}`, r.ok ? 'ok' : 'err'); renderTrading(); }
    catch (e) { toast(e.message, 'err'); }
  });

  const trades = await get('/trades?limit=30').catch(() => []);
  $('#trades-table').innerHTML = table(['Market', 'Dir', 'Qty', 'Entry', 'Exit', 'Gross', 'Fees', 'Net', 'Reason', 'When'],
    trades.map(t => [t.market, badge(t.direction, bcls(t.direction)), fmt(t.qty, 6), fmt(t.entry_price, 4),
      fmt(t.exit_price, 4), fmt(t.gross_pnl), fmt(t.fees), 
      html(`<span class="${t.net_pnl >= 0 ? 'pos' : 'neg'} num">${fmt(t.net_pnl)}</span>`),
      esc(t.close_reason || t.reason || ''), ago(t.closed_at || t.created_at)]));

  const eq = await get('/execution-quality').catch(() => ({ venues: {} }));
  $('#execq-table').innerHTML = table(['Venue', 'Fills', 'Avg |slip| (bps)', 'Fill ratio', 'Med latency ms', 'Quality'],
    Object.entries(eq.venues || {}).map(([v, q]) => [v, fmt(q.samples, 0), fmt(q.avg_abs_slippage_bps, 2),
      fmt(q.avg_fill_ratio, 3), fmt(q.median_latency_ms, 0),
      q.quality_score === null || q.quality_score === undefined ? badge('NO DATA', 'warn') : fmt(q.quality_score, 3)]));

  const dec = await get('/decisions?limit=40').catch(() => []);
  $('#decisions-table').innerHTML = table(['When', 'Market', 'Stage', 'Strategy', 'Model', 'Conf', 'Exp net', 'Decision', 'Reasons'],
    dec.map(d => [ago(d.ts), d.market, badge(d.stage, bcls(d.stage)), esc(d.strategy || '—'),
      esc(String(d.model_version || '—').slice(0, 18)), fmt(d.confidence, 3),
      html(`<span class="${(d.expected_net || 0) >= 0 ? 'pos' : 'neg'} num">${d.expected_net === null ? '—' : pct(d.expected_net, 3)}</span>`),
      badge(d.decision, bcls(d.decision)), wrap(String(d.reasons || '').slice(0, 120))]));
}
$('#pos-mode').onchange = renderTrading;
$('#btn-cancel-all').onclick = async () => {
  if (!await confirmDanger('Cancel all open orders', 'Cancel every open order across paper and live venues?')) return;
  try { const r = await post('/orders/cancel-all', { reason: 'manual-ui' }); toast(`Cancelled ${r.cancelled} order(s)`, 'ok'); renderTrading(); }
  catch (e) { toast(e.message, 'err'); }
};
$('#btn-flatten').onclick = async () => {
  const r = await modal('Emergency flatten', `<p>Close/reduce ALL positions immediately?</p>
    <select data-k="policy"><option value="flatten">flatten — close everything</option><option value="reduce">reduce — halve exposure</option></select>`);
  if (!r) return;
  try { const res = await post('/emergency/flatten', { policy: r.inputs.policy }); toast(`Flatten (${res.policy}): ${res.actions} action(s)`, 'ok'); renderTrading(); refreshStatus(); }
  catch (e) { toast(e.message, 'err'); }
};

// ---------------------------------------------------------------- RISK
async function renderRisk() {
  const r = await get('/risk');
  $('#risk-state').innerHTML = kvList({
    'System state': badge(r.system_state, bcls(r.system_state)),
    'Message': wrap(r.state_message || ''),
    'New entries': r.entries_allowed ? badge('ALLOWED', 'ok') : badge('BLOCKED', 'fail'),
    'Why': wrap(r.entries_reason || ''),
    'Consecutive losses': fmt(r.consecutive_losses, 0),
    'Stage caps': wrap(JSON.stringify(r.stage_caps || {})),
  });
  const st = S.status || await get('/status');
  const ks = st.kill_switch || {};
  $('#risk-kill').innerHTML = kvList({
    'Kill switch': ks.kill_switch ? badge('ENGAGED', 'fail') : badge('OFF', 'ok'),
    'Since': ago(ks.since), 'By': esc(ks.actor || '—'), 'Reason': wrap(ks.reason || ''),
  }) + `<div class="actions" style="margin-top:10px">
      <button class="btn danger" id="btn-kill" ${ks.kill_switch ? 'disabled' : ''}>Engage kill switch</button>
      <button class="btn" id="btn-resume" ${ks.kill_switch ? '' : 'disabled'}>Resume…</button></div>`;
  $('#btn-kill').onclick = async () => {
    const m = await modal('Engage kill switch', `<p>Halt ALL trading immediately? Open orders can be cancelled and positions flattened.</p>
      <input data-k="reason" placeholder="reason (required for audit)">
      <label style="display:block;margin-top:8px"><input type="checkbox" data-k="flatten"> also flatten open positions</label>`, 'ENGAGE');
    if (!m || !m.inputs.reason) { if (m) toast('Reason is required for the audit trail', 'err'); return; }
    try { const res = await post('/kill-switch/engage', { reason: m.inputs.reason, cancel_orders: true, flatten: !!m.inputs.flatten }); toast(res.ok ? 'KILL SWITCH ENGAGED' : `Failed: ${res.error}`, res.ok ? 'warn' : 'err'); refreshStatus(); renderRisk(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#btn-resume').onclick = async () => {
    if (!await confirmDanger('Resume trading', 'Release the kill switch? Risk gates re-evaluate from scratch; nothing auto-resumes.', 'RESUME TRADING')) return;
    try { const res = await post('/kill-switch/resume', { confirmation: 'RESUME TRADING' }); toast(res.ok ? 'Kill switch released' : `Refused: ${res.error}`, res.ok ? 'ok' : 'err'); refreshStatus(); renderRisk(); }
    catch (e) { toast(e.message, 'err'); }
  };

  const g = st.stage_gate || {};
  const req = g.requirements || {};
  const checks = req.checks || {};
  const reqRows = Object.entries(checks).map(([k, v]) => {
    const ok = v && (v.ok !== undefined ? v.ok : v.met);
    let det = ok ? 'met' : 'NOT met';
    if (v) {
      if (v.detail) det = v.detail;
      else if (v.required !== undefined) det = `${v.actual ?? 0}/${v.required} recorded`;
      else if (Array.isArray(v.issues) && v.issues.length) det = v.issues.join('; ');
      else if (v.violations !== undefined) det = `${v.violations} violations`;
    }
    return `<div class="check">${ok ? '<span class="yes">✔</span>' : '<span class="no">✘</span>'}<span>${esc(k)}: ${esc(String(det).slice(0, 110))}</span></div>`;
  }).join('');
  const nextStage = req.next_stage || null;
  $('#risk-gate').innerHTML = kvList({
    'Current stage': badge(g.stage || '?', bcls(g.stage)),
    'Live enabled': g.live_enabled ? badge('YES', 'fail') : badge('NO (default)', 'ok'),
    'Next stage': esc(nextStage || '—'),
    'All prerequisites met': req.met ? badge('YES', 'ok') : badge('NO', 'warn'),
    'Required phrase': esc(req.confirmation_phrase || '—'),
    'Live block': g.live_block_reason ? badge(g.live_block_reason, 'fail') : 'none',
    'Confirmed steps': wrap(JSON.stringify(g.confirmed_steps || [])),
  }) + `<div style="margin-top:8px">${reqRows || '<p class="muted">at final stage — risk limits remain authoritative</p>'}</div>
    <div class="actions" style="margin-top:10px">
      <button class="btn primary" id="btn-promote" ${nextStage ? '' : 'disabled'}>Promote to ${esc(nextStage || '—')}…</button>
      <button class="btn" id="btn-demote">Demote one stage</button></div>`;
  $('#btn-promote').onclick = async () => {
    const phrases = { SHADOW: 'ENABLE SHADOW MODE', LIMITED_LIVE: 'ENABLE LIMITED LIVE', FULL_LIVE: 'ENABLE FULL LIVE' };
    const ph = req.confirmation_phrase || phrases[nextStage] || nextStage;
    if (!await confirmDanger(`Promote to ${nextStage}`, 'All prerequisites below must be met. This never happens automatically.', ph)) return;
    try { const res = await post('/stage/promote', { confirmation: ph, actor: 'ui-operator' }); toast(res.ok ? `Stage promoted: ${res.stage || nextStage}` : `Refused: ${res.error}`, res.ok ? 'ok' : 'err'); refreshStatus(); renderRisk(); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#btn-demote').onclick = async () => {
    if (!await confirmDanger('Demote stage', 'Demote one operational stage? Always allowed — safety first.')) return;
    try { const res = await post('/stage/demote', { reason: 'ui-operator demote', actor: 'ui-operator' }); toast(res.ok ? `Demoted to ${res.stage}` : `Failed: ${res.error}`, 'ok'); refreshStatus(); renderRisk(); }
    catch (e) { toast(e.message, 'err'); }
  };

  $('#risk-hard').innerHTML = `<pre class="code">${esc(JSON.stringify(r.hard_limits, null, 2))}</pre>
    <p class="muted">Enforced in code — no config, API, AI, LLM or MCP path can override these (§21/§29).</p>`;
  const stest = r.self_test || {};
  $('#risk-selftest').innerHTML = Object.entries(stest.checks || stest).map(([k, v]) => {
    const okk = v === true || (v && v.ok === true);
    return `<div class="check">${okk ? '<span class="yes">✔</span>' : '<span class="no">✘</span>'}<span>${esc(k)} ${esc(typeof v === 'object' ? JSON.stringify(v).slice(0, 80) : v)}</span></div>`;
  }).join('') || '<p class="muted">self-test unavailable</p>';
  $('#risk-events').innerHTML = table(['When', 'Kind', 'Market', 'Decision', 'State', 'Reasons'],
    (r.events || []).map(e => [ago(e.created_at), badge(e.kind, bcls(e.decision)), esc(e.market),
      badge(e.decision, bcls(e.decision)), esc(e.system_state || '—'), wrap(String(e.reasons || '').slice(0, 140))]));
}

// ---------------------------------------------------------------- RESEARCH
async function renderResearch() {
  const ai = await get('/ai/status');
  $('#res-ai').innerHTML = kvList({
    'Trained': ai.trained ? badge('TRAINED', 'ok') : badge('UNTRAINED', 'warn'),
    'Degraded': ai.degraded ? badge(`YES — ${ai.degraded_reason}`, 'fail') : badge('NO', 'ok'),
    'Strict mode': ai.strict_mode ? badge('ON — untrained ⇒ WAIT', 'ok') : badge('OFF', 'warn'),
    'Ensemble': esc(ai.ensemble_mode || ''), 'Features': esc(ai.feature_version),
    'Samples': fmt((ai.metrics || {}).samples, 0),
    'OOS accuracy': fmt((ai.metrics || {}).dir_accuracy_oos, 4),
    'OOS Brier (raw)': fmt((ai.metrics || {}).dir_brier_oos, 4),
    'OOS Brier (calibrated)': fmt((ai.metrics || {}).dir_brier_calibrated_oos, 4),
    'OOS ECE': fmt((ai.metrics || {}).dir_ece_oos, 4),
    'Return MAE (OOS)': fmt((ai.metrics || {}).return_mae_oos, 5),
    'Champion': ai.champion ? badge(`${ai.champion.name} ${ai.champion.version}`, 'ok') : badge('none', 'warn'),
  }) + (ai.importances && Object.keys(ai.importances).length
    ? `<h3 style="margin-top:10px">Top feature importances</h3><pre class="code">${esc(JSON.stringify(ai.importances, null, 1))}</pre>` : '');
  const dr = ai.drift || {};
  const checks = dr.checks || {};
  $('#res-drift').innerHTML = kvList({
    'Last action': badge(dr.action || 'none', dr.action === 'disabled' ? 'fail' : (dr.action === 'degraded' ? 'warn' : 'ok')),
    'When': ago(dr.ts),
  }) + Object.entries(checks).map(([k, v]) =>
    `<div class="check">${badge(v.status, bcls(v.status === 'ok' ? 'OK' : (v.status === 'warn' ? 'WAIT' : 'HALTED')))}<span>${esc(k)}: ${esc(JSON.stringify(v).slice(0, 110))}</span></div>`).join('');

  const m = await get('/models');
  $('#models-table').innerHTML = table(['ID', 'Name', 'Version', 'Lifecycle', 'Champion', 'Drift', 'Samples', 'OOS acc', 'Trained', ''],
    (m.models || []).map(x => {
      const met = x.metrics || {};
      return [String(x.id).slice(0, 12), esc(x.name), esc(x.version), badge(x.lifecycle, bcls(x.lifecycle)),
        x.champion ? badge('★', 'ok') : '—', badge(x.drift_status || 'none', bcls(x.drift_status === 'severe' ? 'HALTED' : 'OK')),
        fmt(x.samples, 0), fmt(met.dir_accuracy_oos, 4), ago(x.trained_at),
        html(x.champion ? '' : `<button class="btn small promote-m" data-id="${esc(x.id)}">Promote…</button>`)];
    }));
  $$('.promote-m').forEach(b => b.onclick = async () => {
    const r = await modal('Promote champion (manual §27)', `<p>Model <b>${esc(b.dataset.id)}</b>. Record evidence (OOS comparison, walk-forward ID…). Promotion is never automatic.</p>
      <textarea data-k="evidence" rows="4" placeholder='{"walk_forward":"wf-…","oos_delta":"+0.02"}'></textarea>
      <input data-k="actor" placeholder="your name (audited)">`, 'PROMOTE');
    if (!r || !r.inputs.actor) { if (r) toast('Actor name required for audit', 'err'); return; }
    let ev = {}; try { ev = JSON.parse(r.inputs.evidence || '{}'); } catch { ev = { note: r.inputs.evidence }; }
    try { const res = await post(`/models/${encodeURIComponent(b.dataset.id)}/promote`, { actor: r.inputs.actor, evidence: ev }); toast(res.ok ? `Champion promoted: ${res.champion}` : `Refused: ${res.error}`, res.ok ? 'ok' : 'err'); renderResearch(); }
    catch (e) { toast(e.message, 'err'); }
  });

  const ex = await get('/experiments?limit=25');
  $('#experiments-table').innerHTML = table(['When', 'Kind', 'Name', 'Status', 'Conclusion'],
    ex.map(e => [ago(e.created_at), badge(e.kind, 'info'), esc(e.name), badge(e.status, bcls(e.status === 'complete' ? 'OK' : 'WAIT')), wrap(String(e.conclusion || '').slice(0, 160))]));
}
$('#btn-train').onclick = async () => {
  const r = await modal('Train ensemble (§26)', `<p>Fetches real candles, builds features chronologically, trains 6 models with out-of-sample calibration. No data ⇒ honest "insufficient_data".</p>
    <input data-k="markets" placeholder="markets (comma separated, blank = top 10)">`, 'TRAIN');
  if (!r) return;
  toast('Training on real data… (can take a minute)', 'ok');
  try {
    const markets = r.inputs.markets ? r.inputs.markets.split(',').map(s => s.trim()).filter(Boolean) : null;
    const res = await post('/ai/train', { markets, horizon: 3 });
    toast(`Train: ${res.status} · samples ${res.samples} · OOS acc ${fmt(res.dir_accuracy_oos, 4)}`, res.status === 'trained' ? 'ok' : 'warn');
    renderResearch(); refreshStatus();
  } catch (e) { toast(e.message, 'err'); }
};
$('#btn-research').onclick = async () => {
  toast('Research pipeline running (discover→train→WF→costs→robustness→compare)…', 'ok');
  try { const r = await post('/research/run', {}); toast(`Research: ${String(r.conclusion || r.error || '').slice(0, 110)}`, r.ok ? 'ok' : 'warn'); renderResearch(); }
  catch (e) { toast(e.message, 'err'); }
};
$('#btn-backtest').onclick = async () => {
  const market = $('#bt-market').value.trim();
  if (!market) return toast('Enter a market', 'err');
  toast('Backtesting on real candles…', 'ok');
  try {
    const r = await post('/backtest/run', { market, starting: +$('#bt-starting').value || 10000 });
    renderBt(r);
  } catch (e) { toast(e.message, 'err'); }
};
$('#btn-wf').onclick = async () => {
  toast('Walk-forward running…', 'ok');
  try { const r = await post('/research/walk-forward', { n_folds: 4, horizon: 3 }); renderBt(r, 'Walk-forward'); }
  catch (e) { toast(e.message, 'err'); }
};
$('#btn-hyperopt').onclick = async () => {
  const market = $('#bt-market').value.trim();
  if (!market) return toast('Enter a market', 'err');
  toast('Hyperopt running (penalized objective)…', 'ok');
  try { const r = await post('/research/hyperopt', { market, method: 'random', n_trials: 8 }); renderBt(r, 'Hyperopt'); }
  catch (e) { toast(e.message, 'err'); }
};
function renderBt(r, title = 'Backtest') {
  const el = $('#bt-result');
  if (!r.ok) { el.innerHTML = `<h3 style="margin-top:10px">${title}</h3><p class="neg">${esc(r.error || 'failed')}</p>`; toast(r.error || 'failed', 'err'); return; }
  if (r.best !== undefined) { // hyperopt
    el.innerHTML = `<h3 style="margin-top:10px">Hyperopt · ${esc(r.market)} · ${r.trials} trials · ${esc(r.data && r.data.split || '')}</h3>
      ${kvList({ 'Best objective': fmt(r.best && r.best.obj, 4), 'Best params': wrap(JSON.stringify(r.best && r.best.params)), 'IS net': pct(r.best && r.best.net_is), 'OOS net': pct(r.best && r.best.net_oos), 'IS DD': pct(r.best && r.best.dd_is), 'OOS DD': pct(r.best && r.best.dd_oos), 'OOS gap': fmt(r.best && r.best.oos_gap, 4), 'Objective': wrap(r.objective_note), 'Promotion': wrap(r.promotion) })}`;
    toast(`Hyperopt best obj ${fmt(r.best && r.best.obj, 3)}`, 'ok'); return;
  }
  if (r.windows) { // walk-forward
    el.innerHTML = `<h3 style="margin-top:10px">Walk-forward · ${r.n_folds} folds · ${fmt(r.samples, 0)} real samples</h3>
      ${kvList({ 'OOS accuracy (mean)': fmt(r.oos_accuracy_mean, 4), 'OOS Brier (mean)': fmt(r.oos_brier_mean, 4), 'Stability': fmt(r.stability, 3), 'Champion OOS': fmt(r.champion_oos, 4), 'Recommend promotion': r.recommend_promotion ? badge('YES (manual step required)', 'warn') : badge('NO', 'ok'), 'Conclusion': wrap(r.conclusion) })}
      ${table(['Fold', 'Train n', 'OOS n', 'OOS acc', 'OOS Brier'], (r.windows || []).map(w => [fmt(w.fold, 0), fmt(w.train_samples, 0), fmt(w.oos_samples, 0), fmt(w.oos_accuracy, 4), fmt(w.oos_brier, 4)]))}`;
    toast(`WF OOS acc ${fmt(r.oos_accuracy_mean, 4)} stability ${fmt(r.stability, 2)}`, 'ok'); return;
  }
  el.innerHTML = `<h3 style="margin-top:10px">${title} · ${esc(r.market || (r.markets || []).join(', '))} · ${fmt(r.candles, 0)} real candles</h3>
    <div class="grid">
      ${card('Net return', pct((r.net_return_pct || 0) / 100), `gross ${fmt(r.gross_return_pct)}%`, r.net_return_pct >= 0 ? 'green' : 'red')}
      ${card('Max drawdown', fmt(r.max_dd_pct) + '%', '', 'red')}
      ${card('Profit factor', fmt(r.profit_factor), `${fmt(r.trades, 0)} trades · win ${fmt(r.win_rate, 1)}%`)}
      ${card('Sharpe / Sortino', `${fmt(r.sharpe)} / ${fmt(r.sortino)}`, `Calmar ${fmt(r.calmar)}`)}
      ${card('Expectancy', usd(r.expectancy), 'per trade, net of costs')}
      ${card('Costs paid', usd((r.fees || 0) + (r.slippage || 0) + (r.funding || 0)), `fees ${fmt(r.fees)} · slip ${fmt(r.slippage)} · funding ${fmt(r.funding)}`)}
    </div>
    ${kvList({ 'MAE avg': fmt(r.mae_avg, 5), 'MFE avg': fmt(r.mfe_avg, 5), 'Exit reasons': wrap(JSON.stringify(r.exit_reasons || {})), 'Note': wrap(r.honesty_note || r.note || '') })}`;
  toast(`Backtest: net ${fmt(r.net_return_pct)}% · DD ${fmt(r.max_dd_pct)}% · ${r.trades} trades`, 'ok');
}

// ---------------------------------------------------------------- SYSTEM
async function renderSystem() {
  const cfg = await get('/config');
  const editable = ['universe', 'risk_mode', 'max_positions', 'max_position_pct', 'max_total_exposure_pct',
    'risk_per_trade_pct', 'min_edge_pct', 'cycle_interval', 'candle_interval', 'candle_limit',
    'strict_ai_mode', 'ensemble_mode', 'derivs_enabled', 'ws_streaming', 'paper_starting_balance',
    'paper_participation_max', 'fee_bps', 'slippage_bps', 'backup_keep', 'api_token',
    'default_trading_venue', 'futures_leverage', 'futures_margin_mode', 'max_futures_leverage', 'futures_universe_scan'];
  $('#sys-config').innerHTML = `<div class="kv">${editable.filter(k => k in cfg).map(k => {
    const v = cfg[k];
    return `<div class="k">${esc(k)}</div><div class="v">${Array.isArray(v)
      ? `<input data-cfg="${k}" value="${esc(v.join(', '))}" style="width:100%">`
      : (typeof v === 'boolean' ? `<select data-cfg="${k}"><option ${v ? 'selected' : ''}>true</option><option ${!v ? 'selected' : ''}>false</option></select>`
        : `<input data-cfg="${k}" value="${esc(v)}" style="width:100%">`)}</div>`;
  }).join('')}</div>
  <div class="actions" style="margin-top:10px"><button class="btn primary" id="btn-save-cfg">Save config</button></div>
  <p class="muted">Privileged keys (stage, live_enabled, kill switch, venues_enabled) are refused here by design — use Risk tab.</p>`;
  $('#btn-save-cfg').onclick = async () => {
    const patch = {};
    $$('[data-cfg]').forEach(el => {
      const k = el.dataset.cfg, cur = cfg[k];
      let v = el.value;
      if (Array.isArray(cur)) v = v.split(',').map(s => s.trim()).filter(Boolean);
      else if (typeof cur === 'boolean') v = v === 'true';
      else if (typeof cur === 'number') v = +v;
      patch[k] = v;
    });
    try { const r = await post('/config', { patch }); toast(`Config saved: ${r.changed.join(', ') || 'no changes'}`, 'ok'); refreshStatus(); }
    catch (e) { toast(e.message, 'err'); }
  };

  const st = S.status || await get('/status');
  const venues = st.venues || {};
  $('#sys-venues').innerHTML = Object.entries(venues).map(([vid, v]) => `
    <div class="card" style="margin-bottom:8px">
      <div class="k">${esc(vid)} ${badge(v.status || '?', bcls(v.status))}</div>
      <div class="s">${esc(JSON.stringify((v.capabilities || {})).slice(0, 120))}</div>
      <div class="actions" style="margin-top:6px">
        <button class="btn small test-v" data-v="${vid}">Test connection</button>
        ${vid.includes('binance') || vid === 'zepay' ? `<button class="btn small creds-v" data-v="${vid}">Set credentials…</button>` : ''}
        ${v.enabled === false ? `<button class="btn small enable-v" data-v="${vid}">Enable venue…</button>`
          : `<button class="btn small disable-v" data-v="${vid}">Disable venue</button>`}
      </div>
    </div>`).join('');
  $$('.test-v').forEach(b => b.onclick = async () => {
    try { const r = await post('/venues/test', { venue: b.dataset.v }); toast(`${b.dataset.v}: ${r.connected ? 'CONNECTED' : 'NOT connected'} ${r.error || ''} ${r.withdrawal_refused ? '(withdrawal key refused ✔)' : ''}`, r.connected ? 'ok' : 'warn'); }
    catch (e) { toast(e.message, 'err'); }
  });
  $$('.creds-v').forEach(b => b.onclick = async () => {
    const r = await modal(`Credentials — ${b.dataset.v}`, `<p>Stored encrypted at rest (§52). Keys with withdrawal permission are REFUSED at test time. Paste nothing you are not willing to rotate.</p>
      <input data-k="api_key" placeholder="API key"><input data-k="api_secret" type="password" placeholder="API secret" style="margin-top:8px">`, 'SAVE');
    if (!r || !r.inputs.api_key) return;
    try { const res = await post('/venues/credentials', { venue: b.dataset.v, api_key: r.inputs.api_key, api_secret: r.inputs.api_secret }); toast(res.ok ? 'Credentials saved (encrypted)' : 'Failed', res.ok ? 'ok' : 'err'); refreshStatus(); renderSystem(); }
    catch (e) { toast(e.message, 'err'); }
  });

  const lic = await get('/license/status');
  const licPanel = $('#sys-license');
  if (licPanel) {
    licPanel.innerHTML = `
      <div class="kv">
        <div class="k">Status</div><div class="v">${lic.verified ? badge('VERIFIED', 'ok') : badge('NOT VERIFIED — trading gated', 'fail')}</div>
        <div class="k">Mode</div><div class="v">${esc(lic.mode || '—')}${lic.mode === 'offline' ? ' <span class="muted">(local format check — NOT cloud verification)</span>' : ''}</div>
        <div class="k">Cloud adapter</div><div class="v">${esc(lic.cloud_adapter)}</div>
        <div class="k">Cloud endpoint</div><div class="v">${esc(lic.cloud_endpoint || 'not configured')}</div>
      </div>
      <div class="actions" style="margin-top:8px">
        <input id="lic-key" placeholder="ZEPAY-XXXX-XXXX-…" style="flex:1">
        <button class="btn primary" id="btn-lic-verify">Verify &amp; activate</button>
        ${lic.verified ? '<button class="btn" id="btn-lic-clear">Deactivate</button>' : ''}
        <button class="btn" id="btn-lic-endpoint">Cloud endpoint…</button>
      </div>
      <p class="muted">The key itself is never stored — only its SHA-256 hash. Cloud verification activates automatically once a ZEPAY endpoint is configured; until then verification is local-only and clearly labeled.</p>`;
    $('#btn-lic-verify').onclick = async () => {
      const key = $('#lic-key').value.trim();
      if (!key) return toast('Enter your ZEPAY key first', 'err');
      try { const r = await post('/license/verify', { key, mode: null }); toast(r.ok ? `Key accepted (${r.mode})` : `Refused: ${r.message}`, r.ok ? 'ok' : 'err'); renderSystem(); }
      catch (e) { toast(e.message, 'err'); }
    };
    const clr = $('#btn-lic-clear');
    if (clr) clr.onclick = async () => {
      if (!await confirmDanger('Deactivate license', 'Trading will be gated until a key is verified again. Continue?')) return;
      try { await post('/license/clear', {}); toast('License deactivated — trading gated', 'warn'); renderSystem(); }
      catch (e) { toast(e.message, 'err'); }
    };
    $('#btn-lic-endpoint').onclick = async () => {
      const r = await modal('ZEPAY cloud endpoint', `<p>Only set this when ZEPAY publishes its verification API. Empty = offline mode.</p>
        <input data-k="endpoint" placeholder="https://…" value="${esc(lic.cloud_endpoint || '')}">`, 'SAVE');
      if (!r) return;
      try { await post('/license/endpoint', { endpoint: r.inputs.endpoint.trim() }); toast('Endpoint saved', 'ok'); renderSystem(); }
      catch (e) { toast(e.message, 'err'); }
    };
  }

  $$('.enable-v').forEach(b => b.onclick = async () => {
    const vid = b.dataset.v;
    const r = await modal(`Enable venue ${vid}`, `<p>Typed confirmation required. Enabling makes <b>${vid}</b> eligible for market routing (futures scanning, per-market routing). Live orders still require the stage gate, credentials and the Risk Engine.</p>
      <input data-k="confirm" placeholder="TYPE: ENABLE ${vid.toUpperCase()}">`, 'ENABLE');
    if (!r) return;  // cancelled
    if (!r.inputs.confirm || !r.inputs.confirm.trim()) { toast(`Type "ENABLE ${vid.toUpperCase()}" to confirm`, 'err'); return; }
    try { const res = await post(`/venues/${vid}/enable`, { confirm: r.inputs.confirm.trim() }); toast(res.ok ? `${vid} enabled for routing` : `Refused`, res.ok ? 'ok' : 'err'); renderSystem(); }
    catch (e) { toast(e.message, 'err'); }
  });
  $$('.disable-v').forEach(b => b.onclick = async () => {
    const vid = b.dataset.v;
    if (!await confirmDanger(`Disable ${vid}`, `Disable venue ${vid} for routing? Live execution on it will be refused.`)) return;
    try { const res = await post(`/venues/${vid}/disable`, {}); toast(`${vid} disabled`, 'warn'); renderSystem(); }
    catch (e) { toast(e.message, 'err'); }
  });

  const mcp = await get('/mcp');
  $('#sys-mcp').innerHTML = table(['Server', 'Status', 'Permissions', 'URL', 'Purpose', 'Calls/min', ''],
    (mcp.servers || []).map(s => [esc(s.id), badge(s.status, bcls(s.status)),
      wrap((s.permissions || []).join(', ') || '—'), wrap(s.url || 'NOT_CONFIGURED'),
      wrap(s.purpose || ''), fmt(s.rate_limit_per_min, 0),
      html(`<button class="btn small mcp-toggle" data-id="${esc(s.id)}" data-on="${s.enabled ? '1' : ''}">${s.enabled ? 'Disable' : 'Enable'}</button>`)])) +
    `<p class="muted">Forbidden permission classes (architectural): ${esc(Object.keys(mcp.forbidden_classes || {}).join(', '))}. All suggested servers ship DISABLED + UNCONFIGURED.</p>`;
  $$('.mcp-toggle').forEach(b => b.onclick = async () => {
    const id = b.dataset.id;
    const srv = (mcp.servers || []).find(s => s.id === id) || {};
    const r = await modal(`MCP server ${id}`, `<p>Registered permission classes: ${esc(Object.keys(mcp.permission_classes || {}).join(', '))}. Trading/withdrawal/credential classes are refused by construction.</p>
      <input data-k="url" placeholder="server URL (streamable HTTP)" value="${esc(srv.url || '')}">
      <input data-k="purpose" placeholder="purpose" value="${esc(srv.purpose || '')}" style="margin-top:8px">`, 'SAVE');
    if (!r) return;
    try {
      const res = await post('/mcp/servers', { id, url: r.inputs.url, purpose: r.inputs.purpose, permissions: srv.permissions || ['research.readonly'], enabled: b.dataset.on !== '1' && !!r.inputs.url, rate_limit_per_min: srv.rate_limit_per_min || 10 });
      toast(res.ok ? 'MCP server updated' : `Refused: ${res.error}`, res.ok ? 'ok' : 'err'); renderSystem();
    } catch (e) { toast(e.message, 'err'); }
  });

  const bk = await get('/backup/list');
  $('#sys-backups').innerHTML = `<div class="actions"><button class="btn" id="btn-backup">Run backup now</button></div>` +
    table(['File', 'Size (MB)', 'Created'], (bk.backups || []).map(b => [wrap(b.path), fmt(b.size_mb), esc(b.created)])) +
    `<p class="muted">${esc((bk.restore && bk.restore.note) || '')}</p>`;
  $('#btn-backup').onclick = async () => {
    try { const r = await post('/backup/run'); toast(r.ok ? `Backup OK: ${r.path} (${r.size_mb}MB, integrity ${r.integrity})` : `Backup FAILED: ${r.error}`, r.ok ? 'ok' : 'err'); renderSystem(); }
    catch (e) { toast(e.message, 'err'); }
  };

  const q = await get('/quality');
  const qs = q.summary || {};
  $('#sys-quality').innerHTML = kvList({
    'Overall': badge(qs.overall || '?', bcls(qs.overall)),
    'Status counts': wrap(JSON.stringify(qs.status_counts || {})),
    'Feed health': wrap(JSON.stringify(q.feed_health || {}).slice(0, 220)),
    'Notes': wrap((qs.notes || []).map(String).join(' · ').slice(0, 220)),
  }) + table(['When', 'Venue', 'Market', 'Kind', 'Detail'],
    (q.events || []).slice(0, 20).map(e => [ago(e.created_at), esc(e.venue || '—'), esc(e.market || '—'), badge(e.kind, 'warn'), wrap(String(e.detail || '').slice(0, 100))]));

  const audit = await get('/audit?limit=60');
  $('#sys-audit').innerHTML = table(['When', 'Category', 'Action', 'Actor', 'Details'],
    audit.map(a => [ago(a.created_at), badge(a.category, 'info'), esc(a.action), esc(a.actor || 'system'), wrap(String(a.details || '').slice(0, 140))]));
}

// ---------------------------------------------------------------- router/poller
const RENDER = { dashboard: renderDashboard, markets: renderMarkets, trading: renderTrading, risk: renderRisk, research: renderResearch, system: renderSystem };
async function refreshTab() {
  try { await (RENDER[S.tab] || renderDashboard)(); }
  catch (e) { console.error(e); toast(`${S.tab}: ${e.message}`, 'err'); }
}
setInterval(() => { refreshStatus().catch(() => { }); }, 6000);
setInterval(() => { refreshTab(); }, 15000);
(async function boot() {
  await refreshStatus();
  connectSSE();
  refreshTab();
})();
