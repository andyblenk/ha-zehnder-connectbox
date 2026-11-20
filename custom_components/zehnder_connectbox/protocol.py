"""Independent implementation of the ConnectBox application protocol."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from .const import SENSOR_MODE_LEVEL, VENTILATION_LEVELS
from .models import (
    ActionLogEntry,
    AttachedDevice,
    DayPlan,
    PairedApp,
    PropertyKey,
    PropertyValue,
    Room,
    RunState,
    ScheduledFunction,
    Schedules,
    SiteInfo,
    SummerVentilationSettings,
    UpdateProgress,
    VentilationValue,
    VersionInfo,
    WeekPlan,
)
from .protobuf import (
    Field,
    ProtobufDecodeError,
    WireType,
    bytes_value,
    bytes_values,
    decode_fields,
    decode_packed_uints,
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

# Room fields observed while the official app started and stopped a boost and
# after a level was changed on a unit's control panel.
ROOM_BOOST_UNTIL = 18
"""Unix time at which an active boost ends; absent without a boost."""
ROOM_BOOST_DURATION = 22
"""Configured boost duration in minutes (the app offers 15 to 120)."""
ROOM_TEMPORARY_UNTIL = 61
"""Unix time of the next schedule change that ends a temporary change."""
# Room-value update (SET_ROOM_VALUE) field with which the official app starts
# and stops a boost. The gateway forwarded the app's update to this client as
# an unsolicited room-value notification: the room ID and field 15 set to the
# end of the boost as Unix time when the app started a 15-minute boost, and to
# 0 when the app ended it early. The room then reported field 18 accordingly.
ROOM_VALUE_BOOST_UNTIL = 15


class ProtocolError(ConnectionError):
    """Raised for invalid or unexpected gateway data."""


class IncompleteFrame(ProtocolError):
    """Raised when more bytes are needed for a complete frame."""


class OperationType(IntEnum):
    """Operations used by this integration."""

    VERSION_REQUEST = 18
    VERSION_CONFIRM = 68
    REMOTE_ACCESS_REQUEST = 140
    SET_REMOTE_ACCESS_REQUEST = 141
    REMOTE_ACCESS_CONFIRM = 142
    SET_REMOTE_ACCESS_CONFIRM = 143
    # Schedule operations, numbered as in the official app's message table
    # (GetSchedule, LockDayplan, SetDayplan, ...). The gateway sends the
    # notification with all schedules whenever a client changed one.
    LOCK_DAY_PLAN_REQUEST = 150
    LOCK_DAY_PLAN_CONFIRM = 151
    RELEASE_DAY_PLAN_REQUEST = 152
    RELEASE_DAY_PLAN_CONFIRM = 153
    LOCK_WEEK_PLAN_REQUEST = 154
    LOCK_WEEK_PLAN_CONFIRM = 155
    RELEASE_WEEK_PLAN_REQUEST = 156
    RELEASE_WEEK_PLAN_CONFIRM = 157
    SCHEDULES_REQUEST = 158
    SCHEDULES_CONFIRM = 159
    SCHEDULES_NOTIFICATION = 160
    REMOVE_DAY_PLAN_REQUEST = 165
    REMOVE_DAY_PLAN_CONFIRM = 166
    REMOVE_WEEK_PLAN_REQUEST = 167
    REMOVE_WEEK_PLAN_CONFIRM = 168
    ADD_DAY_PLAN_REQUEST = 169
    ADD_DAY_PLAN_CONFIRM = 170
    ADD_WEEK_PLAN_REQUEST = 171
    ADD_WEEK_PLAN_CONFIRM = 172
    SET_DAY_PLAN_REQUEST = 173
    SET_DAY_PLAN_CONFIRM = 174
    SET_WEEK_PLAN_REQUEST = 175
    SET_WEEK_PLAN_CONFIRM = 176
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
    SET_ACTIVE_WEEK_PLAN_REQUEST = 245
    SET_ACTIVE_WEEK_PLAN_CONFIRM = 246
    # The app's system name and language page, changed under the site's lock.
    LOCK_SITE_REQUEST = 250
    LOCK_SITE_CONFIRM = 251
    RELEASE_SITE_REQUEST = 252
    RELEASE_SITE_CONFIRM = 253
    SITE_INFO_REQUEST = 254
    SITE_INFO_CONFIRM = 255
    SET_SITE_INFO_REQUEST = 256
    SET_SITE_INFO_CONFIRM = 257
    SITE_INFO_NOTIFICATION = 258
    TIME_REQUEST = 260
    TIME_CONFIRM = 261
    ACTION_LOG_REQUEST = 290
    ACTION_LOG_CONFIRM = 291
    ACTION_LOG_NOTIFICATION = 292
    PAIRED_APPS_REQUEST = 299
    PAIRED_APPS_CONFIRM = 300
    SET_STANDBY_TIME_RANGE_REQUEST = 330
    SET_STANDBY_TIME_RANGE_CONFIRM = 331
    UPDATE_PROGRESS_REQUEST = 340
    UPDATE_PROGRESS_CONFIRM = 341
    SUMMER_VENTILATION_REQUEST = 354
    SUMMER_VENTILATION_CONFIRM = 355
    SET_SUMMER_VENTILATION_REQUEST = 356
    SET_SUMMER_VENTILATION_CONFIRM = 357
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
    """Decode the gateway's version values and its serial number."""
    fields = decode_fields(message)
    serial = bytes_value(fields, 2)
    return VersionInfo(
        serial_number=serial.decode(errors="replace") if serial else None,
        system_version=uint_value(fields, 1),
        comfonet_version=uint_value(fields, 3),
        property_list_version=uint_value(fields, 4),
        zone_state_list_version=uint_value(fields, 5),
        connectbox_version=uint_value(fields, 6),
    )


