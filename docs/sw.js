// offline: network first, last good copy when offline (never serves stale pages while online)
const C = "option-lab-v1";
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !/^https?:/.test(u.protocol)) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && (u.origin === location.origin || u.hostname === "raw.githubusercontent.com")) {
      const c = r.clone(); caches.open(C).then(x => x.put(e.request.url.split("?")[0], c));
    }
    return r;
  }).catch(() => caches.match(e.request.url.split("?")[0]).then(r => r || caches.match("./"))));
});
