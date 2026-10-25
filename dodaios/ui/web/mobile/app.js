// Jarvis phone panel: a thin client over the existing /api/agent-chat routes.
// Auth is the Control Key as a Bearer token (kept only in this browser's localStorage).
"use strict";

const $ = (id) => document.getElementById(id);
const KEY_SLOT = "jm_key";
const POLL_MS = 1500;
const IDLE_POLL_MS = 4000;

const state = { key: "", sid: "", timer: 0, pending: [], busy: false, lastSeq: -1, rec: null };

function loadKey() {
  try { return localStorage.getItem(KEY_SLOT) || ""; } catch { return ""; }
}
function saveKey(value) {
  try { value ? localStorage.setItem(KEY_SLOT, value) : localStorage.removeItem(KEY_SLOT); } catch { /* private mode */ }
}

async function api(path, options = {}) {
  const headers = { Authorization: `Bearer ${state.key}`, ...(options.headers || {}) };
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...options, headers, body });
  if (res.status === 401 || res.status === 403) {
    const error = new Error("auth");
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

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function show(view) {
  const chat = view === "chat";
  $("composer").classList.toggle("hide", !chat);
  $("back").classList.toggle("hide", view !== "chat");
  $("logout").classList.toggle("hide", view === "login");
  if (!chat) {
    $("chips").classList.add("hide");
    $("status").classList.add("hide");
  }
}

// ----------------------------------------------------------------- login
function renderLogin(message) {
  stopPolling();
  show("login");
  $("title").textContent = "Jarvis";
  const main = $("main");
  main.replaceChildren();
  main.append(el("p", "", "Inserisci la Control Key di Dodaios (Impostazioni → API Keys → Control Key)."));
  const input = el("input");
  input.type = "password";
  input.autocomplete = "off";
  input.placeholder = "jctl_…";
  const go = el("button", "primary", "Collegati");
  const note = el("p", "act", message || "");
  go.onclick = async () => {
    state.key = input.value.trim();
    if (!state.key) return;
    go.disabled = true;
    try {
      await api("/api/control/auth/probe");
      saveKey(state.key);
      renderList();
    } catch (error) {
      state.key = "";
      note.textContent = error.auth ? "Key non valida." : `Non raggiungo Jarvis (${error.message}).`;
      go.disabled = false;
    }
  };
  main.append(input, go, note);
}

// ------------------------------------------------------------------ list
async function renderList() {
  stopPolling();
  state.sid = "";
  show("list");
  $("title").textContent = "Chat";
  const main = $("main");
  main.replaceChildren(el("p", "act", "Carico…"));
  let sessions;
  try {
    sessions = (await api("/api/agent-chat/sessions?limit=40")).sessions || [];
  } catch (error) {
    return fail(error, renderList);
  }
  main.replaceChildren();
  const fresh = el("button", "primary", "Nuova chat");
  fresh.style.width = "100%";
  fresh.style.marginBottom = "10px";
  fresh.onclick = () => newChat(sessions);
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

async function newChat(sessions) {
  const base = sessions.find((s) => s.provider);
  if (!base) {
    alert("Crea la prima chat dal desktop: da qui non so quale provider usare.");
    return;
  }
  try {
    const made = await api("/api/agent-chat/sessions", {
      method: "POST",
      body: { provider: base.provider, model: base.model || "", surface: base.surface || "agent", cwd: base.cwd || null },
    });
    openChat(made.session_id || (made.session && made.session.session_id), "Nuova chat");
  } catch (error) {
    fail(error, () => newChat(sessions));
  }
}

function fail(error, retry) {
  if (error.auth) {
    saveKey("");
    state.key = "";
    renderLogin("Sessione scaduta: reinserisci la key.");
    return;
  }
  const main = $("main");
  main.replaceChildren(el("p", "msg err", `Errore: ${error.message}`));
  const again = el("button", "", "Riprova");
  again.onclick = retry;
  main.append(again);
}

// ------------------------------------------------------------------ chat
function openChat(sid, title) {
  state.sid = sid;
  state.lastSeq = -1;
  state.pending = [];
  show("chat");
  $("title").textContent = title || "Chat";
  $("main").replaceChildren();
  $("chips").replaceChildren();
  $("chips").classList.add("hide");
  poll();
}

function stopPolling() {
  clearTimeout(state.timer);
  state.timer = 0;
}

async function poll() {
  stopPolling();
  const sid = state.sid;
  if (!sid) return;
  let running = false;
  try {
    const data = await api(`/api/agent-chat/sessions/${encodeURIComponent(sid)}?tail=120`);
    if (sid !== state.sid) return;
    running = !!(data.session && data.session.running);
    if (data.session && data.session.title) $("title").textContent = data.session.title;
    renderEvents(data.events || [], running);
  } catch (error) {
    if (error.auth) return fail(error, renderList);
    $("statustext").textContent = `Connessione persa (${error.message}), riprovo…`;
    $("status").classList.remove("hide");
  }
  if (sid === state.sid) state.timer = setTimeout(poll, running || state.pending.length ? POLL_MS : IDLE_POLL_MS);
}

function renderEvents(events, running) {
  const last = events.length ? events[events.length - 1].seq : -1;
  const resolved = new Set(events.filter((e) => e.kind === "approval_resolved").map((e) => e.payload.approval_id));
  state.pending = events.filter((e) => e.kind === "approval_required" && !resolved.has(e.payload.approval_id));
  const signature = `${last}:${state.pending.map((e) => e.payload.approval_id).join(",")}:${running}`;
  if (signature === state.lastSeq) return;
  state.lastSeq = signature;

  const main = $("main");
  const stick = main.scrollHeight - main.scrollTop - main.clientHeight < 80;
  main.replaceChildren();
  for (const event of events) {
    const p = event.payload || {};
    if (event.kind === "user_message") {
      main.append(el("div", "msg user", String(p.typed || p.text || "")));
    } else if (event.kind === "assistant_text") {
      main.append(el("div", "msg bot", String(p.text || "")));
    } else if (event.kind === "tool_call") {
      main.append(el("div", "act", `⚙ ${p.name || "strumento"}`));
    } else if (event.kind === "error") {
      main.append(el("div", "msg bot err", String(p.message || "Errore")));
    } else if (event.kind === "turn_finished" && p.status === "cancelled") {
      main.append(el("div", "act", "Fermato."));
    }
  }
  for (const event of state.pending) main.append(approvalCard(event.payload));
  if (stick) main.scrollTop = main.scrollHeight;

  const status = $("status");
  const working = running || state.pending.length > 0;
  status.classList.toggle("hide", !working);
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
        await api(`/api/agent-chat/sessions/${encodeURIComponent(state.sid)}/approvals/${encodeURIComponent(p.approval_id)}`, {
          method: "POST", body: { decision },
        });
      } catch (error) {
        if (error.status !== 410 && error.status !== 404) alert(error.message);
      }
      poll();
    };
    row.append(button);
  }
  card.append(row);
  return card;
}