def decode_run_state(message: bytes) -> RunState:
    """Decode the current system state."""
    outer = decode_fields(message)
    inner = _required_bytes(outer, 1, "run state")
    fields = decode_fields(inner)
    standby = uint_value(fields, 7)
    summer = uint_value(fields, 12)
    return RunState(
        run_mode=_required_uint(fields, 1, "run mode"),
        temperature_mode=_required_uint(fields, 2, "temperature mode"),
        standby=bool(standby) if standby is not None else None,
        standby_mode=uint_value(fields, 8),
        summer_ventilation=bool(summer) if summer is not None else None,
        summer_ventilation_end=uint_value(fields, 13),
        errors=uint_values(fields, 11),
        # Field 5 repeats the ID of today's daily schedule (field 4).
        week_plan=uint_value(fields, 6),
        day_plan=_decode_day_plan(bytes_value(fields, 4)),
        standby_start=uint_value(fields, 9),
        standby_end=uint_value(fields, 10),
        situation_source=uint_value(fields, 3),
    )


def _decode_day_plan(message: bytes | None) -> DayPlan | None:
    """Decode a daily schedule from the run state or the schedule list."""
    if not message:
        return None
    try:
        fields = decode_fields(message)
        plan_id = uint_value(fields, 1)
        if plan_id is None:
            return None
        raw_name = bytes_value(fields, 2)
        slots: list[tuple[int, int]] = []
        for slot in bytes_values(fields, 3):
            slot_fields = decode_fields(slot)
            # A zero start or situation may be omitted from the wire.
            slots.append(
                (uint_value(slot_fields, 1) or 0, uint_value(slot_fields, 2) or 0)
            )
        functions: list[ScheduledFunction] = []
        for entry in bytes_values(fields, 20):
            entry_fields = decode_fields(entry)
            functions.append(
                ScheduledFunction(
                    start_minute=uint_value(entry_fields, 1) or 0,
                    function=uint_value(entry_fields, 2) or 0,
                    duration_minutes=uint_value(entry_fields, 3) or 0,
                    room_id=uint_value(entry_fields, 4) or 0,
                    device_id=uint_value(entry_fields, 5) or 0,
                )
            )
    except ProtobufDecodeError:
        return None
    return DayPlan(
        plan_id=plan_id,
        name=raw_name.decode(errors="replace") if raw_name else None,
        slots=tuple(slots),
        functions=tuple(functions),
    )


