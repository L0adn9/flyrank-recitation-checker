// Recite front end: plain ES module, no build step. Talks to the JSON API on the same origin.

const $ = (sel, root = document) => root.querySelector(sel);
const MAX_SECONDS = 180;
const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";

// ---------- small helpers ----------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const arNum = (n) => String(n).replace(/\d/g, (d) => AR_DIGITS[d]);
const pct = (x) => `${Math.round(x * 100)}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const range = (a, b) => (a === b ? `${a}` : `${a}–${b}`);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function ago(iso) {
  const then = new Date(iso);
  const secs = (Date.now() - then.getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (secs < 60) return "just now";
  if (secs < 3600) return rtf.format(-Math.round(secs / 60), "minute");
  if (secs < 86400) return rtf.format(-Math.round(secs / 3600), "hour");
  if (secs < 7 * 86400) return rtf.format(-Math.round(secs / 86400), "day");
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4200);
}

// ---------- API ----------
let token = store.get("recite.token");

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", json, form, blob = false } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;
  let res;
  try {
    res = await fetch(path, { method, headers, body });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is it running?");
  }
  if (res.status === 401 && token) {
    signOut("Your session ended. Please sign in again.");
    throw new ApiError(401, "Signed out");
  }
  if (!res.ok) {
    let msg = `Something went wrong (${res.status})`;
    try { msg = (await res.json()).error || msg; } catch { /* not JSON */ }
    throw new ApiError(res.status, msg);
  }
  if (blob) return res.blob();
  if (res.status === 204) return null;
  const data = await res.json();
  data && typeof data === "object" && Object.defineProperty(data, "_status", { value: res.status });
  return data;
}

// ---------- state ----------
const state = {
  user: null,
  surahs: [],
  texts: new Map(), // surah number -> GET /surahs/{n}
  passage: { surah: 105, from: 1, to: 5 },
  result: null, // attempt shown in the page, or null
  rec: { status: "idle" }, // idle | connecting | live | finishing | busy (uploading)
  live: null, // while reciting: { words, progress, revealed }
};

// ---------- sign in ----------
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  $("#tab-login").setAttribute("aria-selected", mode === "login");
  $("#tab-signup").setAttribute("aria-selected", mode === "signup");
  $("#name-field").hidden = mode === "login";
  $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
  $("#auth-form").password.autocomplete = mode === "login" ? "current-password" : "new-password";
  $("#auth-error").hidden = true;
}

async function onAuthSubmit(e) {
  e.preventDefault();
  const f = e.target;
  const err = $("#auth-error");
  err.hidden = true;
  const payload = { email: f.email.value.trim(), password: f.password.value };
  if (authMode === "signup") {
    payload.name = f.name.value.trim();
    if (!payload.name) return showAuthError("Please enter your name.");
    if (payload.password.length < 8) return showAuthError("Use at least 8 characters for the password.");
  }
  if (!payload.email || !payload.password) return showAuthError("Enter your email and password.");
  const btn = $("#auth-submit");
  btn.disabled = true;
  try {
    const s = await api(authMode === "login" ? "/auth/login" : "/auth/signup", { method: "POST", json: payload });
    token = s.access_token;
    store.set("recite.token", token);
    f.reset();
    await enterApp(s.user);
  } catch (ex) {
    showAuthError(ex.message.replace(/^email: .*/, "Please enter a valid email address."));
  } finally {
    btn.disabled = false;
  }
}

function showAuthError(msg) {
  const err = $("#auth-error");
  err.textContent = msg;
  err.hidden = false;
}

function signOut(message) {
  token = null;
  store.del("recite.token");
  stopRecording(true);
  state.user = null;
  $("#app-view").hidden = true;
  $("#auth-view").hidden = false;
  setAuthMode("login");
  if (message) toast(message);
}

// ---------- passage picker & text ----------
async function surahText(n) {
  if (!state.texts.has(n)) state.texts.set(n, await api(`/surahs/${n}`));
  return state.texts.get(n);
}

function fillPicker() {
  const s = $("#surah");
  s.innerHTML = state.surahs
    .map((x) => `<option value="${x.number}">${x.number}. ${esc(x.name_en)}  ·  ${esc(x.meaning)}</option>`)
    .join("");
}

function fillAyahSelects() {
  const { surah, from, to } = state.passage;
  const count = state.surahs[surah - 1].ayah_count;
  $("#from").innerHTML = Array.from({ length: count }, (_, i) => `<option>${i + 1}</option>`).join("");
  const maxTo = Math.min(count, from + 19); // the API checks at most 20 ayahs at a time
  $("#to").innerHTML = Array.from({ length: maxTo - from + 1 }, (_, i) => `<option>${from + i}</option>`).join("");
  $("#surah").value = surah;
  $("#from").value = from;
  $("#to").value = to;
}

function defaultRange(surah, from = 1) {
  const count = state.surahs[surah - 1].ayah_count;
  return { surah, from, to: count <= 10 ? count : Math.min(count, from + 4) };
}

async function setPassage(p, { keepResult = false } = {}) {
  const count = state.surahs[p.surah - 1].ayah_count;
  const from = Math.max(1, Math.min(p.from, count));
  const to = Math.max(from, Math.min(p.to, count, from + 19));
  state.passage = { surah: p.surah, from, to };
  store.set("recite.passage", JSON.stringify(state.passage));
  fillAyahSelects();
  if (!keepResult) clearResult();
  await renderText();
}

const HAS_LETTER = /[\u0621-\u064A\u0671]/;

/** Mushaf words with their signs kept on them (same rule as the server's display_tokens). */
function wordsFromText(text, ayah) {
  const out = [];
  let lead = "";
  for (const t of text.split(/\s+/).filter(Boolean)) {
    if (HAS_LETTER.test(t)) { out.push(lead + t); lead = ""; }
    else if (out.length && t[0] >= "\u06D6" && t[0] <= "\u06DC") out[out.length - 1] += t;
    else if (out.length && t !== "۞") out[out.length - 1] += " " + t;
    else lead += t + " ";
  }
  return out.map((t) => ({ text: t, ayah, status: null }));
}

/** Render the passage: plain, with a result's marks on it, or live while reciting. */
async function renderText() {
  const { surah, from, to } = state.passage;
  const data = await surahText(surah);
  const r = state.result?.status === "done" ? state.result.result : null;
  const live = isLive() && state.live.words ? state.live : null;
  const hide = Boolean(live) && $("#hide-text").checked;
  let words, extras = [];
  if (r) {
    words = r.words;
    extras = r.extras;
  } else if (live) {
    const p = live.progress;
    words = live.words.map((w, i) => ({ ...w, i, status: p ? p.words[i].status : "pending", heard: p?.words[i].heard }));
    extras = [];   // extra words are only shown in the result, not while reciting
  } else {
    words = data.ayahs.filter((a) => a.ayah >= from && a.ayah <= to).flatMap((a) => wordsFromText(a.text, a.ayah));
  }
  const extrasAt = new Map();
  for (const e of extras) {
    if (!extrasAt.has(e.after)) extrasAt.set(e.after, []);
    extrasAt.get(e.after).push(e);
  }
  const extraHTML = (i) => (extrasAt.get(i) || []).map((e) => {
    const cls = e.kind === "repeat" ? "repeat" : e.kind === "moved" ? "moved" : "extra-word";
    const label = e.kind === "repeat" ? "You went back and said this again (not a mistake)" : e.kind === "moved" ? "Said here, out of order" : "Extra word, not in the text";
    const mark = e.kind === "extra" ? "+ " : e.kind === "repeat" ? "↺ " : "";
    return `<span class="extra ${cls}" title="${label}">${mark}${esc(e.words.join(" "))}</span>`;
  }).join(" ");

  // The mushaf writes some phrases as one word (يا قوم -> يَـٰقَوْمِ). Such words arrive as one word with
  // text and the rest with text "": show them as one, marked with the most serious status among them
  // (and live, not shown as said until all of it has been said).
  const rank = { wrong: 5, moved: 4, missed: 3, current: 2, pending: 1 };
  const units = [];
  words.forEach((w, i) => {
    const idx = w.i ?? i;
    const last = units[units.length - 1];
    if (w.text === "" && last) {
      if ((rank[w.status] || 0) > (rank[last.status] || 0)) last.status = w.status;
      if (w.heard) last.heard = [last.heard, w.heard].filter(Boolean).join(" ");
      last.idx = idx;
      return;
    }
    units.push({ ...w, idx });
  });

  const titles = { wrong: (u) => `Wrong: you said “${u.heard}”`, missed: () => "Missed", moved: () => "Out of order" };
  const nextAt = live ? units.findIndex((u) => u.status === "pending" || u.status === "current") : -1;
  const parts = [extraHTML(-1)];
  let shown = 0;
  units.forEach((u, k) => {
    const waiting = u.status === "pending" || u.status === "current";
    if (hide && waiting) {
      // Reciting from memory: words appear only once said. Mark the spot where the next word will go.
      if (k === nextAt) parts.push(u.status === "current" ? `<span class="hearing" aria-label="listening"></span>` : `<span class="caret" aria-hidden="true"></span>`);
      return;
    }
    let html = esc(u.text);
    if (u.status === "wrong" && !live && u.heard) html = `<ruby>${html}<rt>${esc(u.heard)}</rt></ruby>`;
    let cls = u.status && u.status !== "correct" ? ` ${u.status}` : "";
    if (k === nextAt) cls += " next";
    if (live && !waiting) {
      shown++;
      if (!live.revealed.has(u.idx)) { live.revealed.add(u.idx); cls += " appear"; }
    }
    const title = titles[u.status] ? ` title="${esc(titles[u.status](u))}"` : "";
    parts.push(`<span class="w${cls}"${title}>${html}</span>`);
    if (extrasAt.has(u.idx)) parts.push(extraHTML(u.idx));
    const next = units[k + 1];
    if (!next || next.ayah !== u.ayah) parts.push(`<span class="end" aria-label="end of ayah ${u.ayah}">\u06DD${arNum(u.ayah)}</span>`);
  });
  if (hide && !shown) parts.push(`<p class="live-hint">The words appear here as you recite them.</p>`);
  if (live) {
    const at = units[nextAt];
    const sub = $("#rec-sub");
    if (sub && state.rec.status === "live") {
      sub.textContent = nextAt < 0 ? "You reached the end of the passage. Press stop (or Space) for your result."
        : `Ayah ${at.ayah} of ${from}–${to}. ${hide ? "Recite from memory; the words appear as you say them." : "The highlighted word is where you are."}`;
    }
  }

  const meta = state.surahs[surah - 1];
  const showBasmala = data.basmala && from === 1;
  $("#mushaf").innerHTML = `
    <div class="surah-title"><span class="ar">${esc(meta.name_ar)}</span></div>
    <div class="surah-sub">${esc(meta.name_en)} · ${from === to ? "ayah" : "ayahs"} ${range(from, to)}</div>
    ${showBasmala ? `<div class="basmala">${esc(data.basmala)}</div>` : ""}
    <div class="verses">${parts.join(" ")}</div>`;
}

// ---------- recorder (live) ----------
// While you recite, the microphone audio streams to the server over a WebSocket (16 kHz PCM from an
// AudioWorklet). The server recognises each phrase as it is said and sends every word's status back,
// so the page marks words as they are recited. Stop (or finishing the passage) gives the full result.
const rec = { ws: null, stream: null, ctx: null, node: null, raf: 0, timer: 0, started: 0, chunks: [], queue: [], ready: false, url: null, done: false };
const isLive = () => state.rec.status === "live" || state.rec.status === "finishing";

function meterBars(n = 36) {
  $("#meter").innerHTML = "<i></i>".repeat(n);
}

function renderRecorder() {
  const s = state.rec.status;
  const btn = $("#rec-btn");
  const actions = $("#rec-actions");
  const on = s === "live" || s === "connecting";
  $("#rec-row").classList.toggle("recording-row", on);
  btn.classList.toggle("recording", on);
  btn.disabled = s === "busy" || s === "finishing";
  btn.innerHTML = s === "finishing" ? `<span class="spinner light"></span>` : `<svg><use href="#i-${on ? "stop" : "mic"}"/></svg>`;
  btn.setAttribute("aria-label", on ? "Stop and see the result" : "Start reciting");
  $("#meter").hidden = !on;
  const { surah, from, to } = state.passage;
  const name = state.surahs[surah - 1]?.name_en ?? "";
  if (s === "idle") {
    $("#rec-title").textContent = `Recite ${name} ${range(from, to)}`;
    $("#rec-sub").textContent = "Press the button and start reciting. Each word is checked as you say it.";
    actions.innerHTML = "";
  } else if (s === "connecting") {
    $("#rec-title").innerHTML = `Starting <span class="rec-time" id="rec-time">0:00</span>`;
    $("#rec-sub").textContent = "Connecting to the listener…";
    actions.innerHTML = `<button class="btn ghost small" id="rec-cancel">Cancel</button>`;
    $("#rec-cancel").onclick = () => stopRecording(true);
  } else if (s === "live") {
    $("#rec-title").innerHTML = `Listening <span class="rec-time" id="rec-time">0:00</span>`;
    actions.innerHTML = `<button class="btn ghost small" id="rec-cancel">Cancel</button>`;
    $("#rec-cancel").onclick = () => stopRecording(true);
  } else if (s === "finishing") {
    $("#rec-title").textContent = "Finishing";
    $("#rec-sub").textContent = "Checking the last words…";
    actions.innerHTML = "";
  } else if (s === "busy") {
    actions.innerHTML = "";
  }
}

function wavBlob(chunks) {
  const n = chunks.reduce((t, c) => t + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, t) => [...t].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) { new Int16Array(buf, o, c.length).set(c); o += c.length * 2; }
  return new Blob([buf], { type: "audio/wav" });
}

function releaseAudio() {
  clearInterval(rec.timer);
  cancelAnimationFrame(rec.raf);
  if (rec.node) rec.node.port.onmessage = null;
  rec.ctx?.close().catch(() => {});
  rec.stream?.getTracks().forEach((t) => t.stop());
  Object.assign(rec, { ctx: null, node: null, stream: null });
}

function resetRecorder() {
  releaseAudio();
  if (rec.ws) { rec.ws.onclose = rec.ws.onmessage = rec.ws.onerror = null; try { rec.ws.close(); } catch { /* closed */ } }
  Object.assign(rec, { ws: null, chunks: [], queue: [], ready: false, done: false });
  state.live = null;
  state.rec.status = "idle";
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode || !window.WebSocket) {
    toast("This browser can't check live. Upload a recording instead.");
    return;
  }
  // Ask for 16 kHz, so the browser resamples the mic properly; fall back to the device rate (the worklet
  // then filters and resamples) where a 16 kHz context can't take a microphone.
  let ctx;
  try { ctx = new AudioContext({ sampleRate: 16000 }); } catch { ctx = new AudioContext(); }
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    ctx.close();
    toast("Microphone access was blocked. Allow it in the browser, or upload a recording instead.");
    return;
  }
  clearResult();
  if (rec.url) { URL.revokeObjectURL(rec.url); rec.url = null; }
  rec.ctx = ctx;
  rec.chunks = []; rec.queue = []; rec.ready = false; rec.done = false;
  state.live = { words: null, progress: null, revealed: new Set() };
  state.rec.status = "connecting";
  meterBars();
  renderRecorder();
  await renderText();

  // The listener: a WebSocket to /live. The token goes in the first message (browsers can't set headers).
  const { surah, from, to } = state.passage;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live`);
  ws.binaryType = "arraybuffer";
  rec.ws = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "start", token, surah, ayah_from: from, ayah_to: to }));
  ws.onmessage = (e) => onLiveMessage(JSON.parse(e.data));
  ws.onerror = () => {};
  ws.onclose = () => {
    if (rec.ws !== ws || rec.done) return;
    resetRecorder();
    renderRecorder();
    renderText();
    toast("Lost the connection to the listener. Please try again.");
  };

  // Microphone -> 16 kHz PCM chunks (pcm-worklet.js) -> the socket, and a copy kept for playback.
  try {
    await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
  } catch {
    stopRecording(true);
    toast("This browser can't check live. Upload a recording instead.");
    return;
  }
  if (rec.ctx !== ctx) return; // cancelled meanwhile
  let source;
  try {
    source = ctx.createMediaStreamSource(rec.stream);
  } catch {
    // e.g. Firefox: a mic can't feed a context at another rate. Start again at the device rate.
    ctx.close();
    rec.ctx = ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
    source = ctx.createMediaStreamSource(rec.stream);
  }
  rec.node = new AudioWorkletNode(ctx, "pcm-16k");
  rec.node.port.onmessage = (e) => {
    const pcm = new Int16Array(e.data);
    rec.chunks.push(pcm);
    if (rec.ready && rec.ws?.readyState === WebSocket.OPEN) rec.ws.send(e.data);
    else rec.queue.push(e.data);
  };
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(rec.node).connect(mute).connect(ctx.destination);

  // Level meter: a rolling row of bars from the mic's loudness.
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const bars = [...$("#meter").children];
  const levels = new Array(bars.length).fill(0);
  let last = 0;
  const draw = (t) => {
    rec.raf = requestAnimationFrame(draw);
    if (t - last < 70) return;
    last = t;
    analyser.getFloatTimeDomainData(buf);
    const rms = Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length);
    levels.shift();
    levels.push(Math.min(1, rms * 7));
    bars.forEach((b, i) => (b.style.height = `${3 + levels[i] * 31}px`));
  };
  rec.raf = requestAnimationFrame(draw);
  rec.started = performance.now();
  rec.timer = setInterval(() => {
    const secs = Math.floor((performance.now() - rec.started) / 1000);
    const el = $("#rec-time");
    if (el) el.textContent = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    if (secs >= MAX_SECONDS) { toast("Stopped at the 3-minute limit."); stopRecording(); }
  }, 250);
}

