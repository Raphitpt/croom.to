"""
Tests for the room browser and its use by the Google Meet provider.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from croom.core.config import Config
from croom.meeting import browser
from croom.meeting.browser import BrowserOptions, launch_room_browser
from croom.meeting.providers.google_meet import GoogleMeetProvider
from croom.meeting.providers.base import MeetingState
from croom.meeting.service import MeetingService


class TestBrowserOptions:
    def test_default_profile_dir_macos(self, monkeypatch):
        monkeypatch.setattr(browser.sys, "platform", "darwin")
        path = browser.default_profile_dir()
        assert path.endswith("Library/Application Support/Croom/browser")

    def test_default_profile_dir_linux(self, monkeypatch):
        monkeypatch.setattr(browser.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", "/data")
        assert browser.default_profile_dir() == "/data/croom/browser"

    def test_custom_profile_dir_expands_home(self):
        options = BrowserOptions(profile_dir="~/room")
        assert options.resolved_profile_dir == str(Path.home() / "room")

    def test_kiosk_and_media_flags(self):
        args = BrowserOptions().chromium_args()
        assert "--kiosk" in args
        assert "--use-fake-ui-for-media-stream" in args
        assert "--disable-blink-features=AutomationControlled" in args
        # Hardware video rendering stays on
        assert "--disable-gpu" not in args

    def test_windowed_mode(self):
        args = BrowserOptions(fullscreen=False).chromium_args()
        assert "--kiosk" not in args
        assert "--window-size=1920,1080" in args

    def test_no_sandbox_only_as_root_on_linux(self, monkeypatch):
        monkeypatch.setattr(browser.sys, "platform", "linux")
        monkeypatch.setattr(browser.os, "geteuid", lambda: 1000)
        assert "--no-sandbox" not in BrowserOptions().chromium_args()
        monkeypatch.setattr(browser.os, "geteuid", lambda: 0)
        assert "--no-sandbox" in BrowserOptions().chromium_args()

    def test_no_linux_flags_on_macos(self, monkeypatch):
        monkeypatch.setattr(browser.sys, "platform", "darwin")
        args = BrowserOptions().chromium_args()
        assert "--no-sandbox" not in args
        assert "--disable-dev-shm-usage" not in args


class TestLaunchRoomBrowser:
    async def test_persistent_profile(self, temp_dir):
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context = AsyncMock(return_value="ctx")
        profile = temp_dir / "profile"

        ctx = await launch_room_browser(
            playwright, BrowserOptions(profile_dir=str(profile), channel="chrome")
        )

        assert ctx == "ctx"
        assert profile.is_dir()
        args, kwargs = playwright.chromium.launch_persistent_context.call_args
        assert args[0] == str(profile)
        assert kwargs["channel"] == "chrome"
        assert kwargs["headless"] is False
        assert kwargs["ignore_default_args"] == ["--enable-automation"]
        assert set(kwargs["permissions"]) == {"camera", "microphone"}

    async def test_bundled_chromium_by_default(self, temp_dir):
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context = AsyncMock()

        await launch_room_browser(playwright, BrowserOptions(profile_dir=str(temp_dir)))

        assert playwright.chromium.launch_persistent_context.call_args.kwargs["channel"] is None


class TestGoogleMeetProvider:
    @pytest.fixture
    def page(self):
        page = AsyncMock()
        page.keyboard = AsyncMock()
        return page

    async def test_initialize_reuses_profile_page(self, page):
        context = MagicMock(pages=[page])
        with patch("croom.meeting.providers.google_meet.async_playwright") as ap, \
             patch("croom.meeting.providers.google_meet.launch_room_browser",
                   AsyncMock(return_value=context)) as launch:
            ap.return_value.start = AsyncMock(return_value="pw")
            provider = GoogleMeetProvider(BrowserOptions(channel="chrome"))
            await provider.initialize()

        assert provider._page is page
        assert launch.call_args.args[1].channel == "chrome"

    async def test_join_does_not_wait_for_network_idle(self, page):
        provider = GoogleMeetProvider()
        provider._page = page
        with patch.object(provider, "_handle_prejoin", AsyncMock()), \
             patch.object(provider, "_click_join_button", AsyncMock()), \
             patch.object(provider, "_wait_for_connection", AsyncMock()), \
             patch("croom.meeting.providers.google_meet.asyncio.sleep", AsyncMock()):
            await provider.join_meeting("https://meet.google.com/abc-defg-hij")

        assert page.goto.call_args.kwargs["wait_until"] == "domcontentloaded"
        assert provider.state == MeetingState.CONNECTED

    @pytest.mark.parametrize("method, key", [
        ("toggle_camera", "ControlOrMeta+e"),
        ("toggle_mute", "ControlOrMeta+d"),
    ])
    async def test_shortcuts_work_on_macos(self, page, method, key):
        provider = GoogleMeetProvider()
        provider._page = page
        provider._state = MeetingState.CONNECTED
        with patch("croom.meeting.providers.google_meet.asyncio.sleep", AsyncMock()):
            await getattr(provider, method)()

        page.keyboard.press.assert_awaited_once_with(key)


class TestMeetingServiceBrowserOptions:
    async def test_meet_gets_browser_options_from_config(self):
        config = Config()
        config.meeting.platforms = ["google_meet", "zoom"]
        config.meeting.browser_channel = "chrome"
        config.meeting.browser_profile_dir = "/rooms/a"
        config.meeting.fullscreen = False
        created = {}

        class FakeMeet:
            def __init__(self, browser_options=None):
                created["meet"] = browser_options

            async def initialize(self):
                pass

        class FakeZoom:
            def __init__(self):
                created["zoom"] = True

            async def initialize(self):
                pass

        providers = {"google_meet": FakeMeet, "zoom": FakeZoom}
        with patch("croom.meeting.service.get_provider", side_effect=providers.get):
            await MeetingService(config).start()

        options = created["meet"]
        assert (options.channel, options.profile_dir, options.fullscreen) == ("chrome", "/rooms/a", False)
        assert created["zoom"] is True
