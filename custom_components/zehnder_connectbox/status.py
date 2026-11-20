"""Combined operating status of one ventilation unit.

Kept free of Home Assistant imports so the logic can be tested on its own.
"""

from __future__ import annotations

from .const import SENSOR_MODE_LEVEL
from .models import AttachedDevice, Room, RunMode, RunState, TemperatureMode
from .profiles import summer_ventilation_role

STATUS_OFF = "off"
STATUS_STANDBY = "standby"
STATUS_AUTO = "auto"
STATUS_BOOST = "boost"
STATUS_SUMMER = "summer_ventilation"

FAN_STATUS_OPTIONS = [
    STATUS_OFF,
    STATUS_STANDBY,
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    STATUS_AUTO,
    STATUS_BOOST,
    STATUS_SUMMER,
]

SITUATIONS = {
    TemperatureMode.AWAKE: "awake",
    TemperatureMode.ASLEEP: "asleep",
    TemperatureMode.AWAY: "away",
    TemperatureMode.ANTIFREEZE: "antifreeze",
    TemperatureMode.OVERRIDE: "override",
}


def fan_status(
    run_state: RunState, room: Room, device: AttachedDevice, now: float
) -> str | None:
    """Return what the unit is doing right now, most specific first.

    Global standby wins over everything, then a running boost, then a running
    summer ventilation the unit takes part in. Otherwise the room's level is
    shown: 0 as standby, 1–4 as level, 5 as sensor-controlled operation.
    """
    if run_state.run_mode == RunMode.OFF:
        return STATUS_OFF
    if room.boost_active(now):
        return STATUS_BOOST
    if (
        run_state.summer_ventilation_running(now)
        and summer_ventilation_role(device) is not None
    ):
        return STATUS_SUMMER
    level = room.current_level(run_state.temperature_mode)
    if level == 0:
        return STATUS_STANDBY
    if level == SENSOR_MODE_LEVEL:
        return STATUS_AUTO
    if level in (1, 2, 3, 4):
        return f"level_{level}"
    return None


def situation(run_state: RunState) -> str | None:
    """Return the active situation as a translation key."""
    return situation_name(run_state.temperature_mode)


def situation_name(temperature_mode: int) -> str | None:
    """Return a situation's translation key."""
    try:
        return SITUATIONS[TemperatureMode(temperature_mode)]
    except ValueError:
        return None


def error_codes(device: AttachedDevice) -> list[int]:
    """Return the active fault codes the gateway reports, without duplicates.

    The unit's error log (class 37) is history and not included; see
    profiles.error_log.
    """
    codes: list[int] = []
    for code in device.errors:
        if code != 0 and code not in codes:
            codes.append(code)
    return codes
