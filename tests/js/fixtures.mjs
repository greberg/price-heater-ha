// A fake hass with one Price Heater, shared by the JS tests and the preview.
export function makeHass({ now = Date.now(), lang = "en", reason = "cheap_price", enabled = true, heater = "on", temp = "20.4" } = {}) {
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const prices = [1.02, 0.85, 0.31, 0.22, 0.27, 0.36, 1.21, 1.52, 1.43, 1.05, 0.93, 0.81,
    0.72, 0.7, 0.84, 0.95, 1.33, 1.84, 2.05, 1.91, 1.48, 1.22, 1.1, 1.0];
  const limit = [...prices].sort((a, b) => a - b)[5];
  const prices_today = prices.map((price, h) => ({
    start: new Date(midnight.getTime() + h * 3600000).toISOString(),
    price,
    cheap: price <= limit || price <= 0.75,
  }));
  const hour = new Date(now).getHours();
  const ids = {
    heater: "switch.radiator",
    temperature: "sensor.living_room_temperature",
    price: "sensor.nordpool_kwh_se3",
    status: "sensor.price_heater_status",
    automation: "switch.price_heater_automation",
    heating_demand: "binary_sensor.price_heater_heating_demand",
    cheap_price: "binary_sensor.price_heater_cheap_price",
  };
  const st = (entity_id, state, attributes = {}) => ({ entity_id, state, attributes, last_changed: new Date(now).toISOString() });
  const states = {
    [ids.status]: st(ids.status, reason, {
      friendly_name: "Living room Status",
      automation_enabled: enabled,
      temperature: Number(temp),
      min_temp: 19,
      max_temp: 22,
      current_price: prices[hour],
      is_cheap: prices_today[hour].cheap,
      cheap_price_limit: limit,
      price_threshold: 0.75,
      price_rank_today: 1 + prices.filter((p) => p < prices[hour]).length,
      price_slots_today: 24,
      waiting_for_min_cycle: false,
      price_unit: "SEK/kWh",
      prices_today,
      entities: ids,
    }),
    [ids.heater]: st(ids.heater, heater, { friendly_name: "Radiator" }),
    [ids.temperature]: st(ids.temperature, temp, { unit_of_measurement: "°C" }),
    [ids.price]: st(ids.price, String(prices[hour]), { unit_of_measurement: "SEK/kWh" }),
    [ids.automation]: st(ids.automation, enabled ? "on" : "off"),
    [ids.heating_demand]: st(ids.heating_demand, heater),
    [ids.cheap_price]: st(ids.cheap_price, prices_today[hour].cheap ? "on" : "off"),
  };
  const entities = {
    [ids.status]: { entity_id: ids.status, platform: "price_heater", translation_key: "status" },
  };
  const calls = [];
  const history = {
    [ids.heater]: [
      { s: "off", lu: midnight.getTime() / 1000 },
      { s: "on", lu: (midnight.getTime() + 2 * 3600000) / 1000 },
      { s: "off", lu: (midnight.getTime() + 5.5 * 3600000) / 1000 },
      { s: "on", lu: (midnight.getTime() + 12 * 3600000) / 1000 },
    ],
    [ids.temperature]: Array.from({ length: 48 }, (_, i) => ({
      s: String(19.6 + 1.6 * Math.sin(i / 6)),
      lu: (midnight.getTime() + i * 1800000) / 1000,
    })).filter((r) => r.lu * 1000 <= now),
  };
  return {
    ids,
    calls,
    hass: {
      states,
      entities,
      language: lang,
      locale: { language: lang, number_format: "language", time_format: "24" },
      callService: (domain, service, data) => calls.push({ domain, service, data }),
      callWS: async () => history,
    },
  };
}
