// Jarvis phone companion. Talks only to /api/phone/*: the phone holds an HttpOnly cookie it cannot
// read (set when a pairing code is redeemed) and a CSRF token kept in memory. No long-lived
// credential ever touches localStorage or a URL.
"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;
const IDLE_POLL_MS = 4000;
const RESUME_SLOT = "jm_uploads"; // {fileKey: {id, sha}} - no secret, only lets a retry continue

const state = { csrf: "", device: null, sid: "", timer: 0, pending: [], busy: false, sig: "", rec: null,
  tab: "chat", staged: [], chunk: 4 * 1024 * 1024, maxFile: 0, uploads: new Map(), listTimer: 0 };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.csrf) headers["X-Phone-CSRF"] = state.csrf;
  let body = options.body;
  if (body && typeof body !== "string" && !(body instanceof Blob) && !(body instanceof FormData)) {
    body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...options, headers, body, credentials: "same-origin" });
  if (res.status === 401) {
    const error = new Error("not paired");
    error.auth = true;
    throw error;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : (data.detail && data.detail.message) || res.statusText;
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  return data;
}

function show(view) {
  const chat = view === "chat";
  $("composer").classList.toggle("hide", !chat);
  $("back").classList.toggle("hide", !chat);
  $("logout").classList.toggle("hide", view === "pair");
  $("tabs").classList.toggle("hide", view === "pair");
  if (!chat) { $("status").classList.add("hide"); }
  renderChips();
}

function lost() {
  state.csrf = "";
  state.device = null;
  stopPolling();
  renderPair("Sessione scaduta o dispositivo revocato: collega di nuovo il telefono.");
}

function fail(error, retry) {
  if (error.auth) return lost();
  const main = $("main");
  main.replaceChildren(el("p", "msg err", `Errore: ${error.message}`));
  const again = el("button", "", "Riprova");
  again.onclick = retry;
  main.append(again);
}

// ----------------------------------------------------------------- pairing
function renderPair(message) {
  stopPolling();
  clearInterval(state.listTimer);
  show("pair");
  $("title").textContent = "Jarvis";
  const main = $("main");
  main.replaceChildren();
  main.append(el("p", "", "Sul computer apri /m/pair.html per ottenere il codice di collegamento (vale 5 minuti, una volta sola)."));
  const code = el("input");
  code.type = "text";
  code.autocomplete = "off";
  code.autocapitalize = "characters";
  code.placeholder = "Codice (XXXX-XXXX)";
  const name = el("input");
  name.type = "text";
  name.placeholder = "Nome del telefono";
  name.value = "iPhone";
  const go = el("button", "primary", "Collega");
  const note = el("p", "act", message || "");
  go.onclick = async () => {
    if (!code.value.trim()) return;
    go.disabled = true;
    try {
      const out = await api("/api/phone/pair/redeem", { method: "POST", body: { code: code.value, name: name.value } });
      state.csrf = out.csrf;
      state.device = out.device;
      start();
    } catch (error) {
      note.textContent = error.message || "Collegamento non riuscito.";
      go.disabled = false;
    }
  };
  main.append(code, name, go, note);
}

async function boot() {
  try {
    const me = await api("/api/phone/me");
    state.csrf = me.csrf;
    state.device = me.device;
    start();
  } catch (error) {
    if (error.auth) renderPair(); else renderPair(`Non raggiungo Jarvis (${error.message}).`);
  }
}

function start() { setTab(state.tab); }

function setTab(tab) {
  state.tab = tab;
  $("tab-chat").classList.toggle("on", tab === "chat");
  $("tab-files").classList.toggle("on", tab === "files");
  clearInterval(state.listTimer);
  if (tab === "files") { stopPolling(); state.sid = ""; renderFiles(); } else { renderList(); }
}

