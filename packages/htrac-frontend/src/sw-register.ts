/**
 * Service worker registration for app shell caching.
 *
 * Caches the built static assets (HTML, JS, CSS) so only the first page load
 * is heavy over Tor. Subsequent loads serve from Cache Storage even if the
 * network is slow or unavailable. The WebSocket connection handles live data;
 * the service worker only caches the app shell, not API responses.
 */

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js")
      .catch(() => {
        // Service worker registration failed — app works without it,
        // just won't have offline app shell caching.
      });
  });
}
