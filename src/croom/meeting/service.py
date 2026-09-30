"""
Meeting Service for Croom.

High-level service that manages meeting providers and handles
meeting lifecycle.
"""

import asyncio
import inspect
import logging
from typing import Optional, Dict, Any, List, Callable

from croom.core.config import Config
from croom.core.service import Service
from croom.meeting.browser import BrowserOptions
from croom.meeting.standby import StandbyScreen
from croom.meeting.providers.base import (
    MeetingProvider,
    MeetingInfo,
    MeetingState,
    detect_platform,
)
from croom.meeting.providers import get_provider, get_all_providers

logger = logging.getLogger(__name__)


class MeetingService(Service):
    """
    High-level meeting service.

    Manages meeting providers and provides unified interface
    for joining and controlling meetings across platforms.
    """

    def __init__(self, config: Config):
        super().__init__("meeting")
        self.config = config

        self._providers: Dict[str, MeetingProvider] = {}
        self._active_provider: Optional[MeetingProvider] = None
        self._state_callbacks: List[Callable[[MeetingState], None]] = []

        self.standby = StandbyScreen(
            room_name=config.room.name,
            location=config.room.location,
            timezone_name=config.room.timezone,
            language=config.room.language,
            logo_path=config.room.logo_path,
        )
        self._watchdog_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start meeting service."""
        browser_options = BrowserOptions(
            profile_dir=self.config.meeting.browser_profile_dir,
            channel=self.config.meeting.browser_channel,
            fullscreen=self.config.meeting.fullscreen,
        )

        # Initialize configured providers
        for platform in self.config.meeting.platforms:
            provider_cls = get_provider(platform)
            if provider_cls:
                try:
                    # Providers using the shared room browser take its options
                    if "browser_options" in inspect.signature(provider_cls).parameters:
                        provider = provider_cls(browser_options=browser_options)
                    else:
                        provider = provider_cls()
                    await provider.initialize()
                    self._providers[platform] = provider
                    logger.info(f"Initialized meeting provider: {platform}")
                except Exception as e:
                    logger.error(f"Failed to initialize {platform} provider: {e}")

        if not self._providers:
            logger.warning("No meeting providers available")

        await self.show_standby()
        self._watchdog_task = asyncio.create_task(self._browser_watchdog())

        logger.info(f"Meeting service started with {len(self._providers)} providers")

    async def stop(self) -> None:
        """Stop meeting service."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None

        # Leave any active meeting
        if self._active_provider and self._active_provider.state == MeetingState.CONNECTED:
            await self.leave_meeting()

        # Shutdown all providers
        for name, provider in self._providers.items():
            try:
                await provider.shutdown()
                logger.info(f"Shutdown provider: {name}")
            except Exception as e:
                logger.error(f"Error shutting down {name}: {e}")

        self._providers.clear()
        self._active_provider = None

        logger.info("Meeting service stopped")

    def add_state_callback(self, callback: Callable[[MeetingState], None]) -> None:
        """Add callback for meeting state changes."""
        self._state_callbacks.append(callback)

    def _on_state_change(self, state: MeetingState) -> None:
        """Handle state changes from provider."""
        for callback in self._state_callbacks:
            try:
                callback(state)
            except Exception:
                pass

    async def join_meeting(
        self,
        meeting_url: str,
        display_name: Optional[str] = None,
        camera_on: Optional[bool] = None,
        mic_on: Optional[bool] = None
    ) -> MeetingInfo:
        """
        Join a meeting.

        Automatically detects the platform from the URL and uses
        the appropriate provider.

        Args:
            meeting_url: Meeting URL or code
            display_name: Name to display (default: room name)
            camera_on: Start with camera (default: config setting)
            mic_on: Start with mic (default: config setting)

        Returns:
            MeetingInfo with connection details
        """
        # Apply defaults from config
        if display_name is None:
            display_name = self.config.room.name or "Conference Room"
        if camera_on is None:
            camera_on = self.config.meeting.camera_default_on
        if mic_on is None:
            mic_on = self.config.meeting.mic_default_on

        # Detect platform
        platform = detect_platform(meeting_url)

        if not platform:
            # Try each provider
            for name, provider in self._providers.items():
                if provider.can_handle_url(meeting_url):
                    platform = name
                    break

        if not platform:
            raise ValueError(f"Cannot determine meeting platform for: {meeting_url}")

        # Get provider
        provider = self._providers.get(platform)
        if not provider:
            raise RuntimeError(f"Provider not available: {platform}")

        # Leave any existing meeting
        if self._active_provider and self._active_provider.state == MeetingState.CONNECTED:
            await self.leave_meeting()

        # The browser may have been closed since the last meeting
        if not provider.is_ready:
            await self._restart_provider(provider)

        # Setup state callback
        provider.add_state_callback(self._on_state_change)

        # Join meeting
        self._active_provider = provider
        try:
            meeting_info = await provider.join_meeting(
                meeting_url,
                display_name=display_name,
                camera_on=camera_on,
                mic_on=mic_on
            )
        except Exception:
            # Don't leave Meet's error page on the room display
            self._active_provider = None
            await self.show_standby()
            raise

        return meeting_info

    async def leave_meeting(self) -> None:
        """Leave the current meeting."""
        if self._active_provider:
            await self._active_provider.leave_meeting()
            self._active_provider = None
        await self.show_standby()

    def _display_page(self):
        """Page of the provider whose browser is on the room display."""
        for provider in self._providers.values():
            if provider.page is not None and provider.is_ready:
                return provider.page
        return None

    async def show_standby(self) -> None:
        """Show the standby screen between meetings."""
        page = self._display_page()
        if page is None or self.is_in_meeting:
            return
        try:
            await self.standby.show(page)
        except Exception as e:
            logger.warning(f"Cannot show standby screen: {e}")

    async def update_standby(self, events, calendar_connected: bool = True) -> None:
        """Refresh the meetings listed on the standby screen."""
        self.standby.set_events(events, calendar_connected)
        page = self._display_page()
        if page is None or self.is_in_meeting:
            return
        try:
            await self.standby.push(page)
        except Exception as e:
            logger.warning(f"Cannot update standby screen: {e}")

    async def _restart_provider(self, provider: MeetingProvider) -> None:
        logger.warning(f"{provider.display_name} browser is gone, relaunching it")
        if provider is self._active_provider:
            logger.warning("The meeting in progress was lost")
            self._active_provider = None
        await provider.shutdown()
        await provider.initialize()

    async def _browser_watchdog(self, interval: float = 15) -> None:
        """Relaunch a browser that was closed by hand or crashed."""
        while True:
            await asyncio.sleep(interval)
            for provider in list(self._providers.values()):
                if provider.is_ready:
                    continue
                try:
                    await self._restart_provider(provider)
                    await self.show_standby()
                except Exception as e:
                    logger.error(f"Failed to relaunch {provider.display_name}: {e}")

    async def toggle_camera(self) -> bool:
        """
        Toggle camera on/off.

        Returns:
            New camera state (True = on)
        """
        if not self._active_provider:
            return False
        return await self._active_provider.toggle_camera()

    async def toggle_mute(self) -> bool:
        """
        Toggle microphone mute.

        Returns:
            New mute state (True = muted)
        """
        if not self._active_provider:
            return True
        return await self._active_provider.toggle_mute()

    async def set_camera(self, on: bool) -> bool:
        """Set camera state."""
        if not self._active_provider:
            return False
        return await self._active_provider.set_camera(on)

    async def set_mute(self, muted: bool) -> bool:
        """Set mute state."""
        if not self._active_provider:
            return True
        return await self._active_provider.set_mute(muted)

    @property
    def state(self) -> MeetingState:
        """Get current meeting state."""
        if self._active_provider:
            return self._active_provider.state
        return MeetingState.IDLE

    @property
    def current_meeting(self) -> Optional[MeetingInfo]:
        """Get current meeting info."""
        if self._active_provider:
            return self._active_provider.current_meeting
        return None

    @property
    def is_in_meeting(self) -> bool:
        """Check if currently in a meeting."""
        return self.state == MeetingState.CONNECTED

    def get_available_platforms(self) -> List[str]:
        """Get list of available platforms."""
        return list(self._providers.keys())

    def get_details(self) -> Dict[str, Any]:
        """Get meeting service status."""
        return {
            "running": self.is_running,
            "state": self.state.value,
            "meeting": self.current_meeting.to_dict() if self.current_meeting else None,
            "available_platforms": self.get_available_platforms(),
        }
