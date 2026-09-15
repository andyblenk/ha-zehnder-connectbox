"""Problem indicators for supported ConnectBox ventilation units."""

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
from .profiles import has_fault


@dataclass(frozen=True, kw_only=True)
class ConnectBoxBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a problem indicator."""

    kind: str


BINARY_SENSORS = (
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
            async_add_entities(
                ConnectBoxBinarySensor(coordinator, device_id, description)
                for device_id in sorted(new_ids)
                for description in BINARY_SENSORS
            )
            known.update(new_ids)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxBinarySensor(ConnectBoxDeviceEntity, BinarySensorEntity):
    """One device problem indicator."""

    entity_description: ConnectBoxBinarySensorDescription

    def __init__(self, coordinator, device_id: int, description) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the problem state."""
        data = self.device_data
        if data is None:
            return None
        device = data[1]
        if self.entity_description.kind == "filter":
            return device.filter_warning
        return has_fault(device)

    @property
    def available(self) -> bool:
        """Mark optional filter state unavailable when it is absent."""
        if not super().available:
            return False
        if self.entity_description.kind == "filter":
            return self.is_on is not None
        return True
