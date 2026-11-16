<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0062: Tracker Live-Document Layer — a Yjs Buffer over the Ops Log

Status: Accepted
Date: 2026-10-29
Related: [ADR-0013](0013-structured-tracker.md) (the ops log), [ADR-0019](0019-remote-access-transport.md) (serve; its frontend row names this ADR)
Implementation items: WI-lakid (projection), WI-rudat (write-back bridge), WI-vojil (sync endpoint); later WI-fujus, WI-kinab (views), WI-divug (erasure), WI-pufun (encryption)

## Context

ADR-0019, as amended on 2026-10-17, replaced BlockSuite as the tracker's web
frontend with a document built on Yjs (a CRDT library). The reason is recorded
there, under "Why not BlockSuite". The frontend still needs a data model. It
has to be live, so that several people or agents can edit at once and see each
other. It also has to leave the tracker's guarantees untouched. The ops log
(`.ops` files under `flock`, Lamport clocks, the journal; ADR-0013) is where
validation, locks, edit-mode windows, governance and the human-authority rules
(INV-hivog) are enforced. Anything that bypasses it bypasses all of those.

PlazaFlow's spec draws the same line: "Git is the durability layer, the CRDT is
the live collaboration buffer". This layer rehearses PlazaFlow v0.1–v0.2 on the
tracker: sync, awareness and attribution now, encryption later.

## Decision

The live document is a **projection of, and an edit buffer over, the ops log —
never a replacement for it.** Six decisions follow.

### 1. Document shape: one Y.Doc per tracker root; stealth items are not in it

```
Y.Doc
├── items: Y.Map<item_id, Y.Map>          one entry per canonical and workspace item
│   ├── (op-only, plain values)           kind, status, priority, tags, parent,
│   │                                     isbefore, tier, pr_ref, fields,
│   │                                     locked_fields, frozen, created_at, updated_at
│   ├── title: Y.Text                     live-editable
│   ├── description: Y.Text               live-editable
│   ├── discussion: Y.Array<Y.Map>        committed entries; op-only
│   │   └── {by, actor, at, is_summary, message: string}
│   └── drafts: Y.Map<client_token, Y.Text>   live-editable; one unposted entry per client
└── meta: Y.Map                           projection version, ops-log position
```

- **Live-editable:** `title`, `description`, and a draft discussion entry.
  Nothing else. Each one maps to exactly one existing op: `update --title`,
  `update --description`, `discuss`.
- **Op-only:** everything that validation or governance reads: status,
  priority, tags, parent, isbefore, fields, `locked_fields`, `frozen`, and every
  posted discussion entry.
  - A posted entry changes only through `discuss_clear` /
    `discuss_summarize`, or through `delete-msg` / `undelete-msg` /
    `edit-msg-text` inside an edit-mode window that a human opened. Those are
    human-authority ops, and the live doc does not offer them.
  - Op-only values are plain values, not shared types, so a client has nothing
    to merge into. A client that writes one anyway is overwritten by the next
    projection (§2).
- **The stealth tier is not projected.** Stealth items are gitignored and
  "never leave the machine" (ADR-0013). The serve frontend exists to be reached
  from a phone over Tor (ADR-0019 Part A), so a doc that contains stealth items
  would carry them off the machine.

### 2. Authority: the ops log wins, and a live edit becomes real only as an op

- The projection writes inside a transaction whose Yjs origin is the projection.
  The bridge ignores those transactions (idempotence; WI-lakid, WI-rudat).
- A live edit becomes a tracker change only when the bridge emits the
  corresponding op through `TrackerSet` / `Store`. The bridge never writes an
  `.ops` file. Validation, locks, edit-mode windows and the journal apply to it
  exactly as they apply to the CLI.
- **Commit points.** `title` and `description` commit after 5 s with no
  edits to that field. A draft commits when the client sets its `post` flag.
  The bridge does not emit an op per keystroke, because every op is a line in
  an `.ops` file, and line count drives auto-sync (threshold 80 lines).
- **Conflict.** If an op (from the CLI, the TUI or another bridge commit) lands
  on a live-editable field that has uncommitted live edits, the op wins. The
  projection replaces the field, and the editing client is told through
  awareness that its uncommitted text was superseded. The bridge does not
  re-apply that text: doing so would silently overwrite a change made in a
  surface the editor cannot see.
- An edit the bridge refuses converges back. Refused edits are: an op-only
  field, a field in `locked_fields`, a `frozen` item, or an unknown client
  (§3). The bridge re-projects the authoritative value. `locked_fields` and
  `frozen` are projected, so the client can render those fields read-only.

### 3. Attribution: a pseudonymous token in the doc, the identity outside it

- Every live change carries the Yjs `clientID` as a **pseudonymous token**.
- The table that maps a token to an author (`by:`, `actor`) lives in the server
  process, and never in the doc. The server fills it at WebSocket handshake from
  the connection that serve's authenticator accepted.
