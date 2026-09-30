"""
Standby screen shown on the room display between meetings.

Shows the room, the time and the upcoming meetings of the room calendar.
The page is a self-contained template (standby.html); the agent pushes
meetings into it, so it needs no local web server.
"""

import base64
import json
import logging
import mimetypes
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

PLACEHOLDER = "/*CROOM_CONFIG*/{}"
MAX_MEETINGS = 8


def _logo_data_uri(path: str) -> Optional[str]:
    """Inline the logo: a page set with set_content cannot load local files."""
    if not path:
        return None
    file = Path(path).expanduser()
    mime = mimetypes.guess_type(file.name)[0]
    if not mime or not mime.startswith("image/"):
        logger.warning(f"Standby logo is not an image: {path}")
        return None
    try:
        encoded = base64.b64encode(file.read_bytes()).decode()
    except OSError as e:
        logger.warning(f"Cannot read standby logo {path}: {e}")
        return None
    return f"data:{mime};base64,{encoded}"


class StandbyScreen:
    """Renders the standby page and keeps its meeting list up to date."""

    def __init__(
        self,
        room_name: str = "",
        location: str = "",
        timezone_name: str = "UTC",
        language: str = "en",
        logo_path: str = "",
    ):
        self._config = {
            "room": room_name,
            "location": location,
            "timezone": timezone_name,
            "language": language,
            "logo": _logo_data_uri(logo_path),
        }
        self._meetings: List[Dict[str, str]] = []
        self._calendar_connected = False

    def html(self) -> str:
        template = resources.files("croom.meeting").joinpath("standby.html").read_text()
        # "</" is escaped so a room name cannot close the script element
        config = json.dumps(self._config).replace("</", "<\\/")
        return template.replace(PLACEHOLDER, config)

    def set_events(self, events: Iterable[Any], calendar_connected: bool = True) -> None:
        """Keep the next timed events (calendar events, all-day ones excluded)."""
        now = datetime.now(timezone.utc)
        upcoming = sorted(
            (
                e for e in events
                if not getattr(e, "is_all_day", False)
                and getattr(e, "status", "confirmed") != "cancelled"
                and e.end_time > now
            ),
            key=lambda e: e.start_time,
        )
        self._meetings = [
            {
                "title": e.title,
                "start": e.start_time.isoformat(),
                "end": e.end_time.isoformat(),
            }
            for e in upcoming[:MAX_MEETINGS]
        ]
        self._calendar_connected = calendar_connected

    def payload(self) -> Dict[str, Any]:
        return {"meetings": self._meetings, "calendar": self._calendar_connected}

    async def show(self, page) -> None:
        """Load the standby page in the room browser."""
        # A fresh document first: set_content alone keeps the previous
        # page's scripts and timers running
        await page.goto("about:blank")
        await page.set_content(self.html(), wait_until="domcontentloaded")
        await self.push(page)

    async def push(self, page) -> None:
        """Send the current meetings to an already displayed standby page."""
        await page.evaluate(
            "data => window.croomStandby && window.croomStandby.update(data)",
            self.payload(),
        )