// -------------------------------------------------------------------- chats
async function renderList() {
  stopPolling();
  state.sid = "";
  show("list");
  $("title").textContent = "Chat";
  const main = $("main");
  main.replaceChildren(el("p", "act", "Carico…"));
  let sessions;
  try {
    sessions = (await api("/api/phone/chats")).sessions || [];
  } catch (error) { return fail(error, renderList); }
  main.replaceChildren();
  const fresh = el("button", "primary", "Nuova chat");
  fresh.style.cssText = "width:100%;margin-bottom:10px";
  fresh.onclick = newChat;
  main.append(fresh);
  if (!sessions.length) main.append(el("p", "act", "Nessuna chat ancora."));
  for (const session of sessions) {
    const row = el("div", "row");
    row.append(el("div", "", session.title || session.preview || "(senza titolo)"));
    const meta = [session.provider, session.running ? "al lavoro" : ""];
    if (session.pending_approvals) meta.push(`${session.pending_approvals} da approvare`);
    row.append(el("small", "", meta.filter(Boolean).join(" · ")));
    row.onclick = () => openChat(session.session_id, session.title);
    main.append(row);
  }
}

async function newChat() {
  try {
    const made = await api("/api/phone/chats", { method: "POST", body: {} });
    openChat(made.session_id || (made.session && made.session.session_id), "Nuova chat");
  } catch (error) {
    if (error.auth) return lost();
    alert(error.message);
  }
}

function openChat(sid, title) {
  state.sid = sid;
  state.sig = "";
  state.pending = [];
  state.staged = [];
  show("chat");
  $("title").textContent = title || "Chat";
  $("main").replaceChildren();
  poll();
}

function stopPolling() { clearTimeout(state.timer); state.timer = 0; }

async function poll() {
  stopPolling();
  const sid = state.sid;
  if (!sid || state.tab !== "chat") return;
  let running = false;
  try {
    const data = await api(`/api/phone/chats/${encodeURIComponent(sid)}`);
    if (sid !== state.sid) return;
    running = !!(data.session && data.session.running);
    if (data.session && data.session.title) $("title").textContent = data.session.title;
    renderEvents(data.events || [], running);
  } catch (error) {
    if (error.auth) return lost();
    $("statustext").textContent = `Connessione persa (${error.message}), riprovo…`;
    $("status").classList.remove("hide");
  }
  if (sid === state.sid) state.timer = setTimeout(poll, running || state.pending.length ? POLL_MS : IDLE_POLL_MS);
}

function renderEvents(events, running) {
  const last = events.length ? events[events.length - 1].seq : -1;
  const resolved = new Set(events.filter((e) => e.kind === "approval_resolved").map((e) => e.payload.approval_id));
  state.pending = events.filter((e) => e.kind === "approval_required" && !resolved.has(e.payload.approval_id));
  const sig = `${last}:${state.pending.map((e) => e.payload.approval_id).join(",")}:${running}`;
  if (sig === state.sig) return;
  state.sig = sig;
  const main = $("main");
  const stick = main.scrollHeight - main.scrollTop - main.clientHeight < 80;
  main.replaceChildren();
  for (const event of events) {
    const p = event.payload || {};
    if (event.kind === "user_message") main.append(el("div", "msg user", String(p.typed || p.text || "")));
    else if (event.kind === "assistant_text") main.append(el("div", "msg bot", String(p.text || "")));
    else if (event.kind === "tool_call") main.append(el("div", "act", `⚙ ${p.name || "strumento"}`));
    else if (event.kind === "error") main.append(el("div", "msg bot err", String(p.message || "Errore")));
    else if (event.kind === "turn_finished" && p.status === "cancelled") main.append(el("div", "act", "Fermato."));
  }
  for (const event of state.pending) main.append(approvalCard(event.payload));
  if (stick) main.scrollTop = main.scrollHeight;
  const working = running || state.pending.length > 0;
  $("status").classList.toggle("hide", !working);
  $("statustext").textContent = state.pending.length ? "Aspetta il tuo ok" : "Jarvis sta lavorando…";
  $("stop").classList.toggle("hide", !running);
}

