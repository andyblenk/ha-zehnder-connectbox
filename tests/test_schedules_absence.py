"""Check schedule editing, the absence planner and the history offline.

The expected bytes are the requests the ConnectBox accepted while the
messages were matched against the official app's message table.
"""

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

from custom_components.zehnder_connectbox import (
    binary_sensor,
    select,
    sensor,
    services,
    switch,
)
from custom_components.zehnder_connectbox import client as client_mod
from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.errors import CommandValidationError
from custom_components.zehnder_connectbox.models import (
    DayPlan,
    ScheduledFunction,
    VersionInfo,
    WeekPlan,
)
from custom_components.zehnder_connectbox.protobuf import (
    bytes_value,
    decode_fields,
    encode_bytes,
    encode_string,
    encode_uint,
    uint_value,
)
from custom_components.zehnder_connectbox.protocol import (
    Frame,
    Operation,
    OperationType,
    ProtocolError,
    decode_day_plan_confirm,
    decode_run_state,
    decode_schedules,
    decode_week_plan_confirm,
    encode_active_week_plan,
    encode_day_plan,
    encode_plan_id,
    encode_standby_time_range,
    encode_week_plan,
)
from custom_components.zehnder_connectbox.session import (
    ConnectBoxSession,
    GatewayResponseError,
)

ABSENCE_START = 1796408100  # 04.12.2026 19:15 CET
ABSENCE_END = 1796494500  # 05.12.2026 19:15 CET


def absence_state(*, planned, start=ABSENCE_START, end=ABSENCE_END, week_plan=3):
    """Run state with the absence planner as the gateway reported it."""
    message = run_state_message(week_plan=week_plan)
    inner = bytes_value(decode_fields(message), 1)
    inner += encode_uint(7, 0) + encode_uint(8, int(planned))
    if planned:
        inner += encode_uint(9, start) + encode_uint(10, end)
    return encode_bytes(1, inner)


class FakeTransport:
    def __init__(self, frames):
        self.pending = list(frames)
        self.written = []

    def set_timeout(self, _timeout):
        pass

    def write(self, data):
        self.written.append(data)

    def read(self):
        return self.pending.pop(0)


def gateway_frame(operation, body=b""):
    return Frame(GATEWAY, APP, operation.encode(), body).encode()


class SessionTests(unittest.TestCase):
    def test_exchange_returns_any_answer_unchecked(self):
        transport = FakeTransport(
            [gateway_frame(Operation(type=777, result=5, reference=1), b"\x08\x01")]
        )
        session = ConnectBoxSession(transport, GATEWAY, APP)
        response = session.exchange(776)
        self.assertEqual(
            (response.operation.type, response.operation.result, response.body),
            (777, 5, b"\x08\x01"),
        )

    def test_request_still_checks_type_and_result(self):
        for operation, error in (
            (Operation(type=777, reference=1), ProtocolError),
            (Operation(type=195, result=3, reference=1), GatewayResponseError),
        ):
            with self.subTest(operation=operation):
                session = ConnectBoxSession(
                    FakeTransport([gateway_frame(operation)]), GATEWAY, APP
                )
                with self.assertRaises(error):
                    session.request(194, 195)


class NotificationTests(unittest.TestCase):
    def test_session_reports_frames_that_answer_no_request(self):
        seen = []
        transport = FakeTransport(
            [
                gateway_frame(Operation(type=207), b"\x08\x03"),
                gateway_frame(Operation(type=195, reference=1), b"answer"),
            ]
        )
        session = ConnectBoxSession(
            transport,
            GATEWAY,
            APP,
            on_unsolicited=lambda operation, body: seen.append((operation.type, body)),
        )
        response = session.request(194, 195)
        self.assertEqual(response.body, b"answer")
        self.assertEqual(seen, [(207, b"\x08\x03")])

    def test_session_still_skips_them_without_a_listener(self):
        transport = FakeTransport(
            [
                gateway_frame(Operation(type=207), b"\x08\x03"),
                gateway_frame(Operation(type=195, reference=1), b"answer"),
            ]
        )
        session = ConnectBoxSession(transport, GATEWAY, APP)
        self.assertEqual(session.request(194, 195).body, b"answer")


