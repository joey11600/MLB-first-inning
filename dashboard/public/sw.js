/* NRFI Terminal — service worker.
 *
 * Phase 6 PWA: takes the dashboard from "browser tab" to "installable
 * app on the home screen with offline tolerance."  Strategy:
 *
 *   1. Static-shell precaching
 *      The HTML, CSS, JS, fonts, and icons that make up the chrome
 *      are pre-fetched on install + cached for instant boot.  When
 *      the user opens the installed app on a flaky connection, the
 *      shell renders immediately from cache while the API call to
 *      /api/board races in the background.
 *
 *   2. Network-first for /api/* and Supabase Realtime
 *      Live data must never be served stale from cache.  Network
 *      wins; we only fall back to a cached copy if the request
 *      fails (e.g. subway tunnel) so the user sees SOMETHING
 *      instead of a broken shell.
 *
 *   3. Cache-first for /icon-*.svg, fonts, _next/static/*
 *      Immutable build artifacts.  Cached forever (the cache name
 *      is versioned, so a new deploy invalidates everything via
 *      the install event).
 *
 *   4. Stale-while-revalidate for /
 *      Render the cached homepage instantly (instant boot), then
 *      revalidate in the background.  Next render gets the fresh
 *      version.  Means PWA cold start feels native-app-fast even
 *      after a deploy.
 *
 * Versioning: bump CACHE_VERSION on any worker change.  The new
 * worker's install event purges old caches so we don't accumulate
 * stale Vercel chunks across deploys.
 */

// T-V21-2026-05-06c: bumped from "nrfi-v2".  The activate handler purges
// any cache whose key doesn't start with the current CACHE_VERSION, so
// bumping this string forces a clean slate on every browser the next
// time the SW activates -- which is the only reliable way to evict
// stale HTML cached by past deploys (incl. the dirty `?date=2026-05-02`
// shell that was sticking through the middleware redirect).
const CACHE_VERSION  = "nrfi-v3";
const SHELL_CACHE    = `${CACHE_VERSION}-shell`;
const RUNTIME_CACHE  = `${CACHE_VERSION}-runtime`;

// Static shell — pre-fetch on install.  Keep this list small; Next.js
// bundles its own JS chunks with hashed names that we can't enumerate
// statically, so they fall through to the runtime cache.
const SHELL_ASSETS = [
  "/",
  "/manifest.json",
  "/icon-192.svg",
  "/icon-512.svg",
  "/icon-maskable.svg",
  "/apple-touch-icon.svg",
];

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

self.addEventListener("install", (event) => {
  // Pre-cache the shell.  skipWaiting() ensures the new worker takes
  // over on the next page load without waiting for every tab to close.
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
      .catch((err) => console.warn("[sw] shell precache failed:", err))
  );
});

self.addEventListener("activate", (event) => {
  // Purge old caches from previous CACHE_VERSIONs.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// Fetch strategy router
// ---------------------------------------------------------------------------

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // Only handle GET — POST/PUT/DELETE go straight to network.
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Cross-origin (Supabase, MLB API, fonts CDN, Vercel insights, etc.):
  // pass through without caching.  Cross-origin caching has CORS
  // gotchas that aren't worth the risk for live data sources.
  if (url.origin !== self.location.origin) return;

  // Network-first for API routes (live data).
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(req));
    return;
  }

  // Cache-first for static immutable assets.
  if (
    url.pathname.startsWith("/_next/static/") ||
    url.pathname.match(/\.(svg|png|ico|woff2?|ttf|eot)$/)
  ) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // T-V21-2026-05-06c: HTML is now network-first.  Stale-while-revalidate
  // was serving an old cached shell (the OLD 4-tile layout with the
  // "MODEL: LR-v2 · calibrated" header from a deploy weeks back) on
  // every alternate refresh, because SWR returns cached IMMEDIATELY
  // and only revalidates in the background -- meaning the user sees
  // stale HTML at least once per cache miss-cycle.  For a live data
  // dashboard, freshness > instant boot.  Network-first means:
  //
  //   - Online:  always fresh shell, server has chance to redirect
  //              `?date=` queries before the page renders.
  //   - Offline: cached fallback so the user still sees SOMETHING
  //              (the offline copy is a genuine improvement over a
  //              connection-refused error).
  event.respondWith(networkFirst(req));
});

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

async function networkFirst(req) {
  try {
    const fresh = await fetch(req);
    // Mirror the response into runtime cache so we have a fallback
    // if the next request comes from a tunnel-blackout.
    if (fresh && fresh.ok) {
      const clone = fresh.clone();
      caches.open(RUNTIME_CACHE).then((c) => c.put(req, clone)).catch(() => {});
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(req);
    if (cached) return cached;
    // No fresh, no cached -- give the browser a clean error to render.
    return new Response(
      JSON.stringify({ error: "offline", cachedAt: null }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    );
  }
}

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const fresh = await fetch(req);
  if (fresh && fresh.ok) {
    const clone = fresh.clone();
    caches.open(RUNTIME_CACHE).then((c) => c.put(req, clone)).catch(() => {});
  }
  return fresh;
}

async function staleWhileRevalidate(req) {
  const cached = await caches.match(req);
  const fetchPromise = fetch(req)
    .then((fresh) => {
      if (fresh && fresh.ok) {
        const clone = fresh.clone();
        caches.open(RUNTIME_CACHE).then((c) => c.put(req, clone)).catch(() => {});
      }
      return fresh;
    })
    .catch(() => null);
  return cached || (await fetchPromise) || new Response("", { status: 504 });
}

// ---------------------------------------------------------------------------
// Future: Web Push subscriptions
// ---------------------------------------------------------------------------
//
// The 'push' + 'notificationclick' handlers below let the dashboard
// receive native phone push notifications when picks flip or bets
// land.  Currently no-ops because the user already has Telegram
// pick-flip notifications set up (T2.22) -- no need for double
// notifications.  When/if Web Push lands, populate VAPID keys via
// env vars and uncomment the wiring below.

self.addEventListener("push", (event) => {
  // Best-effort: parse the push payload; if it's not what we expect,
  // bail silently rather than firing a misleading notification.
  if (!event.data) return;
  let payload;
  try { payload = event.data.json(); } catch { return; }
  if (!payload || typeof payload !== "object") return;
  const title = payload.title || "NRFI Terminal";
  const opts  = {
    body:  payload.body || "",
    icon:  "/icon-192.svg",
    badge: "/icon-192.svg",
    tag:   payload.tag || "nrfi-generic",
    data:  { url: payload.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((wins) => {
        // If a NRFI tab is already open, focus it instead of opening a new one.
        for (const w of wins) {
          if (w.url.includes(self.location.origin) && "focus" in w) {
            w.navigate(url);
            return w.focus();
          }
        }
        if (self.clients.openWindow) return self.clients.openWindow(url);
      })
  );
});
