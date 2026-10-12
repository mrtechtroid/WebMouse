/* webmouse web client. Apache License 2.0. */
(() => {
'use strict';

/* ------------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const now = () => performance.now();
const r2 = (v) => Math.round(v * 100) / 100;
const r4 = (v) => Math.round(v * 10000) / 10000;
const BLANK = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

const ICONS = {
  mouse: '<path d="M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z"/>',
  keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.001M10 8h.001M14 8h.001M18 8h.001M8 12h.001M12 12h.001M16 12h.001M7 16h10"/>',
  sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2m-7.07-15.07 1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
};
const icon = (name, size = 16) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
const injectIcons = () => $$('[data-icon]').forEach((el) => { el.innerHTML = icon(el.dataset.icon, el.closest('.brand') ? 20 : 16); });

let toastTimer = 0;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

/* ----------------------------------------------------------------- settings */
const STORE_KEY = 'webmouse.settings';
const FALLBACK = {
  theme: 'system', mode: 'relative', sensitivity: 1, acceleration: 0.5, scrollSpeed: 1,
  naturalScroll: true, tapToClick: true, penMode: 'direct', showButtons: true,
  haptics: true, keepAwake: true, stream: false, quality: 'balanced', monitor: 0,
};
let saved = {};
try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (e) { saved = {}; }
const settings = Object.assign({}, FALLBACK, saved);

function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(saved)); } catch (e) { /* private mode */ }
}
function set(key, value) {
  settings[key] = value;
  saved[key] = value;
  persist();
  onSetting(key);
  syncSettingsUI();
}
// Server config supplies defaults for anything this device has never changed.
function applyServerDefaults(ui) {
  const map = {
    theme: 'theme', mode: 'mode', sensitivity: 'sensitivity', acceleration: 'acceleration',
    scroll_speed: 'scrollSpeed', natural_scroll: 'naturalScroll', tap_to_click: 'tapToClick',
    pen_mode: 'penMode', show_buttons: 'showButtons', haptics: 'haptics', keep_awake: 'keepAwake',
  };
  Object.keys(ui || {}).forEach((k) => {
    const key = map[k];
    if (key && !(key in saved)) settings[key] = ui[k];
  });
}

/* -------------------------------------------------------------------- state */
const state = {
  ws: null, attempt: 0, hello: null, screen: null, monitors: [], platform: '',
  latency: null,
};
let reconnectTimer = 0;
let pingTimer = 0;
let activeTab = 'touchpad';

const isMac = () => state.platform === 'darwin';
const isWin = () => state.platform === 'win32';

function setStatus(kind, text) {
  const badge = $('#status');
  badge.dataset.state = kind;
  $('#status-text').textContent = text || { connecting: 'Connecting', connected: 'Connected', offline: 'Reconnecting' }[kind];
}

function send(obj) {
  const ws = state.ws;
  if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
  return false;
}

function haptic(ms = 8) {
  if (settings.haptics && navigator.vibrate) navigator.vibrate(ms);
}

/* -------------------------------------------------------------------- theme */
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme() {
  const dark = settings.theme === 'dark' || (settings.theme === 'system' && darkQuery.matches);
  document.documentElement.classList.toggle('dark', dark);
  $('#theme-icon').innerHTML = icon(dark ? 'sun' : 'moon');
}
if (darkQuery.addEventListener) {
  darkQuery.addEventListener('change', () => { if (settings.theme === 'system') applyTheme(); });
}

/* ------------------------------------------------------------ sign in / out */
function deviceGuess() {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) return 'iPad';
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? 'Android phone' : 'Android tablet';
  return 'Browser';
}
const storedName = () => { try { return localStorage.getItem('webmouse.name') || deviceGuess(); } catch (e) { return deviceGuess(); } };

function showLogin(message) {
  disconnect();
  $('#main').hidden = true;
  $('#login').hidden = false;
  const name = $('#login-name');
  if (!name.value) name.value = storedName();
  const err = $('#login-error');
  err.hidden = !message;
  err.textContent = message || '';
  setTimeout(() => $('#login-pin').focus(), 50);
}