function approvalCard(p) {
  const card = el("div", "card");
  card.append(el("strong", "", "Approvare?"));
  card.append(el("div", "", p.summary || p.name || "Azione"));
  let detail = "";
  try { detail = p.input ? JSON.stringify(p.input, null, 2) : ""; } catch { detail = ""; }
  if (detail) card.append(el("pre", "", detail.slice(0, 1500)));
  const row = el("div", "btns");
  for (const [decision, label, cls] of [["allow", "Approva", "primary"], ["deny", "Nega", "danger"], ["allow_always", "Sempre", ""]]) {
    const button = el("button", cls, label);
    button.onclick = async () => {
      row.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await api(`/api/phone/chats/${encodeURIComponent(state.sid)}/approvals/${encodeURIComponent(p.approval_id)}`, {
          method: "POST", body: { decision },
        });
      } catch (error) {
        if (error.auth) return lost();
        if (error.status !== 410 && error.status !== 404) alert(error.message);
      }
      poll();
    };
    row.append(button);
  }
  card.append(row);
  return card;
}

function renderChips() {
  const chips = $("chips");
  const items = state.tab === "chat" && state.sid ? state.staged : [];
  chips.replaceChildren(...items.map((s) => {
    const chip = el("span", "chip", s.error ? `${s.name} · errore` : s.ready ? `${s.name} ✓` : `${s.name} · ${s.pct || 0}%`);
    chip.onclick = () => { state.staged = state.staged.filter((x) => x !== s); renderChips(); };
    return chip;
  }));
  chips.classList.toggle("hide", !items.length);
}

async function send() {
  const input = $("input");
  const text = input.value.trim();
  const ids = state.staged.filter((s) => s.ready).map((s) => s.id);
  if (state.busy || (!text && !ids.length)) return;
  if (state.staged.some((s) => !s.ready && !s.error)) { alert("Aspetta che i file finiscano di caricarsi."); return; }
  state.busy = true;
  $("send").disabled = true;
  try {
    await api(`/api/phone/chats/${encodeURIComponent(state.sid)}/messages`, {
      method: "POST", body: { text, file_ids: ids, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    });
    input.value = "";
    input.style.height = "";
    state.staged = [];
    renderChips();
    poll();
  } catch (error) {
    if (error.auth) return lost();
    alert(error.message);
  } finally {
    state.busy = false;
    $("send").disabled = false;
  }
}

// -------------------------------------------------------------------- files
function resumeMap() {
  try { return JSON.parse(localStorage.getItem(RESUME_SLOT) || "{}"); } catch { return {}; }
}
function saveResume(map) {
  try { localStorage.setItem(RESUME_SLOT, JSON.stringify(map)); } catch { /* private mode */ }
}
const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;

async function hashFile(file, onProgress) {
  const hash = new Sha256();
  for (let at = 0; at < file.size; at += 2 * 1024 * 1024) {
    hash.update(new Uint8Array(await file.slice(at, at + 2 * 1024 * 1024).arrayBuffer()));
    onProgress(Math.min(1, (at + 2 * 1024 * 1024) / file.size));
  }
  return hash.hex();
}

/** Upload one file, resuming a previous attempt when the server still has it. */
async function uploadFile(file, view) {
  view.status = "Preparo…";
  view.update();
  if (state.maxFile && file.size > state.maxFile) throw new Error(`Il file supera il limite (${Math.round(state.maxFile / 2 ** 20)} MB)`);
  const sha = await hashFile(file, (f) => { view.pct = Math.round(f * 10); view.update(); });
  const map = resumeMap();
  const key = fileKey(file);
  let id = "";
  let offset = 0;
  const known = map[key];
  if (known && known.sha === sha) {
    try {
      const st = await api(`/api/phone/files/${known.id}`);
      if (!st.ready) { id = known.id; offset = st.offset; } else { id = known.id; offset = st.size; }
    } catch (error) {
      if (error.auth) throw error;
    }
  }
  if (!id) {
    const made = await api("/api/phone/files", { method: "POST", body: { name: file.name, size: file.size, sha256: sha } });
    id = made.id;
    state.chunk = made.chunk_size || state.chunk;
    map[key] = { id, sha };
    saveResume(map);
  }
  view.id = id;
  let failures = 0;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + state.chunk);
    try {
      const out = await api(`/api/phone/files/${id}/chunk?offset=${offset}`, { method: "PUT", body: file.slice(offset, end) });
      offset = out.offset;
      failures = 0;
      view.pct = 10 + Math.round((offset / file.size) * 90);
      view.status = `${Math.round((offset / file.size) * 100)}%`;
      view.update();
    } catch (error) {
      if (error.auth) throw error;
      if (++failures > 5) throw new Error("Connessione persa: riprova");
      await new Promise((r) => setTimeout(r, 1000 * failures));
      const st = await api(`/api/phone/files/${id}`).catch(() => null); // where does the server really stand?
      if (st) offset = st.offset;
    }
  }
  const done = await api(`/api/phone/files/${id}/finish`, { method: "POST" });
  delete map[key];
  saveResume(map);
  view.ready = true;
  view.pct = 100;
  view.status = "Fatto";
  view.update();
  return done;
}

