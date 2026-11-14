// Price Heater cards: entity lookup, texts, formatting and chart shaping.
//
// No DOM here, so it runs (and is unit tested) under plain Node. The cards
// in price-heater-cards.js import it with their own ?v= query.

export const DOMAIN = "price_heater";
const MISSING = new Set(["unavailable", "unknown", "", "undefined", "null"]);

// --------------------------------------------------------------------------
// Texts (Swedish and English; anything else falls back to English)
// --------------------------------------------------------------------------

const en = {
  "card.price-heater-card.name": "Price Heater",
  "card.price-heater-card.description": "Temperature, heater and why it is on or off, with the automation switch.",
  "card.price-heater-chart-card.name": "Price Heater chart",
  "card.price-heater-chart-card.description": "Today's electricity prices with the cheap hours, when the heater ran and the room temperature.",
  "reason.temperature_unavailable": "Temperature unavailable",
  "reason.below_min": "Below minimum – heating",
  "reason.above_max": "Above maximum – off",
  "reason.cheap_price": "Cheap electricity – heating",
  "reason.near_max": "Cheap, but close to maximum",
  "reason.expensive_price": "Expensive – waiting",
  "reason.price_unavailable": "Price unavailable",
  "reason.none": "Waiting for first check",
  "label.temperature": "Temperature",
  "label.heater": "Heater",
  "label.price": "Price now",
  "label.rank": "Rank today",
  "label.rank_value": "{rank} of {count}",
  "label.cheap_limit": "Cheap up to",
  "label.threshold": "Threshold",
  "label.range": "Range",
  "label.on": "On",
  "label.off": "Off",
  "label.automation": "Automation",
  "label.manual": "Manual",
  "label.cheap": "Cheap",
  "label.expensive": "Expensive",
  "label.heater_ran": "Heater on",
  "label.now": "Now",
  "label.power": "Power now",
  "label.energy_today": "Used today",
  "label.cost_today": "Cost today",
  "label.rated": "estimated from rated power",
  "label.no_power": "Add a power sensor or the heater's rated power under Configure to see power and daily usage.",
  "label.power_used": "Power",
  "label.today_total": "Today {energy} · {cost}",
  "alert.disabled": "Automation is off – the heater is not being controlled.",
  "alert.waiting": "Waiting for the minimum time between switches.",
  "alert.temperature": "The temperature sensor is unavailable.",
  "alert.price": "The price sensor is unavailable.",
  "alert.heater": "The heater is unavailable.",
  "alert.power": "The power sensor is unavailable.",
  "chart.empty": "The price sensor has no price list for today. Cheapest hours need Nordpool (HACS), Energi Data Service or ENTSO-e.",
  "chart.aria": "Today's electricity prices",
  "problem.no_entity": "No Price Heater found. Add the integration first.",
  "problem.unknown_entity": "Entity {entity} is not a Price Heater status sensor.",
  "problem.card_error": "The card failed: {error}",
  "editor.entity": "Price Heater",
  "editor.entity_help": "Its Status sensor. Only needed with more than one Price Heater.",
  "editor.title": "Title",
  "editor.title_help": "Leave empty for the integration's name.",
  "editor.show_temperature": "Show temperature line",
  "editor.show_history": "Show when the heater ran",
  "editor.show_power": "Show power",
};

