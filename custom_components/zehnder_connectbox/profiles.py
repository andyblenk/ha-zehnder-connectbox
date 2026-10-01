"""Verified device profiles and value decoders."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    PRODUCT_VARIANT_COMFOAIR_70,
    PRODUCT_VARIANT_COMFOSPOT_50,
    SENSOR_MODE_LEVEL,
    SUPPORTED_PRODUCT_TYPE,
    SUPPORTED_VARIANTS,
)
from .models import AttachedDevice, PropertyKey, Room


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """One device-specific property required by an exposed entity."""

    key: tuple[int, int, int]
    length: int
    signed: bool = False
    scale: int = 1

    def request_key(self, product_type: int) -> PropertyKey:
        """Build the full read identity used by the gateway."""
        return PropertyKey(product_type, 255, 0, *self.key)

    def value(self, device: AttachedDevice) -> int | float | None:
        """Decode the value from a device snapshot."""
        raw = device.property_bytes(self.key)
        if raw is None or len(raw) < self.length:
            return None
        value = int.from_bytes(raw[: self.length], "little", signed=self.signed)
        return value / self.scale if self.scale != 1 else value


EXTRACT_AIR_TEMPERATURE = PropertySpec((25, 0, 1), 2, True, 1000)
INCOMING_AIR_TEMPERATURE = PropertySpec((25, 1, 1), 2, True, 1000)
EXTRACT_AIR_SENSOR_STATUS = PropertySpec((25, 0, 4), 1)
INCOMING_AIR_SENSOR_STATUS = PropertySpec((25, 1, 4), 1)
HUMIDITY_SENSOR_STATUS = PropertySpec((26, 0, 1), 1)
CO2_SENSOR_STATUS = PropertySpec((39, 0, 1), 1)
EXHAUST_FAN_SPEED = PropertySpec((38, 0, 3), 2)
SUPPLY_FAN_SPEED = PropertySpec((38, 1, 3), 2)
FILTER_RUNTIME = PropertySpec((38, 0, 15), 2)
FILTER_REMAINING = PropertySpec((38, 0, 16), 2)
FILTER_MAXIMUM = PropertySpec((38, 0, 17), 2)
FILTER_PROPERTY_SPECS = (FILTER_RUNTIME, FILTER_REMAINING, FILTER_MAXIMUM)
ERROR_CODE_1 = PropertySpec((37, 0, 1), 1)
ERROR_CODE_2 = PropertySpec((37, 1, 1), 1)
ERROR_CODE_3 = PropertySpec((37, 2, 1), 1)

PROPERTY_SPECS = (
    EXTRACT_AIR_TEMPERATURE,
    INCOMING_AIR_TEMPERATURE,
    EXHAUST_FAN_SPEED,
    SUPPLY_FAN_SPEED,
    FILTER_RUNTIME,
    FILTER_REMAINING,
    FILTER_MAXIMUM,
    ERROR_CODE_1,
    ERROR_CODE_2,
    ERROR_CODE_3,
)

SENSOR_STATUS_SPECS = (
    EXTRACT_AIR_SENSOR_STATUS,
    INCOMING_AIR_SENSOR_STATUS,
    HUMIDITY_SENSOR_STATUS,
    CO2_SENSOR_STATUS,
)

# Sensor types in a unit's sensor list (device field 8 of the room model).
SENSOR_TYPE_TEMPERATURE = 1
SENSOR_TYPE_HUMIDITY = 2
SENSOR_TYPE_CO2 = 3
KNOWN_SENSOR_TYPES = (SENSOR_TYPE_TEMPERATURE, SENSOR_TYPE_HUMIDITY, SENSOR_TYPE_CO2)


def supports_sensor_status(device: AttachedDevice) -> bool:
    """Limit the status interpretation to the physically checked profile."""
    return (
        device.product_type == SUPPORTED_PRODUCT_TYPE
        and device.product_variant == PRODUCT_VARIANT_COMFOSPOT_50
    )


def property_specs_for_device(
    device: AttachedDevice, *, include_filter_properties: bool = True
) -> tuple[PropertySpec, ...]:
    """Return only the property reads supported by this device profile."""
    specs = (
        PROPERTY_SPECS + SENSOR_STATUS_SPECS
        if supports_sensor_status(device)
        else PROPERTY_SPECS
    )
    if include_filter_properties:
        return specs
    return tuple(spec for spec in specs if spec not in FILTER_PROPERTY_SPECS)


def sensor_available(device: AttachedDevice, status_spec: PropertySpec) -> bool | None:
    """Interpret the observed 0/1 status values; leave other values unknown."""
    if not supports_sensor_status(device):
        return None
    status = status_spec.value(device)
    if status == 0:
        return True
    if status == 1:
        return False
    return None


def temperature_value(
    device: AttachedDevice, temperature_spec: PropertySpec, status_spec: PropertySpec
) -> int | float | None:
    """Suppress a temperature when its sensor reports unavailable."""
    if sensor_available(device, status_spec) is False:
        return None
    return temperature_spec.value(device)


def has_sensor_board(device: AttachedDevice) -> bool:
    """Return whether the unit reports a sensor of the optional sensor board.

    The base unit only has its two temperature sensors. Each sensor board
    (humidity, CO2, or VOC) adds the humidity sensor, and only these boards
    allow sensor-controlled operation. A humidity or CO2 reading in the unit's
    sensor list, or an available humidity or CO2 sensor status, counts.
    """
    return (
        sensor_available(device, HUMIDITY_SENSOR_STATUS) is True
        or sensor_available(device, CO2_SENSOR_STATUS) is True
        or board_reading(device, SENSOR_TYPE_HUMIDITY) is not None
        or board_reading(device, SENSOR_TYPE_CO2) is not None
    )


def room_uses_sensor_mode(room: Room) -> bool:
    """Return whether the room uses sensor operation now or in a situation."""
    return room.target_level == SENSOR_MODE_LEVEL or any(
        value.level == SENSOR_MODE_LEVEL for value in room.ventilation
    )


def board_reading(device: AttachedDevice, sensor_type: int) -> int | None:
    """Return a sensor-board reading from the unit's sensor list.

    Each unit reports a list of (sensor type, value) pairs in the room model:
    temperature in 0.1 °C, relative humidity in %, and CO2 in ppm. The types
    were matched against six ComfoSpot 50 units with sensor boards, one of
    them a VOC board. The VOC board reports its CO2 equivalent under the CO2
    type and an available CO2 sensor as well, so a CO2 and a VOC board cannot
    be told apart.
    """
    if not supports_sensor_status(device):
        return None
    return device.reading(sensor_type)


def supports_sensor_mode(room: Room, device: AttachedDevice) -> bool:
    """Offer sensor-controlled operation only for a unit with a sensor board.

    A room that already uses sensor operation proves the board even before the
    periodically refreshed sensor-status properties have been read.
    """
    return supports_sensor_status(device) and (
        has_sensor_board(device) or room_uses_sensor_mode(room)
    )


def is_supported(device: AttachedDevice) -> bool:
    """Return whether writes are allowed for this exact product profile."""
    return (
        device.product_type == SUPPORTED_PRODUCT_TYPE
        and device.product_variant in SUPPORTED_VARIANTS
    )


def product_name(device: AttachedDevice) -> str:
    """Return the precise model name when known."""
    if device.product_type != SUPPORTED_PRODUCT_TYPE:
        return f"Unknown product type {device.product_type}"
    if device.product_variant == PRODUCT_VARIANT_COMFOSPOT_50:
        return "ComfoSpot 50"
    if device.product_variant == PRODUCT_VARIANT_COMFOAIR_70:
        return "ComfoAir 70"
    return f"Single-room ventilation unit (variant {device.product_variant})"


def has_fault(device: AttachedDevice) -> bool:
    """Return whether any reported device fault is active."""
    if any(code != 0 for code in device.errors):
        return True
    return any(
        (spec.value(device) or 0) != 0
        for spec in (ERROR_CODE_1, ERROR_CODE_2, ERROR_CODE_3)
    )


def has_filter_warning(device: AttachedDevice) -> bool | None:
    """Return a reported or safely derived filter warning."""
    if device.filter_warning is not None:
        return device.filter_warning
    remaining = FILTER_REMAINING.value(device)
    if remaining is not None:
        return remaining <= 0
    runtime = FILTER_RUNTIME.value(device)
    maximum = FILTER_MAXIMUM.value(device)
    if runtime is not None and maximum is not None:
        return runtime >= maximum
    return None


def format_version(value: int | None) -> str | None:
    """Format a packed Zehnder ComfoNet version value."""
    if value is None:
        return None
    state = (value >> 30) & 0x03
    prefix = {1: "D", 2: "P", 3: "R"}.get(state, "")
    major = (value >> 20) & 0x3FF
    minor = (value >> 10) & 0x3FF
    patch = value & 0x3FF
    return f"{prefix}{major}.{minor}.{patch}"
