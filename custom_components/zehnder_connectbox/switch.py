"""Standby control for Zehnder ConnectBox."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxGatewayEntity
from .models import RunMode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the gateway standby switch."""
    async_add_entities([ConnectBoxVentilationSwitch(entry.runtime_data)])


class ConnectBoxVentilationSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Expose global standby as an on/off control."""

    _attr_translation_key = "central_ventilation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_ventilation_power"

    @property
    def is_on(self) -> bool | None:
        """Return whether the gateway is outside standby."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.run_state.run_mode != RunMode.OFF

    async def async_turn_on(self, **kwargs) -> None:
        """Restore the most recently verified active mode."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Put all attached ventilation units into standby."""
        await self.coordinator.async_set_power(False)
