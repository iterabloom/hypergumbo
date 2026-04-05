/**
 * Root application component for the htrac tracker frontend.
 *
 * Renders a two-panel layout: item list on the left, item detail on the right.
 * On narrow screens (phone), shows only one panel at a time with back navigation.
 * Subscribes to WebSocket state updates and passes items to child components.
 */
import { LitElement, html, css, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { type TrackerItem, onStateUpdate } from "../ws-client.js";
import "./item-list.js";
import "./item-detail.js";

@customElement("tracker-app")
export class TrackerApp extends LitElement {
  static styles = css`
    :host {
      display: flex;
      height: 100vh;
      width: 100vw;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
      color: var(--text, #e0e0e0);
      background: var(--bg, #1a1a2e);
    }

    .list-panel {
      width: 360px;
      min-width: 280px;
      border-right: 1px solid var(--border, #2a2a4a);
      overflow-y: auto;
      flex-shrink: 0;
    }

    .detail-panel {
      flex: 1;
      overflow-y: auto;
    }

    .empty-detail {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--text-muted, #666);
      font-size: 1.1rem;
    }

    /* Phone layout: single panel */
    @media (max-width: 768px) {
      .list-panel {
        width: 100%;
        display: block;
      }
      .detail-panel {
        display: none;
      }
      :host([detail-open]) .list-panel {
        display: none;
      }
      :host([detail-open]) .detail-panel {
        display: block;
      }
    }
  `;

  @state() private items: TrackerItem[] = [];
  @state() private selectedId: string | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    onStateUpdate((items) => {
      this.items = items;
    });
  }

  private get selectedItem(): TrackerItem | undefined {
    return this.items.find((i) => i.id === this.selectedId);
  }

  private onItemSelect(e: CustomEvent<string>): void {
    this.selectedId = e.detail;
    this.setAttribute("detail-open", "");
  }

  private onBack(): void {
    this.removeAttribute("detail-open");
  }

  render() {
    return html`
      <div class="list-panel">
        <item-list
          .items=${this.items}
          .selectedId=${this.selectedId}
          @item-select=${this.onItemSelect}
        ></item-list>
      </div>
      <div class="detail-panel">
        ${this.selectedItem
          ? html`<item-detail
              .item=${this.selectedItem}
              @back=${this.onBack}
            ></item-detail>`
          : html`<div class="empty-detail">Select an item</div>`}
      </div>
    `;
  }
}