function startUploads(files, onView) {
  for (const file of files) {
    const view = { name: file.name, pct: 0, status: "", ready: false, error: "", id: "", update() { renderChips(); if (state.tab === "files") paintUploads(); } };
    state.uploads.set(view, file);
    onView(view);
    uploadFile(file, view).catch((error) => {
      if (error.auth) return lost();
      view.error = error.message;
      view.status = error.message;
      view.update();
    }).then(() => view.update());
  }
}

function paintUploads() {
  const box = $("uploads");
  if (!box) return;
  box.replaceChildren();
  for (const [view, file] of state.uploads) {
    if (view.ready) continue;
    const row = el("div", "row");
    row.append(el("div", "name", view.name), el("small", "", view.error || view.status || ""));
    const bar = el("div", view.error ? "bar err" : "bar");
    const fill = el("i");
    fill.style.width = `${view.pct}%`;
    bar.append(fill);
    row.append(bar);
    if (view.error) {
      const retry = el("button", "", "Riprova");
      retry.onclick = () => { state.uploads.delete(view); startUploads([file], () => {}); paintUploads(); };
      const drop = el("button", "", "Annulla");
      drop.onclick = async () => {
        state.uploads.delete(view);
        if (view.id) await api(`/api/phone/files/${view.id}`, { method: "DELETE" }).catch(() => {});
        paintUploads();
      };
      const line = el("div", "btns");
      line.style.cssText = "display:flex;gap:8px;margin-top:8px";
      line.append(retry, drop);
      row.append(line);
    }
    box.append(row);
  }
}

const fmtSize = (n) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

async function renderFiles() {
  show("files");
  $("title").textContent = "File";
  const main = $("main");
  main.replaceChildren();
  const pick = el("input");
  pick.type = "file";
  pick.multiple = true;
  pick.className = "hide";
  const media = el("input");
  media.type = "file";
  media.multiple = true;
  media.accept = "image/*,video/*";
  media.className = "hide";
  const row = el("div", "");
  row.style.cssText = "display:flex;gap:8px;margin-bottom:10px";
  const bMedia = el("button", "primary", "Foto / Video");
  const bFile = el("button", "", "File");
  bMedia.style.flex = bFile.style.flex = "1";
  bMedia.onclick = () => media.click();
  bFile.onclick = () => pick.click();
  const onFiles = (e) => { startUploads(Array.from(e.target.files || []), () => {}); e.target.value = ""; paintUploads(); };
  pick.onchange = onFiles;
  media.onchange = onFiles;
  row.append(bMedia, bFile);
  const uploads = el("div");
  uploads.id = "uploads";
  const list = el("div");
  list.id = "filelist";
  main.append(pick, media, row, uploads, list);
  paintUploads();
  await refreshFiles();
  clearInterval(state.listTimer);
  state.listTimer = setInterval(() => { if (state.tab === "files" && !document.hidden) refreshFiles(); }, 5000);
}

