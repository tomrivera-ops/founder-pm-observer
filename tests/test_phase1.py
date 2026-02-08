#!/usr/bin/env python3
"""
Tests for Founder-PM Observer Plane — Phase 1

Covers:
  - RunRecord creation and immutability
  - Serialization/deserialization roundtrip
  - Validation rules
  - Context Hub write/read/list operations
  - Immutability enforcement (no overwrites)
  - Metrics aggregation
  - Trend computation
"""

import json
import os
import sys
import tempfile
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import (
    RunRecord,
    InputType,
    PipelineStep,
    generate_run_id,
    current_timestamp,
    validate_run_record,
)
from lib.context_hub import ContextHub, RecordExistsError, ValidationError
from lib.metrics import compute_metrics, compute_trends, MetricsSummary


# ═══════════════════════════════════════
# Schema Tests
# ═══════════════════════════════════════


class TestRunRecord:
    def test_create_minimal(self):
        r = RunRecord(run_id="test-001", timestamp=current_timestamp())
        assert r.run_id == "test-001"
        assert r.source == "founder-pm"
        assert r.build_success is False

    def test_immutability(self):
        r = RunRecord(run_id="test-001", timestamp=current_timestamp())
        with pytest.raises(AttributeError):
            r.run_id = "modified"  # frozen=True should prevent this

    def test_serialization_roundtrip(self):
        r = RunRecord(
            run_id="test-002",
            timestamp="2026-02-06T21:00:00+00:00",
            input_type="PRD",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "audit", "ship"),
            duration_minutes=31.5,
            build_success=True,
            tests_passed=42,
            tests_failed=0,
            lint_errors=1,
            diff_size_lines=412,
        )

        json_str = r.to_json()
        restored = RunRecord.from_json(json_str)

        assert restored.run_id == r.run_id
        assert restored.llm_model == "claude-4.6"
        assert restored.pipeline_steps_executed == ("ingest", "build", "audit", "ship")
        assert restored.duration_minutes == 31.5
        assert restored.tests_passed == 42

    def test_to_dict_converts_tuple_to_list(self):
        r = RunRecord(
            run_id="test-003",
            timestamp=current_timestamp(),
            pipeline_steps_executed=("build", "ship"),
        )
        d = r.to_dict()
        assert isinstance(d["pipeline_steps_executed"], list)

    def test_from_dict_ignores_unknown_fields(self):
        """Forward compatibility: unknown fields don't crash deserialization."""
        data = {
            "run_id": "test-004",
            "timestamp": current_timestamp(),
            "future_field": "should be ignored",
        }
        r = RunRecord.from_dict(data)
        assert r.run_id == "test-004"


class TestValidation:
    def test_valid_record(self):
        r = RunRecord(
            run_id="test-valid",
            timestamp=current_timestamp(),
            build_success=True,
        )
        issues = validate_run_record(r)
        assert issues == []

    def test_missing_run_id(self):
        r = RunRecord(run_id="", timestamp=current_timestamp())
        issues = validate_run_record(r)
        assert any("run_id" in i for i in issues)

    def test_missing_timestamp(self):
        r = RunRecord(run_id="test-no-ts", timestamp="")
        issues = validate_run_record(r)
        assert any("timestamp" in i for i in issues)

    def test_invalid_timestamp_format(self):
        r = RunRecord(run_id="test-bad-ts", timestamp="not-a-date")
        issues = validate_run_record(r)
        assert any("ISO 8601" in i for i in issues)

    def test_negative_duration(self):
        r = RunRecord(
            run_id="test-neg",
            timestamp=current_timestamp(),
            duration_minutes=-5,
        )
        issues = validate_run_record(r)
        assert any("negative" in i for i in issues)

    def test_invalid_input_type(self):
        r = RunRecord(
            run_id="test-bad-type",
            timestamp=current_timestamp(),
            input_type="INVALID",
        )
        issues = validate_run_record(r)
        assert any("input_type" in i for i in issues)

    def test_invalid_pipeline_step(self):
        r = RunRecord(
            run_id="test-bad-step",
            timestamp=current_timestamp(),
            pipeline_steps_executed=("build", "nonexistent"),
        )
        issues = validate_run_record(r)
        assert any("nonexistent" in i for i in issues)


class TestGenerateRunId:
    def test_format(self):
        rid = generate_run_id()
        parts = rid.split("-")
        # Should be YYYY-MM-DD-XXXXXX
        assert len(parts) == 4
        assert len(parts[0]) == 4  # year
        assert len(parts[3]) == 6  # unique suffix

    def test_uniqueness(self):
        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════
# Context Hub Tests
# ═══════════════════════════════════════


