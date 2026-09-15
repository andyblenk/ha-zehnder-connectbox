"""Blocking high-level client for one paired Zehnder ConnectBox."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

from .const import DEFAULT_PORT
from .models import GatewaySnapshot, PairingData, Room, RunMode, RunState, VersionInfo
from .profiles import PROPERTY_SPECS, is_supported
from .protocol import (
    OperationType,
    PropertySequenceCommand,
    ProtocolError,
    decode_pairing,
    decode_rooms,
    decode_run_state,
    decode_version,
    encode_pairing,
    encode_property_request,
    encode_room_level,
    encode_run_state,
)
from .session import ConnectBoxSession, GatewayResponseError
from .transport import ConnectBoxTransport, TransportError

PAIRING_NICKNAME = "Home Assistant"
PROPERTY_SETTLE_TIMEOUT = 5.0


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

    @classmethod
    def pair(
        cls,
        host: str,
        gateway_uuid: UUID,
        *,
        timeout: float,
        port: int = DEFAULT_PORT,
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
                encode_pairing(app_uuid, proposed_remote_uuid, PAIRING_NICKNAME),
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

    def read_snapshot(self, *, refresh_properties: bool) -> GatewaySnapshot:
        """Read a complete state snapshot from the gateway."""
        try:
            version = self._version or self._read_version()
            run_state = self._read_run_state()
            rooms = self._read_rooms()
            if refresh_properties:
                rooms = self._read_device_properties(rooms)
            return GatewaySnapshot(version, run_state, rooms)
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

    def set_level(self, room_id: int, level: int) -> GatewaySnapshot:
        """Set one room's active fan level and read back the result."""
        if level not in (0, 1, 2, 3, 4):
            raise ValueError("ventilation level must be between 0 and 4")
        try:
            run_state = self._read_run_state()
            rooms = self._read_rooms()
            room = next((item for item in rooms if item.room_id == room_id), None)
            if room is None:
                raise ProtocolError("room is no longer available")
            if level == 0 and not any(
                device.level_zero_supported for device in room.devices
            ):
                raise ValueError("this ventilation unit does not support level 0")
            session = self._connected_session()
            session.request(
                OperationType.SET_ROOM_VALUE_REQUEST,
                OperationType.SET_ROOM_VALUE_CONFIRM,
                encode_room_level(room, run_state.temperature_mode, level),
            )
            return self.read_snapshot(refresh_properties=False)
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

    def _read_rooms(self) -> tuple[Room, ...]:
        response = self._connected_session().request(
            OperationType.ROOMS_REQUEST, OperationType.ROOMS_CONFIRM
        )
        return decode_rooms(response.body)

    def _read_device_properties(self, rooms: tuple[Room, ...]) -> tuple[Room, ...]:
        devices = [
            device for room in rooms for device in room.devices if is_supported(device)
        ]
        if not devices:
            return rooms

        try:
            for device in devices:
                for index, spec in enumerate(PROPERTY_SPECS):
                    if index == 0:
                        command = PropertySequenceCommand.START
                    elif index == len(PROPERTY_SPECS) - 1:
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

            expected = {
                (device.device_id, spec.key)
                for device in devices
                for spec in PROPERTY_SPECS
            }
            deadline = time.monotonic() + PROPERTY_SETTLE_TIMEOUT
            while True:
                refreshed = self._read_rooms()
                found = {
                    (device.device_id, value.key.value_identity)
                    for room in refreshed
                    for device in room.devices
                    for value in device.properties
                    if value.value is not None
                }
                if expected.issubset(found) or time.monotonic() >= deadline:
                    return refreshed
                time.sleep(0.2)
        except (ProtocolError, TransportError):
            # Device-specific telemetry is optional. Preserve the core room state
            # and reconnect on the next coordinator refresh.
            self.close()
            return rooms
