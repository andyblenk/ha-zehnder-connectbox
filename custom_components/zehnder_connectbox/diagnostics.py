"""Privacy-preserving diagnostics for Zehnder ConnectBox."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant

from . import ZehnderConnectBoxConfigEntry
from .models import RunMode, TemperatureMode
from .profiles import (
    CO2_SENSOR_STATUS,
    EXHAUST_FAN_SPEED,
    EXTRACT_AIR_SENSOR_STATUS,
    EXTRACT_AIR_TEMPERATURE,
    HUMIDITY_SENSOR_STATUS,
    INCOMING_AIR_SENSOR_STATUS,
    INCOMING_AIR_TEMPERATURE,
    KNOWN_SENSOR_TYPES,
    SENSOR_TYPE_CO2,
    SENSOR_TYPE_HUMIDITY,
    SUPPLY_FAN_SPEED,
    board_reading,
    format_version,
    has_filter_warning,
    has_sensor_board,
    is_supported,
    product_name,
    sensor_available,
    supports_sensor_mode,
    temperature_value,
)


def _temperature_mode_name(mode: int) -> str:
    """Return a readable name for a room ventilation temperature mode."""
    try:
        return TemperatureMode(mode).name.lower()
    except ValueError:
        return str(mode)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZehnderConnectBoxConfigEntry
) -> dict[str, Any]:
    """Return only sanitized operational facts; never return local identities."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    if snapshot is None:
        return {"available": False}

    try:
        run_mode = RunMode(snapshot.run_state.run_mode).name.lower()
    except ValueError:
        run_mode = "unknown"
    devices = [device for room in snapshot.rooms for device in room.devices]
    return {
        "available": coordinator.last_update_success,
        "config_entry_version": entry.version,
        "gateway": {
            "system_version": snapshot.version.system_version,
            "comfonet_version": snapshot.version.comfonet_version,
            "property_list_version": snapshot.version.property_list_version,
            "zone_state_list_version": snapshot.version.zone_state_list_version,
            "connectbox_version": snapshot.version.connectbox_version,
            "run_mode": run_mode,
            "temperature_mode": snapshot.run_state.temperature_mode,
            "standby": snapshot.run_state.standby,
            "summer_ventilation_active": snapshot.run_state.summer_ventilation,
            "summer_ventilation_enabled": (
                snapshot.summer_ventilation_settings.enabled
                if snapshot.summer_ventilation_settings is not None
                else None
            ),
            "summer_ventilation_duration_hours": (
                snapshot.summer_ventilation_settings.duration_hours
                if snapshot.summer_ventilation_settings is not None
                else None
            ),
            "room_count": len(snapshot.rooms),
            "attached_device_count": len(devices),
        },
        "attached_devices": [
            {
                "model": product_name(device),
                "product_type": device.product_type,
                "product_variant": device.product_variant,
                "supported": is_supported(device),
                "summer_ventilation_available": device.summer_ventilation_available,
                "filter_warning": has_filter_warning(device),
                "filter_warning_reported": device.filter_warning,
                "fault_present": any(value != 0 for value in device.errors),
                "hardware_version": device.hardware_version,
                "software_version": format_version(device.software_version),
                "signal_strength": device.signal_strength,
                "temperature_telemetry_available": any(
                    value.key.class_id == 25 and value.value is not None
                    for value in device.properties
                ),
                "fan_telemetry_available": any(
                    value.key.class_id == 38
                    and value.key.property_id == 3
                    and value.value is not None
                    for value in device.properties
                ),
                "ventilation_level": room.level_for_mode(
                    snapshot.run_state.temperature_mode
                ),
                "current_level": room.current_level(
                    snapshot.run_state.temperature_mode
                ),
                "temporary_change_active": room.temporary_until is not None,
                "boost_active": room.boost_active(time.time()),
                "boost_duration": room.boost_duration,
                "ventilation_values": {
                    _temperature_mode_name(value.temperature_mode): value.level
                    for value in room.ventilation
                },
                "sensor_board_reported": has_sensor_board(device),
                "sensor_mode_supported": supports_sensor_mode(room, device),
                "humidity": board_reading(device, SENSOR_TYPE_HUMIDITY),
                "co2": board_reading(device, SENSOR_TYPE_CO2),
                # Readings of not yet mapped sensor types, such as a VOC board.
                "other_sensor_readings": {
                    str(sensor_type): value
                    for sensor_type, value in device.sensor_readings
                    if sensor_type not in KNOWN_SENSOR_TYPES
                },
                "extract_air_temperature": temperature_value(
                    device, EXTRACT_AIR_TEMPERATURE, EXTRACT_AIR_SENSOR_STATUS
                ),
                "incoming_air_temperature": temperature_value(
                    device, INCOMING_AIR_TEMPERATURE, INCOMING_AIR_SENSOR_STATUS
                ),
                "extract_air_sensor_available": sensor_available(
                    device, EXTRACT_AIR_SENSOR_STATUS
                ),
                "incoming_air_sensor_available": sensor_available(
                    device, INCOMING_AIR_SENSOR_STATUS
                ),
                "humidity_sensor_available": sensor_available(
                    device, HUMIDITY_SENSOR_STATUS
                ),
                "co2_sensor_available": sensor_available(device, CO2_SENSOR_STATUS),
                "exhaust_fan_speed": EXHAUST_FAN_SPEED.value(device),
                "supply_fan_speed": SUPPLY_FAN_SPEED.value(device),
            }
            for room in snapshot.rooms
            for device in room.devices
        ],
    }