async function login(pin, name) {
  const btn = $('#login-submit');
  btn.disabled = true;
  try {
    const res = await api('/api/login', { method: 'POST', body: JSON.stringify({ pin, name }) });
    if (res.ok) {
      try { localStorage.setItem('webmouse.name', res.data.name); } catch (e) { /* ignore */ }
      $('#login-pin').value = '';
      enterApp();
    } else {
      showLogin(res.data.error || 'Could not sign in.');
    }
  } catch (e) {
    showLogin('Cannot reach the computer. Check that webmouse is running and you are on the same network.');
  } finally {
    btn.disabled = false;
  }
}

function enterApp() {
  $('#login').hidden = true;
  $('#main').hidden = false;
  showTab('touchpad');
  connect();
}

/* ---------------------------------------------------------------- websocket */
function connect() {
  clearTimeout(reconnectTimer);
  if (state.ws) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  let opened = false;
  setStatus('connecting');

  ws.onopen = () => { opened = true; state.attempt = 0; };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    onServer(msg);
  };
  ws.onclose = async (ev) => {
    if (state.ws !== ws) return;
    state.ws = null;
    stopPing();
    resetPad();
    setStatus('offline');
    if (ev.code === 4401) { showLogin('You were signed out. Enter the PIN again.'); return; }
    if (!opened) {
      const res = await api('/api/session').catch(() => null);
      if (res && !res.data.authed) { showLogin('Session expired. Enter the PIN again.'); return; }
    }
    const delay = Math.min(4000, 400 * 2 ** state.attempt++);
    reconnectTimer = setTimeout(connect, delay);
  };
  ws.onerror = () => { /* onclose follows */ };
}

function disconnect() {
  clearTimeout(reconnectTimer);
  stopPing();
  const ws = state.ws;
  state.ws = null;
  if (ws) { try { ws.close(); } catch (e) { /* ignore */ } }
  applyStream();
}

function onServer(msg) {
  switch (msg.t) {
    case 'hello': onHello(msg); break;
    case 'pong':
      state.latency = Math.round(now() - msg.ts);
      updateServerInfo();
      break;
    case 'screen':
      state.screen = { w: msg.w, h: msg.h };
      layoutPad();
      applyStream();
      break;
    case 'renamed':
      toast('Name saved');
      if (activeTab === 'settings') loadDevices();
      break;
    default: break;
  }
}

function onHello(m) {
  state.hello = m;
  state.platform = m.platform;
  state.screen = m.screen;
  state.monitors = m.monitors || [];
  applyServerDefaults(m.ui);

  const st = m.stream;
  const streamOk = st.enabled && st.available;
  if (!streamOk) settings.stream = false;
  const sw = $('#stream-switch');
  sw.disabled = !streamOk;
  $('#stream-note').textContent = !st.enabled
    ? 'Screen streaming is turned off in the server config.'
    : !st.available
      ? `Screen streaming is not available on this computer: ${st.reason}`
      : 'Mirrors your computer screen inside the touchpad. Uses more battery and Wi-Fi.';

  buildMonitorSelect();
  buildMods();
  buildShortcuts();
  $('#device-name').value = m.name;
  updateServerInfo();
  setStatus('connected');
  startPing();

  if (settings.monitor && settings.monitor !== m.monitor && state.monitors.some((x) => x.i === settings.monitor)) {
    send({ t: 'monitor', i: settings.monitor });
  }
  applyTheme();
  syncSettingsUI();
  layoutPad();
  applyStream();
  keepAwake();
}

function startPing() {
  stopPing();
  const tick = () => send({ t: 'ping', ts: now() });
  tick();
  pingTimer = setInterval(tick, 2000);
}
function stopPing() { clearInterval(pingTimer); }

function updateServerInfo() {
  const h = state.hello;
  if (!h) return;
  const os = { win32: 'Windows', darwin: 'macOS', linux: 'Linux' }[h.platform] || h.platform;
  const lat = state.latency == null ? '' : ` Latency ${state.latency} ms.`;
  $('#server-info').textContent = `webmouse ${h.version} on ${os}, input via ${h.backend}.${lat}`;
}

/* ------------------------------------------------------------- pad geometry */
const pad = $('#pad');
const padArea = $('#pad-area');

function fitMode() { return settings.stream || settings.mode === 'absolute'; }

function layoutPad() {
  if (!fitMode()) { pad.style.width = '100%'; pad.style.height = '100%'; return; }
  const box = padArea.getBoundingClientRect();
  if (box.width < 1 || box.height < 1) return;
  const ratio = state.screen ? state.screen.w / state.screen.h : 16 / 9;
  let w = box.width;
  let h = w / ratio;
  if (h > box.height) { h = box.height; w = h * ratio; }
  pad.style.width = `${Math.floor(w)}px`;
  pad.style.height = `${Math.floor(h)}px`;
}
if (window.ResizeObserver) new ResizeObserver(layoutPad).observe(padArea);

