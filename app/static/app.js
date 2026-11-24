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
  rec: { status: "idle" }, // idle | recording | recorded | busy
  peeked: false,
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

/** Render the passage, optionally with a result's marks on it. */
async function renderText() {
  const { surah, from, to } = state.passage;
  const data = await surahText(surah);
  const r = state.result?.status === "done" ? state.result.result : null;
  let words, extras = [];
  if (r) {
    words = r.words;
    extras = r.extras;
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
    const label = e.kind === "repeat" ? "Repeated" : e.kind === "moved" ? "Said here, out of order" : "Extra word, not in the text";
    return `<span class="extra ${cls}" title="${label}">${e.kind === "extra" ? "+ " : ""}${esc(e.words.join(" "))}</span>`;
  }).join(" ");

  // The mushaf writes some phrases as one word (يا قوم -> يَـٰقَوْمِ). Such words arrive as one word with
  // text and the rest with text "": show them as one, marked with the most serious status among them.
  const rank = { wrong: 3, moved: 2, missed: 1 };
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
  const parts = [extraHTML(-1)];
  units.forEach((u, k) => {
    let html = esc(u.text);
    if (u.status === "wrong") html = `<ruby>${html}<rt>${esc(u.heard)}</rt></ruby>`;
    const cls = u.status && u.status !== "correct" ? ` ${u.status}` : "";
    const title = titles[u.status] ? ` title="${esc(titles[u.status](u))}"` : "";
    parts.push(`<span class="w${cls}"${title}>${html}</span>`);
    if (extrasAt.has(u.idx)) parts.push(extraHTML(u.idx));
    const next = units[k + 1];
    if (!next || next.ayah !== u.ayah) parts.push(`<span class="end" aria-label="end of ayah ${u.ayah}">\u06DD${arNum(u.ayah)}</span>`);
  });

  const meta = state.surahs[surah - 1];
  const showBasmala = data.basmala && from === 1;
  $("#mushaf").innerHTML = `
    <div class="surah-title"><span class="ar">${esc(meta.name_ar)}</span></div>
    <div class="surah-sub">${esc(meta.name_en)} · ${from === to ? "ayah" : "ayahs"} ${range(from, to)}</div>
    ${showBasmala ? `<div class="basmala">${esc(data.basmala)}</div>` : ""}
    <div class="verses">${parts.join(" ")}</div>`;
}

// ---------- recorder ----------
const rec = { media: null, stream: null, chunks: [], ctx: null, raf: 0, timer: 0, started: 0, blob: null, url: null };

function meterBars(n = 36) {
  $("#meter").innerHTML = "<i></i>".repeat(n);
}

function setVeil(on) {
  $("#mushaf").classList.toggle("veiled", on);
  $("#veil").hidden = !on;
}

function renderRecorder() {
  const s = state.rec.status;
  const btn = $("#rec-btn");
  const actions = $("#rec-actions");
  $("#rec-row").classList.toggle("recording-row", s === "recording");
  btn.classList.toggle("recording", s === "recording");
  btn.disabled = s === "busy";
  btn.innerHTML = `<svg><use href="#i-${s === "recording" ? "stop" : "mic"}"/></svg>`;
  btn.setAttribute("aria-label", s === "recording" ? "Stop recording" : "Start recording");
  $("#meter").hidden = s !== "recording";
  const { surah, from, to } = state.passage;
  const name = state.surahs[surah - 1]?.name_en ?? "";
  if (s === "idle") {
    $("#rec-title").textContent = `Recite ${name} ${range(from, to)}`;
    $("#rec-sub").textContent = "Press the button and recite. Pause briefly between ayahs. Up to 3 minutes.";
    actions.innerHTML = "";
  } else if (s === "recording") {
    $("#rec-title").innerHTML = `Recording <span class="rec-time" id="rec-time">0:00</span>`;
    $("#rec-sub").textContent = "Press stop when you finish the last ayah.";
    actions.innerHTML = `<button class="btn ghost small" id="rec-cancel">Cancel</button>`;
    $("#rec-cancel").onclick = () => stopRecording(true);
  } else if (s === "recorded") {
    $("#rec-title").textContent = "Your recording";
    $("#rec-sub").innerHTML = `<audio controls src="${rec.url}"></audio>`;
    actions.innerHTML = `<button class="btn ghost small" id="rec-discard">Discard</button>
      <button class="btn primary" id="rec-send"><svg><use href="#i-check"/></svg>Check it</button>`;
    $("#rec-discard").onclick = () => { resetRecorder(); renderRecorder(); setVeil(false); };
    $("#rec-send").onclick = () => submit(rec.blob, `recitation.${extFor(rec.blob.type)}`);
  } else if (s === "busy") {
    actions.innerHTML = "";
  }
}

