"""Start and end of the absence planner's period."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxGatewayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the period entities once the gateway reports the planner."""
    coordinator = entry.runtime_data
    added = False

    @callback
    def add_when_supported() -> None:
        nonlocal added
        if (
            added
            or coordinator.data is None
            or coordinator.data.run_state.standby_mode is None
        ):
            return
        async_add_entities(
            [
                ConnectBoxAbsenceTime(coordinator, end=False),
                ConnectBoxAbsenceTime(coordinator, end=True),
            ]
        )
        added = True

    add_when_supported()
    entry.async_on_unload(coordinator.async_add_listener(add_when_supported))


class ConnectBoxAbsenceTime(ConnectBoxGatewayEntity, DateTimeEntity):
    """Start or end of the absence planner's period.

    While the planner is on, a change is written right away. While it is off,
    the time is kept until the planner is switched on, like in the app, where
    the period is saved together with the planner.
    """

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, *, end: bool) -> None:
        super().__init__(coordinator)
        self._end = end
        self._attr_translation_key = "absence_end" if end else "absence_start"
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_absence_{'end' if end else 'start'}"

    @property
    def native_value(self) -> datetime | None:
        """Return the saved or chosen time."""
        timestamp = self.coordinator.absence_period()[1 if self._end else 0]
        if not timestamp:
            return None
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            return None

    async def async_set_value(self, value: datetime) -> None:
        """Set the time, in whole minutes as the app offers it."""
        timestamp = int(value.timestamp()) // 60 * 60
        if self._end:
            await self.coordinator.async_set_absence_period(end=timestamp)
        else:
            await self.coordinator.async_set_absence_period(start=timestamp)
