// Price Heater Lovelace cards: status and price chart.
//
// Served by the integration (frontend.py) and loaded on every dashboard;
// plain custom elements, no build step and no external imports. Data
// shaping lives in price-heater-core.js (unit tested), imported with this
// module's own ?v= query so both update together.

const moduleUrl = (name) => new URL(`./${name}${new URL(import.meta.url).search}`, import.meta.url).href;
const core = await import(moduleUrl("price-heater-core.js"));
const {
  formatClock,
  formatNumber,
  formatPower,
  isMissing,
  langOf,
  localeOf,
  numberPoints,
  planTimeline,
  onSpans,
  priceChart,
  registrationLang,
  resolveEntities,
  statusModel,
  t: translate,
  tempGauge,
} = core;

const VERSION = new URL(import.meta.url).searchParams.get("v") || "dev";
const DOCS = "https://github.com/greberg/price-heater-ha#cards";
const HISTORY_REFRESH_MS = 5 * 60 * 1000;

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLES = `
  :host { display: block; }
  ha-card { height: 100%; overflow: hidden; }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 16px 0; }
  .title { font-size: 16px; font-weight: 500; color: var(--primary-text-color); display: flex; align-items: center; gap: 8px; min-width: 0; }
  .title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .title ha-icon { color: var(--state-icon-color); --mdc-icon-size: 20px; }
  .content { padding: 8px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
  .hero { display: flex; align-items: center; gap: 16px; }
  .hero .big { font-size: 40px; line-height: 44px; font-weight: 400; white-space: nowrap; color: var(--primary-text-color); }
  .unit { font-size: 0.5em; color: var(--secondary-text-color); margin-left: 2px; }
  button.link.heater { display: flex; align-items: center; justify-content: center; width: 52px; height: 52px; border-radius: 50%;
    background: color-mix(in srgb, var(--disabled-text-color, #9e9e9e) 16%, transparent); color: var(--disabled-text-color, #9e9e9e); flex: none; }
  button.link.heater.on { background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 18%, transparent); color: var(--state-climate-heat-color, #ff8100); }
  button.link.heater ha-icon { --mdc-icon-size: 30px; }
  button.link.power-now { margin-left: auto; align-items: flex-end; text-align: right; }
  .power-now .value { font-size: 22px; line-height: 28px; white-space: nowrap; color: var(--primary-text-color); }
  .power-now.on .value { color: var(--state-climate-heat-color, #ff8100); }
  button.link.timeline { display: block; width: 100%; margin-top: 4px; }
  .timeline svg { display: block; width: 100%; height: auto; overflow: visible; }
  .timeline text { font-size: 9px; fill: var(--secondary-text-color); }
  .tl-price { fill: color-mix(in srgb, var(--warning-color, #ffa600) 35%, transparent); }
  .tl-price.on { fill: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 55%, transparent); }
  .tl-low { fill: var(--info-color, #039be5); }
  .grid.smart { grid-template-columns: minmax(64px, auto) minmax(64px, auto) 1fr; }
  .value.plan { display: flex; flex-direction: column; font-size: 14px; }
  .reason { font-size: 14px; color: var(--primary-text-color); }
  .reason.heating { color: var(--state-climate-heat-color, #ff8100); }
  .reason.problem { color: var(--error-color, #db4437); }
  .gauge { position: relative; height: 10px; border-radius: 5px; background: var(--divider-color); margin: 4px 0 14px; }
  .gauge .band { position: absolute; top: 0; bottom: 0; background: color-mix(in srgb, var(--success-color, #43a047) 35%, transparent); }
  .gauge .marker { position: absolute; top: -4px; width: 4px; height: 18px; border-radius: 2px; transform: translateX(-50%);
    background: var(--primary-text-color); }
  .gauge .marker.cold { background: var(--info-color, #039be5); }
  .gauge .marker.warm { background: var(--state-climate-heat-color, #ff8100); }
  .gauge .tick { position: absolute; top: 12px; font-size: 11px; color: var(--secondary-text-color); transform: translateX(-50%); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }
  .stat { display: flex; flex-direction: column; min-width: 0; text-align: left; }
  .label { color: var(--secondary-text-color); font-size: 12px; line-height: 16px; }
  .value { font-size: 16px; color: var(--primary-text-color); overflow-wrap: anywhere; }
  .value.cheap { color: var(--success-color, #43a047); }
  .value.expensive { color: var(--warning-color, #ffa600); }
  button.link { all: unset; cursor: pointer; display: inline-flex; flex-direction: column; min-width: 0; border-radius: 6px; }
  button.link:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  button.link:hover .value { text-decoration: underline dotted; }
  .chip { font: inherit; font-size: 13px; line-height: 18px; padding: 4px 10px; border-radius: 16px; display: inline-flex; align-items: center; gap: 4px;
    border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; white-space: nowrap; }
  .chip:hover { border-color: var(--primary-color); }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .chip:disabled { opacity: 0.5; cursor: default; }
  .chip ha-icon { --mdc-icon-size: 16px; }
  .alerts { display: flex; flex-direction: column; gap: 6px; }
  .alert { display: flex; gap: 8px; align-items: center; padding: 6px 10px; border-radius: 8px; font-size: 13px; cursor: pointer; color: var(--primary-text-color); }
  .alert ha-icon { --mdc-icon-size: 18px; flex: none; }
  .alert.error { background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent); }
  .alert.error ha-icon { color: var(--error-color, #db4437); }
  .alert.warning { background: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent); }
  .alert.warning ha-icon { color: var(--warning-color, #ffa600); }
  .alert.info { background: color-mix(in srgb, var(--info-color, #039be5) 12%, transparent); }
  .alert.info ha-icon { color: var(--info-color, #039be5); }
  .sub { color: var(--secondary-text-color); font-size: 11px; }
  .divider { height: 1px; background: var(--divider-color); }
  .power { fill: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 45%, transparent);
    stroke: var(--state-climate-heat-color, #ff8100); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .total { font-size: 12px; color: var(--primary-text-color); }
  .empty { color: var(--secondary-text-color); font-size: 14px; padding: 8px 0; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--secondary-text-color); }
  .legend i { display: inline-block; width: 12px; height: 8px; margin-right: 4px; vertical-align: middle; border-radius: 2px; }
  .legend i.line { height: 2px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  svg text { font-size: 10px; fill: var(--secondary-text-color); font-family: inherit; }
  .bar { fill: var(--warning-color, #ffa600); opacity: 0.55; }
  .bar.cheap { fill: var(--success-color, #43a047); opacity: 0.7; }
  .bar.current { opacity: 1; }
  .grid-line { stroke: var(--divider-color); stroke-width: 1; }
  .limit { stroke: var(--success-color, #43a047); stroke-width: 1; stroke-dasharray: 4 3; }
  .threshold { stroke: var(--primary-color); stroke-width: 1; stroke-dasharray: 2 3; }
  .now { stroke: var(--primary-text-color); stroke-width: 1; opacity: 0.6; }
  .strip-bg { fill: var(--divider-color); }
  .strip-on { fill: var(--state-climate-heat-color, #ff8100); }
  .strip-plan { fill: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 45%, transparent); stroke: var(--state-climate-heat-color, #ff8100); stroke-width: 0.75; stroke-dasharray: 2 2; }
  .legend i.plan { background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 45%, transparent); outline: 1px dashed var(--state-climate-heat-color, #ff8100); }
  .legend i.dashed { background: repeating-linear-gradient(90deg, var(--secondary-text-color) 0 3px, transparent 3px 5px); }
  .floor { fill: none; stroke: var(--secondary-text-color); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .pred { stroke-dasharray: 4 3; opacity: 0.85; }
  .temp { fill: none; stroke: var(--info-color, #039be5); stroke-width: 2; vector-effect: non-scaling-stroke; }
  .temp-limit { stroke: var(--info-color, #039be5); stroke-width: 1; stroke-dasharray: 2 4; opacity: 0.6; }
`;

