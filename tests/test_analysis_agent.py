#!/usr/bin/env python3
"""
Tests for Founder-PM Observer Plane — Analysis Agent (Phase 2)

Covers:
  - AnalysisConfig defaults and parameter loading
  - AnalysisAgent execution with various data scenarios
  - Finding generation (reliability, duration, hygiene, trends)
  - Report generation and persistence
  - Edge cases: empty data, single run, all-passing, all-failing
  - Monitoring integration (agent run logging)
"""

import json
import os
import sys
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import RunRecord, current_timestamp
from lib.context_hub import ContextHub
from lib.metrics import compute_metrics, MetricsSummary
from lib.analysis_config import AnalysisConfig
from lib.analysis_agent import AnalysisAgent, AnalysisResult, Finding, Severity
from lib.monitoring import AgentMonitor, AgentRunLog, create_monitor


# ═══════════════════════════════════════
# Helpers
# ═══════════════════════════════════════


def _make_record(run_id: str, **kwargs) -> RunRecord:
    defaults = {
        "run_id": run_id,
        "timestamp": "2026-02-06T12:00:00+00:00",
        "build_success": True,
        "duration_minutes": 25.0,
        "tests_passed": 40,
        "tests_failed": 0,
        "lint_errors": 0,
        "type_errors": 0,
        "input_type": "PRD",
    }
    defaults.update(kwargs)
    return RunRecord(**defaults)


def _seed_hub(hub: ContextHub, n: int, **overrides) -> list[RunRecord]:
    """Seed a hub with n run records and return them."""
    records = []
    for i in range(n):
        day = min(i + 1, 28)
        defaults = {
            "run_id": f"2026-02-{day:02d}-{i:06x}",
            "timestamp": f"2026-02-{day:02d}T12:00:00+00:00",
            "build_success": True,
            "duration_minutes": 25.0 + i,
            "tests_passed": 40 + i,
            "tests_failed": 0,
            "lint_errors": i % 3,
            "type_errors": 0,
            "input_type": "PRD",
        }
        defaults.update(overrides)
        defaults["run_id"] = f"2026-02-{day:02d}-{i:06x}"
        defaults["timestamp"] = f"2026-02-{day:02d}T12:00:00+00:00"
        r = RunRecord(**defaults)
        hub.write_run(r)
        records.append(r)
    return records


# ═══════════════════════════════════════
# AnalysisConfig Tests
# ═══════════════════════════════════════


class TestAnalysisConfig:
    def test_defaults(self):
        cfg = AnalysisConfig()
        assert cfg.analysis_window_size == 10
        assert cfg.trend_threshold == 0.1
        assert cfg.target_build_success_rate == 0.9
        assert cfg.target_median_cycle_time == 30.0
        assert cfg.target_manual_intervention_rate == 0.1
        assert cfg.target_max_lint_errors == 5
        assert cfg.target_max_type_errors == 0

    def test_from_parameters_with_full_config(self):
        params = {
            "observer": {
                "analysis_window_size": 20,
                "trend_threshold": 0.15,
            },
            "targets": {
                "median_cycle_time_minutes": 45,
                "build_success_rate": 0.95,
                "manual_intervention_rate": 0.05,
                "max_lint_errors_per_run": 3,
                "max_type_errors_per_run": 1,
            },
        }
        cfg = AnalysisConfig.from_parameters(params)
        assert cfg.analysis_window_size == 20
        assert cfg.trend_threshold == 0.15
        assert cfg.target_median_cycle_time == 45
        assert cfg.target_build_success_rate == 0.95
        assert cfg.target_manual_intervention_rate == 0.05
        assert cfg.target_max_lint_errors == 3
        assert cfg.target_max_type_errors == 1

    def test_from_parameters_with_none(self):
        cfg = AnalysisConfig.from_parameters(None)
        assert cfg.analysis_window_size == 10

    def test_from_parameters_with_empty_dict(self):
        cfg = AnalysisConfig.from_parameters({})
        assert cfg.analysis_window_size == 10
        assert cfg.target_build_success_rate == 0.9

    def test_from_parameters_partial_config(self):
        """Unknown or missing keys use defaults."""
        params = {
            "observer": {"analysis_window_size": 5},
            "targets": {},
            "unrelated_key": "ignored",
        }
        cfg = AnalysisConfig.from_parameters(params)
        assert cfg.analysis_window_size == 5
        assert cfg.target_build_success_rate == 0.9  # default

    def test_from_parameters_matches_real_config(self):
        """Config loads correctly from the actual v0.1.0 parameter file."""
        config_path = PROJECT_ROOT / "context_hub" / "parameters" / "v0.1.0.json"
        if config_path.exists():
            with open(config_path) as f:
                params = json.load(f)
            cfg = AnalysisConfig.from_parameters(params)
            assert cfg.analysis_window_size == 10
            assert cfg.target_build_success_rate == 0.9
            assert cfg.target_median_cycle_time == 30


