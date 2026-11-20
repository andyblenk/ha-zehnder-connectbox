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
# The unit's error log, shown on its page in the official app as up to three
# entries "PRID_SINGLE_VENT_UNIT.Error.<code>" with the operating hour at which
# the error occurred: 37.n.1 is the code as a signed byte (133 shows as -123)
# and 37.n.2 the operating hour. A ComfoSpot 50 without an active fault and
# without a warning in the app reported three entries, so the log is history,
# not the list of active faults (device field 10).
ERROR_CODE_1 = PropertySpec((37, 0, 1), 1)
ERROR_CODE_2 = PropertySpec((37, 1, 1), 1)
ERROR_CODE_3 = PropertySpec((37, 2, 1), 1)
ERROR_CODE_SPECS = (ERROR_CODE_1, ERROR_CODE_2, ERROR_CODE_3)
ERROR_HOURS_SPECS = (
    PropertySpec((37, 0, 2), 2),
    PropertySpec((37, 1, 2), 2),
    PropertySpec((37, 2, 2), 2),
)

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

# Role of a unit in the app's summer ventilation, which ventilates with
# outdoor air without heat recovery. Matched against the room settings in the
# official app for five ComfoSpot 50 units.
SUMMER_VENTILATION_ROLE = PropertySpec((38, 0, 11), 1)
SUMMER_VENTILATION_ROLES = {
    0: "supply_and_exhaust",
    1: "supply",
    2: "exhaust",
}
# Settings on the unit's page in the official app, matched on one ComfoSpot 50:
# the runtime of level 1 per hour in minutes (15, 30, 45 or 60; the app's
# 45 min showed as 45), and the bath function, which the app shows as
# "gesperrt" (locked) for 0 and does not let the user change.
LEVEL_ONE_RUNTIME = PropertySpec((38, 0, 9), 1)
LEVEL_ONE_RUNTIME_VALUES = (15, 30, 45, 60)
BATH_FUNCTION = PropertySpec((38, 0, 10), 1)
BATH_FUNCTION_STATES = {0: "locked", 1: "enabled"}
# Read in their own sequence, so a unit that rejects them keeps its telemetry.
# This three-item sequence was read successfully from five ComfoSpot 50 units;
# a single-item read sequence has not been verified.
OPTIONAL_PROPERTY_SPECS = (
    LEVEL_ONE_RUNTIME,
    BATH_FUNCTION,
    SUMMER_VENTILATION_ROLE,
)
# The sensors used by the unit's automatic (sensor) operation, shown read-only
# in the app as "Betriebs-Modus Automatik"; "beide" (both) was 3. Read with
# its neighbour, the two-item sequence the official app reads, and the error
# log's operating hours, both in their own slow sequences.
AUTOMATIC_SENSORS = PropertySpec((38, 0, 14), 1)
AUTOMATIC_SENSOR_STATES = {1: "humidity", 2: "air_quality", 3: "both"}
SETTINGS_PROPERTY_SPECS = (PropertySpec((38, 0, 13), 1), AUTOMATIC_SENSORS)

SUMMER_VENTILATION_ROLE_VALUES = {
    name: value for value, name in SUMMER_VENTILATION_ROLES.items()
}

# Boost of a room. The app offers a duration of 15 to 120 minutes per room in
# steps of 15 and starts and stops the boost on its ventilation page. A
# duration written as room field 22 showed in the app; starting and stopping
# uses the app's room-value update (protocol.ROOM_VALUE_BOOST_UNTIL).
BOOST_DURATION_MINIMUM = 15
BOOST_DURATION_MAXIMUM = 120
BOOST_DURATION_STEP = 15

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


