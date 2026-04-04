/**
 * WebSocket client for htrac serve real-time state sync.
 *
 * Connects to the /ws endpoint and handles the htrac protocol:
 * - state_snapshot: Full item state on connect/reconnect
 * - event: Mutation notifications (item updated, discussed)
 * - result: Responses to command queries
 * - error: Server-side errors
 *
 * Reconnects automatically with exponential backoff (1s → 2s → 4s → ... → 30s).
 * Each reconnect receives a fresh state_snapshot, so no delta tracking needed.
 */

/** Tracker item as sent by htrac serve. */
export interface TrackerItem {
  id: string;
  kind: string;
  title: string;
  status: string;
  priority: number;
  parent: string | null;
  tags: string[];
  before: string[];
  pr_ref: string | null;
  description: string;
  fields: Record<string, unknown>;
  locked_fields: string[];
  discussion: DiscussionEntry[];
  frozen: boolean;
  tier: string | null;
  created_at: string;
  updated_at: string;
}

export interface DiscussionEntry {
  by: string;
  actor: string;
  at: string;
  message: string;
  is_summary: boolean;
}

type MessageHandler = (items: TrackerItem[]) => void;

const handlers: MessageHandler[] = [];
let currentItems: TrackerItem[] = [];

/** Register a callback invoked on every state_snapshot. */
export function onStateUpdate(handler: MessageHandler): void {
  handlers.push(handler);
  // Deliver current state immediately if we already have it
  if (currentItems.length > 0) {
    handler(currentItems);
  }
}

/** Get the current tracker items (last snapshot). */
export function getItems(): TrackerItem[] {
  return currentItems;
}

/** Send a command to the server via WebSocket. */
export function sendCommand(
  action: string,
  payload: Record<string, unknown> = {},
): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "command", action, ...payload }));
  }
}

let ws: WebSocket | null = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;

function getWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

function handleMessage(event: MessageEvent): void {
  let msg: Record<string, unknown>;
  try {
    msg = JSON.parse(event.data as string);
  } catch {
    return;
  }

  if (msg.type === "state_snapshot" && Array.isArray(msg.items)) {
    currentItems = msg.items as TrackerItem[];
    for (const handler of handlers) {
      handler(currentItems);
    }
    // Reset backoff on successful snapshot
    reconnectDelay = 1000;
  }
}

function connect(): void {
  ws = new WebSocket(getWsUrl());

  ws.addEventListener("message", handleMessage);

  ws.addEventListener("open", () => {
    reconnectDelay = 1000;
  });

  ws.addEventListener("close", () => {
    ws = null;
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    // error is always followed by close, which triggers reconnect
  });
}

function scheduleReconnect(): void {
  setTimeout(() => {
    connect();
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
  }, reconnectDelay);
}

/** Start the WebSocket connection to htrac serve. */
export function connectWebSocket(): void {
  connect();
}