# MondayId to SundayId of the app's weekly schedule message.
WEEK_PLAN_DAY_FIELDS = (3, 4, 5, 6, 7, 8, 9)
PLAN_NAME_MAX_BYTES = 32


def _decode_week_plan(message: bytes | None) -> WeekPlan | None:
    """Decode a weekly schedule from the schedule list or a confirmation."""
    if not message:
        return None
    week_fields = decode_fields(message)
    plan_id = uint_value(week_fields, 1)
    if plan_id is None:
        return None
    raw_name = bytes_value(week_fields, 2)
    return WeekPlan(
        plan_id=plan_id,
        name=raw_name.decode(errors="replace") if raw_name else None,
        days=tuple(
            uint_value(week_fields, number) or 0 for number in WEEK_PLAN_DAY_FIELDS
        ),
    )


def decode_schedules(message: bytes) -> Schedules:
    """Decode the list of all daily and weekly schedules."""
    fields = decode_fields(message)
    day_plans = tuple(
        plan
        for value in bytes_values(fields, 1)
        if (plan := _decode_day_plan(value)) is not None
    )
    week_plans = tuple(
        plan
        for value in bytes_values(fields, 2)
        if (plan := _decode_week_plan(value)) is not None
    )
    return Schedules(day_plans=day_plans, week_plans=week_plans)


def decode_day_plan_confirm(message: bytes) -> DayPlan:
    """Decode the daily schedule an add or change confirmation returns.

    The confirmation leaves out the plan's timed functions.
    """
    plan = _decode_day_plan(bytes_value(decode_fields(message), 1))
    if plan is None:
        raise ProtocolError("missing daily schedule")
    return plan


def decode_week_plan_confirm(message: bytes) -> WeekPlan:
    """Decode the weekly schedule an add or change confirmation returns."""
    plan = _decode_week_plan(bytes_value(decode_fields(message), 1))
    if plan is None:
        raise ProtocolError("missing weekly schedule")
    return plan


