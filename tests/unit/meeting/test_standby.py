"""
Tests for the standby screen and the browser watchdog.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from croom.core.config import Config
from croom.meeting.providers.base import MeetingState
from croom.meeting.service import MeetingService
from croom.meeting.standby import PLACEHOLDER, StandbyScreen

NOW = datetime.now(timezone.utc)


def event(title="Weekly", start_min=10, dur_min=30, **extra):
    start = NOW + timedelta(minutes=start_min)
    return SimpleNamespace(title=title, start_time=start,
                           end_time=start + timedelta(minutes=dur_min), **extra)


def injected_config(html):
    start = html.index("const CONFIG = ") + len("const CONFIG = ")
    end = html.index(";\n", start)
    return json.loads(html[start:end].replace("<\\/", "</"))


class TestStandbyScreen:
    def test_config_injected(self):
        html = StandbyScreen("Salle A", "2e étage", "Europe/Paris", "fr").html()

        assert PLACEHOLDER not in html
        config = injected_config(html)
        assert config == {"room": "Salle A", "location": "2e étage", "timezone": "Europe/Paris",
                          "language": "fr", "logo": None}

    def test_room_name_cannot_close_the_script(self):
        html = StandbyScreen("</script><script>alert(1)</script>").html()
        assert "</script><script>alert(1)" not in html

    def test_titles_rendered_as_text(self):
        # Titles come from outside invitations: the page must never use innerHTML
        html = StandbyScreen().html()
        assert "innerHTML" not in html
        assert "textContent" in html

    def test_logo_inlined_as_data_uri(self, temp_dir):
        logo = temp_dir / "logo.svg"
        logo.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")

        config = injected_config(StandbyScreen(logo_path=str(logo)).html())

        assert config["logo"].startswith("data:image/svg+xml;base64,")

    def test_missing_or_non_image_logo_ignored(self, temp_dir):
        notes = temp_dir / "notes.txt"
        notes.write_text("hello")
        assert injected_config(StandbyScreen(logo_path=str(notes)).html())["logo"] is None
        assert injected_config(StandbyScreen(logo_path=str(temp_dir / "x.png")).html())["logo"] is None

    def test_upcoming_events_sorted_and_filtered(self):
        screen = StandbyScreen()
        screen.set_events([
            event("Later", start_min=120),
            event("Past", start_min=-90),
            event("All day", is_all_day=True),
            event("Cancelled", status="cancelled"),
            event("Soon", start_min=5),
            event("Now", start_min=-10),
        ])

        titles = [m["title"] for m in screen.payload()["meetings"]]
        assert titles == ["Now", "Soon", "Later"]
        assert screen.payload()["calendar"] is True

    def test_no_calendar(self):
        screen = StandbyScreen()
        screen.set_events([], calendar_connected=False)
        assert screen.payload() == {"meetings": [], "calendar": False}

    async def test_show_resets_the_page_first(self):
        page = AsyncMock()
        calls = []
        page.goto.side_effect = lambda url: calls.append(("goto", url))
        page.set_content.side_effect = lambda html, **kw: calls.append(("set_content",))

        await StandbyScreen().show(page)

        assert calls == [("goto", "about:blank"), ("set_content",)]
        page.evaluate.assert_awaited_once()


class FakeProvider:
    display_name = "Google Meet"

    def __init__(self):
        self.page = AsyncMock()
        self.is_ready = True
        self.state = MeetingState.IDLE
        self.join_meeting = AsyncMock()
        self.leave_meeting = AsyncMock()
        self.shutdown = AsyncMock()
        self.initialize = AsyncMock()

    def add_state_callback(self, callback):
        pass

    def can_handle_url(self, url):
        return True


@pytest.fixture
def service():
    config = Config()
    config.meeting.platforms = ["google_meet"]
    svc = MeetingService(config)
    provider = FakeProvider()
    svc._providers = {"google_meet": provider}
    svc.standby = MagicMock()
    svc.standby.show = AsyncMock()
    svc.standby.push = AsyncMock()
    return svc, provider


class TestMeetingServiceStandby:
    async def test_standby_after_leaving(self, service):
        svc, provider = service
        svc._active_provider = provider

        await svc.leave_meeting()

        svc.standby.show.assert_awaited_once_with(provider.page)

    async def test_standby_after_failed_join(self, service):
        svc, provider = service
        provider.join_meeting.side_effect = RuntimeError("Could not find join button")

        with pytest.raises(RuntimeError):
            await svc.join_meeting("https://meet.google.com/abc-defg-hij")

        svc.standby.show.assert_awaited_once()
        assert svc._active_provider is None

    async def test_update_pushes_when_idle(self, service):
        svc, provider = service

        await svc.update_standby([event()])

        svc.standby.set_events.assert_called_once()
        svc.standby.push.assert_awaited_once_with(provider.page)

    async def test_update_not_pushed_during_meeting(self, service):
        svc, provider = service
        provider.state = MeetingState.CONNECTED
        svc._active_provider = provider

        await svc.update_standby([event()])

        svc.standby.set_events.assert_called_once()  # kept for after the meeting
        svc.standby.push.assert_not_awaited()

    async def test_closed_browser_relaunched_before_join(self, service):
        svc, provider = service
        provider.is_ready = False

        await svc.join_meeting("https://meet.google.com/abc-defg-hij")

        provider.shutdown.assert_awaited_once()
        provider.initialize.assert_awaited_once()
        provider.join_meeting.assert_awaited_once()

    async def test_watchdog_relaunches_closed_browser(self, service):
        svc, provider = service
        provider.is_ready = False

        async def relaunch():
            provider.is_ready = True

        provider.initialize.side_effect = relaunch
        task = asyncio.create_task(svc._browser_watchdog(interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

        provider.initialize.assert_awaited_once()
        svc.standby.show.assert_awaited()
