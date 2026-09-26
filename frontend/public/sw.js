/* StrataSense service worker: keeps the rig-site app usable when the VSAT link to the RTOC drops.
 *
 * - App shell (index, JS/CSS bundles, icons) is precached from /precache.json, written at build time.
 * - Static assets: cache first (their file names are content hashes).
 * - A small set of read-only API calls the rig view needs: network first, cached copy when offline.
 * - Page navigations: network first, cached index.html when offline.
 * The live picture itself (status, alerts, look-ahead) is cached by the app in localStorage, and
 * acknowledgements made offline are queued there and sent when the link returns.
 */
const SHELL = "stratasense-shell";
const API = "stratasense-api";
const API_CACHED = [/^\/api\/meta$/, /^\/api\/wells$/, /^\/api\/risk\/profile/, /^\/api\/risk\/mw-window/, /^\/api\/recommend/];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const res = await fetch("/precache.json", { cache: "no-store" });
    const { version, files } = await res.json();
    const cache = await caches.open(`${SHELL}-${version}`);
    await cache.addAll(files);
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    // keep only the newest versioned shell (names are stratasense-shell-<build timestamp>; other caches are left alone)
    const shells = keys.filter((k) => /^stratasense-shell-\d+$/.test(k)).sort((a, b) => Number(a.split("-").pop()) - Number(b.split("-").pop()));
    await Promise.all(shells.slice(0, -1).map((k) => caches.delete(k)));
    // caches from before the rename to StrataSense (nwis-shell-*, nwis-api, nwis-nav, nwis-assets)
    await Promise.all(keys.filter((k) => k.startsWith("nwis-")).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

async function networkFirst(request, cacheName, fallbackUrl) {
  try {
    // revalidate with the server so a new build is picked up as soon as the link is up
    const res = await fetch(request.mode === "navigate" ? new Request(request.url, { cache: "no-cache", credentials: "same-origin" }) : request);
    if (res.ok) (await caches.open(cacheName)).put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await caches.match(request) || (fallbackUrl && await caches.match(fallbackUrl));
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/ws/")) return;
  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, "stratasense-nav", "/index.html"));
  } else if (url.pathname.startsWith("/assets/")) {
    // hashed bundles: cache first, and store on first fetch so a build newer than this worker still works offline
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then(async (res) => {
      if (res.ok) (await caches.open("stratasense-assets")).put(req, res.clone());
      return res;
    })));
  } else if (API_CACHED.some((re) => re.test(url.pathname))) {
    event.respondWith(networkFirst(req, API));
  }
});
