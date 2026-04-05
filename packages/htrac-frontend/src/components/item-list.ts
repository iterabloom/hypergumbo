/**
 * Tracker item list component.
 *
 * Renders a scrollable list of tracker items sorted by priority. Each item
 * shows title, status badge, priority, and kind. Clicking an item dispatches
 * an "item-select" event with the item ID.
 */
import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { TrackerItem } from "../ws-client.js";

const STATUS_COLORS: Record<string, string> = {
  todo_hard: "#e74c3c",
  todo_soft: "#3498db",
  violated: "#e74c3c",
  pending_validation: "#f39c12",
  done: "#2ecc71",
  satisfied: "#2ecc71",
  wont_do: "#95a5a6",
  needs_human_review: "#9b59b6",
};

@customElement("item-list")
export class ItemList extends LitElement {
  static styles = css`
    :host {
      display: block;
    }

    .header {
      padding: 16px;
      font-size: 1.2rem;
      font-weight: 600;
      border-bottom: 1px solid var(--border, #2a2a4a);
      background: var(--bg-elevated, #16213e);
    }

    .item {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 16px;
      cursor: pointer;
      border-bottom: 1px solid var(--border, #2a2a4a);
      transition: background 0.15s;
    }

    .item:hover {
      background: var(--bg-hover, #1f2b47);
    }

    .item[data-selected] {
      background: var(--bg-selected, #1a3a5c);
    }

    .priority {
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--text-muted, #888);
      min-width: 24px;
    }

    .content {
      flex: 1;
      min-width: 0;
    }

    .title {
      font-size: 0.9rem;
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .meta {
      display: flex;
      gap: 8px;
      margin-top: 4px;
      font-size: 0.75rem;
      color: var(--text-muted, #888);
    }

    .status-badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-size: 0.7rem;
      font-weight: 600;
      color: #fff;
    }

    .kind {
      opacity: 0.7;
    }

    .count {
      padding: 4px 16px;
      font-size: 0.8rem;
      color: var(--text-muted, #888);
      border-bottom: 1px solid var(--border, #2a2a4a);
    }
  `;

  @property({ type: Array }) items: TrackerItem[] = [];
  @property({ type: String }) selectedId: string | null = null;

  private get sortedItems(): TrackerItem[] {
    return [...this.items].sort((a, b) => a.priority - b.priority);
  }

  private onSelect(id: string): void {
    this.dispatchEvent(new CustomEvent("item-select", { detail: id }));
  }

  render() {
    const sorted = this.sortedItems;
    return html`
      <div class="header">htrac</div>
      <div class="count">${sorted.length} items</div>
      ${sorted.map(
        (item) => html`
          <div
            class="item"
            ?data-selected=${item.id === this.selectedId}
            @click=${() => this.onSelect(item.id)}
          >
            <span class="priority">P${item.priority}</span>
            <div class="content">
              <div class="title">${item.title}</div>
              <div class="meta">
                <span
                  class="status-badge"
                  style="background: ${STATUS_COLORS[item.status] ?? "#555"}"
                  >${item.status.replace(/_/g, " ")}</span
                >
                <span class="kind">${item.kind}</span>
              </div>
            </div>
          </div>
        `,
      )}
    `;
  }
}
