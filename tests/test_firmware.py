"""Check the firmware package, its upload and the update entity offline."""

import asyncio
import struct
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.exceptions import HomeAssistantError
from test_app_parity import (
    APP,
    GATEWAY,
    FakeCoordinator,
    rooms_message,
    snapshot,
)

from custom_components.zehnder_connectbox import update
from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.errors import CommandValidationError
from custom_components.zehnder_connectbox.firmware import (
    FirmwareError,
    find_firmware,
    parse_firmware,
)
from custom_components.zehnder_connectbox.models import UpdateProgress, VersionInfo
from custom_components.zehnder_connectbox.protobuf import (
    bytes_value,
    decode_fields,
    encode_uint,
    uint_value,
)
from custom_components.zehnder_connectbox.protocol import (
    OperationType,
    ProtocolError,
    UpgradeCommand,
    encode_upgrade,
)
from custom_components.zehnder_connectbox.session import GatewayResponseError


def package(version=(1, 9, 0), payload=b"\x5a" * 1000):
    """A package laid out like the app's Gateway-1.8.1.fw."""
    text = ".".join(str(part) for part in version).encode()
    header = bytearray(b"Info")
    header += struct.pack("<IIII", 72, 2, len(payload), zlib.crc32(payload))
    header += struct.pack("<IIII", 0, *version)
    header += text.ljust(32, b"\0")
    header += b"\x03\x01"  # start of the component table
    # The app's file has 69 bytes before the payload.
    header = header.ljust(69, b"\0")
    return bytes(header) + payload


def packed(major, minor, patch):
    return (major << 20) | (minor << 10) | patch


class PackageTests(unittest.TestCase):
    def test_valid_package(self):
        found = parse_firmware(package(), Path("Gateway-1.9.0.fw"))
        self.assertEqual((found.version, found.version_text), ((1, 9, 0), "1.9.0"))

    def test_damaged_packages_are_refused(self):
        good = package()
        cases = {
            "magic": b"Xnfo" + good[4:],
            "crc": good[:-1] + b"\0",
            "short": good[:-10],
            "text": good.replace(b"1.9.0", b"1.9.1"),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(FirmwareError):
                parse_firmware(data, Path("x.fw"))

    def test_newest_valid_package_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "Gateway-1.8.1.fw").write_bytes(package((1, 8, 1)))
            Path(folder, "Gateway-1.9.0.fw").write_bytes(package((1, 9, 0)))
            Path(folder, "Gateway-2.0.0.fw").write_bytes(package((2, 0, 0))[:-1])
            Path(folder, "notes.txt").write_text("not a package")
            found, rejected = find_firmware(Path(folder))
            self.assertEqual(found.version, (1, 9, 0))
            self.assertEqual(len(rejected), 1)
            self.assertIn("Gateway-2.0.0.fw", rejected[0])
        self.assertEqual(find_firmware(Path(folder)), (None, []))