def _check_uint(value: object, label: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must not exceed {maximum}")
    return value


def _plan_name(name: str) -> bytes:
    raw = name.encode()
    if not raw or len(raw) > PLAN_NAME_MAX_BYTES:
        raise ValueError(f"plan names must have 1 to {PLAN_NAME_MAX_BYTES} bytes")
    return raw


def encode_day_plan(plan: DayPlan) -> bytes:
    """Build an add or change request for a complete daily schedule.

    Every value is written, zeros included, as the gateway reports them. The
    plan ID is 0 for a new plan; the gateway assigns one and returns it. The
    app's change request replaces the whole plan, so the timed functions must
    be sent again to keep them.
    """
    message = bytearray(encode_uint(1, _check_uint(plan.plan_id, "plan ID")))
    message.extend(encode_bytes(2, _plan_name(plan.name or "")))
    for minute, mode in plan.slots:
        slot = encode_uint(1, _check_uint(minute, "start", 1439)) + encode_uint(
            2, _check_uint(mode, "situation")
        )
        message.extend(encode_bytes(3, slot))
    for item in plan.functions:
        entry = b"".join(
            (
                encode_uint(1, _check_uint(item.start_minute, "start", 1439)),
                encode_uint(2, _check_uint(item.function, "function")),
                encode_uint(3, _check_uint(item.duration_minutes, "duration")),
                encode_uint(4, _check_uint(item.room_id, "room")),
                encode_uint(5, _check_uint(item.device_id, "device")),
            )
        )
        message.extend(encode_bytes(20, entry))
    return encode_bytes(1, bytes(message))


def encode_week_plan(plan: WeekPlan) -> bytes:
    """Build an add or change request for a weekly schedule (Monday first)."""
    if len(plan.days) != len(WEEK_PLAN_DAY_FIELDS):
        raise ValueError("a weekly schedule needs one daily schedule per day")
    message = bytearray(encode_uint(1, _check_uint(plan.plan_id, "plan ID")))
    message.extend(encode_bytes(2, _plan_name(plan.name or "")))
    for number, day_plan in zip(WEEK_PLAN_DAY_FIELDS, plan.days, strict=True):
        message.extend(encode_uint(number, _check_uint(day_plan, "daily schedule")))
    return encode_bytes(1, bytes(message))


def encode_plan_id(plan_id: int) -> bytes:
    """Build a lock, release, read or remove request for one schedule."""
    return encode_uint(1, _check_uint(plan_id, "plan ID"))


def encode_active_week_plan(plan_id: int) -> bytes:
    """Build the request that makes a weekly schedule the active one."""
    return encode_uint(1, _check_uint(plan_id, "plan ID"))


def encode_standby_time_range(enabled: bool, start: int, end: int) -> bytes:
    """Build the absence planner's update (mode, start and end as Unix time).

    The app's message names the fields mode, startDateTime and endDateTime.
    Mode 1 with a period was reported back as the saved planner; mode 0
    switched it off again.
    """
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    return b"".join(
        (
            encode_uint(1, int(enabled)),
            encode_uint(2, _check_uint(start, "start")),
            encode_uint(3, _check_uint(end, "end")),
        )
    )


def decode_action_log(message: bytes) -> tuple[ActionLogEntry, ...]:
    """Decode the gateway's history, newest entry first as reported."""
    entries: list[ActionLogEntry] = []
    for value in bytes_values(decode_fields(message), 1):
        fields = decode_fields(value)
        nickname = bytes_value(fields, 1)
        text = bytes_value(fields, 3)
        entries.append(
            ActionLogEntry(
                nickname=nickname.decode(errors="replace") if nickname else None,
                timestamp=uint_value(fields, 2),
                message=text.decode(errors="replace") if text else None,
                params=tuple(
                    item.decode(errors="replace")
                    for item in bytes_values(fields, 4)
                    if item
                ),
            )
        )
    return tuple(entries)


def decode_local_time_offset(message: bytes) -> int | None:
    """Decode the gateway's offset of local time from UTC, in seconds.

    The time confirmation carries utcTime and localTime; the local time was
    reported rounded to the minute, so the offset is rounded to 15 minutes.
    """
    fields = decode_fields(message)
    utc = uint_value(fields, 1)
    local = uint_value(fields, 2)
    if utc is None or local is None:
        return None
    return round((local - utc) / 900) * 900


def decode_remote_access(message: bytes) -> bool | None:
    """Decode whether access over the internet is enabled.

    The app's enumeration has AccessEnabled = 0 and AccessDisabled = 1; the
    gateway reported 0 while the app showed the access switched on.
    """
    value = uint_value(decode_fields(message), 1)
    if value is None:
        return None
    return value == 0


def encode_remote_access(enabled: bool) -> bytes:
    """Build the update of the access over the internet (0 = enabled)."""
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    return encode_uint(1, 0 if enabled else 1)


SITE_NAME_MAX_BYTES = 32
# Fields of the app's site update: name, language, location, time zone index
# and name, and the heating setting of other product lines where reported.
SITE_UPDATE_FIELDS = (1, 2, 3, 4, 5, 20)


def _text(value: bytes | None) -> str | None:
    return value.decode(errors="replace") if value else None


def decode_site_info(message: bytes) -> SiteInfo:
    """Decode the system name, language, country and network connection."""
    fields = decode_fields(message)
    return SiteInfo(
        name=_text(bytes_value(fields, 1)),
        language=uint_value(fields, 2),
        location=uint_value(fields, 3),
        timezone=uint_value(fields, 4),
        timezone_name=_text(bytes_value(fields, 5)),
        network_mode=uint_value(fields, 7),
        raw=message,
    )


def encode_site_info(
    site: SiteInfo, *, name: str | None = None, language: int | None = None
) -> bytes:
    """Build a site update that keeps everything but the given values.

    The update is the site as read, without the values the gateway only
    reports (configured, network connection).
    """
    if not site.raw:
        raise ProtocolError("site information does not contain its gateway data")
    if name is not None:
        raw_name = name.encode()
        if not raw_name or len(raw_name) > SITE_NAME_MAX_BYTES:
            raise ValueError(f"the name must have 1 to {SITE_NAME_MAX_BYTES} bytes")
    if language is not None:
        _check_uint(language, "language")
    message = bytearray()
    seen: set[int] = set()
    for item in decode_fields(site.raw):
        if item.number not in SITE_UPDATE_FIELDS or item.number in seen:
            continue
        seen.add(item.number)
        if item.number == 1 and name is not None:
            message.extend(encode_bytes(1, name.encode()))
        elif item.number == 2 and language is not None:
            message.extend(encode_uint(2, language))
        else:
            message.extend(_encode_field(item))
    if name is not None and 1 not in seen:
        message[0:0] = encode_bytes(1, name.encode())
    if language is not None and 2 not in seen:
        raise ProtocolError("the gateway does not report a language")
    return bytes(message)


def _repeated_uints(fields: tuple[Field, ...], number: int) -> tuple[int, ...]:
    """Return a repeated number field, written one by one or packed."""
    values = list(uint_values(fields, number))
    for packed in bytes_values(fields, number):
        values.extend(decode_packed_uints(packed))
    return tuple(values)


def decode_paired_apps(message: bytes) -> tuple[PairedApp, ...]:
    """Decode the paired clients of the app's user page."""
    apps: list[PairedApp] = []
    for value in bytes_values(decode_fields(message), 1):
        fields = decode_fields(value)
        apps.append(
            PairedApp(
                app_id=uint_value(fields, 1),
                nickname=_text(bytes_value(fields, 2)),
                last_active=uint_value(fields, 3),
            )
        )
    return tuple(apps)


def decode_update_progress(message: bytes) -> UpdateProgress:
    """Decode the progress of a firmware update of the units."""
    fields = decode_fields(message)
    return UpdateProgress(
        remaining_minutes=uint_value(fields, 1),
        updating_device_id=uint_value(fields, 2),
        progress=uint_value(fields, 3),
        up_to_date_device_ids=_repeated_uints(fields, 4),
        queued_device_ids=_repeated_uints(fields, 5),
    )


def decode_summer_ventilation_settings(message: bytes) -> SummerVentilationSettings:
    """Decode the separate gateway configuration for the summer function."""
    outer = decode_fields(message)
    fields = decode_fields(_required_bytes(outer, 1, "summer settings"))
    enabled = _required_uint(fields, 1, "summer enabled")
    if enabled not in (0, 1):
        raise ProtocolError("invalid summer-enabled value")
    return SummerVentilationSettings(
        enabled=bool(enabled), duration_hours=uint_value(fields, 2)
    )


def encode_summer_ventilation_settings(enabled: bool, duration_hours: int) -> bytes:
    """Build a complete gateway-wide summer function configuration."""
    if not isinstance(enabled, bool):
        # Callers handle invalid settings as ValueError, like the duration.
        raise ValueError("summer ventilation enabled must be a boolean")  # noqa: TRY004
    if (
        isinstance(duration_hours, bool)
        or not isinstance(duration_hours, int)
        or not 1 <= duration_hours <= 24
    ):
        raise ValueError("summer ventilation duration must be between 1 and 24 hours")
    settings = encode_uint(1, int(enabled)) + encode_uint(2, duration_hours)
    return encode_bytes(1, settings)


def encode_run_state(
    run_mode: int, user_location: int = 1, *, summer_active: bool | None = None
) -> bytes:
    """Build a system run-state update with an optional summer toggle."""
    body = encode_uint(1, run_mode) + encode_uint(2, user_location)
    if summer_active is not None:
        body += encode_uint(3, int(summer_active))
    return body


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
        # The gateway may encode zero when no temporary change is active.
        temporary_until=uint_value(fields, ROOM_TEMPORARY_UNTIL) or None,
        boost_until=uint_value(fields, ROOM_BOOST_UNTIL) or None,
        boost_duration=uint_value(fields, ROOM_BOOST_DURATION),
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
    summer_available = uint_value(fields, 19)
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
        sensor_readings=tuple(
            reading
            for value in bytes_values(fields, 8)
            if (reading := _decode_sensor_reading(value)) is not None
        ),
        summer_ventilation_available=(
            bool(summer_available) if summer_available in (0, 1) else None
        ),
    )


