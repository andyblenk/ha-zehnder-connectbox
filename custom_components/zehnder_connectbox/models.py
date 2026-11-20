"""Data models for Zehnder ConnectBox state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from uuid import UUID


class RunMode(IntEnum):
    """ConnectBox operating modes."""

    AUTOMATIC = 1
    MANUAL = 2
    ANTIFREEZE = 3
    OFF = 4


class TemperatureMode(IntEnum):
    """Temperature profiles used by room ventilation values."""

    AWAKE = 0
    ASLEEP = 1
    AWAY = 2
    ANTIFREEZE = 3
    OVERRIDE = 4


@dataclass(frozen=True, slots=True)
class DiscoveredGateway:
    """A ConnectBox found on the local network."""

    host: str
    gateway_uuid: UUID
    name: str | None = None
    serial_number: str | None = None
    gateway_type: int | None = None
    protocol_version: int | None = None


@dataclass(frozen=True, slots=True)
class PairingData:
    """Local identity established during physical pairing."""

    app_uuid: UUID
    remote_uuid: UUID
    app_id: int
    certificate_sha256: str


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Version values reported by the gateway."""

    system_version: int | None
    comfonet_version: int | None
    property_list_version: int | None
    zone_state_list_version: int | None
    connectbox_version: int | None


@dataclass(frozen=True, slots=True)
class DayPlan:
    """Today's daily schedule as the run state reports it.

    Each slot is (minute of the day, situation) with the situations of
    TemperatureMode: switching the app between its winter and summer weekly
    schedules changed the plan from (0 asleep, 480 awake, 1260 asleep) to
    (0 asleep, 480 away, 1350 awake), matching the app's daily schedules.
    """

    plan_id: int
    name: str | None
    slots: tuple[tuple[int, int], ...]
    functions: tuple[ScheduledFunction, ...] = ()
    """Timed functions of the plan; only reported in the schedule list."""


@dataclass(frozen=True, slots=True)
class ScheduledFunction:
    """A timed function of a daily schedule.

    Adding a function for a room in the app (07:00, 20 minutes, the room's
    boost) added one entry with function 2 and the room's ID. The app's summer
    daily schedule holds entries with function 5 and room 0, one hour each,
    for the summer ventilation.
    """

    start_minute: int
    function: int
    duration_minutes: int
    room_id: int
    device_id: int = 0
    """Device of the function; the app's entries all reported 0."""


@dataclass(frozen=True, slots=True)
class WeekPlan:
    """A weekly schedule: one daily schedule per day, Monday to Sunday.

    The app's message names the seven fields MondayId to SundayId.
    """

    plan_id: int
    name: str | None
    days: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Schedules:
    """All daily and weekly schedules stored on the gateway."""

    day_plans: tuple[DayPlan, ...]
    week_plans: tuple[WeekPlan, ...]

    def day_plan(self, plan_id: int | None) -> DayPlan | None:
        """Return a daily schedule by ID."""
        return next((plan for plan in self.day_plans if plan.plan_id == plan_id), None)

    def week_plan(self, plan_id: int | None) -> WeekPlan | None:
        """Return a weekly schedule by ID."""
        return next((plan for plan in self.week_plans if plan.plan_id == plan_id), None)


@dataclass(frozen=True, slots=True)
class RunState:
    """Current system-wide ConnectBox state."""

    run_mode: int
    temperature_mode: int
    standby: bool | None
    standby_mode: int | None
    summer_ventilation: bool | None
    summer_ventilation_end: int | None
    errors: tuple[int, ...]
    week_plan: int | None = None
    """ID of the active weekly schedule.

    With the app's winter schedule active the run state reported 3 here and
    daily schedule 1; with its summer schedule, 1 and daily schedule 2.
    """
    day_plan: DayPlan | None = None
    """Today's daily schedule of the active weekly schedule."""
    standby_start: int | None = None
    """Start of the absence planner's period as Unix time (field 9).

    Saving the app's absence planner for 04.12. 19:15 to 05.12. 19:15 (CET)
    set field 8 to 1 and fields 9 and 10 to these times as Unix time; field 7
    (standbyModeActive in the app) stayed 0 before the period.
    """
    standby_end: int | None = None
    """End of the absence planner's period as Unix time (field 10)."""
    situation_source: int | None = None
    """Why the situation applies (field 3), from the app's enumeration.

    1 according to the schedule, 2 changed by the user, 3 no schedule runs,
    4 presence switched to at home. Tapping the house on the app's start
    screen in schedule operation changed it from 1 to 2 and back.
    """

    @property
    def absence_planned(self) -> bool | None:
        """Return whether the absence planner is switched on."""
        return bool(self.standby_mode) if self.standby_mode is not None else None

    def summer_ventilation_running(self, now: float) -> bool | None:
        """Interpret the reported flag together with its expiry time."""
        if self.summer_ventilation is None:
            return None
        if not self.summer_ventilation:
            return False
        if self.summer_ventilation_end is None:
            return None
        return self.summer_ventilation_end > now