function updateHint() {
  const hint = $('#pad-hint');
  if (settings.mode === 'relative') {
    hint.textContent = 'Drag to move. Tap to click. Tap with two fingers to right-click and drag with two fingers to scroll. Tap, then touch and drag to move items.';
  } else if (state.screen) {
    hint.textContent = 'This pad is your screen. Touch to move the pointer and tap to click.';
  } else {
    hint.textContent = 'Absolute mode needs the screen layout, which the server could not read on this system.';
  }
}

/* ------------------------------------------------------------- screen stream */
let streamErrors = 0;
let streamRetry = 0;
const streamImg = $('#stream');

function streamParams() {
  const st = state.hello.stream;
  const level = { low: [0.6, 0.5, 0.5], balanced: [0.85, 0.8, 0.75], high: [1, 1, 1] }[settings.quality] || [0.85, 0.8, 0.75];
  return {
    q: Math.max(10, Math.round(st.quality * level[0])),
    s: Math.max(0.1, r2(st.scale * level[1])),
    fps: Math.max(1, Math.round(st.fps * level[2])),
  };
}

function applyStream(resetErrors = true) {
  clearTimeout(streamRetry);
  const h = state.hello;
  const want = settings.stream && h && h.stream.enabled && h.stream.available &&
    state.ws && activeTab === 'touchpad' && !document.hidden && !$('#main').hidden;
  if (!want) {
    streamImg.hidden = true;
    if (streamImg.getAttribute('src') !== BLANK) streamImg.src = BLANK; // aborts the open request
    pad.classList.remove('live');
  } else {
    if (resetErrors) streamErrors = 0;
    const p = streamParams();
    streamImg.hidden = false;
    pad.classList.add('live');
    streamImg.src = `/stream.mjpg?m=${settings.monitor || h.monitor}&q=${p.q}&s=${p.s}&fps=${p.fps}&t=${Date.now()}`;
  }
  layoutPad();
}
streamImg.addEventListener('error', () => {
  if (!settings.stream || streamImg.getAttribute('src') === BLANK) return;
  if (++streamErrors > 3) {
    toast('The screen stream stopped. Check the terminal on your computer.');
    set('stream', false);
    return;
  }
  streamRetry = setTimeout(() => applyStream(false), 1500);
});

/* ------------------------------------------------------------ gesture engine */
const ptrs = new Map();          // active touch pointers
let g = null;                    // the current gesture
let lastTap = { t: 0, x: 0, y: 0 };
let penActiveUntil = 0;
let pen = null;
const dots = new Map();
const pending = { dx: 0, dy: 0, abs: null, sx: 0, sy: 0, zoom: 0 };
let raf = 0;

function schedule() { if (!raf) raf = requestAnimationFrame(flush); }
function flush() {
  raf = 0;
  if (pending.dx || pending.dy) { send({ t: 'm', x: r2(pending.dx), y: r2(pending.dy) }); pending.dx = pending.dy = 0; }
  if (pending.abs) { send({ t: 'a', x: r4(pending.abs.x), y: r4(pending.abs.y) }); pending.abs = null; }
  if (pending.sx || pending.sy) { send({ t: 's', x: r2(pending.sx), y: r2(pending.sy) }); pending.sx = pending.sy = 0; }
  if (pending.zoom) { send({ t: 'z', d: r2(pending.zoom) }); pending.zoom = 0; }
}
function flushNow() { if (raf) { cancelAnimationFrame(raf); raf = 0; } flush(); }

function queueMove(dx, dy) {
  const speed = Math.hypot(dx, dy);
  const gain = settings.sensitivity * (1 + settings.acceleration * Math.min(speed, 30) / 10);
  pending.dx += dx * gain;
  pending.dy += dy * gain;
  schedule();
}
function queueAbs(e) {
  const box = pad.getBoundingClientRect();
  if (box.width < 1 || box.height < 1) return;
  pending.abs = {
    x: clamp((e.clientX - box.left) / box.width, 0, 1),
    y: clamp((e.clientY - box.top) / box.height, 0, 1),
  };
  schedule();
}
// Finger deltas -> wheel deltas. Backend convention: y > 0 scrolls up, x > 0 scrolls right.
function queueScroll(fdx, fdy) {
  const speed = settings.scrollSpeed;
  pending.sy += (settings.naturalScroll ? fdy : -fdy) * speed;
  pending.sx += (settings.naturalScroll ? -fdx : fdx) * speed;
  schedule();
}
function sendButton(b, down) { flushNow(); send({ t: 'b', b, d: down ? 1 : 0 }); }
function sendClick(b, n = 1) { flushNow(); send({ t: 'c', b, n }); }

