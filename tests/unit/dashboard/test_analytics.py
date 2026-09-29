"""
Tests for croom.dashboard.analytics module.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from croom.dashboard.analytics import (
    TimeRange,
    MetricType,
    ReportFormat,
    MeetingRecord,
    UsageStats,
    TrendData,
    Report,
    MeetingTracker,
    AnalyticsEngine,
    ReportGenerator,
    ScheduledReportService,
)


def utcnow():
    return datetime.now(timezone.utc)


def make_record(meeting_id, started_at, minutes=60, platform="google_meet",
                device_id="device-001", room_name="Room A", participants=0):
    return MeetingRecord(
        id=meeting_id,
        device_id=device_id,
        room_name=room_name,
        platform=platform,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=minutes),
        duration_seconds=minutes * 60,
        participant_count=participants,
    )


def make_generator(tracker=None):
    engine = AnalyticsEngine(tracker or MeetingTracker())
    return engine, ReportGenerator(engine)


class TestTimeRange:
    """Tests for TimeRange enum."""

    def test_values(self):
        """Test time range enum values."""
        assert TimeRange.DAY.value == "day"
        assert TimeRange.WEEK.value == "week"
        assert TimeRange.MONTH.value == "month"
        assert TimeRange.QUARTER.value == "quarter"
        assert TimeRange.YEAR.value == "year"


class TestMetricType:
    """Tests for MetricType enum."""

    def test_values(self):
        """Test metric type enum values."""
        assert MetricType.MEETING_COUNT.value == "meeting_count"
        assert MetricType.MEETING_DURATION.value == "meeting_duration"
        assert MetricType.ROOM_UTILIZATION.value == "room_utilization"
        assert MetricType.PARTICIPANT_COUNT.value == "participant_count"


class TestReportFormat:
    """Tests for ReportFormat enum."""

    def test_values(self):
        """Test report format enum values."""
        assert ReportFormat.JSON.value == "json"
        assert ReportFormat.CSV.value == "csv"
        assert ReportFormat.HTML.value == "html"
        assert ReportFormat.PDF.value == "pdf"


class TestMeetingRecord:
    """Tests for MeetingRecord dataclass."""

    def test_creation(self):
        """Test creating a meeting record."""
        record = make_record("meeting-001", utcnow(), minutes=60)

        assert record.id == "meeting-001"
        assert record.platform == "google_meet"
        assert record.duration_seconds == 3600

    def test_to_dict(self):
        """Test serializing a meeting record."""
        record = make_record("meeting-001", utcnow(), minutes=45, platform="teams")

        data = record.to_dict()

        assert data["id"] == "meeting-001"
        assert data["platform"] == "teams"
        assert data["duration_seconds"] == 45 * 60


class TestUsageStats:
    """Tests for UsageStats dataclass."""

    def test_creation(self):
        """Test creating usage stats."""
        now = utcnow()
        stats = UsageStats(
            period_start=now - timedelta(days=30),
            period_end=now,
            total_meetings=100,
            total_duration_hours=100.0,
            avg_meeting_duration_minutes=60.0,
            platform_breakdown={"google_meet": 50, "teams": 30, "zoom": 20},
        )

        assert stats.total_meetings == 100
        assert stats.avg_meeting_duration_minutes == 60.0
        assert len(stats.platform_breakdown) == 3


class TestMeetingTracker:
    """Tests for MeetingTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        tracker = MeetingTracker()
        assert tracker.get_active_meetings() == []
        assert tracker.get_meeting_history() == []

    def test_start_meeting(self):
        """Test starting a meeting."""
        tracker = MeetingTracker()

        record = tracker.start_meeting(
            meeting_id="meeting-001",
            device_id="device-001",
            room_name="Room A",
            platform="google_meet",
        )

        assert record.id == "meeting-001"
        active = tracker.get_active_meetings()
        assert [m.id for m in active] == ["meeting-001"]
        assert active[0].platform == "google_meet"

    def test_end_meeting(self):
        """Test ending a meeting."""
        tracker = MeetingTracker()
        tracker.start_meeting("meeting-001", "device-001", "Room A", "google_meet")

        record = tracker.end_meeting("meeting-001")

        assert record is not None
        assert record.id == "meeting-001"
        assert record.ended_at is not None
        assert tracker.get_active_meetings() == []
        assert len(tracker.get_meeting_history()) == 1

    def test_end_nonexistent_meeting(self):
        """Test ending a meeting that doesn't exist."""
        tracker = MeetingTracker()

        record = tracker.end_meeting("nonexistent")
        assert record is None

    def test_get_active_meetings(self):
        """Test getting active meetings."""
        tracker = MeetingTracker()

        tracker.start_meeting("meeting-001", "device-001", "Room A", "google_meet")
        tracker.start_meeting("meeting-002", "device-002", "Room B", "teams")

        active = tracker.get_active_meetings()
        assert len(active) == 2

    def test_update_meeting_keeps_max_participants(self):
        """Test participant count only grows during a meeting."""
        tracker = MeetingTracker()
        tracker.start_meeting("meeting-001", "device-001", "Room A", "google_meet")

        tracker.update_meeting("meeting-001", participant_count=5)
        record = tracker.update_meeting("meeting-001", participant_count=3)

        assert record.participant_count == 5

    def test_get_meeting_history(self):
        """Test getting meeting history."""
        tracker = MeetingTracker()

        tracker.start_meeting("meeting-001", "device-001", "Room A", "google_meet")
        tracker.end_meeting("meeting-001")

        tracker.start_meeting("meeting-002", "device-001", "Room A", "teams")
        tracker.end_meeting("meeting-002")

        history = tracker.get_meeting_history()
        assert len(history) == 2

    def test_get_meeting_history_filtered(self):
        """Test getting filtered meeting history."""
        tracker = MeetingTracker()

        tracker.start_meeting("meeting-001", "device-001", "Room A", "google_meet")
        tracker.end_meeting("meeting-001")

        tracker.start_meeting("meeting-002", "device-002", "Room B", "teams")
        tracker.end_meeting("meeting-002")

        history = tracker.get_meeting_history(device_id="device-001")
        assert len(history) == 1
        assert history[0].device_id == "device-001"


