"""Minimal Protocol Buffers wire helpers used by the ConnectBox protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ProtobufDecodeError(ValueError):
    """Raised when a message cannot be decoded safely."""


class WireType(IntEnum):
    """Protocol Buffers wire types used by the gateway."""

    VARINT = 0
    FIXED_64 = 1
    BYTES = 2
    FIXED_32 = 5


@dataclass(frozen=True, slots=True)
class Field:
    """A decoded wire field."""

    number: int
    wire_type: WireType
    value: int | bytes


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a Protocol Buffers varint."""
    if value < 0:
        raise ValueError("varint values cannot be negative")

    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field_key(number: int, wire_type: WireType) -> bytes:
    if number < 1:
        raise ValueError("field numbers must be positive")
    return encode_varint((number << 3) | int(wire_type))


def encode_uint(number: int, value: int) -> bytes:
    """Encode an unsigned integer field."""
    return _field_key(number, WireType.VARINT) + encode_varint(value)


def encode_bytes(number: int, value: bytes) -> bytes:
    """Encode a length-delimited field."""
    return _field_key(number, WireType.BYTES) + encode_varint(len(value)) + value


def encode_string(number: int, value: str) -> bytes:
    """Encode a UTF-8 string field."""
    return encode_bytes(number, value.encode())


def decode_fields(message: bytes) -> tuple[Field, ...]:
    """Decode the supported wire types without interpreting message schemas."""
    fields: list[Field] = []
    offset = 0
    while offset < len(message):
        key, offset = _decode_varint(message, offset)
        number = key >> 3
        if number == 0:
            raise ProtobufDecodeError("invalid field number 0")
        try:
            wire_type = WireType(key & 0x07)
        except ValueError as err:
            raise ProtobufDecodeError("unsupported wire type") from err

        if wire_type is WireType.VARINT:
            value, offset = _decode_varint(message, offset)
        elif wire_type is WireType.BYTES:
            length, offset = _decode_varint(message, offset)
            end = offset + length
            if end > len(message):
                raise ProtobufDecodeError("truncated length-delimited field")
            value = message[offset:end]
            offset = end
        elif wire_type is WireType.FIXED_64:
            end = offset + 8
            if end > len(message):
                raise ProtobufDecodeError("truncated fixed64 field")
            value = message[offset:end]
            offset = end
        else:
            end = offset + 4
            if end > len(message):
                raise ProtobufDecodeError("truncated fixed32 field")
            value = message[offset:end]
            offset = end
        fields.append(Field(number, wire_type, value))
    return tuple(fields)


def _decode_varint(message: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(message):
            raise ProtobufDecodeError("truncated varint")
        current = message[offset]
        offset += 1
        value |= (current & 0x7F) << shift
        if current < 0x80:
            return value, offset
    raise ProtobufDecodeError("varint is longer than 10 bytes")


def uint_value(fields: tuple[Field, ...], number: int) -> int | None:
    """Return the first varint value for a field number."""
    for field in fields:
        if field.number == number and field.wire_type is WireType.VARINT:
            return int(field.value)
    return None


def bytes_value(fields: tuple[Field, ...], number: int) -> bytes | None:
    """Return the first byte value for a field number."""
    for field in fields:
        if field.number == number and field.wire_type is WireType.BYTES:
            return bytes(field.value)
    return None


def uint_values(fields: tuple[Field, ...], number: int) -> tuple[int, ...]:
    """Return every varint value for a field number."""
    return tuple(
        int(field.value)
        for field in fields
        if field.number == number and field.wire_type is WireType.VARINT
    )


def bytes_values(fields: tuple[Field, ...], number: int) -> tuple[bytes, ...]:
    """Return every byte value for a field number."""
    return tuple(
        bytes(field.value)
        for field in fields
        if field.number == number and field.wire_type is WireType.BYTES
    )
