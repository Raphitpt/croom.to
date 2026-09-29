"""
Tests for croom.core.service_options module.
"""

import json

from croom.core import service_options
from croom.core.config import Config


class TestServiceOptions:
    def test_video_resolution_and_auto_device(self):
        config = Config()
        config.video.resolution = "720p"

        options = service_options.video_options(config)

        assert options["resolution"] == "1280x720"
        assert options["camera"] == "default"

    def test_display_backend_mapping(self):
        config = Config()
        config.display.backend = "hdmi_cec"

        options = service_options.display_options(config)

        assert options["cec_enabled"] is True
        assert options["ddc_enabled"] is False

    def test_audio_noise_reduction_off(self):
        config = Config()
        config.audio.noise_reduction_level = "off"

        assert service_options.audio_options(config)["noise_reduction"] is False

    def test_calendar_not_configured(self):
        assert service_options.calendar_options(Config()) is None

    def test_calendar_service_account(self, temp_dir):
        key = temp_dir / "sa.json"
        key.write_text(json.dumps({"type": "service_account", "client_email": "x@y"}))
        config = Config()
        config.calendar.providers = ["google"]
        config.calendar.google_credentials_path = str(key)
        config.calendar.google_delegate_email = "room@example.com"
        config.meeting.join_early_minutes = 2

        options = service_options.calendar_options(config)

        assert options["provider"] == "google"
        assert options["credentials"] == {
            "service_account_file": str(key),
            "delegate_email": "room@example.com",
        }
        assert options["auto_join_minutes"] == 2

    def test_calendar_oauth_token_file(self, temp_dir):
        token = temp_dir / "token.json"
        token.write_text(json.dumps({"token": "abc", "refresh_token": "def"}))

        credentials = service_options.google_credentials(str(token))

        assert credentials["oauth_token"]["access_token"] == "abc"
        assert credentials["oauth_token"]["refresh_token"] == "def"

    def test_calendar_unreadable_credentials(self, temp_dir):
        assert service_options.google_credentials(str(temp_dir / "missing.json")) is None

    def test_display_firetv_options(self):
        config = Config()
        config.display.firetv_host = "192.168.1.50"
        config.display.firetv_hdmi_input = "com.example.tv/.HdmiInputService/HW2"

        options = service_options.display_options(config)

        assert options["firetv_host"] == "192.168.1.50"
        assert options["firetv_port"] == 5555
        assert options["firetv_hdmi_input"] == "com.example.tv/.HdmiInputService/HW2"

    def test_display_firetv_ignored_for_other_backends(self):
        config = Config()
        config.display.backend = "hdmi_cec"
        config.display.firetv_host = "192.168.1.50"

        assert "firetv_host" not in service_options.display_options(config)