def _decode_sensor_reading(message: bytes) -> tuple[int, int] | None:
    """Decode one entry of a device's sensor list as (sensor type, value)."""
    fields = decode_fields(message)
    sensor_type = uint_value(fields, 1)
    value = uint_value(fields, 2)
    if sensor_type is None or value is None:
        return None
    return sensor_type, value


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


def encode_room_boost(room_id: int, until: int) -> bytes:
    """Build the room-value update that starts (end time) or stops (0) a boost."""
    for value in (room_id, until):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("room ID and boost end must be non-negative integers")
    return encode_bytes(
        1, encode_uint(1, room_id) + encode_uint(ROOM_VALUE_BOOST_UNTIL, until)
    )


def encode_room_level(room: Room, temperature_mode: int, level: int) -> bytes:
    """Update one active mode while preserving all other room mode values."""
    if level not in VENTILATION_LEVELS and level != SENSOR_MODE_LEVEL:
        raise ValueError(
            "ventilation level must be between 0 and 4 or sensor-controlled"
        )
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


def encode_room_number_update(room: Room, values: dict[int, int]) -> bytes:
    """Write a room back with number fields changed.

    The room is sent as read, without the device fields 30, 31, and 41 that
    the official client leaves out of a room write, and without a filter
    acknowledgement. Only the given top-level number fields change; a field
    the gateway did not report is added in field-number order.
    """
    if not values:
        raise ValueError("no room field to change")
    for number, value in values.items():
        if number in (1, 2, 3, 8, 19, 101) or number < 1:
            raise ValueError("this room field cannot be changed as a number")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("room field values must be non-negative integers")
    return _encode_room_write(room, values)