def run_state_message(
    *,
    week_plan=3,
    day_plan=1,
    slots=((0, 1), (480, 0), (1260, 1)),
    name=b"Wintertagesplan",
):
    plan = encode_uint(1, day_plan) + encode_bytes(2, name)
    for minute, mode in slots:
        slot = (encode_uint(1, minute) if minute else b"") + (
            encode_uint(2, mode) if mode else b""
        )
        plan += encode_bytes(3, slot)
    inner = b"".join(
        (
            encode_uint(1, 1),
            encode_uint(2, 0),
            encode_uint(3, 1),
            encode_bytes(4, plan),
            encode_uint(5, day_plan),
            encode_uint(6, week_plan),
            encode_uint(12, 0),
        )
    )
    return encode_bytes(1, inner)


class ScheduleProtocolTests(unittest.TestCase):
    def test_run_state_reports_week_and_day_plan(self):
        state = decode_run_state(run_state_message())
        self.assertEqual(state.week_plan, 3)
        self.assertEqual(state.day_plan.plan_id, 1)
        self.assertEqual(state.day_plan.name, "Wintertagesplan")
        self.assertEqual(state.day_plan.slots, ((0, 1), (480, 0), (1260, 1)))
        summer = decode_run_state(
            run_state_message(
                week_plan=1, day_plan=2, slots=((0, 1), (480, 2), (1350, 0))
            )
        )
        self.assertEqual(
            (summer.week_plan, summer.day_plan.plan_id, summer.day_plan.slots[1]),
            (1, 2, (480, 2)),
        )

    def test_run_state_without_plan(self):
        inner = encode_uint(1, 2) + encode_uint(2, 0)
        state = decode_run_state(encode_bytes(1, inner))
        self.assertIsNone(state.week_plan)
        self.assertIsNone(state.day_plan)


# --- schedule list ----------------------------------------------------------------


def day_plan_message(plan_id, name, slots, functions=()):
    plan = encode_uint(1, plan_id) + encode_string(2, name)
    for minute, mode in slots:
        plan += encode_bytes(3, encode_uint(1, minute) + encode_uint(2, mode))
    for start, function, duration, room_id in functions:
        plan += encode_bytes(
            20,
            b"".join(
                (
                    encode_uint(1, start),
                    encode_uint(2, function),
                    encode_uint(3, duration),
                    encode_uint(4, room_id),
                    encode_uint(5, 0),
                )
            ),
        )
    return encode_bytes(1, plan)


def week_plan_message(plan_id, name, day_plan):
    plan = encode_uint(1, plan_id) + encode_string(2, name)
    for field_number in range(3, 10):
        plan += encode_uint(field_number, day_plan)
    return encode_bytes(2, plan)


def schedules_message(*, extra_day=()):
    """The schedule list in the shape the gateway reported."""
    return b"".join(
        (
            day_plan_message(1, "Wintertagesplan", ((0, 1), (480, 0), (1260, 1))),
            day_plan_message(
                2,
                "Sommertagesplan",
                ((0, 1), (480, 2), (1350, 0)),
                tuple((minute, 5, 60, 0) for minute in (270, 210, 150, 90, 30)),
            ),
            *extra_day,
            week_plan_message(1, "Sommerwochenplan", 2),
            week_plan_message(3, "Winterwochenplan", 1),
        )
    )


