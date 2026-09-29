"""
Tests for croom.calendar.service module.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from croom.calendar.providers.base import CalendarEvent, MeetingPlatform
from croom.calendar.service import CalendarService

MEET_URL = "https://meet.google.com/abc-defg-hij"


def now():
    return datetime.now(timezone.utc)


def make_event(event_id="e1", title="Meeting", start_in=timedelta(hours=1),
               duration=timedelta(hours=1), **kwargs):
    start = now() + start_in
    return CalendarEvent(id=event_id, title=title, start_time=start,
                         end_time=start + duration, **kwargs)


def make_provider(events=None, calendars=None, auth_ok=True):
    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=auth_ok)
    provider.get_calendars = AsyncMock(
        return_value=calendars if calendars is not None
        else [{"id": "room@example.com", "name": "Room", "primary": True}]
    )
    provider.get_events = AsyncMock(return_value=events or [])
    return provider


class TestCalendarEvent:
    """Tests for CalendarEvent dataclass."""

    def test_basic_event(self):
        event = make_event("event-123", "Team Meeting")

        assert event.id == "event-123"
        assert event.title == "Team Meeting"
        assert event.meeting_url is None
        assert event.has_video_meeting is False

    def test_event_with_meeting(self):
        event = make_event(
            "event-123", "Video Call",
            meeting_url=MEET_URL,
            meeting_platform=MeetingPlatform.GOOGLE_MEET,
        )

        assert event.meeting_url == MEET_URL
        assert event.meeting_platform == MeetingPlatform.GOOGLE_MEET
        assert event.has_video_meeting is True

    def test_event_is_happening_now(self):
        event = make_event(start_in=timedelta(minutes=-30))
        assert event.is_happening_now() is True

    def test_event_not_happening_now(self):
        event = make_event(start_in=timedelta(hours=2))
        assert event.is_happening_now() is False

    def test_event_time_until_start(self):
        event = make_event(start_in=timedelta(minutes=30))

        time_until = event.time_until_start()
        assert 0 < time_until.total_seconds() < 1900  # ~31 minutes


class TestCalendarService:
    """Tests for CalendarService class."""

    @pytest.fixture
    def calendar_service(self):
        return CalendarService()

    def test_initial_state(self, calendar_service):
        assert calendar_service.name == "calendar"
        assert calendar_service.provider is None
        assert calendar_service.events == []
        assert calendar_service.next_meeting is None

    async def test_initialize_uses_primary_calendar(self):
        provider = make_provider()
        service = CalendarService({"provider": "google", "credentials": {"oauth_token": {}}})

        with patch.dict(CalendarService.PROVIDERS, {"google": MagicMock(return_value=provider)}):
            assert await service.initialize() is True

        provider.authenticate.assert_awaited_once_with({"oauth_token": {}})
        assert service._calendar_ids == ["room@example.com"]

    async def test_initialize_with_explicit_calendars(self):
        provider = make_provider()
        service = CalendarService({"provider": "google", "calendar_ids": ["a", "b"]})

        with patch.dict(CalendarService.PROVIDERS, {"google": MagicMock(return_value=provider)}):
            assert await service.initialize() is True

        provider.get_calendars.assert_not_awaited()
        assert service._calendar_ids == ["a", "b"]

    async def test_initialize_fails_on_auth_error(self):
        provider = make_provider(auth_ok=False)
        service = CalendarService({"provider": "google"})

        with patch.dict(CalendarService.PROVIDERS, {"google": MagicMock(return_value=provider)}):
            assert await service.initialize() is False

    async def test_initialize_unknown_provider(self):
        service = CalendarService({"provider": "exchange-2003"})
        assert await service.initialize() is False

    async def test_fetch_events(self, calendar_service):
        calendar_service._provider = make_provider(events=[
            make_event("e1", "Meeting 1", meeting_url=MEET_URL),
            make_event("e2", "Cancelled", status="cancelled"),
        ])
        calendar_service._calendar_ids = ["room@example.com"]

        await calendar_service._fetch_events()

        assert [e.id for e in calendar_service.events] == ["e1"]

    def test_events_sorted_by_start(self, calendar_service):
        later = make_event("e2", "Later", start_in=timedelta(hours=3))
        soon = make_event("e1", "Soon", start_in=timedelta(minutes=30))
        calendar_service._events = {e.id: e for e in (later, soon)}

        assert [e.title for e in calendar_service.events] == ["Soon", "Later"]

    def test_next_meeting_needs_url(self, calendar_service):
        no_url = make_event("e1", "No URL", start_in=timedelta(minutes=10))
        with_url = make_event("e2", "With URL", start_in=timedelta(minutes=30),
                              meeting_url=MEET_URL)
        past = make_event("e3", "Past", start_in=timedelta(hours=-3), meeting_url=MEET_URL)
        calendar_service._events = {e.id: e for e in (no_url, with_url, past)}

        calendar_service._update_next_meeting()

        assert calendar_service.next_meeting is with_url

    async def test_get_today_events(self, calendar_service):
        today_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        today = CalendarEvent("e1", "Today", today_start + timedelta(minutes=1),
                              today_start + timedelta(hours=1))
        tomorrow = CalendarEvent("e2", "Tomorrow", today_start + timedelta(days=1, hours=10),
                                 today_start + timedelta(days=1, hours=11))
        calendar_service._events = {e.id: e for e in (today, tomorrow)}

        today_events = await calendar_service.get_today_events()

        assert [e.title for e in today_events] == ["Today"]

    def test_get_event_by_id(self, calendar_service):
        calendar_service._events = {"e1": make_event("e1", "Event 1"),
                                    "e2": make_event("e2", "Event 2")}

        assert calendar_service.get_event_by_id("e2").title == "Event 2"

    def test_get_nonexistent_event(self, calendar_service):
        assert calendar_service.get_event_by_id("nonexistent") is None


class TestCalendarServiceAutoJoin:
    """Tests for meeting-starting notifications that drive auto-join."""

    @pytest.fixture
    def calendar_service(self):
        service = CalendarService({"auto_join_minutes": 2})
        self.started = []
        service.on_meeting_starting(self.started.append)
        return service

    def _check(self, service, *events):
        service._events = {e.id: e for e in events}
        service._check_upcoming_meetings()

    def test_notifies_meeting_starting_soon(self, calendar_service):
        event = make_event(start_in=timedelta(minutes=1), meeting_url=MEET_URL)
        self._check(calendar_service, event)
        assert self.started == [event]

    def test_notifies_meeting_in_progress(self, calendar_service):
        event = make_event(start_in=timedelta(minutes=-10), meeting_url=MEET_URL)
        self._check(calendar_service, event)
        assert self.started == [event]

    def test_not_too_early(self, calendar_service):
        self._check(calendar_service,
                    make_event(start_in=timedelta(minutes=30), meeting_url=MEET_URL))
        assert self.started == []

    def test_not_without_url(self, calendar_service):
        self._check(calendar_service, make_event(start_in=timedelta(minutes=1)))
        assert self.started == []

    def test_not_after_end(self, calendar_service):
        self._check(calendar_service,
                    make_event(start_in=timedelta(hours=-2), meeting_url=MEET_URL))
        assert self.started == []

    def test_notifies_only_once(self, calendar_service):
        event = make_event(start_in=timedelta(minutes=1), meeting_url=MEET_URL)
        self._check(calendar_service, event)
        calendar_service._check_upcoming_meetings()
        assert self.started == [event]

    async def test_start_checks_immediately(self, calendar_service):
        event = make_event(start_in=timedelta(minutes=-5), meeting_url=MEET_URL)
        calendar_service._provider = make_provider(events=[event])
        calendar_service._calendar_ids = ["room@example.com"]
        calendar_service._poll_interval = 3600

        await calendar_service.start()
        try:
            assert self.started == [event]
        finally:
            await calendar_service.stop()
