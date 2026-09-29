"""
Tests for croom.display.firetv module.
"""

from unittest.mock import AsyncMock, patch

import pytest

from croom.display.cec import CECPowerStatus
from croom.display.firetv import FireTVController
from croom.display.service import DisplayService, DisplayState


class FakeADB:
    """Scripted adb: maps an argument tuple prefix to (returncode, output)."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = {
            ("connect",): (0, "connected to 192.168.1.50:5555"),
            ("-s", "192.168.1.50:5555", "get-state"): (0, "device"),
        }
        self.replies.update(replies or {})

    async def __call__(self, *args):
        self.calls.append(args)
        for prefix, reply in self.replies.items():
            if args[:len(prefix)] == prefix:
                return reply
        return (0, "")

    def shell_calls(self):
        return [c[3:] for c in self.calls if c[:1] == ("-s",) and "shell" in c]


@pytest.fixture
def adb():
    return FakeADB()


@pytest.fixture
def tv(adb):
    controller = FireTVController("192.168.1.50", hdmi_input="com.example.tv/.HdmiInputService/HW2")
    controller._run_adb = adb
    return controller


class TestFireTVController:
    async def test_initialize_connects(self, tv):
        with patch("croom.display.firetv.shutil.which", return_value="/opt/homebrew/bin/adb"):
            assert await tv.initialize() is True
        assert tv.is_available

    async def test_initialize_without_adb(self, tv):
        with patch("croom.display.firetv.shutil.which", return_value=None):
            assert await tv.initialize() is False

    async def test_initialize_unauthorized(self, tv, adb):
        adb.replies[("-s", "192.168.1.50:5555", "get-state")] = (1, "error: device unauthorized")
        with patch("croom.display.firetv.shutil.which", return_value="/opt/homebrew/bin/adb"):
            assert await tv.initialize() is False

    async def test_power_keys(self, tv, adb):
        assert await tv.power_on_tv()
        assert await tv.power_off_tv()
        assert adb.shell_calls() == [
            ("input", "keyevent", "KEYCODE_WAKEUP"),
            ("input", "keyevent", "KEYCODE_SLEEP"),
        ]

    @pytest.mark.parametrize("wakefulness, expected", [
        ("Awake", CECPowerStatus.ON),
        ("Asleep", CECPowerStatus.STANDBY),
        ("Dozing", CECPowerStatus.STANDBY),
    ])
    async def test_power_status(self, tv, adb, wakefulness, expected):
        adb.replies[("-s", "192.168.1.50:5555", "shell", "dumpsys", "power")] = (
            0, f"Power Manager State:\n  mWakefulness={wakefulness}\n")
        assert await tv.get_tv_power_status() == expected

    async def test_power_status_unreachable(self, tv, adb):
        adb.replies[("connect",)] = (1, "failed to connect to 192.168.1.50:5555")
        assert await tv.get_tv_power_status() == CECPowerStatus.UNKNOWN

    async def test_switch_to_hdmi_input(self, tv, adb):
        assert await tv.set_active_source()
        (call,) = adb.shell_calls()
        assert call[:4] == ("am", "start", "-a", "android.intent.action.VIEW")
        assert call[-1] == (
            "content://android.media.tv/passthrough/"
            "com.example.tv%2F.HdmiInputService%2FHW2"
        )

    async def test_switch_without_input_configured(self, adb):
        tv = FireTVController("192.168.1.50")
        tv._run_adb = adb
        assert await tv.set_active_source() is False
        assert adb.shell_calls() == []

    async def test_reconnects_once_after_failure(self, tv, adb):
        results = iter([(1, "error: closed"), (0, "")])
        original = adb.__call__

        async def flaky(*args):
            if "shell" in args:
                adb.calls.append(args)
                return next(results)
            return await original(*args)

        tv._run_adb = flaky
        assert await tv.power_on_tv() is True
        connects = [c for c in adb.calls if c[0] == "connect"]
        assert len(connects) == 2

    async def test_list_hdmi_inputs(self, tv, adb):
        adb.replies[("-s", "192.168.1.50:5555", "shell", "dumpsys", "tv_input")] = (0, """
  inputId: com.amazon.tv.inputpreference.service/com.amazon.tv.HdmiInputService/HW2
  inputId: com.amazon.tv.inputpreference.service/com.amazon.tv.HdmiInputService/HW3
  inputId: com.amazon.tv.inputpreference.service/com.amazon.tv.HdmiInputService/HW2
""")
        assert await tv.list_hdmi_inputs() == [
            "com.amazon.tv.inputpreference.service/com.amazon.tv.HdmiInputService/HW2",
            "com.amazon.tv.inputpreference.service/com.amazon.tv.HdmiInputService/HW3",
        ]


class TestDisplayServiceFireTV:
    async def test_firetv_preferred_when_configured(self):
        service = DisplayService({"firetv_host": "192.168.1.50", "cec_enabled": True})
        with patch.object(FireTVController, "initialize", AsyncMock(return_value=True)), \
             patch.object(FireTVController, "get_tv_power_status",
                          AsyncMock(return_value=CECPowerStatus.STANDBY)), \
             patch("croom.display.service.CECController") as cec, \
             patch.object(DisplayService, "_detect_displays", AsyncMock()):
            assert await service.initialize()

        assert service.control_method == "firetv"
        assert service.state == DisplayState.STANDBY
        cec.assert_not_called()

    async def test_falls_back_when_tv_unreachable(self):
        service = DisplayService({"firetv_host": "192.168.1.50", "cec_enabled": False,
                                  "ddc_enabled": False})
        with patch.object(FireTVController, "initialize", AsyncMock(return_value=False)), \
             patch.object(DisplayService, "_detect_displays", AsyncMock()):
            assert await service.initialize()

        assert service.control_method is None

    async def test_power_on_wakes_and_switches_input(self):
        service = DisplayService({"firetv_host": "192.168.1.50"})
        tv = AsyncMock()
        tv.power_on_tv.return_value = True
        service._firetv = tv
        service._control_method = "firetv"

        with patch("croom.display.service.asyncio.sleep", AsyncMock()):
            assert await service.power_on()

        tv.power_on_tv.assert_awaited_once()
        tv.set_active_source.assert_awaited_once()
        assert service.state == DisplayState.ON

    async def test_power_off_puts_tv_in_standby(self):
        service = DisplayService({"firetv_host": "192.168.1.50"})
        tv = AsyncMock()
        tv.power_off_tv.return_value = True
        service._firetv = tv
        service._control_method = "firetv"

        assert await service.power_off()
        assert service.state == DisplayState.STANDBY