class ScheduleListTests(unittest.TestCase):
    def test_decodes_day_and_week_plans_with_functions(self):
        test_plan = day_plan_message(
            3, "Test", ((0, 1), (420, 0), (510, 2)), ((420, 2, 20, 3),)
        )
        schedules = decode_schedules(schedules_message(extra_day=(test_plan,)))
        self.assertEqual([plan.plan_id for plan in schedules.day_plans], [1, 2, 3])
        self.assertEqual(
            [(plan.plan_id, plan.name) for plan in schedules.week_plans],
            [(1, "Sommerwochenplan"), (3, "Winterwochenplan")],
        )
        self.assertEqual(schedules.week_plan(3).days, (1,) * 7)
        self.assertIsNone(schedules.week_plan(2))
        summer = schedules.day_plan(2)
        self.assertEqual(summer.slots, ((0, 1), (480, 2), (1350, 0)))
        self.assertEqual(len(summer.functions), 5)
        self.assertEqual(
            (summer.functions[0].start_minute, summer.functions[0].function),
            (270, 5),
        )
        boost = schedules.day_plan(3).functions[0]
        self.assertEqual(
            (boost.start_minute, boost.function, boost.duration_minutes, boost.room_id),
            (420, 2, 20, 3),
        )

    def make_client(self, answers):
        client = ConnectBoxClient("test.invalid", GATEWAY, APP, "a" * 64)
        client.close = Mock()
        requests = []

        def request(request, confirm, body=b"", **_kwargs):
            requests.append(request)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return SimpleNamespace(body=answer)

        session = SimpleNamespace(request=request)
        client._session = session
        client._connected_session = lambda: session
        return client, requests

    def test_list_waits_for_the_next_poll_after_a_closed_connection(self):
        client, requests = self.make_client([schedules_message()])
        client._session = None
        self.assertIsNone(client._read_schedules(self.state()))
        self.assertEqual(requests, [])

    def test_refused_connection_does_not_fail_the_snapshot(self):
        client = ConnectBoxClient("test.invalid", GATEWAY, APP, "a" * 64)
        client.close = Mock()
        client._session = SimpleNamespace()

        def refuse():
            raise client_mod.TransportError("unable to establish a connection")

        client._connected_session = refuse
        self.assertIsNone(client._read_schedules(self.state()))
        self.assertGreater(client._schedules_retry_after, 0)

    def state(self, week_plan=3, day_plan=1):
        return decode_run_state(
            run_state_message(week_plan=week_plan, day_plan=day_plan)
        )

    def test_list_is_cached_until_it_may_have_changed(self):
        test_plan = day_plan_message(3, "Test", ((0, 1),))
        client, requests = self.make_client(
            [schedules_message(), schedules_message(extra_day=(test_plan,))]
        )
        first = client._read_schedules(self.state())
        self.assertEqual(len(first.day_plans), 2)
        self.assertIs(client._read_schedules(self.state()), first)
        self.assertEqual(requests, [OperationType.SCHEDULES_REQUEST])
        # The gateway announces a change made in the app.
        client._handle_notification(
            Operation(type=OperationType.SCHEDULES_NOTIFICATION), b""
        )
        second = client._read_schedules(self.state())
        self.assertEqual(len(second.day_plans), 3)
        self.assertEqual(len(requests), 2)

    def test_list_is_read_again_for_an_unknown_active_plan(self):
        client, requests = self.make_client([schedules_message(), schedules_message()])
        client._read_schedules(self.state())
        client._read_schedules(self.state(week_plan=7))
        self.assertEqual(len(requests), 2)

    def test_refused_request_is_not_repeated_and_keeps_the_snapshot(self):
        client, requests = self.make_client([GatewayResponseError(2, None)])
        self.assertIsNone(client._read_schedules(self.state()))
        self.assertIsNone(client._read_schedules(self.state()))
        self.assertEqual(len(requests), 1)
        client.close.assert_called_once_with()

    def test_missing_answer_is_retried_later_and_keeps_the_last_list(self):
        client, requests = self.make_client(
            [schedules_message(), ProtocolError("timed out")]
        )
        first = client._read_schedules(self.state())
        client._handle_notification(
            Operation(type=OperationType.SCHEDULES_NOTIFICATION), b""
        )
        self.assertIs(client._read_schedules(self.state()), first)
        self.assertIs(client._read_schedules(self.state()), first)
        self.assertEqual(len(requests), 2)

    def test_command_read_back_does_not_request_the_list(self):
        client, requests = self.make_client([])
        client._version = VersionInfo(None, None, None, None, None)
        client._read_run_state = lambda: self.state()
        client._read_rooms = lambda: ()
        self.assertIsNone(client.read_snapshot(refresh_properties=False).schedules)
        self.assertEqual(requests, [])

    def test_week_plan_sensor_and_today_functions(self):
        data = replace(
            snapshot(rooms_message()),
            run_state=decode_run_state(
                run_state_message(
                    week_plan=1,
                    day_plan=2,
                    slots=((0, 1), (480, 2), (1350, 0)),
                    name=b"Sommertagesplan",
                )
            ),
            schedules=decode_schedules(
                schedules_message(
                    extra_day=(
                        day_plan_message(3, "Test", ((0, 1),), ((420, 2, 20, 3),)),
                    )
                )
            ),
        )
        entities = set_up(sensor, FakeCoordinator(data))
        week = next(
            e for e in entities if isinstance(e, sensor.ConnectBoxWeekPlanSensor)
        )
        self.assertEqual(week.native_value, "Sommerwochenplan")
        attributes = week.extra_state_attributes
        self.assertEqual(attributes["week_plan"], 1)
        self.assertEqual(attributes["days"], ["Sommertagesplan"] * 7)
        self.assertEqual(
            [(plan["id"], plan["name"]) for plan in attributes["week_plans"]],
            [(1, "Sommerwochenplan"), (3, "Winterwochenplan")],
        )
        self.assertEqual(
            attributes["day_plans"][2]["functions"],
            [
                {
                    "start": "07:00",
                    "function": "boost",
                    "duration_minutes": 20,
                    "room": "Arbeitszimmer",
                }
            ],
        )
        today = next(
            e for e in entities if isinstance(e, sensor.ConnectBoxDayPlanSensor)
        )
        self.assertEqual(
            today.extra_state_attributes["functions"][0],
            {
                "start": "04:30",
                "function": "summer_ventilation",
                "duration_minutes": 60,
                "room": None,
            },
        )

    def test_week_plan_sensor_needs_the_list(self):
        data = replace(
            snapshot(rooms_message()), run_state=decode_run_state(run_state_message())
        )
        entities = set_up(sensor, FakeCoordinator(data))
        self.assertFalse(
            any(isinstance(e, sensor.ConnectBoxWeekPlanSensor) for e in entities)
        )


