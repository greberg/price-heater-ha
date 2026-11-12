"""Serve the Price Heater Lovelace cards (``frontend/price-heater-cards.js``).

The folder is a static path and the cards module an extra frontend module,
registered once per Home Assistant run. The integration version is the
module's query string, so browsers fetch new cards after an update.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

URL_BASE = "/price_heater_cards"
CARDS_MODULE = "price-heater-cards.js"
_REGISTERED = f"{DOMAIN}_cards_registered"


async def async_register_cards(hass: HomeAssistant) -> None:
    """Make the cards available on every dashboard."""
    if hass.data.get(_REGISTERED) or getattr(hass, "http", None) is None:
        return
    hass.data[_REGISTERED] = True
    integration = await async_get_integration(hass, DOMAIN)
    directory = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(directory), True)]
    )
    add_extra_js_url(hass, f"{URL_BASE}/{CARDS_MODULE}?v={integration.version}")
