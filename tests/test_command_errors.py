"""Check readable service failures without contacting a ConnectBox."""

import asyncio
import tempfile
import unittest
from dataclasses import replace
from types import MappingProxyType
from unittest.mock import Mock, patch
from uuid import UUID

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.coordinator import (
    ZehnderConnectBoxCoordinator,
)
from custom_components.zehnder_connectbox.models import (
    AttachedDevice,
    GatewaySnapshot,
    Room,
    RunState,
    SummerVentilationSettings,
    VentilationValue,
    VersionInfo,
)
from custom_components.zehnder_connectbox.number import (
    ConnectBoxSummerVentilationDuration,
)
from custom_components.zehnder_connectbox.protocol import ProtocolError
from custom_components.zehnder_connectbox.select import ConnectBoxSituation
from custom_components.zehnder_connectbox.session import GatewayResponseError
from custom_components.zehnder_connectbox.transport import CertificateMismatchError


class CommandErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="zehnder_connectbox",
            title="Synthetic",
            unique_id=str(UUID(int=1)),
            data={"gateway_uuid": str(UUID(int=1))},
            options={},
            source="user",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        self.client = ConnectBoxClient(
            "test.invalid", UUID(int=1), UUID(int=2), "a" * 64
        )
        device = AttachedDevice(
            device_id=7,
            product_type=30,
            product_variant=1,
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
            summer_ventilation_available=True,
        )
        self.room = Room(1, "Synthetic", None, 2, (VentilationValue(0, 2),), (device,))
        self.state = RunState(2, 0, False, None, False, None, ())
        self.snapshot = GatewaySnapshot(
            VersionInfo(None, None, None, None, None), self.state, (self.room,)
        )
        self.client._read_rooms = Mock(return_value=(self.room,))
        self.client._read_run_state = Mock(return_value=self.state)
        self.client._read_summer_settings = Mock(
            return_value=SummerVentilationSettings(False, 4)
        )
        self.session = Mock()
        self.client._session = self.session
        self.coordinator = ZehnderConnectBoxCoordinator(
            self.hass, self.entry, self.client
        )
        self.coordinator.data = self.snapshot

    async def asyncTearDown(self):
        await self.hass.async_stop()

    async def assert_translated(self, exception_type, key, command):
        with self.assertRaises(exception_type) as raised:
            await command
        self.assertEqual(raised.exception.translation_domain, "zehnder_connectbox")
        self.assertEqual(raised.exception.translation_key, key)
        self.assertIs(self.coordinator.data, self.snapshot)

    async def test_disabled_summer_mode_is_readable_and_never_written(self):
        await self.assert_translated(
            ServiceValidationError, "summer_disabled",
            self.coordinator.async_set_summer_ventilation(True),
        )
        self.session.request.assert_not_called()

    async def test_wrong_run_mode_and_active_interval_have_specific_errors(self):
        self.client._read_summer_settings.return_value = SummerVentilationSettings(
            True, 4
        )
        self.client._read_run_state.return_value = replace(self.state, run_mode=3)
        await self.assert_translated(
            ServiceValidationError, "summer_mode_unavailable",
            self.coordinator.async_set_summer_ventilation(True),
        )
        self.client._read_run_state.return_value = replace(
            self.state, summer_ventilation=True
        )
        await self.assert_translated(
            ServiceValidationError, "summer_running",
            self.coordinator.async_set_summer_configuration(duration_hours=5),
        )
        self.session.request.assert_not_called()

    async def test_sensor_mode_and_invalid_level_are_validation_errors(self):
        for level, key in ((5, "sensor_board_unavailable"), (9, "invalid_level")):
            with self.subTest(level=level):
                await self.assert_translated(
                    ServiceValidationError, key,
                    self.coordinator.async_set_level(1, level),
                )
        self.session.request.assert_not_called()

    async def test_missing_device_and_filter_runtime_are_readable(self):
        for device_id, key in (
            (99, "device_unavailable"), (7, "filter_runtime_unavailable")
        ):
            with self.subTest(device_id=device_id):
                await self.assert_translated(
                    ServiceValidationError, key,
                    self.coordinator.async_reset_filter_timer(device_id),
                )
        self.session.request.assert_not_called()

    async def test_gateway_transport_and_readback_failures_are_distinguished(self):
        for error, expected_type, key in (
            (
                GatewayResponseError(1, "private response"),
                ServiceValidationError,
                "command_rejected",
            ),
            (OSError("private host"), HomeAssistantError, "cannot_connect"),
            (ProtocolError("private frame"), HomeAssistantError, "command_failed"),
        ):
            with self.subTest(key=key):
                self.client.set_run_mode = Mock(side_effect=error)
                await self.assert_translated(
                    expected_type, key, self.coordinator.async_set_mode(2)
                )

    async def test_command_certificate_mismatch_starts_reauth(self):
        self.client.set_run_mode = Mock(
            side_effect=CertificateMismatchError("private pin")
        )
        with patch.object(ConfigEntry, "async_start_reauth") as reauth:
            await self.assert_translated(
                HomeAssistantError, "certificate_changed",
                self.coordinator.async_set_mode(2),
            )
            reauth.assert_called_once_with(self.hass)

    async def test_unexpected_programmer_errors_are_not_disguised(self):
        self.client.set_run_mode = Mock(side_effect=TypeError("synthetic bug"))
        with self.assertRaises(TypeError):
            await self.coordinator.async_set_mode(2)

    async def test_invalid_entity_options_are_translated(self):
        select = ConnectBoxSituation(self.coordinator)
        number = ConnectBoxSummerVentilationDuration(self.coordinator)
        await self.assert_translated(
            ServiceValidationError, "situation_not_selectable",
            select.async_select_option("asleep"),
        )
        await self.assert_translated(
            ServiceValidationError, "invalid_summer_duration",
            number.async_set_native_value(1.5),
        )

    async def test_successful_command_publishes_the_actual_snapshot(self):
        reported = replace(
            self.snapshot, run_state=replace(self.state, run_mode=4, standby=True)
        )
        self.client.set_run_mode = Mock(return_value=reported)
        with patch.object(self.coordinator, "_register_devices"):
            await self.coordinator.async_set_mode(4)
        self.assertIs(self.coordinator.data, reported)
        self.assertEqual(self.coordinator.data.run_state.run_mode, 4)
        self.client.set_run_mode.assert_called_once_with(4)

    async def test_commands_share_the_polling_io_lock(self):
        self.client.set_run_mode = Mock(return_value=self.snapshot)
        await self.coordinator._io_lock.acquire()
        with patch.object(self.coordinator, "_register_devices"):
            task = asyncio.create_task(self.coordinator.async_set_mode(2))
            await asyncio.sleep(0)
            self.client.set_run_mode.assert_not_called()
            self.coordinator._io_lock.release()
            await task
        self.client.set_run_mode.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
