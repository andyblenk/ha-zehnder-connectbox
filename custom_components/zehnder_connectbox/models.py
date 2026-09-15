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
class RunState:
    """Current system-wide ConnectBox state."""

    run_mode: int
    temperature_mode: int
    standby: bool | None
    standby_mode: int | None
    summer_ventilation: bool | None
    errors: tuple[int, ...]


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

    def property_bytes(self, identity: tuple[int, int, int]) -> bytes | None:
        """Return a raw profile value by class, instance, and property ID."""
        for prop in self.properties:
            if prop.key.value_identity == identity:
                return prop.value
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

    def level_for_mode(self, temperature_mode: int) -> int | None:
        """Return the configured level for the active temperature mode."""
        for value in self.ventilation:
            if value.temperature_mode == temperature_mode:
                return value.level
        return self.target_level


@dataclass(frozen=True, slots=True)
class GatewaySnapshot:
    """Complete coordinator state from one bounded refresh."""

    version: VersionInfo
    run_state: RunState
    rooms: tuple[Room, ...]

    def find_device(self, device_id: int) -> tuple[Room, AttachedDevice] | None:
        """Find a device together with its containing room."""
        for room in self.rooms:
            for device in room.devices:
                if device.device_id == device_id:
                    return room, device
        return None
