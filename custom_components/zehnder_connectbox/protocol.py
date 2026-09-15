"""Independent implementation of the ConnectBox application protocol."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from .models import (
    AttachedDevice,
    PropertyKey,
    PropertyValue,
    Room,
    RunState,
    VentilationValue,
    VersionInfo,
)
from .protobuf import (
    Field,
    WireType,
    bytes_value,
    bytes_values,
    decode_fields,
    encode_bytes,
    encode_string,
    encode_uint,
    encode_varint,
    uint_value,
    uint_values,
)

MAX_FRAME_PAYLOAD = 8191
MAX_OPERATION_SIZE = 1023
FRAME_HEADER_SIZE = 34


class ProtocolError(ConnectionError):
    """Raised for invalid or unexpected gateway data."""


class IncompleteFrame(ProtocolError):
    """Raised when more bytes are needed for a complete frame."""


class OperationType(IntEnum):
    """Operations used by this integration."""

    VERSION_REQUEST = 18
    VERSION_CONFIRM = 68
    REMOTE_ACCESS_REQUEST = 140
    REMOTE_ACCESS_CONFIRM = 142
    ROOMS_REQUEST = 194
    ROOMS_CONFIRM = 195
    SET_ROOM_REQUEST = 203
    SET_ROOM_CONFIRM = 204
    SET_ROOM_VALUE_REQUEST = 205
    SET_ROOM_VALUE_CONFIRM = 206
    RUN_STATE_REQUEST = 240
    RUN_STATE_CONFIRM = 241
    SET_RUN_STATE_REQUEST = 243
    SET_RUN_STATE_CONFIRM = 244
    PAIR_REQUEST = 293
    PAIR_CONFIRM = 294
    SET_DEVICE_PROPERTIES_REQUEST = 344
    SET_DEVICE_PROPERTIES_CONFIRM = 345
    DEVICE_PROPERTIES_REQUEST = 346
    DEVICE_PROPERTIES_CONFIRM = 347


class PropertySequenceCommand(IntEnum):
    """Position of a request in a device-property sequence."""

    START = 0
    CONTINUE = 1
    FINISH = 2


@dataclass(frozen=True, slots=True)
class Operation:
    """Decoded operation envelope."""

    type: int
    result: int | None = None
    description: str | None = None
    reference: int | None = None

    def encode(self) -> bytes:
        message = bytearray(encode_uint(1, self.type))
        if self.result is not None:
            message.extend(encode_uint(2, self.result))
        if self.description:
            message.extend(encode_string(3, self.description))
        if self.reference is not None:
            message.extend(encode_uint(4, self.reference))
        return bytes(message)

    @classmethod
    def decode(cls, message: bytes) -> Operation:
        fields = decode_fields(message)
        description = bytes_value(fields, 3)
        return cls(
            type=uint_value(fields, 1) or 0,
            result=uint_value(fields, 2),
            description=(
                description.decode(errors="replace")
                if description is not None
                else None
            ),
            reference=uint_value(fields, 4),
        )


@dataclass(frozen=True, slots=True)
class Frame:
    """One framed request or response on the TLS stream."""

    source: UUID
    destination: UUID
    operation: bytes
    body: bytes = b""

    def encode(self) -> bytes:
        operation_length = len(self.operation)
        if operation_length > MAX_OPERATION_SIZE:
            raise ProtocolError("operation envelope is too large")
        payload_length = FRAME_HEADER_SIZE + operation_length + len(self.body)
        if payload_length > MAX_FRAME_PAYLOAD:
            raise ProtocolError("frame payload is too large")
        return b"".join(
            (
                struct.pack("!I", payload_length),
                self.source.bytes,
                self.destination.bytes,
                struct.pack("!H", operation_length),
                self.operation,
                self.body,
            )
        )

    @classmethod
    def decode(cls, buffer: bytes) -> tuple[Frame, bytes]:
        if len(buffer) < 4:
            raise IncompleteFrame
        payload_length = struct.unpack_from("!I", buffer)[0]
        if not FRAME_HEADER_SIZE <= payload_length <= MAX_FRAME_PAYLOAD:
            raise ProtocolError("invalid frame payload length")
        frame_end = payload_length + 4
        if len(buffer) < frame_end:
            raise IncompleteFrame
        operation_length = struct.unpack_from("!H", buffer, 36)[0]
        if operation_length > MAX_OPERATION_SIZE:
            raise ProtocolError("invalid operation envelope length")
        operation_end = 38 + operation_length
        if operation_end > frame_end:
            raise ProtocolError("operation envelope exceeds the frame")
        return (
            cls(
                source=UUID(bytes=buffer[4:20]),
                destination=UUID(bytes=buffer[20:36]),
                operation=buffer[38:operation_end],
                body=buffer[operation_end:frame_end],
            ),
            buffer[frame_end:],
        )


def encode_pairing(app_uuid: UUID, remote_uuid: UUID, nickname: str) -> bytes:
    """Build a local application-pairing request."""
    if not 1 <= len(nickname.encode()) <= 32:
        raise ValueError("pairing nickname must contain 1 to 32 UTF-8 bytes")
    app = encode_bytes(1, app_uuid.bytes) + encode_string(2, nickname)
    return encode_bytes(1, app) + encode_bytes(2, remote_uuid.bytes)


def decode_pairing(message: bytes) -> tuple[int, UUID | None]:
    """Return the assigned application ID and remote UUID."""
    fields = decode_fields(message)
    app_message = _required_bytes(fields, 1, "pairing result")
    app_fields = decode_fields(app_message)
    app_id = _required_uint(app_fields, 1, "application ID")
    remote = bytes_value(fields, 2)
    if remote is not None and len(remote) != 16:
        raise ProtocolError("invalid remote UUID in pairing response")
    return app_id, UUID(bytes=remote) if remote is not None else None


def decode_version(message: bytes) -> VersionInfo:
    """Decode the non-identifying gateway version values."""
    fields = decode_fields(message)
    return VersionInfo(
        system_version=uint_value(fields, 1),
        comfonet_version=uint_value(fields, 3),
        property_list_version=uint_value(fields, 4),
        zone_state_list_version=uint_value(fields, 5),
        connectbox_version=uint_value(fields, 6),
    )


def decode_run_state(message: bytes) -> RunState:
    """Decode the current system state."""
    outer = decode_fields(message)
    fields = decode_fields(_required_bytes(outer, 1, "run state"))
    standby = uint_value(fields, 7)
    summer = uint_value(fields, 12)
    return RunState(
        run_mode=_required_uint(fields, 1, "run mode"),
        temperature_mode=_required_uint(fields, 2, "temperature mode"),
        standby=bool(standby) if standby is not None else None,
        standby_mode=uint_value(fields, 8),
        summer_ventilation=bool(summer) if summer is not None else None,
        errors=uint_values(fields, 11),
    )


def encode_run_state(run_mode: int, user_location: int = 1) -> bytes:
    """Build a system run-mode update."""
    return encode_uint(1, run_mode) + encode_uint(2, user_location)


def decode_rooms(message: bytes) -> tuple[Room, ...]:
    """Decode the complete room and attached-device model."""
    fields = decode_fields(message)
    return tuple(_decode_room(value) for value in bytes_values(fields, 1))


def _decode_room(message: bytes) -> Room:
    fields = decode_fields(message)
    raw_name = bytes_value(fields, 2)
    return Room(
        room_id=_required_uint(fields, 1, "room ID"),
        name=raw_name.decode(errors="replace") if raw_name else "Ventilation",
        room_type=uint_value(fields, 3),
        target_level=uint_value(fields, 21),
        ventilation=tuple(
            _decode_ventilation(value) for value in bytes_values(fields, 19)
        ),
        devices=tuple(_decode_device(value) for value in bytes_values(fields, 8)),
        raw=message,
    )


def _decode_ventilation(message: bytes) -> VentilationValue:
    fields = decode_fields(message)
    return VentilationValue(
        temperature_mode=_required_uint(fields, 1, "temperature mode"),
        level=_required_uint(fields, 2, "ventilation level"),
    )


def _decode_device(message: bytes) -> AttachedDevice:
    fields = decode_fields(message)
    level_zero = uint_value(fields, 20)
    filter_warning = uint_value(fields, 31)
    return AttachedDevice(
        device_id=_required_uint(fields, 1, "device ID"),
        product_type=_required_uint(fields, 5, "product type"),
        product_variant=uint_value(fields, 110),
        hardware_version=uint_value(fields, 102),
        software_version=uint_value(fields, 103),
        battery_state=uint_value(fields, 6),
        signal_strength=uint_value(fields, 7),
        errors=uint_values(fields, 10),
        level_zero_supported=bool(level_zero) if level_zero is not None else None,
        filter_warning=(bool(filter_warning) if filter_warning is not None else None),
        filter_runtime=uint_value(fields, 41),
        filter_maximum=uint_value(fields, 42),
        properties=tuple(
            _decode_property(value) for value in bytes_values(fields, 101)
        ),
    )


def _decode_property(message: bytes) -> PropertyValue:
    fields = decode_fields(message)
    key_fields = decode_fields(_required_bytes(fields, 1, "property key"))
    return PropertyValue(
        key=PropertyKey(
            product_type=_required_uint(key_fields, 1, "product type"),
            hardware_version=_required_uint(key_fields, 2, "hardware version"),
            minimum_software_version=_required_uint(
                key_fields, 3, "minimum software version"
            ),
            class_id=_required_uint(key_fields, 4, "property class"),
            instance_id=_required_uint(key_fields, 5, "property instance"),
            property_id=_required_uint(key_fields, 6, "property ID"),
        ),
        value=bytes_value(fields, 2),
    )


def encode_room_level(room: Room, temperature_mode: int, level: int) -> bytes:
    """Update one active mode while preserving all other room mode values."""
    if level not in (0, 1, 2, 3, 4):
        raise ValueError("ventilation level must be between 0 and 4")
    values = {value.temperature_mode: value.level for value in room.ventilation}
    if not values:
        raise ProtocolError("room does not expose per-mode ventilation values")
    values[temperature_mode] = level
    room_value = bytearray(encode_uint(1, room.room_id))
    for mode, current_level in sorted(values.items()):
        ventilation = encode_uint(1, mode) + encode_uint(2, current_level)
        room_value.extend(encode_bytes(12, ventilation))
    return encode_bytes(1, bytes(room_value))


def encode_property_request(
    command: PropertySequenceCommand, device_id: int, key: PropertyKey
) -> bytes:
    """Build one item in a bounded device-property read sequence."""
    key_message = b"".join(
        (
            encode_uint(1, key.product_type),
            encode_uint(2, key.hardware_version),
            encode_uint(3, key.minimum_software_version),
            encode_uint(4, key.class_id),
            encode_uint(5, key.instance_id),
            encode_uint(6, key.property_id),
        )
    )
    return b"".join(
        (
            encode_uint(1, int(command)),
            encode_uint(2, device_id),
            encode_bytes(3, key_message),
        )
    )


def encode_filter_reset_room(room: Room, device_id: int) -> bytes:
    """Build the confirmed room update used to clear a device filter alarm."""
    if not room.raw:
        raise ProtocolError("room does not contain its original gateway data")

    device_found = False
    room_value = bytearray()
    for field in decode_fields(room.raw):
        if field.number != 8 or field.wire_type is not WireType.BYTES:
            room_value.extend(_encode_field(field))
            continue

        device_fields = decode_fields(bytes(field.value))
        current_device_id = uint_value(device_fields, 1)
        is_target = current_device_id == device_id
        device_found |= is_target

        # The official client does not echo these read-only alarm/runtime
        # values in a room write. A present false filter flag is the supported
        # acknowledgement for the selected unit.
        writable_fields = tuple(
            item for item in device_fields if item.number not in (30, 31, 41)
        )
        device_value = bytearray(_encode_fields(writable_fields))
        if is_target:
            device_value.extend(encode_uint(31, 0))
        room_value.extend(encode_bytes(8, bytes(device_value)))

    if not device_found:
        raise ProtocolError("device is no longer attached to this room")
    return encode_bytes(1, bytes(room_value))


def encode_property_update(device_id: int, key: PropertyKey, value: bytes) -> bytes:
    """Build a single, confirmed device-property write."""
    if not value:
        raise ValueError("device-property value must not be empty")
    key_message = b"".join(
        (
            encode_uint(1, key.product_type),
            encode_uint(2, key.hardware_version),
            encode_uint(3, key.minimum_software_version),
            encode_uint(4, key.class_id),
            encode_uint(5, key.instance_id),
            encode_uint(6, key.property_id),
        )
    )
    property_value = encode_bytes(1, key_message) + encode_bytes(2, value)
    return b"".join(
        (
            encode_uint(1, int(PropertySequenceCommand.FINISH)),
            encode_uint(2, device_id),
            encode_bytes(3, property_value),
        )
    )


def _encode_fields(fields: tuple[Field, ...]) -> bytes:
    """Re-encode decoded fields while preserving unknown gateway data."""
    return b"".join(_encode_field(field) for field in fields)


def _encode_field(field: Field) -> bytes:
    """Encode one already decoded Protocol Buffers field."""
    if field.wire_type is WireType.VARINT:
        return encode_uint(field.number, int(field.value))
    if field.wire_type is WireType.BYTES:
        return encode_bytes(field.number, bytes(field.value))

    value = bytes(field.value)
    expected_length = 8 if field.wire_type is WireType.FIXED_64 else 4
    if len(value) != expected_length:
        raise ProtocolError("invalid fixed-width Protocol Buffers field")
    return encode_varint((field.number << 3) | int(field.wire_type)) + value


def _required_uint(fields: tuple, number: int, label: str) -> int:
    value = uint_value(fields, number)
    if value is None:
        raise ProtocolError(f"missing {label}")
    return value


def _required_bytes(fields: tuple, number: int, label: str) -> bytes:
    value = bytes_value(fields, number)
    if value is None:
        raise ProtocolError(f"missing {label}")
    return value
