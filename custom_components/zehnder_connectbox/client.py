"""Blocking high-level client for one paired Zehnder ConnectBox."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

from .const import (
    DEFAULT_PORT,
    PRODUCT_VARIANT_COMFOSPOT_50,
    SENSOR_MODE_LEVEL,
    VENTILATION_LEVELS,
)
from .models import (
    AttachedDevice,
    GatewaySnapshot,
    PairingData,
    PropertyKey,
    PropertyValue,
    Room,
    RunMode,
    RunState,
    SummerVentilationSettings,
    TemperatureMode,
    VersionInfo,
)
from .profiles import (
    PropertySpec,
    is_supported,
    optional_property_specs_for_device,
    property_specs_for_device,
    supports_sensor_mode,
    supports_summer_ventilation,
)
from .protocol import (
    OperationType,
    PropertySequenceCommand,
    ProtocolError,
    decode_pairing,
    decode_rooms,
    decode_run_state,
    decode_summer_ventilation_settings,
    decode_version,
    encode_filter_reset_room,
    encode_pairing,
    encode_property_request,
    encode_property_update,
    encode_room_level,
    encode_run_state,
    encode_summer_ventilation_settings,
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
# The app's manual mode offers the situations at home and away. The run-state
# update carries them as the user location next to the run mode.
SITUATION_LOCATIONS = {
    TemperatureMode.AWAKE: 1,
    TemperatureMode.AWAY: 2,
}


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

    def read_snapshot(
        self,
        *,
        refresh_properties: bool,
        refresh_filter_properties: bool = False,
        expected_property_values: dict[
            tuple[int, tuple[int, int, int]], bytes
        ]
        | None = None,
    ) -> GatewaySnapshot:
        """Read a complete state snapshot from the gateway."""
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
            return GatewaySnapshot(version, run_state, rooms, summer_settings)
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
                raise ProtocolError("no supported device reports summer ventilation")
            settings = self._read_summer_settings()
            if enabled:
                if settings is None or not settings.enabled:
                    raise ProtocolError("summer ventilation is disabled in the app")
                if (
                    settings.duration_hours is None
                    or not 1 <= settings.duration_hours <= 24
                ):
                    raise ProtocolError("summer ventilation duration is unavailable")
            if before.run_mode not in (RunMode.AUTOMATIC, RunMode.MANUAL):
                raise ProtocolError("summer ventilation is unavailable in this run mode")
            if before.temperature_mode in (0, 1):
                user_location = 1
            elif before.temperature_mode == 2:
                user_location = 2
            else:
                raise ProtocolError("current situation cannot be preserved safely")

            current = before.summer_ventilation_running(time.time())
            if before.summer_ventilation is None or (enabled and current is None):
                raise ProtocolError("summer ventilation state is unavailable")
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
                    raise ProtocolError("run mode or situation changed during summer ventilation")
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
            raise ValueError("a summer configuration value is required")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("summer ventilation enabled must be a boolean")
        if duration_hours is not None and (
            isinstance(duration_hours, bool)
            or not isinstance(duration_hours, int)
            or not 1 <= duration_hours <= 24
        ):
            raise ValueError(
                "summer ventilation duration must be between 1 and 24 hours"
            )
        try:
            rooms = self._read_rooms()
            if not supports_summer_ventilation(rooms):
                raise ProtocolError("no supported device reports summer ventilation")
            before = self._read_summer_settings()
            if before is None or before.duration_hours is None:
                raise ProtocolError("summer ventilation configuration is unavailable")
            if not 1 <= before.duration_hours <= 24:
                raise ProtocolError("current summer ventilation duration is invalid")
            run_before = self._read_run_state()
            if run_before.summer_ventilation is not False:
                raise ProtocolError(
                    "stop summer ventilation before changing its settings"
                )
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
            raise ValueError(
                "ventilation level must be between 0 and 4 or sensor-controlled"
            )
        try:
            run_state = self._read_run_state()
            # Restore cached telemetry so the sensor-board check below can see
            # the slowly refreshed sensor-status properties.
            rooms = self._restore_device_properties(self._read_rooms())
            room = next((item for item in rooms if item.room_id == room_id), None)
            if room is None:
                raise ProtocolError("room is no longer available")
            if level == 0 and not any(
                device.level_zero_supported for device in room.devices
            ):
                raise ValueError("this ventilation unit does not support level 0")
            if level == SENSOR_MODE_LEVEL and not any(
                supports_sensor_mode(room, device) for device in room.devices
            ):
                raise ValueError(
                    "this ventilation unit does not report a sensor board"
                )
            active_mode = run_state.temperature_mode
            mode = active_mode if temperature_mode is None else temperature_mode
            if temperature_mode is not None and mode not in {
                value.temperature_mode for value in room.ventilation
            }:
                raise ValueError("the room has no value for this situation")
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
            raise ValueError("only the situations at home and away can be selected")
        try:
            self._connected_session().request(
                OperationType.SET_RUN_STATE_REQUEST,
                OperationType.SET_RUN_STATE_CONFIRM,
                encode_run_state(int(RunMode.MANUAL), location),
            )
            run_state = self._wait_for(
                self._read_run_state,
                lambda state: state.run_mode == RunMode.MANUAL
                and state.temperature_mode == temperature_mode,
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
                raise ProtocolError("device is no longer available")
            room, device = result
            if (
                not is_supported(device)
                or device.product_variant != PRODUCT_VARIANT_COMFOSPOT_50
            ):
                raise ProtocolError("filter reset is not supported for this device")
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
        self._session = ConnectBoxSession(transport, self.gateway_uuid, self._app_uuid)
        return self._session

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
        expected_property_values: dict[
            tuple[int, tuple[int, int, int]], bytes
        ]
        | None = None,
    ) -> tuple[Room, ...]:
        devices = [
            device for room in rooms for device in room.devices if is_supported(device)
        ]
        if not devices:
            return rooms

        try:
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
                optional_specs = optional_property_specs_for_device(device)
                if request_filter_properties and optional_specs:
                    try:
                        self._request_properties(device, optional_specs)
                    except GatewayResponseError:
                        # The gateway rejects a sequence with an unknown
                        # property; keep the unit's other telemetry.
                        self.close()
                if request_filter_properties:
                    self._fully_requested_devices.add(device_key)

            if not expected_property_values:
                return self._read_rooms()

            # A filter reset is a write and must still be verified promptly.
            deadline = time.monotonic() + PROPERTY_SETTLE_TIMEOUT
            while True:
                refreshed = self._read_rooms()
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
                    merged = {
                        value.key.value_identity: value for value in cached
                    }
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