class DayPlanSensorTests(unittest.TestCase):
    def snapshot_with_plan(self, message, **kwargs):
        data = snapshot(message, **kwargs)
        return replace(
            data,
            run_state=replace(
                decode_run_state(run_state_message()),
                run_mode=kwargs.get("run_mode", 1),
            ),
        )

    def test_day_plan_sensor(self):
        coordinator = FakeCoordinator(self.snapshot_with_plan(rooms_message()))
        entities = set_up(sensor, coordinator)
        plan = next(
            e for e in entities if isinstance(e, sensor.ConnectBoxDayPlanSensor)
        )
        self.assertEqual(plan.native_value, "Wintertagesplan")
        self.assertEqual(
            plan.extra_state_attributes,
            {
                "day_plan": 1,
                "week_plan": 3,
                "slots": [
                    {"start": "00:00", "situation": "asleep"},
                    {"start": "08:00", "situation": "awake"},
                    {"start": "21:00", "situation": "asleep"},
                ],
            },
        )


class EncodingTests(unittest.TestCase):
    def test_day_plan_with_boost_matches_the_accepted_request(self):
        plan = DayPlan(
            4,
            "HA Test",
            ((0, 1), (420, 0), (1380, 1)),
            (ScheduledFunction(420, 2, 15, 3),),
        )
        self.assertEqual(
            encode_day_plan(plan).hex(),
            "0a2d08041207484120546573741a04080010011a0508a40310001a0508e40a1001"
            "a2010b08a4031002180f20032800",
        )

    def test_new_week_plan_matches_the_accepted_request(self):
        self.assertEqual(
            encode_week_plan(WeekPlan(0, "HA Test", (4,) * 7)).hex(),
            "0a1908001207484120546573741804200428043004380440044804",
        )

    def test_week_plan_needs_seven_days_and_a_short_name(self):
        with self.assertRaises(ValueError):
            encode_week_plan(WeekPlan(0, "Woche", (1,) * 6))
        with self.assertRaises(ValueError):
            encode_week_plan(WeekPlan(0, "x" * 33, (1,) * 7))
        with self.assertRaises(ValueError):
            encode_day_plan(DayPlan(0, "Tag", ((1440, 0),)))

    def test_absence_and_active_week_plan_requests(self):
        self.assertEqual(
            encode_standby_time_range(True, ABSENCE_START, ABSENCE_END).hex(),
            "080110a486ccd80618a4a9d1d806",
        )
        self.assertEqual(
            encode_standby_time_range(False, ABSENCE_START, ABSENCE_END).hex(),
            "080010a486ccd80618a4a9d1d806",
        )
        self.assertEqual(encode_active_week_plan(1).hex(), "0801")
        self.assertEqual(encode_plan_id(3).hex(), "0803")

    def test_run_state_reports_the_absence_planner(self):
        state = decode_run_state(absence_state(planned=True))
        self.assertTrue(state.absence_planned)
        self.assertFalse(state.standby)
        self.assertEqual(
            (state.standby_start, state.standby_end), (ABSENCE_START, ABSENCE_END)
        )
        off = decode_run_state(absence_state(planned=False))
        self.assertFalse(off.absence_planned)
        self.assertIsNone(off.standby_start)

    def test_confirmations_return_the_assigned_ids(self):
        day = day_plan_message(4, "HA Test", ((0, 1), (420, 0)))
        self.assertEqual(decode_day_plan_confirm(day).plan_id, 4)
        week = encode_bytes(
            1,
            encode_uint(1, 2)
            + encode_string(2, "HA Test")
            + b"".join(encode_uint(number, 4) for number in range(3, 10)),
        )
        self.assertEqual(decode_week_plan_confirm(week).days, (4,) * 7)