class TestContextHub:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def _make_record(self, run_id: str, **kwargs) -> RunRecord:
        defaults = {
            "run_id": run_id,
            "timestamp": current_timestamp(),
            "build_success": True,
        }
        defaults.update(kwargs)
        return RunRecord(**defaults)

    def test_init_creates_directories(self, hub):
        assert hub.runs_dir.exists()
        assert hub.metrics_dir.exists()
        assert hub.analysis_dir.exists()
        assert hub.proposals_dir.exists()
        assert hub.parameters_dir.exists()

    def test_write_and_read(self, hub):
        record = self._make_record("test-write-001")
        hub.write_run(record)

        restored = hub.read_run("test-write-001")
        assert restored is not None
        assert restored.run_id == "test-write-001"

    def test_immutability_enforcement(self, hub):
        record = self._make_record("test-immut-001")
        hub.write_run(record)

        with pytest.raises(RecordExistsError):
            hub.write_run(record)

    def test_validation_on_write(self, hub):
        bad_record = RunRecord(run_id="", timestamp="")
        with pytest.raises(ValidationError):
            hub.write_run(bad_record)

    def test_read_nonexistent(self, hub):
        assert hub.read_run("does-not-exist") is None

    def test_list_runs_ordering(self, hub):
        for i in range(5):
            record = self._make_record(
                f"2026-02-0{i+1}-aaaaaa",
                timestamp=f"2026-02-0{i+1}T12:00:00+00:00",
            )
            hub.write_run(record)

        runs = hub.list_runs(newest_first=True)
        assert runs[0].run_id == "2026-02-05-aaaaaa"
        assert runs[-1].run_id == "2026-02-01-aaaaaa"

        runs_asc = hub.list_runs(newest_first=False)
        assert runs_asc[0].run_id == "2026-02-01-aaaaaa"

    def test_list_runs_with_limit(self, hub):
        for i in range(10):
            hub.write_run(self._make_record(f"2026-01-{i+10:02d}-bbbbbb"))

        runs = hub.list_runs(limit=3)
        assert len(runs) == 3

    def test_run_count(self, hub):
        assert hub.run_count() == 0
        hub.write_run(self._make_record("count-001"))
        hub.write_run(self._make_record("count-002"))
        assert hub.run_count() == 2

    def test_run_exists(self, hub):
        hub.write_run(self._make_record("exists-001"))
        assert hub.run_exists("exists-001") is True
        assert hub.run_exists("nope") is False

    def test_analysis_write_read(self, hub):
        hub.write_analysis("test-report", "# Analysis\nLooks good.")
        content = hub.read_analysis("test-report")
        assert content == "# Analysis\nLooks good."

    def test_parameters_write_read(self, hub):
        config = {"audit_depth": "adaptive", "verbosity": "low"}
        hub.write_parameters("v001", config)
        restored = hub.read_parameters("v001")
        assert restored == config

    def test_latest_parameters(self, hub):
        hub.write_parameters("v001", {"version": 1})
        hub.write_parameters("v002", {"version": 2})
        latest = hub.latest_parameters()
        assert latest["version"] == 2


# ═══════════════════════════════════════
# Metrics Tests
# ═══════════════════════════════════════


class TestMetrics:
    def _make_runs(self, n: int, **overrides) -> list[RunRecord]:
        runs = []
        for i in range(n):
            defaults = {
                "run_id": f"metric-{i:03d}",
                "timestamp": f"2026-02-0{min(i+1, 9)}T12:00:00+00:00",
                "duration_minutes": 30.0 + i,
                "build_success": True,
                "tests_passed": 40 + i,
                "tests_failed": i % 3,
                "lint_errors": i % 2,
                "type_errors": 0,
                "diff_size_lines": 100 + i * 50,
                "manual_intervention": i == 0,
            }
            defaults.update(overrides)
            defaults["run_id"] = f"metric-{i:03d}"
            runs.append(RunRecord(**defaults))
        return runs

    def test_empty_runs(self):
        summary = compute_metrics([])
        assert summary.run_count == 0

    def test_basic_aggregation(self):
        runs = self._make_runs(5)
        s = compute_metrics(runs)
        assert s.run_count == 5
        assert s.build_success_rate == 1.0
        assert s.duration_mean > 0
        assert s.duration_median > 0
        assert s.total_tests_passed > 0

    def test_success_rate_with_failures(self):
        runs = self._make_runs(4)
        # Override one to fail
        failed_run = RunRecord(
            run_id="metric-fail",
            timestamp=current_timestamp(),
            build_success=False,
        )
        runs.append(failed_run)
        s = compute_metrics(runs)
        assert s.build_success_rate == 0.8  # 4/5

    def test_manual_intervention_rate(self):
        runs = self._make_runs(5)
        # First run has manual=True by default in _make_runs
        s = compute_metrics(runs)
        assert s.manual_intervention_rate == 0.2  # 1/5

    def test_trend_improving(self):
        prev = MetricsSummary(
            run_count=5,
            duration_mean=45.0,
            build_success_rate=0.8,
            avg_lint_errors=5.0,
        )
        curr = MetricsSummary(
            run_count=5,
            duration_mean=30.0,  # faster
            build_success_rate=0.95,  # more reliable
            avg_lint_errors=1.0,  # cleaner
        )
        result = compute_trends(curr, prev)
        assert result.duration_trend == "improving"
        assert result.reliability_trend == "improving"
        assert result.hygiene_trend == "improving"

    def test_trend_degrading(self):
        prev = MetricsSummary(
            run_count=5,
            duration_mean=20.0,
            build_success_rate=1.0,
            avg_lint_errors=0.5,
        )
        curr = MetricsSummary(
            run_count=5,
            duration_mean=40.0,
            build_success_rate=0.7,
            avg_lint_errors=5.0,
        )
        result = compute_trends(curr, prev)
        assert result.duration_trend == "degrading"
        assert result.reliability_trend == "degrading"
        assert result.hygiene_trend == "degrading"

    def test_trend_insufficient_data(self):
        prev = MetricsSummary(run_count=0)
        curr = MetricsSummary(run_count=5, duration_mean=30.0)
        result = compute_trends(curr, prev)
        assert result.duration_trend == "insufficient_data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