async function onLiveMessage(m) {
  if (m.type === "ready") {
    rec.ready = true;
    state.live.words = m.words;
    for (const b of rec.queue) rec.ws.send(b);
    rec.queue = [];
    if (state.rec.status === "connecting") state.rec.status = "live";
    renderRecorder();
    await renderText();
  } else if (m.type === "progress") {
    if (!state.live) return;
    state.live.progress = m;
    await renderText();
  } else if (m.type === "complete") {
    // The whole passage has been recited: no need to press stop.
    if (state.rec.status === "live") setTimeout(() => stopRecording(), 400);
  } else if (m.type === "result") {
    rec.done = true;
    rec.url = rec.chunks.length ? URL.createObjectURL(wavBlob(rec.chunks)) : null;
    const url = rec.url;
    resetRecorder();
    rec.url = url;
    await showResult(m.attempt);
    refreshSide();
  } else if (m.type === "error") {
    rec.done = true;
    resetRecorder();
    if (m.status === 401) return signOut("Your session ended. Please sign in again.");
    renderRecorder();
    await renderText();
    toast(m.error);
  }
}

function stopRecording(cancel = false) {
  if (!["connecting", "live"].includes(state.rec.status)) return;
  if (cancel || !rec.ready) {
    if (rec.ws?.readyState === WebSocket.OPEN) rec.ws.send(JSON.stringify({ type: "cancel" }));
    resetRecorder();
    renderRecorder();
    renderText();
    return;
  }
  releaseAudio();
  rec.ws.send(JSON.stringify({ type: "stop" }));
  state.rec.status = "finishing";
  renderRecorder();
}