class FakeScheduleGateway:
    """Answer schedule, run-state and absence requests from mutable state."""

    def __init__(self):
        self.week_plan = 3
        self.planned = False
        self.start = None
        self.end = None
        self.day_plans = {
            1: DayPlan(1, "Wintertagesplan", ((0, 1), (480, 0), (1260, 1))),
            2: DayPlan(2, "Sommertagesplan", ((0, 1), (480, 2), (1350, 0))),
        }
        self.week_plans = {
            1: WeekPlan(1, "Sommerwochenplan", (2,) * 7),
            3: WeekPlan(3, "Winterwochenplan", (1,) * 7),
        }
        self.locked: set[tuple[bool, int]] = set()
        self.foreign_lock: set[tuple[bool, int]] = set()
        self.requests = []
        self.drop_functions = False

    def schedules(self):
        return b"".join(
            [encode_day_plan(plan) for plan in self.day_plans.values()]
            + [
                encode_bytes(2, bytes_value(decode_fields(encode_week_plan(plan)), 1))
                for plan in self.week_plans.values()
            ]
        )

    def request(self, request, confirm, body=b"", **_kwargs):
        self.requests.append(OperationType(request))
        op = OperationType
        if request == op.RUN_STATE_REQUEST:
            message = absence_state(
                planned=self.planned,
                start=self.start or 0,
                end=self.end or 0,
                week_plan=self.week_plan,
            )
            return SimpleNamespace(body=message)
        if request == op.SCHEDULES_REQUEST:
            return SimpleNamespace(body=self.schedules())
        if request == op.SET_ACTIVE_WEEK_PLAN_REQUEST:
            self.week_plan = uint_value(decode_fields(body), 1)
            return SimpleNamespace(body=body)
        if request == op.SET_STANDBY_TIME_RANGE_REQUEST:
            fields = decode_fields(body)
            self.planned = bool(uint_value(fields, 1))
            self.start, self.end = uint_value(fields, 2), uint_value(fields, 3)
            return SimpleNamespace(body=b"")
        if request in (
            op.LOCK_DAY_PLAN_REQUEST,
            op.LOCK_WEEK_PLAN_REQUEST,
            op.RELEASE_DAY_PLAN_REQUEST,
            op.RELEASE_WEEK_PLAN_REQUEST,
        ):
            key = (
                request in (op.LOCK_WEEK_PLAN_REQUEST, op.RELEASE_WEEK_PLAN_REQUEST),
                uint_value(decode_fields(body), 1),
            )
            if key in self.foreign_lock:
                raise GatewayResponseError(9, None)
            if request in (op.LOCK_DAY_PLAN_REQUEST, op.LOCK_WEEK_PLAN_REQUEST):
                self.locked.add(key)
            else:
                self.locked.discard(key)
            return SimpleNamespace(body=encode_uint(1, key[1]) + encode_uint(2, 1))
        if request in (op.ADD_DAY_PLAN_REQUEST, op.SET_DAY_PLAN_REQUEST):
            plan = decode_schedules(body).day_plans[0]
            if request == op.ADD_DAY_PLAN_REQUEST:
                # The ConnectBox dropped the functions of a new plan.
                plan = replace(plan, plan_id=max(self.day_plans) + 1, functions=())
            elif (False, plan.plan_id) not in self.locked:
                raise GatewayResponseError(9, None)
            if self.drop_functions:
                plan = replace(plan, functions=())
            self.day_plans[plan.plan_id] = plan
            return SimpleNamespace(body=encode_day_plan(replace(plan, functions=())))
        if request in (op.ADD_WEEK_PLAN_REQUEST, op.SET_WEEK_PLAN_REQUEST):
            plan = decode_week_plan_confirm(body)
            if request == op.ADD_WEEK_PLAN_REQUEST:
                plan = replace(plan, plan_id=max(self.week_plans) + 1)
            self.week_plans[plan.plan_id] = plan
            return SimpleNamespace(body=encode_week_plan(plan))
        if request == op.REMOVE_DAY_PLAN_REQUEST:
            plan_id = uint_value(decode_fields(body), 1)
            if any(plan_id in plan.days for plan in self.week_plans.values()):
                raise GatewayResponseError(12, None)
            del self.day_plans[plan_id]
            return SimpleNamespace(body=body)
        if request == op.REMOVE_WEEK_PLAN_REQUEST:
            del self.week_plans[uint_value(decode_fields(body), 1)]
            return SimpleNamespace(body=body)
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
        self.gateway = FakeScheduleGateway()
        self.client = make_client(self.gateway)

    def test_active_week_plan_is_switched_and_confirmed(self):
        self.client._reload_schedules()
        result = self.client.set_active_week_plan(1)
        self.assertEqual(result.run_state.week_plan, 1)
        with self.assertRaises(CommandValidationError):
            self.client.set_active_week_plan(7)

    def test_absence_is_saved_and_switched_off(self):
        start = int(client_mod.time.time()) + 3600
        result = self.client.set_absence(True, start, start + 86400)
        self.assertTrue(result.run_state.absence_planned)
        self.assertEqual(result.run_state.standby_end, start + 86400)
        off = self.client.set_absence(False)
        self.assertFalse(off.run_state.absence_planned)
        # The reported period is sent back with mode 0, as tested.
        self.assertEqual(self.gateway.start, start)

    def test_absence_needs_a_valid_future_period(self):
        now = int(client_mod.time.time())
        for start, end, key in (
            (None, None, "absence_period_missing"),
            (now + 7200, now + 3600, "absence_period_invalid"),
            (now - 7200, now - 3600, "absence_period_past"),
        ):
            with (
                self.subTest(key=key),
                self.assertRaises(CommandValidationError) as err,
            ):
                self.client.set_absence(True, start, end)
            self.assertEqual(err.exception.translation_key, key)

    def test_day_plan_is_changed_under_its_lock_and_checked(self):
        plan = DayPlan(
            1,
            "Wintertagesplan",
            ((480, 0), (0, 1), (1290, 1)),
            (ScheduledFunction(420, 2, 20, 3),),
        )
        _snapshot, plan_id = self.client.save_day_plan(plan)
        self.assertEqual(plan_id, 1)
        stored = self.gateway.day_plans[1]
        self.assertEqual(stored.slots, ((0, 1), (480, 0), (1290, 1)))
        self.assertEqual(stored.functions[0].room_id, 3)
        op = OperationType
        self.assertEqual(
            self.gateway.requests[:3],
            [
                op.LOCK_DAY_PLAN_REQUEST,
                op.SET_DAY_PLAN_REQUEST,
                op.RELEASE_DAY_PLAN_REQUEST,
            ],
        )
        self.assertEqual(self.gateway.locked, set())

    def test_new_day_plan_gets_the_assigned_id(self):
        _snapshot, plan_id = self.client.save_day_plan(
            DayPlan(0, "Homeoffice", ((0, 1), (420, 0)))
        )
        self.assertEqual(plan_id, 3)
        self.assertEqual(self.gateway.day_plans[3].name, "Homeoffice")
        self.assertNotIn(OperationType.LOCK_DAY_PLAN_REQUEST, self.gateway.requests)

    def test_functions_of_a_new_plan_are_added_with_a_change(self):
        boost = ScheduledFunction(390, 2, 15, 3)
        _snapshot, plan_id = self.client.save_day_plan(
            DayPlan(0, "Homeoffice", ((0, 1), (390, 0)), (boost,))
        )
        self.assertEqual(self.gateway.day_plans[plan_id].functions, (boost,))
        op = OperationType
        self.assertEqual(
            self.gateway.requests[:4],
            [
                op.ADD_DAY_PLAN_REQUEST,
                op.LOCK_DAY_PLAN_REQUEST,
                op.SET_DAY_PLAN_REQUEST,
                op.RELEASE_DAY_PLAN_REQUEST,
            ],
        )

    def test_dropped_function_is_reported(self):
        self.gateway.drop_functions = True
        with self.assertRaises(ProtocolError):
            self.client.save_day_plan(
                DayPlan(
                    1, "Wintertagesplan", ((0, 1),), (ScheduledFunction(420, 2, 20, 1),)
                )
            )

    def test_plan_locked_by_another_app_is_refused(self):
        self.gateway.foreign_lock.add((False, 1))
        with self.assertRaises(CommandValidationError) as err:
            self.client.save_day_plan(DayPlan(1, "Wintertagesplan", ((0, 1),)))
        self.assertEqual(err.exception.translation_key, "plan_locked")

    def test_week_plan_add_change_and_remove(self):
        _snapshot, plan_id = self.client.save_week_plan(
            WeekPlan(0, "HA Test", (2,) * 7)
        )
        self.assertEqual(plan_id, 4)
        self.client.save_week_plan(WeekPlan(4, "HA Test 2", (1,) + (2,) * 6))
        self.assertEqual(self.gateway.week_plans[4].days[0], 1)
        result = self.client.remove_plan(4, week=True)
        self.assertIsNone(result.schedules.week_plan(4))
        self.assertEqual(self.gateway.locked, set())

    def test_day_plan_in_use_is_refused_by_the_gateway(self):
        with self.assertRaises(GatewayResponseError):
            self.client.remove_plan(1, week=False)
        self.assertEqual(self.gateway.locked, set())


