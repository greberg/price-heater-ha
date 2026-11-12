import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../../custom_components/price_heater/frontend/price-heater-core.js";
import { makeHass } from "./fixtures.mjs";

const NOON = new Date(2026, 10, 12, 12, 30).getTime();

test("finds the status sensor from the registry and its entity map", () => {
  const { hass, ids } = makeHass({ now: NOON });
  const found = core.resolveEntities(hass, {});
  assert.equal(found.problem, null);
  assert.equal(found.ids.heater, ids.heater);
  assert.equal(found.ids.status, ids.status);
});

test("falls back to attributes without the registry, and reports problems", () => {
  const { hass } = makeHass({ now: NOON });
  delete hass.entities;
  assert.equal(core.resolveEntities(hass, {}).problem, null);
  assert.equal(core.resolveEntities(hass, { entity: "sensor.other" }).problem, "unknown_entity");
  assert.equal(core.resolveEntities({ states: {} }, {}).problem, "no_entity");
});

test("status model", () => {
  const { hass } = makeHass({ now: NOON, enabled: false });
  const m = core.statusModel(hass, core.resolveEntities(hass).ids);
  assert.equal(m.temperature, 20.4);
  assert.equal(m.heaterOn, true);
  assert.equal(m.heating, true);
  assert.equal(m.title, "Living room");
  assert.equal(m.priceUnit, "SEK/kWh");
  assert.equal(m.priceDigits, 2);
  assert.deepEqual(m.alerts.map((a) => a.text), ["alert.disabled"]);
});

test("climate heaters count as on unless off", () => {
  assert.equal(core.heaterIsOn({ entity_id: "climate.x", state: "heat" }), true);
  assert.equal(core.heaterIsOn({ entity_id: "climate.x", state: "off" }), false);
  assert.equal(core.heaterIsOn({ entity_id: "switch.x", state: "unavailable" }), null);
});

test("temperature gauge", () => {
  const g = core.tempGauge(20.5, 19, 22);
  assert.ok(g.minPct < g.valuePct && g.valuePct < g.maxPct);
  assert.equal(g.zone, "ok");
  assert.equal(core.tempGauge(18, 19, 22).zone, "cold");
  assert.equal(core.tempGauge(25, 19, 22).zone, "warm");
  assert.equal(core.tempGauge(20, null, 22), null);
});

test("heater on-spans from minimal history, clipped to now", () => {
  const start = 0;
  const rows = [{ s: "off", lu: 0 }, { s: "on", lu: 3600 }, { s: "off", lu: 7200 }, { s: "on", lu: 10800 }];
  assert.deepEqual(core.onSpans(rows, "switch.r", start, 12600000), [
    { start: 3600000, end: 7200000 },
    { start: 10800000, end: 12600000 },
  ]);
  assert.deepEqual(core.onSpans([{ state: "heat", last_changed: new Date(0).toISOString() }], "climate.r", 0, 1000), [{ start: 0, end: 1000 }]);
});

test("price chart geometry", () => {
  const { hass } = makeHass({ now: NOON });
  const a = hass.states["sensor.price_heater_status"].attributes;
  const chart = core.priceChart({
    schedule: a.prices_today,
    now: NOON,
    cheapLimit: a.cheap_price_limit,
    threshold: 0.75,
    spans: [{ start: NOON - 3600000, end: NOON }],
    temps: [{ t: NOON - 7200000, v: 20 }, { t: NOON, v: 20.5 }],
    min: 19,
    max: 22,
  });
  assert.equal(chart.empty, false);
  assert.equal(chart.bars.length, 24);
  assert.equal(chart.bars.filter((b) => b.current).length, 1);
  assert.ok(chart.bars[12].current);
  assert.ok(chart.nowX > chart.plot.x0 && chart.nowX < chart.plot.x1);
  assert.ok(chart.limitY !== null && chart.thresholdY !== null);
  assert.equal(chart.spans.length, 1);
  assert.match(chart.tempPath, /^M/);
  // taller price, taller bar
  const b18 = chart.bars[18];
  const b3 = chart.bars[3];
  assert.ok(b18.h > b3.h);
  for (const b of chart.bars) assert.ok(b.y >= chart.plot.y0 - 0.1 && b.y + b.h <= chart.plot.y1 + 0.1);
});

test("negative prices stay inside the plot", () => {
  const schedule = Array.from({ length: 24 }, (_, h) => ({ start: new Date(2026, 0, 1, h).toISOString(), price: h - 5, cheap: h < 6 }));
  const chart = core.priceChart({ schedule, now: 0 });
  for (const b of chart.bars) assert.ok(b.y >= chart.plot.y0 - 0.1 && b.y + b.h <= chart.plot.y1 + 0.1);
  assert.equal(chart.nowX, null);
});

test("empty schedule", () => {
  assert.equal(core.priceChart({ schedule: [], now: 0 }).empty, true);
  assert.equal(core.priceChart({ schedule: [{ start: "2026-01-01T00:00:00Z", price: null }], now: 0 }).empty, true);
});

test("texts: Swedish, fallback and placeholders", () => {
  assert.equal(core.t("sv-SE", "label.on"), "På");
  assert.equal(core.t("de", "label.on"), "On");
  assert.equal(core.t("en", "label.rank_value", { rank: 3, count: 24 }), "3 of 24");
  for (const key of Object.keys(core.STRINGS.en)) assert.ok(key in core.STRINGS.sv, `sv is missing ${key}`);
});

test("number formats follow the profile", () => {
  assert.equal(core.formatNumber(1.5, 2, core.localeOf({ locale: { language: "sv", number_format: "language" } })), "1,50");
  assert.equal(core.formatNumber(1.5, 2, core.localeOf({ locale: { language: "sv", number_format: "comma_decimal" } })), "1.50");
  assert.equal(core.formatNumber(null), "–");
});