function showDot(e) {
  if (settings.mode !== 'absolute' && e.pointerType !== 'pen') return;
  let dot = dots.get(e.pointerId);
  if (!dot) {
    dot = document.createElement('div');
    dot.className = 'touch-dot';
    pad.appendChild(dot);
    dots.set(e.pointerId, dot);
  }
  moveDot(e);
}
function moveDot(e) {
  const dot = dots.get(e.pointerId);
  if (!dot) { if (e.pointerType === 'pen') showDot(e); return; }
  const box = pad.getBoundingClientRect();
  dot.style.transform = `translate(${e.clientX - box.left}px, ${e.clientY - box.top}px)`;
}
function hideDot(id) {
  const dot = dots.get(id);
  if (dot) { dot.remove(); dots.delete(id); }
}

function baseline() {
  const a = Array.from(ptrs.values());
  if (a.length < 2 || !g) return;
  g.cx = (a[0].x + a[1].x) / 2;
  g.cy = (a[0].y + a[1].y) / 2;
  g.dist = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
}

function onDown(e) {
  e.preventDefault();
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  const t = now();
  if (e.pointerType === 'pen') {
    penActiveUntil = t + 700;
    if (settings.penMode === 'direct') { penDown(e); return; }
  } else if (t < penActiveUntil) {
    return; // palm rejection while the stylus is in use
  }
  if (g && g.drag) return;
  try { pad.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  const p = { id: e.pointerId, x: e.clientX, y: e.clientY };
  ptrs.set(e.pointerId, p);
  showDot(e);

  if (ptrs.size === 1) {
    g = { start: t, max: 1, path: 0, mode: null, accMove: 0, accPinch: 0, cx: 0, cy: 0, dist: 0, drag: false };
    if (settings.mode === 'absolute') queueAbs(e);
    // Tap, then touch again and hold: drag.
    if (settings.tapToClick && t - lastTap.t < 300 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 40) {
      g.drag = true;
      lastTap.t = 0;
      sendButton('left', true);
    }
  } else if (g) {
    g.max = Math.max(g.max, ptrs.size);
    g.mode = null;
    g.accMove = g.accPinch = 0;
    baseline();
  }
}

function onMove(e) {
  if (e.pointerType === 'pen' && settings.penMode === 'direct') { penMove(e); return; }
  const p = ptrs.get(e.pointerId);
  if (!p || !g) return;
  const dx = e.clientX - p.x;
  const dy = e.clientY - p.y;
  p.x = e.clientX;
  p.y = e.clientY;
  g.path += Math.hypot(dx, dy);
  moveDot(e);

  if (ptrs.size === 1) {
    if (g.max > 1) return; // leftover finger after a multi-finger gesture
    if (settings.mode === 'absolute') queueAbs(e); else queueMove(dx, dy);
  } else if (ptrs.size === 2) {
    const a = Array.from(ptrs.values());
    const cx = (a[0].x + a[1].x) / 2;
    const cy = (a[0].y + a[1].y) / 2;
    const dist = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
    const dcx = cx - g.cx;
    const dcy = cy - g.cy;
    const dd = dist - g.dist;
    g.cx = cx; g.cy = cy; g.dist = dist;
    g.accMove += Math.hypot(dcx, dcy);
    g.accPinch += Math.abs(dd);
    if (!g.mode && Math.max(g.accMove, g.accPinch) > 10) {
      g.mode = g.accPinch > g.accMove * 1.2 ? 'pinch' : 'scroll';
    }
    if (g.mode === 'scroll') queueScroll(dcx, dcy);
    else if (g.mode === 'pinch') { pending.zoom += dd; schedule(); }
  }
}

function onUp(e) {
  if (e.pointerType === 'pen' && settings.penMode === 'direct') { penUp(e); return; }
  const p = ptrs.get(e.pointerId);
  if (!p) return;
  ptrs.delete(e.pointerId);
  hideDot(e.pointerId);
  try { pad.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  if (!g) return;
  if (ptrs.size > 0) { baseline(); return; }
  endGesture(e.type === 'pointercancel', p);
}

function endGesture(cancelled, p) {
  const gesture = g;
  g = null;
  flushNow();
  if (gesture.drag) { sendButton('left', false); return; }
  if (cancelled || !settings.tapToClick) return;
  const t = now();
  const quick = t - gesture.start < (gesture.max === 1 ? 300 : 380);
  const still = gesture.path < (gesture.max === 1 ? 12 : 26);
  if (quick && still) {
    const button = gesture.max === 1 ? 'left' : gesture.max === 2 ? 'right' : 'middle';
    sendClick(button);
    haptic();
    if (button === 'left') lastTap = { t, x: p.x, y: p.y };
  }
}

/* Stylus in "direct" mode: hover moves the pointer, contact presses the button. */
function penDown(e) {
  try { pad.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  const button = (e.buttons & 2) ? 'right' : (e.buttons & 32) ? 'middle' : 'left';
  queueAbs(e);
  flushNow();
  sendButton(button, true);
  pen = { id: e.pointerId, button };
  showDot(e);
  haptic(6);
}
function penMove(e) {
  penActiveUntil = now() + 700;
  queueAbs(e);
  moveDot(e);
}
function penUp(e) {
  if (pen && pen.id === e.pointerId) {
    queueAbs(e);
    flushNow();
    sendButton(pen.button, false);
    pen = null;
  }
  try { pad.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  if (e.type === 'pointercancel') hideDot(e.pointerId);
}

function resetPad() {
  if (g && g.drag) sendButton('left', false);
  if (pen) { sendButton(pen.button, false); pen = null; }
  ptrs.clear();
  g = null;
  dots.forEach((d) => d.remove());
  dots.clear();
  $$('.mbtn.pressed').forEach((b) => { b.classList.remove('pressed'); sendButton(b.dataset.btn, false); });
}

pad.addEventListener('pointerdown', onDown);
pad.addEventListener('pointermove', onMove);
pad.addEventListener('pointerup', onUp);
pad.addEventListener('pointercancel', onUp);
pad.addEventListener('pointerleave', (e) => { if (e.pointerType === 'pen' && !pen) hideDot(e.pointerId); });
pad.addEventListener('contextmenu', (e) => e.preventDefault());
['gesturestart', 'gesturechange', 'gestureend'].forEach((n) =>
  document.addEventListener(n, (e) => e.preventDefault()));

// On-screen mouse buttons. They work together with the pad, so you can hold
// Left with one finger and drag on the pad with another.
$$('.mbtn').forEach((btn) => {
  const name = btn.dataset.btn;
  btn.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    btn.classList.add('pressed');
    sendButton(name, true);
    haptic();
  });
  const release = () => {
    if (!btn.classList.contains('pressed')) return;
    btn.classList.remove('pressed');
    sendButton(name, false);
  };
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointercancel', release);
  btn.addEventListener('contextmenu', (e) => e.preventDefault());
});

/* ----------------------------------------------------------------- keyboard */
const modState = { ctrl: 0, alt: 0, shift: 0, meta: 0 }; // 0 off, 1 next key only, 2 locked
const modTapAt = {};

function activeMods() { return Object.keys(modState).filter((k) => modState[k] > 0); }
function renderMods() {
  $$('#mod-row .key').forEach((b) => {
    const k = b.dataset.mod;
    b.setAttribute('aria-pressed', String(modState[k] > 0));
    b.dataset.lock = String(modState[k] === 2);
  });
}
function clearOneShot() {
  let changed = false;
  Object.keys(modState).forEach((k) => { if (modState[k] === 1) { modState[k] = 0; changed = true; } });
  if (changed) renderMods();
}
function tapMod(k) {
  const t = now();
  if (modState[k] === 0) { modState[k] = 1; modTapAt[k] = t; }
  else if (modState[k] === 1 && t - modTapAt[k] < 450) modState[k] = 2;
  else modState[k] = 0;
  renderMods();
}

function sendKey(key, extra = [], count = 1) {
  const mods = Array.from(new Set(activeMods().concat(extra)));
  send({ t: 'key', k: key, mods, n: count });
  clearOneShot();
  haptic(5);
}

function typeText(text) {
  const mods = activeMods();
  if (!mods.length) { send({ t: 'text', s: text }); return; }
  Array.from(text).forEach((ch) => {
    if (ch.length === 1) send({ t: 'key', k: ch.toLowerCase(), mods });
  });
  clearOneShot();
}

function makeKey(def, className = '') {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `btn btn-outline key ${className}`.trim();
  b.textContent = def.l;
  if (def.a) b.setAttribute('aria-label', def.a);
  let delay = 0; let timer = 0; let repeating = false;
  const fire = () => sendKey(def.k, def.m || []);
  const stop = () => { clearTimeout(delay); clearInterval(timer); delay = timer = 0; };
  if (def.r) {
    b.addEventListener('pointerdown', () => {
      repeating = false;
      delay = setTimeout(() => { repeating = true; fire(); timer = setInterval(fire, 70); }, 450);
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach((n) => b.addEventListener(n, stop));
  }
  b.addEventListener('click', () => { if (repeating) { repeating = false; return; } fire(); });
  b.addEventListener('mousedown', (e) => e.preventDefault()); // keep the text field focused
  return b;
}
function fill(container, defs, className) {
  container.textContent = '';
  defs.forEach((d) => container.appendChild(makeKey(d, className)));
}

function buildKeyboard() {
  fill($('#key-main'), [
    { l: 'Esc', k: 'esc' }, { l: 'Tab', k: 'tab', r: 1 }, { l: 'Enter', k: 'enter', r: 1 },
    { l: 'Backspace', k: 'backspace', r: 1 }, { l: 'Delete', k: 'delete', r: 1 }, { l: 'Space', k: 'space', r: 1 },
  ]);
  fill($('#key-arrows'), [
    { l: '\u2191', k: 'up', r: 1, a: 'Up' }, { l: '\u2190', k: 'left', r: 1, a: 'Left' },
    { l: '\u2193', k: 'down', r: 1, a: 'Down' }, { l: '\u2192', k: 'right', r: 1, a: 'Right' },
  ]);
  fill($('#key-nav'), [
    { l: 'Home', k: 'home' }, { l: 'End', k: 'end' },
    { l: 'Page up', k: 'pageup', r: 1 }, { l: 'Page down', k: 'pagedown', r: 1 },
  ]);
  const fn = [];
  for (let i = 1; i <= 12; i++) fn.push({ l: `F${i}`, k: `f${i}` });
  fill($('#key-fn'), fn);
  fill($('#key-media'), [
    { l: 'Volume down', k: 'voldown', r: 1 }, { l: 'Volume up', k: 'volup', r: 1 }, { l: 'Mute', k: 'mute' },
    { l: 'Previous', k: 'prev' }, { l: 'Play/Pause', k: 'playpause' }, { l: 'Next', k: 'next' },
  ]);
  buildShortcuts();
  buildMods();
}

function buildShortcuts() {
  fill($('#key-shortcuts'), [
    { l: 'Copy', k: 'c', m: ['mod'] }, { l: 'Paste', k: 'v', m: ['mod'] }, { l: 'Cut', k: 'x', m: ['mod'] },
    { l: 'Undo', k: 'z', m: ['mod'] }, { l: 'Redo', k: 'z', m: ['mod', 'shift'] },
    { l: 'Select all', k: 'a', m: ['mod'] }, { l: 'Save', k: 's', m: ['mod'] }, { l: 'Find', k: 'f', m: ['mod'] },
    { l: 'Close tab', k: 'w', m: ['mod'] }, { l: 'Switch app', k: 'tab', m: [isMac() ? 'meta' : 'alt'] },
  ]);
}

function buildMods() {
  const row = $('#mod-row');
  row.textContent = '';
  const labels = {
    ctrl: 'Ctrl', alt: isMac() ? 'Option' : 'Alt', shift: 'Shift',
    meta: isMac() ? 'Cmd' : isWin() ? 'Win' : 'Super',
  };
  Object.keys(modState).forEach((k) => {
    const b = makeKey({ l: labels[k], k }, '');
    const fresh = b.cloneNode(true); // drop the default click handler
    fresh.dataset.mod = k;
    fresh.addEventListener('click', () => { tapMod(k); haptic(5); });
    fresh.addEventListener('mousedown', (e) => e.preventDefault());
    row.appendChild(fresh);
  });
  renderMods();
}

/* Live typing. The field holds a zero-width sentinel so Backspace always
   produces an input event, even when "empty". Each edit is diffed against the
   previous value, which also handles autocorrect and predictive text. */
const kb = $('#kb-input');
const SENTINEL = '\u200b';
let kbPrev = SENTINEL;
const kbStrip = (s) => s.split(SENTINEL).join('');

function kbReset() {
  kb.value = SENTINEL;
  kbPrev = SENTINEL;
  try { kb.setSelectionRange(1, 1); } catch (e) { /* ignore */ }
}

kb.addEventListener('input', (e) => {
  const before = Array.from(kbStrip(kbPrev));
  const after = Array.from(kbStrip(kb.value));
  kbPrev = kb.value;
  if (before.join('') === after.join('')) {
    if (e.inputType && e.inputType.indexOf('delete') === 0) send({ t: 'key', k: 'backspace', mods: [], n: 1 });
  } else {
    let i = 0;
    const limit = Math.min(before.length, after.length);
    while (i < limit && before[i] === after[i]) i++;
    const removed = before.length - i;
    if (removed > 0) send({ t: 'key', k: 'backspace', mods: [], n: removed });
    const added = after.slice(i).join('');
    if (added) typeText(added);
  }
  if (!e.isComposing) {
    if (kb.value.indexOf(SENTINEL) !== 0) { kb.value = SENTINEL + kb.value; kbPrev = kb.value; }
    if (after.length > 200) kbReset();
  }
});

const KB_KEYS = {
  Enter: 'enter', Tab: 'tab', Escape: 'esc', Delete: 'delete', Home: 'home', End: 'end',
  PageUp: 'pageup', PageDown: 'pagedown', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
};
kb.addEventListener('keydown', (e) => {
  if (e.isComposing || e.keyCode === 229) return;
  if (KB_KEYS[e.key]) {
    e.preventDefault();
    sendKey(KB_KEYS[e.key]);
    if (e.key === 'Enter') kbReset();
  } else if ((e.ctrlKey || e.metaKey || e.altKey) && e.key.length === 1) {
    e.preventDefault(); // hardware keyboard shortcuts go to the computer
    const mods = [];
    if (e.ctrlKey) mods.push('ctrl');
    if (e.metaKey) mods.push('meta');
    if (e.altKey) mods.push('alt');
    if (e.shiftKey) mods.push('shift');
    send({ t: 'key', k: e.key.toLowerCase(), mods, n: 1 });
  }
});
kb.addEventListener('focus', () => setTimeout(() => {
  try { kb.setSelectionRange(kb.value.length, kb.value.length); } catch (e) { /* ignore */ }
}, 0));

/* --------------------------------------------------------------- settings UI */
const fmtValue = (key, v) => (key === 'acceleration' ? `${Math.round(v * 100)}%` : `${Number(v).toFixed(1)}\u00d7`);

function syncSettingsUI() {
  $$('[data-setting]').forEach((el) => {
    const key = el.dataset.setting;
    const v = settings[key];
    if (el.type === 'range') {
      el.value = v;
      const out = $(`[data-value-for="${key}"]`);
      if (out) out.textContent = fmtValue(key, v);
    } else if (el.getAttribute('role') === 'switch') {
      el.setAttribute('aria-checked', String(!!v));
    } else if (el.classList.contains('toggle-group')) {
      $$('.toggle', el).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === String(v))));
    } else if (el.tagName === 'SELECT') {
      el.value = String(v || (state.hello && state.hello.monitor) || '');
    }
  });
  $('#mouse-buttons').hidden = !settings.showButtons;
  updateHint();
}

function bindSettings() {
  $$('[data-setting]').forEach((el) => {
    const key = el.dataset.setting;
    if (el.type === 'range') {
      el.addEventListener('input', () => set(key, parseFloat(el.value)));
    } else if (el.getAttribute('role') === 'switch') {
      el.addEventListener('click', () => set(key, el.getAttribute('aria-checked') !== 'true'));
    } else if (el.classList.contains('toggle-group')) {
      el.addEventListener('click', (e) => {
        const b = e.target.closest('.toggle');
        if (b) set(key, b.dataset.value);
      });
    } else if (el.tagName === 'SELECT') {
      el.addEventListener('change', () => set(key, parseInt(el.value, 10)));
    }
  });
}

function onSetting(key) {
  switch (key) {
    case 'theme': applyTheme(); break;
    case 'mode': resetPad(); layoutPad(); break;
    case 'penMode': resetPad(); break;
    case 'stream': case 'quality': applyStream(); break;
    case 'monitor': send({ t: 'monitor', i: settings.monitor }); break; // reply re-lays out the pad
    case 'keepAwake': keepAwake(); break;
    default: break;
  }
}

function buildMonitorSelect() {
  const sel = $('#s-monitor');
  sel.textContent = '';
  state.monitors.forEach((m) => {
    const o = document.createElement('option');
    o.value = String(m.i);
    o.textContent = `Monitor ${m.i} (${m.w}\u00d7${m.h})`;
    sel.appendChild(o);
  });
  $('#monitor-field').hidden = state.monitors.length < 2;
}

async function loadDevices() {
  const res = await api('/api/devices').catch(() => null);
  if (!res || !res.ok) return;
  const list = $('#devices');
  list.textContent = '';
  res.data.devices.forEach((d) => {
    const li = document.createElement('li');
    const meta = document.createElement('div');
    meta.className = 'meta';
    const name = document.createElement('div');
    name.className = 'name';
    const label = document.createElement('span');
    label.textContent = d.name; // textContent: names come from other devices
    name.appendChild(label);
    if (d.current) {
      const tag = document.createElement('span');
      tag.className = 'badge badge-solid';
      tag.textContent = 'This device';
      name.appendChild(tag);
    }
    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = `${d.ip}, ${d.online ? 'connected' : 'not connected'}`;
    meta.append(name, sub);
    li.appendChild(meta);
    if (!d.current) {
      const rm = document.createElement('button');
      rm.className = 'btn btn-ghost btn-sm';
      rm.textContent = 'Remove';
      rm.addEventListener('click', async () => {
        await api(`/api/devices/${encodeURIComponent(d.id)}/revoke`, { method: 'POST', body: '{}' });
        loadDevices();
      });
      li.appendChild(rm);
    }
    list.appendChild(li);
  });
}

/* --------------------------------------------------------------------- tabs */
function showTab(name) {
  activeTab = name;
  $$('.tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
  $$('main .panel').forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
  resetPad();
  if (name === 'touchpad') requestAnimationFrame(layoutPad);
  if (name === 'settings') loadDevices();
  applyStream();
}

/* ------------------------------------------------------------------ wake lock */
let wakeLock = null;
async function keepAwake() {
  try {
    const wanted = settings.keepAwake && 'wakeLock' in navigator && !document.hidden && !$('#main').hidden;
    if (wanted && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } else if (!wanted && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch (e) { wakeLock = null; }
}

/* ------------------------------------------------------------------- startup */
function bindMisc() {
  $('#login-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const pin = $('#login-pin').value.trim();
    if (pin) login(pin, $('#login-name').value.trim() || storedName());
  });
  $$('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));
  $('#theme-btn').addEventListener('click', () =>
    set('theme', document.documentElement.classList.contains('dark') ? 'light' : 'dark'));
  $('#device-save').addEventListener('click', () => {
    const name = $('#device-name').value.trim();
    if (!name) return;
    try { localStorage.setItem('webmouse.name', name); } catch (e) { /* ignore */ }
    send({ t: 'rename', name });
  });
  $('#logout-btn').addEventListener('click', async () => {
    await api('/api/logout', { method: 'POST', body: '{}' });
    showLogin();
  });
  const fs = $('#fullscreen-btn');
  if (document.documentElement.requestFullscreen) {
    fs.hidden = false;
    fs.addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen().catch(() => {});
    });
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { resetPad(); send({ t: 'release' }); }
    keepAwake();
    applyStream();
  });
}

async function boot() {
  injectIcons();
  applyTheme();
  buildKeyboard();
  kbReset();
  bindSettings();
  bindMisc();
  syncSettingsUI();

  // The QR code carries the PIN in the URL fragment, which is never sent to the server.
  const match = /[#&]pin=([^&]+)/.exec(location.hash);
  const fragmentPin = match ? decodeURIComponent(match[1]) : null;
  if (fragmentPin) history.replaceState(null, '', location.pathname + location.search);

  let session = null;
  try { session = await api('/api/session'); } catch (e) { /* offline */ }
  if (session && session.data.authed) { enterApp(); return; }
  showLogin();
  if (fragmentPin) {
    $('#login-pin').value = fragmentPin;
    await login(fragmentPin, $('#login-name').value.trim() || storedName());
  }
}

boot();
})();
