"""Fan controls for supported ConnectBox ventilation units."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .entity import ConnectBoxDeviceEntity, supported_device_ids
from .models import RunMode

PRESET_LEVELS = {
    "Level 1": 1,
    "Level 2": 2,
    "Level 3": 3,
    "Level 4": 4,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up fan entities and add newly attached devices dynamically."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new_entities() -> None:
        new_ids = supported_device_ids(coordinator) - known
        if new_ids:
            async_add_entities(
                ConnectBoxFan(coordinator, device_id) for device_id in sorted(new_ids)
            )
            known.update(new_ids)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxFan(ConnectBoxDeviceEntity, FanEntity):
    """Ventilation-level control for one attached unit."""

    _attr_translation_key = "ventilation"
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_percentage_step = 25
    _attr_preset_modes = list(PRESET_LEVELS)
    _attr_speed_count = 4

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data["gateway_uuid"]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_ventilation"

    @property
    def is_on(self) -> bool | None:
        """Return whether this unit is actively ventilating."""
        if self.coordinator.data is None:
            return None
        if self.coordinator.data.run_state.run_mode == RunMode.OFF:
            return False
        percentage = self.percentage
        return percentage > 0 if percentage is not None else None

    @property
    def percentage(self) -> int | None:
        """Map verified ventilation levels 1–4 to Home Assistant percentage."""
        data = self.device_data
        if data is None or self.coordinator.data is None:
            return None
        room, _device = data
        level = room.level_for_mode(self.coordinator.data.run_state.temperature_mode)
        return level * 25 if level in (0, 1, 2, 3, 4) else None

    @property
    def preset_mode(self) -> str | None:
        """Return the current ventilation level as a named preset."""
        percentage = self.percentage
        if percentage is None or percentage == 0:
            return None
        level = percentage // 25
        return next(
            (name for name, value in PRESET_LEVELS.items() if value == level),
            None,
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set a verified level or enter standby for zero percent."""
        if percentage == 0:
            await self.async_turn_off()
            return
        data = self.device_data
        if data is None:
            return
        if (
            self.coordinator.data is not None
            and self.coordinator.data.run_state.run_mode == RunMode.OFF
        ):
            await self.coordinator.async_set_power(True)
        level = max(1, min(4, round(percentage / 25)))
        await self.coordinator.async_set_level(data[0].room_id, level)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a named ventilation level."""
        await self.async_set_percentage(PRESET_LEVELS[preset_mode] * 25)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Wake the system and use level 1 when no speed is supplied."""
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return

        data = self.device_data
        if data is None:
            return
        if (
            self.coordinator.data is not None
            and self.coordinator.data.run_state.run_mode == RunMode.OFF
        ):
            await self.coordinator.async_set_power(True)
        await self.coordinator.async_set_level(data[0].room_id, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Use device level 0 where reported, otherwise use global standby."""
        data = self.device_data
        if data is not None and data[1].level_zero_supported:
            await self.coordinator.async_set_level(data[0].room_id, 0)
        else:
            await self.coordinator.async_set_power(False)
