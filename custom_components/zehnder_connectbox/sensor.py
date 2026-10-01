"""Read-only telemetry for supported ConnectBox ventilation units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
from .models import AttachedDevice
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
    SUPPLY_FAN_SPEED,
    board_reading,
    temperature_value,
)


@dataclass(frozen=True, kw_only=True)
class ConnectBoxSensorDescription(SensorEntityDescription):
    """Describe how a device value is obtained."""

    value_fn: Callable[[AttachedDevice], int | float | None]
    # Create optional entities only once the unit reports a usable value.
    exists_fn: Callable[[AttachedDevice], bool] | None = None


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
    def native_value(self) -> int | float | None:
        """Return the decoded telemetry value."""
        data = self.device_data
        return self.entity_description.value_fn(data[1]) if data else None

    @property
    def available(self) -> bool:
        """Hide stale values when optional telemetry is not reported."""
        return super().available and self.native_value is not None
