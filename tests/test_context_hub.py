#!/usr/bin/env python3
"""
Tests for Context Hub write-time validation (OBS-005).

Verifies that write_run() rejects invalid records at write time
via validate_run_record() integration.
"""

import sys
import tempfile
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import RunRecord, current_timestamp
from lib.context_hub import ContextHub, ValidationError, RecordExistsError


@pytest.fixture
def hub(tmp_path):
    """Create a ContextHub with a temporary directory."""
    return ContextHub(str(tmp_path / "context_hub"))


def _valid_record(**overrides) -> RunRecord:
    """Create a valid RunRecord with optional field overrides."""
    defaults = {
        "run_id": "test-001",
        "source": "founder-pm",
        "timestamp": current_timestamp(),
        "build_success": True,
    }
    defaults.update(overrides)
    return RunRecord(**defaults)


class TestWriteTimeValidation:
    """Tests for write-time validation in ContextHub.write_run()."""

    def test_rejects_empty_run_id(self, hub):
        """write_run() must reject a record with empty run_id."""
        record = _valid_record(run_id="")
        with pytest.raises(ValidationError, match="run_id is required"):
            hub.write_run(record)

    def test_rejects_invalid_timestamp(self, hub):
        """write_run() must reject a record with malformed timestamp."""
        record = _valid_record(timestamp="not-a-date")
        with pytest.raises(ValidationError, match="timestamp is not valid ISO 8601"):
            hub.write_run(record)

    def test_rejects_negative_tests_passed(self, hub):
        """write_run() must reject a record with negative test counts."""
        record = _valid_record(tests_passed=-1)
        with pytest.raises(ValidationError, match="tests_passed cannot be negative"):
            hub.write_run(record)
