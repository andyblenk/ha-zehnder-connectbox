"""Set up the integration in Home Assistant with a simulated ConnectBox.

Requires pytest-homeassistant-custom-component. The client is replaced, so
no network is used; every platform, service and entity is created as with
a real ConnectBox that reports all data of the official app.
"""

from dataclasses import replace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_app_parity import rooms_message, snapshot
from test_gateway_settings import apps_message, site_message
from test_schedules_absence import absence_state, schedules_message

from custom_components.zehnder_connectbox.const import DOMAIN
from custom_components.zehnder_connectbox.models import (
    ActionLogEntry,
    UpdateProgress,
    VersionInfo,
)
from custom_components.zehnder_connectbox.protobuf import (
    bytes_value,
    decode_fields,
    encode_bytes,
    encode_string,
    encode_uint,
)
from custom_components.zehnder_connectbox.protocol import (
    decode_paired_apps,
    decode_run_state,
    decode_schedules,
    decode_site_info,
)

GATEWAY = UUID(int=1)


def office_message():
    """The test room, reported as an office (room type 8) like the app's."""
    room = bytes_value(decode_fields(rooms_message()), 1).replace(
        encode_string(2, "Arbeitszimmer"),
        encode_string(2, "Arbeitszimmer") + encode_uint(3, 8),
    )
    return encode_bytes(1, room)


def full_snapshot(week_plan=3):
    data = snapshot(office_message())
    return replace(
        data,
        version=VersionInfo((1 << 20) | (8 << 10) | 1, None, None, None, None, "S1"),
        run_state=decode_run_state(absence_state(planned=False, week_plan=week_plan)),
        schedules=decode_schedules(schedules_message()),
        action_log=(
            ActionLogEntry("ComfoRF DUTY CYCLE", 1795202400, "53 24h:52 MAX:55"),
            ActionLogEntry("Home Assistant", 1795202340, "CHANGED_DAYPLAN", ("Test",)),
        ),
        remote_access=True,
        site=decode_site_info(site_message()),
        paired_apps=decode_paired_apps(apps_message()),
        update_progress=UpdateProgress(None, None, None, (7,), ()),
    )


@pytest.fixture
def client():
    fake = MagicMock()
    fake.read_snapshot.return_value = full_snapshot()
    fake.set_active_week_plan.return_value = full_snapshot(week_plan=1)
    fake.unsupported_gateway_reads = set()
    with patch(
        "custom_components.zehnder_connectbox.ConnectBoxClient", return_value=fake
    ):
        yield fake


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=str(GATEWAY),
        data={
            "host": "192.0.2.1",
            "gateway_uuid": str(GATEWAY),
            "app_uuid": str(UUID(int=2)),
            "remote_uuid": str(UUID(int=3)),
            "app_id": 1,
            "certificate_sha256": "a" * 64,
        },
    )
    config_entry.add_to_hass(hass)
    return config_entry


def unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        item.unique_id: item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_setup_creates_the_app_functions(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    ids = unique_ids(hass, entry)
    for suffix in (
        "active_week_plan",
        "absence_planner",
        "absence_start",
        "absence_end",
        "absence_active",
        "presence",
        "history",
        "radio_duty_cycle",
        "site_name",
        "system_language",
        "remote_access_switch",
        "users",
        "country",
        "network_connection",
        "system_version",
        "firmware_update",
        "firmware",
        "7_boost",
        "7_boost_duration",
        "7_room_name",
        "7_room_type",
        "7_level_one_runtime",
        "7_summer_ventilation_role",
    ):
        assert f"{GATEWAY}_{suffix}" in ids, suffix

    for service in ("save_day_plan", "remove_day_plan", "save_week_plan"):
        assert hass.services.has_service(DOMAIN, service)

    firmware = hass.states.get(ids[f"{GATEWAY}_firmware"])
    assert firmware.state == "off"
    assert firmware.attributes["installed_version"] == "1.8.1"
    week = hass.states.get(ids[f"{GATEWAY}_active_week_plan"])
    assert week.state == "Winterwochenplan"
    presence = hass.states.get(ids[f"{GATEWAY}_presence"])
    assert presence.state == "on"
    assert hass.states.get(ids[f"{GATEWAY}_users"]).state == "2"


async def test_select_writes_and_shows_the_confirmed_state(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = unique_ids(hass, entry)[f"{GATEWAY}_active_week_plan"]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "Sommerwochenplan"},
        blocking=True,
    )
    client.set_active_week_plan.assert_called_once_with(1)
    assert hass.states.get(entity_id).state == "Sommerwochenplan"


async def test_unload(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    client.close.assert_called()