// --------------------------------------------------------------------------
// Base card
// --------------------------------------------------------------------------

class PriceHeaterBaseCard extends HTMLElement {
  static cardType = "";
  static defaultIcon = "mdi:radiator";

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._seen = new Map();
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  static getConfigElement() {
    return createEditor(this);
  }

  static getStubConfig() {
    return {};
  }

  static extraSchema() {
    return [];
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    if (config.entity !== undefined && !String(config.entity).startsWith("sensor.")) {
      throw new Error("entity must be the Price Heater status sensor (sensor.*)");
    }
    this._config = { ...config };
    this._seen.clear();
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || hass.locale !== this._localeRef || this._changed()) {
      this._localeRef = hass.locale;
      this._render();
    }
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _changed() {
    let changed = false;
    const ids = this._ids ? Object.values(this._ids) : [];
    if (!ids.length) return true;
    for (const id of ids) {
      const s = this._hass.states[id];
      if (this._seen.get(id) !== s) {
        this._seen.set(id, s);
        changed = true;
      }
    }
    return changed;
  }

  t(key, vars) {
    return translate(this.lang, key, vars);
  }

  n(value, digits = 1) {
    return formatNumber(value, digits, this.loc);
  }

  stat(key, label, text, cls = "") {
    const id = this._ids?.[key];
    const inner = `<span class="label">${esc(label)}</span><span class="value ${cls}">${text}</span>`;
    if (!id) return `<div class="stat">${inner}</div>`;
    return `<button class="link stat" data-more="${esc(key)}" title="${esc(id)}">${inner}</button>`;
  }