def schedule_snapshot(**changes):
    data = replace(
        snapshot(rooms_message()),
        run_state=decode_run_state(absence_state(planned=False)),
        schedules=decode_schedules(schedules_message()),
    )
    return replace(data, **changes)


class EntityTests(unittest.TestCase):
    def test_week_plan_select(self):
        coordinator = FakeCoordinator(schedule_snapshot())
        calls = []

        async def activate(plan_id):
            calls.append(plan_id)

        coordinator.async_set_active_week_plan = activate
        entity = next(
            item
            for item in set_up(select, coordinator)
            if isinstance(item, select.ConnectBoxActiveWeekPlan)
        )
        self.assertEqual(entity.options, ["Sommerwochenplan", "Winterwochenplan"])
        self.assertEqual(entity.current_option, "Winterwochenplan")
        asyncio.run(entity.async_select_option("Sommerwochenplan"))
        self.assertEqual(calls, [1])

    def test_duplicate_week_plan_names_get_their_id(self):
        schedules = decode_schedules(schedules_message())
        schedules = replace(
            schedules,
            week_plans=(
                *schedules.week_plans,
                replace(schedules.week_plans[0], plan_id=5),
            ),
        )
        self.assertEqual(
            list(select.week_plan_options(schedules)),
            ["Sommerwochenplan (1)", "Winterwochenplan", "Sommerwochenplan (5)"],
        )

    def test_absence_switch_and_indicators(self):
        data = schedule_snapshot(
            run_state=decode_run_state(absence_state(planned=True)),
        )
        coordinator = FakeCoordinator(data)
        coordinator.absence_period = lambda: (ABSENCE_START, ABSENCE_END)
        absence = next(
            item
            for item in set_up(switch, coordinator)
            if isinstance(item, switch.ConnectBoxAbsencePlannerSwitch)
        )
        self.assertTrue(absence.is_on)
        self.assertEqual(
            absence.extra_state_attributes["starts_at"], "2026-12-04T18:15:00+00:00"
        )
        indicators = {
            item.entity_description.key: item
            for item in set_up(binary_sensor, coordinator)
            if isinstance(item, binary_sensor.ConnectBoxGatewayBinarySensor)
        }
        self.assertFalse(indicators["absence_active"].is_on)


