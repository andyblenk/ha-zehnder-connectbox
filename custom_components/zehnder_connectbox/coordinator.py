"""Shared connection and state coordinator for Zehnder ConnectBox."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ConnectBoxClient
from .const import (
    CONF_GATEWAY_UUID,
    DEFAULT_NAME,
    DOMAIN,
    POLL_INTERVAL,
    PROPERTY_REFRESH_INTERVAL,
)
from .models import GatewaySnapshot, RunMode
from .profiles import format_version, product_name
from .protocol import ProtocolError
from .transport import CertificateMismatchError, TransportError

_LOGGER = logging.getLogger(__name__)
MAX_RETRY_INTERVAL = 300


class ZehnderConnectBoxCoordinator(DataUpdateCoordinator[GatewaySnapshot]):
    """Coordinate all I/O for one ConnectBox config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ConnectBoxClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title or DEFAULT_NAME,
            update_interval=POLL_INTERVAL,
            config_entry=entry,
        )
        self.entry = entry
        self.client = client
        self._last_property_refresh = 0.0
        self._failures = 0
        self._last_non_off_mode = RunMode.MANUAL
        self._io_lock = asyncio.Lock()

    async def _async_update_data(self) -> GatewaySnapshot:
        refresh_properties = (
            self.data is None
            or time.monotonic() - self._last_property_refresh
            >= PROPERTY_REFRESH_INTERVAL
        )
        try:
            async with self._io_lock:
                snapshot = await self.hass.async_add_executor_job(
                    partial(
                        self.client.read_snapshot,
                        refresh_properties=refresh_properties,
                    )
                )
        except CertificateMismatchError as err:
            self._set_failure_interval()
            raise UpdateFailed(
                "The ConnectBox certificate no longer matches the paired gateway"
            ) from err
        except (ProtocolError, TransportError, OSError) as err:
            self._set_failure_interval()
            raise UpdateFailed("Unable to communicate with the ConnectBox") from err

        self._failures = 0
        self.update_interval = POLL_INTERVAL
        if refresh_properties:
            self._last_property_refresh = time.monotonic()
        self._remember_mode(snapshot)
        self._register_devices(snapshot)
        return snapshot

    async def async_set_level(self, room_id: int, level: int) -> None:
        """Set and confirm a room ventilation level."""
        async with self._io_lock:
            snapshot = await self.hass.async_add_executor_job(
                self.client.set_level, room_id, level
            )
        self._accept_command_snapshot(snapshot)

    async def async_set_mode(self, mode: RunMode) -> None:
        """Set and confirm the system run mode."""
        async with self._io_lock:
            snapshot = await self.hass.async_add_executor_job(
                self.client.set_run_mode, mode
            )
        self._accept_command_snapshot(snapshot)

    async def async_set_power(self, enabled: bool) -> None:
        """Enter standby or restore the last verified active mode."""
        mode = self._last_non_off_mode if enabled else RunMode.OFF
        await self.async_set_mode(mode)

    async def async_close(self) -> None:
        """Close the client outside Home Assistant's event loop."""
        async with self._io_lock:
            await self.hass.async_add_executor_job(self.client.close)

    def _accept_command_snapshot(self, snapshot: GatewaySnapshot) -> None:
        self._remember_mode(snapshot)
        self._register_devices(snapshot)
        self.async_set_updated_data(snapshot)

    def _remember_mode(self, snapshot: GatewaySnapshot) -> None:
        try:
            mode = RunMode(snapshot.run_state.run_mode)
        except ValueError:
            return
        if mode is not RunMode.OFF:
            self._last_non_off_mode = mode

    def _set_failure_interval(self) -> None:
        self._failures += 1
        seconds = min(
            POLL_INTERVAL.total_seconds() * (2 ** (self._failures - 1)),
            MAX_RETRY_INTERVAL,
        )
        self.update_interval = timedelta(seconds=seconds + random.uniform(0, 2))

    def _register_devices(self, snapshot: GatewaySnapshot) -> None:
        registry = dr.async_get(self.hass)
        gateway_uuid = self.entry.data[CONF_GATEWAY_UUID]
        gateway_device = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, gateway_uuid)},
            manufacturer="Zehnder",
            model="ConnectBox CU-RF-ZMA",
            name=self.entry.title or DEFAULT_NAME,
            sw_version=format_version(snapshot.version.connectbox_version),
        )
        for room in snapshot.rooms:
            for device in room.devices:
                registry.async_get_or_create(
                    config_entry_id=self.entry.entry_id,
                    identifiers={(DOMAIN, f"{gateway_uuid}:{device.device_id}")},
                    manufacturer="Zehnder",
                    model=product_name(device),
                    name=f"{room.name} {product_name(device)}",
                    hw_version=(
                        str(device.hardware_version)
                        if device.hardware_version is not None
                        else None
                    ),
                    sw_version=format_version(device.software_version),
                    suggested_area=room.name,
                    via_device_id=gateway_device.id,
                )