  alertList(alerts) {
    const icons = { error: "mdi:alert-circle", warning: "mdi:alert", info: "mdi:timer-sand" };
    return alerts
      .map(
        (a) =>
          `<div class="alert ${a.level}" data-more="${esc(a.key)}" role="button" tabindex="0"><ha-icon icon="${icons[a.level]}"></ha-icon><span>${esc(
            this.t(a.text, a.time ? { time: formatClock(a.time, this.loc) } : {}),
          )}</span></div>`,
      )
      .join("");
  }

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset && (el.dataset.more || el.dataset.toggle));
    if (!target) return;
    if (target.dataset.toggle) {
      const id = this._ids?.[target.dataset.toggle];
      const s = id && this._hass.states[id];
      if (!s || isMissing(s)) return;
      this._hass.callService("switch", s.state === "on" ? "turn_off" : "turn_on", { entity_id: id });
      return;
    }
    const entityId = this._ids?.[target.dataset.more];
    if (entityId) this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _render() {
    if (!this._config || !this._hass) return;
    this.lang = langOf(this._hass);
    this.loc = localeOf(this._hass);
    const found = resolveEntities(this._hass, this._config);
    this._ids = found.ids;
    for (const id of Object.values(this._ids)) this._seen.set(id, this._hass.states[id]);
    let body;
    let model = null;
    if (found.problem) {
      body = `<div class="content"><div class="empty">${esc(this.t(`problem.${found.problem}`, { entity: found.status }))}</div></div>`;
    } else {
      try {
        model = statusModel(this._hass, this._ids);
        body = this.renderBody(model, Date.now());
      } catch (err) {
        body = `<div class="content"><div class="empty">${esc(this.t("problem.card_error", { error: err?.message || err }))}</div></div>`;
        console.error("Price Heater card", err);
      }
    }
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card>${this.renderHeader(model)}${body}</ha-card>`;
  }

  renderHeader(model, extra = "") {
    const title = this._config.title ?? model?.title ?? "Price Heater";
    if (!title && !extra) return "";
    return `<div class="header"><div class="title"><ha-icon icon="${esc(this._config.icon || this.constructor.defaultIcon)}"></ha-icon><span>${esc(
      title,
    )}</span></div>${extra}</div>`;
  }

  renderBody() {
    return "";
  }
}

// --------------------------------------------------------------------------
// Status card
// --------------------------------------------------------------------------

class PriceHeaterCard extends PriceHeaterBaseCard {
  static cardType = "price-heater-card";

  static extraSchema() {
    return [{ name: "show_plan", selector: { boolean: {} } }];
  }

  getCardSize() {
    return 5;
  }

  renderHeader(model) {
    if (!model || !this._ids.automation) return super.renderHeader(model);
    const auto = this._hass.states[this._ids.automation];
    const chip = `<button class="chip ${model.enabled ? "on" : ""}" data-toggle="automation" ${isMissing(auto) ? "disabled" : ""}><ha-icon icon="${
      model.enabled ? "mdi:robot" : "mdi:hand-back-right"
    }"></ha-icon>${esc(this.t(model.enabled ? "label.automation" : "label.manual"))}</button>`;
    return super.renderHeader(model, chip);
  }

  renderPower(m) {
    if (!m.hasPower) return "";
    const rated = m.powerSource === "rated";
    return `<button class="link stat power-now ${m.power > 0 ? "on" : ""}" data-more="power">
        <span class="label">${esc(this.t("label.power"))}</span>
        <span class="value">${esc(formatPower(m.power, this.loc))}${rated ? ` <span class="sub" title="${esc(this.t("label.rated"))}">≈</span>` : ""}</span>
      </button>`;
  }

  renderSmart(m, now = Date.now()) {
    if (!m.smart) return "";
    const deg = (v) => (v === null ? "–" : `${this.n(v, 1)}<span class="unit">°</span>`);
    let plan = "–";
    if (m.planOn !== null) {
      const clock = (t) => formatClock(t, this.loc);
      const next = m.planOn
        ? this.t("label.heating_until", { time: m.planNext ? clock(m.planNext) : clock(m.planUntil) })
        : m.planNext
          ? this.t("label.next_heating", { time: clock(m.planNext) })
          : this.t("label.no_heating");
      const span = m.planUntil ? Math.round((m.planUntil - now) / 3600000) : null;
      const total =
        m.planHours === null || span === null
          ? ""
          : m.planCost !== null
            ? this.t("label.plan_total", { span, hours: this.n(m.planHours, 1), cost: `${this.n(m.planCost, 2)} ${m.currency}`.trim() })
            : this.t("label.plan_hours", { span, hours: this.n(m.planHours, 1) });
      plan = `${esc(next)}${total ? `<span class="sub">${esc(total)}</span>` : ""}`;
    }
    return `<div class="divider"></div>
      <div class="grid smart">
        ${m.floor !== null ? this.stat("floor", this.t("label.floor"), deg(m.floor)) : ""}
        ${m.outdoor !== null ? this.stat(this._ids.outdoor ? "outdoor" : "weather", this.t("label.outdoor"), deg(m.outdoor)) : ""}
        ${this.stat("status", this.t("label.plan"), plan, "plan")}
      </div>
      ${this._config.show_plan !== false ? this.renderTimeline(m, now) : ""}`;
  }

  renderTimeline(m, now) {
    const tl = planTimeline({ plan: m.planSteps, now, min: m.min, max: m.max });
    if (!tl) return "";
    const a = tl.area;
    const clock = (t) => formatClock(t, this.loc);
    const parts = [];
    for (const b of tl.bars) parts.push(`<rect class="tl-price ${b.on ? "on" : ""}" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}"/>`);
    if (tl.minY !== null) parts.push(`<line class="temp-limit" x1="${a.x0}" x2="${a.x1}" y1="${tl.minY}" y2="${tl.minY}"/><text x="${a.x1 + 4}" y="${tl.minY + 3}">${esc(this.n(m.min, 1))}°</text>`);
    if (tl.maxY !== null) parts.push(`<line class="temp-limit" x1="${a.x0}" x2="${a.x1}" y1="${tl.maxY}" y2="${tl.maxY}"/><text x="${a.x1 + 4}" y="${tl.maxY + 3}">${esc(this.n(m.max, 1))}°</text>`);
    if (tl.floorPath) parts.push(`<path class="floor pred" d="${tl.floorPath}"><title>${esc(this.t("label.floor"))} · ${esc(this.t("label.forecast"))}</title></path>`);
    if (tl.tempPath) parts.push(`<path class="temp pred" d="${tl.tempPath}"><title>${esc(this.t("label.temperature"))} · ${esc(this.t("label.forecast"))}</title></path>`);
    if (tl.lowest) parts.push(`<circle class="tl-low" cx="${tl.lowest.x}" cy="${tl.lowest.y}" r="2.5"><title>${esc(this.n(tl.lowest.value, 1))}°</title></circle>`);
    parts.push(`<rect class="strip-bg" x="${a.x0}" y="${tl.stripY}" width="${a.x1 - a.x0}" height="6" rx="2"/>`);
    for (const h of tl.heat) {
      parts.push(`<rect class="strip-plan" x="${h.x}" y="${tl.stripY}" width="${h.w}" height="6" rx="2"><title>${esc(this.t("label.planned"))} ${esc(clock(h.start))}–${esc(clock(h.end))}</title></rect>`);
    }
    parts.push(`<text x="${a.x0}" y="${tl.height - 2}">${esc(this.t("label.now"))}</text>`);
    for (const tick of tl.ticks) {
      if (tick.x - a.x0 < 24) continue;
      parts.push(`<line class="grid-line" x1="${tick.x}" x2="${tick.x}" y1="${a.y0}" y2="${tl.stripY + 6}"/><text x="${tick.x}" y="${tl.height - 2}" text-anchor="middle">${esc(clock(tick.time))}</text>`);
    }
    return `<button class="link timeline" data-more="status">
      <svg viewBox="0 0 ${tl.width} ${tl.height}" role="img" aria-label="${esc(this.t("label.plan_aria", { hours: tl.hours }))}">${parts.join("")}</svg>
    </button>`;
  }

  renderConsumption(m) {
    if (!m.hasPower) return `<div class="divider"></div><span class="sub">${esc(this.t("label.no_power"))}</span>`;
    const energy = m.energy === null ? "–" : `${this.n(m.energy, 2)} <span class="unit">kWh</span>`;
    const cost = m.cost === null ? "–" : `${this.n(m.cost, 2)}${m.currency ? ` <span class="unit">${esc(m.currency)}</span>` : ""}`;
    return `<div class="divider"></div>
      <div class="grid">
        ${this.stat("energy_today", this.t("label.energy_today"), energy)}
        ${this.stat("cost_today", this.t("label.cost_today"), cost)}
      </div>
      ${m.powerSource === "rated" ? `<span class="sub">≈ ${esc(this.t("label.rated"))}</span>` : ""}`;
  }

  renderBody(m) {
    const temp = m.temperature === null ? "–" : `${this.n(m.temperature, 1)}<span class="unit">${esc(m.tempUnit)}</span>`;
    const reasonCls = m.heating ? "heating" : m.problem ? "problem" : "";
    const reason = this.t(m.reason ? `reason.${m.reason}` : "reason.none");
    const gauge = tempGauge(m.temperature, m.min, m.max);
    const gaugeHtml = gauge
      ? `<div class="gauge" data-more="temperature" role="button">
          <div class="band" style="left:${gauge.minPct}%;width:${gauge.maxPct - gauge.minPct}%"></div>
          ${gauge.valuePct !== null ? `<div class="marker ${gauge.zone}" style="left:${gauge.valuePct}%"></div>` : ""}
          <span class="tick" style="left:${gauge.minPct}%">${this.n(m.min, 1)}°</span>
          <span class="tick" style="left:${gauge.maxPct}%">${this.n(m.max, 1)}°</span>
        </div>`
      : "";
    const unit = m.priceUnit ? ` <span class="unit">${esc(m.priceUnit)}</span>` : "";
    const priceCls = m.cheap === true ? "cheap" : m.cheap === false ? "expensive" : "";
    const stats = [
      this.stat("price", this.t("label.price"), m.price === null ? "–" : `${this.n(m.price, m.priceDigits)}${unit}`, priceCls),
      m.rank !== null && m.count ? this.stat("status", this.t("label.rank"), esc(this.t("label.rank_value", { rank: m.rank, count: m.count }))) : "",
      m.cheapLimit !== null ? this.stat("status", this.t("label.cheap_limit"), `${this.n(m.cheapLimit, m.priceDigits)}${unit}`) : "",
      m.threshold !== null ? this.stat("status", this.t("label.threshold"), `${this.n(m.threshold, m.priceDigits)}${unit}`) : "",
    ].join("");
    return `<div class="content">
      <div class="hero">
        <button class="link heater ${m.heaterOn ? "on" : ""}" data-more="heater" title="${esc(this.t("label.heater"))}: ${esc(
          this.t(m.heaterOn ? "label.on" : "label.off"),
        )}"><ha-icon icon="${m.heaterOn ? "mdi:radiator" : "mdi:radiator-off"}"></ha-icon></button>
        <div class="stat">
          <button class="link" data-more="temperature"><span class="big">${temp}</span></button>
          <button class="link" data-more="status"><span class="reason ${reasonCls}">${esc(reason)}</span></button>
        </div>
        ${this.renderPower(m)}
      </div>
      ${gaugeHtml}
      <div class="grid">${stats}</div>
      ${this.renderSmart(m)}
      ${this.renderConsumption(m)}
      ${m.alerts.length ? `<div class="alerts">${this.alertList(m.alerts)}</div>` : ""}
    </div>`;
  }
}

