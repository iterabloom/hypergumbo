<!-- SPDX-License-Identifier: MPL-2.0 -->
# ADR-0019: Remote Access Transport

Date: 2026-03-30
Status: Proposed

## Context

### The access problem

hypergumbo-tracker runs on a Linux VM in a homelab. The VM can only make **outgoing** network connections — no inbound ports are open. The human operator wants to access the tracker from a phone over the internet, with a rich web UI rather than just a terminal.

### The auth invariant

The system must enforce: **the phone cannot access the agent's tracker unless the user has (1) plugged in a YubiKey, (2) entered a password, (3) passed Face ID, and (4) not entered the duress password — all within the configured session TTL (default 15 minutes).** This is a hard security boundary, not a nice-to-have. It rules out static credentials, long-lived tokens, and any auth scheme that doesn't prove physical hardware presence and voluntary intent at interaction time.

The four factors cover four distinct threat scenarios:

| Factor | What it proves | Threat it defeats |
|--------|---------------|-------------------|
| YubiKey | Physical possession of specific hardware | Phone theft, remote compromise, credential stuffing |
| Password | Knowledge | Shoulder surfing alone, device theft alone |
| Face ID | Biometric presence | Stolen YubiKey + known password (attacker isn't you) |
| Duress password | Voluntary intent | Coercion — someone forcing you to authenticate |

### Why SSH tunneling is insufficient

The current access path is an SSH tunnel to the VM via a terminal client. This works for terminal sessions but does not extend to phone access over the internet, and does not satisfy the YubiKey auth invariant for web UI sessions.

### Alternatives evaluated

- **Tor onion service only (no direct path):** Always works for an outbound-only VM. Tor is valued infrastructure and the always-available foundation — but using it for all bulk traffic (canvas interactions, real-time updates, large state syncs) when a direct path exists would unnecessarily load volunteer relays. WireGuard optimization is both better UX and better Tor citizenship.
- **Reverse SSH tunnel to a VPS:** Requires maintaining a public VPS and keeping the tunnel alive. Simpler than Tor+WireGuard but introduces a single point of failure and a public endpoint to harden.
- **Tailscale/ZeroTier/overlay network:** Requires an external coordination service. Violates the self-hosted constraint.
- **WireGuard with phone as server:** Phone is behind NAT/CGNAT, changes networks frequently, and has no stable public endpoint. Wrong hub for the mesh.
- **Pomerium as auth proxy:** Designed for teams with IdPs and multi-service meshes. For a single-user governance tool, it adds ~134MB of Go binary, IdP integration, TLS certificate management, and route configuration. Replaced by `py_webauthn` in the Python app itself (see Decision).

## Decision

### Transport: Tor + WireGuard, both valued

**Design philosophy:** Tor is the always-available foundation — valued infrastructure, not a crutch. WireGuard is an automatic optimization that moves bulk traffic off the Tor network when a direct path exists. Using WireGuard when possible is good citizenship: it frees volunteer relay capacity for people who need anonymity, while htrac users need private reachability, not anonymity.

**Tor (always available, bootstrap + fallback):**
- The VM runs a Tor daemon that publishes a v3 onion service, forwarding to `127.0.0.1` (or a Unix socket) where `htrac serve` listens.
- Tor v3 client authorization is enabled — only clients holding the private credential can connect.
- This path requires only outgoing connections from the VM. No inbound ports, no public IP, no DNS.
- Tor is used for initial bootstrap (endpoint exchange for WireGuard), as the fallback when WireGuard can't establish a path, and as the primary transport when the user wants location privacy (traveling, hostile network).

**WireGuard (automatic optimization):**
- The client discovers its current public UDP mapping via a **STUN reflector** (see below).
- The client sends its candidate endpoint to the VM over the Tor control channel.
- Both sides attempt WireGuard handshake with `PersistentKeepalive`.
- If a direct UDP path forms, bulk traffic (WebSocket events, UI updates, canvas interactions) moves to WireGuard.
- If not (unfriendly NAT, CGNAT, no cooperating path), the session stays on Tor transparently.

**Transport selection is automatic.** The user does not choose or see which path is active, except optionally via a status indicator. The system always starts on Tor, attempts WireGuard upgrade in the background, and falls back to Tor if WireGuard drops.

### NAT traversal: STUN and TURN

WireGuard needs to know the client's public IP:port to establish a direct UDP path. Any **STUN-compatible UDP reflector** can answer this question — it receives a UDP packet and replies with the observed source address.

**STUN is pluggable, not managed.** The htrac config accepts any STUN server URL. Users can point it at:
- A public STUN server (Google, Cloudflare, and others operate these freely)
- A self-hosted STUN server (e.g., `coturn` in STUN-only mode on a $5/month VPS)
- A future managed reflector, if one is offered as a convenience default

The system does not require any specific STUN provider. WireGuard activates as long as *some* STUN server is configured. If no STUN server is configured, WireGuard never activates and the session stays on Tor — which is fine.

Properties of any STUN reflector:
- **Stateless.** No user data, no logs, no sessions.
- **Privacy-neutral.** It doesn't know htrac exists. It's a generic UDP echo service.
- **Not a relay.** It does not forward packets between peers or store anything.

**TURN for symmetric NAT (mobile networks).** STUN works for full-cone and restricted-cone NAT, but fails on symmetric NAT — common on mobile carriers and behind CGNAT. Since the primary use case for remote access is phone connectivity, this is a significant gap. A **TURN relay** solves this: when STUN-discovered endpoints cannot establish a direct path, both sides relay traffic through the TURN server instead.

The NAT traversal strategy becomes: STUN first (direct path, zero relay cost) → TURN fallback (relayed path, adds latency but works on any NAT) → Tor fallback (always available, highest latency). `coturn` supports both STUN and TURN in a single deployment, so a self-hosted `coturn` instance covers both tiers. TURN relay credentials are short-lived (generated per-session by `htrac serve` using a shared secret with the TURN server) to prevent unauthorized relay use.

### Server: `htrac serve`

`htrac serve` is the web server that the transport connects to and that auth protects. It runs as a long-lived process on the VM, serving both the web UI and the federation API (ADR-0021).

**Architecture: one backend, multiple frontends.**

The server is an **additive capability**, not a replacement for the CLI. `htrac add`, `htrac update`, `htrac show`, and all other CLI commands work identically whether or not `htrac serve` is running. The CLI, TUI, web UI, and federation API are all clients of the same core:

```
htrac serve (Python, single process)
├── core engine
│   ├── TrackerSet (read/write, same as CLI/TUI)
│   ├── command handlers (add, update, discuss, lock, ...)
│   ├── state (authoritative, in-memory + ops on disk)
│   └── event bus (state changes → all subscribers)
├── interface adapters
│   ├── WebSocket/HTTP adapter (browser UI, federation API)
│   ├── CLI adapter (htrac add/update/... calls core directly)
│   └── TUI adapter (Textual app calls core directly)
└── auth layer (py_webauthn, session management, duress module)
```

**Key design principle: ops files on disk are the source of truth; the server's in-memory state is a read cache.**

- Commands (from CLI, browser, or federation peer) go to the core engine.
- The core updates state (appends ops, compiles) and emits events.
- All connected clients (browser WebSocket, TUI, federation peers) receive the event and update their view.
- The CLI is not a special sink — `htrac discuss INV-foo "message"` and a browser form submission both call the same `discuss()` handler.

**CLI/server coexistence:**

Both the CLI and `htrac serve` use the same Store/TrackerSet with the same advisory flock mechanism. The server is just another flock user — there is no coordination protocol, no IPC delegation, no requirement that the CLI detect a running server. When the CLI writes an op while the server is running, the server detects the change via filesystem notification (inotify/fsnotify), recompiles, and pushes the updated state to connected WebSocket clients. The delay between a CLI write and the WebSocket push is sub-second — expected and acceptable.

**WebSocket protocol:**

The browser UI connects via WebSocket for real-time bidirectional communication. The protocol uses typed JSON messages:

| Message type | Direction | Purpose |
|-------------|-----------|---------|
| `command` | client → server | Invoke a tracker operation (update, discuss, lock, ...) |
| `event` | server → client | State change notification (item updated, discussion added, ...) |
| `state_snapshot` | server → client | Full current state, sent on connect and reconnect |
| `error` | server → client | Structured error response to a command |

The message schema should be defined formally before implementation — ad hoc JSON messages become brittle quickly.

**Process model:**

- `htrac serve` runs as a single long-lived process, managed by systemd (or a process manager).
- Binds to `127.0.0.1` only (or a Unix socket). Never binds to `0.0.0.0`.
- PID file prevents multiple instances.
- Logs to a file or journald, not stdout (background process).
- Health check endpoint (`/health`) for monitoring.
- Graceful shutdown: drain WebSocket connections, flush pending ops, exit.

```bash
htrac serve                    # foreground (development)
htrac serve --background       # daemonize, write PID file
htrac serve --stop             # graceful shutdown via PID
htrac serve --status           # running? PID? uptime? connected clients?
                               # STUN reachability, WireGuard activation rate,
                               # current transport path (Tor/WireGuard) per client
```

**Failure behavior:**

- WebSocket disconnects: client reconnects and requests `state_snapshot`. No state is lost — the server is authoritative.
- Server restart: all sessions are invalidated (session state is in-memory only). Clients must re-authenticate with full four-factor auth on reconnect.
- Multiple clients: supported. All see the same state via the event bus. Concurrent writes are serialized by the core engine (same flock-based locking as the CLI).
- SSH tunnel drops: the browser in `WKWebView` reconnects when the tunnel is re-established. The WebSocket protocol handles reconnect via `state_snapshot`.

**Federation API (ADR-0021):**

The same server process also handles federation:

- `GET /api/federation/items` — compiled-view feed (WebSocket, subscribe/push)
- `POST /api/federation/items/{id}/update` — write-at-origin for remote peers
- `POST /api/federation/items/{id}/discuss` — discussion from remote peers
- `POST /api/federation/register-peer` — peer provisioning (human auth required)
- `POST /api/federation/revoke-peer` — peer revocation (human auth required)

Federation API requests are authenticated via Ed25519-signed requests (machine-to-machine), not via the four-factor human auth flow.

**Integration point for ADR-0020 and ADR-0021.** `htrac serve` is the shared backend for the web UI (which renders SVG screenshots natively, replacing the Chafa pipeline in ADR-0020), the federation compiled-view feed and write-at-origin API (ADR-0021), and the WebSocket event bus that connects all clients. The CLI and TUI operate independently of `htrac serve` — they use the same Store/TrackerSet via flock. `htrac serve` is required only for web access, federation sync, and real-time multi-client updates.

### Authentication: four-factor with duress detection

`htrac serve` implements authentication directly — no external IdP, no Pomerium, no companion binary. Auth lives in the same Python process as the tracker.

**Factor sequence:**

1. **Face ID** (iOS system level). The app requires biometric auth before presenting any UI. This happens in the iOS app via `LAContext.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics)` before the app shows its own login screen. The server never sees biometric data — Face ID is a client-side gate.

2. **YubiKey** (WebAuthn/FIDO2). The app opens `ASWebAuthenticationSession` to the server's `/auth/webauthn` endpoint. The server issues a challenge; the YubiKey signs it. `py_webauthn` verifies the response. This proves physical possession of the registered hardware token. Note: `ASWebAuthenticationSession` bounces the user to a Safari system sheet and back — a brief context switch on every authentication. With the 15-minute session TTL, this happens frequently. This is the intended security/convenience tradeoff.

3. **Password**. After WebAuthn succeeds, the app presents a password field. The password is sent to the server over the already-authenticated channel. The server accepts either the **real password** or the **duress password** — both are valid credentials that produce a successful login.

4. **Duress detection** (server-side, invisible). The server stores bcrypt hashes for both passwords. On password verification:
   - If the real password was used: issue a normal session token.
   - If the duress password was used: issue a session token tagged server-side as a duress session. The token itself is indistinguishable from a normal token — no `duress=true` flag, no different format. The duress state lives only in the server's session store.

**Session management:**

- Session TTL is configurable in `config.yaml` under `auth.session_ttl_minutes` (default: 15). The TTL is a fixed window — interaction does not reset the timer. Expiry requires full re-authentication (all four factors).
- Transport-level auth (WireGuard peer key, Tor client auth) does **not** extend the session TTL. Those prove device identity, not human presence. The four-factor flow proves human intent and must be repeated on every session expiry.
- Session tokens are opaque, cryptographically random, stored server-side. The client receives a cookie.
- The server maintains a session record mapping each token to: creation time, expiry time, auth class (normal or duress), and client fingerprint.
- Session state is held in-memory only. If `htrac serve` crashes or restarts, all active sessions are invalidated — clients must re-authenticate with full four-factor auth. This eliminates the need for session persistence and ensures duress session tags cannot be lost to a partial disk write.
- The client should display a pre-expiry warning (e.g., 1 minute before TTL) prompting the user to save work before re-authentication. The configurable TTL (default 15 minutes) can be increased by users who prefer less friction.

**Password brute-force protection:**

After WebAuthn succeeds, the password step is rate-limited per WebAuthn credential. Failed password attempts trigger exponential backoff (configurable base and max delay). After a configurable maximum number of consecutive failures, the credential is locked — re-enabling it requires human-mediated YubiKey re-registration via `htrac setup`. Rate-limit state is in-memory (resets on server restart, which also invalidates all sessions).

**Duress session behavior:**

A duress session must be **indistinguishable from a normal session** to someone watching over the user's shoulder. Same load time, same UI chrome, same apparent interaction patterns.

The specific behavior triggered by a duress login is **deliberately not specified** in this ADR, in config files, or in source code comments. Instead, `htrac serve` exposes a **duress module interface** — a Python protocol class that users implement themselves:

```python
class DuressHandler(Protocol):
    async def on_duress_login(self, session: Session, context: AuthContext) -> None:
        """Called when a duress password is used. Implementation is user-defined."""
        ...

    def filter_response(self, session: Session, response: Response) -> Response:
        """Called on every response during a duress session. May modify, replace, or pass through."""
        ...
```

The user writes their own handler module (a single Python file, not committed to version control, not tracked by git), registers it in `config.yaml` under `auth.duress_module`, and the server loads it at startup. What that module does — filter items, show synthetic data, send alerts, lock things, do nothing, do something nobody has thought of yet — is entirely up to the user.

**Why this is deliberately underspecified:**

- If duress behavior is enumerated in an ADR or config schema, an attacker who reads the source knows what to look for. "Sanitize mode hides stealth items" tells them to check for stealth items. "Lockdown mode sends a webhook" tells them to monitor outgoing connections.
- The strength of a duress system is that the attacker cannot predict what it does. A user-defined module with no public documentation achieves this.
- The module file itself should be excluded from version control (gitignored), stored in a location the agent does not read, and ideally encrypted at rest. The server loads it into memory at startup and does not expose its contents through any API.

**What the framework guarantees (without revealing behavior):**

- The duress password produces a valid session token indistinguishable from a normal token.
- `filter_response` is called on every response, giving the module full control over what the client sees.
- `on_duress_login` is called exactly once per duress session, synchronously before the session token is issued to the client.
- The module has access to the `TrackerSet`, the federation peer list, and the network transport layer — it can do anything the server can do.
- Timing: `on_duress_login` and `filter_response` must complete within the same latency envelope as normal operations. The framework enforces a timeout to prevent a slow module from creating a detectable timing difference.

### Machine-to-machine authentication

Node-to-node connections (for federation sync per ADR-0021) use a separate auth mechanism from human access. The human with the YubiKey is the **root of trust** for the entire federation — they decide which machines can talk to each other.

**Node identity:**

Each htrac node has an **Ed25519 identity keypair** generated on first run (`htrac init --federation`). The public key is the node's identity. This is independent of the transport — the same identity works whether the connection arrives over Tor, WireGuard, or a future transport.

**Transport-level auth (defense in depth):**

| Transport | Auth mechanism | What it proves |
|-----------|---------------|----------------|
| Tor onion service | v3 client authorization (x25519 keypair) | Client is authorized to connect to this onion service |
| WireGuard | Peer public key in config | Peer holds the corresponding private key |
| Either | Ed25519 signed sync requests | Request originated from a known node identity |

Transport-level auth and application-level auth are independent. A compromised Tor client auth key lets an attacker connect to the onion service, but sync requests will be rejected if they aren't signed by a registered node identity. Two layers, either sufficient to block an unauthorized peer.

**Provisioning flow (human-mediated):**

The human provisions peers. No automatic discovery, no trust-on-first-use.

1. New node generates its Ed25519 identity keypair: `htrac init --federation`.
2. New node displays its public key (QR code in TUI, or base64 string for copy/paste).
3. Human transfers the public key to their phone (scan QR, or paste into the app).
4. Human authenticates on the phone app (YubiKey + password + Face ID — full four-factor).
5. Phone app sends a `register-peer` command to each existing node that should trust the new one. The command includes the new node's public key and the sync tiers it should have access to.
6. Existing nodes add the new peer to their `config.yaml` federation section.
7. Human sends existing nodes' public keys + onion addresses back to the new node (same mechanism, reverse direction).

**Revocation:**

Same flow in reverse. Human sends a `revoke-peer` command from the phone app. Nodes remove the peer from their config, close active connections, and reject future sync requests from that identity. Revocation is immediate — no certificate expiry to wait for.

**What the YubiKey protects (and what it doesn't):**

Compromising a single node gives the attacker that node's Ed25519 key, Tor client auth key, and WireGuard key. They can impersonate that node to its peers — including reading all synced data and writing ops as that node — for as long as the node remains trusted.

What the attacker **can** do without the YubiKey:
- Read everything the compromised node can read from its peers (all synced tiers for that node's trust level)
- Write ops as the compromised node to any peer that accepts writes from it
- Impersonate the compromised node indefinitely until the human revokes it

What the attacker **cannot** do without the YubiKey:
- Add new peers to the federation (requires `register-peer`, which requires four-factor human auth)
- Change which tiers a peer shares with the compromised node (trust configuration is human-mediated)
- Access the web UI as the human (separate auth boundary: WebAuthn + password + Face ID)
- Access nodes that the compromised node had no prior trust relationship with

**Blast radius is bounded by the compromised node's trust level.** If Node A only has canonical-tier sync with Node B, compromising Node A exposes canonical items but not Node B's workspace or stealth items. This is why per-peer tier configuration matters — it's not just an organizational convenience, it's a containment boundary.

**Detection and response:** The human's primary mitigation is revocation. Once a compromise is detected, the human authenticates on the phone app (four-factor) and sends `revoke-peer` to all nodes that trusted the compromised node. Revocation is immediate. The window of exposure is the time between compromise and revocation. The duress module (if the compromise involves coercion of the human) provides an additional response path — but that is user-defined and deliberately unspecified (see above).

### Client apps: one web frontend, shared Rust core, two native shells

The UI is a **web frontend** — a single codebase of HTML/CSS/JS/TypeScript with canvas libraries for rich interactions (diagramming, annotation, entity relationships, Apple Pencil support). It runs identically in every context. The native shells are thin wrappers that provide transport orchestration and auth gating — they contain no UI code. A **shared Rust core** implements the transport layer once, used by both platforms.

**Web frontend (shared across all platforms):**

- Served as static assets by `htrac serve` (or bundled with the native shells)
- Connects to `htrac serve` via WebSocket for real-time state sync
- Canvas/SVG for diagramming, annotation, spatial layout (Excalidraw, tldraw, Konva, D3, or similar)
- Responsive layout: full canvas workspace on desktop/iPad, triage-optimized on phone
- Apple Pencil support via standard pointer events in the browser/webview
- All canvas interactions are client-side (local rendering at 120fps); structured data is sent to the server on save

**Shared Rust core (transport layer):**

A single Rust static library implements the transport layer for both platforms:

| Component | Crate | Role |
|-----------|-------|------|
| Tor client | `arti` | Onion service connectivity and control channel |
| WireGuard (desktop only) | `boringtun` | UDP tunnel management (not used on iOS — see below) |
| STUN discovery | custom / `stun_codec` | NAT traversal for WireGuard |
| Ed25519 identity | `ed25519-dalek` | Node identity keypair, request signing |
| Local proxy | `hyper` / custom | HTTP/WebSocket proxy on `localhost`, transport selection and failover |
| WireGuard status | platform trait | Abstract interface: `is_up()`, `endpoint()`, `activate()`, `deactivate()` |

On desktop (Tauri), these crates are called directly — the Rust backend is native, and `boringtun` manages WireGuard in-process. On iOS, the core is compiled as a static library and linked via C FFI in an XCFramework. The Swift shell calls `htrac_proxy_start(config) -> port`, `htrac_proxy_stop()`, `htrac_proxy_status() -> TransportStatus` through a thin C bridge.

**WireGuard is platform-native.** On desktop, `boringtun` manages the WireGuard tunnel in-process within the Rust core. On iOS, WireGuard is handled entirely by the native Swift `NEPacketTunnelProvider` (Network Extension) — `boringtun` is not compiled for iOS targets. The Rust core queries tunnel status via a platform-abstracted trait interface, so the failover state machine does not need to know which implementation is active.

The transport *state machine* — start on Tor, attempt WireGuard upgrade via STUN, failover to Tor if WireGuard drops — is implemented once in the Rust core and behaves identically on both platforms. WireGuard tunnel management is the one platform-specific component: `boringtun` on desktop, `NEPacketTunnelProvider` on iOS. Fixing a failover edge case in the state machine fixes it everywhere; fixing a WireGuard tunnel bug requires platform-specific work.

**Costs of the shared core approach:**
- **App Store review.** Embedding a Rust static library is unusual in the iOS ecosystem. Precedent exists: Signal, 1Password, and Firefox all ship Rust via FFI on iOS and have passed review.
- **Cross-compilation.** Building for iOS targets (`aarch64-apple-ios`, `aarch64-apple-ios-sim`) requires a configured Rust toolchain and XCFramework packaging. CI must cross-compile on every release.
- **FFI boundary discipline.** Rust panics across FFI are undefined behavior. Every C-exported function must catch panics at the boundary and return a result type. Debugging requires correlating Swift and Rust stack frames.
- **Binary size.** `arti` adds ~5-15MB to the iOS binary (statically linked). `boringtun` is not included on iOS (WireGuard is handled by the native Network Extension), reducing the iOS binary delta. Acceptable for most apps but notable.

**Desktop shell: Tauri (Rust)**

| Component | Role |
|-----------|------|
| Shared Rust core | Tor, WireGuard, STUN, Ed25519, local proxy (in-process, native) |
| Tauri framework | Window management, system tray, build/packaging |
| System webview | Renders the web frontend (WebKit on macOS, WebKitGTK on Linux) |

Tauri produces small binaries (~5-10MB plus the shared core), uses the system webview (no bundled Chromium), and Rust is excellent for the crypto and transport work. Builds for macOS, Linux, and Windows from one codebase.

WebAuthn works natively in the system webview on macOS (Safari's WebKit supports FIDO2 security keys). No bounce to an external browser session needed on desktop.

**iOS/iPadOS shell: Swift + WKWebView**

A thin native shell (~500-800 lines of Swift) that handles platform integration and auth. Transport logic lives in the shared Rust core. The web frontend does all the UI work.

| Component | Role |
|-----------|------|
| Shared Rust core (via C FFI) | Tor, STUN, Ed25519, local proxy (no `boringtun` — WireGuard is native) |
| `LAContext` (LocalAuthentication) | Face ID / Touch ID biometric gate (client-side, before any network activity) |
| `ASWebAuthenticationSession` | YubiKey WebAuthn challenge (bounces to system browser sheet — required because `WKWebView` does not support FIDO2 security keys) |
| Password field (native UI) | Real password or duress password entry |
| `WKWebView` | Renders the web frontend — same code as desktop |
| `NEPacketTunnelProvider` extension | WireGuard tunnel (native Swift — OS-level VPN interface, separate from app-level proxy) |
| App Groups shared container | Transport state, session tokens, endpoint candidates, peer provisioning |

Note: `NEPacketTunnelProvider` is the **sole WireGuard implementation on iOS**. It runs in a separate process (Network Extension), conforms to Apple's VPN API, and handles all WireGuard tunnel management natively in Swift. The Rust core does not include `boringtun` on iOS — it handles Tor, STUN discovery, Ed25519, and the local proxy, and queries WireGuard tunnel status from the Network Extension via the App Groups shared container. This is a deliberate platform split: WireGuard tunnel management is `boringtun` (Rust, in-process) on desktop and `NEPacketTunnelProvider` (Swift, Network Extension) on iOS. The failover state machine in the Rust core is platform-agnostic — it uses the abstract tunnel status interface regardless of which implementation is active.

The same app binary runs on iPhone and iPad. The only difference is screen real estate — the web frontend adapts via responsive CSS. On iPad, the full canvas workspace with Apple Pencil support is available. On iPhone, the layout optimizes for triage, status updates, and discussion.

**Local proxy (both platforms):**

Each native shell (Tauri and iOS) runs a local HTTP/WebSocket proxy on `127.0.0.1:<port>`. The system webview (`WKWebView` on iOS, WebKit/WebKitGTK on desktop) always connects to `localhost` — never directly to the onion address or WireGuard tunnel IP. The proxy handles transport selection (Tor vs WireGuard), failover, and reconnection transparently.

This architecture solves two problems:

1. **Session cookie domain.** The webview always sees `localhost` as the origin, regardless of which transport is active. Cookies set on `localhost` survive transport switches. No cross-origin issues, no custom auth headers, no transport-aware cookie management.
2. **App Transport Security (iOS).** ATS allows cleartext HTTP to `localhost` without an exception entry. No self-signed certificate, no ATS plist override, no TLS termination at the proxy. The proxy binds to `127.0.0.1` only (not `0.0.0.0`), so there is no network exposure.

The proxy is implemented in the shared Rust core (see above) — one implementation, used by both Tauri and the iOS shell.

**Product flow (iOS/iPadOS):**
1. App launches. Immediately presents **Face ID** via `LAContext`. Fails → app stays locked.
2. On biometric success, establishes Tor control channel to the onion service.
3. Presents **YubiKey WebAuthn** login via `ASWebAuthenticationSession`. Server issues challenge, YubiKey signs it.
4. On WebAuthn success, app presents a **password field** (native UI, not in WKWebView).
5. User enters either the real password or the duress password. Both produce a successful login. The server determines which was used and tags the session accordingly.
6. App receives session cookie, loads `WKWebView` with the web frontend. If duress: the server silently filters responses per the duress module. The UI looks normal.
7. In background, the app contacts the STUN reflector, discovers its public UDP mapping, and sends the candidate to the VM over the Tor channel.
8. Both sides attempt WireGuard handshake. If successful, the local proxy routes traffic through the WireGuard tunnel. `WKWebView` stays connected to `localhost` — the switch is invisible. Bulk traffic moves off Tor.
9. If WireGuard fails or drops, falls back to Tor transparently.
10. Session expires after the configured TTL (default 15 minutes); app re-prompts for full four-factor auth.

**Peer provisioning via the iOS app:**
The phone/iPad app also serves as the provisioning device for federation (ADR-0021). After authenticating (with the real password, not duress), the human can:
- Scan a QR code from a new node's TUI to register its Ed25519 public key.
- Send `register-peer` / `revoke-peer` commands to existing nodes.
- View federation topology (which nodes can see which tiers).

This makes the phone + YubiKey the single root of trust for the entire federation.

**Why not Flutter or Electron:**

- **Flutter** would require writing the UI in Dart, separate from the web frontend. The canvas/diagramming ecosystem is weaker in Flutter than on the web platform. Apple Pencil support is less mature. And you'd lose Tauri's Rust backend on desktop.
- **Electron** bundles Chromium (~150MB+). Tauri uses the system webview and is 10-30x smaller.
- **The web frontend is the unifying layer.** One UI codebase in JS/TS, running in Tauri's webview on desktop and WKWebView on iOS. The native shells handle transport and auth only — they are small, platform-specific, and don't duplicate UI code.

### Server-side deployment

The full server-side stack on the VM:

```
systemd
├── htrac-serve.service        # htrac serve --background
├── tor.service                # publishes onion service, forwards to htrac
└── wg-quick@htrac.service     # WireGuard interface (optional)
```

- **`htrac serve`** is the application. See the Server section above for architecture.
- **Tor** is configured via `torrc` to publish the onion service, forwarding to `htrac serve`'s local listener.
- **WireGuard** peer configuration is managed by `htrac serve` (generates keys, writes config, signals `wg-quick` to reload).
- **`htrac setup --remote`** (future) automates: Tor installation/config, WireGuard key generation, systemd unit installation, STUN reflector setup on a VPS, and iOS app provisioning profile generation.

## Consequences

### Benefits

- **Rich UI everywhere** — desktop diagramming with full canvas, iPad with Apple Pencil, phone for triage — all from one web frontend codebase.
- **No inbound ports on the VM.** Both Tor and WireGuard initiate outbound connections only.
- **Four-factor auth** with hardware presence (YubiKey), knowledge (password), biometric (Face ID), and voluntary intent (duress detection). Each factor defeats a distinct threat.
- **Duress resilience.** Under coercion, the system accepts a valid-looking login but triggers a user-defined, deliberately unspecified response.
- **Good Tor citizenship.** Tor is valued infrastructure, always available. WireGuard automatically takes bulk traffic off volunteer relays when a direct path exists.
- **No managed infrastructure required.** STUN/TURN is pluggable — any public or self-hosted server works. `coturn` handles both STUN and TURN in a single deployment. No proprietary service dependency.
- **One web frontend, shared Rust core, two thin shells.** UI code is written once. The transport state machine (Tor, STUN, failover) and crypto (Ed25519) are implemented once in Rust and shared via FFI. WireGuard tunnel management is platform-native (`boringtun` on desktop, `NEPacketTunnelProvider` on iOS) — a deliberate split that follows each platform's grain. Platform-specific code is limited to auth gating, WireGuard integration, and OS integration (~500-800 lines of Swift on iOS, Tauri framework on desktop).
- **Self-contained Python server:** `py_webauthn` and the WebSocket server are pip dependencies. No companion binaries on the server side.
- **Phone + YubiKey as root of trust for federation.** The same device that authenticates the human also provisions machine-to-machine trust (ADR-0021).

### Costs

- **Shared Rust core + two native shells.** The transport layer is implemented once in Rust and shared via FFI. The Tauri desktop shell uses it natively; the iOS shell calls it via C bridge. The Swift shell is small (~500-800 lines) but still requires iOS development expertise and an Apple Developer account. The shared core adds cross-compilation infrastructure (XCFramework packaging for iOS targets) and FFI boundary discipline (panic catching, result types). App Store review for embedded Rust is an accepted risk with established precedent (Signal, 1Password, Firefox).
- **Web frontend is the largest piece of new code.** The canvas/diagramming UI, WebSocket client, responsive layout, and offline-capable state management are substantial — this is a real web application, not a simple dashboard.
- **Four-factor auth UX** adds friction to every session. The configurable TTL (default 15 minutes) means re-authenticating frequently. This is the intended tradeoff — security over convenience. Users who want less friction can increase the TTL in `config.yaml`.
- **Duress module** shifts implementation burden to the user. The framework provides the hooks (`on_duress_login`, `filter_response`) but the user must write a handler that produces a convincing session. No default implementation is shipped — shipping one would document the behavior.
- **STUN/TURN dependency for WireGuard.** WireGuard requires STUN for NAT discovery and TURN for relay fallback on symmetric NAT (common on mobile carriers). Public STUN servers are freely available; TURN requires a self-hosted or paid relay (`coturn` handles both). Self-hosting is simple but is still infrastructure to maintain.
- **Two transport paths** means testing and debugging transport selection, failover, and session continuity across path changes. The Tor → WireGuard upgrade and fallback must be transparent and reliable.
- **Ed25519 key management** for federation adds operational surface: key generation, distribution, storage, revocation. Lost keys require human re-provisioning.

### Dependencies

**Server (Python, pip-installable):**
- `aiohttp` or `uvicorn` + `starlette` — async HTTP/WebSocket server
- `py_webauthn` — WebAuthn/FIDO2 registration and authentication
- `bcrypt` — password hashing (real and duress passwords)
- `PyNaCl` or `cryptography` — Ed25519 node identity keys, session token generation
- `cairosvg` — SVG rasterization for inline previews (see ADR-0020, optional)

**System (not bundled):**
- Tor daemon — publishes onion service
- WireGuard tools — tunnel management
- systemd — process supervision for `htrac serve`

**Shared Rust core (static library, used by both desktop and iOS):**
- `arti` — Tor client
- `boringtun` — WireGuard (desktop only; iOS uses native `NEPacketTunnelProvider`)
- `stun_codec` or custom — STUN NAT discovery
- `ed25519-dalek` — node identity and request signing
- `hyper` or custom — local HTTP/WebSocket proxy

**Desktop client (Tauri):**
- Rust toolchain — Tauri backend + shared core (in-process, native)
- System webview (WebKit on macOS, WebKitGTK on Linux)

**iOS/iPadOS client (Swift shell):**
- Xcode + Apple Developer account
- Shared Rust core (via XCFramework, C FFI)
- `NEPacketTunnelProvider` for WireGuard (native Swift, OS-level VPN interface)
- `WKWebView` for web frontend

**Web frontend (JS/TS, shared):**
- Canvas/diagramming library (Excalidraw, tldraw, Konva, D3, or similar)
- WebSocket client library
- Build toolchain (Vite, esbuild, or similar)

**External (not bundled, pluggable):**
- STUN/TURN server — any public or self-hosted reflector/relay for WireGuard NAT discovery and traversal (e.g., `coturn` for both)

### Open questions

- **Android client:** The transport architecture (Tor + WireGuard, local proxy, shared Rust core) translates to Android. Platform differences: `WebView` (Chromium-based, different security properties from WebKit), `BiometricPrompt` (replaces LAContext), Chrome Custom Tabs (replaces ASWebAuthenticationSession), Android VPN API (replaces NEPacketTunnelProvider). The shared Rust core links via JNI/JNA instead of C FFI. Not in scope for v1.
- **Per-App VPN on iOS:** `NETunnelProviderManager` programmatic Per-App VPN requires MDM. The app will use a full-device tunnel or app-scoped tunnel via the packet tunnel extension.
- **Tor client on iOS:** Resolved: `arti` is embedded via the shared Rust core (C FFI through XCFramework). This is the same approach used by Signal and other apps that embed Rust on iOS. Adds ~5-15MB to binary size. App Store review risk is accepted based on precedent.
- **Duress module distribution:** Users need guidance on writing effective handlers without that guidance itself becoming a roadmap for attackers. Consider a separate, access-controlled document or in-person knowledge transfer rather than public docs.
- **Session continuity across transport switch.** Resolved: the local proxy architecture eliminates this concern. `WKWebView` always connects to `localhost` — the proxy handles transport switches transparently. Cookie domain is always `localhost`, so session cookies survive transport changes.
- **Face ID fallback.** If the device doesn't have Face ID (older iPhone, iPad without TrueDepth), should the app accept Touch ID? Passcode? Or refuse to run? Recommendation: accept any `LAPolicy.deviceOwnerAuthenticationWithBiometrics` result, which covers both Face ID and Touch ID. Do not fall back to passcode — that weakens the biometric factor to a knowledge factor.
- **Web frontend framework choice.** The ADR specifies canvas libraries (Excalidraw, tldraw, Konva, D3) but not the application framework (React, Svelte, vanilla, etc.). This affects bundle size, developer experience, and ecosystem compatibility with the chosen canvas library. Decide during implementation.
- **Tauri on Linux without WebKitGTK.** Some headless Linux servers (where `htrac serve` runs) may not have WebKitGTK. The Tauri desktop app is a *client* — it runs on the user's desktop machine, not on the server VM. But if someone wants to run the desktop client on the same Linux box via X forwarding, WebKitGTK becomes a dependency. Clarify that the Tauri app is for desktop client machines, not servers.
- **App Store review for Tor.** Apple has historically been cautious about apps that embed Tor functionality. The app should frame Tor as a privacy/connectivity feature, not an anonymity tool. The STUN reflector + WireGuard as primary data path (with Tor as fallback) may help the review narrative.
