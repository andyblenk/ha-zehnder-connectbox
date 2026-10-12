"""Offline airflow tests using synthetic models, never a gateway connection.

Run with Python 3.12+ and the integration's Home Assistant dependencies:
    python -m unittest discover -s tests
The same file can be piped into an existing HA container's python process.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

# Also support the read-only integration mount in a local HA test container.
if Path("/config/custom_components/zehnder_connectbox").is_dir():
    sys.path.insert(0, "/config")

from homeassistant.exceptions import ServiceValidationError

from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.coordinator import (
    ZehnderConnectBoxCoordinator,
)
from custom_components.zehnder_connectbox.models import (
    AirflowMode,
    AttachedDevice,
    GatewaySnapshot,
    PropertyKey,
    PropertyValue,
    Room,
    RunMode,
    RunState,
    VentilationValue,
    VersionInfo,
)
from custom_components.zehnder_connectbox.profiles import supply_only_operation
from custom_components.zehnder_connectbox.protobuf import (
    bytes_value,
    decode_fields,
    encode_bytes,
    encode_uint,
    uint_value,
)
from custom_components.zehnder_connectbox.protocol import (
    OperationType,
    ProtocolError,
    decode_rooms,
    encode_airflow_mode,
)
from custom_components.zehnder_connectbox.select import (
    ConnectBoxAirflowMode,
    async_setup_entry,
)


def device(device_id=7, *, variant=1, product_type=30, enabled=1):
    """Build an unrelated synthetic unit and its fan-status property."""
    properties = () if enabled is None else (
        PropertyValue(PropertyKey(product_type, 0, 0, 38, 0, 5), bytes((enabled,))),
    )
    return AttachedDevice(
        device_id, product_type, variant, None, None, None, None, (),
        False, None, None, None, properties,
    )


def snapshot(mode=AirflowMode.NORMAL, *, unit=None, state=None, **room_changes):
    room = Room(9, "Synthetic", 1, 2, (VentilationValue(0, 2),), (unit or device(),))
    room = replace(room, airflow_mode=mode, **room_changes)
    state = state or RunState(RunMode.MANUAL, 0, False, 2, False, None, ())
    return GatewaySnapshot(VersionInfo(None, None, None, None, None), state, (room,))


def event(value, *, event_id=38):
    record = encode_bytes(1, encode_uint(1, event_id))
    if value is not None:
        record += encode_bytes(2, value)
    return encode_bytes(101, record)


def decoded_mode(events=b""):
    return decode_rooms(encode_bytes(1, encode_uint(1, 9) + events))[0].airflow_mode


class ProtocolTests(unittest.TestCase):
    def test_single_byte_write_has_only_room_and_airflow_event(self):
        for mode in (AirflowMode.NORMAL, AirflowMode.SUPPLY_ONLY):
            with self.subTest(mode=mode):
                fields = decode_fields(encode_airflow_mode(9, mode))
                self.assertEqual([field.number for field in fields], [1, 2])
                self.assertEqual(uint_value(fields, 1), 9)
                record = decode_fields(bytes_value(fields, 2))
                self.assertEqual([field.number for field in record], [1, 2])
                key = decode_fields(bytes_value(record, 1))
                self.assertEqual(uint_value(key, 1), 38)
                self.assertEqual(bytes_value(record, 2), bytes((mode,)))

    def test_invalid_write_values(self):
        for mode in (True, False, -1, 2, 3, 256, 1.0, "1", None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                encode_airflow_mode(9, mode)
        for room_id in (True, -1, 256, 1.0, "9", None):
            with self.subTest(room_id=room_id), self.assertRaises(ValueError):
                encode_airflow_mode(room_id, AirflowMode.NORMAL)

    def test_decode_reported_modes_in_short_and_padded_records(self):
        for mode in AirflowMode:
            for length in (1, 8):
                with self.subTest(mode=mode, length=length):
                    value = bytes((mode,)) + bytes(length - 1)
                    self.assertEqual(decoded_mode(event(value)), mode)

    def test_absent_unknown_invalid_and_duplicate_records_remain_unknown(self):
        for records in (
            b"", event(None), event(b""), event(b"\xff"), event(bytes(9)),
            event(b"\x01", event_id=99), event(b"\x01") * 2,
            event(b"\x00") + event(b"\x01"), encode_bytes(101, b"\x00"),
        ):
            with self.subTest(records=records):
                self.assertIsNone(decoded_mode(records))
        self.assertEqual(decoded_mode(event(b"\x00")), AirflowMode.NORMAL)

    def test_unrelated_room_fields_survive_mode_decode(self):
        room = encode_uint(1, 9) + encode_uint(21, 4)
        room += encode_bytes(19, encode_uint(1, 0) + encode_uint(2, 3))
        room += event(b"\x01")
        decoded = decode_rooms(encode_bytes(1, room))[0]
        self.assertEqual(decoded.target_level, 4)
        self.assertEqual(decoded.ventilation, (VentilationValue(0, 3),))
        self.assertEqual(decoded.raw, room)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = ConnectBoxClient(
            "invalid.example", UUID(int=1), UUID(int=2), "synthetic"
        )
        self.session = Mock()
        self.client._connected_session = Mock(return_value=self.session)
        self.client.close = Mock()

    def prepare(self, before, after):
        self.client.read_snapshot = Mock(side_effect=[before, after])
        self.client._read_rooms = Mock(return_value=after.rooms)

    def test_supply_and_normal_confirm_without_modifying_levels_or_global_state(self):
        for requested in (AirflowMode.SUPPLY_ONLY, AirflowMode.NORMAL):
            with self.subTest(requested=requested):
                self.session.reset_mock()
                previous = AirflowMode.NORMAL if requested else AirflowMode.SUPPLY_ONLY
                before = snapshot(previous)
                after = snapshot(requested)
                self.prepare(before, after)
                result = self.client.set_airflow_mode(7, requested)
                self.session.request.assert_called_once_with(
                    OperationType.SET_AIRFLOW_MODE_REQUEST,
                    OperationType.SET_AIRFLOW_MODE_CONFIRM,
                    encode_airflow_mode(9, requested),
                )
                self.assertIs(result, after)
                self.assertEqual(result.run_state, before.run_state)
                self.assertEqual(
                    result.rooms[0].ventilation, before.rooms[0].ventilation
                )
                # Gateway-reported supply mode must not manufacture fan stoppage.
                self.assertIs(supply_only_operation(result.rooms[0].devices[0]), False)
                self.assertEqual(self.client.read_snapshot.call_count, 2)
                self.client.read_snapshot.assert_called_with(refresh_properties=True)

    def test_already_reported_mode_refreshes_without_write(self):
        state = snapshot(AirflowMode.SUPPLY_ONLY)
        self.prepare(state, state)
        self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
        self.session.request.assert_not_called()

    def test_absent_mode_is_not_an_idempotent_normal(self):
        self.prepare(snapshot(None), snapshot(AirflowMode.NORMAL))
        self.client.set_airflow_mode(7, AirflowMode.NORMAL)
        self.session.request.assert_called_once()

    def test_manual_levels_and_situations_preserve_configured_mapping(self):
        for level in (1, 2, 3, 4):
            for situation in (0, 2):
                with self.subTest(level=level, situation=situation):
                    state = replace(snapshot().run_state, temperature_mode=situation)
                    before = snapshot(target_level=level, state=state)
                    after = replace(
                        before,
                        rooms=(replace(before.rooms[0], airflow_mode=AirflowMode.SUPPLY_ONLY),),
                    )
                    self.prepare(before, after)
                    self.assertIs(
                        self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY), after
                    )

    def test_delayed_readback_is_polled_until_confirmed(self):
        before, after = snapshot(), snapshot(AirflowMode.SUPPLY_ONLY)
        self.prepare(before, after)
        self.client._read_rooms.side_effect = [before.rooms, after.rooms]
        with patch("custom_components.zehnder_connectbox.client.time.sleep"):
            self.assertIs(
                self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY), after
            )
        self.assertEqual(self.client._read_rooms.call_count, 2)

    def test_property_cache_does_not_preserve_a_missing_room_mode(self):
        self.client._remember_device_properties(snapshot(AirflowMode.SUPPLY_ONLY).rooms)
        rooms = self.client._restore_device_properties(
            snapshot(None, unit=device(enabled=None)).rooms
        )
        self.assertIsNone(rooms[0].airflow_mode)
        self.assertIs(supply_only_operation(rooms[0].devices[0]), False)

    def test_refuse_unsupported_or_ambiguous_scope_before_write(self):
        states = [
            snapshot(unit=device(variant=2)), snapshot(unit=device(variant=None)),
            snapshot(unit=device(product_type=99)), snapshot(unit=device(enabled=None)),
            snapshot(unit=device(enabled=2)), snapshot(devices=(device(), device(8))),
            replace(snapshot(), rooms=()),
        ]
        for before in states:
            with self.subTest(before=before):
                self.prepare(before, before)
                self.session.reset_mock()
                with self.assertRaises(ProtocolError):
                    self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
                self.session.request.assert_not_called()

    def test_rejected_fan_sequence_prevents_write_even_with_cached_flag(self):
        self.prepare(snapshot(), snapshot(AirflowMode.SUPPLY_ONLY))
        self.client._rejected_fan_state_devices.add((7, 30, 1))
        with self.assertRaises(ProtocolError):
            self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
        self.session.request.assert_not_called()

    def test_start_conditions_are_checked_from_fresh_state(self):
        state = snapshot().run_state
        states = [snapshot(state=replace(state, **change)) for change in (
            {"run_mode": RunMode.AUTOMATIC}, {"run_mode": RunMode.OFF},
            {"run_mode": RunMode.ANTIFREEZE}, {"temperature_mode": 3},
            {"standby": True}, {"standby": None},
            {"summer_ventilation": True}, {"summer_ventilation": None},
        )]
        states += [snapshot(target_level=level) for level in (0, 5)]
        states += [snapshot(boost_until=2**40)]
        for before in states:
            with self.subTest(before=before):
                self.prepare(before, snapshot(AirflowMode.SUPPLY_ONLY))
                self.session.reset_mock()
                with self.assertRaises(ProtocolError):
                    self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
                self.session.request.assert_not_called()

    def test_normal_restoration_available_after_start_conditions_change(self):
        state = replace(snapshot().run_state, run_mode=RunMode.OFF, standby=True)
        self.prepare(
            snapshot(AirflowMode.SUPPLY_ONLY, state=state), snapshot(state=state)
        )
        self.client.set_airflow_mode(7, AirflowMode.NORMAL)
        self.session.request.assert_called_once()

    def test_acknowledgement_without_mode_readback_fails_and_closes(self):
        for reported in (None, AirflowMode.NORMAL, AirflowMode.EXHAUST_ONLY):
            with self.subTest(reported=reported):
                self.prepare(snapshot(), snapshot(reported))
                with patch(
                    "custom_components.zehnder_connectbox.client.COMMAND_SETTLE_TIMEOUT", 0
                ):
                    with self.assertRaises(ProtocolError):
                        self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
                self.client.close.assert_called()

    def test_final_refresh_must_still_confirm_target_and_mode(self):
        for after in (
            snapshot(None), snapshot(room_id=10), snapshot(unit=device(variant=2)),
            replace(snapshot(), rooms=()),
        ):
            with self.subTest(after=after):
                self.prepare(snapshot(), after)
                self.client._read_rooms = Mock(
                    return_value=snapshot(AirflowMode.SUPPLY_ONLY).rooms
                )
                with self.assertRaises(ProtocolError):
                    self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
                self.client.close.assert_called()

    def test_write_rejection_is_not_swallowed(self):
        self.prepare(snapshot(), snapshot(AirflowMode.SUPPLY_ONLY))
        self.session.request.side_effect = ProtocolError("synthetic rejection")
        with self.assertRaises(ProtocolError):
            self.client.set_airflow_mode(7, AirflowMode.SUPPLY_ONLY)
        self.client.close.assert_called_once()

    def test_unsupported_options_do_not_even_read_state(self):
        self.client.read_snapshot = Mock()
        for mode in (True, False, 1.0, 2, 3, None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.client.set_airflow_mode(7, mode)
        self.client.read_snapshot.assert_not_called()


class EntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = SimpleNamespace(
            data=snapshot(), last_update_success=True,
            entry=SimpleNamespace(data={"gateway_uuid": "synthetic"}),
            async_set_airflow_mode=AsyncMock(),
            async_add_listener=Mock(return_value=lambda: None),
        )
        self.entity = ConnectBoxAirflowMode(self.coordinator, 7)

    async def test_selection_delegates_without_optimistic_status(self):
        await self.entity.async_select_option("supply_only")
        self.coordinator.async_set_airflow_mode.assert_awaited_once_with(
            7, AirflowMode.SUPPLY_ONLY
        )
        self.assertEqual(self.entity.current_option, "normal")
        self.assertTrue(self.entity.entity_registry_enabled_default)

    async def test_missing_mode_remains_unknown_with_control_available(self):
        self.coordinator.data = snapshot(None)
        self.assertIsNone(self.entity.current_option)
        self.assertTrue(self.entity.available)

    async def test_other_modes_are_displayed_but_cannot_be_written(self):
        for mode, option in (
            (AirflowMode.EXHAUST_ONLY, "exhaust_only"), (AirflowMode.SUMMER, "summer")
        ):
            self.coordinator.data = snapshot(mode)
            self.assertEqual(self.entity.current_option, option)
            self.assertIn(option, self.entity.options)
            with self.assertRaises(ServiceValidationError):
                await self.entity.async_select_option(option)
        self.coordinator.async_set_airflow_mode.assert_not_awaited()

    async def test_unavailable_when_profile_scope_device_or_connection_changes(self):
        for data in (
            None, snapshot(unit=device(variant=2)),
            snapshot(devices=(device(), device(8))), replace(snapshot(), rooms=()),
        ):
            self.coordinator.data = data
            self.assertFalse(self.entity.available)
            with self.assertRaises(ServiceValidationError):
                await self.entity.async_select_option("normal")
        self.coordinator.data = snapshot()
        self.coordinator.last_update_success = False
        self.assertFalse(self.entity.available)

    async def test_dynamic_setup_adds_airflow_once_after_fan_status_appears(self):
        self.coordinator.data = snapshot(unit=device(enabled=None))
        entry = SimpleNamespace(runtime_data=self.coordinator, async_on_unload=Mock())
        added = []
        await async_setup_entry(None, entry, added.extend)
        self.assertFalse(
            any(isinstance(entity, ConnectBoxAirflowMode) for entity in added)
        )
        listener = self.coordinator.async_add_listener.call_args.args[0]
        self.coordinator.data = snapshot()
        listener()
        listener()
        self.assertEqual(
            sum(isinstance(entity, ConnectBoxAirflowMode) for entity in added), 1
        )

    async def test_coordinator_serializes_and_publishes_confirmed_snapshot(self):
        after = snapshot(AirflowMode.SUPPLY_ONLY)
        client = SimpleNamespace(set_airflow_mode=Mock(return_value=after))
        accept = Mock()
        executor = AsyncMock(side_effect=lambda function, *args: function(*args))
        coordinator = SimpleNamespace(
            _io_lock=asyncio.Lock(), client=client,
            hass=SimpleNamespace(async_add_executor_job=executor),
            _accept_command_snapshot=accept, _last_property_refresh=0,
        )
        await ZehnderConnectBoxCoordinator.async_set_airflow_mode(
            coordinator, 7, AirflowMode.SUPPLY_ONLY
        )
        accept.assert_called_once_with(after)
        self.assertGreater(coordinator._last_property_refresh, 0)
        client.set_airflow_mode.side_effect = ProtocolError("synthetic failure")
        accept.reset_mock()
        with self.assertRaises(ProtocolError):
            await ZehnderConnectBoxCoordinator.async_set_airflow_mode(
                coordinator, 7, AirflowMode.NORMAL
            )
        accept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
