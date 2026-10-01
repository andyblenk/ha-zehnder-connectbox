"""Fan controls for supported ConnectBox ventilation units."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import SENSOR_MODE_LEVEL, VENTILATION_LEVELS
from .entity import ConnectBoxDeviceEntity, supported_device_ids
from .models import RunMode
from .profiles import supports_sensor_mode

PRESET_LEVELS = {
    "Level 1": 1,
    "Level 2": 2,
    "Level 3": 3,
    "Level 4": 4,
}
PRESET_AUTO = "auto"


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
    _attr_speed_count = 4

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data["gateway_uuid"]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_ventilation"

    @property
    def _room_level(self) -> int | None:
        """Return the raw room value for the active temperature mode."""
        data = self.device_data
        if data is None or self.coordinator.data is None:
            return None
        room, _device = data
        return room.level_for_mode(self.coordinator.data.run_state.temperature_mode)

    @property
    def preset_modes(self) -> list[str]:
        """Offer sensor-controlled operation only for units with a sensor board."""
        modes = list(PRESET_LEVELS)
        data = self.device_data
        if data is not None and supports_sensor_mode(*data):
            modes.append(PRESET_AUTO)
        return modes

    @property
    def is_on(self) -> bool | None:
        """Return whether this unit is actively ventilating."""
        if self.coordinator.data is None:
            return None
        if self.coordinator.data.run_state.run_mode == RunMode.OFF:
            return False
        level = self._room_level
        if level == SENSOR_MODE_LEVEL:
            return True
        return level > 0 if level in VENTILATION_LEVELS else None

    @property
    def percentage(self) -> int | None:
        """Map verified ventilation levels 1–4 to Home Assistant percentage.

        The level chosen by the unit in sensor-controlled operation is not
        reported, so no percentage is shown while the Auto preset is active.
        """
        level = self._room_level
        return level * 25 if level in VENTILATION_LEVELS else None

    @property
    def preset_mode(self) -> str | None:
        """Return the current ventilation level or sensor mode as a preset."""
        level = self._room_level
        if level == SENSOR_MODE_LEVEL:
            return PRESET_AUTO
        return next(
            (name for name, value in PRESET_LEVELS.items() if value == level),
            None,
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set a verified level or enter standby for zero percent."""
        if percentage == 0:
            await self.async_turn_off()
            return
        level = max(1, min(4, round(percentage / 25)))
        await self._async_set_room_level(level)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a named ventilation level or sensor-controlled operation."""
        if preset_mode == PRESET_AUTO:
            await self._async_set_room_level(SENSOR_MODE_LEVEL)
            return
        await self.async_set_percentage(PRESET_LEVELS[preset_mode] * 25)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Wake the system and use level 1 when no speed is supplied.

        Sensor-controlled operation is kept when the room already uses it.
        """
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        if self._room_level == SENSOR_MODE_LEVEL:
            await self._async_wake_system()
            return
        await self._async_set_room_level(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Use device level 0 where reported, otherwise use global standby."""
        data = self.device_data
        if data is not None and data[1].level_zero_supported:
            await self.coordinator.async_set_level(data[0].room_id, 0)
        else:
            await self.coordinator.async_set_power(False)

    async def _async_wake_system(self) -> None:
        """Leave global standby before a room value is applied."""
        if (
            self.coordinator.data is not None
            and self.coordinator.data.run_state.run_mode == RunMode.OFF
        ):
            await self.coordinator.async_set_power(True)

    async def _async_set_room_level(self, level: int) -> None:
        """Wake the system if needed and write the active room value."""
        data = self.device_data
        if data is None:
            return
        await self._async_wake_system()
        await self.coordinator.async_set_level(data[0].room_id, level)
