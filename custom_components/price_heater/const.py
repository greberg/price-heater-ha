"""Constants for the Price Heater integration."""

from __future__ import annotations

DOMAIN = "price_heater"

CONF_HEATER = "heater_entity"
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_PRICE_SENSOR = "price_sensor"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_HYSTERESIS = "hysteresis"
CONF_CHEAP_HOURS = "cheap_hours"
CONF_PRICE_THRESHOLD = "price_threshold"
CONF_MIN_CYCLE = "min_cycle_minutes"
CONF_POWER_SENSOR = "power_sensor"
CONF_HEATER_POWER = "heater_power"

DEFAULT_NAME = "Price Heater"
DEFAULT_MIN_TEMP = 19.0
DEFAULT_MAX_TEMP = 22.0
DEFAULT_HYSTERESIS = 0.5
DEFAULT_CHEAP_HOURS = 6.0
DEFAULT_MIN_CYCLE = 5.0

HEATER_DOMAINS = ["switch", "input_boolean", "climate"]
