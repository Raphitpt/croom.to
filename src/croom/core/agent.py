"""
Croom Agent - Main coordinator for the Croom system.

The agent orchestrates all services and handles the main event loop.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from croom.core.config import Config, load_config
from croom.core.service import ServiceManager, Service, ServiceState
from croom.core import service_options
from croom.platform.detector import PlatformDetector, PlatformInfo
from croom.platform.capabilities import CapabilityDetector, Capabilities

logger = logging.getLogger(__name__)


class CroomAgent:
    """
    Main Croom agent that coordinates all system components.

    The agent is responsible for:
    - Loading configuration
    - Detecting platform capabilities
    - Managing service lifecycle
    - Handling system signals
    - Coordinating between services
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the Croom agent.

        Args:
            config_path: Optional path to configuration file.
        """
        self.config: Config = load_config(config_path)
        self.platform_info: PlatformInfo = PlatformDetector.detect()
        self.capabilities: Capabilities = CapabilityDetector.detect()
        self.service_manager = ServiceManager()

        self._running = False
        self._main_task: Optional[asyncio.Task] = None
        self._meeting_tasks: set = set()

        logger.info(f"Croom Agent initialized on {self.platform_info.device.value}")
        logger.info(f"AI accelerators: {self.platform_info.ai_accelerators}")

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s))
            )

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signal."""
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        await self.stop()

    def _initialize_services(self) -> None:
        """Initialize and register all services based on configuration.

        Only the meeting service is required: a room without a working camera
        control, TV link or dashboard must still be able to join meetings.
        """
        # Import services lazily to avoid circular imports
        # and allow optional dependencies
        ai_deps = None

        # AI Service (if enabled)
        if self.config.ai.enabled and not self.config.ai.privacy_mode:
            try:
                from croom.ai.service import AIService
                self.service_manager.register(
                    AIService(self.config, self.capabilities), required=False
                )
                ai_deps = ["ai"]
                logger.info("AI service registered")
            except ImportError as e:
                logger.warning(f"AI service not available: {e}")

        # Audio Service
        try:
            from croom.audio.service import AudioService
            self.service_manager.register(
                AudioService(service_options.audio_options(self.config)),
                dependencies=ai_deps,
                required=False,
            )
            logger.info("Audio service registered")
        except ImportError as e:
            logger.warning(f"Audio service not available: {e}")

        # Video Service
        try:
            from croom.video.service import VideoService
            self.service_manager.register(
                VideoService(service_options.video_options(self.config)),
                dependencies=ai_deps,
                required=False,
            )
            logger.info("Video service registered")
        except ImportError as e:
            logger.warning(f"Video service not available: {e}")

        # Display Service
        try:
            from croom.display.service import DisplayService
            self.service_manager.register(
                DisplayService(service_options.display_options(self.config)),
                required=False,
            )
            logger.info("Display service registered")
        except ImportError as e:
            logger.warning(f"Display service not available: {e}")

        # Meeting Service
        from croom.meeting.service import MeetingService
        meeting_service = MeetingService(self.config)
        self.service_manager.register(
            meeting_service,
            dependencies=[n for n in ("audio", "video") if self.service_manager.get_service(n)],
        )
        logger.info("Meeting service registered")

        # Calendar Service
        calendar_options = service_options.calendar_options(self.config)
        if calendar_options is None:
            logger.info("Calendar not configured, auto-join disabled")
        else:
            try:
                from croom.calendar.service import CalendarService
                calendar_service = CalendarService(calendar_options)
                if self.config.calendar.auto_join:
                    calendar_service.on_meeting_starting(self._on_calendar_meeting)
                self.service_manager.register(calendar_service, required=False)
                logger.info("Calendar service registered")
            except ImportError as e:
                logger.warning(f"Calendar service not available: {e}")

        # Dashboard Connection Service
        if self.config.dashboard.enabled and self.config.dashboard.url:
            try:
                from croom.dashboard.client import DashboardClient
                self.service_manager.register(
                    DashboardClient(service_options.dashboard_options(self.config)),
                    required=False,
                )
                logger.info("Dashboard client registered")
            except ImportError as e:
                logger.warning(f"Dashboard client not available: {e}")

    def _on_calendar_meeting(self, event) -> None:
        """Calendar callback: a meeting with a video link is starting."""
        task = asyncio.create_task(self._join_calendar_meeting(event))
        self._meeting_tasks.add(task)
        task.add_done_callback(self._meeting_tasks.discard)

    async def _join_calendar_meeting(self, event) -> None:
        """Join a calendar meeting, then leave it at its end time."""
        meeting = self.service_manager.get_service("meeting")
        if meeting is None or not meeting.is_running:
            return
        if meeting.is_in_meeting:
            logger.info(f"Already in a meeting, not joining '{event.title}'")
            return

        logger.info(f"Auto-joining '{event.title}': {event.meeting_url}")
        try:
            await meeting.join_meeting(event.meeting_url)
        except Exception as e:
            logger.error(f"Auto-join failed for '{event.title}': {e}")
            return

        if not self.config.meeting.auto_leave:
            return

        delay = (event.end_time - datetime.now(timezone.utc)).total_seconds()
        await asyncio.sleep(max(delay, 0))
        current = meeting.current_meeting
        if meeting.is_in_meeting and current and current.meeting_url == event.meeting_url:
            logger.info(f"'{event.title}' is over, leaving")
            await meeting.leave_meeting()

    async def start(self) -> None:
        """Start the Croom agent and all services."""
        if self._running:
            logger.warning("Agent already running")
            return

        logger.info("Starting Croom Agent...")
        self._running = True

        try:
            # Setup signal handlers
            self._setup_signal_handlers()

            # Initialize services
            self._initialize_services()

            # Start all services
            success = await self.service_manager.start_all()
            if not success:
                logger.error("Failed to start all services")
                self._running = False
                return

            logger.info("Croom Agent started successfully")

            # Wait for shutdown
            await self.service_manager.wait_for_shutdown()

        except Exception as e:
            logger.error(f"Agent error: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Croom agent and all services."""
        if not self._running:
            return

        logger.info("Stopping Croom Agent...")
        self._running = False

        for task in list(self._meeting_tasks):
            task.cancel()

        # Stop all services
        await self.service_manager.stop_all()

        logger.info("Croom Agent stopped")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current agent status.

        Returns:
            Dictionary containing agent and service status.
        """
        return {
            "running": self._running,
            "platform": {
                "device": self.platform_info.device.value,
                "os": self.platform_info.os_name,
                "arch": self.platform_info.arch,
                "ai_accelerators": self.platform_info.ai_accelerators,
            },
            "capabilities": self.capabilities.to_dict(),
            "services": {
                name: status.__dict__
                for name, status in self.service_manager.get_status().items()
            },
            "config": {
                "room_name": self.config.room.name,
                "ai_enabled": self.config.ai.enabled,
                "platforms": self.config.meeting.platforms,
            }
        }

    def get_capabilities(self) -> Capabilities:
        """Get detected platform capabilities."""
        return self.capabilities

    def get_platform_info(self) -> PlatformInfo:
        """Get platform information."""
        return self.platform_info


async def run_agent(config_path: Optional[str] = None) -> None:
    """
    Run the Croom agent.

    Args:
        config_path: Optional path to configuration file.
    """
    agent = CroomAgent(config_path)
    await agent.start()


def main() -> None:
    """Main entry point for Croom agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Croom Agent")
    parser.add_argument(
        "-c", "--config",
        help="Path to configuration file",
        default=None
    )
    parser.add_argument(
        "-v", "--verbose",
        help="Enable verbose logging",
        action="store_true"
    )
    parser.add_argument(
        "--debug",
        help="Enable debug logging",
        action="store_true"
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run agent
    try:
        asyncio.run(run_agent(args.config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