class TestAnalyticsEngine:
    """Tests for AnalyticsEngine class."""

    def test_init(self):
        """Test engine initialization."""
        tracker = MeetingTracker()
        engine = AnalyticsEngine(tracker)
        assert engine._tracker == tracker

    def test_get_usage_stats_empty(self):
        """Test getting usage stats with no data."""
        engine = AnalyticsEngine(MeetingTracker())

        stats = engine.get_usage_stats(TimeRange.WEEK)

        assert stats.total_meetings == 0
        assert stats.total_duration_hours == 0
        assert stats.avg_meeting_duration_minutes == 0

    def test_get_usage_stats_with_data(self):
        """Test getting usage stats with meeting data."""
        tracker = MeetingTracker()
        now = utcnow()
        for i in range(5):
            tracker._completed_meetings.append(
                make_record(f"meeting-{i}", now - timedelta(days=1, hours=i), minutes=60)
            )

        engine = AnalyticsEngine(tracker)
        stats = engine.get_usage_stats(TimeRange.WEEK)

        assert stats.total_meetings == 5
        assert stats.total_duration_hours == pytest.approx(5.0)
        assert stats.avg_meeting_duration_minutes == pytest.approx(60.0)

    def test_get_usage_stats_excludes_old_meetings(self):
        """Test meetings outside the period are ignored."""
        tracker = MeetingTracker()
        tracker._completed_meetings.append(
            make_record("old", utcnow() - timedelta(days=10))
        )

        stats = AnalyticsEngine(tracker).get_usage_stats(TimeRange.WEEK)

        assert stats.total_meetings == 0

    def test_get_platform_distribution(self):
        """Test getting platform distribution."""
        tracker = MeetingTracker()
        now = utcnow()
        tracker._completed_meetings.append(
            make_record("meeting-1", now - timedelta(hours=2), platform="google_meet")
        )
        tracker._completed_meetings.append(
            make_record("meeting-2", now - timedelta(hours=4), platform="teams")
        )

        engine = AnalyticsEngine(tracker)
        distribution = engine.get_platform_distribution(TimeRange.DAY)

        # Percentages
        assert distribution["google_meet"] == pytest.approx(50.0)
        assert distribution["teams"] == pytest.approx(50.0)

    def test_get_platform_distribution_empty(self):
        """Test distribution with no meetings."""
        engine = AnalyticsEngine(MeetingTracker())
        assert engine.get_platform_distribution(TimeRange.DAY) == {}

    def test_get_peak_hours(self):
        """Test getting peak hours."""
        tracker = MeetingTracker()
        day = utcnow() - timedelta(days=1)
        tracker._completed_meetings.append(
            make_record("meeting-1", day.replace(hour=9, minute=0))
        )
        tracker._completed_meetings.append(
            make_record("meeting-2", day.replace(hour=9, minute=30), platform="teams")
        )

        engine = AnalyticsEngine(tracker)
        peak_hours = engine.get_peak_hours(TimeRange.WEEK)

        assert len(peak_hours) == 24
        assert peak_hours[9] == 2  # Both meetings started at 9

    def test_get_trend(self):
        """Test getting trend data."""
        tracker = MeetingTracker()
        now = utcnow()
        for i in range(7):
            tracker._completed_meetings.append(
                make_record(f"meeting-{i}", now - timedelta(days=i, hours=1))
            )

        engine = AnalyticsEngine(tracker)
        trend = engine.get_trend(MetricType.MEETING_COUNT, TimeRange.WEEK)

        assert len(trend) > 0
        assert all(isinstance(t, TrendData) for t in trend)
        assert sum(t.value for t in trend) == 7


