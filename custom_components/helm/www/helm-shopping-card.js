/**
 * Helm shopping list card.
 *
 * Home Assistant's to-do model has no quantity field, so the built-in to-do
 * card cannot show one. This card reads the full Helm records off the to-do
 * entity's `items` attribute and writes through the helm.* services, which
 * means quantity, category and recipe links all work properly.
 */

const CARD_VERSION = "0.1.0";

// Quantity changes are held briefly so holding down + fires one request, not ten.
const QTY_DEBOUNCE_MS = 600;

const DEFAULTS = {
  title: "Shopping list",
  group_by_category: false,
  show_completed: true,
};

const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );

const STYLES = `
  :host { display: block; }
  ha-card { padding: 12px 0 4px; }
  .header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 4px 16px 12px; gap: 8px;
  }
  .title {
    font-size: var(--ha-card-header-font-size, 24px);
    font-weight: 400; color: var(--ha-card-header-color, var(--primary-text-color));
    line-height: 1.2;
  }
  .count {
    color: var(--secondary-text-color); font-size: 14px; white-space: nowrap;
  }
  .add-row {
    display: flex; align-items: center; gap: 8px; padding: 0 16px 12px;
  }
  .add-row input[type="text"] {
    flex: 1 1 auto; min-width: 0;
    background: var(--card-background-color); color: var(--primary-text-color);
    border: none; border-bottom: 1px solid var(--divider-color);
    padding: 8px 2px; font-size: 15px; font-family: inherit;
  }
  .add-row input[type="text"]:focus {
    outline: none; border-bottom: 2px solid var(--primary-color); padding-bottom: 7px;
  }
  .add-row input[type="number"] {
    width: 52px; flex: 0 0 auto;
    background: var(--card-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 4px;
    padding: 7px 4px; font-size: 15px; font-family: inherit; text-align: center;
  }
  button {
    background: none; border: none; cursor: pointer; padding: 0;
    color: var(--secondary-text-color); font-family: inherit;
    display: inline-flex; align-items: center; justify-content: center;
  }
  button:hover:not(:disabled) { color: var(--primary-text-color); }
  button:disabled { opacity: 0.4; cursor: default; }
  .add-btn {
    flex: 0 0 auto; color: var(--primary-color);
    width: 36px; height: 36px; border-radius: 50%;
  }
  .add-btn:hover:not(:disabled) { background: rgba(var(--rgb-primary-color, 3,169,244), 0.12); }
  .group {
    padding: 10px 16px 4px; font-size: 12px; font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--secondary-text-color);
  }
  .item {
    display: flex; align-items: center; gap: 10px; padding: 6px 16px;
    min-height: 40px;
  }
  .item:hover { background: rgba(var(--rgb-primary-text-color, 0,0,0), 0.04); }
  .item input[type="checkbox"] {
    flex: 0 0 auto; width: 18px; height: 18px; margin: 0; cursor: pointer;
    accent-color: var(--primary-color);
  }
  .name { flex: 1 1 auto; min-width: 0; color: var(--primary-text-color); font-size: 15px; }
  .name .sub { display: block; font-size: 12px; color: var(--secondary-text-color); }
  .done .name { text-decoration: line-through; color: var(--secondary-text-color); }
  .qty { display: flex; align-items: center; gap: 2px; flex: 0 0 auto; }
  .qty button { width: 28px; height: 28px; border-radius: 50%; font-size: 18px; line-height: 1; }
  .qty .value {
    min-width: 22px; text-align: center; font-size: 14px;
    font-variant-numeric: tabular-nums; color: var(--primary-text-color);
  }
  .qty.pending .value { color: var(--primary-color); }
  a.recipe { flex: 0 0 auto; color: var(--secondary-text-color); display: inline-flex; }
  a.recipe:hover { color: var(--primary-color); }
  .delete { flex: 0 0 auto; width: 28px; height: 28px; border-radius: 50%; }
  .delete:hover { color: var(--error-color); }
  .toggle-done {
    padding: 10px 16px 6px; font-size: 13px; color: var(--secondary-text-color);
    width: 100%; justify-content: flex-start; gap: 6px;
  }
  .empty { padding: 8px 16px 16px; color: var(--secondary-text-color); font-size: 14px; }
  .error {
    margin: 0 16px 12px; padding: 8px 12px; border-radius: 6px; font-size: 13px;
    background: rgba(var(--rgb-error-color, 219,68,55), 0.12); color: var(--error-color);
  }
  ha-icon { --mdc-icon-size: 20px; }
`;

class HelmShoppingCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("todo.") && "items" in (hass.states[id].attributes ?? {}),
    );
    return { entity: entity ?? "todo.helm_shopping_list", ...DEFAULTS };
  }

  static getConfigElement() {
    return document.createElement("helm-shopping-card-editor");
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._signature = null;
    this._error = null;
    this._showCompleted = false;
    // item id -> quantity the user is currently clicking towards
    this._pendingQty = new Map();
    this._qtyTimers = new Map();
    this._built = false;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Set 'entity' to your Helm shopping list, e.g. todo.helm_shopping_list");
    }
    if (!config.entity.startsWith("todo.")) {
      throw new Error("'entity' must be a to-do entity");
    }
    this._config = { ...DEFAULTS, ...config };
    this._signature = null;
    if (this._built) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._render();
  }

  getCardSize() {
    return 1 + Math.ceil(this._items().length / 2);
  }

  _stateObj() {
    return this._hass?.states?.[this._config.entity] ?? null;
  }

  _items() {
    const items = this._stateObj()?.attributes?.items;
    return Array.isArray(items) ? items : [];
  }

  /** Quantity to display: the pending click target if there is one. */
  _displayQty(item) {
    return this._pendingQty.has(item.id) ? this._pendingQty.get(item.id) : (item.qty ?? 1);
  }

  _build() {
    this._built = true;
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="header">
          <div class="title"></div>
          <div class="count"></div>
        </div>
        <div class="error" hidden></div>
        <div class="add-row">
          <input type="text" class="add-name" placeholder="Add an item" autocomplete="off" />
          <input type="number" class="add-qty" min="1" max="999" value="1" aria-label="Quantity" />
          <button class="add-btn" title="Add"><ha-icon icon="mdi:plus"></ha-icon></button>
        </div>
        <div class="list"></div>
      </ha-card>
    `;

    // The add row is built once and never re-rendered, so typing is never
    // interrupted by an unrelated state update.
    const name = root.querySelector(".add-name");
    const qty = root.querySelector(".add-qty");
    root.querySelector(".add-btn").addEventListener("click", () => this._add());
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") this._add();
    });
    qty.addEventListener("keydown", (event) => {
      if (event.key === "Enter") this._add();
    });

    root.querySelector(".list").addEventListener("click", (event) => this._onListClick(event));
    root.querySelector(".list").addEventListener("change", (event) => this._onListChange(event));
  }

  _render() {
    if (!this._config || !this._hass) return;
    const root = this.shadowRoot;
    const state = this._stateObj();

    root.querySelector(".title").textContent = this._config.title ?? DEFAULTS.title;

    if (!state) {
      root.querySelector(".count").textContent = "";
      root.querySelector(".list").innerHTML =
        `<div class="empty">Entity <code>${escapeHtml(this._config.entity)}</code> not found.</div>`;
      return;
    }
    if (state.state === "unavailable") {
      root.querySelector(".list").innerHTML =
        `<div class="empty">The Helm shopping list is unavailable.</div>`;
      return;
    }

    const items = this._items();
    const outstanding = items.filter((item) => !item.completed);
    const done = items.filter((item) => item.completed);

    root.querySelector(".count").textContent = outstanding.length
      ? `${outstanding.length} to buy`
      : "all done";

    // Re-render the list only when something that affects it actually changed.
    const signature = JSON.stringify([
      items.map((item) => [
        item.id,
        item.name,
        item.qty,
        item.completed,
        item.url,
        item.type?.name,
        item.meal?.name,
      ]),
      [...this._pendingQty.entries()],
      this._showCompleted,
      this._config.group_by_category,
      this._config.show_completed,
    ]);
    if (signature === this._signature) return;
    this._signature = signature;

    let html = "";
    if (!outstanding.length && !done.length) {
      html += `<div class="empty">Nothing on the list.</div>`;
    } else if (this._config.group_by_category) {
      for (const [category, group] of this._groupByCategory(outstanding)) {
        html += `<div class="group">${escapeHtml(category)}</div>`;
        html += group.map((item) => this._itemHtml(item)).join("");
      }
    } else {
      html += outstanding.map((item) => this._itemHtml(item)).join("");
    }

    if (this._config.show_completed && done.length) {
      html += `
        <button class="toggle-done">
          <ha-icon icon="mdi:chevron-${this._showCompleted ? "up" : "down"}"></ha-icon>
          ${done.length} completed
        </button>`;
      if (this._showCompleted) {
        html += done.map((item) => this._itemHtml(item)).join("");
      }
    }

    root.querySelector(".list").innerHTML = html;
  }

  _groupByCategory(items) {
    const groups = new Map();
    for (const item of items) {
      const key = item.type?.name ?? "Uncategorised";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    // Named categories alphabetically, uncategorised last.
    return [...groups.entries()].sort(([a], [b]) => {
      if (a === "Uncategorised") return 1;
      if (b === "Uncategorised") return -1;
      return a.localeCompare(b);
    });
  }

  _itemHtml(item) {
    const qty = this._displayQty(item);
    const pending = this._pendingQty.has(item.id);
    const sub = [];
    if (item.meal?.name) sub.push(`for ${item.meal.name}`);
    if (!this._config.group_by_category && item.type?.name) sub.push(item.type.name);

    return `
      <div class="item ${item.completed ? "done" : ""}" data-id="${item.id}">
        <input type="checkbox" ${item.completed ? "checked" : ""}
               aria-label="${escapeHtml(item.name)}" />
        <label class="name">
          ${escapeHtml(item.name)}
          ${sub.length ? `<span class="sub">${escapeHtml(sub.join(" · "))}</span>` : ""}
        </label>
        <div class="qty ${pending ? "pending" : ""}">
          <button data-act="dec" title="Fewer" ${qty <= 1 ? "disabled" : ""}>−</button>
          <span class="value">${qty}</span>
          <button data-act="inc" title="More" ${qty >= 999 ? "disabled" : ""}>+</button>
        </div>
        ${
          item.url
            ? `<a class="recipe" href="${escapeHtml(item.url)}" target="_blank"
                  rel="noopener noreferrer" title="Open link">
                 <ha-icon icon="mdi:open-in-new"></ha-icon></a>`
            : ""
        }
        <button class="delete" data-act="delete" title="Remove">
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>`;
  }

  _itemFor(event) {
    const row = event.target.closest(".item");
    if (!row) return null;
    const id = Number(row.dataset.id);
    return this._items().find((item) => item.id === id) ?? null;
  }

  _onListClick(event) {
    if (event.target.closest(".toggle-done")) {
      this._showCompleted = !this._showCompleted;
      this._render();
      return;
    }
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const item = this._itemFor(event);
    if (!item) return;

    const action = button.dataset.act;
    if (action === "delete") {
      this._call("delete_shopping_item", { item_id: item.id });
    } else if (action === "inc" || action === "dec") {
      this._nudgeQty(item, action === "inc" ? 1 : -1);
    }
  }

  _onListChange(event) {
    if (event.target.type !== "checkbox") return;
    const item = this._itemFor(event);
    if (!item) return;
    // Sending only `completed` uses Helm's tick-only permission path.
    this._call("update_shopping_item", {
      item_id: item.id,
      completed: event.target.checked,
    });
  }

  _nudgeQty(item, delta) {
    const next = Math.min(999, Math.max(1, this._displayQty(item) + delta));
    this._pendingQty.set(item.id, next);
    this._render();

    clearTimeout(this._qtyTimers.get(item.id));
    this._qtyTimers.set(
      item.id,
      setTimeout(async () => {
        this._qtyTimers.delete(item.id);
        const target = this._pendingQty.get(item.id);
        await this._call("update_shopping_item", { item_id: item.id, qty: target });
        this._pendingQty.delete(item.id);
        this._signature = null;
        this._render();
      }, QTY_DEBOUNCE_MS),
    );
  }

  async _add() {
    const nameInput = this.shadowRoot.querySelector(".add-name");
    const qtyInput = this.shadowRoot.querySelector(".add-qty");
    const name = nameInput.value.trim();
    if (!name) {
      nameInput.focus();
      return;
    }
    const qty = Math.min(999, Math.max(1, Number(qtyInput.value) || 1));

    const data = { name };
    if (qty > 1) data.qty = qty;

    nameInput.value = "";
    qtyInput.value = "1";
    nameInput.focus();

    await this._call("add_shopping_item", data);
  }

  async _call(service, data) {
    try {
      await this._hass.callService("helm", service, {
        entity_id: this._config.entity,
        ...data,
      });
      this._setError(null);
    } catch (err) {
      this._setError(err?.message ?? `Could not ${service.replace(/_/g, " ")}`);
    }
  }

  _setError(message) {
    const box = this.shadowRoot?.querySelector(".error");
    if (!box) return;
    this._error = message;
    box.textContent = message ?? "";
    box.hidden = !message;
  }
}

class HelmShoppingCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...DEFAULTS, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) =>
        ({
          entity: "Shopping list entity",
          title: "Title",
          group_by_category: "Group by category",
          show_completed: "Show completed items",
        })[schema.name] ?? schema.name;
      this._form.addEventListener("value-changed", (event) => {
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: event.detail.value },
            bubbles: true,
            composed: true,
          }),
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { domain: "todo" } } },
      { name: "title", selector: { text: {} } },
      { name: "group_by_category", selector: { boolean: {} } },
      { name: "show_completed", selector: { boolean: {} } },
    ];
  }
}

customElements.define("helm-shopping-card", HelmShoppingCard);
customElements.define("helm-shopping-card-editor", HelmShoppingCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "helm-shopping-card",
  name: "Helm Shopping List",
  description: "The Helm shared shopping list, with quantities, categories and recipe links.",
  preview: false,
  documentationURL: "https://github.com/lukeketchen/helm-homeassistant",
});

console.info(`%c HELM-SHOPPING-CARD %c ${CARD_VERSION} `,
  "color:white;background:#03a9f4;font-weight:700",
  "color:#03a9f4;background:white;font-weight:700");