const sv = {
  "card.price-heater-card.name": "Price Heater",
  "card.price-heater-card.description": "Temperatur, element och varför det är på eller av, med automatikbrytaren.",
  "card.price-heater-chart-card.name": "Price Heater-diagram",
  "card.price-heater-chart-card.description": "Dagens elpriser med de billiga timmarna, när elementet var på och rumstemperaturen.",
  "reason.temperature_unavailable": "Temperatur saknas",
  "reason.below_min": "Under minimum – värmer",
  "reason.above_max": "Över max – av",
  "reason.cheap_price": "Billig el – värmer",
  "reason.near_max": "Billigt, men nära max",
  "reason.expensive_price": "Dyrt – väntar",
  "reason.price_unavailable": "Pris saknas",
  "reason.none": "Väntar på första kontrollen",
  "label.temperature": "Temperatur",
  "label.heater": "Element",
  "label.price": "Pris nu",
  "label.rank": "Plats idag",
  "label.rank_value": "{rank} av {count}",
  "label.cheap_limit": "Billigt upp till",
  "label.threshold": "Tröskel",
  "label.range": "Intervall",
  "label.on": "På",
  "label.off": "Av",
  "label.automation": "Automatik",
  "label.manual": "Manuellt",
  "label.cheap": "Billigt",
  "label.expensive": "Dyrt",
  "label.heater_ran": "Element på",
  "label.now": "Nu",
  "label.power": "Effekt nu",
  "label.energy_today": "Förbrukat idag",
  "label.cost_today": "Kostnad idag",
  "label.rated": "uppskattat från märkeffekt",
  "label.no_power": "Lägg till en effektsensor eller elementets märkeffekt under Konfigurera för att se effekt och förbrukning per dag.",
  "label.power_used": "Effekt",
  "label.today_total": "Idag {energy} · {cost}",
  "alert.disabled": "Automatiken är av – elementet styrs inte.",
  "alert.waiting": "Väntar på minsta tid mellan omslag.",
  "alert.temperature": "Temperaturgivaren är inte tillgänglig.",
  "alert.price": "Elprissensorn är inte tillgänglig.",
  "alert.heater": "Elementet är inte tillgängligt.",
  "alert.power": "Effektsensorn är inte tillgänglig.",
  "chart.empty": "Elprissensorn har ingen prislista för idag. Billigaste timmar kräver Nordpool (HACS), Energi Data Service eller ENTSO-e.",
  "chart.aria": "Dagens elpriser",
  "problem.no_entity": "Ingen Price Heater hittades. Lägg till integrationen först.",
  "problem.unknown_entity": "{entity} är inte en statussensor från Price Heater.",
  "problem.card_error": "Kortet misslyckades: {error}",
  "editor.entity": "Price Heater",
  "editor.entity_help": "Dess statussensor. Behövs bara med fler än en Price Heater.",
  "editor.title": "Rubrik",
  "editor.title_help": "Lämna tomt för integrationens namn.",
  "editor.show_temperature": "Visa temperaturlinje",
  "editor.show_history": "Visa när elementet var på",
  "editor.show_power": "Visa effekt",
};

export const STRINGS = { en, sv };

export function normLang(lang) {
  return String(lang || "en").toLowerCase().startsWith("sv") ? "sv" : "en";
}

export function langOf(hass) {
  return normLang(hass?.locale?.language || hass?.language);
}

export function registrationLang() {
  return normLang(globalThis.document?.documentElement?.lang || globalThis.navigator?.language);
}

export function t(lang, key, vars) {
  let text = STRINGS[normLang(lang)][key] ?? en[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) text = text.replaceAll(`{${k}}`, String(v));
  return text;
}

// --------------------------------------------------------------------------
// Numbers and times in the viewer's format
// --------------------------------------------------------------------------

export function toNumber(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function isMissing(stateObj) {
  return !stateObj || MISSING.has(String(stateObj.state));
}

const NUMBER_LOCALES = { comma_decimal: "en-US", decimal_comma: "de-DE", space_comma: "fr-FR" };

export function localeOf(hass) {
  const l = hass?.locale || {};
  const language = l.language || hass?.language || "en";
  let hour12;
  if (l.time_format === "12") hour12 = true;
  else if (l.time_format === "24") hour12 = false;
  return {
    lang: normLang(language),
    locale: language,
    numberLocale: NUMBER_LOCALES[l.number_format] || (l.number_format === "system" ? undefined : language),
    grouping: l.number_format !== "none",
    hour12,
    timeZone: l.time_zone === "server" ? hass?.config?.time_zone : undefined,
  };
}

export function formatNumber(value, digits = 1, loc = {}) {
  const n = toNumber(value);
  if (n === null) return "–";
  try {
    return new Intl.NumberFormat(loc.numberLocale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      useGrouping: loc.grouping !== false,
    }).format(n);
  } catch (_err) {
    return n.toFixed(digits);
  }
}

export function formatClock(time, loc = {}) {
  try {
    return new Intl.DateTimeFormat(loc.locale, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: loc.hour12,
      timeZone: loc.timeZone,
    }).format(new Date(time));
  } catch (_err) {
    const d = new Date(time);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
}

