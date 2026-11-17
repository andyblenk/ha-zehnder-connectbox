"""Offline reauthentication checks using real Home Assistant entry handling."""

import asyncio
import tempfile
import unittest
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from homeassistant.config_entries import ConfigEntries, ConfigEntry, SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.zehnder_connectbox.config_flow import (
    ZehnderConnectBoxConfigFlow,
)
from custom_components.zehnder_connectbox.client import PairingError
from custom_components.zehnder_connectbox.coordinator import (
    ZehnderConnectBoxCoordinator,
)
from custom_components.zehnder_connectbox.models import DiscoveredGateway, PairingData
from custom_components.zehnder_connectbox.protocol import ProtocolError
from custom_components.zehnder_connectbox.transport import (
    CertificateMismatchError,
    TransportError,
)


class ReauthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="zehnder_connectbox",
            title="Custom title",
            unique_id=str(UUID(int=1)),
            data={
                "host": "test.invalid",
                "gateway_uuid": str(UUID(int=1)),
                "app_uuid": str(UUID(int=2)),
                "remote_uuid": str(UUID(int=3)),
                "app_id": 1,
                "certificate_sha256": "a" * 64,
                "retained_setting": True,
            },
            options={"retained_option": True},
            pref_disable_new_entities=True,
            source="user",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.flow = ZehnderConnectBoxConfigFlow()
        self.flow.hass = self.hass
        self.flow.context = {"source": SOURCE_REAUTH, "entry_id": self.entry.entry_id}
        self.flow.handler = "zehnder_connectbox"
        self.gateway = DiscoveredGateway("test.invalid", UUID(int=1), "Synthetic")
        self.pairing = PairingData(UUID(int=4), UUID(int=5), 2, "b" * 64)

    async def asyncTearDown(self):
        await self.hass.async_stop()

    async def test_certificate_mismatch_requires_reauth_instead_of_retry(self):
        client = Mock()
        client.read_snapshot.side_effect = CertificateMismatchError("synthetic")
        coordinator = ZehnderConnectBoxCoordinator(self.hass, self.entry, client)
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)

    async def test_connection_failure_is_not_an_auth_failure(self):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        client = Mock()
        client.read_snapshot.side_effect = TransportError("synthetic")
        coordinator = ZehnderConnectBoxCoordinator(self.hass, self.entry, client)
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_reauth_requires_confirmation_before_discovery_or_pairing(self):
        original = dict(self.entry.data)
        with patch(
            "custom_components.zehnder_connectbox.config_flow.discover_gateways"
        ) as discover, patch(
            "custom_components.zehnder_connectbox.config_flow.ConnectBoxClient.pair"
        ) as pair:
            result = await self.flow.async_step_reauth(self.entry.data)
            self.assertEqual(result["type"], "form")
            self.assertEqual(result["step_id"], "reauth_confirm")
            discover.assert_not_called()
            pair.assert_not_called()
        self.assertEqual(dict(self.entry.data), original)

    async def test_wrong_or_offline_gateway_does_not_change_saved_trust(self):
        cases = (
            ({}, "cannot_connect"),
            (
                {UUID(int=9): DiscoveredGateway("test.invalid", UUID(int=9))},
                "wrong_gateway",
            ),
        )
        for discovered, error in cases:
            with self.subTest(error=error):
                original = dict(self.entry.data)
                with patch(
                    "custom_components.zehnder_connectbox.config_flow."
                    "discover_gateways",
                    return_value=discovered,
                ), patch(
                    "custom_components.zehnder_connectbox.config_flow."
                    "ConnectBoxClient.check_gateway_identity",
                    side_effect=TransportError("synthetic offline gateway"),
                    create=True,
                ), patch(
                    "custom_components.zehnder_connectbox.config_flow."
                    "ConnectBoxClient.pair"
                ) as pair:
                    result = await self.flow.async_step_reauth_confirm(
                        {"host": "test.invalid", "pairing_name": "Home Assistant"}
                    )
                self.assertEqual(result["errors"]["base"], error)
                pair.assert_not_called()
                self.assertEqual(dict(self.entry.data), original)

    async def test_success_updates_existing_entry_only_after_physical_pairing(self):
        original_id = self.entry.entry_id
        original_options = dict(self.entry.options)
        moved_gateway = DiscoveredGateway("moved.invalid", UUID(int=1))
        with patch(
            "custom_components.zehnder_connectbox.config_flow.discover_gateways",
            return_value={UUID(int=1): moved_gateway},
        ), patch(
            "custom_components.zehnder_connectbox.config_flow.ConnectBoxClient.pair",
            return_value=self.pairing,
        ) as pair, patch.object(
            ConfigEntries, "async_schedule_reload"
        ) as reload, patch.object(
            ConfigEntries, "_async_save_and_notify"
        ):
            result = await self.flow.async_step_reauth_confirm(
                {"host": "moved.invalid", "pairing_name": "Test HA"}
            )
            self.assertIn(result["type"], ("progress", "progress_done"))
            self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)
            await self.flow._pair_task
            result = await self.flow.async_step_pair()
            self.assertEqual(result["type"], "progress_done")
            result = await self.flow.async_step_finish()
            self.assertEqual(result["type"], "abort")
            self.assertEqual(result["reason"], "reauth_successful")
            reload.assert_called_once_with(original_id)
            self.assertEqual(pair.call_args.args[:2], ("moved.invalid", UUID(int=1)))
            self.assertEqual(pair.call_args.kwargs["nickname"], "Test HA")
        self.assertEqual(len(self.hass.config_entries.async_entries()), 1)
        self.assertEqual(self.entry.entry_id, original_id)
        self.assertEqual(self.entry.unique_id, str(UUID(int=1)))
        self.assertEqual(self.entry.title, "Custom title")
        self.assertEqual(dict(self.entry.options), original_options)
        self.assertTrue(self.entry.pref_disable_new_entities)
        self.assertTrue(self.entry.data["retained_setting"])
        self.assertEqual(self.entry.data["certificate_sha256"], "b" * 64)
        self.assertEqual(self.entry.data["app_uuid"], str(UUID(int=4)))
        self.assertEqual(self.entry.data["remote_uuid"], str(UUID(int=5)))
        self.assertEqual(self.entry.data["app_id"], 2)
        self.assertEqual(self.entry.data["host"], "moved.invalid")

    async def test_pairing_failure_leaves_old_data_and_can_be_retried(self):
        original = dict(self.entry.data)
        self.flow._selected = self.gateway
        self.flow._pair_task = asyncio.get_running_loop().create_future()
        self.flow._pair_task.set_exception(PairingError("synthetic"))
        result = await self.flow.async_step_finish()
        self.assertEqual(result["step_id"], "pair_failed")
        self.assertEqual(dict(self.entry.data), original)
        self.assertIsNone(self.flow._pair_task)
        with patch.object(
            self.flow, "_async_pair", new=AsyncMock(return_value=self.pairing)
        ):
            result = await self.flow.async_step_pair_failed({})
            self.assertIn(result["type"], ("progress", "progress_done"))
            await self.flow._pair_task

    async def test_invalid_pairing_name_does_not_start_discovery(self):
        with patch(
            "custom_components.zehnder_connectbox.config_flow.discover_gateways"
        ) as discover:
            result = await self.flow.async_step_reauth_confirm(
                {"host": "test.invalid", "pairing_name": " "}
            )
            self.assertEqual(result["errors"]["pairing_name"], "invalid_pairing_name")
            discover.assert_not_called()

    async def test_known_host_can_reauthenticate_without_udp_discovery(self):
        with patch(
            "custom_components.zehnder_connectbox.config_flow.discover_gateways",
            return_value={},
        ), patch(
            "custom_components.zehnder_connectbox.config_flow."
            "ConnectBoxClient.check_gateway_identity",
            create=True,
        ) as check, patch.object(
            self.flow, "_async_pair", new=AsyncMock(return_value=self.pairing)
        ):
            result = await self.flow.async_step_reauth_confirm(
                {"host": "test.invalid", "pairing_name": "Home Assistant"}
            )
            self.assertIn(result["type"], ("progress", "progress_done"))
            check.assert_called_once_with("test.invalid", UUID(int=1))
            await self.flow._pair_task
        self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)

    async def test_tls_identity_mismatch_does_not_start_pairing(self):
        with patch(
            "custom_components.zehnder_connectbox.config_flow.discover_gateways",
            return_value={},
        ), patch(
            "custom_components.zehnder_connectbox.config_flow."
            "ConnectBoxClient.check_gateway_identity",
            side_effect=ProtocolError("synthetic identity mismatch"),
        ), patch.object(self.flow, "_async_pair", new=AsyncMock()) as pair:
            result = await self.flow.async_step_reauth_confirm(
                {"host": "test.invalid", "pairing_name": "Home Assistant"}
            )
        self.assertEqual(result["errors"]["base"], "cannot_verify_gateway")
        pair.assert_not_called()
        self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)

    async def test_pairing_timeout_keeps_existing_trust(self):
        self.flow._selected = self.gateway
        self.flow._pair_task = asyncio.get_running_loop().create_future()
        self.flow._pair_task.set_exception(TimeoutError())
        result = await self.flow.async_step_finish()
        self.assertEqual(result["step_id"], "pair_failed")
        self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)

    async def test_finish_cannot_replace_the_entry_with_a_different_gateway(self):
        self.flow._selected = DiscoveredGateway("test.invalid", UUID(int=9))
        self.flow._pair_task = asyncio.get_running_loop().create_future()
        self.flow._pair_task.set_result(self.pairing)
        result = await self.flow.async_step_finish()
        self.assertEqual(result["reason"], "wrong_gateway")
        self.assertEqual(self.entry.data["certificate_sha256"], "a" * 64)

    async def test_normal_onboarding_still_creates_an_entry(self):
        self.flow.context = {"source": "user"}
        self.flow._selected = self.gateway
        self.flow._pair_task = asyncio.get_running_loop().create_future()
        self.flow._pair_task.set_result(self.pairing)
        result = await self.flow.async_step_finish()
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["gateway_uuid"], str(UUID(int=1)))
        self.assertEqual(result["data"]["certificate_sha256"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