function extFor(type) {
  if (type.includes("ogg")) return "ogg";
  if (type.includes("mp4") || type.includes("aac")) return "m4a";
  return "webm";
}

function resetRecorder() {
  if (rec.url) URL.revokeObjectURL(rec.url);
  Object.assign(rec, { blob: null, url: null, chunks: [] });
  state.rec.status = "idle";
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    toast("This browser can't record audio. Upload a recording instead.");
    return;
  }
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    toast("Microphone access was blocked. Allow it in the browser, or upload a recording instead.");
    return;
  }
  clearResult();
  await renderText();
  resetRecorder();
  rec.media = new MediaRecorder(rec.stream);
  rec.media.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
  rec.media.onstop = onRecorded;
  rec.media.start(250);
  rec.started = performance.now();
  state.rec.status = "recording";
  state.peeked = false;
  setVeil($("#hide-text").checked);
  meterBars();
  renderRecorder();

  // Live level meter: a rolling row of bars from the mic's RMS level.
  rec.ctx = new AudioContext();
  const analyser = rec.ctx.createAnalyser();
  analyser.fftSize = 1024;
  rec.ctx.createMediaStreamSource(rec.stream).connect(analyser);
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
  rec.timer = setInterval(() => {
    const secs = Math.floor((performance.now() - rec.started) / 1000);
    const el = $("#rec-time");
    if (el) el.textContent = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    if (secs >= MAX_SECONDS) { toast("Stopped at the 3-minute limit."); stopRecording(); }
  }, 250);
}

function stopRecording(cancel = false) {
  if (state.rec.status !== "recording") return;
  rec.cancelled = cancel;
  clearInterval(rec.timer);
  cancelAnimationFrame(rec.raf);
  rec.ctx?.close();
  rec.media?.state !== "inactive" && rec.media.stop();
  rec.stream?.getTracks().forEach((t) => t.stop());
}

function onRecorded() {
  if (rec.cancelled) {
    resetRecorder();
    setVeil(false);
    renderRecorder();
    return;
  }
  rec.blob = new Blob(rec.chunks, { type: rec.media.mimeType || "audio/webm" });
  rec.url = URL.createObjectURL(rec.blob);
  state.rec.status = "recorded";
  renderRecorder();
  if ((performance.now() - rec.started) / 1000 < 1.2) toast("That was very short. Record the whole passage.");
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
    await showResult(a);
    refreshSide();
  } catch (ex) {
    if (ex.status === 401) return;
    toast(ex.message);
    state.rec.status = rec.blob ? "recorded" : "idle";
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
  setVeil(false);
  if (state.rec.status === "busy") state.rec.status = "idle";
  renderRecorder();
}

async function showResult(a) {
  state.result = a;
  setVeil(false);
  $("#recorder").hidden = true;
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
  const speed = a.cache_hit ? "instant: this exact recording was heard before"
    : a.recognition_seconds != null ? `listened in ${a.recognition_seconds.toFixed(1)} s` : "";
  head.innerHTML = `
    <div class="score">
      <div class="score-big num">${pct(a.accuracy)}<small>%</small></div>
      <div class="score-text">
        <b>${r.correct} of ${plural(r.total, "word")} right</b>${r.mistakes ? ` · ${plural(r.mistakes, "mistake")}` : " · no mistakes"}${hes ? ` · ${plural(hes, "hesitation")}` : ""}
        <p>${esc(a.surah_name)} ${range(a.ayah_from, a.ayah_to)} · ${when}${speed ? ` · ${speed}` : ""}</p>
      </div>
    </div>
    <div class="ayah-dots" aria-label="Ayahs">${r.ayahs.map((x) =>
      `<span class="ayah-dot ${x.solid ? "solid" : "weak"}" title="Ayah ${x.ayah}: ${x.solid ? "solid" : `${plural(x.mistakes, "mistake")}${x.repeats ? `, ${plural(x.repeats, "hesitation")}` : ""}`}">${x.ayah}</span>`).join("")}</div>`;
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
    if (state.rec.status === "recording") stopRecording(true);
    resetRecorder();
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

  $("#rec-btn").onclick = () => (state.rec.status === "recording" ? stopRecording() : startRecording());
  $("#peek").onclick = () => { state.peeked = true; setVeil(false); };
  $("#hide-text").onchange = (e) => {
    store.set("recite.hide", e.target.checked ? "1" : "0");
    if (state.rec.status === "recording" && !state.peeked) setVeil(e.target.checked);
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