async function refreshFiles() {
  const list = $("filelist");
  if (!list) return;
  let data;
  try { data = await api("/api/phone/files"); } catch (error) { if (error.auth) return lost(); return; }
  state.chunk = data.chunk_size || state.chunk;
  state.maxFile = data.max_file || 0;
  list.replaceChildren();
  for (const [heading, direction] of [["Da Jarvis", "out"], ["Inviati", "in"]]) {
    const rows = data.files.filter((f) => f.direction === direction && f.ready);
    if (!rows.length) continue;
    list.append(el("h2", "", heading));
    for (const f of rows) list.append(fileRow(f));
  }
  if (!list.children.length) list.append(el("p", "act", "Nessun file. Invia una foto o un documento, oppure aspetta quelli di Jarvis."));
}

function fileRow(f) {
  const row = el("div", "row file");
  const base = `/api/phone/files/${f.id}/download`;
  if (f.inline && /\.(jpe?g|png|gif|webp)$/i.test(f.name)) {
    const img = el("img");
    img.src = `${base}?inline=1`;
    img.alt = "";
    img.loading = "lazy";
    row.append(img);
  }
  const text = el("div", "grow");
  text.append(el("div", "name", f.name), el("small", "", fmtSize(f.size)));
  row.append(text);
  if (f.inline) {
    const open = el("a", "", "Apri");
    open.href = `${base}?inline=1`;
    open.target = "_blank";
    open.rel = "noopener";
    row.append(open);
  }
  const get = el("a", "", "Scarica");
  get.href = base;
  row.append(get);
  const del = el("button", "", "✕");
  del.setAttribute("aria-label", "Elimina");
  del.onclick = async () => {
    if (!confirm(`Eliminare ${f.name}?`)) return;
    try { await api(`/api/phone/files/${f.id}`, { method: "DELETE" }); } catch (error) { if (error.auth) return lost(); }
    refreshFiles();
  };
  row.append(del);
  return row;
}

// -------------------------------------------------------------------- voice
function toggleMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { alert("Questo browser non offre il riconoscimento vocale."); return; }
  if (state.rec) { state.rec.stop(); return; }
  const rec = new Recognition();
  rec.lang = "it-IT";
  rec.interimResults = true;
  const input = $("input");
  const base = input.value ? `${input.value} ` : "";
  rec.onresult = (e) => { input.value = base + Array.from(e.results).map((r) => r[0].transcript).join(""); };
  const done = () => { state.rec = null; $("mic").classList.remove("on"); };
  rec.onend = done;
  rec.onerror = done;
  state.rec = rec;
  $("mic").classList.add("on");
  rec.start();
}

// --------------------------------------------------------------------- boot
$("back").onclick = renderList;
$("logout").onclick = async () => {
  if (!confirm("Scollegare questo telefono da Jarvis?")) return;
  try { await api("/api/phone/logout", { method: "POST" }); } catch { /* already gone */ }
  state.csrf = "";
  renderPair("Telefono scollegato.");
};
$("tab-chat").onclick = () => setTab("chat");
$("tab-files").onclick = () => setTab("files");
$("send").onclick = send;
$("mic").onclick = toggleMic;
$("attach").onclick = () => $("file").click();
$("file").onchange = (e) => {
  startUploads(Array.from(e.target.files || []), (view) => { state.staged.push(view); renderChips(); });
  e.target.value = "";
};
$("stop").onclick = async () => {
  $("stop").disabled = true;
  try { await api(`/api/phone/chats/${encodeURIComponent(state.sid)}/cancel`, { method: "POST" }); } catch (error) { alert(error.message); }
  $("stop").disabled = false;
  poll();
};
$("input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.sid) poll(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/m/sw.js").catch(() => {});
boot();