class ServiceHelperTests(unittest.TestCase):
    def test_plans_are_found_by_name_or_id(self):
        plans = [(1, "Wintertagesplan"), (2, "Sommertagesplan"), (3, "Test")]
        self.assertEqual(services._resolve("sommertagesplan", plans), 2)
        self.assertEqual(services._resolve(3, plans), 3)
        self.assertEqual(services._resolve("1", plans), 1)
        with self.assertRaises(ServiceValidationError):
            services._resolve("Homeoffice", plans)
        with self.assertRaises(ServiceValidationError):
            services._resolve("Test", [*plans, (4, "test")])

    def test_boost_needs_a_room_with_the_boost(self):
        data = snapshot(rooms_message())
        self.assertEqual(services._function_room(data, "arbeitszimmer"), 3)
        with self.assertRaises(ServiceValidationError):
            services._function_room(data, "Küche")
        no_boost = snapshot(rooms_message(boost_duration=None))
        with self.assertRaises(ServiceValidationError):
            services._function_room(no_boost, 3)

    def test_schemas(self):
        data = services.SAVE_DAY_PLAN_SCHEMA(
            {
                "name": "Homeoffice",
                "slots": [
                    {"start": "00:00", "situation": "asleep"},
                    {"start": "07:30:00", "situation": "awake"},
                ],
                "functions": [],
            }
        )
        self.assertEqual(data["slots"][1]["start"].minute, 30)
        self.assertEqual(data["functions"], [])
        import voluptuous as vol

        with self.assertRaises(vol.Invalid):
            services.SAVE_DAY_PLAN_SCHEMA(
                {"slots": [{"start": "07:30:10", "situation": "awake"}]}
            )
        with self.assertRaises(vol.Invalid):
            services.SAVE_DAY_PLAN_SCHEMA({"name": "x" * 33})


