"""
Tests for calendar-driven auto-join in croom.core.agent.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from croom.core.agent import CroomAgent


def make_event(url="https://meet.google.com/abc-defg-hij", minutes_left=30):
    return SimpleNamespace(
        title="Weekly",
        meeting_url=url,
        end_time=datetime.now(timezone.utc) + timedelta(minutes=minutes_left),
    )


@pytest.fixture
def agent():
    with patch("croom.core.agent.PlatformDetector.detect", return_value=MagicMock()), \
         patch("croom.core.agent.CapabilityDetector.detect", return_value=MagicMock()):
        agent = CroomAgent()
    meeting = MagicMock()
    meeting.is_running = True
    meeting.is_in_meeting = False
    meeting.join_meeting = AsyncMock()
    meeting.leave_meeting = AsyncMock()
    display = MagicMock()
    display.is_running = True
    display.on_meeting_start = AsyncMock()
    display.on_meeting_end = AsyncMock()
    display.power_off = AsyncMock()
    agent.display = display
    services = {"meeting": meeting, "display": display}
    agent.service_manager.get_service = MagicMock(side_effect=services.get)
    return agent, meeting


class TestCalendarAutoJoin:
    async def test_joins_calendar_meeting(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = False
        event = make_event()

        await agent._join_calendar_meeting(event)

        meeting.join_meeting.assert_awaited_once_with(event.meeting_url)

    async def test_does_not_interrupt_current_meeting(self, agent):
        agent, meeting = agent
        meeting.is_in_meeting = True

        await agent._join_calendar_meeting(make_event())

        meeting.join_meeting.assert_not_awaited()

    async def test_leaves_at_end_time(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = True
        event = make_event(minutes_left=0)

        async def join(url):
            meeting.is_in_meeting = True
            meeting.current_meeting = SimpleNamespace(meeting_url=url)

        meeting.join_meeting.side_effect = join

        await agent._join_calendar_meeting(event)

        meeting.leave_meeting.assert_awaited_once()

    async def test_stays_if_room_moved_to_another_meeting(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = True
        event = make_event(minutes_left=0)

        async def join(url):
            meeting.is_in_meeting = True
            meeting.current_meeting = SimpleNamespace(meeting_url="https://meet.google.com/other")

        meeting.join_meeting.side_effect = join

        await agent._join_calendar_meeting(event)

        meeting.leave_meeting.assert_not_awaited()

    async def test_join_failure_is_logged_not_raised(self, agent):
        agent, meeting = agent
        meeting.join_meeting.side_effect = RuntimeError("browser crashed")

        await agent._join_calendar_meeting(make_event())

        meeting.leave_meeting.assert_not_awaited()


class TestCalendarMeetingDisplay:
    async def test_tv_woken_before_joining(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = False
        order = []
        agent.display.on_meeting_start.side_effect = lambda: order.append("tv")
        meeting.join_meeting.side_effect = lambda url: order.append("join")

        await agent._join_calendar_meeting(make_event())

        assert order == ["tv", "join"]

    async def test_tv_standby_after_meeting_when_configured(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = True
        agent.config.display.power_off_after_meeting = True
        event = make_event(minutes_left=0)

        async def join(url):
            meeting.is_in_meeting = True
            meeting.current_meeting = SimpleNamespace(meeting_url=url)

        meeting.join_meeting.side_effect = join

        await agent._join_calendar_meeting(event)

        agent.display.power_off.assert_awaited_once()

    async def test_tv_left_on_by_default(self, agent):
        agent, meeting = agent
        agent.config.meeting.auto_leave = True
        event = make_event(minutes_left=0)

        async def join(url):
            meeting.is_in_meeting = True
            meeting.current_meeting = SimpleNamespace(meeting_url=url)

        meeting.join_meeting.side_effect = join

        await agent._join_calendar_meeting(event)

        agent.display.power_off.assert_not_awaited()
