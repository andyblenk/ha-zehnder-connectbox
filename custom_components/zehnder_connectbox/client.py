"""Blocking high-level client for one paired Zehnder ConnectBox."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

from .const import (
    DEFAULT_PORT,
    PRODUCT_VARIANT_COMFOSPOT_50,
    SENSOR_MODE_LEVEL,
    VENTILATION_LEVELS,
)
from .errors import CommandValidationError
from .models import (
    AttachedDevice,
    DayPlan,
    GatewaySnapshot,
    PairingData,
    PropertyKey,
    PropertyValue,
    Room,
    RunMode,
    RunState,
    ScheduledFunction,
    Schedules,
    SummerVentilationSettings,
    TemperatureMode,
    VersionInfo,
    WeekPlan,
)
from .profiles import (
    BOOST_DURATION_MAXIMUM,
    BOOST_DURATION_MINIMUM,
    BOOST_DURATION_STEP,
    LEVEL_ONE_RUNTIME,
    LEVEL_ONE_RUNTIME_VALUES,
    SUMMER_VENTILATION_ROLE,
    SUMMER_VENTILATION_ROLES,
    PropertySpec,
    error_hours_property_specs_for_device,
    fan_state_property_specs_for_device,
    is_supported,
    optional_property_specs_for_device,
    property_specs_for_device,
    settings_property_specs_for_device,
    supports_boost,
    supports_sensor_mode,
    supports_summer_ventilation,
)
from .protobuf import ProtobufDecodeError
from .protocol import (
    ROOM_BOOST_DURATION,
    Operation,
    OperationType,
    PropertySequenceCommand,
    ProtocolError,
    decode_day_plan_confirm,
    decode_pairing,
    decode_rooms,
    decode_run_state,
    decode_schedules,
    decode_summer_ventilation_settings,
    decode_version,
    decode_week_plan_confirm,
    encode_active_week_plan,
    encode_day_plan,
    encode_filter_reset_room,
    encode_pairing,
    encode_plan_id,
    encode_property_request,
    encode_property_update,
    encode_room_boost,
    encode_room_level,
    encode_room_number_update,
    encode_run_state,
    encode_standby_time_range,
    encode_summer_ventilation_settings,
    encode_week_plan,
)
from .session import ConnectBoxSession, GatewayResponseError
from .transport import ConnectBoxTransport, TransportError

PAIRING_NICKNAME = "Home Assistant"
PROPERTY_SETTLE_TIMEOUT = 5.0
LEVEL_SETTLE_TIMEOUT = 5.0
LEVEL_SETTLE_POLL_INTERVAL = 0.5
SUMMER_SETTLE_TIMEOUT = 8.0
SUMMER_SETTLE_POLL_INTERVAL = 0.5
COMMAND_SETTLE_TIMEOUT = 10.0
# A started boost may end this many seconds later than the room's duration
# allows (clock differences) before it is stopped again as unexpected.
BOOST_END_TOLERANCE = 300
SCHEDULES_REFRESH_INTERVAL = 15 * 60.0
SCHEDULES_REQUEST_TIMEOUT = 4.0
SCHEDULES_RETRY_INTERVAL = 60.0
# Run-state field 3 of the app's enumeration: the situation was changed by
# a user, or presence was switched to at home, while the schedule runs.
PRESENCE_LOCATION_HOME = 1
PRESENCE_LOCATION_AWAY = 2
# Gateway results of the app's enumeration (LOCKED, NOT_EXIST).
RESULT_NOT_EXIST = 7
RESULT_LOCKED = 9
# Slow sequences read with the filter refresh, each in its own sequence after
# the optional settings, so a unit that rejects one keeps its other telemetry.
SLOW_PROPERTY_SEQUENCES: dict[
    str, Callable[[AttachedDevice], tuple[PropertySpec, ...]]
] = {
    "settings": settings_property_specs_for_device,
    "error_hours": error_hours_property_specs_for_device,
}
# The app's manual mode offers the situations at home and away. The run-state
# update carries them as the user location next to the run mode.
SITUATION_LOCATIONS = {
    TemperatureMode.AWAKE: 1,
    TemperatureMode.AWAY: 2,
}


def _function_order(item: ScheduledFunction) -> tuple[int, int, int, int, int]:
    return (
        item.start_minute,
        item.function,
        item.duration_minutes,
        item.room_id,
        item.device_id,
    )


class PairingError(ConnectionError):
    """Raised when physical ConnectBox pairing does not complete."""


class ConnectBoxClient:
    """Synchronous state and command client with one shared TLS session."""

    def __init__(
        self,
        host: str,
        gateway_uuid: UUID,
        app_uuid: UUID,
        certificate_sha256: str,
        *,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.host = host
        self.gateway_uuid = gateway_uuid
        self._app_uuid = app_uuid
        self._certificate_sha256 = certificate_sha256
        self._port = port
        self._transport: ConnectBoxTransport | None = None
        self._session: ConnectBoxSession | None = None
        self._version: VersionInfo | None = None
        self._property_cache: dict[
            tuple[int, int, int | None], tuple[PropertyValue, ...]
        ] = {}
        self._fully_requested_devices: set[tuple[int, int, int | None]] = set()
        self._rejected_optional_devices: set[tuple[int, int, int | None]] = set()
        self._rejected_fan_state_devices: set[tuple[int, int, int | None]] = set()
        # Units that rejected one of the slow sequences, per sequence.
        self._rejected_slow_devices: dict[str, set[tuple[int, int, int | None]]] = {
            name: set() for name in SLOW_PROPERTY_SEQUENCES
        }
        self._schedules: Schedules | None = None
        self._schedules_read: float | None = None
        self._schedules_unsupported = False
        self._schedules_retry_after = 0.0

    @classmethod
    def pair(
        cls,
        host: str,
        gateway_uuid: UUID,
        *,
        timeout: float,
        port: int = DEFAULT_PORT,
        nickname: str = PAIRING_NICKNAME,
    ) -> PairingData:
        """Pair a new local identity after physical gateway confirmation."""
        app_uuid = uuid4()
        proposed_remote_uuid = uuid4()
        transport = ConnectBoxTransport(
            host,
            port=port,
            expected_fingerprint=None,
            io_timeout=timeout,
        )
        try:
            transport.connect()
            fingerprint = transport.certificate_fingerprint
            session = ConnectBoxSession(transport, gateway_uuid, app_uuid)
            response = session.request(
                OperationType.PAIR_REQUEST,
                OperationType.PAIR_CONFIRM,
                encode_pairing(app_uuid, proposed_remote_uuid, nickname),
                timeout=timeout,
            )
            app_id, assigned_remote_uuid = decode_pairing(response.body)
            return PairingData(
                app_uuid=app_uuid,
                remote_uuid=assigned_remote_uuid or proposed_remote_uuid,
                app_id=app_id,
                certificate_sha256=fingerprint,
            )
        except (ProtocolError, TransportError, GatewayResponseError) as err:
            raise PairingError("local pairing did not complete") from err
        finally:
            transport.close()

    @staticmethod
    def check_gateway_identity(host: str, gateway_uuid: UUID) -> None:
        """Check a known gateway over TLS without pairing or storing new trust.

        Only used after explicit reauth confirmation when UDP is unavailable.
        The session checks source/destination UUIDs, operation and reference
        before handling a reply. Even for an unpaired-request refusal, this
        checks that the reply reports the expected gateway UUID.
        Physical pairing is still required before accepting its certificate.
        """
        transport = ConnectBoxTransport(
            host, port=DEFAULT_PORT, expected_fingerprint=None
        )
        try:
            transport.connect()
            session = ConnectBoxSession(transport, gateway_uuid, uuid4())
            try:
                session.request(
                    OperationType.VERSION_REQUEST, OperationType.VERSION_CONFIRM
                )
            except GatewayResponseError:
                # A refusal is only raised after the reply envelope is checked.
                pass
        finally:
            transport.close()

    def read_snapshot(
        self,
        *,
        refresh_properties: bool,
        refresh_filter_properties: bool = False,
        expected_property_values: dict[tuple[int, tuple[int, int, int]], bytes]
        | None = None,
        refresh_schedules: bool = False,
    ) -> GatewaySnapshot:
        """Read a complete state snapshot from the gateway.

        The schedule list is only read again with refresh_schedules, which
        the regular poll sets; the read-back after a command reuses it.
        """
        try:
            version = self._version or self._read_version()
            run_state = self._read_run_state()
            rooms = self._read_rooms()
            summer_settings = (
                self._read_summer_settings()
                if supports_summer_ventilation(rooms)
                else None
            )
            if refresh_properties:
                rooms = self._read_device_properties(
                    rooms,
                    include_filter_properties=refresh_filter_properties,
                    expected_property_values=expected_property_values,
                )
            rooms = self._restore_device_properties(rooms)
            self._remember_device_properties(rooms)
            schedules = (
                self._read_schedules(run_state)
                if refresh_schedules
                else self._schedules
            )
            return GatewaySnapshot(
                version, run_state, rooms, summer_settings, schedules
            )
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_run_mode(self, mode: RunMode) -> GatewaySnapshot:
        """Set and read back a supported system operating mode."""
        try:
            session = self._connected_session()
            session.request(
                OperationType.SET_RUN_STATE_REQUEST,
                OperationType.SET_RUN_STATE_CONFIRM,
                encode_run_state(int(mode)),
            )
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_summer_ventilation(self, enabled: bool) -> GatewaySnapshot:
        """Toggle a configured gateway-wide summer interval and verify it."""
        try:
            before = self._read_run_state()
            rooms = self._read_rooms()
            if not supports_summer_ventilation(rooms):
                raise CommandValidationError("summer_not_supported")
            settings = self._read_summer_settings()
            if enabled:
                if settings is None or not settings.enabled:
                    raise CommandValidationError("summer_disabled")
                if (
                    settings.duration_hours is None
                    or not 1 <= settings.duration_hours <= 24
                ):
                    raise CommandValidationError("summer_duration_unavailable")
            if before.run_mode not in (RunMode.AUTOMATIC, RunMode.MANUAL):
                raise CommandValidationError("summer_mode_unavailable")
            if before.temperature_mode in (0, 1):
                user_location = 1
            elif before.temperature_mode == 2:
                user_location = 2
            else:
                raise CommandValidationError("situation_unavailable")

            current = before.summer_ventilation_running(time.time())
            if before.summer_ventilation is None or (enabled and current is None):
                raise CommandValidationError("summer_state_unavailable")
            if enabled and current:
                return self.read_snapshot(refresh_properties=False)
            if (
                not enabled
                and before.summer_ventilation is False
                and before.summer_ventilation_end is None
            ):
                return self.read_snapshot(refresh_properties=False)

            self._connected_session().request(
                OperationType.SET_RUN_STATE_REQUEST,
                OperationType.SET_RUN_STATE_CONFIRM,
                encode_run_state(
                    before.run_mode,
                    user_location,
                    summer_active=enabled,
                ),
            )
            deadline = time.monotonic() + SUMMER_SETTLE_TIMEOUT
            while True:
                after = self._read_run_state()
                if (
                    after.run_mode != before.run_mode
                    or after.temperature_mode != before.temperature_mode
                ):
                    raise ProtocolError(
                        "run mode or situation changed during summer ventilation"
                    )
                confirmed = (
                    after.summer_ventilation_running(time.time()) is True
                    if enabled
                    else after.summer_ventilation is False
                    and after.summer_ventilation_end is None
                )
                if confirmed:
                    return self.read_snapshot(refresh_properties=False)
                if time.monotonic() >= deadline:
                    raise ProtocolError("summer ventilation write was not confirmed")
                time.sleep(SUMMER_SETTLE_POLL_INTERVAL)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_summer_configuration(
        self, *, enabled: bool | None = None, duration_hours: int | None = None
    ) -> GatewaySnapshot:
        """Change one global summer setting while preserving the other."""
        if enabled is None and duration_hours is None:
            raise CommandValidationError("invalid_summer_configuration")
        if enabled is not None and not isinstance(enabled, bool):
            raise CommandValidationError("invalid_summer_configuration")
        if duration_hours is not None and (
            isinstance(duration_hours, bool)
            or not isinstance(duration_hours, int)
            or not 1 <= duration_hours <= 24
        ):
            raise CommandValidationError("invalid_summer_duration")
        try:
            rooms = self._read_rooms()
            if not supports_summer_ventilation(rooms):
                raise CommandValidationError("summer_not_supported")
            before = self._read_summer_settings()
            if before is None or before.duration_hours is None:
                raise CommandValidationError("summer_settings_unavailable")
            if not 1 <= before.duration_hours <= 24:
                raise CommandValidationError("summer_duration_unavailable")
            run_before = self._read_run_state()
            if run_before.summer_ventilation is not False:
                raise CommandValidationError("summer_running")
            target_enabled = before.enabled if enabled is None else enabled
            target_duration = (
                before.duration_hours if duration_hours is None else duration_hours
            )
            if (target_enabled, target_duration) == (
                before.enabled,
                before.duration_hours,
            ):
                return self.read_snapshot(refresh_properties=False)

            self._connected_session().request(
                OperationType.SET_SUMMER_VENTILATION_REQUEST,
                OperationType.SET_SUMMER_VENTILATION_CONFIRM,
                encode_summer_ventilation_settings(target_enabled, target_duration),
            )
            after = self._read_summer_settings()
            run_after = self._read_run_state()
            if after != SummerVentilationSettings(target_enabled, target_duration):
                raise ProtocolError(
                    "summer ventilation configuration was not confirmed"
                )
            if (
                run_after.run_mode != run_before.run_mode
                or run_after.temperature_mode != run_before.temperature_mode
                or run_after.summer_ventilation is not False
            ):
                raise ProtocolError("run state changed during summer configuration")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_level(
        self, room_id: int, level: int, temperature_mode: int | None = None
    ) -> GatewaySnapshot:
        """Set a room's level for the active or a given situation and read back.

        Without a temperature mode, the currently active situation is changed.
        """
        if level not in VENTILATION_LEVELS and level != SENSOR_MODE_LEVEL:
            raise CommandValidationError("invalid_level")
        try:
            run_state = self._read_run_state()
            # Restore cached telemetry so the sensor-board check below can see
            # the slowly refreshed sensor-status properties.
            rooms = self._restore_device_properties(self._read_rooms())
            room = next((item for item in rooms if item.room_id == room_id), None)
            if room is None:
                raise CommandValidationError("room_unavailable")
            if level == 0 and not any(
                device.level_zero_supported for device in room.devices
            ):
                raise CommandValidationError("level_zero_unsupported")
            if level == SENSOR_MODE_LEVEL and not any(
                supports_sensor_mode(room, device) for device in room.devices
            ):
                raise CommandValidationError("sensor_board_unavailable")
            active_mode = run_state.temperature_mode
            mode = active_mode if temperature_mode is None else temperature_mode
            if temperature_mode is not None and mode not in {
                value.temperature_mode for value in room.ventilation
            }:
                raise CommandValidationError("situation_not_configured")
            session = self._connected_session()
            session.request(
                OperationType.SET_ROOM_VALUE_REQUEST,
                OperationType.SET_ROOM_VALUE_CONFIRM,
                encode_room_level(room, mode, level),
            )
            if mode == active_mode:
                self._wait_for_room_level(room_id, level)
            else:
                self._wait_for_configured_level(room_id, mode, level)
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_situation(self, temperature_mode: int) -> GatewaySnapshot:
        """Select a situation of the manual mode and confirm it was applied."""
        location = SITUATION_LOCATIONS.get(TemperatureMode(temperature_mode))
        if location is None:
            raise CommandValidationError("situation_not_selectable")
        try:
            self._connected_session().request(
                OperationType.SET_RUN_STATE_REQUEST,
                OperationType.SET_RUN_STATE_CONFIRM,
                encode_run_state(int(RunMode.MANUAL), location),
            )
            run_state = self._wait_for(
                self._read_run_state,
                lambda state: (
                    state.run_mode == RunMode.MANUAL
                    and state.temperature_mode == temperature_mode
                ),
            )
            if (
                run_state.run_mode != RunMode.MANUAL
                or run_state.temperature_mode != temperature_mode
            ):
                raise ProtocolError(
                    "gateway reported run mode "
                    f"{run_state.run_mode} and situation {run_state.temperature_mode}"
                )
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    @staticmethod
    def _wait_for[T](read: Callable[[], T], condition: Callable[[T], bool]) -> T:
        """Poll a gateway value until it meets the condition or time runs out."""
        deadline = time.monotonic() + COMMAND_SETTLE_TIMEOUT
        while True:
            value = read()
            if condition(value) or time.monotonic() >= deadline:
                return value
            time.sleep(LEVEL_SETTLE_POLL_INTERVAL)

    def _wait_for_room_level(self, room_id: int, level: int) -> None:
        """Briefly wait until the room's current level reports a written value.

        The units apply a new level over the radio link, so the room's target
        level can lag behind the confirmed write. Returning early would show
        the previous level until the next poll.
        """
        deadline = time.monotonic() + LEVEL_SETTLE_TIMEOUT
        while True:
            room = next(
                (item for item in self._read_rooms() if item.room_id == room_id),
                None,
            )
            if room is None or room.target_level in (None, level):
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(LEVEL_SETTLE_POLL_INTERVAL)

    def _wait_for_configured_level(
        self, room_id: int, temperature_mode: int, level: int
    ) -> None:
        """Confirm a level written for a situation that is not active."""
        deadline = time.monotonic() + LEVEL_SETTLE_TIMEOUT
        while True:
            room = next(
                (item for item in self._read_rooms() if item.room_id == room_id),
                None,
            )
            if room is None:
                raise ProtocolError("room is no longer available")
            if any(
                value.temperature_mode == temperature_mode and value.level == level
                for value in room.ventilation
            ):
                return
            if time.monotonic() >= deadline:
                raise ProtocolError(
                    "gateway did not report the configured situation level"
                )
            time.sleep(LEVEL_SETTLE_POLL_INTERVAL)

    def reset_filter_timer(
        self, device_id: int, property_key: PropertyKey
    ) -> GatewaySnapshot:
        """Acknowledge filter replacement and reset its verified runtime."""
        if property_key.value_identity != (38, 0, 15):
            raise ValueError("unexpected filter-runtime property")

        try:
            rooms = self._read_rooms()
            result = next(
                (
                    (room, device)
                    for room in rooms
                    for device in room.devices
                    if device.device_id == device_id
                ),
                None,
            )
            if result is None:
                raise CommandValidationError("device_unavailable")
            room, device = result
            if (
                not is_supported(device)
                or device.product_variant != PRODUCT_VARIANT_COMFOSPOT_50
            ):
                raise CommandValidationError("filter_reset_unsupported")
            if property_key.product_type != device.product_type:
                raise ProtocolError("filter-runtime property does not match the device")

            session = self._connected_session()
            session.request(
                OperationType.SET_ROOM_REQUEST,
                OperationType.SET_ROOM_CONFIRM,
                encode_filter_reset_room(room, device_id),
            )
            session.request(
                OperationType.SET_DEVICE_PROPERTIES_REQUEST,
                OperationType.SET_DEVICE_PROPERTIES_CONFIRM,
                encode_property_update(device_id, property_key, b"\x00\x00"),
            )

            self._property_cache.pop(self._property_cache_key(device), None)
            return self.read_snapshot(
                refresh_properties=True,
                refresh_filter_properties=True,
                expected_property_values={
                    (device_id, property_key.value_identity): b"\x00\x00"
                },
            )
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_summer_ventilation_role(
        self, device_id: int, property_key: PropertyKey, role: int
    ) -> GatewaySnapshot:
        """Write a unit's summer ventilation role and read it back.

        The official app sets the role on the unit's settings page; the value
        written from here showed there. It is refused while a summer interval
        runs.
        """
        if property_key.value_identity != SUMMER_VENTILATION_ROLE.key:
            raise ValueError("unexpected summer-role property")
        if role not in SUMMER_VENTILATION_ROLES:
            raise CommandValidationError("invalid_summer_role")
        return self._write_optional_setting(
            device_id,
            property_key,
            bytes((role,)),
            unsupported_key="summer_role_unsupported",
            check_run_state=lambda run_state: (
                "summer_role_interval_active" if run_state.summer_ventilation else None
            ),
            label="summer ventilation role",
        )

    def set_level_one_runtime(
        self, device_id: int, property_key: PropertyKey, minutes: int
    ) -> GatewaySnapshot:
        """Write a unit's runtime of level 1 per hour and read it back."""
        if property_key.value_identity != LEVEL_ONE_RUNTIME.key:
            raise ValueError("unexpected level-one runtime property")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes not in LEVEL_ONE_RUNTIME_VALUES
        ):
            raise CommandValidationError("invalid_level_one_runtime")
        return self._write_optional_setting(
            device_id,
            property_key,
            bytes((minutes,)),
            unsupported_key="level_one_runtime_unsupported",
            label="level-one runtime",
        )

    def _write_optional_setting(
        self,
        device_id: int,
        property_key: PropertyKey,
        expected: bytes,
        *,
        unsupported_key: str,
        label: str,
        check_run_state: Callable[[RunState], str | None] | None = None,
    ) -> GatewaySnapshot:
        """Write one setting of the optional sequence (38.0.9-11) and read it back.

        The setting is written like the filter runtime, as one device-property
        write with the key the gateway reported, and then requested again in
        its verified read sequence until the unit reports the written value.
        """
        try:
            if check_run_state is not None:
                refusal = check_run_state(self._read_run_state())
                if refusal is not None:
                    raise CommandValidationError(refusal)
            device = next(
                (
                    item
                    for room in self._read_rooms()
                    for item in room.devices
                    if item.device_id == device_id
                ),
                None,
            )
            if device is None:
                raise CommandValidationError("device_unavailable")
            if not optional_property_specs_for_device(device):
                raise CommandValidationError(unsupported_key)
            if property_key.product_type != device.product_type:
                raise ProtocolError(f"{label} property does not match the device")

            self._connected_session().request(
                OperationType.SET_DEVICE_PROPERTIES_REQUEST,
                OperationType.SET_DEVICE_PROPERTIES_CONFIRM,
                encode_property_update(device_id, property_key, expected),
            )
            self._request_properties(device, optional_property_specs_for_device(device))
            identity = property_key.value_identity
            deadline = time.monotonic() + COMMAND_SETTLE_TIMEOUT
            while True:
                rooms = self._read_rooms()
                current = next(
                    (
                        item
                        for room in rooms
                        for item in room.devices
                        if item.device_id == device_id
                    ),
                    None,
                )
                if current is not None and current.property_bytes(identity) == expected:
                    # Keep the read-back value for the following snapshots.
                    self._remember_device_properties(
                        self._restore_device_properties(rooms)
                    )
                    break
                if time.monotonic() >= deadline:
                    raise ProtocolError(f"gateway did not report the written {label}")
                time.sleep(LEVEL_SETTLE_POLL_INTERVAL)
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_boost_duration(self, room_id: int, minutes: int) -> GatewaySnapshot:
        """Write a room's boost duration and read it back.

        The room is written back as read with only its boost duration
        (field 22) changed; the app showed the written value on the room's
        settings page. The app offers 15 to 120 minutes in steps of 15.
        """
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not BOOST_DURATION_MINIMUM <= minutes <= BOOST_DURATION_MAXIMUM
            or minutes % BOOST_DURATION_STEP
        ):
            raise CommandValidationError("invalid_boost_duration")
        try:
            room = self._boost_room(room_id)
            if room.boost_active(time.time()):
                raise CommandValidationError("boost_active")
            self._connected_session().request(
                OperationType.SET_ROOM_REQUEST,
                OperationType.SET_ROOM_CONFIRM,
                encode_room_number_update(room, {ROOM_BOOST_DURATION: minutes}),
            )
            written = self._wait_for(
                lambda: self._find_room(room_id),
                lambda item: item is None or item.boost_duration == minutes,
            )
            if written is None or written.boost_duration != minutes:
                raise ProtocolError("gateway did not report the written boost duration")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_boost(self, room_id: int, active: bool) -> GatewaySnapshot:
        """Start a boost for the room's duration or stop it, as the app does.

        The app sends a room-value update with field 15 set to the end of the
        boost as Unix time, or to 0 to end it early. The write is confirmed by
        the end the room reports afterwards (room field 18). A reported end
        later than the duration allows is stopped again and reported.
        """
        try:
            run_state = self._read_run_state()
            room = self._boost_room(room_id)
            now = time.time()
            if room.boost_active(now) is active:
                return self.read_snapshot(refresh_properties=False)
            if active:
                if run_state.run_mode not in (RunMode.AUTOMATIC, RunMode.MANUAL):
                    raise CommandValidationError("boost_mode_unavailable")
                duration = room.boost_duration
                if (
                    duration is None
                    or not BOOST_DURATION_MINIMUM <= duration <= BOOST_DURATION_MAXIMUM
                ):
                    raise CommandValidationError("boost_duration_unavailable")
                until = int(now) + duration * 60
            else:
                until = 0

            session = self._connected_session()
            session.request(
                OperationType.SET_ROOM_VALUE_REQUEST,
                OperationType.SET_ROOM_VALUE_CONFIRM,
                encode_room_boost(room_id, until),
            )
            written = self._wait_for(
                lambda: self._find_room(room_id),
                lambda item: item is None or item.boost_active(time.time()) is active,
            )
            if written is None or written.boost_active(time.time()) is not active:
                raise ProtocolError(
                    "gateway did not start the boost"
                    if active
                    else "gateway did not stop the boost"
                )
            if active and (written.boost_until or 0) > until + BOOST_END_TOLERANCE:
                # Do not leave a boost running longer than the room allows.
                session.request(
                    OperationType.SET_ROOM_VALUE_REQUEST,
                    OperationType.SET_ROOM_VALUE_CONFIRM,
                    encode_room_boost(room_id, 0),
                )
                raise ProtocolError("gateway reported an unexpected boost end")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_active_week_plan(self, plan_id: int) -> GatewaySnapshot:
        """Make a weekly schedule the active one, as the app's plan page does.

        The gateway confirmed the request and then reported the schedule's ID
        in the run state (field 6), together with today's daily schedule.
        """
        try:
            schedules = self._schedules
            if schedules is not None and schedules.week_plan(plan_id) is None:
                raise CommandValidationError("week_plan_unknown")
            self._connected_session().request(
                OperationType.SET_ACTIVE_WEEK_PLAN_REQUEST,
                OperationType.SET_ACTIVE_WEEK_PLAN_CONFIRM,
                encode_active_week_plan(plan_id),
            )
            run_state = self._wait_for(
                self._read_run_state, lambda state: state.week_plan == plan_id
            )
            if run_state.week_plan != plan_id:
                raise ProtocolError("gateway did not activate the weekly schedule")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def set_absence(
        self, enabled: bool, start: int | None = None, end: int | None = None
    ) -> GatewaySnapshot:
        """Save or switch off the absence planner and read it back.

        Switched on, the gateway puts the system into frost protection for the
        period, as the app explains on the planner's page. A period that is
        not given is kept as reported; switching off sends the reported period
        with mode 0, as tested.
        """
        try:
            before = self._read_run_state()
            start = start if start is not None else before.standby_start
            end = end if end is not None else before.standby_end
            if enabled:
                if start is None or end is None:
                    raise CommandValidationError("absence_period_missing")
                if end <= start:
                    raise CommandValidationError("absence_period_invalid")
                if end <= time.time():
                    raise CommandValidationError("absence_period_past")
            else:
                start = start or 0
                end = end or 0
            self._connected_session().request(
                OperationType.SET_STANDBY_TIME_RANGE_REQUEST,
                OperationType.SET_STANDBY_TIME_RANGE_CONFIRM,
                encode_standby_time_range(enabled, start, end),
            )

            def saved(state: RunState) -> bool:
                if not enabled:
                    return state.absence_planned is False
                return (
                    state.absence_planned is True
                    and state.standby_start == start
                    and state.standby_end == end
                )

            run_state = self._wait_for(self._read_run_state, saved)
            if not saved(run_state):
                raise ProtocolError("gateway did not report the absence planner")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def save_day_plan(self, plan: DayPlan) -> tuple[GatewaySnapshot, int]:
        """Add (plan ID 0) or replace a daily schedule and check the result.

        A change is made under the schedule's lock, like the app does, so a
        plan another client is editing is refused. The stored schedule is read
        back from the schedule list, which also holds the timed functions.
        """
        expected_slots = tuple(sorted(plan.slots))
        expected_functions = sorted(plan.functions, key=lambda item: item.start_minute)
        plan = replace(plan, slots=expected_slots, functions=tuple(expected_functions))
        try:
            session = self._connected_session()
            if plan.plan_id == 0:
                # The gateway dropped the timed functions of a new plan; it
                # kept them when the plan was changed, so they are added with
                # a change right after the plan was created.
                response = session.request(
                    OperationType.ADD_DAY_PLAN_REQUEST,
                    OperationType.ADD_DAY_PLAN_CONFIRM,
                    encode_day_plan(replace(plan, functions=())),
                )
                plan_id = decode_day_plan_confirm(response.body).plan_id
                if plan.functions:
                    with self._plan_lock(plan_id, week=False):
                        session.request(
                            OperationType.SET_DAY_PLAN_REQUEST,
                            OperationType.SET_DAY_PLAN_CONFIRM,
                            encode_day_plan(replace(plan, plan_id=plan_id)),
                        )
            else:
                plan_id = plan.plan_id
                with self._plan_lock(plan_id, week=False):
                    session.request(
                        OperationType.SET_DAY_PLAN_REQUEST,
                        OperationType.SET_DAY_PLAN_CONFIRM,
                        encode_day_plan(plan),
                    )
            stored = self._reload_schedules().day_plan(plan_id)
            if (
                stored is None
                or stored.name != plan.name
                or tuple(sorted(stored.slots)) != expected_slots
                or sorted(stored.functions, key=_function_order)
                != sorted(plan.functions, key=_function_order)
            ):
                raise ProtocolError(
                    "gateway did not store the daily schedule as written"
                )
            return self.read_snapshot(refresh_properties=False), plan_id
        except (ProtocolError, TransportError):
            self.close()
            raise

    def save_week_plan(self, plan: WeekPlan) -> tuple[GatewaySnapshot, int]:
        """Add (plan ID 0) or replace a weekly schedule and check the result."""
        try:
            session = self._connected_session()
            if plan.plan_id == 0:
                response = session.request(
                    OperationType.ADD_WEEK_PLAN_REQUEST,
                    OperationType.ADD_WEEK_PLAN_CONFIRM,
                    encode_week_plan(plan),
                )
                plan_id = decode_week_plan_confirm(response.body).plan_id
            else:
                plan_id = plan.plan_id
                with self._plan_lock(plan_id, week=True):
                    session.request(
                        OperationType.SET_WEEK_PLAN_REQUEST,
                        OperationType.SET_WEEK_PLAN_CONFIRM,
                        encode_week_plan(plan),
                    )
            stored = self._reload_schedules().week_plan(plan_id)
            if stored is None or (stored.name, stored.days) != (plan.name, plan.days):
                raise ProtocolError(
                    "gateway did not store the weekly schedule as written"
                )
            return self.read_snapshot(refresh_properties=False), plan_id
        except (ProtocolError, TransportError):
            self.close()
            raise

    def remove_plan(self, plan_id: int, *, week: bool) -> GatewaySnapshot:
        """Remove a daily or weekly schedule and check that it is gone.

        The gateway refused (result 12) to remove a daily schedule that a
        weekly schedule still used.
        """
        try:
            session = self._connected_session()
            with self._plan_lock(plan_id, week=week):
                session.request(
                    OperationType.REMOVE_WEEK_PLAN_REQUEST
                    if week
                    else OperationType.REMOVE_DAY_PLAN_REQUEST,
                    OperationType.REMOVE_WEEK_PLAN_CONFIRM
                    if week
                    else OperationType.REMOVE_DAY_PLAN_CONFIRM,
                    encode_plan_id(plan_id),
                )
            schedules = self._reload_schedules()
            remaining = (
                schedules.week_plan(plan_id) if week else schedules.day_plan(plan_id)
            )
            if remaining is not None:
                raise ProtocolError("gateway still lists the removed schedule")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    @contextmanager
    def _plan_lock(self, plan_id: int, *, week: bool) -> Iterator[None]:
        """Hold a schedule's lock while it is changed, then release it."""
        session = self._connected_session()
        if week:
            lock = (
                OperationType.LOCK_WEEK_PLAN_REQUEST,
                OperationType.LOCK_WEEK_PLAN_CONFIRM,
            )
            release = (
                OperationType.RELEASE_WEEK_PLAN_REQUEST,
                OperationType.RELEASE_WEEK_PLAN_CONFIRM,
            )
        else:
            lock = (
                OperationType.LOCK_DAY_PLAN_REQUEST,
                OperationType.LOCK_DAY_PLAN_CONFIRM,
            )
            release = (
                OperationType.RELEASE_DAY_PLAN_REQUEST,
                OperationType.RELEASE_DAY_PLAN_CONFIRM,
            )
        try:
            session.request(*lock, encode_plan_id(plan_id))
        except GatewayResponseError as err:
            if err.result == RESULT_LOCKED:
                raise CommandValidationError("plan_locked") from err
            if err.result == RESULT_NOT_EXIST:
                raise CommandValidationError("plan_unknown") from err
            raise
        try:
            yield
        finally:
            try:
                session.request(*release, encode_plan_id(plan_id))
            except (GatewayResponseError, ProtocolError, TransportError):
                # The connection is closed after a failure, which ends the
                # session and with it the lock.
                self.close()

    def _reload_schedules(self) -> Schedules:
        """Read the schedule list now, after this client changed it."""
        response = self._connected_session().request(
            OperationType.SCHEDULES_REQUEST,
            OperationType.SCHEDULES_CONFIRM,
            timeout=SCHEDULES_REQUEST_TIMEOUT,
        )
        self._schedules = decode_schedules(response.body)
        self._schedules_read = time.monotonic()
        self._schedules_unsupported = False
        return self._schedules

    def set_presence(self, home: bool) -> GatewaySnapshot:
        """Switch between at home and away while the schedule keeps running.

        Tapping the house on the app's start screen in schedule operation
        changed the situation to away and back; the run state reported the
        change as made by the user (field 3 = 2) and the mode stayed
        automatic. The same update as the app's mode change is sent with the
        automatic mode and the location.
        """
        try:
            before = self._read_run_state()
            if before.run_mode != RunMode.AUTOMATIC:
                raise CommandValidationError("presence_needs_schedule")
            self._connected_session().request(
                OperationType.SET_RUN_STATE_REQUEST,
                OperationType.SET_RUN_STATE_CONFIRM,
                encode_run_state(
                    int(RunMode.AUTOMATIC),
                    PRESENCE_LOCATION_HOME if home else PRESENCE_LOCATION_AWAY,
                ),
            )

            def applied(state: RunState) -> bool:
                away = state.temperature_mode == TemperatureMode.AWAY
                return state.run_mode == RunMode.AUTOMATIC and away is not home

            run_state = self._wait_for(self._read_run_state, applied)
            if not applied(run_state):
                raise ProtocolError("gateway did not report the presence")
            return self.read_snapshot(refresh_properties=False)
        except (ProtocolError, TransportError):
            self.close()
            raise

    def _find_room(self, room_id: int) -> Room | None:
        """Read the rooms and return one of them."""
        return next(
            (item for item in self._read_rooms() if item.room_id == room_id), None
        )

    def _boost_room(self, room_id: int) -> Room:
        """Return a room whose unit offers the boost."""
        room = self._find_room(room_id)
        if room is None:
            raise CommandValidationError("room_unavailable")
        if not any(supports_boost(room, device) for device in room.devices):
            raise CommandValidationError("boost_unsupported")
        return room

    def close(self) -> None:
        """Close the shared transport."""
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._session = None

    def _connected_session(self) -> ConnectBoxSession:
        if self._session is not None:
            return self._session
        transport = ConnectBoxTransport(
            self.host,
            port=self._port,
            expected_fingerprint=self._certificate_sha256,
        )
        transport.connect()
        self._transport = transport
        self._session = ConnectBoxSession(
            transport,
            self.gateway_uuid,
            self._app_uuid,
            on_unsolicited=self._handle_notification,
        )
        return self._session

    def _handle_notification(self, operation: Operation, _body: bytes) -> None:
        """Note what another client changed, from a frame that answers no request.

        The gateway forwards changes of other clients, such as the official
        app, to every connected client. The affected data is read again with
        the next poll.
        """
        if operation.type == OperationType.SCHEDULES_NOTIFICATION:
            # A client changed a schedule; read the list again.
            self._schedules_read = None

    def _read_schedules(self, run_state: RunState) -> Schedules | None:
        """Return the schedule list, read again when it may have changed.

        The list is stored on the gateway, so reading it does not use the
        radio link. It is read at the start, after the gateway announced a
        change, when the run state names a schedule the list does not have,
        and otherwise every SCHEDULES_REFRESH_INTERVAL seconds. A gateway
        that does not answer the request with a list is not asked again until
        the integration is reloaded.
        """
        cached = self._schedules
        now = time.monotonic()
        if self._schedules_unsupported or now < self._schedules_retry_after:
            return cached
        if self._session is None:
            # The connection was just closed, for example after a unit
            # rejected an optional sequence. The gateway may refuse a new
            # connection right away, so read the list with the next poll.
            return cached
        day_plan = run_state.day_plan
        fresh = (
            cached is not None
            and self._schedules_read is not None
            and now - self._schedules_read < SCHEDULES_REFRESH_INTERVAL
            and (
                run_state.week_plan is None
                or cached.week_plan(run_state.week_plan) is not None
            )
            and (day_plan is None or cached.day_plan(day_plan.plan_id) is not None)
        )
        if fresh:
            return cached
        try:
            response = self._connected_session().request(
                OperationType.SCHEDULES_REQUEST,
                OperationType.SCHEDULES_CONFIRM,
                timeout=SCHEDULES_REQUEST_TIMEOUT,
            )
            self._schedules = decode_schedules(response.body)
        except (GatewayResponseError, ProtobufDecodeError):
            # Keep the snapshot; a refused request may need a new connection.
            self._schedules_unsupported = True
            self._schedules = None
            self.close()
            return None
        except (ProtocolError, TransportError):
            # No usable answer in time: keep the last list and try again later.
            self._schedules_retry_after = now + SCHEDULES_RETRY_INTERVAL
            self.close()
            return cached
        self._schedules_read = now
        return self._schedules

    def _read_version(self) -> VersionInfo:
        response = self._connected_session().request(
            OperationType.VERSION_REQUEST, OperationType.VERSION_CONFIRM
        )
        self._version = decode_version(response.body)
        return self._version

    def _read_run_state(self) -> RunState:
        response = self._connected_session().request(
            OperationType.RUN_STATE_REQUEST, OperationType.RUN_STATE_CONFIRM
        )
        return decode_run_state(response.body)

    def _read_summer_settings(self) -> SummerVentilationSettings | None:
        """Read the global summer function setting when supported."""
        try:
            response = self._connected_session().request(
                OperationType.SUMMER_VENTILATION_REQUEST,
                OperationType.SUMMER_VENTILATION_CONFIRM,
            )
        except GatewayResponseError as err:
            if err.result in (1, 7):
                return None
            raise
        return decode_summer_ventilation_settings(response.body)

    def _read_rooms(self) -> tuple[Room, ...]:
        response = self._connected_session().request(
            OperationType.ROOMS_REQUEST, OperationType.ROOMS_CONFIRM
        )
        return decode_rooms(response.body)

    def _read_device_properties(
        self,
        rooms: tuple[Room, ...],
        *,
        include_filter_properties: bool,
        expected_property_values: dict[tuple[int, tuple[int, int, int]], bytes]
        | None = None,
    ) -> tuple[Room, ...]:
        devices = [
            device for room in rooms for device in room.devices if is_supported(device)
        ]
        if not devices:
            return rooms

        try:
            fan_state_devices: list[AttachedDevice] = []
            optional_devices: list[AttachedDevice] = []
            slow_devices: dict[str, list[AttachedDevice]] = {
                name: [] for name in SLOW_PROPERTY_SEQUENCES
            }
            for device in devices:
                device_key = self._property_cache_key(device)
                request_filter_properties = (
                    include_filter_properties
                    or device_key not in self._fully_requested_devices
                )
                specs = property_specs_for_device(
                    device,
                    include_filter_properties=request_filter_properties,
                )
                self._request_properties(device, specs)
                if (
                    not expected_property_values
                    and device_key not in self._rejected_fan_state_devices
                    and fan_state_property_specs_for_device(device)
                ):
                    fan_state_devices.append(device)
                if (
                    request_filter_properties
                    and not expected_property_values
                    and device_key not in self._rejected_optional_devices
                    and optional_property_specs_for_device(device)
                ):
                    optional_devices.append(device)
                if request_filter_properties and not expected_property_values:
                    for name, specs_for in SLOW_PROPERTY_SEQUENCES.items():
                        if device_key not in self._rejected_slow_devices[
                            name
                        ] and specs_for(device):
                            slow_devices[name].append(device)
                if request_filter_properties:
                    self._fully_requested_devices.add(device_key)

            # Complete the normal property reads before an optional sequence
            # can be rejected and require a new connection.
            core_rooms = self._read_rooms()
            for device in fan_state_devices:
                try:
                    self._request_properties(
                        device, fan_state_property_specs_for_device(device)
                    )
                except GatewayResponseError:
                    # As for the optional settings below: keep the completed
                    # core telemetry and skip this unit's fan state until the
                    # integration is reloaded.
                    self._rejected_fan_state_devices.add(
                        self._property_cache_key(device)
                    )
                    self.close()
                    return core_rooms
            for device in optional_devices:
                try:
                    self._request_properties(
                        device, optional_property_specs_for_device(device)
                    )
                except GatewayResponseError:
                    # A rejected sequence may remain open on the gateway.
                    # Keep the completed core telemetry and skip this optional
                    # sequence until the integration is reloaded.
                    self._rejected_optional_devices.add(
                        self._property_cache_key(device)
                    )
                    self.close()
                    return core_rooms
            for name, specs_for in SLOW_PROPERTY_SEQUENCES.items():
                for device in slow_devices[name]:
                    try:
                        self._request_properties(device, specs_for(device))
                    except GatewayResponseError:
                        # As above: skip this sequence for the unit until the
                        # integration is reloaded.
                        self._rejected_slow_devices[name].add(
                            self._property_cache_key(device)
                        )
                        self.close()
                        return core_rooms

            if not expected_property_values:
                if fan_state_devices or optional_devices or any(slow_devices.values()):
                    return self._read_rooms()
                return core_rooms

            # A filter reset is a write and must still be verified promptly.
            deadline = time.monotonic() + PROPERTY_SETTLE_TIMEOUT
            refreshed = core_rooms
            while True:
                found = {
                    (device.device_id, value.key.value_identity): value.value
                    for room in refreshed
                    for device in room.devices
                    for value in device.properties
                    if value.value is not None
                }
                if all(
                    found.get(identity) == value
                    for identity, value in expected_property_values.items()
                ):
                    return refreshed
                if time.monotonic() >= deadline:
                    raise ProtocolError(
                        "gateway did not report the reset filter runtime"
                    )
                time.sleep(0.2)
                refreshed = self._read_rooms()
        except (ProtocolError, TransportError):
            if expected_property_values:
                raise
            # Device-specific telemetry is optional. Preserve the core room state
            # and reconnect on the next coordinator refresh.
            self.close()
            return rooms

    def _request_properties(
        self, device: AttachedDevice, specs: tuple[PropertySpec, ...]
    ) -> None:
        """Request one bounded property sequence for a unit."""
        for index, spec in enumerate(specs):
            if index == 0:
                command = PropertySequenceCommand.START
            elif index == len(specs) - 1:
                command = PropertySequenceCommand.FINISH
            else:
                command = PropertySequenceCommand.CONTINUE
            self._connected_session().request(
                OperationType.DEVICE_PROPERTIES_REQUEST,
                OperationType.DEVICE_PROPERTIES_CONFIRM,
                encode_property_request(
                    command,
                    device.device_id,
                    spec.request_key(device.product_type),
                ),
            )

    @staticmethod
    def _property_cache_key(device: AttachedDevice) -> tuple[int, int, int | None]:
        """Return an identity that cannot cross device product profiles."""
        return device.device_id, device.product_type, device.product_variant

    def _remember_device_properties(self, rooms: tuple[Room, ...]) -> None:
        """Remember usable telemetry from each room-state refresh."""
        connected = {
            self._property_cache_key(device)
            for room in rooms
            for device in room.devices
        }
        self._property_cache = {
            key: value
            for key, value in self._property_cache.items()
            if key in connected
        }
        self._fully_requested_devices.intersection_update(connected)
        self._rejected_optional_devices.intersection_update(connected)
        self._rejected_fan_state_devices.intersection_update(connected)
        for rejected in self._rejected_slow_devices.values():
            rejected.intersection_update(connected)
        for room in rooms:
            for device in room.devices:
                usable = tuple(value for value in device.properties if value.value)
                if usable:
                    self._property_cache[self._property_cache_key(device)] = usable

    def _restore_device_properties(self, rooms: tuple[Room, ...]) -> tuple[Room, ...]:
        """Keep slow device telemetry across normal room-state refreshes."""
        restored_rooms: list[Room] = []
        for room in rooms:
            restored_devices: list[AttachedDevice] = []
            for device in room.devices:
                cached = self._property_cache.get(self._property_cache_key(device))
                if cached is not None:
                    merged = {value.key.value_identity: value for value in cached}
                    for value in device.properties:
                        identity = value.key.value_identity
                        # A property read may temporarily expose an empty value.
                        # Keep the last usable value until the gateway reports
                        # a replacement, including an explicit status byte.
                        if value.value or identity not in merged:
                            merged[identity] = value
                    device = replace(device, properties=tuple(merged.values()))
                restored_devices.append(device)
            restored_rooms.append(replace(room, devices=tuple(restored_devices)))
        return tuple(restored_rooms)
