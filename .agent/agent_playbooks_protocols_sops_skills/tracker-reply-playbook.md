<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
## Tracker Reply Playbook

When `scripts/tracker check-messages` surfaces unread human messages, reply substantively before starting new feature work. Drive-by acknowledgments ("Acknowledged") waste the human's attention without advancing the conversation.

### When to Use

- At session start, when the stop hook or `check-messages` surfaces unread human messages
- Before starting any new feature branch (part of the pre-work mental checklist)
- When explicitly prompted by the stop hook's "REPLY-FIRST" guidance

### The Four-Step Protocol

**Step 1 — Read the message in context.** Use `scripts/tracker show <ID>` to see the full item description, all prior discussion entries, and the human's latest message. Do not reply based on the notification preview alone — it lacks the thread context needed for a substantive response.

**Step 2 — Classify the message.** Determine what the human is asking for:

| Classification | Examples | Required Response |
|---------------|----------|-------------------|
| **Approval** | "I approve governance changes", "go ahead" | Acknowledge, update item status, proceed with implementation |
| **Directive** | "keep a running list of deferred bakeoffs", "not done until we get a tracker item" | Create the requested artifact (tracker item, document, etc.), reference it in your reply |
| **Question** | "might analogous situations arise for other languages?", "what was the motivation?" | Investigate thoroughly, report findings with evidence, propose next steps |
| **Tabling** | "tabling this until there's a compelling need" | Acknowledge, update status (wont_do with someday tag or needs_human_review), do not work on it |
| **Correction** | "that was created by *you* not me!" | Acknowledge the error, investigate the correct answer, update your understanding |

**Step 3 — Reply with substance.** Your `tracker discuss` reply should:
- Directly address the human's message (not just acknowledge it)
- Include evidence, references, or artifacts when the classification calls for them
- If the message requires creating a tracker item or other artifact, create it first, then reference it in the reply
- If the message requires investigation, do the investigation before replying — do not promise to "investigate next session"

**Step 4 — Update item status if warranted.** After replying:
- If the human approved work: change status to `todo_soft` (from `needs_human_review`) and proceed
- If the human tabled work: change status to `wont_do` with appropriate tags
- If the human gave a directive that changes scope: update the item description
- If the reply completes the item's purpose: mark `done` with a resolution note

### Anti-Patterns

- **Drive-by acknowledgment.** "Acknowledged. Will implement next session." This tells the human nothing they didn't already know. Instead: "Acknowledged. Created WI-xyz for the follow-up. The is_test_file boolean landed in PR #2813; the tier-slice query upgrade is tracked separately."

- **Replying in the same turn as starting a new feature branch.** If a human message requires investigation, do the investigation before switching to feature work. Starting a branch while a reply is pending creates cognitive debt that compounds across sessions.

- **Promising future action instead of acting now.** "Will investigate in the next session" is almost always wrong. The next session won't have the context. If you can't investigate in 5 minutes, create a concrete tracker item with enough detail that the next session can pick it up without re-reading the original message.

- **Replying without reading the full thread.** The human's latest message often refers to something earlier in the discussion. Reading only the unread message produces non-sequiturs.

### Integration with Stop Hook

The stop hook surfaces unread messages via `scripts/tracker check-messages`. When unread messages exist, the hook guidance includes a "reply debt" count. The intent is that replying to human messages takes priority over starting new feature work — human attention is the scarcest resource in the system.
