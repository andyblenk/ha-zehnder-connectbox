"""Synthetic offline checks for provisional ComfoAir 70 sensor support.

Run in an environment with Home Assistant and the manifest dependencies:
    python -m unittest discover -s tests -v
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.zehnder_connectbox.fan import ConnectBoxFan
from custom_components.zehnder_connectbox.models import (
    AttachedDevice,
    GatewaySnapshot,
    PropertyValue,
    Room,
    RunState,
    VentilationValue,
    VersionInfo,
)
from custom_components.zehnder_connectbox.profiles import (
    HUMIDITY_SENSOR_STATUS,
    PROPERTY_SPECS,
    board_reading,
    property_specs_for_device,
    sensor_available,
    supports_sensor_mode,
    supports_summer_ventilation,
)
from custom_components.zehnder_connectbox.sensor import async_setup_entry
from custom_components.zehnder_connectbox.session import GatewayResponseError


def make_room(variant=2, readings=((2, 45), (3, 780))):
    """Build invented device data without captures or local identifiers."""
    device = AttachedDevice(
        device_id=7,
        product_type=30,
        product_variant=variant,
        hardware_version=None,
        software_version=None,
        battery_state=None,
        signal_strength=None,
        errors=(),
        level_zero_supported=False,
        filter_warning=None,
        filter_runtime=None,
        filter_maximum=None,
        properties=(),
        sensor_readings=readings,
    )
    return Room(
        room_id=1,
        name="Synthetic room",
        room_type=None,
        target_level=2,
        ventilation=(VentilationValue(0, 2), VentilationValue(1, 1)),
        devices=(device,),
    )


def make_coordinator(room):
    """Provide only the coordinator boundary used by real HA entities."""
    snapshot = GatewaySnapshot(
        VersionInfo(None, None, None, None, None),
        RunState(2, 0, False, None, False, None, ()),
        (room,),
    )
    entry = SimpleNamespace(
        data={"gateway_uuid": str(UUID(int=1))},
        version=1,
        async_on_unload=Mock(),
    )
    coordinator = SimpleNamespace(
        data=snapshot,
        entry=entry,
        last_update_success=True,
        async_add_listener=Mock(),
    )
    entry.runtime_data = coordinator
    return coordinator


class SensorProfileTests(unittest.TestCase):
    def test_both_profiles_expose_board_readings_and_auto(self):
        for variant in (1, 2):
            with self.subTest(variant=variant):
                room = make_room(variant)
                device = room.devices[0]
                self.assertEqual(board_reading(device, 2), 45)
                self.assertEqual(board_reading(device, 3), 780)
                self.assertTrue(supports_sensor_mode(room, device))

    def test_either_board_sensor_including_zero_enables_auto(self):
        for readings in (((2, 0),), ((3, 780),)):
            with self.subTest(readings=readings):
                room = make_room(readings=readings)
                self.assertTrue(supports_sensor_mode(room, room.devices[0]))

    def test_existing_sensor_mode_is_recognized_without_readings(self):
        room = make_room(readings=())
        for configured in (False, True):
            with self.subTest(configured=configured):
                active = replace(
                    room,
                    target_level=2 if configured else 5,
                    ventilation=(VentilationValue(0, 2), VentilationValue(1, 5))
                    if configured
                    else room.ventilation,
                )
                self.assertTrue(supports_sensor_mode(active, active.devices[0]))

    def test_temperature_alone_does_not_enable_auto(self):
        for variant in (1, 2):
            room = make_room(variant, readings=((1, 215),))
            self.assertFalse(supports_sensor_mode(room, room.devices[0]))

    def test_unknown_profiles_do_not_inherit_sensor_support(self):
        room = replace(make_room(), target_level=5)
        for product_type, variant in ((30, 3), (30, None), (99, 2)):
            with self.subTest(product_type=product_type, variant=variant):
                device = replace(
                    room.devices[0],
                    product_type=product_type,
                    product_variant=variant,
                )
                self.assertIsNone(board_reading(device, 2))
                self.assertIsNone(board_reading(device, 3))
                self.assertFalse(supports_sensor_mode(room, device))

    def test_ca70_does_not_gain_unverified_property_reads_or_summer_control(self):
        room = make_room()
        device = replace(room.devices[0], summer_ventilation_available=True)
        self.assertEqual(property_specs_for_device(device), PROPERTY_SPECS)
        self.assertIsNone(sensor_available(device, HUMIDITY_SENSOR_STATUS))
        self.assertFalse(
            supports_summer_ventilation((replace(room, devices=(device,)),))
        )

    def test_status_only_board_detection_remains_cs50_only(self):
        for variant in (1, 2):
            with self.subTest(variant=variant):
                room = make_room(variant, readings=())
                status = PropertyValue(HUMIDITY_SENSOR_STATUS.request_key(30), b"\x00")
                device = replace(room.devices[0], properties=(status,))
                self.assertEqual(supports_sensor_mode(room, device), variant == 1)


class SensorEntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_sensors_appear_once_and_missing_values_disappear(self):
        room = make_room(readings=())
        coordinator = make_coordinator(room)
        entities = []
        await async_setup_entry(None, coordinator.entry, entities.extend)
        self.assertFalse(
            any(e.entity_description.key in ("humidity", "co2") for e in entities)
        )

        coordinator.data = replace(coordinator.data, rooms=(make_room(),))
        update = coordinator.async_add_listener.call_args.args[0]
        update()
        update()
        board_entities = {
            entity.entity_description.key: entity
            for entity in entities
            if entity.entity_description.key in ("humidity", "co2")
        }
        self.assertEqual(len(entities), len({entity.unique_id for entity in entities}))
        self.assertEqual(set(board_entities), {"humidity", "co2"})
        self.assertEqual(board_entities["humidity"].native_value, 45)
        self.assertEqual(board_entities["co2"].native_value, 780)
        self.assertEqual(board_entities["humidity"].native_unit_of_measurement, "%")
        self.assertEqual(board_entities["co2"].native_unit_of_measurement, "ppm")
        self.assertTrue(all(entity.available for entity in board_entities.values()))

        coordinator.data = replace(coordinator.data, rooms=(room,))
        self.assertTrue(
            all(entity.native_value is None for entity in board_entities.values())
        )
        self.assertFalse(any(entity.available for entity in board_entities.values()))

    async def test_fan_recognizes_auto_and_returns_to_manual_level(self):
        room = make_room()
        coordinator = make_coordinator(room)
        fan = ConnectBoxFan(coordinator, 7)
        self.assertIn("auto", fan.preset_modes)
        coordinator.data = replace(
            coordinator.data, rooms=(replace(room, target_level=5),)
        )
        self.assertTrue(fan.available)
        self.assertTrue(fan.is_on)
        self.assertEqual(fan.preset_mode, "auto")
        self.assertIsNone(fan.percentage)
        coordinator.data = replace(coordinator.data, rooms=(room,))
        self.assertEqual(fan.preset_mode, "level_2")
        self.assertEqual(fan.percentage, 50)
        coordinator.data = replace(coordinator.data, rooms=(make_room(readings=()),))
        self.assertNotIn("auto", fan.preset_modes)

    async def test_diagnostics_include_ca70_readings_and_mode_support(self):
        coordinator = make_coordinator(make_room())
        result = await async_get_config_entry_diagnostics(None, coordinator.entry)
        device = result["attached_devices"][0]
        self.assertEqual(device["model"], "ComfoAir 70")
        self.assertEqual(device["humidity"], 45)
        self.assertEqual(device["co2"], 780)
        self.assertTrue(device["sensor_board_reported"])
        self.assertTrue(device["sensor_mode_supported"])
        self.assertNotIn("Synthetic room", str(result))


class SensorCommandTests(unittest.TestCase):
    def make_client(self, room):
        client = ConnectBoxClient("test.invalid", UUID(int=1), UUID(int=2), "synthetic")
        client._version = VersionInfo(None, None, None, None, None)
        client._session = Mock()
        client._read_rooms = Mock(return_value=(room,))
        client._read_run_state = Mock(
            return_value=RunState(2, 0, False, None, False, None, ())
        )
        return client

    def test_auto_and_manual_writes_preserve_other_situations_and_read_back(self):
        room = make_room()
        client = self.make_client(room)
        session = client._session
        auto = replace(
            room,
            target_level=5,
            ventilation=(VentilationValue(0, 5), VentilationValue(1, 1)),
        )
        client._read_rooms.side_effect = [(room,), (auto,), (auto,)]
        result = client.set_level(1, 5)
        session.request.assert_called_once_with(
            205, 206, bytes.fromhex("0a0e0801620408001005620408011001")
        )
        self.assertEqual(result.rooms[0].target_level, 5)
        self.assertEqual(result.rooms[0].ventilation[1].level, 1)

        session.request.reset_mock()
        client._read_rooms.side_effect = [(auto,), (room,), (room,)]
        result = client.set_level(1, 2)
        session.request.assert_called_once_with(
            205, 206, bytes.fromhex("0a0e0801620408001002620408011001")
        )
        self.assertEqual(result.rooms[0].target_level, 2)

    def test_auto_is_refused_without_board_or_for_unknown_profile(self):
        for room in (make_room(readings=()), make_room(variant=3)):
            with self.subTest(variant=room.devices[0].product_variant):
                client = self.make_client(room)
                session = client._session
                with self.assertRaises(ValueError):
                    client.set_level(1, 5)
                session.request.assert_not_called()

    def test_gateway_rejection_does_not_report_auto_success(self):
        client = self.make_client(make_room())
        client._session.request.side_effect = GatewayResponseError(
            1, "Synthetic rejection"
        )
        with self.assertRaises(GatewayResponseError):
            client.set_level(1, 5)
        self.assertIsNone(client._session)


if __name__ == "__main__":
    unittest.main()