class UpgradeRequestTests(unittest.TestCase):
    def test_requests_as_the_app_builds_them(self):
        start = decode_fields(encode_upgrade(UpgradeCommand.START, b"ab"))
        self.assertEqual((uint_value(start, 1), bytes_value(start, 2)), (0, b"ab"))
        self.assertEqual(encode_upgrade(UpgradeCommand.FINISH), encode_uint(1, 2))
        self.assertEqual(encode_upgrade(UpgradeCommand.ABORT), encode_uint(1, 3))
        for command, chunk in (
            (UpgradeCommand.START, b""),
            (UpgradeCommand.FINISH, b"x"),
            (UpgradeCommand.CONTINUE, b"x" * 257),
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                encode_upgrade(command, chunk)


class FakeUpgradeGateway:
    def __init__(self, *, fail_at=None, locked=False):
        self.received = bytearray()
        self.commands = []
        self.fail_at = fail_at
        self.locked = locked

    def request(self, request, confirm, body=b"", **kwargs):
        assert request == OperationType.UPGRADE_REQUEST
        assert confirm == OperationType.UPGRADE_CONFIRM
        assert kwargs["timeout"] > 0
        fields = decode_fields(body)
        command = uint_value(fields, 1)
        self.commands.append(command)
        if self.locked:
            raise GatewayResponseError(9, None)
        if self.fail_at is not None and len(self.commands) == self.fail_at:
            raise ProtocolError("timed out")
        self.received += bytes_value(fields, 2) or b""
        return SimpleNamespace(body=b"")


def make_client(gateway):
    client = ConnectBoxClient("test.invalid", GATEWAY, APP, "a" * 64)
    client.close = Mock()
    session = SimpleNamespace(request=gateway.request)
    client._session = session
    client._connected_session = lambda: session
    client._version = VersionInfo(packed(1, 8, 1), None, None, None, None)
    return client


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.package = parse_firmware(
            package(payload=bytes(range(256)) * 3 + b"end"), Path("Gateway-1.9.0.fw")
        )

    def test_package_is_sent_in_blocks_and_finished(self):
        gateway = FakeUpgradeGateway()
        client = make_client(gateway)
        steps = []
        client.upload_firmware(
            self.package, lambda done, total: steps.append((done, total))
        )
        self.assertEqual(bytes(gateway.received), self.package.data)
        blocks = -(-len(self.package.data) // 256)
        self.assertEqual(gateway.commands, [0] + [1] * (blocks - 1) + [2])
        self.assertEqual(steps[-1], (blocks, blocks))
        # The gateway restarts; the version is read again.
        self.assertIsNone(client._version)
        client.close.assert_called()

    def test_failed_upload_is_aborted(self):
        gateway = FakeUpgradeGateway(fail_at=3)
        client = make_client(gateway)
        with self.assertRaises(ProtocolError):
            client.upload_firmware(self.package, lambda *_: None)
        self.assertEqual(gateway.commands, [0, 1, 1, 3])
        self.assertIsNotNone(client._version)

    def test_upload_of_another_app_is_not_disturbed(self):
        gateway = FakeUpgradeGateway(locked=True)
        client = make_client(gateway)
        with self.assertRaises(CommandValidationError) as err:
            client.upload_firmware(self.package, lambda *_: None)
        self.assertEqual(err.exception.translation_key, "firmware_update_locked")
        self.assertEqual(gateway.commands, [0])


class UpdateEntityTests(unittest.TestCase):
    def entity(self, found=None, progress=None):
        data = snapshot(rooms_message())
        data = replace(
            data,
            version=VersionInfo(packed(1, 8, 1), None, None, None, None),
            update_progress=progress,
        )
        coordinator = FakeCoordinator(data)
        entity = update.ConnectBoxFirmwareUpdate(coordinator)
        entity._package = found
        return entity

    def test_versions(self):
        entity = self.entity()
        self.assertEqual(
            (entity.installed_version, entity.latest_version), ("1.8.1", "1.8.1")
        )
        self.assertIn("zehnder_connectbox", entity.release_summary)
        same = parse_firmware(package((1, 8, 1)), Path("Gateway-1.8.1.fw"))
        self.assertEqual(self.entity(same).latest_version, "1.8.1")
        newer = parse_firmware(package((1, 9, 0)), Path("Gateway-1.9.0.fw"))
        entity = self.entity(newer)
        self.assertEqual(entity.latest_version, "1.9.0")
        self.assertIn("Gateway-1.9.0.fw", entity.release_summary)

    def test_install_needs_a_newer_package(self):
        same = parse_firmware(package((1, 8, 1)), Path("Gateway-1.8.1.fw"))
        for found in (None, same):
            with self.subTest(found=found), self.assertRaises(HomeAssistantError):
                asyncio.run(self.entity(found).async_install(None, False))

    def test_unit_update_is_shown_as_progress(self):
        entity = self.entity(progress=UpdateProgress(10, 7, 40, (), (9,)))
        self.assertTrue(entity.in_progress)
        self.assertEqual(entity.update_percentage, 40)
        idle = self.entity(progress=UpdateProgress(None, None, None, (7,), ()))
        self.assertFalse(idle.in_progress)
        self.assertIsNone(idle.update_percentage)


if __name__ == "__main__":
    unittest.main()