// ---------- checking ----------
function setStep(step) {
  const order = ["upload", "queued", "processing", "done"];
  const at = order.indexOf(step);
  document.querySelectorAll("#steps .step").forEach((el) => {
    const i = order.indexOf(el.dataset.step);
    el.classList.toggle("done", i < at || step === "done");
    el.classList.toggle("active", i === at && step !== "done");
  });
}

async function submit(blob, filename) {
  const { surah, from, to } = state.passage;
  const form = new FormData();
  form.append("surah", surah);
  form.append("ayah_from", from);
  form.append("ayah_to", to);
  form.append("audio", blob, filename);
  state.rec.status = "busy";
  $("#recorder").hidden = true;
  $("#steps").hidden = false;
  setStep("upload");
  try {
    let a = await api("/attempts", { method: "POST", form });
    const t0 = Date.now();
    while (a.status === "queued" || a.status === "processing") {
      setStep(a.status);
      if (Date.now() - t0 > 180000) throw new Error("This is taking unusually long. It will appear under Recent checks when done.");
      await sleep(700);
      a = await api(`/attempts/${a.id}`);
    }
    setStep("done");
    resetRecorder();
    if (rec.url) { URL.revokeObjectURL(rec.url); rec.url = null; }
    await showResult(a);
    refreshSide();
  } catch (ex) {
    if (ex.status === 401) return;
    toast(ex.message);
    state.rec.status = "idle";
    $("#recorder").hidden = false;
    $("#steps").hidden = true;
    renderRecorder();
  }
}

