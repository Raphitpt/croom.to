"""
Google Calendar calls must not block the event loop; "today" follows the room time zone.
"""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from croom.calendar.providers.base import CalendarEvent
from croom.calendar.providers.google import GoogleCalendarProvider
from croom.calendar.service import CalendarService


class TestGoogleProviderOffLoop:
    async def test_events_fetched_in_worker_thread(self):
        loop_thread = threading.get_ident()
        seen = {}

        def execute():
            seen["thread"] = threading.get_ident()
            return {"items": []}

        provider = GoogleCalendarProvider()
        provider._authenticated = True
        provider._service = MagicMock()
        provider._service.events.return_value.list.return_value.execute = execute

        now = datetime.now(timezone.utc)
        assert await provider.get_events("primary", now, now + timedelta(days=1)) == []
        assert seen["thread"] != loop_thread

    async def test_calendars_listed_in_worker_thread(self):
        loop_thread = threading.get_ident()
        seen = {}

        def execute():
            seen["thread"] = threading.get_ident()
            return {"items": [{"id": "room@example.com", "primary": True}]}

        provider = GoogleCalendarProvider()
        provider._authenticated = True
        provider._service = MagicMock()
        provider._service.calendarList.return_value.list.return_value.execute = execute

        calendars = await provider.get_calendars()
        assert calendars[0]["id"] == "room@example.com"
        assert seen["thread"] != loop_thread


def event_at(start):
    return CalendarEvent(
        id=start.isoformat(),
        title="Standup",
        start_time=start,
        end_time=start + timedelta(minutes=30),
        meeting_url="https://meet.google.com/abc-defg-hij",
    )


class TestTodayInRoomTimezone:
    async def test_today_uses_room_timezone(self):
        paris = ZoneInfo("Europe/Paris")
        service = CalendarService({"timezone": "Europe/Paris"})
        # 23:30 in Paris on 30 Sept is 21:30 UTC: still "today" in Paris
        fake_now = datetime(2026, 9, 30, 23, 30, tzinfo=paris)
        late = event_at(datetime(2026, 9, 30, 23, 45, tzinfo=paris))
        tomorrow = event_at(datetime(2026, 10, 1, 0, 30, tzinfo=paris))
        service._events = {e.id: e for e in (late, tomorrow)}

        with patch("croom.calendar.service.datetime") as dt:
            dt.now.side_effect = lambda tz=None: fake_now.astimezone(tz) if tz else fake_now
            today = await service.get_today_events()

        assert today == [late]

    def test_unknown_timezone_falls_back_to_utc(self):
        service = CalendarService({"timezone": "Mars/Olympus"})
        assert service._timezone == timezone.utc
