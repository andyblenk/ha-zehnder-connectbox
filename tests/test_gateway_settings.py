"""Check the system, user, update and room settings of the app offline."""

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.exceptions import ServiceValidationError
from test_app_parity import (
    APP,
    GATEWAY,
    FakeCoordinator,
    rooms_message,
    set_up,
    snapshot,
)
from test_schedules_absence import run_state_message

from custom_components.zehnder_connectbox import client as client_mod
from custom_components.zehnder_connectbox import select, sensor, switch, text
from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.errors import CommandValidationError
from custom_components.zehnder_connectbox.models import (
    ActionLogEntry,
    PairedApp,
    UpdateProgress,
    VersionInfo,
)
from custom_components.zehnder_connectbox.protobuf import (
    bytes_value,
    bytes_values,
    decode_fields,
    encode_bytes,
    encode_string,
    encode_uint,
    uint_value,
)
from custom_components.zehnder_connectbox.protocol import (
    OperationType,
    ProtocolError,
    decode_action_log,
    decode_paired_apps,
    decode_remote_access,
    decode_rooms,
    decode_site_info,
    decode_update_progress,
    decode_version,
    encode_remote_access,
    encode_room_settings,
    encode_site_info,
)
from custom_components.zehnder_connectbox.session import GatewayResponseError


def site_message(name="Home", language=2):
    """The site in the shape the gateway reported."""
    return b"".join(
        (
            encode_string(1, name),
            encode_uint(2, language),
            encode_uint(3, 2),
            encode_uint(4, 4),
            encode_string(5, "Europe/Berlin"),
            encode_uint(6, 2),
            encode_uint(7, 0),
        )
    )


def apps_message():
    def app(app_id, nickname, last_active):
        return encode_bytes(
            1,
            encode_uint(1, app_id)
            + encode_string(2, nickname)
            + encode_uint(3, last_active),
        )

    return app(1, "Peter iPhone", 1795109700) + app(4, "Home Assistant", 1795203420)


