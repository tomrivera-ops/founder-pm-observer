"""Tests for bin/phase4_readiness.py — OBS-001 variance and trend checks."""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bin.phase4_readiness import (
    _check_approval_rate_variance,
    _check_trend_not_degrading,
)
from lib.schema import RunRecord, current_timestamp


def _make_proposals(risk_status_pairs):
    """Build proposal dicts from (risk_level, status) pairs."""
    proposals = []
    for i, (risk, status) in enumerate(risk_status_pairs):
        proposals.append({
            "id": f"prop-{i}",
            "status": status,
            "risk_assessment": risk,
            "created_at": current_timestamp(),
        })
    return proposals


def _make_runs(n, build_success=True, duration=5.0):
    """Build a list of RunRecord objects."""
    return [
        RunRecord(
            run_id=f"run-{i:03d}",
            timestamp=current_timestamp(),
            build_success=build_success,
            duration_minutes=duration,
        )
        for i in range(n)
    ]


class TestApprovalRateVariance:
    def test_variance_pass_low_spread(self):
        """All risk levels approve at similar rates → variance <= 0.3."""
        proposals = _make_proposals([
            ("low", "approved"), ("low", "approved"), ("low", "rejected"),
            ("high", "approved"), ("high", "rejected"),
        ])
        result = _check_approval_rate_variance(proposals)
        assert result["passed"] is True

    def test_variance_fail_high_spread(self):
        """100% low-risk approved, 0% high-risk approved → variance 1.0."""
        proposals = _make_proposals([
            ("low", "approved"), ("low", "approved"),
            ("high", "rejected"), ("high", "rejected"),
        ])
        result = _check_approval_rate_variance(proposals)
        assert result["passed"] is False
        assert result["actual"] > 0.3

    def test_variance_edge_exactly_at_threshold(self):
        """Variance exactly at 0.3 should pass (<= comparison)."""
        # 3 low (2 approved, 1 rejected) = 0.667 rate
        # 3 high (1 approved, 2 rejected) = 0.333 rate
        # variance = 0.667 - 0.333 = 0.333 > 0.3 → fail
        # Adjust: 10 low (7 approved, 3 rejected) = 0.7
        # 10 high (4 approved, 6 rejected) = 0.4 → variance 0.3 exactly
        proposals = _make_proposals(
            [("low", "approved")] * 7 + [("low", "rejected")] * 3 +
            [("high", "approved")] * 4 + [("high", "rejected")] * 6
        )
        result = _check_approval_rate_variance(proposals)
        assert result["passed"] is True
        assert abs(result["actual"] - 0.3) < 0.01


class TestTrendNotDegrading:
    def test_trend_pass_stable(self):
        """Stable runs with consistent metrics → passes."""
        runs = _make_runs(8, build_success=True, duration=5.0)
        result = _check_trend_not_degrading(runs)
        assert result["passed"] is True

    def test_trend_fail_insufficient_runs(self):
        """Fewer than 6 runs → cannot compute trends → fails."""
        runs = _make_runs(3)
        result = _check_trend_not_degrading(runs)
        assert result["passed"] is False
        assert "Not enough runs" in result["detail"]

    def test_trend_edge_exactly_six_runs(self):
        """Exactly 6 runs should be enough for trend analysis."""
        runs = _make_runs(6, build_success=True, duration=5.0)
        result = _check_trend_not_degrading(runs)
        assert result["passed"] is True