function clearResult() {
  state.result = null;
  $("#result-head").hidden = true;
  $("#result-head").innerHTML = "";
  $("#legend").hidden = true;
  $("#heard").hidden = true;
  $("#result-actions").hidden = true;
  $("#steps").hidden = true;
  $("#recorder").hidden = false;
  $("#playback").hidden = true;
  if (state.rec.status === "busy") state.rec.status = "idle";
  renderRecorder();
}

async function showResult(a) {
  state.result = a;
  $("#recorder").hidden = true;
  const play = $("#playback");
  play.hidden = !rec.url;
  if (rec.url) $("audio", play).src = rec.url;
  $("#steps").hidden = true;
  $("#result-actions").hidden = false;
  const head = $("#result-head");
  head.hidden = false;
  const when = new Date(a.created_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  if (a.status === "failed") {
    head.innerHTML = `<div class="banner">${esc(a.error || "This check failed.")}</div>`;
    $("#legend").hidden = true;
    $("#heard").hidden = true;
    await renderText();
    return;
  }
  const r = a.result;
  const hes = r.repeats;
  const speed = a.live ? "checked live"
    : a.cache_hit ? "instant: this exact recording was heard before"
    : a.recognition_seconds != null ? `listened in ${a.recognition_seconds.toFixed(1)} s` : "";
  head.innerHTML = `
    <div class="score">
      <div class="score-big num">${pct(a.accuracy)}<small>%</small></div>
      <div class="score-text">
        <b>${r.correct} of ${plural(r.total, "word")} right</b>${r.mistakes ? ` · ${plural(r.mistakes, "mistake")}` : " · no mistakes"}${hes ? ` · went back ${hes === 1 ? "once" : `${hes} times`}` : ""}
        <p>${esc(a.surah_name)} ${range(a.ayah_from, a.ayah_to)} · ${when}${speed ? ` · ${speed}` : ""}</p>
      </div>
    </div>
    <div class="ayah-dots" aria-label="Ayahs">${r.ayahs.map((x) =>
      `<span class="ayah-dot ${x.solid ? "solid" : "weak"}" title="Ayah ${x.ayah}: ${x.solid ? "solid" : `${plural(x.mistakes, "mistake")}${x.repeats ? `, went back ${x.repeats === 1 ? "once" : `${x.repeats} times`}` : ""}`}">${x.ayah}</span>`).join("")}</div>`;
  $("#legend").hidden = r.mistakes + r.repeats === 0;
  const heard = $("#heard");
  heard.hidden = !a.transcript;
  $("p", heard).textContent = a.transcript || "";
  await renderText();
}

// ---------- side column ----------
async function refreshSide() {
  await Promise.all([loadPlan(), loadRecent()]);
}

async function loadPlan() {
  const plan = await api("/revision/today");
  $("#plan-date").textContent = new Date(`${plan.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const ul = $("#plan");
  if (!plan.items.length) {
    ul.innerHTML = `<li><p class="empty">${plan.generated_at ? "Nothing is due today." : "No plan yet."} Every passage you check comes back here when it is time to revise it.</p></li>`;
    return;
  }
  ul.innerHTML = plan.items.map((it, k) => `
    <li><button class="plan-item${it.done ? " done" : ""}" data-k="${k}">
      <span class="pi-name">${esc(it.surah_name)} ${range(it.ayah_from, it.ayah_to)}</span>
      ${it.done ? `<svg class="check" aria-label="done"><use href="#i-check"/></svg>` : `<span class="pi-ar">${esc(it.surah_name_ar)}</span>`}
      <span class="pi-sub${it.weak && !it.done ? " weak" : ""}">${it.done ? "Done today" : esc(it.reason)}</span>
    </button></li>`).join("");
  ul.querySelectorAll(".plan-item").forEach((b) => (b.onclick = () => {
    const it = plan.items[b.dataset.k];
    setPassage({ surah: it.surah, from: it.ayah_from, to: it.ayah_to });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }));
}

async function loadRecent() {
  const list = await api("/attempts?limit=8");
  const ul = $("#recent");
  $("#recent-count").textContent = "";
  if (!list.length) {
    ul.innerHTML = `<li><p class="empty">Your checks will appear here.</p></li>`;
    return;
  }
  ul.innerHTML = list.map((a) => {
    const right = a.status === "done"
      ? `<span class="acc${a.accuracy < 0.9 ? " low" : ""}">${pct(a.accuracy)}%</span>`
      : `<span class="status-chip">${a.status === "failed" ? "failed" : "checking…"}</span>`;
    const sub = a.status === "done"
      ? `${ago(a.created_at)} · ${a.mistake_count ? plural(a.mistake_count, "mistake") : "no mistakes"}`
      : ago(a.created_at);
    return `<li><button class="recent-item" data-id="${a.id}">
      <span class="pi-name">${esc(a.surah_name)} ${range(a.ayah_from, a.ayah_to)}</span>${right}
      <span class="pi-sub">${sub}</span></button></li>`;
  }).join("");
  ul.querySelectorAll(".recent-item").forEach((b) => (b.onclick = () => openAttempt(+b.dataset.id)));
}

async function openAttempt(id) {
  try {
    const a = await api(`/attempts/${id}`);
    if (a.status === "queued" || a.status === "processing") return toast("Still checking this one. Give it a moment.");
    if (isLive() || state.rec.status === "connecting") stopRecording(true);
    resetRecorder();
    if (rec.url) { URL.revokeObjectURL(rec.url); rec.url = null; }
    await setPassage({ surah: a.surah, from: a.ayah_from, to: a.ayah_to }, { keepResult: true });
    await showResult(a);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (ex) {
    if (ex.status !== 401) toast(ex.message);
  }
}

async function makeReport() {
  const btn = $("#report-btn");
  const status = $("#report-status");
  btn.disabled = true;
  status.innerHTML = `<span class="spinner" style="display:inline-block;vertical-align:-3px"></span> Building…`;
  try {
    let r = await api("/reports", { method: "POST", json: { days: 30 } });
    const t0 = Date.now();
    while (r.status === "queued" || r.status === "processing") {
      if (Date.now() - t0 > 90000) throw new Error("The report is taking long. Try again in a minute.");
      await sleep(800);
      r = await api(`/reports/${r.id}`);
    }
    if (r.status === "failed") throw new Error(r.error || "Could not build the report.");
    const pdf = await api(r.download_url, { blob: true });
    const url = URL.createObjectURL(pdf);
    status.innerHTML = `<a href="${url}" download="recite-progress.pdf">Download PDF</a> · <a href="${url}" target="_blank" rel="noopener">open</a>`;
  } catch (ex) {
    status.textContent = "";
    if (ex.status !== 401) toast(ex.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------- boot ----------
async function enterApp(user) {
  state.user = user;
  $("#who-name").textContent = user.name;
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  if (!state.surahs.length) {
    state.surahs = await api("/surahs");
    fillPicker();
  }
  let p = null;
  try { p = JSON.parse(store.get("recite.passage")); } catch { /* none */ }
  await setPassage(p && p.surah ? p : defaultRange(105));
  refreshSide();
}

function wire() {
  $("#tab-login").onclick = () => setAuthMode("login");
  $("#tab-signup").onclick = () => setAuthMode("signup");
  $("#auth-form").onsubmit = onAuthSubmit;
  $("#sign-out").onclick = () => signOut();

  $("#surah").onchange = (e) => setPassage(defaultRange(+e.target.value));
  $("#from").onchange = (e) => {
    const from = +e.target.value;
    const to = Math.max(from, Math.min(state.passage.to, from + 19));
    setPassage({ ...state.passage, from, to });
  };
  $("#to").onchange = (e) => setPassage({ ...state.passage, to: +e.target.value });

  $("#rec-btn").onclick = () => (["live", "connecting"].includes(state.rec.status) ? stopRecording() : startRecording());
  $("#hide-text").onchange = (e) => {
    store.set("recite.hide", e.target.checked ? "1" : "0");
    if (isLive()) renderText();
  };
  $("#hide-text").checked = store.get("recite.hide") !== "0";
  $("#upload-btn").onclick = () => $("#file").click();
  $("#file").onchange = (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) return toast("That file is over 10 MB. Trim it to the passage you recited.");
    clearResult();
    submit(f, f.name);
  };

  $("#again").onclick = async () => { clearResult(); await renderText(); $("#rec-btn").focus(); };
  document.addEventListener("keydown", (e) => {
    // Space starts and stops reciting, unless typing or on a control.
    if (e.code !== "Space" || e.repeat || e.target.closest("input, select, textarea, button, audio, a")) return;
    if ($("#app-view").hidden || $("#recorder").hidden) return;
    e.preventDefault();
    $("#rec-btn").click();
  });
  $("#next-passage").onclick = () => {
    const { surah, to } = state.passage;
    const count = state.surahs[surah - 1].ayah_count;
    if (to < count) setPassage({ surah, from: to + 1, to: Math.min(count, to + 1 + (state.passage.to - state.passage.from)) });
    else if (surah < 114) setPassage(defaultRange(surah + 1));
  };
  $("#report-btn").onclick = makeReport;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.user) refreshSide().catch(() => {});
  });
}

async function boot() {
  wire();
  if (token) {
    try {
      return await enterApp(await api("/auth/me"));
    } catch (ex) {
      if (ex.status !== 401) {
        $("#auth-view").hidden = false;
        toast(ex.message);
        return;
      }
    }
  }
  $("#auth-view").hidden = false;
}

boot();
