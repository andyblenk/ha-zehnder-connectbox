"""Operating-mode select for Zehnder ConnectBox."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxGatewayEntity
from .models import RunMode

MODE_TO_OPTION = {
    RunMode.AUTOMATIC: "automatic",
    RunMode.MANUAL: "manual",
    RunMode.ANTIFREEZE: "antifreeze",
    RunMode.OFF: "off",
}
OPTION_TO_MODE = {option: mode for mode, option in MODE_TO_OPTION.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the gateway run-mode select."""
    async_add_entities([ConnectBoxOperatingMode(entry.runtime_data)])


class ConnectBoxOperatingMode(ConnectBoxGatewayEntity, SelectEntity):
    """Select the system-wide operating mode."""

    _attr_translation_key = "operating_mode"
    _attr_options: ClassVar[list[str]] = list(OPTION_TO_MODE)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_operating_mode"

    @property
    def current_option(self) -> str | None:
        """Return the current operating mode."""
        if self.coordinator.data is None:
            return None
        try:
            return MODE_TO_OPTION[RunMode(self.coordinator.data.run_state.run_mode)]
        except (KeyError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Set a supported operating mode."""
        await self.coordinator.async_set_mode(OPTION_TO_MODE[option])
