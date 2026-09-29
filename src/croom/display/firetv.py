"""
Fire TV controller for Croom.

Controls TVs running Fire OS (Xiaomi, Toshiba, Insignia Fire TV Edition...)
over the network with ADB, for machines without HDMI-CEC such as a Mac
connected through a USB-C to HDMI adapter.

Setup on the TV: Settings > My Fire TV > About, click the device name 7
times, then Developer options > ADB debugging. Accept the prompt shown on
the TV the first time Croom connects ("Always allow from this computer").
"""

import asyncio
import logging
import re
import shutil
from typing import List, Optional, Tuple
from urllib.parse import quote

from croom.display.cec import CECPowerStatus

logger = logging.getLogger(__name__)

# Fire OS TV inputs look like "<package>/<service>/HW<n>"
HDMI_INPUT_PATTERN = re.compile(r"[\w.]+/[\w.$]+/HW\d+")


class FireTVController:
    """
    ADB-based TV control, exposing the same interface as CECController.
    """

    def __init__(
        self,
        host: str,
        port: int = 5555,
        hdmi_input: str = "",
        adb_path: str = "adb",
        timeout: float = 10.0,
    ):
        """
        Args:
            host: TV IP address (reserve it in the router's DHCP)
            port: ADB port, 5555 on Fire OS
            hdmi_input: TV input id of the HDMI port the room computer uses,
                e.g. "com.example.tv/.HdmiInputService/HW2" (see list_hdmi_inputs)
            adb_path: adb executable (brew install android-platform-tools)
            timeout: Seconds before an adb command is abandoned
        """
        self._serial = f"{host}:{port}"
        self._hdmi_input = hdmi_input
        self._adb = adb_path
        self._timeout = timeout
        self._connected = False

    async def _run_adb(self, *args: str) -> Tuple[int, str]:
        """Run adb; returns (returncode, output), -1 if adb is missing or hangs."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._adb, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            logger.error("adb not found. Install: brew install android-platform-tools")
            return -1, ""

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning(f"adb {' '.join(args)} timed out")
            return -1, ""

        return proc.returncode, stdout.decode(errors="ignore").strip()

    async def _connect(self) -> bool:
        rc, out = await self._run_adb("connect", self._serial)
        if rc != 0 or not ("connected to" in out or "already connected" in out):
            logger.warning(f"Fire TV {self._serial} unreachable: {out}")
            self._connected = False
            return False

        _, state = await self._run_adb("-s", self._serial, "get-state")
        if state != "device":
            if "unauthorized" in state or "unauthorized" in out:
                logger.warning("Fire TV refused ADB: accept the prompt on the TV screen")
            self._connected = False
            return False

        self._connected = True
        return True

    async def _shell(self, *command: str) -> Optional[str]:
        """Run a shell command on the TV, reconnecting once if the link dropped."""
        for attempt in range(2):
            if not self._connected and not await self._connect():
                return None
            rc, out = await self._run_adb("-s", self._serial, "shell", *command)
            if rc == 0:
                return out
            # The TV may have slept or changed state; reconnect and retry once
            self._connected = False
            if attempt == 0:
                logger.debug(f"adb shell failed ({out}), reconnecting")
        return None

    async def _keyevent(self, key: str) -> bool:
        return await self._shell("input", "keyevent", key) is not None

    async def initialize(self) -> bool:
        """Connect to the TV; False if adb is missing or the TV refuses."""
        if shutil.which(self._adb) is None:
            logger.warning("adb not found. Install: brew install android-platform-tools")
            return False
        if not await self._connect():
            return False
        logger.info(f"Fire TV control enabled ({self._serial})")
        return True

    @property
    def is_available(self) -> bool:
        return self._connected

    async def power_on_tv(self) -> bool:
        return await self._keyevent("KEYCODE_WAKEUP")

    async def power_off_tv(self) -> bool:
        return await self._keyevent("KEYCODE_SLEEP")

    async def get_tv_power_status(self) -> CECPowerStatus:
        out = await self._shell("dumpsys", "power")
        if out is None:
            return CECPowerStatus.UNKNOWN
        match = re.search(r"mWakefulness=(\w+)", out)
        if not match:
            return CECPowerStatus.UNKNOWN
        return CECPowerStatus.ON if match.group(1) == "Awake" else CECPowerStatus.STANDBY

    async def set_active_source(self) -> bool:
        """Switch the TV to the configured HDMI input."""
        if not self._hdmi_input:
            logger.warning("display.firetv_hdmi_input not set, cannot switch input")
            return False
        uri = "content://android.media.tv/passthrough/" + quote(self._hdmi_input, safe="")
        out = await self._shell(
            "am", "start", "-a", "android.intent.action.VIEW", "-d", uri
        )
        return out is not None and "Error" not in out

    async def list_hdmi_inputs(self) -> List[str]:
        """HDMI input ids declared by the TV, to fill display.firetv_hdmi_input."""
        out = await self._shell("dumpsys", "tv_input")
        if not out:
            return []
        return sorted(set(HDMI_INPUT_PATTERN.findall(out)))

    async def volume_up(self) -> bool:
        return await self._keyevent("KEYCODE_VOLUME_UP")

    async def volume_down(self) -> bool:
        return await self._keyevent("KEYCODE_VOLUME_DOWN")

    async def mute(self) -> bool:
        return await self._keyevent("KEYCODE_VOLUME_MUTE")
