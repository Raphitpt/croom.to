"""
Translate the typed Config into the option dicts each service expects.

Services take plain dicts so they can be used standalone; the agent owns
the mapping from the global configuration.
"""

import json
import logging
from typing import Any, Dict, Optional

from croom.core.config import Config

logger = logging.getLogger(__name__)

RESOLUTIONS = {
    "480p": "640x480",
    "720p": "1280x720",
    "1080p": "1920x1080",
    "4k": "3840x2160",
}


def _device(value: str) -> str:
    return "default" if value in ("", "auto") else value


def audio_options(config: Config) -> Dict[str, Any]:
    return {
        "input_device": _device(config.audio.input_device),
        "output_device": _device(config.audio.output_device),
        "noise_reduction": config.ai.noise_reduction
        and config.audio.noise_reduction_level != "off",
        "echo_cancellation": config.audio.echo_cancellation,
    }


def video_options(config: Config) -> Dict[str, Any]:
    return {
        "camera": _device(config.video.device),
        "resolution": RESOLUTIONS.get(config.video.resolution, config.video.resolution),
        "fps": config.video.framerate,
    }


def display_options(config: Config) -> Dict[str, Any]:
    backend = config.display.backend
    return {
        "cec_enabled": backend in ("auto", "hdmi_cec"),
        "ddc_enabled": backend in ("auto", "ddc"),
        "auto_power_on": config.display.power_on_boot,
    }


def calendar_options(config: Config) -> Optional[Dict[str, Any]]:
    """Calendar options, or None when no provider is configured."""
    cal = config.calendar
    provider = cal.providers[0] if cal.providers else None

    if provider == "google":
        credentials = google_credentials(cal.google_credentials_path, cal.google_delegate_email)
        if credentials is None:
            return None
    else:
        # Other providers are configured through their own credentials dict
        return None

    return {
        "provider": provider,
        "credentials": credentials,
        "calendar_ids": cal.calendar_ids,
        "poll_interval": cal.sync_interval_seconds,
        "auto_join_minutes": config.meeting.join_early_minutes,
    }


def google_credentials(path: str, delegate_email: str = "") -> Optional[Dict[str, Any]]:
    """Build GoogleCalendarProvider credentials from a JSON file.

    Accepts a service account key (Workspace, delegated to the room
    account) or an OAuth token of the room account.
    """
    if not path:
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f"Cannot read Google credentials {path}: {e}")
        return None

    if data.get("type") == "service_account":
        credentials: Dict[str, Any] = {"service_account_file": path}
        if delegate_email:
            credentials["delegate_email"] = delegate_email
        return credentials

    # Token files written by google-auth use "token"; the provider expects "access_token"
    token = dict(data)
    if "access_token" not in token and "token" in token:
        token["access_token"] = token["token"]
    return {"oauth_token": token}


def dashboard_options(config: Config) -> Dict[str, Any]:
    return {
        "url": config.dashboard.url,
        "api_key": config.dashboard.enrollment_token,
        "room_name": config.room.name,
        "heartbeat_interval": config.dashboard.heartbeat_interval_seconds,
    }
