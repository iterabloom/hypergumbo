/**
 * htrac frontend entry point.
 *
 * Initializes BlockSuite with a PageEditor (block mode for tracker items) and
 * registers the service worker for app shell caching. The editor document is
 * populated from the htrac serve WebSocket state_snapshot on connect.
 *
 * Architecture: BlockSuite runs client-side as web components. State flows
 * from htrac serve via WebSocket -> local Doc model -> BlockSuite renders.
 * User mutations are sent back as WebSocket command messages.
 */
import "@toeverything/theme/style.css";
import "@blocksuite/presets/effects";
import { PageEditor, createEmptyDoc } from "@blocksuite/presets";
import { connectWebSocket } from "./ws-client.js";
import { registerServiceWorker } from "./sw-register.js";

// Initialize BlockSuite document and editor
const { doc, init } = createEmptyDoc();
const page = init();

const editor = new PageEditor();
editor.doc = page;

const app = document.getElementById("app");
if (app) {
  app.appendChild(editor);
}

// Connect to htrac serve WebSocket for live state
connectWebSocket();

// Register service worker for app shell caching (critical for Tor latency)
registerServiceWorker();
