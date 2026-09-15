"""Base entities for Zehnder ConnectBox."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GATEWAY_UUID, DOMAIN
from .coordinator import ZehnderConnectBoxCoordinator
from .models import AttachedDevice, Room
from .profiles import is_supported, product_name


class ConnectBoxGatewayEntity(CoordinatorEntity[ZehnderConnectBoxCoordinator]):
    """Base class for a gateway-wide entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZehnderConnectBoxCoordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, gateway_uuid)})


class ConnectBoxDeviceEntity(CoordinatorEntity[ZehnderConnectBoxCoordinator]):
    """Base class for an attached ventilation-device entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ZehnderConnectBoxCoordinator, device_id: int
    ) -> None:
        super().__init__(coordinator)
        self.device_id = device_id
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{gateway_uuid}:{device_id}")},
            via_device=(DOMAIN, gateway_uuid),
        )

    @property
    def device_data(self) -> tuple[Room, AttachedDevice] | None:
        """Return the latest room/device pair."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.find_device(self.device_id)

    @property
    def available(self) -> bool:
        """Report unavailable when this device is absent from the latest model."""
        return super().available and self.device_data is not None


def supported_device_ids(coordinator: ZehnderConnectBoxCoordinator) -> set[int]:
    """Return supported attached device IDs in the current snapshot."""
    if coordinator.data is None:
        return set()
    return {
        device.device_id
        for room in coordinator.data.rooms
        for device in room.devices
        if is_supported(device)
    }


def device_model(device: AttachedDevice) -> str:
    """Expose a stable helper for entity debugging and naming."""
    return product_name(device)
