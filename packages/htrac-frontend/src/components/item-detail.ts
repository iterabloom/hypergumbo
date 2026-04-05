/**
 * Tracker item detail component.
 *
 * Renders full item details: title, status, priority, description, tags,
 * and discussion thread. Supports update (status change) and discuss (add
 * message) actions via WebSocket commands.
 */
import { LitElement, html, css, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { type TrackerItem, sendCommand } from "../ws-client.js";

const VALID_STATUSES = [
  "todo_hard",
  "todo_soft",
  "violated",
  "pending_validation",
  "done",
  "satisfied",
  "wont_do",
  "needs_human_review",
];

@customElement("item-detail")
export class ItemDetail extends LitElement {
  static styles = css`
    :host {
      display: block;
      padding: 20px;
      max-width: 800px;
    }

    .back-btn {
      display: none;
      margin-bottom: 12px;
      padding: 6px 12px;
      background: var(--bg-elevated, #16213e);
      border: 1px solid var(--border, #2a2a4a);
      border-radius: 4px;
      color: var(--text, #e0e0e0);
      cursor: pointer;
      font-size: 0.85rem;
    }

    @media (max-width: 768px) {
      .back-btn {
        display: inline-block;
      }
    }

    h1 {
      font-size: 1.3rem;
      margin: 0 0 12px;
      line-height: 1.3;
    }

    .meta-row {
      display: flex;
      gap: 16px;
      margin-bottom: 16px;
      font-size: 0.85rem;
      flex-wrap: wrap;
    }

    .field-label {
      color: var(--text-muted, #888);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 2px;
    }

    .field-value {
      font-size: 0.9rem;
    }

    .tag {
      display: inline-block;
      padding: 2px 8px;
      background: var(--bg-elevated, #16213e);
      border-radius: 3px;
      font-size: 0.8rem;
      margin-right: 4px;
    }

    .description {
      margin: 16px 0;
      padding: 12px;
      background: var(--bg-elevated, #16213e);
      border-radius: 6px;
      font-size: 0.9rem;
      line-height: 1.5;
      white-space: pre-wrap;
    }

    .section-title {
      font-size: 0.85rem;
      font-weight: 600;
      margin: 20px 0 8px;
      color: var(--text-muted, #aaa);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .discussion-entry {
      padding: 10px 12px;
      margin-bottom: 8px;
      background: var(--bg-elevated, #16213e);
      border-radius: 6px;
      border-left: 3px solid var(--border, #2a2a4a);
    }

    .discussion-entry[data-by="human"] {
      border-left-color: #9b59b6;
    }

    .discussion-entry[data-by="agent"] {
      border-left-color: #3498db;
    }

    .discussion-meta {
      font-size: 0.75rem;
      color: var(--text-muted, #888);
      margin-bottom: 4px;
    }

    .discussion-message {
      font-size: 0.85rem;
      line-height: 1.5;
      white-space: pre-wrap;
    }

    .actions {
      margin-top: 20px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }

    .action-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    select,
    textarea,
    button {
      font-family: inherit;
      font-size: 0.85rem;
      color: var(--text, #e0e0e0);
      background: var(--bg-elevated, #16213e);
      border: 1px solid var(--border, #2a2a4a);
      border-radius: 4px;
      padding: 6px 10px;
    }

    textarea {
      width: 100%;
      min-height: 60px;
      resize: vertical;
      box-sizing: border-box;
    }

    button {
      cursor: pointer;
      padding: 8px 16px;
    }

    button:hover {
      background: var(--bg-hover, #1f2b47);
    }

    .discuss-form {
      margin-top: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .discuss-form button {
      align-self: flex-end;
    }

    .id-text {
      font-family: monospace;
      font-size: 0.75rem;
      color: var(--text-muted, #666);
      word-break: break-all;
    }
  `;

  @property({ type: Object }) item!: TrackerItem;
  @state() private discussMessage = "";

  private onBack(): void {
    this.dispatchEvent(new Event("back"));
  }

  private onStatusChange(e: Event): void {
    const select = e.target as HTMLSelectElement;
    sendCommand("update", {
      item_id: this.item.id,
      set_fields: { status: select.value },
    });
  }

  private onDiscussSubmit(): void {
    const msg = this.discussMessage.trim();
    if (!msg) return;
    sendCommand("discuss", {
      item_id: this.item.id,
      message: msg,
    });
    this.discussMessage = "";
  }

  private onDiscussInput(e: Event): void {
    this.discussMessage = (e.target as HTMLTextAreaElement).value;
  }

  private formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  render() {
    const item = this.item;
    return html`
      <button class="back-btn" @click=${this.onBack}>Back</button>

      <h1>${item.title}</h1>
      <div class="id-text">${item.id}</div>

      <div class="meta-row">
        <div>
          <div class="field-label">Status</div>
          <div class="field-value">
            <select .value=${item.status} @change=${this.onStatusChange}>
              ${VALID_STATUSES.map(
                (s) =>
                  html`<option value=${s} ?selected=${s === item.status}>
                    ${s.replace(/_/g, " ")}
                  </option>`,
              )}
            </select>
          </div>
        </div>
        <div>
          <div class="field-label">Priority</div>
          <div class="field-value">P${item.priority}</div>
        </div>
        <div>
          <div class="field-label">Kind</div>
          <div class="field-value">${item.kind}</div>
        </div>
        <div>
          <div class="field-label">Tier</div>
          <div class="field-value">${item.tier ?? "none"}</div>
        </div>
      </div>

      ${item.tags.length > 0
        ? html`
            <div>
              <div class="field-label">Tags</div>
              <div>${item.tags.map((t) => html`<span class="tag">${t}</span>`)}</div>
            </div>
          `
        : nothing}
      ${item.parent
        ? html`
            <div style="margin-top: 8px">
              <div class="field-label">Parent</div>
              <div class="id-text">${item.parent}</div>
            </div>
          `
        : nothing}

      <div class="description">${item.description || "No description"}</div>

      <div class="section-title">Discussion (${item.discussion.length})</div>
      ${item.discussion.map(
        (d) => html`
          <div class="discussion-entry" data-by=${d.by}>
            <div class="discussion-meta">
              ${d.actor} (${d.by}) &middot; ${this.formatDate(d.at)}
            </div>
            <div class="discussion-message">${d.message}</div>
          </div>
        `,
      )}

      <div class="discuss-form">
        <textarea
          placeholder="Add a message..."
          .value=${this.discussMessage}
          @input=${this.onDiscussInput}
        ></textarea>
        <button @click=${this.onDiscussSubmit}>Send</button>
      </div>

      <div style="margin-top: 16px; font-size: 0.75rem; color: var(--text-muted, #666)">
        Created: ${this.formatDate(item.created_at)} &middot; Updated:
        ${this.formatDate(item.updated_at)}
      </div>
    `;
  }
}
