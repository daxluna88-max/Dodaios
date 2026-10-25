// Shell only: the panel opens offline, but every /api call goes to the network (never cached).
const SHELL = "jarvis-panel-v1";
const FILES = ["/m/", "/m/app.js", "/m/manifest.webmanifest", "/m/icon-256.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES))));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || !url.pathname.startsWith("/m/")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