ROOM_NAME_MAX_BYTES = 32
ROOM_NAME = 2
ROOM_TYPE = 3


def encode_room_settings(
    room: Room, *, name: str | None = None, room_type: int | None = None
) -> bytes:
    """Write a room back with its name or type changed, as the app's room page.

    Everything else is sent as read, like a change of the boost duration.
    """
    if name is None and room_type is None:
        raise ValueError("no room setting to change")
    raw_name: bytes | None = None
    if name is not None:
        raw_name = name.encode()
        if not raw_name or len(raw_name) > ROOM_NAME_MAX_BYTES:
            raise ValueError(f"the name must have 1 to {ROOM_NAME_MAX_BYTES} bytes")
    numbers: dict[int, int] = {}
    if room_type is not None:
        numbers[ROOM_TYPE] = _check_uint(room_type, "room type")
    return _encode_room_write(room, numbers, name=raw_name)


def _encode_room_write(
    room: Room, values: dict[int, int], *, name: bytes | None = None
) -> bytes:
    """Re-encode a room as read with number fields or its name replaced."""
    if not room.raw:
        raise ProtocolError("room does not contain its original gateway data")
    pending = dict(sorted(values.items()))
    name_written = name is None
    room_value = bytearray()
    for field in decode_fields(room.raw):
        for number in [item for item in pending if item < field.number]:
            room_value.extend(encode_uint(number, pending.pop(number)))
        if field.number == ROOM_NAME and name is not None:
            if not name_written:
                room_value.extend(encode_bytes(ROOM_NAME, name))
                name_written = True
            continue
        if field.number in pending:
            if field.wire_type is not WireType.VARINT:
                raise ProtocolError("the room field is not a number")
            room_value.extend(encode_uint(field.number, pending.pop(field.number)))
            continue
        if field.number in values and field.wire_type is WireType.VARINT:
            continue  # a repeated occurrence of a field that was replaced
        if field.number == 8 and field.wire_type is WireType.BYTES:
            device_fields = decode_fields(bytes(field.value))
            writable_fields = tuple(
                item for item in device_fields if item.number not in (30, 31, 41)
            )
            room_value.extend(encode_bytes(8, _encode_fields(writable_fields)))
            continue
        room_value.extend(_encode_field(field))
    if not name_written:
        raise ProtocolError("the room does not report a name")
    for number, value in pending.items():
        room_value.extend(encode_uint(number, value))
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
