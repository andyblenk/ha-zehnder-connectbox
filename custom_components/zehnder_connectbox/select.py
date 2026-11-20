"""Operating-mode, situation and per-situation level selects."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID, DOMAIN, SENSOR_MODE_LEVEL
from .entity import ConnectBoxDeviceEntity, ConnectBoxGatewayEntity
from .models import RunMode, Schedules, TemperatureMode
from .profiles import (
    SUMMER_VENTILATION_ROLE_VALUES,
    is_supported,
    summer_ventilation_role,
    summer_ventilation_role_key,
    supports_sensor_mode,
)

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
    known_roles: set[int] = set()
    known_room_types: set[int] = set()
    week_plan_added = False
    language_added = False

    @callback
    def add_new_entities() -> None:
        nonlocal week_plan_added, language_added
        if coordinator.data is None:
            return
        new_entities: list[SelectEntity] = []
        if not week_plan_added and coordinator.data.schedules is not None:
            new_entities.append(ConnectBoxActiveWeekPlan(coordinator))
            week_plan_added = True
        site = coordinator.data.site
        if not language_added and site is not None and site.language is not None:
            new_entities.append(ConnectBoxSystemLanguage(coordinator))
            language_added = True
        for room in coordinator.data.rooms:
            modes = {value.temperature_mode for value in room.ventilation}
            for device in room.devices:
                if not is_supported(device):
                    continue
                if (
                    device.device_id not in known_room_types
                    and room.room_type is not None
                ):
                    new_entities.append(
                        ConnectBoxRoomType(coordinator, device.device_id)
                    )
                    known_room_types.add(device.device_id)
                if (
                    device.device_id not in known_roles
                    and summer_ventilation_role_key(device) is not None
                ):
                    new_entities.append(
                        ConnectBoxSummerVentilationRole(coordinator, device.device_id)
                    )
                    known_roles.add(device.device_id)
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
                translation_domain=DOMAIN, translation_key="situation_not_selectable"
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


class ConnectBoxSummerVentilationRole(ConnectBoxDeviceEntity, SelectEntity):
    """Role of the unit in the summer ventilation, as set in the app.

    The app offers supply and exhaust (the unit takes no part), supply, or
    exhaust on the unit's settings page. The role cannot be changed while a
    summer ventilation interval runs.
    """

    _attr_translation_key = "summer_ventilation_role"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(SUMMER_VENTILATION_ROLE_VALUES)

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_summer_ventilation_role"

    @property
    def current_option(self) -> str | None:
        """Return the role reported by the unit."""
        data = self.device_data
        return summer_ventilation_role(data[1]) if data is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the unit reports its role."""
        if not super().available:
            return False
        data = self.device_data
        return data is not None and summer_ventilation_role_key(data[1]) is not None

    async def async_select_option(self, option: str) -> None:
        """Write the role and read it back from the unit."""
        if option == self.current_option:
            return
        await self.coordinator.async_set_summer_ventilation_role(
            self.device_id, SUMMER_VENTILATION_ROLE_VALUES[option]
        )


def week_plan_options(schedules: Schedules) -> dict[str, int]:
    """Name each weekly schedule as in the app; add the ID where needed.

    A schedule without a name, or with a name another one shares, is shown
    with its ID so every option stays unique.
    """
    names = [plan.name for plan in schedules.week_plans]
    options: dict[str, int] = {}
    for plan in schedules.week_plans:
        if plan.name and names.count(plan.name) == 1:
            options[plan.name] = plan.plan_id
        else:
            options[f"{plan.name or ''} ({plan.plan_id})".strip()] = plan.plan_id
    return options


class ConnectBoxActiveWeekPlan(ConnectBoxGatewayEntity, SelectEntity):
    """Select the active weekly schedule, as on the app's plan page.

    The options are the weekly schedules stored on the gateway. The gateway
    confirmed the change and reported the new schedule and today's daily
    schedule in its run state.
    """

    _attr_translation_key = "active_week_plan"
    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_active_week_plan"

    def _options(self) -> dict[str, int]:
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.schedules is None:
            return {}
        return week_plan_options(snapshot.schedules)

    @property
    def options(self) -> list[str]:
        """Return the stored weekly schedules."""
        return list(self._options())

    @property
    def current_option(self) -> str | None:
        """Return the active weekly schedule."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        active = snapshot.run_state.week_plan
        return next(
            (name for name, plan_id in self._options().items() if plan_id == active),
            None,
        )

    @property
    def available(self) -> bool:
        """Only offer a change while the schedule list is known."""
        return super().available and bool(self._options())

    async def async_select_option(self, option: str) -> None:
        """Activate the selected weekly schedule."""
        plan_id = self._options().get(option)
        if plan_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="plan_unknown"
            )
        if option == self.current_option:
            return
        await self.coordinator.async_set_active_week_plan(plan_id)


# Languages of the app's enumeration. The app's system name and language
# page offers the first four; the others are shown when the gateway reports
# them.
LANGUAGES = {
    2: "german",
    1: "english",
    3: "french",
    4: "dutch",
    5: "spanish",
    6: "portuguese",
    7: "italian",
}
SELECTABLE_LANGUAGES = ("german", "english", "french", "dutch")
LANGUAGE_VALUES = {option: value for value, option in LANGUAGES.items()}


class ConnectBoxSystemLanguage(ConnectBoxGatewayEntity, SelectEntity):
    """System language, as on the app's system name and language page."""

    _attr_translation_key = "system_language"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:translate"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_system_language"

    @property
    def options(self) -> list[str]:
        """Offer the app's languages and the reported one."""
        options = list(SELECTABLE_LANGUAGES)
        current = self.current_option
        if current is not None and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        """Return the reported language."""
        snapshot = self.coordinator.data
        site = snapshot.site if snapshot is not None else None
        return LANGUAGES.get(site.language) if site is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the gateway reports a known language."""
        return super().available and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        """Write the language and read it back."""
        if option == self.current_option:
            return
        if option not in SELECTABLE_LANGUAGES:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="language_not_selectable"
            )
        await self.coordinator.async_set_site_info(language=LANGUAGE_VALUES[option])


# Room types of the app's enumeration, as on its room page. Type 1 (device
# pool) is not a room.
ROOM_TYPES = {
    2: "living_room",
    3: "kitchen",
    4: "bathroom",
    5: "toilet",
    6: "bedroom",
    7: "dining_room",
    8: "office",
    9: "other",
}
ROOM_TYPE_VALUES = {option: value for value, option in ROOM_TYPES.items()}


class ConnectBoxRoomType(ConnectBoxDeviceEntity, SelectEntity):
    """Type of the unit's room, as on the app's room page."""

    _attr_translation_key = "room_type"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:floor-plan"
    _attr_options: ClassVar[list[str]] = list(ROOM_TYPE_VALUES)

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_room_type"

    @property
    def current_option(self) -> str | None:
        """Return the reported room type."""
        data = self.device_data
        return ROOM_TYPES.get(data[0].room_type) if data is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the room reports a known type."""
        return super().available and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        """Write the room type and read it back."""
        data = self.device_data
        if data is None or option == self.current_option:
            return
        await self.coordinator.async_set_room_settings(
            data[0].room_id, room_type=ROOM_TYPE_VALUES[option]
        )
