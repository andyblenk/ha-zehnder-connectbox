"""Zehnder ConnectBox integration."""

from __future__ import annotations

from uuid import UUID

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .client import ConnectBoxClient
from .const import (
    CONF_APP_UUID,
    CONF_CERTIFICATE_SHA256,
    CONF_GATEWAY_UUID,
)
from .coordinator import ZehnderConnectBoxCoordinator

PLATFORMS = (
    Platform.FAN,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
)

type ZehnderConnectBoxConfigEntry = ConfigEntry[ZehnderConnectBoxCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: ZehnderConnectBoxConfigEntry
) -> bool:
    """Set up one paired ConnectBox."""
    client = ConnectBoxClient(
        entry.data[CONF_HOST],
        UUID(entry.data[CONF_GATEWAY_UUID]),
        UUID(entry.data[CONF_APP_UUID]),
        entry.data[CONF_CERTIFICATE_SHA256],
    )
    coordinator = ZehnderConnectBoxCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ZehnderConnectBoxConfigEntry
) -> bool:
    """Unload an entry and close its local connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded
