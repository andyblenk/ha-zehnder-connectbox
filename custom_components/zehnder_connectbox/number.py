"""Gateway-wide numeric configuration for Zehnder ConnectBox."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID, DOMAIN
from .entity import ConnectBoxDeviceEntity, ConnectBoxGatewayEntity
from .profiles import (
    BOOST_DURATION_MAXIMUM,
    BOOST_DURATION_MINIMUM,
    BOOST_DURATION_STEP,
    LEVEL_ONE_RUNTIME_VALUES,
    level_one_runtime,
    level_one_runtime_key,
    supports_boost,
    supports_summer_ventilation,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the summer duration once the gateway reports the setting."""
    coordinator = entry.runtime_data
    duration_added = False

    @callback
    def add_duration_when_supported() -> None:
        nonlocal duration_added
        snapshot = coordinator.data
        if (
            duration_added
            or snapshot is None
            or snapshot.summer_ventilation_settings is None
            or not supports_summer_ventilation(snapshot.rooms)
        ):
            return
        async_add_entities([ConnectBoxSummerVentilationDuration(coordinator)])
        duration_added = True

    add_duration_when_supported()
    entry.async_on_unload(coordinator.async_add_listener(add_duration_when_supported))

    # Boost duration of each unit's room.
    known_boost: set[int] = set()

    @callback
    def add_boost_durations() -> None:
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
                ConnectBoxBoostDuration(coordinator, device_id)
                for device_id in sorted(new_ids)
            )
            known_boost.update(new_ids)

    add_boost_durations()
    entry.async_on_unload(coordinator.async_add_listener(add_boost_durations))

    # Runtime of level 1 per hour of each unit that reports it.
    known_runtime: set[int] = set()

    @callback
    def add_level_one_runtimes() -> None:
        if coordinator.data is None:
            return
        new_ids = {
            device.device_id
            for room in coordinator.data.rooms
            for device in room.devices
            if level_one_runtime_key(device) is not None
        } - known_runtime
        if new_ids:
            async_add_entities(
                ConnectBoxLevelOneRuntime(coordinator, device_id)
                for device_id in sorted(new_ids)
            )
            known_runtime.update(new_ids)

    add_level_one_runtimes()
    entry.async_on_unload(coordinator.async_add_listener(add_level_one_runtimes))


class ConnectBoxSummerVentilationDuration(ConnectBoxGatewayEntity, NumberEntity):
    """Set the configured duration of a future summer interval in hours."""

    _attr_translation_key = "summer_ventilation_duration"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = 1
    _attr_native_max_value = 24
    _attr_native_step = 1

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_summer_ventilation_duration"

    @property
    def native_value(self) -> float | None:
        """Return the duration reported by the gateway."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return settings.duration_hours if settings is not None else None

    @property
    def available(self) -> bool:
        """Allow changes while idle even if the function is disabled."""
        snapshot = self.coordinator.data
        settings = snapshot.summer_ventilation_settings if snapshot else None
        return bool(
            super().available
            and snapshot is not None
            and supports_summer_ventilation(snapshot.rooms)
            and settings is not None
            and settings.duration_hours is not None
            and 1 <= settings.duration_hours <= 24
            and snapshot.run_state.summer_ventilation is False
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set whole hours while retaining the current function setting."""
        if not 1 <= value <= 24 or not float(value).is_integer():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_summer_duration"
            )
        await self.coordinator.async_set_summer_configuration(duration_hours=int(value))


class ConnectBoxBoostDuration(ConnectBoxDeviceEntity, NumberEntity):
    """Duration of a boost in the unit's room, as set in the app.

    The app offers 15 to 120 minutes per room. The room is written back as
    read with only its boost duration changed.
    """

    _attr_translation_key = "boost_duration"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = BOOST_DURATION_MINIMUM
    _attr_native_max_value = BOOST_DURATION_MAXIMUM
    _attr_native_step = BOOST_DURATION_STEP
    _attr_icon = "mdi:fan-clock"

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_boost_duration"

    @property
    def native_value(self) -> float | None:
        """Return the duration reported for the room."""
        data = self.device_data
        return data[0].boost_duration if data is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the room reports its duration."""
        if not super().available:
            return False
        data = self.device_data
        return data is not None and supports_boost(*data)

    async def async_set_native_value(self, value: float) -> None:
        """Write the duration in the app's steps of 15 minutes."""
        if (
            not BOOST_DURATION_MINIMUM <= value <= BOOST_DURATION_MAXIMUM
            or not float(value).is_integer()
            or int(value) % BOOST_DURATION_STEP
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_boost_duration"
            )
        data = self.device_data
        if data is None:
            return
        await self.coordinator.async_set_boost_duration(data[0].room_id, int(value))


class ConnectBoxLevelOneRuntime(ConnectBoxDeviceEntity, NumberEntity):
    """Runtime of level 1 per hour, as set on the unit's page in the app."""

    _attr_translation_key = "level_one_runtime"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = LEVEL_ONE_RUNTIME_VALUES[0]
    _attr_native_max_value = LEVEL_ONE_RUNTIME_VALUES[-1]
    _attr_native_step = LEVEL_ONE_RUNTIME_VALUES[1] - LEVEL_ONE_RUNTIME_VALUES[0]
    _attr_icon = "mdi:fan-clock"

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_level_one_runtime"

    @property
    def native_value(self) -> float | None:
        """Return the runtime the unit reports."""
        data = self.device_data
        return level_one_runtime(data[1]) if data is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the unit reports the runtime."""
        if not super().available:
            return False
        data = self.device_data
        return data is not None and level_one_runtime_key(data[1]) is not None

    async def async_set_native_value(self, value: float) -> None:
        """Write one of the app's values (15, 30, 45 or 60 minutes)."""
        if not float(value).is_integer() or int(value) not in LEVEL_ONE_RUNTIME_VALUES:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_level_one_runtime"
            )
        if int(value) == self.native_value:
            return
        await self.coordinator.async_set_level_one_runtime(self.device_id, int(value))
