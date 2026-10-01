"""Read-only telemetry for supported ConnectBox ventilation units."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxDeviceEntity, supported_device_ids
from .models import AttachedDevice, Room
from .profiles import (
    EXHAUST_FAN_SPEED,
    EXTRACT_AIR_SENSOR_STATUS,
    EXTRACT_AIR_TEMPERATURE,
    FILTER_MAXIMUM,
    FILTER_REMAINING,
    FILTER_RUNTIME,
    INCOMING_AIR_SENSOR_STATUS,
    INCOMING_AIR_TEMPERATURE,
    SENSOR_TYPE_CO2,
    SENSOR_TYPE_HUMIDITY,
    SUMMER_VENTILATION_ROLES,
    SUPPLY_FAN_SPEED,
    board_reading,
    summer_ventilation_role,
    supports_sensor_status,
    temperature_value,
)


@dataclass(frozen=True, kw_only=True)
class ConnectBoxSensorDescription(SensorEntityDescription):
    """Describe how a device value is obtained."""

    value_fn: Callable[[AttachedDevice], int | float | str | datetime | None]
    # Create optional entities only once the unit reports a usable value.
    exists_fn: Callable[[AttachedDevice], bool] | None = None
    # Read the value from the unit's room instead of the unit itself.
    room_value_fn: Callable[[Room], int | float | str | datetime | None] | None = None
    # Keep the entity available while no value is reported (state unknown).
    available_without_value: bool = False


def _temporary_until(room: Room) -> datetime | None:
    """Return the end of a temporary change of the room, if one is active."""
    if not room.temporary_until:
        return None
    return datetime.fromtimestamp(room.temporary_until, UTC)


def _boost_until(room: Room) -> datetime | None:
    """Return the end of the room's boost while one is running."""
    if room.boost_until is None or not room.boost_active(time.time()):
        return None
    return datetime.fromtimestamp(room.boost_until, UTC)


SENSORS = (
    ConnectBoxSensorDescription(
        key="extract_air_temperature",
        translation_key="extract_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: temperature_value(
            device, EXTRACT_AIR_TEMPERATURE, EXTRACT_AIR_SENSOR_STATUS
        ),
        exists_fn=lambda device: (
            temperature_value(device, EXTRACT_AIR_TEMPERATURE, EXTRACT_AIR_SENSOR_STATUS)
            is not None
        ),
    ),
    ConnectBoxSensorDescription(
        key="incoming_air_temperature",
        translation_key="incoming_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: temperature_value(
            device, INCOMING_AIR_TEMPERATURE, INCOMING_AIR_SENSOR_STATUS
        ),
    ),
    ConnectBoxSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: board_reading(device, SENSOR_TYPE_HUMIDITY),
        exists_fn=lambda device: (
            board_reading(device, SENSOR_TYPE_HUMIDITY) is not None
        ),
    ),
    ConnectBoxSensorDescription(
        key="co2",
        translation_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: board_reading(device, SENSOR_TYPE_CO2),
        exists_fn=lambda device: board_reading(device, SENSOR_TYPE_CO2) is not None,
    ),
    ConnectBoxSensorDescription(
        key="temporary_until",
        translation_key="temporary_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: None,
        room_value_fn=_temporary_until,
        available_without_value=True,
    ),
    ConnectBoxSensorDescription(
        key="summer_ventilation_role",
        translation_key="summer_ventilation_role",
        device_class=SensorDeviceClass.ENUM,
        options=list(SUMMER_VENTILATION_ROLES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=summer_ventilation_role,
        exists_fn=supports_sensor_status,
    ),
    ConnectBoxSensorDescription(
        key="boost_until",
        translation_key="boost_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: None,
        room_value_fn=_boost_until,
        available_without_value=True,
    ),
    ConnectBoxSensorDescription(
        key="exhaust_fan_speed",
        translation_key="exhaust_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=EXHAUST_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="supply_fan_speed",
        translation_key="supply_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=SUPPLY_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="filter_runtime",
        translation_key="filter_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: (
            FILTER_RUNTIME.value(device)
            if FILTER_RUNTIME.value(device) is not None
            else device.filter_runtime
        ),
    ),
    ConnectBoxSensorDescription(
        key="filter_remaining",
        translation_key="filter_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=FILTER_REMAINING.value,
    ),
    ConnectBoxSensorDescription(
        key="filter_maximum",
        translation_key="filter_maximum",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            FILTER_MAXIMUM.value(device)
            if FILTER_MAXIMUM.value(device) is not None
            else device.filter_maximum
        ),
    ),
    ConnectBoxSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.signal_strength,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and add newly attached devices or readings dynamically."""
    coordinator = entry.runtime_data
    known: set[tuple[int, str]] = set()

    @callback
    def add_new_entities() -> None:
        if coordinator.data is None:
            return
        supported = supported_device_ids(coordinator)
        new_entities: list[ConnectBoxSensor] = []
        for room in coordinator.data.rooms:
            for device in room.devices:
                if device.device_id not in supported:
                    continue
                for description in SENSORS:
                    identity = (device.device_id, description.key)
                    if identity in known:
                        continue
                    if description.exists_fn is not None and not (
                        description.exists_fn(device)
                    ):
                        continue
                    new_entities.append(
                        ConnectBoxSensor(coordinator, device.device_id, description)
                    )
                    known.add(identity)
        if new_entities:
            async_add_entities(new_entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxSensor(ConnectBoxDeviceEntity, SensorEntity):
    """One decoded device telemetry value."""

    entity_description: ConnectBoxSensorDescription

    def __init__(self, coordinator, device_id: int, description) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_{description.key}"

    @property
    def native_value(self) -> int | float | str | datetime | None:
        """Return the decoded telemetry value."""
        data = self.device_data
        if data is None:
            return None
        room, device = data
        if self.entity_description.room_value_fn is not None:
            return self.entity_description.room_value_fn(room)
        return self.entity_description.value_fn(device)

    @property
    def available(self) -> bool:
        """Hide stale values when optional telemetry is not reported."""
        if not super().available:
            return False
        if self.entity_description.available_without_value:
            return True
        return self.native_value is not None
