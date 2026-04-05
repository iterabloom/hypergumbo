/**
 * Service worker for htrac app shell caching.
 *
 * Strategy: cache-first for static assets (JS, CSS, HTML, fonts, images).
 * Network-first for API calls (/api/*, /ws, /health). On install, precaches
 * the app shell so subsequent loads over Tor are instant.
 *
 * Cache versioning: bump CACHE_VERSION when deploying new builds. The activate
 * event cleans up old caches.
 */

const CACHE_VERSION = "htrac-v1";
const APP_SHELL_URLS = ["/", "/index.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL_URLS)),
  );
  // Activate immediately without waiting for old tabs to close
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_VERSION)
          .map((name) => caches.delete(name)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Network-first for API, WebSocket, and health endpoints
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname === "/ws" ||
    url.pathname === "/health"
  ) {
    return;
  }

  // Cache-first for static assets
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(event.request).then((response) => {
        // Cache successful GET responses for static assets
        if (response.ok && event.request.method === "GET") {
          const clone = response.clone();
          caches.open(CACHE_VERSION).then((cache) => {
            cache.put(event.request, clone);
          });
        }
        return response;
      });
    }),
  );
});