def supports_summer_ventilation(rooms: tuple[Room, ...]) -> bool:
    """Permit a global write only when every attached unit is validated."""
    devices = [device for room in rooms for device in room.devices]
    return bool(devices) and all(
        device.summer_ventilation_available is True
        and device.product_type == SUPPORTED_PRODUCT_TYPE
        and device.product_variant == PRODUCT_VARIANT_COMFOSPOT_50
        for device in devices
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


def optional_property_specs_for_device(
    device: AttachedDevice,
) -> tuple[PropertySpec, ...]:
    """Return slowly changing settings read for the checked profile only."""
    return OPTIONAL_PROPERTY_SPECS if supports_sensor_status(device) else ()


# Whether the exhaust fan is enabled (1) or switched off (0). On a ComfoSpot 50,
# supply-only operation switched on at the unit's control panel set it to 0,
# and switching the operation off set it back to 1; the exhaust fan stood still
# in between. The ConnectBox confirms a write of this value, but the unit does
# not change, so it is only read.
EXHAUST_FAN_ENABLED = PropertySpec((38, 0, 5), 1)
# Read with every property refresh in its own sequence after the core
# telemetry, like the optional settings. A unit that rejects it keeps its
# telemetry and is not asked again until the integration is reloaded. This
# three-item sequence was read successfully from five ComfoSpot 50 units; the
# two neighbouring values are requested only to keep that verified sequence.
FAN_STATE_PROPERTY_SPECS = (
    EXHAUST_FAN_ENABLED,
    PropertySpec((38, 0, 6), 1),
    PropertySpec((38, 0, 8), 1),
)


def settings_property_specs_for_device(
    device: AttachedDevice,
) -> tuple[PropertySpec, ...]:
    """Return the automatic-operation settings sequence for the checked profile."""
    return SETTINGS_PROPERTY_SPECS if supports_sensor_status(device) else ()


def error_hours_property_specs_for_device(
    device: AttachedDevice,
) -> tuple[PropertySpec, ...]:
    """Return the error log's operating-hour sequence for the checked profile."""
    return ERROR_HOURS_SPECS if supports_sensor_status(device) else ()


def fan_state_property_specs_for_device(
    device: AttachedDevice,
) -> tuple[PropertySpec, ...]:
    """Return the fan-state sequence for the checked profile only."""
    return FAN_STATE_PROPERTY_SPECS if supports_sensor_status(device) else ()


def supply_only_operation(device: AttachedDevice) -> bool | None:
    """Interpret the exhaust fan's enable flag; leave other values unknown."""
    if not supports_sensor_status(device):
        return None
    value = EXHAUST_FAN_ENABLED.value(device)
    if value == 0:
        return True
    if value == 1:
        return False
    return None


def summer_ventilation_role(device: AttachedDevice) -> str | None:
    """Return the unit's role in the summer ventilation, if reported."""
    if not supports_sensor_status(device):
        return None
    value = SUMMER_VENTILATION_ROLE.value(device)
    return SUMMER_VENTILATION_ROLES.get(value) if value is not None else None


def summer_ventilation_role_key(device: AttachedDevice) -> PropertyKey | None:
    """Return the key under which the unit reports its role, for a write.

    The role is written with the key the gateway reported it under, like the
    filter runtime, and only for a unit that reports a known role.
    """
    if summer_ventilation_role(device) is None:
        return None
    return _reported_key(device, SUMMER_VENTILATION_ROLE.key)


def supports_boost(room: Room, device: AttachedDevice) -> bool:
    """Offer the boost for rooms like the ones it was checked on.

    The room must report its boost duration, and the unit must be a
    ComfoSpot 50, the profile on which the boost was checked against the app.
    """
    return supports_sensor_status(device) and room.boost_duration is not None


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
    be told apart. A device owner confirmed the same reading format on a
    ComfoAir 70 with a VOC board against the official app.
    """
    if not is_supported(device):
        return None
    return device.reading(sensor_type)


def supports_sensor_mode(room: Room, device: AttachedDevice) -> bool:
    """Offer sensor-controlled operation only for a unit with a sensor board.

    A room that already uses sensor operation proves the board even before the
    periodically refreshed sensor-status properties have been read.
    ComfoAir 70 shares the same sensor-mode level, but its
    board detection uses reported readings or existing room sensor mode only;
    sensor-status property reads remain restricted to ComfoSpot 50.
    """
    return is_supported(device) and (
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
    """Return whether the gateway reports an active fault of the unit.

    Only the device's error list counts; the unit's error log (class 37) is
    history, see ERROR_CODE_SPECS.
    """
    return any(code != 0 for code in device.errors)


def error_log(device: AttachedDevice) -> list[dict[str, int | None]]:
    """Return the unit's logged errors with the operating hour, newest first.

    Codes are signed bytes as the app shows them; empty entries (code 0) are
    left out.
    """
    entries: list[dict[str, int | None]] = []
    for code_spec, hours_spec in zip(ERROR_CODE_SPECS, ERROR_HOURS_SPECS, strict=True):
        raw = device.property_bytes(code_spec.key)
        if not raw:
            continue
        code = int.from_bytes(raw[:1], "little", signed=True)
        if code == 0:
            continue
        hours_raw = device.property_bytes(hours_spec.key)
        entries.append(
            {
                "code": code,
                "operating_hours": (
                    int.from_bytes(hours_raw[:4], "little") if hours_raw else None
                ),
            }
        )
    return entries


def level_one_runtime(device: AttachedDevice) -> int | None:
    """Return the runtime of level 1 per hour in minutes, if reported."""
    if not supports_sensor_status(device):
        return None
    value = LEVEL_ONE_RUNTIME.value(device)
    return value if value in LEVEL_ONE_RUNTIME_VALUES else None


def level_one_runtime_key(device: AttachedDevice) -> PropertyKey | None:
    """Return the key under which the unit reports the runtime, for a write."""
    if level_one_runtime(device) is None:
        return None
    return _reported_key(device, LEVEL_ONE_RUNTIME.key)


def bath_function(device: AttachedDevice) -> str | None:
    """Return the bath function state as the app shows it, if known."""
    if not supports_sensor_status(device):
        return None
    value = BATH_FUNCTION.value(device)
    return BATH_FUNCTION_STATES.get(value) if value is not None else None


def automatic_sensors(device: AttachedDevice) -> str | None:
    """Return which sensors the automatic operation uses, if known."""
    if not supports_sensor_status(device):
        return None
    value = AUTOMATIC_SENSORS.value(device)
    return AUTOMATIC_SENSOR_STATES.get(value) if value is not None else None


def _reported_key(
    device: AttachedDevice, identity: tuple[int, int, int]
) -> PropertyKey | None:
    """Return the full key of a property the gateway reported with a value."""
    for prop in device.properties:
        if prop.key.value_identity == identity and prop.value:
            return prop.key
    return None


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
