"""Operating-mode, situation and per-situation level selects."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID, SENSOR_MODE_LEVEL
from .entity import ConnectBoxDeviceEntity, ConnectBoxGatewayEntity
from .models import RunMode, TemperatureMode
from .profiles import is_supported, supports_sensor_mode

MODE_TO_OPTION = {
    RunMode.AUTOMATIC: "automatic",
    RunMode.MANUAL: "manual",
    RunMode.ANTIFREEZE: "antifreeze",
    RunMode.OFF: "off",
}
OPTION_TO_MODE = {option: mode for mode, option in MODE_TO_OPTION.items()}

# Situations of the official app with their own configured level per room.
SITUATIONS = {
    TemperatureMode.AWAKE: "situation_level_awake",
    TemperatureMode.ASLEEP: "situation_level_asleep",
    TemperatureMode.AWAY: "situation_level_away",
    TemperatureMode.ANTIFREEZE: "situation_level_antifreeze",
}
LEVEL_TO_OPTION = {
    0: "standby",
    1: "level_1",
    2: "level_2",
    3: "level_3",
    4: "level_4",
    SENSOR_MODE_LEVEL: "auto",
}
OPTION_TO_LEVEL = {option: level for level, option in LEVEL_TO_OPTION.items()}

# Active situation of the system. Only the situations of the app's manual mode
# can be selected; the others are shown while a schedule or mode applies them.
SITUATION_TO_OPTION = {
    TemperatureMode.AWAKE: "awake",
    TemperatureMode.ASLEEP: "asleep",
    TemperatureMode.AWAY: "away",
    TemperatureMode.ANTIFREEZE: "antifreeze",
    TemperatureMode.OVERRIDE: "override",
}
OPTION_TO_SITUATION = {option: mode for mode, option in SITUATION_TO_OPTION.items()}
SELECTABLE_SITUATIONS = ("awake", "away")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the run-mode and situation selects and the level selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [ConnectBoxOperatingMode(coordinator), ConnectBoxSituation(coordinator)]
    )
    known: set[tuple[int, int]] = set()

    @callback
    def add_new_entities() -> None:
        if coordinator.data is None:
            return
        new_entities: list[ConnectBoxSituationLevel] = []
        for room in coordinator.data.rooms:
            modes = {value.temperature_mode for value in room.ventilation}
            for device in room.devices:
                if not is_supported(device):
                    continue
                for mode in SITUATIONS:
                    identity = (device.device_id, int(mode))
                    if identity in known or int(mode) not in modes:
                        continue
                    new_entities.append(
                        ConnectBoxSituationLevel(coordinator, device.device_id, mode)
                    )
                    known.add(identity)
        if new_entities:
            async_add_entities(new_entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


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


class ConnectBoxSituation(ConnectBoxGatewayEntity, SelectEntity):
    """Show the active situation and select one of the manual mode."""

    _attr_translation_key = "situation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_situation"

    @property
    def options(self) -> list[str]:
        """Offer the manual mode's situations and the currently active one."""
        options = list(SELECTABLE_SITUATIONS)
        current = self.current_option
        if current is not None and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        """Return the active situation."""
        if self.coordinator.data is None:
            return None
        try:
            return SITUATION_TO_OPTION[
                TemperatureMode(self.coordinator.data.run_state.temperature_mode)
            ]
        except (KeyError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Switch to the manual mode with the selected situation."""
        if option == self.current_option and (
            self.coordinator.data is not None
            and self.coordinator.data.run_state.run_mode == RunMode.MANUAL
        ):
            return
        if option not in SELECTABLE_SITUATIONS:
            raise ServiceValidationError(
                "Only the situations at home and away can be selected"
            )
        await self.coordinator.async_set_situation(OPTION_TO_SITUATION[option])


class ConnectBoxSituationLevel(ConnectBoxDeviceEntity, SelectEntity):
    """Configured ventilation level of the unit's room for one situation."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device_id: int, mode: TemperatureMode) -> None:
        super().__init__(coordinator, device_id)
        self._mode = int(mode)
        self._attr_translation_key = SITUATIONS[mode]
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = (
            f"{gateway_uuid}_{device_id}_situation_level_{mode.name.lower()}"
        )

    @property
    def options(self) -> list[str]:
        """Offer standby and Auto only where the unit supports them."""
        options = ["level_1", "level_2", "level_3", "level_4"]
        data = self.device_data
        if data is None:
            return options
        room, device = data
        if device.level_zero_supported:
            options.insert(0, "standby")
        if supports_sensor_mode(room, device):
            options.append("auto")
        current = self.current_option
        if current is not None and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        """Return the configured level of this situation."""
        data = self.device_data
        if data is None:
            return None
        room, _device = data
        level = next(
            (
                value.level
                for value in room.ventilation
                if value.temperature_mode == self._mode
            ),
            None,
        )
        return LEVEL_TO_OPTION.get(level) if level is not None else None

    async def async_select_option(self, option: str) -> None:
        """Write the configured level of this situation."""
        data = self.device_data
        if data is None:
            return
        await self.coordinator.async_set_level(
            data[0].room_id, OPTION_TO_LEVEL[option], self._mode
        )
