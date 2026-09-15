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
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxDeviceEntity, supported_device_ids
from .models import AttachedDevice
from .profiles import (
    EXHAUST_FAN_SPEED,
    EXTRACT_AIR_TEMPERATURE,
    FILTER_MAXIMUM,
    FILTER_REMAINING,
    FILTER_RUNTIME,
    INCOMING_AIR_TEMPERATURE,
    SUPPLY_FAN_SPEED,
)


@dataclass(frozen=True, kw_only=True)
class ConnectBoxSensorDescription(SensorEntityDescription):
    """Describe how a device value is obtained."""

    value_fn: Callable[[AttachedDevice], int | float | None]


SENSORS = (
    ConnectBoxSensorDescription(
        key="extract_air_temperature",
        translation_key="extract_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=EXTRACT_AIR_TEMPERATURE.value,
    ),
    ConnectBoxSensorDescription(
        key="incoming_air_temperature",
        translation_key="incoming_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=INCOMING_AIR_TEMPERATURE.value,
    ),
    ConnectBoxSensorDescription(
        key="exhaust_fan_speed",
        translation_key="exhaust_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=EXHAUST_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="supply_fan_speed",
        translation_key="supply_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=SUPPLY_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="filter_runtime",
        translation_key="filter_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        entity_category=EntityCategory.DIAGNOSTIC,
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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and add newly attached devices dynamically."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new_entities() -> None:
        new_ids = supported_device_ids(coordinator) - known
        if new_ids:
            async_add_entities(
                ConnectBoxSensor(coordinator, device_id, description)
                for device_id in sorted(new_ids)
                for description in SENSORS
            )
            known.update(new_ids)

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
