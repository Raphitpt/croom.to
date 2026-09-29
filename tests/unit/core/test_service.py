"""
Tests for croom.core.service module.
"""

import pytest

from croom.core.service import Service, ServiceManager, ServiceState


class FakeService(Service):
    def __init__(self, name, init_ok=True, start_error=None):
        super().__init__(name)
        self.init_ok = init_ok
        self.start_error = start_error
        self.calls = []

    async def initialize(self):
        self.calls.append("initialize")
        return self.init_ok

    async def start(self):
        self.calls.append("start")
        if self.start_error:
            raise self.start_error

    async def stop(self):
        self.calls.append("stop")


class ShadowingService(FakeService):
    """Redefines ``state`` like DisplayService or DashboardClient do."""

    @property
    def state(self):
        return "domain-state"


class TestServiceManager:
    async def test_initialize_runs_before_start(self):
        manager = ServiceManager()
        svc = FakeService("a")
        manager.register(svc)

        assert await manager.start_all() is True
        assert svc.calls == ["initialize", "start"]
        assert svc.service_state == ServiceState.RUNNING

    async def test_optional_service_failure_keeps_others_running(self):
        manager = ServiceManager()
        broken = FakeService("display", init_ok=False)
        ok = FakeService("meeting")
        manager.register(broken, required=False)
        manager.register(ok)

        assert await manager.start_all() is True
        assert broken.service_state == ServiceState.ERROR
        assert "start" not in broken.calls
        assert ok.service_state == ServiceState.RUNNING

    async def test_optional_service_start_exception_is_contained(self):
        manager = ServiceManager()
        broken = FakeService("dashboard", start_error=RuntimeError("offline"))
        manager.register(broken, required=False)

        assert await manager.start_all() is True
        assert broken.service_state == ServiceState.ERROR
        assert broken.get_status().error == "offline"

    async def test_required_service_failure_stops_everything(self):
        manager = ServiceManager()
        first = FakeService("audio")
        required = FakeService("meeting", init_ok=False)
        manager.register(first)
        manager.register(required)

        assert await manager.start_all() is False
        assert first.calls == ["initialize", "start", "stop"]

    async def test_stop_all_uses_lifecycle_state_not_domain_state(self):
        manager = ServiceManager()
        svc = ShadowingService("display")
        manager.register(svc)

        await manager.start_all()
        await manager.stop_all()

        assert svc.calls[-1] == "stop"
        assert svc.service_state == ServiceState.STOPPED
        assert svc.state == "domain-state"

    async def test_dependencies_start_first(self):
        manager = ServiceManager()
        order = []

        class Recorder(FakeService):
            async def start(self):
                order.append(self.name)

        manager.register(Recorder("audio"))
        manager.register(Recorder("video"))
        manager.register(Recorder("meeting"), dependencies=["audio", "video"])

        await manager.start_all()
        assert order.index("meeting") > order.index("audio")
        assert order.index("meeting") > order.index("video")
