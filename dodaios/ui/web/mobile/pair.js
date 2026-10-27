// Owner page: mint a pairing code, list and revoke phones, publish a file to them.
// It runs in the computer's own browser; a remote browser must present the Control Key,
// which is kept in this page's memory only.
"use strict";
const $ = (id) => document.getElementById(id);
let bearer = "";
let expiresAt = 0;
let ticker = 0;

async function call(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...options, headers, body: options.body ? JSON.stringify(options.body) : undefined, credentials: "same-origin" });
  if (res.status === 401 || res.status === 403) { const e = new Error("auth"); e.auth = true; throw e; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.statusText);
  return data;
}

function needKey() {
  $("pair").hidden = true;
  $("login").hidden = false;
}

async function newCode() {
  try {
    const out = await call("/api/phone-admin/pairing", { method: "POST" });
    $("login").hidden = true;
    $("pair").hidden = false;
    $("code").textContent = out.code;
    $("url").textContent = `${location.origin}/m/`;
    expiresAt = Date.now() + out.expires_in * 1000;
    clearInterval(ticker);
    ticker = setInterval(tick, 1000);
    tick();
    await devices();
  } catch (error) {
    if (error.auth) return needKey();
    $("code").textContent = "—";
    $("left").textContent = error.message;
    $("login").hidden = true;
    $("pair").hidden = false;
  }
}

function tick() {
  const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
  $("left").textContent = left ? `Scade tra ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "Scaduto: crea un nuovo codice.";
  if (!left) $("code").style.opacity = "0.35"; else $("code").style.opacity = "1";
}

async function devices() {
  const box = $("devices");
  box.replaceChildren();
  const { devices: list } = await call("/api/phone-admin/devices");
  const live = list.filter((d) => !d.revoked);
  if (!live.length) { const p = document.createElement("p"); p.className = "mute"; p.textContent = "Nessun telefono collegato."; box.append(p); }
  for (const d of live) {
    const row = document.createElement("div");
    row.className = "box dev";
    const text = document.createElement("div");
    text.className = "g";
    text.textContent = d.name;
    const seen = document.createElement("small");
    seen.textContent = ` · visto ${new Date(d.last_seen * 1000).toLocaleString("it-IT")}`;
    text.append(seen);
    const revoke = document.createElement("button");
    revoke.className = "danger";
    revoke.textContent = "Revoca";
    revoke.onclick = async () => {
      if (!confirm(`Revocare ${d.name}? Smette subito di funzionare.`)) return;
      await call(`/api/phone-admin/devices/${d.id}`, { method: "DELETE" });
      devices();
    };
    row.append(text, revoke);
    box.append(row);
  }
}

$("new").onclick = newCode;
$("keygo").onclick = () => { bearer = $("key").value.trim(); $("key").value = ""; newCode(); };
$("send").onclick = async () => {
  const path = $("path").value.trim();
  if (!path) return;
  $("sent").textContent = "Invio…";
  try {
    const out = await call("/api/phone-admin/outbox", { method: "POST", body: { path } });
    $("sent").textContent = `Pronto sul telefono: ${out.name}`;
    $("path").value = "";
  } catch (error) { $("sent").textContent = error.auth ? "Non autorizzato." : error.message; }
};
newCode();
