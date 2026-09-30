"""Verified device profiles and value decoders."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    PRODUCT_VARIANT_COMFOAIR_70,
    PRODUCT_VARIANT_COMFOSPOT_50,
    SUPPORTED_PRODUCT_TYPE,
    SUPPORTED_VARIANTS,
)
from .models import AttachedDevice, PropertyKey


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


def supports_sensor_status(device: AttachedDevice) -> bool:
    """Limit the status interpretation to the physically checked profile."""
    return (
        device.product_type == SUPPORTED_PRODUCT_TYPE
        and device.product_variant == PRODUCT_VARIANT_COMFOSPOT_50
    )


def property_specs_for_device(device: AttachedDevice) -> tuple[PropertySpec, ...]:
    """Return only the property reads supported by this device profile."""
    if supports_sensor_status(device):
        return PROPERTY_SPECS + SENSOR_STATUS_SPECS
    return PROPERTY_SPECS


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
