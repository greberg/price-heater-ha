# Price Heater for Home Assistant

[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![CI](https://github.com/greberg/price-heater-ha/actions/workflows/ci.yml/badge.svg)](https://github.com/greberg/price-heater-ha/actions/workflows/ci.yml)

A custom integration that turns a heater **on or off based on room temperature and electricity price**,
with two dashboard cards included.

## How it decides

Checked every minute and whenever the temperature, price or heater changes, in this order:

| Situation | Heater |
|---|---|
| Temperature sensor unavailable | left as it is |
| Temperature below **minimum** | **ON**, whatever the price (keeps going until min + hysteresis) |
| Temperature at/above **maximum** | **OFF**, whatever the price |
| In between, electricity is **cheap** | **ON** (pre-heats towards max; starts only below max − hysteresis) |
| In between, electricity is **expensive** | **OFF** (lets the room drift down towards min) |

Electricity counts as **cheap** when either is true:

- the current slot is among today's **N cheapest hours** (`cheap_hours`, default 6), or
- the current price is at or below the optional **price threshold**.

A **minimum cycle time** (default 5 min) stops the heater from flapping. The min/max temperature limits ignore it.

## Supported devices

- **Heater:** `switch`, `input_boolean` or `climate` (climate is switched with `hvac_mode: heat` / `off`).
- **Temperature:** any `sensor` with device class temperature.
- **Price:** any `sensor` whose state is the current price. For *cheapest hours* it also needs today's prices as an attribute:
  - `raw_today` / `today`: [Nordpool (HACS)](https://github.com/custom-components/nordpool), Energi Data Service
  - `prices_today`: ENTSO-e

  Hourly and 15-minute price lists both work. With a sensor that has no price list (for example the built-in Tibber or Nord Pool integrations), use the **price threshold** instead.

## Installation

**HACS:**

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=greberg&repository=price-heater-ha&category=integration)

or in HACS: ⋮ → **Custom repositories** → `https://github.com/greberg/price-heater-ha`, type **Integration**. Then install **Price Heater** and restart Home Assistant.

**Manual:** copy `custom_components/price_heater` into your Home Assistant `config/custom_components/` folder and restart.

Then go to **Settings → Devices & services → Add integration → Price Heater**. Every setting can be changed later with **Configure**.

## Entities

| Entity | Purpose |
|---|---|
| `switch.<name>_automation` | Turn the automatic control on or off. When off, the integration only reports and never touches the heater. Use it for manual override. |
| `sensor.<name>_status` | Why the heater is on or off. Attributes show temperature, price, price rank today, the cheap-price limit and more. |
| `binary_sensor.<name>_heating_demand` | What the rules want right now. |
| `binary_sensor.<name>_cheap_price` | Whether electricity counts as cheap right now. |

If you switch the heater by hand while automation is on, it will be switched back at the next check (after the minimum cycle time). Turn the automation switch off first.

## Cards

The integration ships two dashboard cards; nothing else to install. Edit a dashboard → **Add card** → search for **Price Heater**. Both follow your theme (light or dark) and each viewer's language (Swedish or English) and number/time format. After an update, reload the browser if the cards look old.

Options common to both cards:

- `entity`: the Price Heater's **Status** sensor. Only needed with more than one Price Heater; otherwise the first one is used.
- `title`: replaces the card title (default: the integration's name); `title: ""` hides the header.

**Price Heater** — the room temperature (big), a gauge with the min–max band, the heater (tap for its history) and in words why it is on or off. Below: the price now (green when cheap, orange when expensive), its rank today, the cheap-price limit and your threshold. An **Automation / Manual** chip in the header turns the automation on or off. Alerts show only when something needs attention: automation off, waiting for the minimum cycle time, or a sensor or the heater unavailable.

```yaml
type: custom:price-heater-card
```

**Price Heater chart** — today's prices as bars, green for the hours that count as cheap, with the current hour highlighted, the cheap-price limit (dashed green) and your threshold (dotted). A strip below shows when the heater actually ran today, and a blue line the room temperature against your min and max. Hover a bar for its time and price.

```yaml
type: custom:price-heater-chart-card
show_history: true      # strip with when the heater ran (default true)
show_temperature: true  # room temperature line (default true)
```

## Development

The decision rules live in `custom_components/price_heater/logic.py` and have no Home Assistant imports. The cards are plain custom elements in `custom_components/price_heater/frontend/` (no build step); data shaping is in `price-heater-core.js`.

```bash
python3 -m pytest tests
```

```bash
node --test tests/js/*.test.mjs
```

To look at the cards without Home Assistant, serve the repository root (`python3 -m http.server`) and open `/tests/js/preview.html`.

A new `version` in `manifest.json` merged to `main` is released automatically once CI passes, so HACS offers it as an update.

## License

MIT
