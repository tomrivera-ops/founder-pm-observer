"""Tests for schema.py v2.1 field extensions — backward compatibility + validation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.schema import RunRecord, validate_run_record, current_timestamp, generate_run_id


class TestNewFieldDefaults:
    def test_defaults_are_zero_or_empty(self):
        """New v2.1 fields should have safe defaults."""
        record = RunRecord(run_id="test-defaults", timestamp=current_timestamp())
        assert record.model_provider == ""
        assert record.model_name == ""
        assert record.tokens_input == 0
        assert record.tokens_output == 0
        assert record.cost_usd == 0.0
        assert record.retry_count == 0
        assert record.fail_category == ""
        assert record.fail_stage == ""
        assert record.input_content_hash == ""
        assert record.step_timings == ()
        assert record.is_recursive is False
        assert record.recursive_parent_id == ""
        assert record.iteration_number == 0


class TestBackwardCompatibility:
    def test_old_record_deserializes(self):
        """A record from before v2.1 (without new fields) should deserialize correctly."""
        old_data = {
            "run_id": "2026-02-06-abc123",
            "source": "founder-pm",
            "input_type": "PRD",
            "timestamp": "2026-02-06T12:00:00+00:00",
            "build_success": True,
            "tests_passed": 10,
            "tests_failed": 0,
            "pipeline_steps_executed": ["ingest", "build", "ship"],
            "notes": "old record",
        }
        record = RunRecord.from_dict(old_data)
        assert record.run_id == "2026-02-06-abc123"
        assert record.model_provider == ""  # Default
        assert record.step_timings == ()  # Default
        assert record.is_recursive is False

    def test_old_record_roundtrip(self):
        """Old record → deserialize → serialize should preserve data."""
        old_data = {
            "run_id": "2026-02-06-abc123",
            "source": "founder-pm",
            "input_type": "PRD",
            "timestamp": "2026-02-06T12:00:00+00:00",
            "build_success": True,
            "tests_passed": 5,
            "tests_failed": 0,
            "pipeline_steps_executed": ["ingest", "build"],
            "notes": "roundtrip test",
        }
        record = RunRecord.from_dict(old_data)
        d = record.to_dict()
        assert d["run_id"] == "2026-02-06-abc123"
        assert d["notes"] == "roundtrip test"
        assert d["model_provider"] == ""
        assert d["step_timings"] == []  # to_dict converts tuple to list


class TestNewFieldsPopulated:
    def test_from_dict_with_new_fields(self):
        data = {
            "run_id": "2026-02-10-new001",
            "timestamp": "2026-02-10T12:00:00+00:00",
            "build_success": True,
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4",
            "tokens_input": 5000,
            "tokens_output": 2000,
            "cost_usd": 0.05,
            "retry_count": 1,
            "fail_category": "build",
            "fail_stage": "build",
            "input_content_hash": "sha256abc",
            "step_timings": {"ingest": 30, "build": 120},
            "is_recursive": True,
            "recursive_parent_id": "parent-001",
            "iteration_number": 2,
            "pipeline_steps_executed": ["ingest", "build"],
        }
        record = RunRecord.from_dict(data)
        assert record.model_provider == "anthropic"
        assert record.tokens_input == 5000
        assert record.cost_usd == 0.05
        assert record.is_recursive is True
        assert record.recursive_parent_id == "parent-001"
        assert record.iteration_number == 2
        # step_timings converted from dict to tuple of pairs
        assert ("ingest", 30) in record.step_timings
        assert ("build", 120) in record.step_timings

    def test_step_timings_list_format(self):
        """step_timings as list of [step, seconds] pairs."""
        data = {
            "run_id": "test-list-timings",
            "timestamp": "2026-02-10T12:00:00+00:00",
            "step_timings": [["ingest", 30], ["build", 120]],
            "pipeline_steps_executed": [],
        }
        record = RunRecord.from_dict(data)
        assert ("ingest", 30) in record.step_timings


class TestValidationNewFields:
    def test_valid_new_fields(self):
        record = RunRecord(
            run_id="test-valid",
            timestamp=current_timestamp(),
            model_provider="google",
            tokens_input=100,
            cost_usd=0.01,
        )
        issues = validate_run_record(record)
        assert issues == []

    def test_negative_tokens_input(self):
        record = RunRecord(
            run_id="test-neg-tokens",
            timestamp=current_timestamp(),
            tokens_input=-1,
        )
        issues = validate_run_record(record)
        assert any("tokens_input" in i for i in issues)

    def test_negative_tokens_output(self):
        record = RunRecord(
            run_id="test-neg-out",
            timestamp=current_timestamp(),
            tokens_output=-5,
        )
        issues = validate_run_record(record)
        assert any("tokens_output" in i for i in issues)

    def test_negative_cost(self):
        record = RunRecord(
            run_id="test-neg-cost",
            timestamp=current_timestamp(),
            cost_usd=-0.5,
        )
        issues = validate_run_record(record)
        assert any("cost_usd" in i for i in issues)

    def test_negative_iteration(self):
        record = RunRecord(
            run_id="test-neg-iter",
            timestamp=current_timestamp(),
            iteration_number=-1,
        )
        issues = validate_run_record(record)
        assert any("iteration_number" in i for i in issues)

    def test_invalid_fail_category(self):
        record = RunRecord(
            run_id="test-bad-cat",
            timestamp=current_timestamp(),
            fail_category="invalid_category",
        )
        issues = validate_run_record(record)
        assert any("fail_category" in i for i in issues)

    def test_valid_fail_category(self):
        record = RunRecord(
            run_id="test-good-cat",
            timestamp=current_timestamp(),
            fail_category="build",
        )
        issues = validate_run_record(record)
        assert not any("fail_category" in i for i in issues)

    def test_recursive_requires_parent_id(self):
        record = RunRecord(
            run_id="test-recursive-no-parent",
            timestamp=current_timestamp(),
            is_recursive=True,
            recursive_parent_id="",
        )
        issues = validate_run_record(record)
        assert any("recursive_parent_id" in i for i in issues)

    def test_recursive_with_parent_id_valid(self):
        record = RunRecord(
            run_id="test-recursive-ok",
            timestamp=current_timestamp(),
            is_recursive=True,
            recursive_parent_id="parent-001",
        )
        issues = validate_run_record(record)
        assert not any("recursive_parent_id" in i for i in issues)