- A change from a token that is not in the table is refused and reverted (§2).
  There are no anonymous ops.
- A committed op carries the resolved `by` / `actor`, exactly as a CLI op does.
  Those are already visible to anyone who can read the ops log, so projecting
  them for posted entries leaks nothing new.
- What stays withheld is who is typing what before it is posted. That is the
  PlazaFlow v1.2 §4.8.5 requirement: the clientID-to-keypair map never travels
  in plaintext. It also avoids §4.8.2's design, which stores `meta:createdBy`
  public keys inside the shared document where every content reader sees them.
- The out-of-doc table is also what makes erase-by-author possible (WI-divug).

### 4. Awareness: no identity

`{item_id: str | null, label: str, kind: "human" | "agent"}`.

- `label` is a per-session pseudonym assigned by the server (`human-1`,
  `agent-2`), never a name or key. PlazaFlow §4.8.5 names awareness as the
  primary leak vector.
- BlockNote's collaboration cursor (`user: {name, color}`) is given the same
  label.

### 5. Transport: Yjs 13, a pycrdt sync route inside serve's guarded app

- **Yjs 13.6.** As of 2026-10-29, `yjs` latest is 13.6.33 (2026-09-23), and
  14 is still pre-release (`yjs@14.0.0-16` beta; `@y/y` 14.0.0-rc.29 beta).
  `y-websocket` 3.1.0 peers on `yjs ^13.5.6`.
  - BlockNote 0.56 (`@blocknote/core`) peers on either `yjs ^13.6.33` or
    `@y/y ^14`, so 13 costs the frontend nothing.
  - Revisit when `yjs` tags 14 as `latest` and pycrdt states compatibility
    with it.
- **Python side: `pycrdt`** (0.14.10, 2026-10-19), with **`pycrdt-websocket`**
  (0.16.5, 2026-09-20) for the y-protocols sync and awareness messages.
  `y-py` (0.6.2, last released 2023-10-05) is unmaintained.
  - `pycrdt-store` is not used. The doc is rebuilt from the ops log at
    startup, so it needs no persistence of its own.
  - As a consequence, uncommitted live edits are lost on restart.
- **Where it runs:** a `WebSocketRoute("/yjs", ...)` in `create_app`'s route
  list. That list is wrapped by WI-hopip's guard, so the route is denied
  unless an authenticator admits the connection. Admission is also what fills
  the token table (§3). There is one room per tracker root, fixed by the
  server; the client cannot choose a room.
- **A sidecar `y-websocket` (Node) relay is rejected.**
  - The bridge and the token table have to live in the process that holds the
    `TrackerSet` and authenticated the connection, so a relay would still need
    a Python client of itself.
  - The relay would be a second listener outside the guard.
  - It would store plaintext updates.
- **Dependency, stated rather than raised.** `run_server` calls
  `create_app()` with neither a TrackerSet nor an authenticator. Wiring them
  in is WI-pupod, which the owner has deferred indefinitely. Until it lands,
  `htrac serve` answers only `/health`, and the live layer runs where the tests
  run it: an app from `create_app(tracker_set=..., authenticate=...)`, driven
  in-process.

### 6. Security posture and non-goals

- 127.0.0.1 only, hard-coded rather than a flag, until WI-sunan (tracker
  self-claims) lands. A widening beyond loopback is a new decision, not a
  config change.
- The `/yjs` route sits inside the WI-hopip guard, so
  `test_serve_auth_gate.py`'s route enumeration covers it automatically.
- No encryption in this layer; client-side encrypted updates are WI-pufun.
- Not multi-repo, and not federation (ADR-0021).
- Not a replacement for `tracker sync` or auto-sync. Ops reach git the way they
  always have.

## Consequences

- Every guarantee that lives in the ops path holds for live edits, because a
  live edit is an op by the time it is real. This covers validation, field
  locks, `frozen`, the edit-mode windows that gate message edits, the
  human-authority rules (INV-hivog) and journal durability.
- Live editing produces ops at commit points. A busy editing session raises the
  pending-op line count, and with it the auto-sync rate.
- Uncommitted live text is not durable: it is lost on restart, or when an op
  supersedes it, and the editor is told in the second case.
- The doc carries `by` / `actor` only for content that is already in the
  ops log.

## Alternatives considered

- **The Y.Doc as the source of truth, with the ops log as an export.** This
  would move validation, locks and governance out of the one path that
  enforces them. Rejected.
- **All fields live-editable.** Status, priority, tags and links are what
  governance and `tracker ready` read. A CRDT merge of two concurrent status
  edits produces a status nobody chose. Rejected.
- **Identity in awareness** (the y-websocket examples' `user.name`). This is
  the leak PlazaFlow §4.8.5 names. Rejected for a server-assigned pseudonym.
- **BlockSuite.** See ADR-0019, "Why not BlockSuite (re-decided 2026-10-17)".
