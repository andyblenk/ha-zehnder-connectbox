"""Services to edit the daily and weekly schedules, as on the app's plan page."""

from __future__ import annotations

from datetime import time as dt_time

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import ZehnderConnectBoxCoordinator
from .models import GatewaySnapshot, ScheduledFunction, Schedules
from .profiles import supports_boost
from .protocol import PLAN_NAME_MAX_BYTES

SERVICE_SAVE_DAY_PLAN = "save_day_plan"
SERVICE_REMOVE_DAY_PLAN = "remove_day_plan"
SERVICE_SAVE_WEEK_PLAN = "save_week_plan"
SERVICE_REMOVE_WEEK_PLAN = "remove_week_plan"

ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_PLAN = "plan"
ATTR_NAME = "name"
ATTR_SLOTS = "slots"
ATTR_FUNCTIONS = "functions"
ATTR_DAYS = "days"

# Situations a daily schedule switches to, as the app offers them.
SLOT_SITUATIONS = {"awake": 0, "asleep": 1, "away": 2}
# Timed functions: the app's room boost added function 2 with the room's ID;
# its summer schedule holds function 5 with room 0 for summer ventilation.
FUNCTIONS = {"boost": 2, "summer_ventilation": 5}
DAYS_PER_WEEK = 7

PLAN_REFERENCE = vol.Any(vol.All(int, vol.Range(min=1)), cv.string)


def _plan_name(value: object) -> str:
    name = cv.string(value).strip()
    if not name or len(name.encode()) > PLAN_NAME_MAX_BYTES:
        raise vol.Invalid(f"the name must have 1 to {PLAN_NAME_MAX_BYTES} bytes")
    return name


def _whole_minute(value: object) -> dt_time:
    parsed = cv.time(value)
    if parsed.second or parsed.microsecond:
        raise vol.Invalid("times must be whole minutes")
    return parsed


SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("start"): _whole_minute,
        vol.Required("situation"): vol.In(SLOT_SITUATIONS),
    }
)
FUNCTION_SCHEMA = vol.Schema(
    {
        vol.Required("start"): _whole_minute,
        vol.Required("function"): vol.In(FUNCTIONS),
        vol.Required("duration_minutes"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=24 * 60)
        ),
        vol.Optional("room"): vol.Any(vol.All(int, vol.Range(min=1)), cv.string),
    }
)
SAVE_DAY_PLAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(ATTR_PLAN): PLAN_REFERENCE,
        vol.Optional(ATTR_NAME): _plan_name,
        vol.Optional(ATTR_SLOTS): vol.All(cv.ensure_list, [SLOT_SCHEMA]),
        vol.Optional(ATTR_FUNCTIONS): vol.All(cv.ensure_list, [FUNCTION_SCHEMA]),
    }
)
SAVE_WEEK_PLAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(ATTR_PLAN): PLAN_REFERENCE,
        vol.Optional(ATTR_NAME): _plan_name,
        vol.Optional(ATTR_DAYS): vol.All(
            cv.ensure_list,
            vol.Length(min=1, max=DAYS_PER_WEEK),
            [PLAN_REFERENCE],
        ),
    }
)
REMOVE_PLAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Required(ATTR_PLAN): PLAN_REFERENCE,
    }
)


