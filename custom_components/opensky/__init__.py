"""The opensky component."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OpenSkyApiClient, OpenSkyApiError
from .const import CONF_CONTRIBUTING_USER, DOMAIN, PLATFORMS
from .coordinator import OpenSkyDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up opensky from a config entry."""

    client = OpenSkyApiClient(session=async_get_clientsession(hass))
    if CONF_CLIENT_ID in entry.options and CONF_CLIENT_SECRET in entry.options:
        try:
            await client.authenticate(
                entry.options[CONF_CLIENT_ID],
                entry.options[CONF_CLIENT_SECRET],
                contributing_user=entry.options.get(CONF_CONTRIBUTING_USER, False),
            )
        except OpenSkyApiError as exc:
            raise ConfigEntryNotReady from exc

    coordinator = OpenSkyDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload opensky config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