// --------------------------------------------------------------------------
// Price chart card
// --------------------------------------------------------------------------

class PriceHeaterChartCard extends PriceHeaterBaseCard {
  static cardType = "price-heater-chart-card";
  static defaultIcon = "mdi:chart-bar";

  static extraSchema() {
    return [
      { name: "show_history", selector: { boolean: {} } },
      { name: "show_temperature", selector: { boolean: {} } },
      { name: "show_power", selector: { boolean: {} } },
    ];
  }

  getCardSize() {
    return 5;
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      this._history = null;
      this._render();
    }, HISTORY_REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = undefined;
  }

  _changed() {
    // A heater switch reloads the history so the on-strip stays live.
    const heaterId = this._ids?.heater;
    const before = heaterId ? this._seen.get(heaterId)?.state : undefined;
    const changed = super._changed();
    if (heaterId && this._hass.states[heaterId]?.state !== before) this._historyStale = true;
    return changed;
  }

  async _loadHistory(start) {
    if (this._loading || !this._hass?.callWS) return;
    const ids = [this._ids.heater, this._ids.temperature, this._ids.power, this._ids.floor].filter(Boolean);
    if (!ids.length) return;
    this._loading = true;
    try {
      const result = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: new Date(start).toISOString(),
        end_time: new Date().toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      this._history = { start, data: result || {} };
    } catch (err) {
      console.warn("Price Heater chart: history unavailable", err);
      this._history = { start, data: {} };
    } finally {
      this._loading = false;
      this._historyStale = false;
      this._render();
    }
  }

  renderBody(m, now) {
    const status = this._hass.states[this._ids.status];
    const schedule = status?.attributes?.prices_today || [];
    const dayStart = schedule.length ? Date.parse(schedule[0].start) : null;
    const showHistory = this._config.show_history !== false;
    const showTemp = this._config.show_temperature !== false;
    const showPower = this._config.show_power !== false && m.hasPower && !!this._ids.power;

    const plan = Array.isArray(status?.attributes?.plan) ? status.attributes.plan : [];
    let spans = [];
    let temps = [];
    let floors = [];
    let power = [];
    if (dayStart !== null && (showHistory || showTemp || showPower)) {
      if (!this._history || this._history.start !== dayStart || this._historyStale) {
        queueMicrotask(() => this._loadHistory(dayStart));
      }
      const data = this._history?.data || {};
      if (showHistory) spans = onSpans(data[this._ids.heater], this._ids.heater, dayStart, now);
      if (showTemp) temps = numberPoints(data[this._ids.temperature]);
      // The live state extends the history to now.
      if (showTemp && m.temperature !== null) temps.push({ t: now, v: m.temperature });
      if (showTemp && this._ids.floor) {
        floors = numberPoints(data[this._ids.floor]);
        if (m.floor !== null) floors.push({ t: now, v: m.floor });
      }
      if (showPower) {
        power = numberPoints(data[this._ids.power]);
        if (m.power !== null) power.push({ t: now, v: m.power });
      }
    }

    const chart = priceChart({
      schedule,
      now,
      cheapLimit: m.cheapLimit,
      threshold: m.threshold,
      spans,
      temps,
      floors,
      power,
      plan: showTemp || showHistory ? plan : [],
      min: m.min,
      max: m.max,
    });
    if (chart.empty) return `<div class="content"><div class="empty">${esc(this.t("chart.empty"))}</div></div>`;

    const p = chart.plot;
    const unit = m.priceUnit ? ` ${m.priceUnit}` : "";
    const parts = [];
    for (const tick of chart.yTicks) {
      parts.push(`<line class="grid-line" x1="${p.x0}" x2="${p.x1}" y1="${tick.y}" y2="${tick.y}"/>`);
      parts.push(`<text x="${p.x0 - 4}" y="${tick.y + 3}" text-anchor="end">${esc(this.n(tick.value, chart.digits > 0 ? Math.min(chart.digits, 2) : 0))}</text>`);
    }
    for (const b of chart.bars) {
      const label = `${formatClock(b.start, this.loc)}–${formatClock(b.end, this.loc)}: ${this.n(b.price, chart.digits)}${unit} · ${this.t(
        b.cheap ? "label.cheap" : "label.expensive",
      )}`;
      parts.push(
        `<rect class="bar ${b.cheap ? "cheap" : ""} ${b.current ? "current" : ""}" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="1"><title>${esc(
          label,
        )}</title></rect>`,
      );
    }
    if (chart.limitY !== null) parts.push(`<line class="limit" x1="${p.x0}" x2="${p.x1}" y1="${chart.limitY}" y2="${chart.limitY}"/>`);
    if (chart.thresholdY !== null) parts.push(`<line class="threshold" x1="${p.x0}" x2="${p.x1}" y1="${chart.thresholdY}" y2="${chart.thresholdY}"/>`);
    if (chart.tempTicks.length && showTemp) {
      parts.push(`<line class="temp-limit" x1="${p.x0}" x2="${p.x1}" y1="${chart.minY}" y2="${chart.minY}"/>`);
      parts.push(`<line class="temp-limit" x1="${p.x0}" x2="${p.x1}" y1="${chart.maxY}" y2="${chart.maxY}"/>`);
      if (chart.floorPath) parts.push(`<path class="floor" d="${chart.floorPath}"><title>${esc(this.t("label.floor"))}</title></path>`);
      if (chart.floorPredPath) parts.push(`<path class="floor pred" d="${chart.floorPredPath}"><title>${esc(this.t("label.floor"))} · ${esc(this.t("label.forecast"))}</title></path>`);
      if (chart.tempPath) parts.push(`<path class="temp" d="${chart.tempPath}"/>`);
      if (chart.predPath) parts.push(`<path class="temp pred" d="${chart.predPath}"><title>${esc(this.t("label.temperature"))} · ${esc(this.t("label.forecast"))}</title></path>`);
      for (const tick of chart.tempTicks) parts.push(`<text x="${p.x1 + 4}" y="${tick.y + 3}">${esc(this.n(tick.value, 1))}°</text>`);
    }
    if (chart.powerPath) {
      parts.push(`<path class="power" d="${chart.powerPath}"><title>${esc(this.t("label.power_used"))}</title></path>`);
      parts.push(`<text x="${p.x1 + 4}" y="${chart.powerTop + 7}">${esc(formatPower(chart.powerMax, this.loc))}</text>`);
    }
    if (showHistory) {
      parts.push(`<rect class="strip-bg" x="${p.x0}" y="${chart.stripY}" width="${p.x1 - p.x0}" height="6" rx="2"/>`);
      for (const s of chart.spans) parts.push(`<rect class="strip-on" x="${s.x}" y="${chart.stripY}" width="${s.w}" height="6" rx="2"/>`);
      for (const s of chart.planSpans) parts.push(`<rect class="strip-plan" x="${s.x}" y="${chart.stripY}" width="${s.w}" height="6" rx="2"><title>${esc(this.t("label.planned"))}</title></rect>`);
    }
    if (chart.nowX !== null) {
      parts.push(`<line class="now" x1="${chart.nowX}" x2="${chart.nowX}" y1="${p.y0}" y2="${showHistory ? chart.stripY + 6 : p.y1}"/>`);
    }
    for (const tick of chart.xTicks) {
      parts.push(`<text x="${tick.x}" y="${chart.height - 1}" text-anchor="middle">${esc(formatClock(tick.time, this.loc))}</text>`);
    }

    const legend = [
      `<span><i style="background:var(--success-color,#43a047)"></i>${esc(this.t("label.cheap"))}</span>`,
      `<span><i style="background:var(--warning-color,#ffa600);opacity:.6"></i>${esc(this.t("label.expensive"))}</span>`,
      showHistory ? `<span><i style="background:var(--state-climate-heat-color,#ff8100)"></i>${esc(this.t("label.heater_ran"))}</span>` : "",
      showHistory && chart.planSpans.length ? `<span><i class="plan"></i>${esc(this.t("label.planned"))}</span>` : "",
      showTemp && (chart.tempPath || chart.predPath) ? `<span><i class="line" style="background:var(--info-color,#039be5)"></i>${esc(this.t("label.temperature"))}</span>` : "",
      showTemp && (chart.floorPath || chart.floorPredPath) ? `<span><i class="line" style="background:var(--secondary-text-color)"></i>${esc(this.t("label.floor"))}</span>` : "",
      showTemp && chart.predPath ? `<span><i class="line dashed"></i>${esc(this.t("label.forecast"))}</span>` : "",
      chart.powerPath ? `<span><i style="background:var(--state-climate-heat-color,#ff8100);opacity:.5"></i>${esc(this.t("label.power_used"))}</span>` : "",
      unit ? `<span>${esc(unit.trim())}</span>` : "",
    ].join("");

    return `<div class="content">
      <svg viewBox="0 0 ${chart.width} ${chart.height}" role="img" aria-label="${esc(this.t("chart.aria"))}">${parts.join("")}</svg>
      <div class="legend">${legend}</div>
      ${m.hasPower && m.energy !== null ? `<div class="total">${esc(
        this.t("label.today_total", {
          energy: `${this.n(m.energy, 2)} kWh`,
          cost: m.cost === null ? "–" : `${this.n(m.cost, 2)} ${m.currency}`.trim(),
        }),
      )}</div>` : ""}
    </div>`;
  }
}

