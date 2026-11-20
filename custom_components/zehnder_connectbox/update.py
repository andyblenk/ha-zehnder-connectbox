"""Firmware update of the Zehnder system, as on the app's update page."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID, DOMAIN
from .entity import ConnectBoxGatewayEntity
from .firmware import (
    FIRMWARE_DIRECTORY,
    FirmwareError,
    FirmwarePackage,
    Version,
    find_firmware,
    format_version_tuple,
    read_firmware,
)

FIRMWARE_SCAN_INTERVAL = timedelta(minutes=10)


def system_version(packed: int | None) -> Version | None:
    """Return the system version the gateway reports, as the app shows it."""
    if packed is None:
        return None
    return ((packed >> 20) & 0x3FF, (packed >> 10) & 0x3FF, packed & 0x3FF)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the firmware update once the gateway reports its version."""
    coordinator = entry.runtime_data
    added = False

    @callback
    def add_when_reported() -> None:
        nonlocal added
        snapshot = coordinator.data
        if added or snapshot is None or snapshot.version.system_version is None:
            return
        async_add_entities([ConnectBoxFirmwareUpdate(coordinator)])
        added = True

    add_when_reported()
    entry.async_on_unload(coordinator.async_add_listener(add_when_reported))


class ConnectBoxFirmwareUpdate(ConnectBoxGatewayEntity, UpdateEntity):
    """Install a firmware package of the official app on the system.

    The official app ships the package of the whole system (ConnectBox and
    units), such as Gateway-1.8.1.fw, and offers it when it is newer than
    the installed system version. Home Assistant cannot download it: copy
    the file from the app into the folder zehnder_connectbox of the
    configuration. The newest valid package found there is offered.
    """

    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_firmware"
        self._package: FirmwarePackage | None = None
        self._rejected: list[str] = []
        self._uploading = False
        self._upload_percentage: int | None = None

    @property
    def _directory(self) -> Path:
        return Path(self.hass.config.path(FIRMWARE_DIRECTORY))

    async def async_added_to_hass(self) -> None:
        """Look for packages now and every few minutes."""
        await super().async_added_to_hass()
        await self._async_scan()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_scan_and_write, FIRMWARE_SCAN_INTERVAL
            )
        )

    async def async_update(self) -> None:
        """Look for packages again when an update of the entity is requested."""
        await super().async_update()
        await self._async_scan()

    async def _async_scan_and_write(self, _now=None) -> None:
        await self._async_scan()
        self.async_write_ha_state()

    async def _async_scan(self) -> None:
        package, rejected = await self.hass.async_add_executor_job(
            find_firmware, self._directory
        )
        # Keep only the version; the file is read again for an install.
        self._package = (
            FirmwarePackage(package.path, package.version, b"")
            if package is not None
            else None
        )
        self._rejected = rejected

    @property
    def _installed(self) -> Version | None:
        snapshot = self.coordinator.data
        return system_version(snapshot.version.system_version) if snapshot else None

    @property
    def installed_version(self) -> str | None:
        """Return the installed system version."""
        installed = self._installed
        return format_version_tuple(installed) if installed else None

    @property
    def latest_version(self) -> str | None:
        """Return a newer package's version, else the installed version."""
        installed = self._installed
        if installed is None:
            return None
        if self._package is not None and self._package.version > installed:
            return self._package.version_text
        return format_version_tuple(installed)

    @property
    def release_summary(self) -> str | None:
        """Say where packages are looked for and what was left out."""
        lines: list[str] = []
        if self._package is None:
            lines.append(
                f"Copy the firmware package of the official app (Gateway-x.y.z.fw) "
                f"into the folder {FIRMWARE_DIRECTORY} of the configuration."
            )
        else:
            lines.append(f"Package found: {self._package.path.name}")
        lines.extend(f"Left out: {item}" for item in self._rejected)
        return "\n".join(lines)[:255]

    @property
    def in_progress(self) -> bool:
        """Report the upload, then the update of the units the gateway runs."""
        if self._uploading:
            return True
        snapshot = self.coordinator.data
        progress = snapshot.update_progress if snapshot else None
        return bool(
            progress is not None
            and (progress.updating_device_id or progress.queued_device_ids)
        )

    @property
    def update_percentage(self) -> int | None:
        """Return the upload's progress, then the progress of the unit update."""
        if self._uploading:
            return self._upload_percentage
        snapshot = self.coordinator.data
        progress = snapshot.update_progress if snapshot else None
        if progress is not None and progress.updating_device_id:
            return progress.progress
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Show the found package's file name."""
        return {
            "package": self._package.path.name if self._package else None,
            "folder": FIRMWARE_DIRECTORY,
        }

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Upload the newest package found, if it is newer than the installed."""
        if self._uploading:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_update_running"
            )
        installed = self._installed
        found = self._package
        if found is None or installed is None or found.version <= installed:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_not_newer"
            )
        # Read and check the file again; it may have changed since the scan.
        try:
            package = await self.hass.async_add_executor_job(read_firmware, found.path)
        except FirmwareError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_invalid"
            ) from err
        if package.version != found.version:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_invalid"
            )

        @callback
        def progress(done: int, total: int) -> None:
            percentage = done * 100 // total
            if percentage != self._upload_percentage:
                self._upload_percentage = percentage
                self.async_write_ha_state()

        self._uploading = True
        self._upload_percentage = 0
        self.async_write_ha_state()
        try:
            await self.coordinator.async_install_firmware(package, progress)
        finally:
            self._uploading = False
            self._upload_percentage = None
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
