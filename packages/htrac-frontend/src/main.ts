/**
 * htrac frontend entry point.
 *
 * Mounts the tracker application which renders tracker items from the htrac
 * serve WebSocket. BlockSuite editor is initialized but reserved for future
 * use (edgeless mode for diagramming, block mode for rich content).
 *
 * Architecture: Lit web components render the tracker UI. State flows from
 * htrac serve via WebSocket -> ws-client -> tracker-app -> child components.
 * User mutations are sent back as WebSocket command messages.
 */
import "./components/tracker-app.js";
import { connectWebSocket } from "./ws-client.js";
import { registerServiceWorker } from "./sw-register.js";

// Mount the tracker app
const app = document.getElementById("app");
if (app) {
  app.innerHTML = "<tracker-app></tracker-app>";
}

// Connect to htrac serve WebSocket for live state
connectWebSocket();

// Register service worker for app shell caching (critical for Tor latency)
registerServiceWorker();