// --------------------------------------------------------------------------
// Editor (ha-form)
// --------------------------------------------------------------------------

async function ensureHaForm() {
  if (customElements.get("ha-form")) return;
  try {
    const helpers = await window.loadCardHelpers?.();
    const card = await helpers?.createCardElement({ type: "entities", entities: [] });
    await card?.constructor?.getConfigElement?.();
  } catch (_err) {
    // the editor falls back to YAML when ha-form never appears
  }
}

async function createEditor(cardClass) {
  await ensureHaForm();
  const editor = document.createElement("price-heater-card-editor");
  editor.schema = [
    { name: "entity", selector: { entity: { integration: "price_heater", domain: "sensor" } } },
    { name: "title", selector: { text: {} } },
    ...cardClass.extraSchema(),
  ];
  return editor;
}

class PriceHeaterCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _update() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => translate(langOf(this._hass), `editor.${s.name}`);
      this._form.computeHelper = (s) => {
        const key = `editor.${s.name}_help`;
        const text = translate(langOf(this._hass), key);
        return text === key ? "" : text;
      };
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const config = { ...ev.detail.value };
        for (const [k, v] of Object.entries(config)) if (v === "" || v === undefined || v === null) delete config[k];
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this.schema || [];
    this._form.data = { show_history: true, show_temperature: true, show_power: true, ...this._config };
  }
}

// --------------------------------------------------------------------------
// Registration
// --------------------------------------------------------------------------

const CARDS = [PriceHeaterCard, PriceHeaterChartCard];
const pickerLang = registrationLang();
if (!customElements.get("price-heater-card-editor")) customElements.define("price-heater-card-editor", PriceHeaterCardEditor);
window.customCards = window.customCards || [];
for (const cls of CARDS) {
  if (!customElements.get(cls.cardType)) customElements.define(cls.cardType, cls);
  if (!window.customCards.some((c) => c.type === cls.cardType)) {
    window.customCards.push({
      type: cls.cardType,
      name: translate(pickerLang, `card.${cls.cardType}.name`),
      description: translate(pickerLang, `card.${cls.cardType}.description`),
      preview: true,
      documentationURL: DOCS,
    });
  }
}

console.info(
  `%c Price Heater cards %c ${VERSION} `,
  "background:#ff8100;color:#fff;border-radius:3px 0 0 3px",
  "background:#555;color:#fff;border-radius:0 3px 3px 0",
);