/** "850 W", "1.2 kW" (kW from 1000 W). */
export function formatPower(watts, loc = {}) {
  const n = toNumber(watts);
  if (n === null) return "–";
  if (Math.abs(n) >= 1000) return `${formatNumber(n / 1000, 2, loc)} kW`;
  return `${formatNumber(n, 0, loc)} W`;
}

/** Sensible digits for a price: öre-level for kr/€ prices, fewer for big numbers. */
export function priceDigits(values) {
  const max = Math.max(0, ...values.map((v) => Math.abs(toNumber(v) ?? 0)));
  if (max >= 100) return 0;
  if (max >= 10) return 1;
  return 2;
}

// --------------------------------------------------------------------------
// Finding the integration's entities
// --------------------------------------------------------------------------

/** All Price Heater status sensors (entity registry first, then attributes). */
export function statusEntities(hass) {
  const fromRegistry = Object.values(hass?.entities || {})
    .filter((e) => e?.platform === DOMAIN && e?.translation_key === "status")
    .map((e) => e.entity_id);
  if (fromRegistry.length) return fromRegistry.sort();
  return Object.keys(hass?.states || {})
    .filter((id) => id.startsWith("sensor.") && isStatusState(hass.states[id]))
    .sort();
}

function isStatusState(stateObj) {
  const a = stateObj?.attributes;
  return !!a && typeof a.entities === "object" && a.entities !== null && "prices_today" in a;
}

/** ``{status, ids, problem}`` for a card config (``entity`` optional). */
export function resolveEntities(hass, config = {}) {
  let status = config.entity;
  if (status) {
    if (!isStatusState(hass?.states?.[status])) return { status, ids: {}, problem: "unknown_entity" };
  } else {
    status = statusEntities(hass)[0];
    if (!status) return { status: null, ids: {}, problem: "no_entity" };
  }
  const map = hass.states[status]?.attributes?.entities || {};
  return { status, ids: { ...map, status }, problem: null };
}

// --------------------------------------------------------------------------
// Models
// --------------------------------------------------------------------------

const HEATING_REASONS = new Set(["below_min", "cheap_price"]);
const PROBLEM_REASONS = new Set(["temperature_unavailable", "price_unavailable"]);

export function heaterIsOn(stateObj) {
  if (isMissing(stateObj)) return null;
  const domain = String(stateObj.entity_id || "").split(".")[0];
  if (domain === "climate") return stateObj.state !== "off";
  return stateObj.state === "on";
}

/** Everything the status card shows. */
export function statusModel(hass, ids) {
  const st = (key) => (ids[key] ? hass.states[ids[key]] : undefined);
  const status = st("status");
  const a = status?.attributes || {};
  const tempState = st("temperature");
  const temperature = toNumber(isMissing(tempState) ? a.temperature : tempState.state) ?? toNumber(a.temperature);
  const automation = st("automation");
  const enabled = automation ? automation.state === "on" : a.automation_enabled !== false;
  const heater = st("heater");
  const reason = isMissing(status) ? null : status.state;
  const prices = Array.isArray(a.prices_today) ? a.prices_today : [];

  const alerts = [];
  if (!enabled) alerts.push({ level: "warning", key: "automation", text: "alert.disabled" });
  if (enabled && a.waiting_for_min_cycle) alerts.push({ level: "info", key: "heater", text: "alert.waiting" });
  if (isMissing(tempState)) alerts.push({ level: "error", key: "temperature", text: "alert.temperature" });
  if (isMissing(st("price"))) alerts.push({ level: "error", key: "price", text: "alert.price" });
  if (isMissing(heater)) alerts.push({ level: "error", key: "heater", text: "alert.heater" });
  if (ids.power_meter && isMissing(st("power_meter"))) alerts.push({ level: "error", key: "power_meter", text: "alert.power" });

  return {
    title: status?.attributes?.friendly_name?.replace(/\s*Status$/i, "") || "Price Heater",
    temperature,
    tempUnit: tempState?.attributes?.unit_of_measurement || "°C",
    min: toNumber(a.min_temp),
    max: toNumber(a.max_temp),
    heaterOn: heaterIsOn(heater),
    demand: st("heating_demand")?.state === "on",
    enabled,
    reason,
    heating: HEATING_REASONS.has(reason),
    problem: PROBLEM_REASONS.has(reason),
    price: toNumber(a.current_price),
    priceUnit: a.price_unit || st("price")?.attributes?.unit_of_measurement || "",
    priceDigits: priceDigits(prices.map((p) => p.price)),
    cheap: a.is_cheap === true ? true : a.is_cheap === false ? false : null,
    rank: toNumber(a.price_rank_today),
    count: toNumber(a.price_slots_today),
    cheapLimit: toNumber(a.cheap_price_limit),
    threshold: toNumber(a.price_threshold),
    hasPower: a.power_source === "sensor" || a.power_source === "rated",
    powerSource: a.power_source || null,
    power: toNumber(a.power),
    energy: toNumber(a.energy_today),
    cost: toNumber(a.cost_today),
    currency: a.currency || "",
    alerts,
  };
}

