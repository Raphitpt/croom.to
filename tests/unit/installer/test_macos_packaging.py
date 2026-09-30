"""
The macOS example configuration must stay loadable: Config.from_dict
rejects unknown keys and load_config then falls back to defaults silently.
"""

import plistlib
from pathlib import Path

import yaml

from croom.core import service_options
from croom.core.config import Config

MACOS_DIR = Path(__file__).parents[3] / "packaging" / "macos"


def test_example_config_is_valid():
    data = yaml.safe_load((MACOS_DIR / "config.example.yaml").read_text())
    config = Config.from_dict(data)

    assert config.meeting.platforms == ["google_meet"]
    assert config.meeting.browser_channel == "chrome"
    assert config.display.backend == "firetv"
    assert config.calendar.providers == ["google"]


def test_example_config_enables_firetv_once_host_is_set():
    data = yaml.safe_load((MACOS_DIR / "config.example.yaml").read_text())
    data["display"]["firetv_host"] = "192.168.1.50"

    options = service_options.display_options(Config.from_dict(data))

    assert options["firetv_host"] == "192.168.1.50"
    assert options["cec_enabled"] is False


def test_launch_agent_template_is_a_valid_plist():
    template = (MACOS_DIR / "to.croom.agent.plist.in").read_text()
    rendered = (
        template.replace("@LABEL@", "to.croom.agent")
        .replace("@CROOM@", "/venv/bin/croom")
        .replace("@CONFIG@", "/config.yaml")
        .replace("@LOG_DIR@", "/logs")
        .replace("@BREW_PREFIX@", "/opt/homebrew")
    )
    plist = plistlib.loads(rendered.encode())

    assert plist["ProgramArguments"][:3] == ["/venv/bin/croom", "--config", "/config.yaml"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["LimitLoadToSessionType"] == "Aqua"
    assert "/opt/homebrew/bin" in plist["EnvironmentVariables"]["PATH"]
