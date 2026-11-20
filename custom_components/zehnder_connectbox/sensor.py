"""Read-only telemetry for supported ConnectBox ventilation units."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZehnderConnectBoxConfigEntry
from .const import CONF_GATEWAY_UUID
from .entity import (
    ConnectBoxDeviceEntity,
    ConnectBoxGatewayEntity,
    supported_device_ids,
)
from .models import (
    AttachedDevice,
    DayPlan,
    GatewaySnapshot,
    Room,
)
from .profiles import (
    AUTOMATIC_SENSOR_STATES,
    BATH_FUNCTION_STATES,
    ERROR_CODE_SPECS,
    EXHAUST_FAN_SPEED,
    EXTRACT_AIR_SENSOR_STATUS,
    EXTRACT_AIR_TEMPERATURE,
    FILTER_MAXIMUM,
    FILTER_REMAINING,
    FILTER_RUNTIME,
    INCOMING_AIR_SENSOR_STATUS,
    INCOMING_AIR_TEMPERATURE,
    SENSOR_TYPE_CO2,
    SENSOR_TYPE_HUMIDITY,
    SUMMER_VENTILATION_ROLES,
    SUPPLY_FAN_SPEED,
    automatic_sensors,
    bath_function,
    board_reading,
    error_log,
    summer_ventilation_role,
    supply_only_operation,
    temperature_value,
)
from .status import (
    FAN_STATUS_OPTIONS,
    error_codes,
    fan_status,
    situation,
    situation_name,
)

try:
    from homeassistant.const import UnitOfRatio

    PARTS_PER_MILLION = UnitOfRatio.PARTS_PER_MILLION
except ImportError:  # older Home Assistant without UnitOfRatio
    from homeassistant.const import (
        CONCENTRATION_PARTS_PER_MILLION as PARTS_PER_MILLION,
    )


@dataclass(frozen=True, kw_only=True)
class ConnectBoxSensorDescription(SensorEntityDescription):
    """Describe how a device value is obtained."""

    value_fn: Callable[[AttachedDevice], int | float | str | datetime | None]
    # Create optional entities only once the unit reports a usable value.
    exists_fn: Callable[[AttachedDevice], bool] | None = None
    # Read the value from the unit's room instead of the unit itself.
    room_value_fn: Callable[[Room], int | float | str | datetime | None] | None = None
    # Keep the entity available while no value is reported (state unknown).
    available_without_value: bool = False
    # Extra attributes from the unit.
    attributes_fn: Callable[[AttachedDevice], dict[str, object]] | None = None


def _temporary_until(room: Room) -> datetime | None:
    """Return the end of a temporary change of the room, if one is active."""
    if not room.temporary_until:
        return None
    return datetime.fromtimestamp(room.temporary_until, UTC)


def _boost_until(room: Room) -> datetime | None:
    """Return the end of the room's boost while one is running."""
    if room.boost_until is None or not room.boost_active(time.time()):
        return None
    return datetime.fromtimestamp(room.boost_until, UTC)