/** Where the temperature sits on a gauge from below min to above max (0..100). */
export function tempGauge(temperature, min, max) {
  if (min === null || max === null || !(max > min)) return null;
  const pad = Math.max(1, (max - min) * 0.6);
  const lo = min - pad;
  const hi = max + pad;
  const pct = (v) => Math.round(((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * 1000) / 10;
  return {
    minPct: pct(min),
    maxPct: pct(max),
    valuePct: temperature === null ? null : pct(temperature),
    zone: temperature === null ? null : temperature < min ? "cold" : temperature >= max ? "warm" : "ok",
  };
}

// --------------------------------------------------------------------------
// History (WebSocket history/history_during_period, minimal or full)
// --------------------------------------------------------------------------

function rowTime(row) {
  if (row.lu !== undefined) return row.lu * 1000;
  if (row.lc !== undefined) return row.lc * 1000;
  return Date.parse(row.last_changed || row.last_updated);
}

function rowState(row) {
  return row.s !== undefined ? row.s : row.state;
}

/** Time spans the heater was on, clipped to [start, end]. */
export function onSpans(rows, entityId, start, end) {
  const domain = String(entityId || "").split(".")[0];
  const isOn = (s) => (domain === "climate" ? !MISSING.has(String(s)) && s !== "off" : s === "on");
  const spans = [];
  let open = null;
  for (const row of rows || []) {
    const time = Math.max(start, rowTime(row));
    if (!Number.isFinite(time)) continue;
    const on = isOn(rowState(row));
    if (on && open === null) open = time;
    if (!on && open !== null) {
      if (time > open) spans.push({ start: open, end: Math.min(time, end) });
      open = null;
    }
  }
  if (open !== null && end > open) spans.push({ start: open, end });
  return spans.filter((s) => s.end > s.start);
}

/** Numeric points ``{t, v}`` from history rows. */
export function numberPoints(rows) {
  return (rows || [])
    .map((row) => ({ t: rowTime(row), v: toNumber(rowState(row)) }))
    .filter((p) => Number.isFinite(p.t) && p.v !== null);
}

// --------------------------------------------------------------------------
// Price chart
// --------------------------------------------------------------------------

const r1 = (v) => Math.round(v * 10) / 10;

function niceStep(range, target) {
  const raw = range / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

/**
 * SVG geometry for today's prices.
 *
 * schedule: [{start, price, cheap}] (from the status sensor)
 * spans: heater-on spans in ms; temps: [{t, v}] room temperature points
 */
export function priceChart({ schedule, now, cheapLimit = null, threshold = null, spans = [], temps = [], power = [], min = null, max = null, width = 600, height = 220 }) {
  const slots = (schedule || [])
    .map((s) => ({ start: Date.parse(s.start), price: toNumber(s.price), cheap: s.cheap }))
    .filter((s) => Number.isFinite(s.start));
  if (!slots.some((s) => s.price !== null)) return { empty: true, width, height };

  const duration = slots.length > 1 ? slots[1].start - slots[0].start : 3600000;
  const start = slots[0].start;
  const end = slots[slots.length - 1].start + duration;
  const showTemp = temps.length > 0 && min !== null && max !== null;
  const showPower = power.length > 0;
  // Bottom band: the heater-on strip, or the power curve (taller) with a meter.
  const band = showPower ? 30 : 0;
  const plot = { x0: 36, x1: width - (showTemp || showPower ? 32 : 8), y0: 8, y1: height - 34 - band };
  const x = (time) => r1(plot.x0 + ((time - start) / (end - start)) * (plot.x1 - plot.x0));

  const prices = slots.map((s) => s.price).filter((v) => v !== null);
  const pLo = Math.min(0, ...prices);
  const pTop = Math.max(...prices, threshold ?? -Infinity, cheapLimit ?? -Infinity);
  const step = niceStep(Math.max(pTop - pLo, 1e-6), 4);
  const pHi = Math.ceil(pTop / step) * step || step;
  const pBase = Math.floor(pLo / step) * step;
  const y = (v) => r1(plot.y1 - ((v - pBase) / (pHi - pBase)) * (plot.y1 - plot.y0));

  const digits = priceDigits(prices);
  const bars = slots
    .filter((s) => s.price !== null)
    .map((s) => {
      const top = y(Math.max(0, s.price));
      const bottom = y(Math.min(0, s.price));
      return {
        x: x(s.start),
        w: Math.max(0.5, r1(x(s.start + duration) - x(s.start) - (duration >= 3600000 ? 1.5 : 0.5))),
        y: top,
        h: Math.max(0.5, r1(bottom - top)),
        cheap: s.cheap === true,
        current: now >= s.start && now < s.start + duration,
        price: s.price,
        start: s.start,
        end: s.start + duration,
      };
    });

  const yTicks = [];
  for (let v = pBase; v <= pHi + step / 2; v += step) yTicks.push({ y: y(v), value: Math.round(v / step) * step });

  const xTicks = [];
  for (let h = 0; h <= 24; h += 3) {
    const time = start + h * 3600000;
    if (time <= end) xTicks.push({ x: x(time), time });
  }

  const result = {
    empty: false,
    width,
    height,
    plot,
    bars,
    digits,
    yTicks,
    xTicks,
    nowX: now >= start && now <= end ? x(now) : null,
    limitY: cheapLimit !== null ? y(cheapLimit) : null,
    thresholdY: threshold !== null && threshold <= pHi ? y(threshold) : null,
    spans: spans
      .map((s) => ({ x: x(Math.max(start, s.start)), x2: x(Math.min(end, s.end)) }))
      .filter((s) => s.x2 > s.x)
      .map((s) => ({ x: s.x, w: r1(s.x2 - s.x) })),
    stripY: height - 18,
    powerPath: "",
    powerMax: null,
    powerTop: null,
    tempPath: "",
    tempTicks: [],
    minY: null,
    maxY: null,
  };

  if (showPower) {
    // A step area from the strip's bottom up to the band's top.
    const bottom = height - 12;
    const top = plot.y1 + 8;
    const inDay = power.filter((p) => p.t <= now).sort((a, b) => a.t - b.t);
    const peak = Math.max(0, ...inDay.map((p) => p.v));
    if (peak > 0) {
      const yP = (v) => r1(bottom - (Math.max(0, v) / peak) * (bottom - top));
      const stopAt = Math.min(end, now);
      let d = "";
      inDay.forEach((p, i) => {
        const from = Math.max(start, p.t);
        const to = Math.min(stopAt, i + 1 < inDay.length ? inDay[i + 1].t : stopAt);
        if (to <= from) return;
        d += `${d ? " L" : `M${x(from)} ${bottom} L`}${x(from)} ${yP(p.v)} L${x(to)} ${yP(p.v)}`;
      });
      if (d) {
        const lastX = d.match(/L([\d.]+) [\d.]+$/)[1];
        result.powerPath = `${d} L${lastX} ${bottom} Z`;
      }
      result.powerMax = peak;
      result.powerTop = top;
      result.stripY = bottom;
    }
  }

  if (showTemp) {
    const inDay = temps.filter((p) => p.t >= start && p.t <= Math.min(end, now));
    const values = [...inDay.map((p) => p.v), min, max];
    const tLo = Math.floor(Math.min(...values) - 0.5);
    const tHi = Math.ceil(Math.max(...values) + 0.5);
    const yT = (v) => r1(plot.y1 - ((v - tLo) / (tHi - tLo)) * (plot.y1 - plot.y0));
    result.tempPath = inDay.map((p, i) => `${i ? "L" : "M"}${x(p.t)} ${yT(p.v)}`).join(" ");
    if (inDay.length) result.tempPath += ` L${x(Math.min(end, now))} ${yT(inDay[inDay.length - 1].v)}`;
    result.minY = yT(min);
    result.maxY = yT(max);
    result.tempTicks = [
      { y: yT(min), value: min },
      { y: yT(max), value: max },
    ];
  }
  return result;
}