@dataclass(frozen=True, slots=True)
class SummerVentilationSettings:
    """Gateway-wide summer function configuration."""

    enabled: bool
    duration_hours: int | None


@dataclass(frozen=True, slots=True)
class VentilationValue:
    """Ventilation level for a temperature mode."""

    temperature_mode: int
    level: int


@dataclass(frozen=True, slots=True)
class PropertyKey:
    """Identity of a device-specific value."""

    product_type: int
    hardware_version: int
    minimum_software_version: int
    class_id: int
    instance_id: int
    property_id: int

    @property
    def value_identity(self) -> tuple[int, int, int]:
        """Return the portion used to identify a value within a profile."""
        return self.class_id, self.instance_id, self.property_id


@dataclass(frozen=True, slots=True)
class PropertyValue:
    """Raw device-specific value."""

    key: PropertyKey
    value: bytes | None


@dataclass(frozen=True, slots=True)
class AttachedDevice:
    """A ventilation device attached to a ConnectBox room."""

    device_id: int
    product_type: int
    product_variant: int | None
    hardware_version: int | None
    software_version: int | None
    battery_state: int | None
    signal_strength: int | None
    errors: tuple[int, ...]
    level_zero_supported: bool | None
    filter_warning: bool | None
    filter_runtime: int | None
    filter_maximum: int | None
    properties: tuple[PropertyValue, ...]
    sensor_readings: tuple[tuple[int, int], ...] = ()
    summer_ventilation_available: bool | None = None

    def property_bytes(self, identity: tuple[int, int, int]) -> bytes | None:
        """Return a raw profile value by class, instance, and property ID."""
        for prop in self.properties:
            if prop.key.value_identity == identity:
                return prop.value
        return None

    def reading(self, sensor_type: int) -> int | None:
        """Return the latest room-model reading of one sensor type."""
        for current_type, value in self.sensor_readings:
            if current_type == sensor_type:
                return value
        return None


@dataclass(frozen=True, slots=True)
class Room:
    """A room and its attached devices."""

    room_id: int
    name: str
    room_type: int | None
    target_level: int | None
    ventilation: tuple[VentilationValue, ...]
    devices: tuple[AttachedDevice, ...]
    raw: bytes = field(default=b"", repr=False, compare=False)
    temporary_until: int | None = None
    """Unix time at which a temporary change (e.g. on the unit) ends."""
    boost_until: int | None = None
    """Unix time at which the room's active boost ends."""
    boost_duration: int | None = None
    """Configured boost duration in minutes."""

    def boost_active(self, now: float) -> bool:
        """Return whether a boost is running at the given Unix time."""
        return self.boost_until is not None and self.boost_until > now

    def level_for_mode(self, temperature_mode: int) -> int | None:
        """Return the configured level for the active temperature mode."""
        for value in self.ventilation:
            if value.temperature_mode == temperature_mode:
                return value.level
        return self.target_level

    def current_level(self, temperature_mode: int) -> int | None:
        """Return the level the room's units currently run at.

        The room's target level follows a change made on a unit's own control
        panel immediately, while the configured value for the situation only
        changes later, if at all. Fall back to the configured value.
        """
        if self.target_level is not None:
            return self.target_level
        return self.level_for_mode(temperature_mode)


@dataclass(frozen=True, slots=True)
class GatewaySnapshot:
    """Complete coordinator state from one bounded refresh."""

    version: VersionInfo
    run_state: RunState
    rooms: tuple[Room, ...]
    summer_ventilation_settings: SummerVentilationSettings | None = None
    schedules: Schedules | None = None

    def find_device(self, device_id: int) -> tuple[Room, AttachedDevice] | None:
        """Find a device together with its containing room."""
        for room in self.rooms:
            for device in room.devices:
                if device.device_id == device_id:
                    return room, device
        return None
