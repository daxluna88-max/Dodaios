// Shell only: the panel opens offline, but every /api call goes to the network (never cached).
// The cache is owned by this panel: only caches named "jarvis-panel-*" are ever deleted, and only
// same-origin GETs under /m/ are handled, so installing the panel cannot touch other app caches.
const PREFIX = "jarvis-panel-";
const SHELL = `${PREFIX}v3`;
const FILES = ["/m/", "/m/app.js", "/m/sha256.js", "/m/manifest.webmanifest", "/m/icon-256.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES))));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k.startsWith(PREFIX) && k !== SHELL).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith("/m/")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
