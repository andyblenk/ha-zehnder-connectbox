"""Shared connection and state coordinator for Zehnder ConnectBox."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Callable
from datetime import timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ConnectBoxClient
from .const import (
    CONF_GATEWAY_UUID,
    DEFAULT_NAME,
    DOMAIN,
    FILTER_PROPERTY_REFRESH_INTERVAL,
    POLL_INTERVAL,
    PROPERTY_REFRESH_INTERVAL,
)
from .errors import CommandValidationError
from .models import (
    DayPlan,
    GatewaySnapshot,
    RunMode,
    ScheduledFunction,
    Schedules,
    WeekPlan,
)
from .profiles import (
    FILTER_RUNTIME,
    format_version,
    level_one_runtime_key,
    product_name,
    summer_ventilation_role_key,
)
from .protocol import ProtocolError
from .session import GatewayResponseError
from .transport import CertificateMismatchError, TransportError

_LOGGER = logging.getLogger(__name__)
_SUPPORTS_VIA_DEVICE_ID = (
    "via_device_id"
    in inspect.signature(dr.DeviceRegistry.async_get_or_create).parameters
)
MAX_RETRY_INTERVAL = 300
# The app suggests a period of one day when the absence planner is switched on.
ABSENCE_DEFAULT_DURATION = 24 * 60 * 60
MINUTES_PER_DAY = 24 * 60


def _via_gateway(gateway_uuid: str, gateway_device_id: str) -> dict[str, object]:
    """Link a unit to the ConnectBox in the way this Home Assistant supports.

    Newer versions take the device ID and deprecate the identifier tuple,
    which is removed in 2027.8; older versions only know the tuple.
    """
    if _SUPPORTS_VIA_DEVICE_ID:
        return {"via_device_id": gateway_device_id}
    return {"via_device": (DOMAIN, gateway_uuid)}


def _validate_slots(slots: tuple[tuple[int, int], ...]) -> None:
    """Require switching times within the day, one of them at midnight.

    Every daily schedule of the app starts at 00:00.
    """
    starts = [minute for minute, _mode in slots]
    if (
        not starts
        or 0 not in starts
        or len(set(starts)) != len(starts)
        or any(not 0 <= minute < MINUTES_PER_DAY for minute in starts)
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_day_plan_slots"
        )


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
        self._last_filter_property_refresh = 0.0
        self._failures = 0
        self._last_non_off_mode = RunMode.MANUAL
        self._io_lock = asyncio.Lock()
        # Absence period chosen while the planner is off, as Unix time. It is
        # written when the planner is switched on. Not stored.
        self.pending_absence_start: int | None = None
        self.pending_absence_end: int | None = None

    async def _async_update_data(self) -> GatewaySnapshot:
        now = time.monotonic()
        refresh_properties = (
            self.data is None
            or now - self._last_property_refresh >= PROPERTY_REFRESH_INTERVAL
        )
        refresh_filter_properties = (
            self.data is None
            or now - self._last_filter_property_refresh
            >= FILTER_PROPERTY_REFRESH_INTERVAL
        )
        try:
            async with self._io_lock:
                snapshot = await self.hass.async_add_executor_job(
                    partial(
                        self.client.read_snapshot,
                        refresh_properties=(
                            refresh_properties or refresh_filter_properties
                        ),
                        refresh_filter_properties=refresh_filter_properties,
                        refresh_schedules=True,
                    )
                )
        except CertificateMismatchError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="certificate_changed",
            ) from err
        except (ProtocolError, TransportError, OSError) as err:
            self._set_failure_interval()
            raise UpdateFailed("Unable to communicate with the ConnectBox") from err

        self._failures = 0
        self.update_interval = POLL_INTERVAL
        if refresh_properties or refresh_filter_properties:
            self._last_property_refresh = time.monotonic()
        if refresh_filter_properties:
            self._last_filter_property_refresh = self._last_property_refresh
        self._remember_mode(snapshot)
        self._register_devices(snapshot)
        return snapshot

    async def async_set_level(
        self, room_id: int, level: int, temperature_mode: int | None = None
    ) -> None:
        """Set and confirm a room level for the active or a given situation."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_level, room_id, level, temperature_mode)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_situation(self, temperature_mode: int) -> None:
        """Select a situation of the manual mode and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_situation, temperature_mode)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_mode(self, mode: RunMode) -> None:
        """Set and confirm the system run mode."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_run_mode, mode)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_summer_ventilation(self, enabled: bool) -> None:
        """Set and confirm the gateway-wide summer ventilation interval."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_summer_ventilation, enabled)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_summer_configuration(
        self, *, enabled: bool | None = None, duration_hours: int | None = None
    ) -> None:
        """Set and confirm the gateway-wide summer function configuration."""
        snapshot = await self._async_execute_command(
            partial(
                self.client.set_summer_configuration,
                enabled=enabled,
                duration_hours=duration_hours,
            )
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_power(self, enabled: bool) -> None:
        """Enter standby or restore the last verified active mode."""
        mode = self._last_non_off_mode if enabled else RunMode.OFF
        await self.async_set_mode(mode)

    async def async_reset_filter_timer(self, device_id: int) -> None:
        """Reset a supported unit's filter timer and refresh its telemetry."""
        data = self.data.find_device(device_id) if self.data is not None else None
        if data is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        device = data[1]
        property_value = next(
            (
                value
                for value in device.properties
                if value.key.value_identity == FILTER_RUNTIME.key
                and value.value is not None
                and len(value.value) == FILTER_RUNTIME.length
            ),
            None,
        )
        if property_value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="filter_runtime_unavailable"
            )

        snapshot = await self._async_execute_command(
            partial(self.client.reset_filter_timer, device_id, property_value.key)
        )
        self._last_property_refresh = time.monotonic()
        self._last_filter_property_refresh = self._last_property_refresh
        self._accept_command_snapshot(snapshot)

    async def async_set_summer_ventilation_role(
        self, device_id: int, role: int
    ) -> None:
        """Write a unit's summer ventilation role and confirm it."""
        data = self.data.find_device(device_id) if self.data is not None else None
        if data is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        key = summer_ventilation_role_key(data[1])
        if key is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="summer_role_unavailable"
            )
        snapshot = await self._async_execute_command(
            partial(self.client.set_summer_ventilation_role, device_id, key, role)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_level_one_runtime(self, device_id: int, minutes: int) -> None:
        """Write a unit's runtime of level 1 per hour and confirm it."""
        data = self.data.find_device(device_id) if self.data is not None else None
        if data is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        key = level_one_runtime_key(data[1])
        if key is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="level_one_runtime_unavailable",
            )
        snapshot = await self._async_execute_command(
            partial(self.client.set_level_one_runtime, device_id, key, minutes)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_boost_duration(self, room_id: int, minutes: int) -> None:
        """Write a room's boost duration and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_boost_duration, room_id, minutes)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_boost(self, room_id: int, active: bool) -> None:
        """Start or stop a room's boost and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_boost, room_id, active)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_active_week_plan(self, plan_id: int) -> None:
        """Make a weekly schedule the active one and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_active_week_plan, plan_id)
        )
        self._accept_command_snapshot(snapshot)

    def absence_period(self) -> tuple[int | None, int | None]:
        """Return the saved period while the planner is on, else the chosen one."""
        run_state = self.data.run_state if self.data is not None else None
        if run_state is not None and run_state.absence_planned:
            return run_state.standby_start, run_state.standby_end
        return self.pending_absence_start, self.pending_absence_end

    async def async_set_absence(self, enabled: bool) -> None:
        """Switch the absence planner on for the chosen period, or off.

        Without a chosen period the planner starts now and ends a day later,
        as the app suggests when the planner is switched on.
        """
        start, end = self.absence_period()
        if enabled:
            if start is None:
                start = int(time.time()) // 60 * 60
            if end is None:
                end = start + ABSENCE_DEFAULT_DURATION
            command = partial(self.client.set_absence, True, start, end)
        else:
            command = partial(self.client.set_absence, False)
        snapshot = await self._async_execute_command(command)
        if enabled:
            self.pending_absence_start = self.pending_absence_end = None
        else:
            # Offer the period again when the planner is switched back on.
            self.pending_absence_start, self.pending_absence_end = start, end
        self._accept_command_snapshot(snapshot)

    async def async_set_absence_period(
        self, *, start: int | None = None, end: int | None = None
    ) -> None:
        """Change the period; written right away while the planner is on."""
        run_state = self.data.run_state if self.data is not None else None
        if run_state is None or not run_state.absence_planned:
            if start is not None:
                self.pending_absence_start = start
            if end is not None:
                self.pending_absence_end = end
            self.async_update_listeners()
            return
        snapshot = await self._async_execute_command(
            partial(self.client.set_absence, True, start, end)
        )
        self._accept_command_snapshot(snapshot)

    def _schedules(self) -> Schedules:
        schedules = self.data.schedules if self.data is not None else None
        if schedules is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedules_unavailable"
            )
        return schedules

    async def async_save_day_plan(
        self,
        plan_id: int | None,
        name: str | None,
        slots: tuple[tuple[int, int], ...] | None,
        functions: tuple[ScheduledFunction, ...] | None,
    ) -> int:
        """Add or change a daily schedule; unchanged parts are kept."""
        schedules = self._schedules()
        if plan_id is not None:
            existing = schedules.day_plan(plan_id)
            if existing is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_unknown"
                )
            name = name if name is not None else existing.name
            slots = slots if slots is not None else existing.slots
            functions = functions if functions is not None else existing.functions
        elif not name or slots is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="day_plan_incomplete"
            )
        _validate_slots(slots)
        plan = DayPlan(plan_id or 0, name, slots, functions or ())
        return await self._async_save(partial(self.client.save_day_plan, plan))

    async def async_save_week_plan(
        self, plan_id: int | None, name: str | None, days: tuple[int, ...] | None
    ) -> int:
        """Add or change a weekly schedule; unchanged parts are kept."""
        schedules = self._schedules()
        if plan_id is not None:
            existing = schedules.week_plan(plan_id)
            if existing is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_unknown"
                )
            name = name if name is not None else existing.name
            days = days if days is not None else existing.days
        elif not name or days is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="week_plan_incomplete"
            )
        if any(schedules.day_plan(day) is None for day in days):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="plan_unknown"
            )
        plan = WeekPlan(plan_id or 0, name, tuple(days))
        return await self._async_save(partial(self.client.save_week_plan, plan))

    async def async_remove_plan(self, plan_id: int, *, week: bool) -> None:
        """Remove a schedule that is neither active nor used by another one."""
        schedules = self._schedules()
        if week:
            if schedules.week_plan(plan_id) is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_unknown"
                )
            if self.data.run_state.week_plan == plan_id:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_active"
                )
        else:
            if schedules.day_plan(plan_id) is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_unknown"
                )
            if any(plan_id in item.days for item in schedules.week_plans):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="plan_in_use"
                )
        snapshot = await self._async_execute_command(
            partial(self.client.remove_plan, plan_id, week=week)
        )
        self._accept_command_snapshot(snapshot)

    async def _async_save(
        self, command: Callable[[], tuple[GatewaySnapshot, int]]
    ) -> int:
        snapshot, plan_id = await self._async_execute_command(command)
        self._accept_command_snapshot(snapshot)
        return plan_id

    async def async_set_presence(self, home: bool) -> None:
        """Switch between at home and away while the schedule keeps running."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_presence, home)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_site_info(
        self, *, name: str | None = None, language: int | None = None
    ) -> None:
        """Change the system name or language and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_site_info, name=name, language=language)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_remote_access(self, enabled: bool) -> None:
        """Switch the access over the internet and confirm it."""
        snapshot = await self._async_execute_command(
            partial(self.client.set_remote_access, enabled)
        )
        self._accept_command_snapshot(snapshot)

    async def async_set_room_settings(
        self, room_id: int, *, name: str | None = None, room_type: int | None = None
    ) -> None:
        """Change a room's name or type and confirm it."""
        snapshot = await self._async_execute_command(
            partial(
                self.client.set_room_settings, room_id, name=name, room_type=room_type
            )
        )
        self._accept_command_snapshot(snapshot)

    async def async_close(self) -> None:
        """Close the client outside Home Assistant's event loop."""
        async with self._io_lock:
            await self.hass.async_add_executor_job(self.client.close)

    async def _async_execute_command[T](self, command: Callable[[], T]) -> T:
        """Serialize commands and translate expected failures at the HA boundary."""
        try:
            async with self._io_lock:
                return await self.hass.async_add_executor_job(command)
        except CommandValidationError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=err.translation_key
            ) from err
        except CertificateMismatchError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="certificate_changed"
            ) from err
        except GatewayResponseError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="command_rejected"
            ) from err
        except ProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from err
        except (TransportError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err

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
            serial_number=snapshot.version.serial_number,
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
                        if device.hardware_version not in (None, 0)
                        else None
                    ),
                    sw_version=format_version(device.software_version),
                    suggested_area=room.name,
                    **_via_gateway(gateway_uuid, gateway_device.id),
                )