class PresenceTests(unittest.TestCase):
    def test_presence_keeps_the_schedule_running(self):
        state = {"location": 1}

        def request(request, confirm, body=b"", **_kwargs):
            if request == OperationType.SET_RUN_STATE_REQUEST:
                fields = decode_fields(body)
                self.assertEqual(uint_value(fields, 1), 1)
                state["location"] = uint_value(fields, 2)
                return SimpleNamespace(body=b"")
            if request == OperationType.RUN_STATE_REQUEST:
                message = run_state_message()
                inner = bytes_value(decode_fields(message), 1)
                if state["location"] == 2:
                    inner = inner.replace(encode_uint(2, 0), encode_uint(2, 2))
                    inner = inner.replace(encode_uint(3, 1), encode_uint(3, 2))
                return SimpleNamespace(body=encode_bytes(1, inner))
            if request == OperationType.ROOMS_REQUEST:
                return SimpleNamespace(body=rooms_message())
            raise AssertionError(request)

        client = make_client(SimpleNamespace(request=request))
        with patch.object(client_mod, "LEVEL_SETTLE_POLL_INTERVAL", 0):
            away = client.set_presence(False)
            self.assertEqual(
                (away.run_state.temperature_mode, away.run_state.situation_source),
                (2, 2),
            )
            home = client.set_presence(True)
        self.assertEqual(home.run_state.temperature_mode, 0)

    def test_presence_switch(self):
        data = snapshot(rooms_message())
        data = replace(data, run_state=decode_run_state(run_state_message()))
        coordinator = FakeCoordinator(data)
        calls = []

        async def set_presence(home):
            calls.append(home)

        coordinator.async_set_presence = set_presence
        entity = next(
            e
            for e in set_up(switch, coordinator)
            if isinstance(e, switch.ConnectBoxPresenceSwitch)
        )
        self.assertTrue(entity.is_on)
        self.assertTrue(entity.available)
        self.assertEqual(entity.extra_state_attributes, {"source": "schedule"})
        asyncio.run(entity.async_turn_off())
        self.assertEqual(calls, [False])
        manual = replace(data, run_state=replace(data.run_state, run_mode=2))
        coordinator.data = manual
        self.assertFalse(entity.available)


if __name__ == "__main__":
    unittest.main()
