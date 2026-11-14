// Smoke tests of the card elements with a minimal DOM shim: each card
// renders from a fake hass without throwing and shows its main figures.
import test from "node:test";
import assert from "node:assert/strict";
import { makeHass } from "./fixtures.mjs";

class FakeElement {
  constructor() {
    this.children = [];
    this.events = [];
  }
  attachShadow() {
    this.shadowRoot = { innerHTML: "", addEventListener() {} };
    return this.shadowRoot;
  }
  dispatchEvent(ev) {
    this.events.push(ev);
    return true;
  }
  appendChild(el) {
    this.children.push(el);
  }
  addEventListener() {}
}
const registry = new Map();
globalThis.HTMLElement = FakeElement;
globalThis.customElements = { get: (n) => registry.get(n), define: (n, c) => registry.set(n, c) };
globalThis.window = globalThis;
globalThis.document = {
  createElement(tag) {
    const C = registry.get(tag);
    return C ? new C() : { tag, addEventListener() {} };
  },
};
const realInfo = console.info;
console.info = () => {};
await import("../../custom_components/price_heater/frontend/price-heater-cards.js?v=test");
console.info = realInfo;

const tick = () => new Promise((r) => setTimeout(r, 0));

test("both cards are registered for the card picker", () => {
  assert.ok(registry.get("price-heater-card"));
  assert.ok(registry.get("price-heater-chart-card"));
  assert.deepEqual(window.customCards.map((c) => c.type), ["price-heater-card", "price-heater-chart-card"]);
});

test("status card shows temperature, reason and toggles automation", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({});
  const { hass, calls } = makeHass({ now: Date.now() });
  card.hass = hass;
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /20\.4/);
  assert.match(html, /Cheap electricity – heating/);
  assert.match(html, /Living room/);
  assert.match(html, /data-toggle="automation"/);
  const target = { dataset: { toggle: "automation" } };
  card._onClick({ composedPath: () => [target] });
  assert.deepEqual(calls[0], { domain: "switch", service: "turn_off", data: { entity_id: "switch.price_heater_automation" } });
});

test("status card shows power, energy and cost", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({});
  card.hass = makeHass({ lang: "sv" }).hass;
  const html = card.shadowRoot.innerHTML;
  // power now sits in the top row, next to the temperature
  const hero = html.slice(html.indexOf('class="hero"'), html.indexOf('class="gauge"'));
  assert.match(hero, /Effekt nu/);
  assert.match(hero, /1,18 kW/);
  assert.match(html, /Förbrukat idag/);
  assert.match(html, /4,37/);
  assert.match(html, /3,91/);
  assert.match(html, /Kostnad idag/);
  const rated = document.createElement("price-heater-card");
  rated.setConfig({});
  rated.hass = makeHass({ power: "rated" }).hass;
  assert.match(rated.shadowRoot.innerHTML, /estimated from rated power/);
  const none = document.createElement("price-heater-card");
  none.setConfig({});
  none.hass = makeHass({ power: null }).hass;
  assert.doesNotMatch(none.shadowRoot.innerHTML, /Power now/);
  assert.match(none.shadowRoot.innerHTML, /rated power under Configure/);
});

test("status card in Swedish with automation off", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({ title: "Vardagsrum" });
  card.hass = makeHass({ lang: "sv", enabled: false, reason: "expensive_price", heater: "off" }).hass;
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /Dyrt – väntar/);
  assert.match(html, /20,4/);
  assert.match(html, /Automatiken är av/);
  assert.match(html, /Vardagsrum/);
});

test("chart card draws bars and loads history", async () => {
  const card = document.createElement("price-heater-chart-card");
  card.setConfig({});
  card.hass = makeHass().hass;
  await tick();
  await tick();
  const html = card.shadowRoot.innerHTML;
  assert.equal((html.match(/class="bar /g) || []).length, 24);
  assert.match(html, /class="strip-on"/);
  assert.match(html, /class="temp"/);
  assert.match(html, /class="power"/);
  assert.match(html, /Today 4\.37 kWh · 3\.91 SEK/);
});

test("problems are shown instead of the card", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({});
  card.hass = { states: {}, entities: {}, language: "en" };
  assert.match(card.shadowRoot.innerHTML, /No Price Heater found/);
  assert.throws(() => card.setConfig({ entity: "switch.x" }));
});

test("status card shows the smart plan, floor and outdoor", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({});
  card.hass = makeHass({ reason: "planned_off", heater: "off", smart: "ready" }).hass;
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /Off – stored heat lasts/);
  assert.match(html, /Floor/);
  assert.match(html, /24\.6/);
  assert.match(html, /-3\.5|−3\.5/);
  assert.match(html, /Next heating \d\d:\d\d/);
  // the plan timeline: next 24 h with planned heating and the forecast
  assert.match(html, /Smart plan for the next 2[34] hours/);
  assert.match(html, /class="link timeline"/);
  assert.match(html, /class="strip-plan"/);
  assert.match(html, /class="temp pred"/);
  const hidden = document.createElement("price-heater-card");
  hidden.setConfig({ show_plan: false });
  hidden.hass = makeHass({ reason: "planned_off", heater: "off", smart: "ready" }).hass;
  assert.doesNotMatch(hidden.shadowRoot.innerHTML, /class="link timeline"/);
  assert.match(html, /Next \d+ h: \d+(\.\d)? h heat · 3\.07 SEK/);
});

test("status card says smart is still learning", () => {
  const card = document.createElement("price-heater-card");
  card.setConfig({});
  card.hass = makeHass({ lang: "sv", smart: "learning" }).hass;
  assert.match(card.shadowRoot.innerHTML, /Smart lär sig/);
});

test("chart card draws the plan, forecast and floor into tomorrow", async () => {
  const now = Date.now();
  const card = document.createElement("price-heater-chart-card");
  card.setConfig({});
  card.hass = makeHass({ now, smart: "ready" }).hass;
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /class="strip-plan"/);
  assert.match(html, /class="temp pred"/);
  assert.match(html, /class="floor"/);
  assert.match(html, /class="floor pred"/);
  assert.match(html, /Planned heating/);
  // bars reach into tomorrow: more than 24 hourly bars
  assert.ok((html.match(/class="bar /g) || []).length > 24);
});