class TestReportGenerator:
    """Tests for ReportGenerator class."""

    def test_init(self):
        """Test generator initialization."""
        engine, generator = make_generator()
        assert generator._analytics == engine

    async def test_generate_usage_report(self):
        """Test generating usage report."""
        _, generator = make_generator()

        report = await generator.generate_usage_report(
            time_range=TimeRange.WEEK,
            format=ReportFormat.JSON,
        )

        assert report is not None
        assert report.name == "Usage Report"
        assert report.format == ReportFormat.JSON
        assert generator.get_report(report.id) is report
        assert set(report.data) == {"summary", "trends", "platform_distribution", "peak_hours"}

    def test_export_to_json(self):
        """Test exporting report to JSON."""
        _, generator = make_generator()
        report = Report(
            id="report-001",
            name="Test Report",
            report_type="usage",
            format=ReportFormat.JSON,
            data={"total_meetings": 10},
        )

        json_output = generator.export_to_json(report)
        assert json.loads(json_output) == {"total_meetings": 10}

    async def test_export_to_csv(self):
        """Test exporting report to CSV."""
        tracker = MeetingTracker()
        tracker._completed_meetings.append(make_record("m1", utcnow() - timedelta(hours=3)))
        _, generator = make_generator(tracker)
        report = await generator.generate_usage_report(TimeRange.WEEK, ReportFormat.CSV)

        csv_output = generator.export_to_csv(report)

        assert "Summary" in csv_output
        assert "total_meetings,1" in csv_output
        assert "google_meet,100.0%" in csv_output

    async def test_export_to_html(self):
        """Test exporting report to HTML."""
        _, generator = make_generator()
        report = await generator.generate_usage_report(
            TimeRange.WEEK, ReportFormat.HTML, name="Test Report"
        )

        html_output = generator.export_to_html(report)

        assert "<html>" in html_output
        assert "<title>Test Report</title>" in html_output


class TestScheduledReportService:
    """Tests for ScheduledReportService class."""

    def test_init(self):
        """Test service initialization."""
        _, generator = make_generator()
        service = ScheduledReportService(generator)

        assert service._generator == generator
        assert service._schedules == {}

    def test_add_schedule(self):
        """Test adding report schedule."""
        _, generator = make_generator()
        service = ScheduledReportService(generator)

        service.add_schedule(
            schedule_id="weekly",
            report_type="usage",
            time_range=TimeRange.WEEK,
            format=ReportFormat.JSON,
            interval_hours=24 * 7,
            name="Weekly Report",
        )

        assert service._schedules["weekly"]["name"] == "Weekly Report"
        assert service._schedules["weekly"]["last_run"] is None

    def test_remove_schedule(self):
        """Test removing report schedule."""
        _, generator = make_generator()
        service = ScheduledReportService(generator)
        service.add_schedule("weekly", "usage", TimeRange.WEEK, ReportFormat.JSON)

        assert service.remove_schedule("weekly") is True
        assert "weekly" not in service._schedules
        assert service.remove_schedule("weekly") is False

    async def test_run_scheduled_report_notifies(self):
        """Test a scheduled run generates a report and notifies callbacks."""
        _, generator = make_generator()
        service = ScheduledReportService(generator)
        service.add_schedule("monthly", "usage", TimeRange.MONTH, ReportFormat.HTML, name="Monthly")
        received = []
        service.on_report_generated(received.append)

        await service._run_scheduled_report("monthly", service._schedules["monthly"])

        assert len(received) == 1
        assert received[0].name == "Monthly"
        assert service._schedules["monthly"]["last_run"] is not None
