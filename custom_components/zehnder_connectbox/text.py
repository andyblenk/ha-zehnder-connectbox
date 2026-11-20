"""Names of the system and of its rooms, as set in the app."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID, DOMAIN
from .entity import (
    ConnectBoxDeviceEntity,
    ConnectBoxGatewayEntity,
    supported_device_ids,
)
from .protocol import ROOM_NAME_MAX_BYTES, SITE_NAME_MAX_BYTES


def _checked_name(value: str, max_bytes: int) -> str:
    """Return the trimmed name, or refuse an empty or too long one."""
    name = value.strip()
    if not name or len(name.encode()) > max_bytes:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_name",
            translation_placeholders={"max_bytes": str(max_bytes)},
        )
    return name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the system name once reported and a room name per unit."""
    coordinator = entry.runtime_data
    site_added = False
    known: set[int] = set()

    @callback
    def add_new_entities() -> None:
        nonlocal site_added
        if coordinator.data is None:
            return
        new_entities: list[TextEntity] = []
        if not site_added and coordinator.data.site is not None:
            new_entities.append(ConnectBoxSiteName(coordinator))
            site_added = True
        for device_id in sorted(supported_device_ids(coordinator) - known):
            new_entities.append(ConnectBoxRoomName(coordinator, device_id))
            known.add(device_id)
        if new_entities:
            async_add_entities(new_entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxSiteName(ConnectBoxGatewayEntity, TextEntity):
    """Name of the system, as on the app's system name and language page."""

    _attr_translation_key = "site_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:home-edit"
    _attr_native_min = 1
    _attr_native_max = SITE_NAME_MAX_BYTES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_site_name"

    @property
    def native_value(self) -> str | None:
        """Return the reported name."""
        snapshot = self.coordinator.data
        site = snapshot.site if snapshot is not None else None
        return site.name if site is not None else None

    @property
    def available(self) -> bool:
        """Only allow a change while the gateway reports the site."""
        snapshot = self.coordinator.data
        return super().available and snapshot is not None and snapshot.site is not None

    async def async_set_value(self, value: str) -> None:
        """Write the name and read it back."""
        name = _checked_name(value, SITE_NAME_MAX_BYTES)
        if name != self.native_value:
            await self.coordinator.async_set_site_info(name=name)


class ConnectBoxRoomName(ConnectBoxDeviceEntity, TextEntity):
    """Name of the unit's room, as on the app's room page."""

    _attr_translation_key = "room_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename"
    _attr_native_min = 1
    _attr_native_max = ROOM_NAME_MAX_BYTES

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_room_name"

    @property
    def native_value(self) -> str | None:
        """Return the reported name."""
        data = self.device_data
        return data[0].name if data is not None else None

    async def async_set_value(self, value: str) -> None:
        """Write the name and read it back."""
        name = _checked_name(value, ROOM_NAME_MAX_BYTES)
        data = self.device_data
        if data is not None and name != data[0].name:
            await self.coordinator.async_set_room_settings(data[0].room_id, name=name)
