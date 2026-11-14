"""Config and options flow for Price Heater."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CHEAP_HOURS,
    CONF_HEATER,
    CONF_HEATER_POWER,
    CONF_HYSTERESIS,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_POWER_SENSOR,
    CONF_PRICE_SENSOR,
    CONF_PRICE_THRESHOLD,
    CONF_TEMP_SENSOR,
    CONF_FLOOR_SENSOR,
    CONF_MAX_FLOOR_TEMP,
    CONF_OUTDOOR_SENSOR,
    CONF_SOLAR_SENSOR,
    CONF_STRATEGY,
    CONF_WEATHER,
    DEFAULT_MAX_FLOOR_TEMP,
    STRATEGIES,
    STRATEGY_SIMPLE,
    STRATEGY_SMART,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_HYSTERESIS,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_CYCLE,
    DEFAULT_MIN_TEMP,
    DEFAULT_NAME,
    DOMAIN,
    HEATER_DOMAINS,
)
from .logic import range_error


def _temp_selector(minimum: float, maximum: float, step: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=UnitOfTemperature.CELSIUS,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _schema() -> dict:
    """Fields shared by setup and options."""
    return {
        vol.Required(CONF_HEATER): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=HEATER_DOMAINS)
        ),
        vol.Required(CONF_TEMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Required(CONF_PRICE_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): _temp_selector(0, 35, 0.5),
        vol.Required(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): _temp_selector(0, 35, 0.5),
        vol.Required(CONF_HYSTERESIS, default=DEFAULT_HYSTERESIS): _temp_selector(0, 5, 0.1),
        vol.Required(CONF_CHEAP_HOURS, default=DEFAULT_CHEAP_HOURS): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=24, step=0.25, unit_of_measurement="h",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(CONF_PRICE_THRESHOLD): selector.NumberSelector(
            selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_MIN_CYCLE, default=DEFAULT_MIN_CYCLE): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=120, step=1, unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_POWER_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Optional(CONF_HEATER_POWER): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=20000, step=10, unit_of_measurement="W",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_STRATEGY, default=STRATEGY_SIMPLE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=STRATEGIES,
                translation_key=CONF_STRATEGY,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_WEATHER): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        vol.Optional(CONF_OUTDOOR_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_FLOOR_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_SOLAR_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="sensor", device_class=["illuminance", "irradiance", "power"]
            )
        ),
        vol.Optional(CONF_MAX_FLOOR_TEMP, default=DEFAULT_MAX_FLOOR_TEMP): _temp_selector(15, 40, 0.5),
    }


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    error = range_error(
        user_input[CONF_MIN_TEMP], user_input[CONF_MAX_TEMP], user_input[CONF_HYSTERESIS]
    )
    if error:
        return {"base": error}
    if user_input.get(CONF_STRATEGY) == STRATEGY_SMART and not (
        user_input.get(CONF_WEATHER) or user_input.get(CONF_OUTDOOR_SENSOR)
    ):
        return {"base": "smart_needs_outdoor"}
    return {}


class PriceHeaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_HEATER])
                self._abort_if_unique_id_configured()
                data = dict(user_input)
                title = data.pop(CONF_NAME)
                return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(), **_schema()}
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PriceHeaterOptionsFlow()


class PriceHeaterOptionsFlow(OptionsFlow):
    """Change settings after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_schema()), user_input or current
            ),
            errors=errors,
        )