SENSORS = (
    ConnectBoxSensorDescription(
        key="extract_air_temperature",
        translation_key="extract_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: temperature_value(
            device, EXTRACT_AIR_TEMPERATURE, EXTRACT_AIR_SENSOR_STATUS
        ),
        exists_fn=lambda device: (
            temperature_value(
                device, EXTRACT_AIR_TEMPERATURE, EXTRACT_AIR_SENSOR_STATUS
            )
            is not None
        ),
    ),
    ConnectBoxSensorDescription(
        key="incoming_air_temperature",
        translation_key="incoming_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: temperature_value(
            device, INCOMING_AIR_TEMPERATURE, INCOMING_AIR_SENSOR_STATUS
        ),
    ),
    ConnectBoxSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: board_reading(device, SENSOR_TYPE_HUMIDITY),
        exists_fn=lambda device: (
            board_reading(device, SENSOR_TYPE_HUMIDITY) is not None
        ),
    ),
    ConnectBoxSensorDescription(
        key="co2",
        translation_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: board_reading(device, SENSOR_TYPE_CO2),
        exists_fn=lambda device: board_reading(device, SENSOR_TYPE_CO2) is not None,
    ),
    ConnectBoxSensorDescription(
        key="temporary_until",
        translation_key="temporary_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: None,
        room_value_fn=_temporary_until,
        available_without_value=True,
    ),
    ConnectBoxSensorDescription(
        key="summer_ventilation_role",
        translation_key="summer_ventilation_role",
        device_class=SensorDeviceClass.ENUM,
        options=list(SUMMER_VENTILATION_ROLES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=summer_ventilation_role,
        exists_fn=lambda device: summer_ventilation_role(device) is not None,
    ),
    ConnectBoxSensorDescription(
        key="boost_until",
        translation_key="boost_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: None,
        room_value_fn=_boost_until,
        available_without_value=True,
    ),
    ConnectBoxSensorDescription(
        key="exhaust_fan_speed",
        translation_key="exhaust_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=EXHAUST_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="supply_fan_speed",
        translation_key="supply_fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=SUPPLY_FAN_SPEED.value,
    ),
    ConnectBoxSensorDescription(
        key="filter_runtime",
        translation_key="filter_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: (
            FILTER_RUNTIME.value(device)
            if FILTER_RUNTIME.value(device) is not None
            else device.filter_runtime
        ),
    ),
    ConnectBoxSensorDescription(
        key="filter_remaining",
        translation_key="filter_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=FILTER_REMAINING.value,
    ),
    ConnectBoxSensorDescription(
        key="filter_maximum",
        translation_key="filter_maximum",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            FILTER_MAXIMUM.value(device)
            if FILTER_MAXIMUM.value(device) is not None
            else device.filter_maximum
        ),
    ),
    ConnectBoxSensorDescription(
        key="bath_function",
        translation_key="bath_function",
        device_class=SensorDeviceClass.ENUM,
        options=list(BATH_FUNCTION_STATES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=bath_function,
        exists_fn=lambda device: bath_function(device) is not None,
    ),
    ConnectBoxSensorDescription(
        key="automatic_sensors",
        translation_key="automatic_sensors",
        device_class=SensorDeviceClass.ENUM,
        options=list(AUTOMATIC_SENSOR_STATES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=automatic_sensors,
        exists_fn=lambda device: automatic_sensors(device) is not None,
    ),
    ConnectBoxSensorDescription(
        key="error_log",
        translation_key="error_log",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: len(error_log(device)),
        exists_fn=lambda device: any(
            device.property_bytes(spec.key) for spec in ERROR_CODE_SPECS
        ),
        attributes_fn=lambda device: {"entries": error_log(device)},
    ),
    ConnectBoxSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.signal_strength,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZehnderConnectBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and add newly attached devices or readings dynamically."""
    coordinator = entry.runtime_data
    known: set[tuple[int, str]] = set()

    @callback
    def add_new_entities() -> None:
        if coordinator.data is None:
            return
        supported = supported_device_ids(coordinator)
        new_entities: list[SensorEntity] = []
        for room in coordinator.data.rooms:
            for device in room.devices:
                if device.device_id not in supported:
                    continue
                if (device.device_id, "fan_status") not in known:
                    new_entities.append(
                        ConnectBoxFanStatusSensor(coordinator, device.device_id)
                    )
                    known.add((device.device_id, "fan_status"))
                for description in SENSORS:
                    identity = (device.device_id, description.key)
                    if identity in known:
                        continue
                    if description.exists_fn is not None and not (
                        description.exists_fn(device)
                    ):
                        continue
                    new_entities.append(
                        ConnectBoxSensor(coordinator, device.device_id, description)
                    )
                    known.add(identity)
        # Today's daily schedule, once the gateway reports one.
        if (
            0,
            "day_plan",
        ) not in known and coordinator.data.run_state.day_plan is not None:
            new_entities.append(ConnectBoxDayPlanSensor(coordinator))
            known.add((0, "day_plan"))
        # The active weekly schedule, once the gateway reports the schedules.
        if (
            (0, "week_plan") not in known
            and coordinator.data.schedules is not None
            and coordinator.data.run_state.week_plan is not None
        ):
            new_entities.append(ConnectBoxWeekPlanSensor(coordinator))
            known.add((0, "week_plan"))
        if new_entities:
            async_add_entities(new_entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ConnectBoxSensor(ConnectBoxDeviceEntity, SensorEntity):
    """One decoded device telemetry value."""

    entity_description: ConnectBoxSensorDescription

    def __init__(self, coordinator, device_id: int, description) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_{description.key}"

    @property
    def native_value(self) -> int | float | str | datetime | None:
        """Return the decoded telemetry value."""
        data = self.device_data
        if data is None:
            return None
        room, device = data
        if self.entity_description.room_value_fn is not None:
            return self.entity_description.room_value_fn(room)
        return self.entity_description.value_fn(device)

    @property
    def available(self) -> bool:
        """Hide stale values when optional telemetry is not reported."""
        if not super().available:
            return False
        if self.entity_description.available_without_value:
            return True
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return extra attributes of the unit, if the description has any."""
        if self.entity_description.attributes_fn is None:
            return None
        data = self.device_data
        return self.entity_description.attributes_fn(data[1]) if data else None


class ConnectBoxFanStatusSensor(ConnectBoxDeviceEntity, SensorEntity):
    """What the unit is doing right now, with the details as attributes.

    One entity for dashboards: global standby, boost, summer ventilation,
    standby, level 1–4 or sensor-controlled operation, plus the fan speeds,
    the active situation, end times and active fault codes.
    """

    entity_description = SensorEntityDescription(
        key="fan_status",
        translation_key="fan_status",
        device_class=SensorDeviceClass.ENUM,
        options=FAN_STATUS_OPTIONS,
    )

    def __init__(self, coordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_{device_id}_fan_status"

    @property
    def native_value(self) -> str | None:
        """Return the combined operating status."""
        data = self.device_data
        if data is None or self.coordinator.data is None:
            return None
        room, device = data
        return fan_status(self.coordinator.data.run_state, room, device, time.time())

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose the values a status card needs in one place."""
        data = self.device_data
        if data is None or self.coordinator.data is None:
            return None
        room, device = data
        run_state = self.coordinator.data.run_state
        level = room.current_level(run_state.temperature_mode)
        codes = error_codes(device)
        return {
            "level": level if level in (0, 1, 2, 3, 4) else None,
            "situation": situation(run_state),
            "supply_fan_speed": SUPPLY_FAN_SPEED.value(device),
            "exhaust_fan_speed": EXHAUST_FAN_SPEED.value(device),
            "supply_only": supply_only_operation(device),
            "temporary_until": _temporary_until(room),
            "boost_until": _boost_until(room),
            "fault": bool(codes),
            "error_codes": codes,
        }

    @property
    def available(self) -> bool:
        """Report unavailable while the status cannot be determined."""
        return super().available and self.native_value is not None


def _plan_slots(day_plan: DayPlan) -> list[dict[str, str | None]]:
    """Return the plan's switching times with their situation."""
    return [
        {"start": _clock(minute), "situation": situation_name(mode)}
        for minute, mode in day_plan.slots
    ]


class ConnectBoxDayPlanSensor(ConnectBoxGatewayEntity, SensorEntity):
    """Today's daily schedule of the active weekly schedule.

    The state is the plan's name as set in the app; the attributes list its
    switching times with the situation and the active weekly schedule.
    """

    _attr_translation_key = "day_plan"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_day_plan"

    @property
    def native_value(self) -> str | None:
        """Return the name of today's plan."""
        snapshot = self.coordinator.data
        plan = snapshot.run_state.day_plan if snapshot is not None else None
        if plan is None:
            return None
        return plan.name or str(plan.plan_id)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return the switching times and the plan numbers."""
        snapshot = self.coordinator.data
        plan = snapshot.run_state.day_plan if snapshot is not None else None
        if plan is None:
            return None
        attributes: dict[str, object] = {
            "day_plan": plan.plan_id,
            "week_plan": snapshot.run_state.week_plan,
            "slots": _plan_slots(plan),
        }
        # The run state leaves out the plan's timed functions.
        listed = (
            snapshot.schedules.day_plan(plan.plan_id)
            if snapshot.schedules is not None
            else None
        )
        if listed is not None:
            attributes["functions"] = _plan_functions(listed, snapshot)
        return attributes

    @property
    def available(self) -> bool:
        """Report unavailable while no plan is reported."""
        return super().available and self.native_value is not None


SCHEDULED_FUNCTIONS = {2: "boost", 5: "summer_ventilation"}


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _plan_functions(
    day_plan: DayPlan, snapshot: GatewaySnapshot
) -> list[dict[str, object]]:
    """Return the plan's timed functions with the room they apply to."""
    rooms = {room.room_id: room.name for room in snapshot.rooms}
    return [
        {
            "start": _clock(item.start_minute),
            "function": SCHEDULED_FUNCTIONS.get(item.function, item.function),
            "duration_minutes": item.duration_minutes,
            "room": rooms.get(item.room_id) if item.room_id else None,
        }
        for item in day_plan.functions
    ]


class ConnectBoxWeekPlanSensor(ConnectBoxGatewayEntity, SensorEntity):
    """The active weekly schedule and all schedules stored on the gateway.

    The state is the name of the active weekly schedule as set in the app.
    The attributes list the daily schedule of each of its seven days, and
    all weekly and daily schedules with their switching times and functions.
    """

    _attr_translation_key = "week_plan"
    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        gateway_uuid = coordinator.entry.data[CONF_GATEWAY_UUID]
        self._attr_unique_id = f"{gateway_uuid}_week_plan"

    @property
    def native_value(self) -> str | None:
        """Return the name of the active weekly schedule."""
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.schedules is None:
            return None
        plan = snapshot.schedules.week_plan(snapshot.run_state.week_plan)
        if plan is None:
            return None
        return plan.name or str(plan.plan_id)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return the active plan's days and all stored schedules."""
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.schedules is None:
            return None
        schedules = snapshot.schedules

        def day_name(plan_id: int) -> str | None:
            plan = schedules.day_plan(plan_id)
            return (plan.name or str(plan.plan_id)) if plan is not None else None

        active = schedules.week_plan(snapshot.run_state.week_plan)
        return {
            "week_plan": snapshot.run_state.week_plan,
            "days": [day_name(plan_id) for plan_id in active.days] if active else None,
            "week_plans": [
                {
                    "id": plan.plan_id,
                    "name": plan.name,
                    "days": [day_name(plan_id) for plan_id in plan.days],
                }
                for plan in schedules.week_plans
            ],
            "day_plans": [
                {
                    "id": plan.plan_id,
                    "name": plan.name,
                    "slots": _plan_slots(plan),
                    "functions": _plan_functions(plan, snapshot),
                }
                for plan in schedules.day_plans
            ],
        }

    @property
    def available(self) -> bool:
        """Report unavailable while the active schedule is not known."""
        return super().available and self.native_value is not None