class ProtocolTests(unittest.TestCase):
    def test_site_is_decoded(self):
        site = decode_site_info(site_message())
        self.assertEqual(
            (site.name, site.language, site.location, site.timezone_name),
            ("Home", 2, 2, "Europe/Berlin"),
        )
        self.assertEqual(site.network_mode, 0)

    def test_site_update_keeps_everything_but_the_change(self):
        site = decode_site_info(site_message())
        fields = decode_fields(encode_site_info(site, name="Zuhause"))
        self.assertEqual([item.number for item in fields], [1, 2, 3, 4, 5])
        self.assertEqual(bytes_value(fields, 1), b"Zuhause")
        self.assertEqual(bytes_value(fields, 5), b"Europe/Berlin")
        fields = decode_fields(encode_site_info(site, language=1))
        self.assertEqual((bytes_value(fields, 1), uint_value(fields, 2)), (b"Home", 1))
        self.assertEqual(uint_value(fields, 3), 2)

    def test_site_name_must_fit(self):
        site = decode_site_info(site_message())
        for name in ("", "x" * 33):
            with self.subTest(name=name), self.assertRaises(ValueError):
                encode_site_info(site, name=name)

    def test_remote_access_uses_the_apps_enumeration(self):
        self.assertEqual(encode_remote_access(True), encode_uint(1, 0))
        self.assertEqual(encode_remote_access(False), encode_uint(1, 1))

    def test_users_and_update_progress(self):
        apps = decode_paired_apps(apps_message())
        self.assertEqual(
            [(app.app_id, app.nickname) for app in apps],
            [(1, "Peter iPhone"), (4, "Home Assistant")],
        )
        one_by_one = encode_uint(4, 7) + encode_uint(4, 9) + encode_uint(5, 11)
        packed = encode_bytes(4, bytes((7, 9))) + encode_bytes(5, bytes((11,)))
        for message in (one_by_one, packed):
            with self.subTest(message=message.hex()):
                progress = decode_update_progress(message)
                self.assertEqual(progress.up_to_date_device_ids, (7, 9))
                self.assertEqual(progress.queued_device_ids, (11,))
                self.assertIsNone(progress.updating_device_id)

    def test_version_reports_the_serial_number(self):
        version = decode_version(encode_uint(1, 5) + encode_string(2, "110-01"))
        self.assertEqual(version.serial_number, "110-01")
        self.assertIsNone(decode_version(encode_uint(1, 5)).serial_number)

    def test_room_name_and_type_are_replaced(self):
        room_body = bytes_value(decode_fields(rooms_message()), 1).replace(
            encode_string(2, "Arbeitszimmer"),
            encode_string(2, "Arbeitszimmer") + encode_uint(3, 8),
        )
        room = decode_rooms(encode_bytes(1, room_body))[0]
        self.assertEqual(room.room_type, 8)

        written = decode_fields(
            bytes_value(decode_fields(encode_room_settings(room, name="Büro")), 1)
        )
        self.assertEqual(bytes_value(written, 2), "Büro".encode())
        self.assertEqual(uint_value(written, 3), 8)
        self.assertEqual(uint_value(written, 22), 15)
        device = decode_fields(bytes_values(written, 8)[0])
        self.assertFalse({30, 31, 41} & {item.number for item in device})

        written = decode_fields(
            bytes_value(decode_fields(encode_room_settings(room, room_type=9)), 1)
        )
        self.assertEqual(bytes_value(written, 2), b"Arbeitszimmer")
        self.assertEqual(uint_value(written, 3), 9)
        with self.assertRaises(ValueError):
            encode_room_settings(room)
        with self.assertRaises(ValueError):
            encode_room_settings(room, name="x" * 33)

    def test_history_and_internet_access(self):
        def entry(nickname, timestamp, message, *params):
            return encode_bytes(
                1,
                encode_string(1, nickname)
                + encode_uint(2, timestamp)
                + encode_string(3, message)
                + b"".join(encode_string(4, item) for item in params)
                + encode_bytes(4, b""),
            )

        log = decode_action_log(
            entry("ComfoRF DUTY CYCLE", 1795202340, "53 24h:53 MAX:55")
            + entry("Home Assistant", 1795202340, "REMOVED_DAYPLAN", "HA Test")
            + entry("Mac", 1795201140, "CHANGED_DAYPLAN", "Test")
        )
        self.assertEqual(len(log), 3)
        self.assertEqual(log[1].message, "REMOVED_DAYPLAN")
        self.assertEqual(log[1].params, ("HA Test",))
        self.assertEqual(log[0].params, ())
        self.assertTrue(decode_remote_access(encode_uint(1, 0)))
        self.assertFalse(decode_remote_access(encode_uint(1, 1)))
        self.assertIsNone(decode_remote_access(b""))