async function send() {
  const input = $("input");
  const text = input.value.trim();
  if (state.busy || (!text && !state.files.length)) return;
  state.busy = true;
  $("send").disabled = true;
  try {
    let attachments = [];
    if (state.files.length) {
      const form = new FormData();
      for (const file of state.files) form.append("files", file, file.name);
      form.append("session_id", state.sid);
      attachments = (await api("/api/agent-chat/attachments", { method: "POST", body: form })).attachments || [];
    }
    await api(`/api/agent-chat/sessions/${encodeURIComponent(state.sid)}/messages`, {
      method: "POST", body: { text, attachments, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    });
    input.value = "";
    input.style.height = "";
    setFiles([]);
    poll();
  } catch (error) {
    if (error.auth) return fail(error, renderList);
    alert(error.message);
  } finally {
    state.busy = false;
    $("send").disabled = false;
  }
}

state.files = [];
function setFiles(files) {
  state.files = files;
  const chips = $("chips");
  chips.replaceChildren(...files.map((f) => el("span", "chip", `${f.name} · ${Math.max(1, Math.round(f.size / 1024))} KB`)));
  chips.classList.toggle("hide", !files.length);
}

// ----------------------------------------------------------------- voice
function toggleMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { alert("Questo browser non offre il riconoscimento vocale."); return; }
  if (state.rec) { state.rec.stop(); return; }
  const rec = new Recognition();
  rec.lang = "it-IT";
  rec.interimResults = true;
  const input = $("input");
  const base = input.value ? `${input.value} ` : "";
  rec.onresult = (e) => {
    input.value = base + Array.from(e.results).map((r) => r[0].transcript).join("");
  };
  rec.onend = () => { state.rec = null; $("mic").classList.remove("on"); };
  rec.onerror = () => { state.rec = null; $("mic").classList.remove("on"); };
  state.rec = rec;
  $("mic").classList.add("on");
  rec.start();
}

// ------------------------------------------------------------------ boot
$("back").onclick = renderList;
$("logout").onclick = () => { saveKey(""); state.key = ""; renderLogin(); };
$("send").onclick = send;
$("mic").onclick = toggleMic;
$("attach").onclick = () => $("file").click();
$("file").onchange = (e) => { setFiles(Array.from(e.target.files || [])); e.target.value = ""; };
$("stop").onclick = async () => {
  $("stop").disabled = true;
  try {
    await api(`/api/agent-chat/sessions/${encodeURIComponent(state.sid)}/cancel`, { method: "POST", headers: { "X-Jarvis-Stop-Via": "phone-panel" } });
  } catch (error) { alert(error.message); }
  $("stop").disabled = false;
  poll();
};
$("input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.sid) poll(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/m/sw.js").catch(() => {});

state.key = loadKey();
if (state.key) renderList(); else renderLogin();
