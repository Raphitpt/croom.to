"""
Room browser for meeting providers.

Meetings run in a persistent browser profile so the room account stays
signed in across restarts (otherwise every meeting is joined as an
anonymous guest waiting to be admitted). ``croom-login`` opens the same
profile in a normal, non-automated browser to sign in once: Google often
refuses sign-in from automated browsers.
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

GOOGLE_SIGN_IN_URL = "https://accounts.google.com/"


def default_profile_dir() -> str:
    """Per-OS location of the room browser profile."""
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support" / "Croom" / "browser")
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return str(Path(data_home) / "croom" / "browser")


@dataclass
class BrowserOptions:
    """How the room browser is launched."""
    profile_dir: str = ""  # Empty: default_profile_dir()
    channel: str = ""  # "chrome" for installed Google Chrome, empty for Playwright's Chromium
    fullscreen: bool = True  # Kiosk mode on the room display

    @property
    def resolved_profile_dir(self) -> str:
        return os.path.expanduser(self.profile_dir) if self.profile_dir else default_profile_dir()

    def chromium_args(self) -> List[str]:
        args = [
            "--use-fake-ui-for-media-stream",  # Auto-accept camera/mic prompts
            # The room's own browser: don't flag it as automated to Meet
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--noerrdialogs",
        ]
        if self.fullscreen:
            args.append("--kiosk")
        else:
            args.append("--window-size=1920,1080")
        if sys.platform.startswith("linux"):
            args.append("--disable-dev-shm-usage")
            # Chromium refuses to start as root with its sandbox enabled
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                args.append("--no-sandbox")
        return args


async def launch_room_browser(playwright, options: BrowserOptions):
    """Launch a persistent browser context for meetings."""
    profile_dir = options.resolved_profile_dir
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    return await playwright.chromium.launch_persistent_context(
        profile_dir,
        headless=False,  # Meeting pages need a visible browser
        channel=options.channel or None,
        args=options.chromium_args(),
        # Hides the "controlled by automated software" banner
        ignore_default_args=["--enable-automation"],
        permissions=["camera", "microphone"],
        no_viewport=True,  # Follow the window/screen size
    )


def _browser_executable(channel: str) -> Optional[str]:
    """Executable for a normal (non-automated) session on the room profile."""
    if channel == "chrome":
        if sys.platform == "darwin":
            path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        else:
            path = "/usr/bin/google-chrome"
        return path if os.path.exists(path) else None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        return p.chromium.executable_path


def login_main(argv: Optional[List[str]] = None) -> int:
    """Entry point of ``croom-login``: sign the room account in once."""
    from croom.core.config import load_config

    parser = argparse.ArgumentParser(
        description="Open the room browser profile to sign in the room's Google account"
    )
    parser.add_argument("-c", "--config", help="Path to configuration file", default=None)
    parser.add_argument("--url", default=GOOGLE_SIGN_IN_URL, help="Page to open")
    args = parser.parse_args(argv)

    meeting = load_config(args.config).meeting
    options = BrowserOptions(
        profile_dir=meeting.browser_profile_dir,
        channel=meeting.browser_channel,
    )
    executable = _browser_executable(options.channel)
    if not executable:
        print("Browser not found. Install Google Chrome, or run: playwright install chromium",
              file=sys.stderr)
        return 1

    profile_dir = options.resolved_profile_dir
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    print(f"Profile: {profile_dir}")
    print("Sign in to the room account, then close the browser window.")
    print("Stop the Croom agent first: a profile can only be open once.")
    return subprocess.call([executable, f"--user-data-dir={profile_dir}", args.url])


if __name__ == "__main__":
    sys.exit(login_main())