class FakeSettingsGateway:
    """Answer site, access, user and update requests from mutable state."""

    def __init__(self):
        self.name = "Home"
        self.language = 2
        self.remote = 0
        self.locked = False
        self.foreign_lock = False
        self.refuse: set[OperationType] = set()
        self.requests = []

    def request(self, request, confirm, body=b"", **_kwargs):
        op = OperationType
        self.requests.append(op(request))
        if request in self.refuse:
            raise GatewayResponseError(13, None)
        if request == op.LOCK_SITE_REQUEST:
            if self.foreign_lock:
                raise GatewayResponseError(9, None)
            self.locked = True
            return SimpleNamespace(body=b"")
        if request == op.RELEASE_SITE_REQUEST:
            self.locked = False
            return SimpleNamespace(body=b"")
        if request == op.SITE_INFO_REQUEST:
            return SimpleNamespace(body=site_message(self.name, self.language))
        if request == op.SET_SITE_INFO_REQUEST:
            if not self.locked:
                raise GatewayResponseError(9, None)
            fields = decode_fields(body)
            self.name = bytes_value(fields, 1).decode()
            self.language = uint_value(fields, 2)
            return SimpleNamespace(body=body)
        if request == op.REMOTE_ACCESS_REQUEST:
            return SimpleNamespace(body=encode_uint(1, self.remote))
        if request == op.SET_REMOTE_ACCESS_REQUEST:
            self.remote = uint_value(decode_fields(body), 1)
            return SimpleNamespace(body=body)
        if request == op.TIME_REQUEST:
            return SimpleNamespace(
                body=encode_uint(1, 1795198822) + encode_uint(2, 1795202400)
            )
        if request == op.ACTION_LOG_REQUEST:
            entry = (
                encode_string(1, "Home Assistant")
                + encode_uint(2, 1795202340)
                + encode_string(3, "REMOVED_DAYPLAN")
            )
            return SimpleNamespace(body=encode_bytes(1, entry))
        if request == op.PAIRED_APPS_REQUEST:
            return SimpleNamespace(body=apps_message())
        if request == op.UPDATE_PROGRESS_REQUEST:
            return SimpleNamespace(body=encode_uint(4, 7))
        if request == op.RUN_STATE_REQUEST:
            return SimpleNamespace(body=run_state_message())
        if request == op.ROOMS_REQUEST:
            return SimpleNamespace(body=rooms_message())
        raise AssertionError(f"unexpected request {request}")


def make_client(gateway):
    client = ConnectBoxClient("test.invalid", GATEWAY, APP, "a" * 64)
    client.close = Mock()
    session = SimpleNamespace(request=gateway.request)
    client._session = session
    client._connected_session = lambda: session
    client._version = VersionInfo(None, None, None, None, None)
    return client


class ClientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(client_mod, "LEVEL_SETTLE_POLL_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gateway = FakeSettingsGateway()
        self.client = make_client(self.gateway)

    def test_gateway_data_is_read_with_times_as_unix_time(self):
        self.client._read_action_log()
        # Logged at 19:19 CET, which the gateway reported as 19:19 UTC.
        self.assertEqual(self.client._action_log[0].timestamp, 1795202340 - 3600)
        self.assertEqual(self.client._paired_apps[1].last_active, 1795203420 - 3600)
        self.assertTrue(self.client._remote_access)
        self.assertEqual(self.client._site.name, "Home")
        self.assertEqual(self.client._update_progress.up_to_date_device_ids, (7,))
        result = self.client.read_snapshot(refresh_properties=False)
        self.assertEqual(result.site.language, 2)
        self.assertEqual(len(result.paired_apps), 2)

    def test_refused_read_is_skipped_and_the_others_are_read_later(self):
        self.gateway.refuse.add(OperationType.PAIRED_APPS_REQUEST)
        self.client._read_action_log()
        self.assertEqual(self.client.unsupported_gateway_reads, {"users"})
        self.assertIsNone(self.client._paired_apps)
        self.assertIsNone(self.client._update_progress)
        self.client._read_action_log()
        self.assertIsNotNone(self.client._update_progress)
        self.assertEqual(
            self.gateway.requests.count(OperationType.PAIRED_APPS_REQUEST), 1
        )

    def test_site_is_changed_under_its_lock(self):
        result = self.client.set_site_info(name="Zuhause")
        self.assertEqual((self.gateway.name, self.gateway.language), ("Zuhause", 2))
        self.assertEqual(result.site.name, "Zuhause")
        op = OperationType
        self.assertEqual(
            self.gateway.requests[:5],
            [
                op.LOCK_SITE_REQUEST,
                op.SITE_INFO_REQUEST,
                op.SET_SITE_INFO_REQUEST,
                op.RELEASE_SITE_REQUEST,
                op.SITE_INFO_REQUEST,
            ],
        )
        self.assertFalse(self.gateway.locked)
        self.client.set_site_info(language=1)
        self.assertEqual((self.gateway.name, self.gateway.language), ("Zuhause", 1))

    def test_site_locked_by_another_app_is_refused(self):
        self.gateway.foreign_lock = True
        with self.assertRaises(CommandValidationError) as err:
            self.client.set_site_info(name="Zuhause")
        self.assertEqual(err.exception.translation_key, "site_locked")
        self.assertEqual(self.gateway.name, "Home")

    def test_remote_access_is_switched_and_read_back(self):
        self.assertFalse(self.client.set_remote_access(False).remote_access)
        self.assertEqual(self.gateway.remote, 1)
        self.assertTrue(self.client.set_remote_access(True).remote_access)

    def test_unapplied_remote_access_is_reported(self):
        gateway = self.gateway
        original = gateway.request

        def ignore_set(request, confirm, body=b"", **kwargs):
            if request == OperationType.SET_REMOTE_ACCESS_REQUEST:
                return SimpleNamespace(body=body)
            return original(request, confirm, body, **kwargs)

        self.client._connected_session = lambda: SimpleNamespace(request=ignore_set)
        with (
            patch.object(client_mod, "COMMAND_SETTLE_TIMEOUT", 0),
            self.assertRaises(ProtocolError),
        ):
            self.client.set_remote_access(False)


def settings_snapshot(**changes):
    data = replace(
        snapshot(rooms_message()),
        site=decode_site_info(site_message()),
        remote_access=True,
        paired_apps=(
            PairedApp(1, "Peter iPhone", 1795106100),
            PairedApp(4, "Home Assistant", 1795199820),
        ),
        update_progress=UpdateProgress(None, None, None, (7,), ()),
    )
    return replace(data, **changes)


class SettingsCoordinator(FakeCoordinator):
    async def async_set_site_info(self, **values):
        self.calls.append(("site", values))

    async def async_set_room_settings(self, room_id, **values):
        self.calls.append(("room", room_id, values))

    async def async_set_remote_access(self, enabled):
        self.calls.append(("remote", enabled))


class EntityTests(unittest.TestCase):
    def test_names_are_written_trimmed_and_checked(self):
        coordinator = SettingsCoordinator(settings_snapshot())
        entities = set_up(text, coordinator)
        site = next(e for e in entities if isinstance(e, text.ConnectBoxSiteName))
        room = next(e for e in entities if isinstance(e, text.ConnectBoxRoomName))
        self.assertEqual(
            (site.native_value, room.native_value), ("Home", "Arbeitszimmer")
        )
        asyncio.run(site.async_set_value(" Zuhause "))
        asyncio.run(site.async_set_value("Home"))
        asyncio.run(room.async_set_value("Büro"))
        self.assertEqual(
            coordinator.calls,
            [("site", {"name": "Zuhause"}), ("room", 3, {"name": "Büro"})],
        )
        for value in ("  ", "ä" * 17):
            with self.subTest(value=value), self.assertRaises(ServiceValidationError):
                asyncio.run(room.async_set_value(value))

    def test_language_select(self):
        coordinator = SettingsCoordinator(settings_snapshot())
        entity = next(
            e
            for e in set_up(select, coordinator)
            if isinstance(e, select.ConnectBoxSystemLanguage)
        )
        self.assertEqual(entity.current_option, "german")
        self.assertEqual(entity.options, ["german", "english", "french", "dutch"])
        asyncio.run(entity.async_select_option("english"))
        self.assertEqual(coordinator.calls, [("site", {"language": 1})])
        with self.assertRaises(ServiceValidationError):
            asyncio.run(entity.async_select_option("italian"))

    def test_room_type_select(self):
        room_body = bytes_value(decode_fields(rooms_message()), 1).replace(
            encode_string(2, "Arbeitszimmer"),
            encode_string(2, "Arbeitszimmer") + encode_uint(3, 8),
        )
        coordinator = SettingsCoordinator(
            settings_snapshot(rooms=decode_rooms(encode_bytes(1, room_body)))
        )
        entity = next(
            e
            for e in set_up(select, coordinator)
            if isinstance(e, select.ConnectBoxRoomType)
        )
        self.assertEqual(entity.current_option, "office")
        asyncio.run(entity.async_select_option("office"))
        asyncio.run(entity.async_select_option("other"))
        self.assertEqual(coordinator.calls, [("room", 3, {"room_type": 9})])

    def test_remote_access_switch(self):
        coordinator = SettingsCoordinator(settings_snapshot())
        entity = next(
            e
            for e in set_up(switch, coordinator)
            if isinstance(e, switch.ConnectBoxRemoteAccessSwitch)
        )
        self.assertTrue(entity.is_on)
        asyncio.run(entity.async_turn_off())
        self.assertEqual(coordinator.calls, [("remote", False)])

    def test_gateway_sensors(self):
        coordinator = SettingsCoordinator(settings_snapshot())
        sensors = {
            e.entity_description.key: e
            for e in set_up(sensor, coordinator)
            if isinstance(e, sensor.ConnectBoxGatewaySensor)
        }
        # No system version in the test snapshot.
        self.assertEqual(
            set(sensors), {"users", "country", "network_connection", "firmware_update"}
        )
        self.assertEqual(sensors["users"].native_value, 2)
        self.assertEqual(
            sensors["users"].extra_state_attributes["users"][1],
            {"name": "Home Assistant", "last_active": "2026-11-20T18:37:00+00:00"},
        )
        self.assertEqual(sensors["country"].native_value, "germany")
        self.assertEqual(
            sensors["country"].extra_state_attributes, {"time_zone": "Europe/Berlin"}
        )
        self.assertEqual(sensors["network_connection"].native_value, "lan")
        self.assertEqual(sensors["firmware_update"].native_value, "up_to_date")
        self.assertEqual(
            sensors["firmware_update"].extra_state_attributes["up_to_date"],
            ["Arbeitszimmer"],
        )

    def test_running_update_is_shown(self):
        progress = UpdateProgress(12, 7, 40, (), (9,))
        data = settings_snapshot(update_progress=progress)
        self.assertEqual(sensor._update_state(progress), "updating")
        attributes = sensor._update_attributes(data)
        self.assertEqual(
            (attributes["updating"], attributes["progress"], attributes["queued"]),
            ("Arbeitszimmer", 40, ["9"]),
        )
        self.assertEqual(
            sensor._update_state(UpdateProgress(None, None, None, (), (9,))), "queued"
        )

    def test_history_and_duty_cycle_sensors(self):
        log = (
            ActionLogEntry("ComfoRF DUTY CYCLE", 1795202400, "53 24h:52 MAX:55"),
            ActionLogEntry("Home Assistant", 1795202340, "CHANGED_DAYPLAN", ("Test",)),
            ActionLogEntry("Mac", 1795201140, "ADDED_DAYPLAN", ("Test",)),
        )
        coordinator = SettingsCoordinator(settings_snapshot(action_log=log))
        entities = set_up(sensor, coordinator)
        history = next(
            item
            for item in entities
            if isinstance(item, sensor.ConnectBoxHistorySensor)
        )
        duty = next(
            item
            for item in entities
            if isinstance(item, sensor.ConnectBoxDutyCycleSensor)
        )
        self.assertEqual(history.native_value, "CHANGED_DAYPLAN: Test")
        attributes = history.extra_state_attributes
        self.assertEqual(attributes["by"], "Home Assistant")
        self.assertEqual(len(attributes["entries"]), 2)
        self.assertEqual(duty.native_value, 53)
        self.assertEqual(duty.extra_state_attributes["last_24_hours"], 52)


if __name__ == "__main__":
    unittest.main()
