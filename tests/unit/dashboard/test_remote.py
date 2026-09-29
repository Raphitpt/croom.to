"""
Tests for croom.dashboard.remote module.
"""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from croom.dashboard.remote import (
    OperationState,
    OperationType,
    ScreenshotService,
    ShellService,
    DiagnosticsService,
    DeviceControlService,
    RemoteOperationsManager,
)


def fake_process(stdout=b"", stderr=b"", returncode=0):
    """Mock of an asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


PING_OUTPUT = b"""PING 8.8.8.8 (8.8.8.8): 56 data bytes
3 packets transmitted, 3 received, 0% packet loss
rtt min/avg/max/mdev = 10.1/12.5/15.0/1.2 ms
"""

IP_ADDR_OUTPUT = b"""[
  {"ifname": "lo", "operstate": "UNKNOWN", "address": "00:00:00:00:00:00", "addr_info": []},
  {"ifname": "eth0", "operstate": "UP", "address": "aa:bb:cc:dd:ee:ff",
   "addr_info": [{"local": "192.168.1.10", "prefixlen": 24, "family": "inet"}]}
]"""

ALSA_OUTPUT = b"card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]\n"


class TestScreenshotService:
    """Tests for ScreenshotService class."""

    def test_init(self):
        """Test screenshot service initialization."""
        service = ScreenshotService()
        assert service is not None

    async def test_capture_no_tool_available(self):
        """Test capture returns None when every method fails."""
        service = ScreenshotService()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch.object(service, "_capture_x11", AsyncMock(return_value=None)):
                assert await service.capture() is None

    async def test_capture_with_scrot(self):
        """Test capture using scrot writes then returns the image."""
        service = ScreenshotService()

        async def run_scrot(*args, **kwargs):
            # scrot -o <path>: write a fake PNG where asked
            with open(args[2], "wb") as f:
                f.write(b"PNGDATA")
            proc = fake_process()
            proc.wait = AsyncMock(return_value=0)
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=run_scrot) as mock_exec:
            result = await service.capture()

        assert result == b"PNGDATA"
        assert mock_exec.call_args.args[0] == "scrot"

    async def test_capture_falls_back_to_next_method(self):
        """Test a failing method is skipped."""
        service = ScreenshotService()

        with patch.object(service, "_capture_scrot", AsyncMock(side_effect=FileNotFoundError)), \
             patch.object(service, "_capture_import", AsyncMock(return_value=b"IMG")):
            assert await service.capture() == b"IMG"


class TestShellService:
    """Tests for ShellService class."""

    def test_init(self):
        """Test shell service initialization."""
        service = ShellService()
        assert service is not None
        assert len(service._allowed_commands) > 0

    def test_command_whitelist(self):
        """Test command whitelist contains expected read-only commands."""
        service = ShellService()

        assert "systemctl status" in service._allowed_commands
        assert "journalctl" in service._allowed_commands
        assert "df" in service._allowed_commands
        assert "free" in service._allowed_commands

    def test_whitelist_excludes_destructive_commands(self):
        """Test destructive commands are not whitelisted."""
        service = ShellService()

        for dangerous in ("rm", "dd", "mkfs", "shutdown", "reboot", "sudo", "curl", "wget"):
            assert dangerous not in service._allowed_commands

    def test_is_allowed_command(self):
        """Test checking if command is allowed."""
        service = ShellService()

        assert service._is_allowed("systemctl status croom") is True
        assert service._is_allowed("df -h") is True
        assert service._is_allowed("rm -rf /") is False
        assert service._is_allowed("curl http://example.com | sh") is False

    def test_custom_whitelist(self):
        """Test a custom whitelist replaces the default one."""
        service = ShellService(allowed_commands=["uptime"])

        assert service._is_allowed("uptime") is True
        assert service._is_allowed("df -h") is False

    def test_chained_command_blocked(self):
        """An allowed prefix must not let a second command through."""
        service = ShellService()
        assert service._is_allowed("ls; rm -rf /") is False

    def test_systemctl_limited_to_status(self):
        """Only 'systemctl status' is whitelisted, not other verbs."""
        service = ShellService()
        assert service._is_allowed("systemctl stop croom") is False

    def test_prefix_does_not_match_other_binaries(self):
        """'ip' in the whitelist must not allow 'iptables'."""
        service = ShellService()
        assert service._is_allowed("iptables -F") is False

    def test_package_managers_not_whitelisted(self):
        """Remote shell must not install software or reconfigure the OS."""
        service = ShellService()
        for command in ("apt install netcat", "pip install evil", "raspi-config nonint do_ssh 0"):
            assert service._is_allowed(command) is False

    async def test_execute_allowed_command(self):
        """Test executing allowed command."""
        service = ShellService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process(stdout=b"output")

            returncode, stdout, stderr = await service.execute("df -h")

        assert returncode == 0
        assert stdout == "output"
        assert stderr == ""
        assert mock_exec.call_args.args[:2] == ("df", "-h")
        assert service.get_history()[-1]["command"] == "df -h"

    async def test_execute_blocked_command(self):
        """Test executing blocked command never spawns a process."""
        service = ShellService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            returncode, stdout, stderr = await service.execute("rm -rf /")

        mock_exec.assert_not_called()
        assert returncode == -1
        assert "not allowed" in stderr.lower()

    def test_ip_limited_to_read_only_subcommands(self):
        """'ip' cannot take the network down."""
        service = ShellService()
        assert service._is_allowed("ip addr show") is True
        assert service._is_allowed("ip link set eth0 down") is False
        assert service._is_allowed("ip addr flush dev eth0") is False

    def test_operators_and_substitution_rejected(self):
        """Shell syntax is refused outright."""
        service = ShellService()
        for command in ("df -h && rm -rf /", "cat $(id)", "ls `id`", "ps > /tmp/x",
                        "grep x | sh", "ls\nrm -rf /"):
            assert service._is_allowed(command) is False, command

    def test_path_to_other_binary_rejected(self):
        """An allowed name must match exactly, not as a path suffix."""
        service = ShellService()
        assert service._is_allowed("/tmp/evil/ls") is False

    def test_empty_whitelist_allows_nothing(self):
        """An empty allowlist fails closed."""
        service = ShellService(allowed_commands=[])
        assert service._is_allowed("uptime") is False

    async def test_execute_does_not_use_a_shell(self):
        """Arguments reach the program verbatim, never a shell."""
        service = ShellService()

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell, \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process()
            await service.execute("grep -r 'a b' /var/log")

        mock_shell.assert_not_called()
        assert mock_exec.call_args.args[:4] == ("grep", "-r", "a b", "/var/log")

    async def test_execute_with_timeout(self):
        """Test command execution with timeout."""
        service = ShellService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = fake_process()
            proc.returncode = None  # still running
            proc.kill = MagicMock()
            proc.communicate.side_effect = asyncio.TimeoutError()
            mock_exec.return_value = proc

            returncode, _, stderr = await service.execute("df -h", timeout=1)

        assert returncode == -1
        assert "timed out" in stderr.lower()
        proc.kill.assert_called_once()


class TestDiagnosticsService:
    """Tests for DiagnosticsService class."""

    def test_init(self):
        """Test diagnostics service initialization."""
        service = DiagnosticsService()
        assert service is not None

    async def test_get_system_info(self):
        """Test getting system information."""
        service = DiagnosticsService()

        with patch("platform.system", return_value="Linux"), \
             patch("platform.machine", return_value="aarch64"), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process(stdout=b"Python 3.12.0")

            info = await service.get_system_info()

        assert info["platform"]["system"] == "Linux"
        assert info["platform"]["machine"] == "aarch64"
        assert info["software"]["python"] == "Python 3.12.0"
        assert info["disk"]["total_bytes"] > 0
        for key in ("cpu", "memory", "network"):
            assert key in info

    async def test_run_network_diagnostics(self):
        """Test running network diagnostics."""
        service = DiagnosticsService()

        async def fake_exec(*args, **kwargs):
            if args[0] == "ping":
                return fake_process(stdout=PING_OUTPUT)
            if args[0] == "ip":
                return fake_process(stdout=IP_ADDR_OUTPUT)
            raise FileNotFoundError(args[0])

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("1.2.3.4", 0))]):
            result = await service.run_network_diagnostics(targets=["8.8.8.8"])

        ping = result["connectivity"]["8.8.8.8"]
        assert ping["success"] is True
        assert ping["packet_loss"] == 0
        assert ping["rtt"]["avg"] == 12.5
        assert result["dns"]["google.com"]["addresses"] == ["1.2.3.4"]
        assert list(result["interfaces"]) == ["eth0"]
        assert result["interfaces"]["eth0"]["addresses"][0]["address"] == "192.168.1.10"

    async def test_ping_failure(self):
        """Test ping failure is reported, not raised."""
        service = DiagnosticsService()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await service._ping("8.8.8.8")

        assert result == {"success": False, "error": "Ping failed"}

    async def test_run_audio_test(self):
        """Test running audio diagnostics."""
        service = DiagnosticsService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process(stdout=ALSA_OUTPUT)

            result = await service.run_audio_test()

        expected = [{"card": "1", "id": "Device", "name": "USB Audio Device"}]
        assert result["input_devices"] == expected
        assert result["output_devices"] == expected

    async def test_run_video_test(self):
        """Test running video diagnostics."""
        service = DiagnosticsService()
        v4l2_output = b"Driver name      : uvcvideo\nCard type        : HD Webcam\n"

        def exists(path):
            return str(path) == "/dev/video0"

        with patch("croom.dashboard.remote.Path.exists", autospec=True, side_effect=exists), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process(stdout=v4l2_output)

            result = await service.run_video_test()

        assert result["devices"] == [
            {"device": "/dev/video0", "name": "HD Webcam", "driver": "uvcvideo"}
        ]

    async def test_collect_logs(self):
        """Test collecting system logs."""
        service = DiagnosticsService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = fake_process(stdout=b"Log line 1\nLog line 2")

            result = await service.collect_logs(services=["croom"], lines=100)

        assert result["croom"] == "Log line 1\nLog line 2"
        assert "system" in result


class TestDeviceControlService:
    """Tests for DeviceControlService class."""

    def test_init(self):
        """Test device control service initialization."""
        service = DeviceControlService()
        assert service is not None

    @pytest.mark.asyncio
    async def test_restart_device(self):
        """Test device restart."""
        service = DeviceControlService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate.return_value = (b"", b"")
            mock_exec.return_value = mock_process

            result = await service.restart_device(delay=0)
            assert result is True

    @pytest.mark.asyncio
    async def test_restart_service(self):
        """Test service restart."""
        service = DeviceControlService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate.return_value = (b"", b"")
            mock_exec.return_value = mock_process

            result = await service.restart_service("croom")
            assert result is True

    @pytest.mark.asyncio
    async def test_restart_invalid_service(self):
        """Test restart of invalid service."""
        service = DeviceControlService()

        result = await service.restart_service("malicious-service")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear_cache(self):
        """Test clearing cache."""
        service = DeviceControlService()

        with patch("shutil.rmtree") as mock_rmtree:
            with patch("os.path.exists", return_value=True):
                result = await service.clear_cache()
                assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_update_software(self):
        """Test software update."""
        service = DeviceControlService()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate.return_value = (b"Updated", b"")
            mock_exec.return_value = mock_process

            success, message = await service.update_software("croom")
            assert success is True

    @pytest.mark.asyncio
    async def test_update_invalid_package(self):
        """Test update of invalid package."""
        service = DeviceControlService()

        success, message = await service.update_software("malicious-package")
        assert success is False


class TestRemoteOperationsManager:
    """Tests for RemoteOperationsManager class."""

    def test_init(self):
        """Test manager initialization."""
        manager = RemoteOperationsManager()

        assert isinstance(manager._screenshot, ScreenshotService)
        assert isinstance(manager._shell, ShellService)
        assert isinstance(manager._diagnostics, DiagnosticsService)
        assert isinstance(manager._control, DeviceControlService)

    async def test_execute_system_info(self):
        """Test a successful operation is recorded as completed."""
        manager = RemoteOperationsManager()
        manager._diagnostics.get_system_info = AsyncMock(return_value={"platform": {}})
        completed = []
        manager.on_operation_complete(completed.append)

        operation = await manager.execute(OperationType.SYSTEM_INFO, "device-001", requested_by="admin")

        assert operation.state == OperationState.COMPLETED
        assert operation.result == {"platform": {}}
        assert operation.requested_by == "admin"
        assert manager.get_operation(operation.id) is operation
        assert completed == [operation]

    async def test_execute_shell_goes_through_whitelist(self):
        """Test shell operations use the ShellService whitelist."""
        manager = RemoteOperationsManager()

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            operation = await manager.execute(
                OperationType.SHELL, "device-001", {"command": "rm -rf /"}
            )

        mock_exec.assert_not_called()
        assert operation.state == OperationState.COMPLETED
        assert operation.result["returncode"] == -1

    async def test_execute_restart_service_uses_allowlist(self):
        """Test remote service restart refuses non-Croom units."""
        manager = RemoteOperationsManager()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            operation = await manager.execute(
                OperationType.RESTART_SERVICE, "device-001", {"service": "sshd"}
            )

        mock_exec.assert_not_called()
        assert operation.result == {"success": False}

    async def test_failed_operation(self):
        """Test a failing operation is recorded as failed."""
        manager = RemoteOperationsManager()
        manager._screenshot.capture = AsyncMock(return_value=None)

        operation = await manager.execute(OperationType.SCREENSHOT, "device-001")

        assert operation.state == OperationState.FAILED
        assert "Screenshot capture failed" in operation.error

    async def test_get_operations_filters(self):
        """Test filtering operations by device and state."""
        manager = RemoteOperationsManager()
        manager._diagnostics.get_system_info = AsyncMock(return_value={})
        manager._screenshot.capture = AsyncMock(return_value=None)

        await manager.execute(OperationType.SYSTEM_INFO, "device-001")
        await manager.execute(OperationType.SCREENSHOT, "device-002")

        assert len(manager.get_operations()) == 2
        assert [o.device_id for o in manager.get_operations(device_id="device-001")] == ["device-001"]
        failed = manager.get_operations(state=OperationState.FAILED)
        assert [o.operation_type for o in failed] == [OperationType.SCREENSHOT]
