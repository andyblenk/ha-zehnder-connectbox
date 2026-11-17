"""Status and problem indicators for supported ConnectBox ventilation units."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import ConnectBoxDeviceEntity, supported_device_ids
from .profiles import (
    CO2_SENSOR_STATUS,
    EXHAUST_FAN_ENABLED,
    EXTRACT_AIR_SENSOR_STATUS,
    HUMIDITY_SENSOR_STATUS,
    INCOMING_AIR_SENSOR_STATUS,
    PropertySpec,
    has_fault,
    has_filter_warning,
    sensor_available,
    supply_only_operation,
    supports_sensor_status,
)


@dataclass(frozen=True, kw_only=True)
class ConnectBoxBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a device status indicator."""

    kind: str
    status_spec: PropertySpec | None = None


BINARY_SENSORS = (
    ConnectBoxBinarySensorDescription(
        key="supply_only",
        translation_key="supply_only",
        kind="supply_only",
        status_spec=EXHAUST_FAN_ENABLED,
    ),
    ConnectBoxBinarySensorDescription(
        key="filter_warning",
        translation_key="filter_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="filter",
    ),
    ConnectBoxBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="fault",
    ),
    ConnectBoxBinarySensorDescription(
        key="extract_air_sensor_available",
        translation_key="extract_air_sensor_available",
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="sensor_availability",
        status_spec=EXTRACT_AIR_SENSOR_STATUS,
    ),
    ConnectBoxBinarySensorDescription(
        key="incoming_air_sensor_available",
        translation_key="incoming_air_sensor_available",
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="sensor_availability",
        status_spec=INCOMING_AIR_SENSOR_STATUS,
    ),
    ConnectBoxBinarySensorDescription(
        key="humidity_sensor_available",
        translation_key="humidity_sensor_available",
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="sensor_availability",
        status_spec=HUMIDITY_SENSOR_STATUS,
    ),
    ConnectBoxBinarySensorDescription(
        key="co2_sensor_available",
        translation_key="co2_sensor_available",
        entity_category=EntityCategory.DIAGNOSTIC,
        kind="sensor_availability",
        status_spec=CO2_SENSOR_STATUS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors and add attached devices dynamically."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new_entities() -> None:
        new_ids = supported_device_ids(coordinator) - known
        if new_ids:
            status_ids = {
                device.device_id
                for room in coordinator.data.rooms
                for device in room.devices
                if supports_sensor_status(device)
            }
            async_add_entities(
                ConnectBoxBinarySensor(coordinator, device_id, description)
                for device_id in sorted(new_ids)
                for description in BINARY_SENSORS
                if description.status_spec is None or device_id in status_ids
            )
            known.update(new_ids)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxBinarySensor(ConnectBoxDeviceEntity, BinarySensorEntity):
    """One device diagnostic indicator."""

    entity_description: ConnectBoxBinarySensorDescription

    def __init__(self, coordinator, device_id: int, description) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the operating, problem, or sensor-availability state."""
        data = self.device_data
        if data is None:
            return None
        device = data[1]
        if self.entity_description.kind == "supply_only":
            return supply_only_operation(device)
        if self.entity_description.status_spec is not None:
            return sensor_available(device, self.entity_description.status_spec)
        if self.entity_description.kind == "filter":
            return has_filter_warning(device)
        return has_fault(device)

    @property
    def available(self) -> bool:
        """Keep missing or unrecognized status values unavailable."""
        if not super().available:
            return False
        if (
            self.entity_description.kind == "filter"
            or self.entity_description.status_spec is not None
        ):
            return self.is_on is not None
        return True
