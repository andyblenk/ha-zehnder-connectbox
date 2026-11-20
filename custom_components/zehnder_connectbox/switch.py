"""Gateway-wide controls for Zehnder ConnectBox."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxDeviceEntity, ConnectBoxGatewayEntity
from .models import GatewaySnapshot, RunMode, TemperatureMode
from .profiles import (
    supports_boost,
    supports_summer_ventilation,
)


def _supports_summer_ventilation(snapshot: GatewaySnapshot) -> bool:
    """Require a gateway setting and an explicitly capable supported unit."""
    return (
        snapshot.summer_ventilation_settings is not None
        and supports_summer_ventilation(snapshot.rooms)
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up standby and any reported summer ventilation control."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ConnectBoxVentilationSwitch(coordinator),
            ConnectBoxPresenceSwitch(coordinator),
        ]
    )
    summer_added = False

    @callback
    def add_summer_when_supported() -> None:
        nonlocal summer_added
        if (
            summer_added
            or coordinator.data is None
            or not _supports_summer_ventilation(coordinator.data)
        ):
            return
        async_add_entities(
            [
                ConnectBoxSummerVentilationSwitch(coordinator),
                ConnectBoxSummerVentilationEnabledSwitch(coordinator),
            ]
        )
        summer_added = True

    add_summer_when_supported()
    entry.async_on_unload(coordinator.async_add_listener(add_summer_when_supported))

    known_boost: set[int] = set()

    @callback
    def add_boost_switches() -> None:
        if coordinator.data is None:
            return
        new_ids = {
            device.device_id
            for room in coordinator.data.rooms
            for device in room.devices
            if supports_boost(room, device)
        } - known_boost
        if new_ids:
            async_add_entities(
                ConnectBoxBoostSwitch(coordinator, device_id)
                for device_id in sorted(new_ids)
            )
            known_boost.update(new_ids)

    add_boost_switches()
    entry.async_on_unload(coordinator.async_add_listener(add_boost_switches))

    absence_added = False

    @callback
    def add_absence_switch() -> None:
        nonlocal absence_added
        if (
            absence_added
            or coordinator.data is None
            or coordinator.data.run_state.standby_mode is None
        ):
            return
        async_add_entities([ConnectBoxAbsencePlannerSwitch(coordinator)])
        absence_added = True

    add_absence_switch()
    entry.async_on_unload(coordinator.async_add_listener(add_absence_switch))


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


# Run-state field 3 from the app's enumeration.
SITUATION_SOURCES = {
    1: "schedule",
    2: "user",
    3: "no_schedule",
    4: "presence_home",
}


class ConnectBoxPresenceSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """At home or away while the schedule runs, like the house on the app's
    start screen.

    On is at home: the schedule's situations apply. Off is away until the
    switch is turned on again or the schedule switches; the operating mode
    stays automatic, unlike selecting a situation, which switches to manual.
    """

    _attr_translation_key = "presence"
    _attr_icon = "mdi:home-account"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_presence"

    @property
    def is_on(self) -> bool | None:
        """Return whether the system is at home."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.run_state.temperature_mode != TemperatureMode.AWAY

    @property
    def available(self) -> bool:
        """Only offer the switch while the schedule runs, as in the app."""
        snapshot = self.coordinator.data
        return bool(
            super().available
            and snapshot is not None
            and snapshot.run_state.run_mode == RunMode.AUTOMATIC
            and snapshot.run_state.temperature_mode
            in (TemperatureMode.AWAKE, TemperatureMode.ASLEEP, TemperatureMode.AWAY)
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Show why the current situation applies."""
        snapshot = self.coordinator.data
        source = snapshot.run_state.situation_source if snapshot else None
        return {"source": SITUATION_SOURCES.get(source, source) if source else None}

    async def async_turn_on(self, **kwargs) -> None:
        """Return to at home."""
        await self.coordinator.async_set_presence(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Switch to away while the schedule keeps running."""
        await self.coordinator.async_set_presence(False)


class ConnectBoxSummerVentilationSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Start or stop the app-configured summer interval for the gateway."""

    _attr_translation_key = "summer_ventilation"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_summer_ventilation"

    @property
    def is_on(self) -> bool | None:
        """Follow the gateway's active flag so a running interval remains stoppable."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.run_state.summer_ventilation

    @property
    def available(self) -> bool:
        """Keep a running interval stoppable after the app disables the feature."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return bool(
            super().available
            and snapshot is not None
            and _supports_summer_ventilation(snapshot)
            and snapshot.run_state.run_mode in (RunMode.AUTOMATIC, RunMode.MANUAL)
            and snapshot.run_state.temperature_mode in (0, 1, 2)
            and self.is_on is not None
            and (
                snapshot.run_state.summer_ventilation is True
                or (
                    settings is not None
                    and settings.enabled
                    and settings.duration_hours is not None
                    and 1 <= settings.duration_hours <= 24
                )
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Show the configured duration and reported end of an interval."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        settings = snapshot.summer_ventilation_settings
        end = snapshot.run_state.summer_ventilation_end
        try:
            ends_at = datetime.fromtimestamp(end, UTC).isoformat() if end else None
        except (OverflowError, OSError, ValueError):
            ends_at = None
        return {
            "duration_hours": settings.duration_hours if settings else None,
            "ends_at": ends_at,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Start summer ventilation for the configured duration."""
        await self.coordinator.async_set_summer_ventilation(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the active summer ventilation interval."""
        await self.coordinator.async_set_summer_ventilation(False)


class ConnectBoxSummerVentilationEnabledSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """Configure whether the gateway permits summer ventilation."""

    _attr_translation_key = "summer_ventilation_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_summer_ventilation_enabled"

    @property
    def is_on(self) -> bool | None:
        """Return the gateway's function setting."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return settings.enabled if settings is not None else None

    @property
    def available(self) -> bool:
        """Avoid changing configuration during an active interval."""
        snapshot = self.coordinator.data
        return bool(
            super().available
            and snapshot is not None
            and _supports_summer_ventilation(snapshot)
            and snapshot.run_state.summer_ventilation is False
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the configured summer function."""
        await self.coordinator.async_set_summer_configuration(enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the summer function while preserving its duration."""
        await self.coordinator.async_set_summer_configuration(enabled=False)


def _iso(timestamp: int | None) -> str | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class ConnectBoxAbsencePlannerSwitch(ConnectBoxGatewayEntity, SwitchEntity):
    """The app's absence planner (Abwesenheitsplaner).

    Switched on, the gateway puts the system into frost protection for the
    period set with the absence start and end; without one, the period starts
    now and lasts one day, as in the app.
    """

    _attr_translation_key = "absence_planner"
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_absence_planner"

    @property
    def is_on(self) -> bool | None:
        """Return whether the planner is saved for a period."""
        snapshot = self.coordinator.data
        return snapshot.run_state.absence_planned if snapshot is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Show the period and whether it is running now."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        start, end = self.coordinator.absence_period()
        return {
            "starts_at": _iso(start),
            "ends_at": _iso(end),
            "active": snapshot.run_state.standby,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Save the planner for the chosen period."""
        await self.coordinator.async_set_absence(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Switch the planner off."""
        await self.coordinator.async_set_absence(False)


class ConnectBoxBoostSwitch(ConnectBoxDeviceEntity, SwitchEntity):
    """Start or stop the boost of the unit's room, like the app's Boost button.

    The state follows the end of the boost the gateway reports, so a boost
    started in the app or ended by its timer shows as well.
    """

    _attr_translation_key = "boost"
    _attr_icon = "mdi:fan-plus"

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_boost"

    @property
    def is_on(self) -> bool | None:
        """Return whether the room reports a running boost."""
        data = self.device_data
        return data[0].boost_active(time.time()) if data is not None else None

    @property
    def available(self) -> bool:
        """Only offer the boost while the room reports its duration."""
        if not super().available:
            return False
        data = self.device_data
        return data is not None and supports_boost(*data)

    @property
    def extra_state_attributes(self) -> dict[str, int | str | None]:
        """Show the room's duration and the reported end of a boost."""
        data = self.device_data
        if data is None:
            return {}
        room = data[0]
        ends_at = None
        if room.boost_until and room.boost_active(time.time()):
            try:
                ends_at = datetime.fromtimestamp(room.boost_until, UTC).isoformat()
            except (OverflowError, OSError, ValueError):
                ends_at = None
        return {"duration_minutes": room.boost_duration, "ends_at": ends_at}

    async def async_turn_on(self, **kwargs) -> None:
        """Start a boost for the room's configured duration."""
        data = self.device_data
        if data is not None:
            await self.coordinator.async_set_boost(data[0].room_id, True)

    async def async_turn_off(self, **kwargs) -> None:
        """End the room's running boost early."""
        data = self.device_data
        if data is not None:
            await self.coordinator.async_set_boost(data[0].room_id, False)