def _error(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _coordinator(
    hass: HomeAssistant, call: ServiceCall
) -> ZehnderConnectBoxCoordinator:
    """Return the selected ConnectBox, or the only one that is loaded."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    entry_id = call.data.get(ATTR_CONFIG_ENTRY)
    if entry_id is not None:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if len(entries) != 1:
        raise _error("config_entry_required" if entries else "not_loaded")
    return entries[0].runtime_data


def _resolve(reference: int | str, plans: list[tuple[int, str | None]]) -> int:
    """Find a schedule by ID or by its name as shown in the app."""
    if isinstance(reference, str) and reference.strip().isdigit():
        reference = int(reference.strip())
    if isinstance(reference, int):
        if any(plan_id == reference for plan_id, _name in plans):
            return reference
        raise _error("plan_unknown")
    wanted = reference.strip().casefold()
    found = [plan_id for plan_id, name in plans if (name or "").casefold() == wanted]
    if len(found) > 1:
        raise _error("plan_ambiguous")
    if not found:
        raise _error("plan_unknown")
    return found[0]


def _day_plans(schedules: Schedules) -> list[tuple[int, str | None]]:
    return [(plan.plan_id, plan.name) for plan in schedules.day_plans]


def _week_plans(schedules: Schedules) -> list[tuple[int, str | None]]:
    return [(plan.plan_id, plan.name) for plan in schedules.week_plans]


def _schedules(coordinator: ZehnderConnectBoxCoordinator) -> Schedules:
    snapshot = coordinator.data
    if snapshot is None or snapshot.schedules is None:
        raise _error("schedules_unavailable")
    return snapshot.schedules


def _minute(value: dt_time) -> int:
    return value.hour * 60 + value.minute


def _function_room(snapshot: GatewaySnapshot, reference: int | str | None) -> int:
    """Return a room whose unit offers the boost, by ID or name."""
    if reference is None:
        raise _error("function_room_invalid")
    if isinstance(reference, str) and reference.strip().isdigit():
        reference = int(reference.strip())
    for room in snapshot.rooms:
        matches = (
            room.room_id == reference
            if isinstance(reference, int)
            else (room.name or "").casefold() == reference.strip().casefold()
        )
        if matches and any(supports_boost(room, device) for device in room.devices):
            return room.room_id
    raise _error("function_room_invalid")


def _functions(
    snapshot: GatewaySnapshot, items: list[dict]
) -> tuple[ScheduledFunction, ...]:
    functions: list[ScheduledFunction] = []
    for item in items:
        function = FUNCTIONS[item["function"]]
        room_id = (
            _function_room(snapshot, item.get("room"))
            if item["function"] == "boost"
            else 0
        )
        functions.append(
            ScheduledFunction(
                start_minute=_minute(item["start"]),
                function=function,
                duration_minutes=item["duration_minutes"],
                room_id=room_id,
            )
        )
    return tuple(functions)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the schedule services once for all ConnectBoxes."""

    async def save_day_plan(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        schedules = _schedules(coordinator)
        plan_id = (
            _resolve(call.data[ATTR_PLAN], _day_plans(schedules))
            if ATTR_PLAN in call.data
            else None
        )
        slots = (
            tuple(
                (_minute(item["start"]), SLOT_SITUATIONS[item["situation"]])
                for item in call.data[ATTR_SLOTS]
            )
            if ATTR_SLOTS in call.data
            else None
        )
        functions = (
            _functions(coordinator.data, call.data[ATTR_FUNCTIONS])
            if ATTR_FUNCTIONS in call.data
            else None
        )
        saved = await coordinator.async_save_day_plan(
            plan_id, call.data.get(ATTR_NAME), slots, functions
        )
        return {"plan_id": saved}

    async def save_week_plan(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        schedules = _schedules(coordinator)
        plan_id = (
            _resolve(call.data[ATTR_PLAN], _week_plans(schedules))
            if ATTR_PLAN in call.data
            else None
        )
        days = None
        if ATTR_DAYS in call.data:
            references = call.data[ATTR_DAYS]
            if len(references) not in (1, DAYS_PER_WEEK):
                raise _error("invalid_week_plan_days")
            resolved = [
                _resolve(reference, _day_plans(schedules)) for reference in references
            ]
            days = tuple(resolved * DAYS_PER_WEEK if len(resolved) == 1 else resolved)
        saved = await coordinator.async_save_week_plan(
            plan_id, call.data.get(ATTR_NAME), days
        )
        return {"plan_id": saved}

    async def remove_day_plan(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        plan_id = _resolve(call.data[ATTR_PLAN], _day_plans(_schedules(coordinator)))
        await coordinator.async_remove_plan(plan_id, week=False)

    async def remove_week_plan(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        plan_id = _resolve(call.data[ATTR_PLAN], _week_plans(_schedules(coordinator)))
        await coordinator.async_remove_plan(plan_id, week=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_DAY_PLAN,
        save_day_plan,
        schema=SAVE_DAY_PLAN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_WEEK_PLAN,
        save_week_plan,
        schema=SAVE_WEEK_PLAN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_DAY_PLAN, remove_day_plan, schema=REMOVE_PLAN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_WEEK_PLAN, remove_week_plan, schema=REMOVE_PLAN_SCHEMA
    )