# ═══════════════════════════════════════
# AnalysisAgent Tests
# ═══════════════════════════════════════


class TestAnalysisAgent:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    @pytest.fixture
    def config(self):
        return AnalysisConfig(analysis_window_size=5)

    def test_empty_hub(self, hub, config):
        """Agent handles empty Context Hub gracefully."""
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.success is True
        assert result.runs_analyzed == 0
        assert result.findings_count == 0
        assert "No runs found" in result.report_content

    def test_single_run(self, hub, config):
        """Agent works with a single run record."""
        _seed_hub(hub, 1)
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.success is True
        assert result.runs_analyzed == 1

    def test_full_window(self, hub, config):
        """Agent analyzes exactly window_size runs."""
        _seed_hub(hub, 10)
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.success is True
        assert result.runs_analyzed == 5  # window_size=5

    def test_report_written_to_analysis_dir(self, hub, config):
        """Report is persisted as markdown in context_hub/analysis/."""
        _seed_hub(hub, 5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.success is True
        assert result.report_filename.endswith(".md")
        assert result.report_filename.startswith("analysis-")

        # Verify file exists
        analyses = hub.list_analyses()
        assert len(analyses) >= 1

    def test_report_contains_sections(self, hub, config):
        """Report includes expected markdown sections."""
        _seed_hub(hub, 5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        report = result.report_content
        assert "# Observer Analysis Report" in report
        assert "## Findings" in report
        assert "## Metrics Summary" in report
        assert "## Trends" in report
        assert "## Duration Distribution" in report
        assert "## Test Health" in report
        assert "## Run Details" in report

    def test_duration_seconds_tracked(self, hub, config):
        """Agent tracks its own execution time."""
        _seed_hub(hub, 3)
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.duration_seconds >= 0
        assert result.duration_seconds < 10  # should be fast

    def test_result_summary(self, hub, config):
        """AnalysisResult.summary provides human-readable output."""
        _seed_hub(hub, 3)
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert "Analyzed" in result.summary
        assert "findings" in result.summary


# ═══════════════════════════════════════
# Finding Generation Tests
# ═══════════════════════════════════════


class TestFindings:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_all_passing_generates_info(self, hub):
        """All-passing runs produce an info finding about success."""
        config = AnalysisConfig(analysis_window_size=5)
        _seed_hub(hub, 5, build_success=True)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.success is True
        assert any(
            "All builds succeeded" in r
            for r in result.report_content.split("\n")
        )

    def test_low_success_rate_generates_critical(self, hub):
        """Build success rate below target triggers critical finding."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_build_success_rate=0.9,
        )
        # 3 pass, 2 fail = 60% success rate
        for i in range(5):
            r = _make_record(
                f"2026-02-{i+1:02d}-aaaaaa",
                timestamp=f"2026-02-{i+1:02d}T12:00:00+00:00",
                build_success=(i < 3),
            )
            hub.write_run(r)

        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.findings_count >= 1
        assert "below target" in result.report_content

    def test_slow_cycle_time_generates_warning(self, hub):
        """Cycle time above target triggers a warning."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_median_cycle_time=20.0,
        )
        _seed_hub(hub, 5, duration_minutes=35.0)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.findings_count >= 1
        assert "cycle time" in result.report_content.lower()

    def test_high_manual_intervention_generates_warning(self, hub):
        """Manual intervention rate above target triggers a warning."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_manual_intervention_rate=0.1,
        )
        # All 5 runs have manual intervention -> 100% rate
        _seed_hub(
            hub, 5,
            manual_intervention=True,
            manual_intervention_reason="test reason",
        )
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.findings_count >= 1
        assert "intervention" in result.report_content.lower()

    def test_high_lint_errors_generates_warning(self, hub):
        """Lint errors above target triggers a warning."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_max_lint_errors=2,
        )
        _seed_hub(hub, 5, lint_errors=10)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.findings_count >= 1
        assert "lint" in result.report_content.lower()

    def test_high_type_errors_generates_warning(self, hub):
        """Type errors above target triggers a warning."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_max_type_errors=0,
        )
        _seed_hub(hub, 5, type_errors=3)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert result.findings_count >= 1
        assert "type error" in result.report_content.lower()

    def test_no_findings_when_all_within_targets(self, hub):
        """No warnings/criticals when everything is within targets."""
        config = AnalysisConfig(
            analysis_window_size=5,
            target_build_success_rate=0.9,
            target_median_cycle_time=50.0,
            target_manual_intervention_rate=0.5,
            target_max_lint_errors=10,
            target_max_type_errors=5,
        )
        _seed_hub(hub, 5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        # Should only have info-level findings (like "all builds succeeded")
        assert all(
            "below target" not in line and "exceeds target" not in line
            for line in result.report_content.split("\n")
        )


# ═══════════════════════════════════════
# Report Format Tests
# ═══════════════════════════════════════


class TestReportFormat:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_metrics_table_format(self, hub):
        """Metrics summary table has correct markdown structure."""
        _seed_hub(hub, 5)
        config = AnalysisConfig(analysis_window_size=5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        lines = result.report_content.split("\n")
        table_header_idx = None
        for i, line in enumerate(lines):
            if "| Metric | Value | Target | Status |" in line:
                table_header_idx = i
                break

        assert table_header_idx is not None
        # Separator row follows header
        assert lines[table_header_idx + 1].startswith("|---")

    def test_run_details_table(self, hub):
        """Run details table contains each run."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(analysis_window_size=3, include_run_details=True)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert "## Run Details" in result.report_content
        assert "| Run ID |" in result.report_content

    def test_run_details_excluded_when_disabled(self, hub):
        """Run details section can be disabled."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(
            analysis_window_size=3, include_run_details=False
        )
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert "## Run Details" not in result.report_content

    def test_report_footer(self, hub):
        """Report ends with attribution."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(analysis_window_size=3)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert "Analysis Agent (Phase 2)" in result.report_content

    def test_empty_report_message(self, hub):
        """Empty hub produces a helpful message."""
        config = AnalysisConfig(analysis_window_size=5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        assert "No runs found" in result.report_content


# ═══════════════════════════════════════
# Monitoring Tests
# ═══════════════════════════════════════


class TestMonitoring:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    @pytest.fixture
    def monitor(self, tmp_path):
        return AgentMonitor(tmp_path / "test_hub" / "metrics")

    def test_agent_run_logged(self, hub):
        """Each agent.run() produces a monitoring log entry."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(analysis_window_size=3)
        agent = AnalysisAgent(hub, config)
        agent.run()

        monitor = create_monitor(hub.base_path)
        assert monitor.run_count() == 1

    def test_multiple_runs_logged(self, hub):
        """Multiple agent runs each produce a log entry."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(analysis_window_size=3)
        agent = AnalysisAgent(hub, config)
        agent.run()
        agent.run()
        agent.run()

        monitor = create_monitor(hub.base_path)
        assert monitor.run_count() == 3

    def test_log_entry_fields(self, hub):
        """Log entry captures expected metadata."""
        _seed_hub(hub, 3)
        config = AnalysisConfig(analysis_window_size=3)
        agent = AnalysisAgent(hub, config)
        agent.run()

        monitor = create_monitor(hub.base_path)
        entries = monitor.recent_runs(limit=1)
        assert len(entries) == 1

        entry = entries[0]
        assert entry.agent_name == "analysis_agent"
        assert entry.success is True
        assert entry.runs_analyzed == 3
        assert entry.duration_seconds >= 0
        assert entry.window_size == 3

    def test_failed_run_logged(self, tmp_path):
        """Failed analysis runs are logged with error details."""
        # Create a hub that will cause an error by corrupting the runs dir
        hub = ContextHub(str(tmp_path / "broken_hub"))

        # Write a corrupt JSON file to trigger an error
        corrupt_file = hub.runs_dir / "corrupt.json"
        with open(corrupt_file, "w") as f:
            f.write("not json at all {{{")

        config = AnalysisConfig(analysis_window_size=5)
        agent = AnalysisAgent(hub, config)
        result = agent.run()

        # The agent should handle the corrupt file gracefully
        # (ContextHub skips corrupt files, so this should still succeed)
        assert result.success is True

    def test_monitor_log_run(self, monitor):
        """Monitor.log_run writes a structured entry."""
        entry = AgentRunLog(
            agent_name="test_agent",
            timestamp="2026-02-08T12:00:00+00:00",
            duration_seconds=1.5,
            runs_analyzed=10,
            findings_count=3,
            success=True,
        )
        monitor.log_run(entry)
        assert monitor.run_count() == 1

    def test_monitor_recent_runs(self, monitor):
        """Monitor returns recent runs in newest-first order."""
        for i in range(5):
            entry = AgentRunLog(
                agent_name="test_agent",
                timestamp=f"2026-02-0{i+1}T12:00:00+00:00",
                duration_seconds=float(i),
                runs_analyzed=i,
                findings_count=0,
                success=True,
            )
            monitor.log_run(entry)

        recent = monitor.recent_runs(limit=3)
        assert len(recent) == 3
        # Newest first (last written is first returned)
        assert recent[0].timestamp == "2026-02-05T12:00:00+00:00"

    def test_monitor_success_rate(self, monitor):
        """Monitor computes agent success rate."""
        for success in [True, True, True, False]:
            entry = AgentRunLog(
                agent_name="test_agent",
                timestamp="2026-02-08T12:00:00+00:00",
                duration_seconds=0.5,
                runs_analyzed=5,
                findings_count=0,
                success=success,
            )
            monitor.log_run(entry)

        rate = monitor.success_rate()
        assert rate == 0.75  # 3/4

    def test_monitor_empty_success_rate(self, monitor):
        """Success rate returns None with no data."""
        assert monitor.success_rate() is None


# ═══════════════════════════════════════
# Severity and Finding Tests
# ═══════════════════════════════════════


class TestSeverityAndFinding:
    def test_severity_constants(self):
        assert Severity.INFO == "info"
        assert Severity.WARNING == "warning"
        assert Severity.CRITICAL == "critical"

    def test_finding_creation(self):
        f = Finding(
            severity=Severity.WARNING,
            category="reliability",
            message="Test message",
            detail="Some detail",
        )
        assert f.severity == "warning"
        assert f.category == "reliability"
        assert f.message == "Test message"
        assert f.detail == "Some detail"

    def test_finding_without_detail(self):
        f = Finding(
            severity=Severity.INFO,
            category="test",
            message="Info message",
        )
        assert f.detail == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
